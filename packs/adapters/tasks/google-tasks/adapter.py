"""Google Tasks v1 bidirectional state-mirror adapter.

The adapter mirrors task lists and tasks into the canonical ``list.*`` and
``list_item.*`` families.  It preserves Google's opaque position, etag,
links, completion and assignment provenance without pretending Google has
native priority, alarm, recurrence, or geofence fields.  Every outbound
operation is separately declared and passes through observation mode.
"""

# [§8.2, D6] no LLM SDK imports under packs/adapters/.
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import AdapterContext, AdapterStatus, SensitivityFloor
from adminme.adapters.emission import write_adapter_event
from adminme.adapters.credentials import (
    assert_installed_user_binding,
    resolve_json_credentials,
)
from adminme.lib.adapters import (
    ChangeEnvelope,
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.observation import outbound as observation_outbound

_log = logging.getLogger(__name__)

GOOGLE_TASKS_READONLY_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/tasks.readonly"
]
GOOGLE_TASKS_SCOPES: list[str] = ["https://www.googleapis.com/auth/tasks"]

# [§6 invariant 10] privileged > normal; config may raise but not lower.
_FLOOR_RANK: dict[str, int] = {"normal": 0, "privileged": 1}

_DEFAULT_POLL_INTERVAL_S: int = 60
_DEFAULT_PAGE_SIZE: int = 100

# Google has no priority.  ``0`` means genuinely unspecified.
_GOOGLE_TASKS_DEFAULT_PRIORITY: Literal[0, 1, 5, 9] = 0


class _TasksLeaseLost(RuntimeError):
    """The poll may no longer commit provider progress."""


GOOGLE_TASKS_WRITE_CAPABILITIES = [
    "create_list",
    "update_list",
    "delete_list",
    "create_task",
    "update_task",
    "delete_task",
    "complete_task",
    "uncomplete_task",
    "move_task",
    "clear_completed_tasks",
]

CredentialsResolver = Callable[[str], dict[str, Any]]
"""``op://`` ref → service-account JSON. Tests inject a stub; production
calls the 1Password CLI."""

ServiceFactory = Callable[[dict[str, Any], list[str], str], Any]
"""(credentials, scopes, subject_email) → Tasks service. Tests inject a
fake; production lazily imports the Google SDK."""


def _default_credentials_resolver(reference: str) -> dict[str, Any]:
    return resolve_json_credentials(reference)


def _default_service_factory(
    credentials_json: dict[str, Any],
    scopes: list[str],
    subject_email: str,
) -> Any:
    """Build Tasks v1 service; lazy import keeps SDK out of CI."""
    from googleapiclient.discovery import build

    if {"client_id", "client_secret", "refresh_token"} <= credentials_json.keys():
        from google.oauth2.credentials import Credentials as UserCredentials

        creds: Any = UserCredentials(
            token=None,
            refresh_token=str(credentials_json["refresh_token"]),
            token_uri=str(
                credentials_json.get("token_uri")
                or "https://oauth2.googleapis.com/token"
            ),
            client_id=str(credentials_json["client_id"]),
            client_secret=str(credentials_json["client_secret"]),
            scopes=scopes,
        )
    else:
        from google.oauth2.service_account import (
            Credentials as ServiceAccountCredentials,
        )

        creds = ServiceAccountCredentials.from_service_account_info(
            credentials_json, scopes=scopes
        )
        if subject_email:
            creds = creds.with_subject(subject_email)
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


class GoogleTasksAdapter(ExternalStateMirrorAdapter):
    """Google Tasks Cat-B standalone adapter (fresh canonical-build pack).

    Polls ``tasks.list`` every ``poll_interval_s`` and emits one
    ``list_item.added@v1`` per non-deduped task mapping Google's response
    onto the existing schema per [D7] + canonical 11a's "MAP DON'T
    REGISTER". Outbound :meth:`_apply_write_impl` accepts
    ``WriteOperation(kind="create_task")`` and routes ``tasks.insert``
    through :func:`observation_outbound` per [§6.20].
    """

    name: ClassVar[str] = "tasks-google-tasks"
    family: ClassVar[Literal["reminders", "calendaring", "contacts", "tasks"]] = "tasks"
    version: ClassVar[str] = "2.1.0"
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    # [memo 05 §3.2, PM-35] class-level mirror of the manifest's
    # capabilities. One Cat-B entry declaring the EXISTING on-main
    # ``list_item.added`` family — NO new schema per [D7].
    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-B",
            "runtime": "central",
            "event_families": [
                "list.created",
                "list.updated",
                "list.deleted",
                "list_item.added",
                "list_item.updated",
                "list_item.completed",
                "list_item.uncompleted",
                "list_item.deleted",
            ],
        },
    ]

    # [UT-27] asymmetric — declare ONLY what is implemented.
    write_capabilities: ClassVar[list[str]] = list(GOOGLE_TASKS_WRITE_CAPABILITIES)

    # [memo 05 §2.5, PM-35] projection-row column for per-member vs
    # shared rows on the future tasks projection.
    sharing_model_discriminator: ClassVar[str] = "owner_member_id"

    PACK_ID: ClassVar[str] = "adapter:tasks-google-tasks"
    HARDCODED_FLOOR: ClassVar[SensitivityFloor] = "normal"
    SCOPES: ClassVar[list[str]] = list(GOOGLE_TASKS_SCOPES)
    POLL_INTERVAL_S: ClassVar[int] = _DEFAULT_POLL_INTERVAL_S
    PAGE_SIZE: ClassVar[int] = _DEFAULT_PAGE_SIZE

    def __init__(
        self,
        *,
        config: dict[str, Any],
        credentials_resolver: CredentialsResolver | None = None,
        service_factory: ServiceFactory | None = None,
        max_poll_cycles: int | None = None,
        observation_session: Any = None,
        observation_manager: Any = None,
        observation_event_log: Any = None,
    ) -> None:
        self._config = dict(config)
        read_only = self._config.get("read_only", False)
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be a boolean")
        self._scopes = list(GOOGLE_TASKS_READONLY_SCOPES if read_only else self.SCOPES)
        if read_only and self._config.get("permission_scope") != self._scopes:
            raise ValueError(
                "read-only Google Tasks permission_scope must exactly match "
                "the tasks.readonly OAuth scope"
            )
        self.__dict__["write_capabilities"] = (
            [] if read_only else list(type(self).write_capabilities)
        )
        self._credentials_resolver = (
            credentials_resolver or _default_credentials_resolver
        )
        self._service_factory = service_factory or _default_service_factory
        # [§6 invariant 10] resolve floor at construction; raise on downgrade.
        self.sensitivity_floor: SensitivityFloor = (  # type: ignore[misc]
            self._resolve_sensitivity_floor()
        )
        self._poll_interval_s: int = int(
            self._config.get("poll_interval_s", self.POLL_INTERVAL_S)
        )
        self._status = AdapterStatus()
        self._service: Any = None
        self._ctx: AdapterContext | None = None
        self._stop_requested: bool = False
        self._max_poll_cycles: int | None = max_poll_cycles
        self._lease_owner = f"google-tasks-{uuid.uuid4()}"
        self._seen_inbound: dict[tuple[str, str], dict[str, Any]] = {}
        self._seen_lists: dict[str, dict[str, Any]] = {}
        # [§6.20, PM-27] required fail-closed observation seam slots.
        self._observation_session: Any = observation_session
        self._observation_manager: Any = observation_manager
        self._observation_event_log: Any = observation_event_log

    def _resolve_sensitivity_floor(self) -> SensitivityFloor:
        """[§6 invariant 10] config may RAISE; never lower."""
        cfg_value = self._config.get("sensitivity_floor", self.HARDCODED_FLOOR)
        if cfg_value not in _FLOOR_RANK:
            raise ValueError(
                f"sensitivity_floor must be 'normal' or 'privileged', got {cfg_value!r}"
            )
        if _FLOOR_RANK[cfg_value] < _FLOOR_RANK[self.HARDCODED_FLOOR]:
            raise ValueError(
                f"config sensitivity_floor={cfg_value!r} lower than hardcoded "
                f"{self.HARDCODED_FLOOR!r}; refusing to downgrade "
                "[SYSTEM_INVARIANTS.md §6 invariant 10]"
            )
        return cfg_value  # type: ignore[return-value]

    async def start(self, *, ctx: AdapterContext) -> None:
        """Authenticate, emit ``adapter.authenticated``, run poll loop."""
        op_ref = self._config.get("oauth_credentials_ref") or self._config.get(
            "service_account_key_ref"
        )
        tasklist_id = self._config.get("tasklist_id")
        user_email = self._config.get("user_email")
        member_id = self._config.get("member_id")
        if not isinstance(op_ref, str) or not op_ref.startswith(
            ("op://", "keychain://")
        ):
            raise ValueError(
                "credential must be a 1Password reference or macOS Keychain reference"
            )
        if not isinstance(tasklist_id, str) or not tasklist_id:
            raise ValueError("tasklist_id must be a non-empty string")
        if not isinstance(user_email, str) or "@" not in user_email:
            raise ValueError("user_email must be a valid email address")
        if not isinstance(member_id, str) or not member_id:
            raise ValueError("member_id must be a non-empty string")
        creds_json = self._credentials_resolver(op_ref)
        if op_ref.startswith("keychain://"):
            provider_account_id = self._config.get("provider_account_id")
            if not isinstance(provider_account_id, str):
                raise ValueError(
                    "installed-user OAuth requires a provider account binding"
                )
            assert_installed_user_binding(
                creds_json,
                member_id=member_id,
                provider_account_id=provider_account_id,
            )
        self._service = self._service_factory(creds_json, self._scopes, user_email)
        self._ctx = ctx
        self._status.started_at = ctx.now_factory()
        self._status.state = "running"
        # [D7, D8] adapter.authenticated v1; correlation_id from factory.
        await self._emit_authenticated(ctx=ctx, op_ref=op_ref)
        await self._poll_loop(ctx=ctx)

    async def stop(self) -> None:
        """Release the Tasks service handle. Idempotent."""
        self._stop_requested = True
        self._service = None
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def _poll_loop(self, *, ctx: AdapterContext) -> None:
        """Inbound loop. Fatal errors halt; transient stays. Tests bound
        via ``max_poll_cycles``."""
        cycles = 0
        while not self._stop_requested:
            if self._max_poll_cycles is not None and cycles >= self._max_poll_cycles:
                return
            sleep_s = float(self._poll_interval_s)
            try:
                await self._poll_once(ctx=ctx)
            except BaseException as exc:  # noqa: BLE001 — classify & emit
                severity, error_kind, retry_after_s = self._classify_tasks_error(exc)
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=retry_after_s,
                )
                if severity == "fatal":
                    self._status.state = "crashed"
                    return
                self._status.state = "backoff"
                sleep_s = self._bounded_retry_delay(retry_after_s=retry_after_s)
            else:
                # A transient provider failure is durable audit history, not a
                # permanent current-health state.  A later successful poll
                # proves the configured instance is running again.
                lease_state = self._status.extra.get("cursor_lease")
                self._status.state = (
                    "backoff"
                    if lease_state in {"circuit_open", "held_by_another_runtime"}
                    else "running"
                )
            cycles += 1
            try:
                await asyncio.sleep(sleep_s)
            except asyncio.CancelledError:
                return

    async def _poll_once(self, *, ctx: AdapterContext) -> int:
        """Mirror provider lists and emit deterministic task transitions."""
        if self._service is None:
            raise RuntimeError(
                "Google Tasks service not initialized; call start() first"
            )
        store = ctx.cursor_store
        cursor_id = self._cursor_id()
        if store is not None and not store.claim(
            cursor_id,
            self._lease_owner,
            ttl_ms=max(30_000, self._poll_interval_s * 2000),
        ):
            cursor = store.get(cursor_id)
            self._status.state = "backoff"
            self._status.extra["cursor_lease"] = (
                "circuit_open"
                if cursor.circuit_state == "open"
                else "held_by_another_runtime"
            )
            self._set_durable_status(ctx)
            return 0
        try:
            emitted, completion_watermark = await self._poll_once_owned(ctx=ctx)
            if store is not None:
                if not store.record_success(
                    cursor_id,
                    lease_owner=self._lease_owner,
                    provider_watermark=completion_watermark,
                ):
                    raise _TasksLeaseLost(
                        "Google Tasks lease lost before completion commit"
                    )
                self._set_durable_status(ctx)
            self._status.extra["cursor_lease"] = "owned"
            return emitted
        except BaseException as exc:
            if store is not None:
                _severity, _kind, retry_after_s = self._classify_tasks_error(exc)
                error_cursor = store.record_error(
                    cursor_id,
                    lease_owner=self._lease_owner,
                    recovery_after_ms=int(
                        self._bounded_retry_delay(retry_after_s=retry_after_s) * 1000
                    ),
                )
                if error_cursor is False:
                    self._status.extra["cursor_lease"] = "lost_before_error_commit"
                    self._set_durable_status(ctx)
                else:
                    durable = error_cursor.public()
                    durable["provider_watermark"] = (
                        "present" if durable.get("provider_watermark") else None
                    )
                    self._status.extra["durable_cursor"] = durable
            raise
        finally:
            if store is not None:
                store.release(cursor_id, self._lease_owner)

    async def _poll_once_owned(self, *, ctx: AdapterContext) -> tuple[int, str]:
        emitted = 0
        completion_revisions: list[str] = []
        tasklists = self._fetch_tasklists()
        observed_list_ids = {
            str(tasklist.get("id")) for tasklist in tasklists if tasklist.get("id")
        }
        if bool(self._config.get("include_all_tasklists", True)):
            for missing_id in sorted(set(self._seen_lists) - observed_list_ids):
                await self._emit_list_deleted(ctx=ctx, tasklist_id=missing_id)
                self._seen_lists.pop(missing_id, None)
                for key in [key for key in self._seen_inbound if key[0] == missing_id]:
                    self._seen_inbound.pop(key, None)
                emitted += 1
        for tasklist in tasklists:
            tasklist_id = str(tasklist.get("id") or "")
            if not tasklist_id:
                continue
            previous_list = self._seen_lists.get(tasklist_id)
            list_fingerprint = self._provider_fingerprint(
                {
                    "id": tasklist_id,
                    "title": tasklist.get("title"),
                    "etag": tasklist.get("etag"),
                    "updated": tasklist.get("updated"),
                },
                state="present",
            )
            list_external_id = f"list:{tasklist_id}"
            completion_revisions.append(f"list:{tasklist_id}:{list_fingerprint}")
            durable_list = self._durable_latest(ctx, list_external_id)
            if previous_list is None and durable_list is not None:
                if durable_list[0] == list_fingerprint:
                    self._seen_lists[tasklist_id] = dict(tasklist)
                    previous_list = self._seen_lists[tasklist_id]
                else:
                    previous_list = {"etag": "durable-prior-revision"}
            if previous_list is None or previous_list.get("etag") != tasklist.get(
                "etag"
            ):
                event_id = await self._emit_list_change(
                    ctx=ctx,
                    event_type="list.created"
                    if previous_list is None
                    else "list.updated",
                    tasklist=tasklist,
                    fingerprint=list_fingerprint,
                )
                self._record_durable(ctx, list_external_id, list_fingerprint, event_id)
                self._seen_lists[tasklist_id] = dict(tasklist)
                emitted += 1
            for task in self._fetch_tasks(tasklist_id=tasklist_id):
                task_id = task.get("id")
                if not isinstance(task_id, str) or not task_id:
                    continue
                key = (tasklist_id, task_id)
                previous = self._seen_inbound.get(key)
                task_fingerprint = self._provider_fingerprint(
                    task,
                    state=(
                        "deleted"
                        if task.get("deleted")
                        else str(task.get("status") or "needsAction")
                    ),
                )
                durable_external_id = f"item:{tasklist_id}:{task_id}"
                completion_revisions.append(
                    f"item:{tasklist_id}:{task_id}:{task_fingerprint}"
                )
                durable = self._durable_latest(ctx, durable_external_id)
                if previous is None and durable is not None:
                    if durable[0] == task_fingerprint:
                        self._seen_inbound[key] = dict(task)
                        continue
                    previous = {"status": durable[0].split(":", 1)[0]}
                if task.get("deleted"):
                    if previous is not None:
                        event_id = await self._emit_deleted(
                            ctx=ctx,
                            tasklist_id=tasklist_id,
                            task=task,
                            fingerprint=task_fingerprint,
                        )
                        self._record_durable(
                            ctx, durable_external_id, task_fingerprint, event_id
                        )
                        self._seen_inbound.pop(key, None)
                        emitted += 1
                    continue
                title = task.get("title")
                if not isinstance(title, str) or not title.strip():
                    _log.warning("skipping titleless Google task %s", task_id)
                    continue
                if previous is None:
                    event_type = "list_item.added"
                elif previous.get("etag") == task.get("etag") and previous.get(
                    "updated"
                ) == task.get("updated"):
                    continue
                elif previous.get("status") != task.get("status"):
                    event_type = (
                        "list_item.completed"
                        if task.get("status") == "completed"
                        else "list_item.uncompleted"
                    )
                else:
                    event_type = "list_item.updated"
                event_id = await self._emit_item(
                    ctx=ctx,
                    event_type=event_type,
                    tasklist=tasklist,
                    task=task,
                    fingerprint=task_fingerprint,
                )
                self._record_durable(
                    ctx, durable_external_id, task_fingerprint, event_id
                )
                self._seen_inbound[key] = dict(task)
                emitted += 1
        digest = hashlib.sha256(
            "\n".join(sorted(completion_revisions)).encode("utf-8")
        ).hexdigest()
        return emitted, f"complete:{digest}"

    def _cursor_id(self) -> str:
        return str(
            getattr(self, "instance_name", None) or self._config.get("id") or self.name
        )

    @staticmethod
    def _provider_fingerprint(value: dict[str, Any], *, state: str) -> str:
        digest = hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hexdigest()
        return f"{state}:{digest}"

    def _durable_latest(
        self, ctx: AdapterContext, external_id: str
    ) -> tuple[str, str] | None:
        if ctx.cursor_store is None:
            return None
        latest = ctx.cursor_store.latest_event(self._cursor_id(), external_id)
        if latest is not None:
            self._status.extra["restart_dedupe"] = "durable"
        return latest

    def _record_durable(
        self,
        ctx: AdapterContext,
        external_id: str,
        fingerprint: str,
        event_id: str,
    ) -> None:
        if ctx.cursor_store is None:
            return
        if not ctx.cursor_store.record_success(
            self._cursor_id(),
            lease_owner=self._lease_owner,
            mark_healthy=False,
            external_id=external_id,
            fingerprint=fingerprint,
            event_id=event_id,
        ):
            raise _TasksLeaseLost("Google Tasks lease lost before dedupe commit")

    def _set_durable_status(self, ctx: AdapterContext) -> None:
        if ctx.cursor_store is None:
            return
        durable = ctx.cursor_store.get(self._cursor_id()).public()
        durable["provider_watermark"] = (
            "present" if durable.get("provider_watermark") else None
        )
        self._status.extra["durable_cursor"] = durable

    def _fetch_tasklists(self) -> list[dict[str, Any]]:
        """Return every task list when enabled, otherwise the configured list."""
        if not bool(self._config.get("include_all_tasklists", True)) or not hasattr(
            self._service, "tasklists"
        ):
            tasklist_id = str(self._config["tasklist_id"])
            return [
                {
                    "id": tasklist_id,
                    "title": self._config.get("list_name") or tasklist_id,
                    "etag": None,
                }
            ]
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"maxResults": self.PAGE_SIZE}
            if page_token:
                params["pageToken"] = page_token
            response = self._service.tasklists().list(**params).execute()
            records.extend(response.get("items") or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                return records

    def _fetch_tasks(self, *, tasklist_id: str) -> list[dict[str, Any]]:
        """Page the complete task state, including terminal tombstones."""
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "tasklist": tasklist_id,
                "maxResults": self.PAGE_SIZE,
                "showCompleted": True,
                "showDeleted": True,
                "showHidden": True,
                "showAssigned": bool(self._config.get("show_assigned", True)),
            }
            if page_token:
                params["pageToken"] = page_token
            resp = self._service.tasks().list(**params).execute()
            records.extend(resp.get("items") or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return records

    async def _emit_list_item_added(
        self,
        *,
        ctx: AdapterContext,
        tasklist_id: str,
        task: dict[str, Any],
    ) -> None:
        """Backward-compatible helper retained for pack consumers."""
        await self._emit_item(
            ctx=ctx,
            event_type="list_item.added",
            tasklist={
                "id": tasklist_id,
                "title": self._config.get("list_name") or tasklist_id,
            },
            task=task,
        )

    @staticmethod
    def _date_components(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return {
            "year": parsed.year,
            "month": parsed.month,
            "day": parsed.day,
            "all_day": True,
            "floating": True,
        }

    def _item_payload(
        self,
        *,
        ctx: AdapterContext,
        tasklist: dict[str, Any],
        task: dict[str, Any],
    ) -> dict[str, Any]:
        assigned = bool(task.get("assignmentInfo"))
        links = []
        for link in task.get("links") or []:
            if link.get("link"):
                links.append(
                    {
                        "kind": str(link.get("type") or "provider"),
                        "url": str(link["link"]),
                        "description": link.get("description"),
                    }
                )
        updated = task.get("updated") or ctx.now_factory().isoformat()
        return {
            "member_id": str(self._config["member_id"]),
            "provider": "google_tasks",
            "provider_account_id": str(
                self._config.get("provider_account_id")
                or self._config.get("user_email")
            ),
            "list_external_id": str(tasklist.get("id") or ""),
            "item_external_id": str(task.get("id") or ""),
            "list_name": str(tasklist.get("title") or tasklist.get("id") or "Tasks"),
            "list_color": None,
            "title": str(task.get("title") or ""),
            "notes": task.get("notes"),
            "url": task.get("webViewLink"),
            "priority": _GOOGLE_TASKS_DEFAULT_PRIORITY,
            "completed": task.get("status") == "completed",
            "completion_at": task.get("completed"),
            "start": None,
            "due": self._date_components(task.get("due")),
            "alarms": [],
            "recurrence_rules": [],
            "flagged": None,
            "tags": None,
            "parent_external_id": task.get("parent"),
            "sort_order": None,
            "linked_item_kind": None,
            "linked_item_id": None,
            "completion_policy": "manual",
            "provider_revision": task.get("etag"),
            "last_modified_at": updated,
            "sync_cycle_id": ctx.correlation_id_factory(),
            "provider_position": task.get("position"),
            "source_links": links,
            "provider_data": {
                "self_link": task.get("selfLink"),
                "hidden": bool(task.get("hidden")),
                "assignment_info": task.get("assignmentInfo"),
            },
            "read_only_reason": (
                "Assigned Google Tasks have provider-enforced write restrictions."
                if assigned and self._config.get("assigned_tasks_read_only", True)
                else None
            ),
        }

    async def _emit_item(
        self,
        *,
        ctx: AdapterContext,
        event_type: str,
        tasklist: dict[str, Any],
        task: dict[str, Any],
        fingerprint: str | None = None,
    ) -> str:
        fingerprint = fingerprint or self._provider_fingerprint(
            task, state=str(task.get("status") or "needsAction")
        )
        payload = self._item_payload(ctx=ctx, tasklist=tasklist, task=task)
        payload["sync_cycle_id"] = (
            f"google-tasks-revision:{fingerprint.split(':', 1)[1]}"
        )
        return await self._append(
            ctx=ctx,
            event_type=event_type,
            payload=payload,
            schema_version=3,
            idempotency_key=self._revision_idempotency_key(
                event_type,
                str(tasklist.get("id") or ""),
                str(task.get("id") or ""),
                fingerprint,
            ),
        )

    async def _emit_list_change(
        self,
        *,
        ctx: AdapterContext,
        event_type: str,
        tasklist: dict[str, Any],
        fingerprint: str,
    ) -> str:
        return await self._append(
            ctx=ctx,
            event_type=event_type,
            payload={
                "member_id": str(self._config["member_id"]),
                "provider": "google_tasks",
                "provider_account_id": str(
                    self._config.get("provider_account_id")
                    or self._config.get("user_email")
                ),
                "list_external_id": str(tasklist.get("id") or ""),
                "list_name": str(tasklist.get("title") or "Tasks"),
                "color": None,
                "is_default": tasklist.get("id") == "@default",
                "provider_revision": tasklist.get("etag"),
                "changed_at": tasklist.get("updated") or ctx.now_factory().isoformat(),
            },
            idempotency_key=self._revision_idempotency_key(
                event_type, str(tasklist.get("id") or ""), "", fingerprint
            ),
        )

    async def _emit_deleted(
        self,
        *,
        ctx: AdapterContext,
        tasklist_id: str,
        task: dict[str, Any],
        fingerprint: str,
    ) -> str:
        return await self._append(
            ctx=ctx,
            event_type="list_item.deleted",
            schema_version=2,
            payload={
                "member_id": str(self._config["member_id"]),
                "provider": "google_tasks",
                "provider_account_id": str(
                    self._config.get("provider_account_id")
                    or self._config.get("user_email")
                ),
                "list_external_id": tasklist_id,
                "item_external_id": str(task.get("id") or ""),
                "deleted_at": task.get("updated") or ctx.now_factory().isoformat(),
                "sync_cycle_id": f"google-tasks-revision:{fingerprint.split(':', 1)[1]}",
            },
            idempotency_key=self._revision_idempotency_key(
                "list_item.deleted", tasklist_id, str(task.get("id") or ""), fingerprint
            ),
        )

    def _revision_idempotency_key(
        self,
        event_type: str,
        list_id: str,
        item_id: str,
        fingerprint: str,
    ) -> str:
        seed = "\x1f".join(
            (
                self._cursor_id(),
                str(self._config.get("member_id") or ""),
                str(
                    self._config.get("provider_account_id")
                    or self._config.get("user_email")
                    or ""
                ),
                list_id,
                item_id,
                event_type,
                fingerprint,
            )
        )
        return f"google-tasks:provider-revision:{hashlib.sha256(seed.encode('utf-8')).hexdigest()}"

    async def _emit_list_deleted(
        self, *, ctx: AdapterContext, tasklist_id: str
    ) -> None:
        await self._append(
            ctx=ctx,
            event_type="list.deleted",
            payload={
                "member_id": str(self._config["member_id"]),
                "provider": "google_tasks",
                "provider_account_id": str(
                    self._config.get("provider_account_id")
                    or self._config.get("user_email")
                ),
                "list_external_id": tasklist_id,
                "deleted_at": ctx.now_factory().isoformat(),
            },
        )

    async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult:
        """Dispatch one explicitly declared Google Tasks operation."""
        if operation.kind not in self.write_capabilities:
            raise WriteCapabilityError(
                f"GoogleTasksAdapter received undeclared kind={operation.kind!r}"
            )
        service = self._service
        if service is None:
            return WriteResult(applied=False, external_id=None, suppressed=False)

        payload = operation.payload
        tasklist_id = str(
            payload.get("tasklist_id") or self._config.get("tasklist_id") or ""
        )
        target_id = str(operation.target_external_id or payload.get("task_id") or "")
        task_body = {
            key: payload[key]
            for key in ("title", "notes", "due", "status", "completed")
            if key in payload and payload[key] is not None
        }
        list_body = {"title": str(payload.get("title") or "")}

        def _request() -> Any:
            tasks = service.tasks()
            tasklists = service.tasklists() if hasattr(service, "tasklists") else None
            if operation.kind == "create_task":
                kwargs: dict[str, Any] = {"tasklist": tasklist_id, "body": task_body}
                for key in ("parent", "previous"):
                    if payload.get(key):
                        kwargs[key] = payload[key]
                return tasks.insert(**kwargs)
            if operation.kind == "update_task":
                return tasks.patch(tasklist=tasklist_id, task=target_id, body=task_body)
            if operation.kind in {"complete_task", "uncomplete_task"}:
                body: dict[str, Any] = {
                    "status": (
                        "completed"
                        if operation.kind == "complete_task"
                        else "needsAction"
                    )
                }
                if operation.kind == "complete_task":
                    body["completed"] = (
                        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    )
                return tasks.patch(tasklist=tasklist_id, task=target_id, body=body)
            if operation.kind == "delete_task":
                return tasks.delete(tasklist=tasklist_id, task=target_id)
            if operation.kind == "move_task":
                kwargs = {"tasklist": tasklist_id, "task": target_id}
                for key in ("parent", "previous"):
                    if payload.get(key):
                        kwargs[key] = payload[key]
                destination = payload.get("destinationTasklist") or payload.get(
                    "destination_list_external_id"
                )
                if destination:
                    kwargs["destinationTasklist"] = destination
                return tasks.move(**kwargs)
            if operation.kind == "clear_completed_tasks":
                return tasks.clear(tasklist=tasklist_id)
            if tasklists is None:
                raise RuntimeError("Google Tasks service does not expose tasklists")
            if operation.kind == "create_list":
                return tasklists.insert(body=list_body)
            if operation.kind == "update_list":
                return tasklists.patch(
                    tasklist=target_id or tasklist_id, body=list_body
                )
            if operation.kind == "delete_list":
                return tasklists.delete(tasklist=target_id or tasklist_id)
            raise WriteCapabilityError(f"unsupported operation {operation.kind!r}")

        async def _do_write() -> dict[str, Any]:
            request = _request()
            expected_revision = payload.get("expected_provider_revision")
            if expected_revision and hasattr(request, "headers"):
                request.headers["If-Match"] = expected_revision
            return request.execute() or {}

        session = self._observation_session
        manager = self._observation_manager
        event_log = self._observation_event_log
        if session is None or manager is None or event_log is None:
            raise RuntimeError(
                "Google Tasks observation seam is required for external writes"
            )

        response: dict[str, Any] = {}
        try:
            outbound_result = await observation_outbound(
                session,
                f"tasks.{operation.kind}",
                {
                    "tasklist_id": tasklist_id,
                    "target_external_id": target_id or None,
                    "fields": sorted(task_body or list_body),
                },
                _do_write,
                manager=manager,
                event_sink=event_log,
                target_channel="tasks-google-tasks",
                target_identifier=tasklist_id,
            )
            if outbound_result.suppressed:
                return WriteResult(applied=False, external_id=None, suppressed=True)
            response = outbound_result.result or {}
        except BaseException as exc:  # noqa: BLE001 — classify & emit
            ctx = self._ctx
            severity, error_kind, retry_after_s = self._classify_tasks_error(exc)
            if ctx is not None:
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=retry_after_s,
                )
            raise

        external_id = response.get("id") or target_id or tasklist_id
        return WriteResult(
            applied=True,
            external_id=str(external_id) if external_id else None,
            suppressed=False,
        )

    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        """Phase A no-op satisfying the Cat-B ABC. Inbound flow runs
        through :meth:`_poll_once`; Google Tasks has no push-notification
        ingress so this hook stays a no-op."""
        return None

    async def _emit_authenticated(self, *, ctx: AdapterContext, op_ref: str) -> None:
        """``adapter.authenticated@v1``; carries op-ref, NEVER the secret."""
        await self._append(
            ctx=ctx,
            event_type="adapter.authenticated",
            payload={
                "adapter_name": self.name,
                "adapter_family": self.family,
                "token_storage_ref": op_ref,
                "authenticated_at": ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scopes": list(self._scopes),
            },
        )

    async def _emit_error(
        self,
        *,
        ctx: AdapterContext,
        severity: str,
        error_kind: str,
        error_message: str,
        retry_after_s: int | None,
    ) -> None:
        """``adapter.error@v1`` — drives the supervisor's backoff."""
        payload: dict[str, Any] = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "severity": severity,
            "error_kind": error_kind,
            "error_message": error_message[:2000],
            "retry_after_s": retry_after_s,
            "occurred_at": ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._status.last_error = dict(payload)
        await self._append(ctx=ctx, event_type="adapter.error", payload=payload)

    async def _append(
        self,
        *,
        ctx: AdapterContext,
        event_type: str,
        payload: dict[str, Any],
        causation_id: str | None = None,
        schema_version: int = 1,
        idempotency_key: str | None = None,
    ) -> str:
        """[§7.3, D17] append to the event log; never write projections."""
        return await write_adapter_event(
            ctx,
            adapter_name=self.name,
            sensitivity_floor=self.sensitivity_floor,
            config=self._config,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
            schema_version=schema_version,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _classify_tasks_error(
        exc: BaseException,
    ) -> tuple[str, str, int | None]:
        """``HttpError`` duck-typed via ``.resp.status`` so the SDK is
        not imported at module load — same pattern as Gmail / Google
        Calendar."""
        status: int | None = None
        retry_after: int | None = None
        resp = getattr(exc, "resp", None)
        if resp is not None:
            raw_status: Any = getattr(resp, "status", None)
            if raw_status is None and hasattr(resp, "get"):
                raw_status = resp.get("status")
            try:
                status = int(raw_status) if raw_status is not None else None
            except (TypeError, ValueError):
                status = None
            retry_header: Any = (
                resp.get("retry-after") or resp.get("Retry-After")
                if hasattr(resp, "get")
                else None
            )
            try:
                retry_after = int(retry_header) if retry_header is not None else None
            except (TypeError, ValueError):
                retry_after = None
        if status == 429:
            return ("transient", "rate_limited", retry_after)
        if status in (401, 403):
            return ("fatal", "auth_failed", retry_after)
        if status is not None and 500 <= status < 600:
            return ("transient", "network", retry_after)
        if status is not None and 400 <= status < 500:
            return ("transient", "api_changed", retry_after)
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return ("transient", "network", retry_after)
        return ("transient", "unknown", retry_after)

    def _bounded_retry_delay(self, *, retry_after_s: int | None) -> float:
        base = max(1.0, float(self._poll_interval_s))
        requested = float(retry_after_s or 0)
        return min(max(base, requested), max(base, 900.0))


__all__ = [
    "CredentialsResolver",
    "GOOGLE_TASKS_READONLY_SCOPES",
    "GOOGLE_TASKS_SCOPES",
    "GoogleTasksAdapter",
    "ServiceFactory",
]
