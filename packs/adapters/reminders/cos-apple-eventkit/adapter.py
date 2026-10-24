"""One service-owned EventKit list, read-only through the production event sink."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import AdapterContext, AdapterStatus, SensitivityFloor
from adminme.adapters.cos_apple_reminders import (
    ADAPTER_KIND,
    digest,
    local_device_identity,
    validate_adapter_config,
    verify_device,
)
from adminme.adapters.cos_reminders_helper import HelperRemindersReader, redacted_error
from adminme.adapters.emission import write_adapter_event
from adminme.events.schemas.lists import ListItemV4
from adminme.provider_bindings import adapter_owner_coordinate_id
from adminme.lib.adapters.external_state_mirror_base import (
    ExternalStateMirrorAdapter,
    ChangeEnvelope,
    WriteOperation,
    WriteResult,
    WriteCapabilityError,
)


class CoSAppleRemindersAdapter(ExternalStateMirrorAdapter):
    name: ClassVar[str] = ADAPTER_KIND
    family: ClassVar[Literal["reminders", "calendaring", "contacts", "tasks"]] = (
        "reminders"
    )
    version: ClassVar[str] = "1.0.0"
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"
    write_capabilities: ClassVar[list[str]] = []
    sharing_model_discriminator: ClassVar[str] = "owner_service_principal_id"
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
        }
    ]

    def __init__(
        self,
        *,
        config: dict[str, Any],
        reader_factory: Callable[[], Any] | None = None,
        device_identity: Callable[[], tuple[str, int]] = local_device_identity,
        poll_interval_s: float = 30,
    ) -> None:
        self._config = config
        self._selection = validate_adapter_config(config)
        self._reader_factory = reader_factory or HelperRemindersReader
        self._device_identity = device_identity
        self._interval = poll_interval_s
        self._reader: Any = None
        self._task: asyncio.Task[None] | None = None
        self._status = AdapterStatus(state="stopped")
        self._lease = str(uuid.uuid4())

    async def start(self, *, ctx: AdapterContext) -> None:
        validate_adapter_config(self._config)
        verify_device(self._selection, self._device_identity())
        if ctx.cursor_store is None or ctx.source_events is None:
            raise RuntimeError(
                "CoS Reminders requires durable snapshot state and receipts"
            )
        self._status = AdapterStatus(state="starting", started_at=ctx.now_factory())
        try:
            self._reader = self._reader_factory()
            await self._poll_once(ctx=ctx)
        except Exception as exc:
            self._status.state = "crashed"
            raise RuntimeError(
                "CoS Reminders startup read failed: " + redacted_error(exc)
            ) from None
        await self._emit(
            ctx,
            "adapter.authenticated",
            3,
            {
                "adapter_name": self.name,
                "adapter_family": self.family,
                "authorization_kind": "os-permission",
                "authenticated_at": ctx.now_factory().isoformat(),
                "configuration_sha256": digest(self._config),
                "device_identity_sha256": self._selection.device_identity_sha256,
                "source_ownership_evidence_sha256": self._selection.source_ownership_evidence_sha256,
                "scopes": self._config["permission_scope"],
            },
        )
        self._task = asyncio.create_task(self._poll(ctx))
        try:
            # The supervisor owns start() for the entire acquisition lifetime.
            # Returning here makes its inventory report a successfully read list
            # as stopped while an unobserved task continues polling.
            await self._task
        finally:
            self._task = None
            self._reader = None
            self._status.state = "stopped"

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._reader = None
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult:
        raise WriteCapabilityError("CoS Reminders does not authorize provider writes")

    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        raise ValueError("CoS Reminders accepts complete selected snapshots only")

    async def _poll(self, ctx: AdapterContext) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._poll_once(ctx=ctx)
            except Exception as exc:
                error = {
                    "adapter_name": self.name,
                    "adapter_family": self.family,
                    "severity": "transient",
                    "error_kind": "auth_failed"
                    if isinstance(exc, PermissionError)
                    else "unknown",
                    "error_message": "Selected Reminders acquisition failed: "
                    + redacted_error(exc),
                    "occurred_at": ctx.now_factory().isoformat(),
                }
                self._status.state = "backoff"
                self._status.last_error = error
                await self._emit(ctx, "adapter.error", 1, error)

    def _owner(self) -> dict[str, Any]:
        return {
            "member_id": None,
            "service_principal_id": self._config["service_principal_id"],
            "workspace": "service",
            "provider": "apple_reminders",
            "provider_account_id": self._selection.provider_account_id,
            "list_external_id": self._selection.list_id,
        }

    async def _emit(
        self,
        ctx: AdapterContext,
        kind: str,
        version: int,
        payload: dict[str, Any],
        key: str | None = None,
    ) -> str:
        return await write_adapter_event(
            ctx,
            adapter_name=self.name,
            sensitivity_floor=self.sensitivity_floor,
            config=self._config,
            event_type=kind,
            schema_version=version,
            payload=payload,
            idempotency_key=key,
        )

    @staticmethod
    def _state(raw: str | None) -> dict[str, Any]:
        if raw is None:
            return {"revision": 0, "list_digest": None, "items": {}}
        state = json.loads(raw)
        if (
            not isinstance(state, dict)
            or set(state) != {"revision", "list_digest", "items"}
            or type(state["revision"]) is not int
            or state["revision"] < 0
            or not isinstance(state["items"], dict)
            or (
                state["list_digest"] is not None
                and not isinstance(state["list_digest"], str)
            )
        ):
            raise ValueError("CoS Reminders snapshot checkpoint is malformed")
        for key, item in state["items"].items():
            if (
                not isinstance(key, str)
                or not isinstance(item, dict)
                or set(item) != {"digest", "completed"}
                or not isinstance(item["digest"], str)
                or type(item["completed"]) is not bool
            ):
                raise ValueError("CoS Reminders item checkpoint is malformed")
        return state

    def _item(
        self, item: Any, list_name: str, ctx: AdapterContext
    ) -> tuple[dict[str, Any], str]:
        if (
            item.list_identifier != self._selection.list_id
            or not item.calendarItemIdentifier
        ):
            raise PermissionError("Reminders item is outside its selected source")
        modified = item.last_modified_at.isoformat() if item.last_modified_at else None
        priority = int(item.priority)
        payload = {
            **self._owner(),
            "item_external_id": item.calendarItemIdentifier,
            "list_name": list_name,
            "title": item.title,
            "notes": item.notes,
            "url": item.url,
            "priority": 0
            if priority == 0
            else 1
            if priority < 5
            else 5
            if priority == 5
            else 9,
            "completed": item.completed,
            "completion_at": item.completion_date.isoformat()
            if item.completion_date
            else None,
            "start": item.start_components,
            "due": item.due_components,
            "alarms": item.alarms,
            "recurrence_rules": item.recurrence_rules,
            "last_modified_at": modified,
            "provider_data": {"native_source_kind": self._selection.source_kind},
            "read_only_reason": "CoS-owned Reminders acquisition is read-only",
        }
        payload.update(
            last_modified_at=modified or ctx.now_factory().isoformat(),
            sync_cycle_id="cos-reminders:pending",
        )
        # Native absolute alarms carry datetime values. Normalize the complete
        # schema before hashing; only the generated observation clock is omitted.
        normalized = ListItemV4.model_validate(payload).model_dump(mode="json")
        fingerprint_payload = {
            key: value
            for key, value in normalized.items()
            if key not in {"provider_revision", "sync_cycle_id"}
        }
        if modified is None:
            fingerprint_payload["last_modified_at"] = None
        fingerprint = digest(fingerprint_payload)
        normalized.update(
            provider_revision=fingerprint,
            sync_cycle_id="cos-reminders:" + fingerprint,
        )
        return normalized, fingerprint

    async def _poll_once(self, *, ctx: AdapterContext) -> int:
        store = ctx.cursor_store
        if store is None or self._reader is None:
            raise RuntimeError("CoS Reminders is not prepared")
        tenant_id = ctx.event_sink.tenant_id
        if not tenant_id:
            raise RuntimeError("CoS Reminders event sink has no tenant")
        coordinate = (
            adapter_owner_coordinate_id(self.name, self._config)
            + ":"
            + digest(self._selection.list_id)
        )
        if not store.claim(coordinate, self._lease, ttl_ms=120000):
            return 0
        try:
            before = self._state(store.get(coordinate).provider_watermark)
            snapshot = await asyncio.to_thread(self._reader.read, self._selection)
            if (
                snapshot.source_id != self._selection.source_id
                or snapshot.list_id != self._selection.list_id
                or (snapshot.list_name is None and snapshot.items)
            ):
                raise PermissionError(
                    "Reminders snapshot does not match its reviewed selection"
                )
            if snapshot.list_name is None and before["revision"] == 0:
                raise LookupError("The selected existing Reminders list is unavailable")
            # Validate the complete snapshot before publishing anything or inferring deletions.
            items: dict[str, tuple[dict[str, Any], str]] = {}
            for item in snapshot.items:
                payload, fingerprint = self._item(item, snapshot.list_name, ctx)
                key = payload["item_external_id"]
                if key in items:
                    raise ValueError(
                        "Reminders snapshot contains duplicate identifiers"
                    )
                items[key] = (payload, fingerprint)
            list_digest = (
                digest(snapshot.list_name) if snapshot.list_name is not None else None
            )
            current = {
                "revision": before["revision"] + 1,
                "list_digest": list_digest,
                "items": {
                    key: {"digest": value[1], "completed": value[0]["completed"]}
                    for key, value in items.items()
                },
            }
            if (
                current["list_digest"] == before["list_digest"]
                and current["items"] == before["items"]
            ):
                self._status.state = "running"
                self._status.last_error = None
                return 0
            revision = current["revision"]
            prefix = "cos-reminders:" + digest([coordinate, revision]) + ":"
            count = 0

            async def emit(
                kind: str, version: int, payload: dict[str, Any], identifier: str
            ) -> None:
                nonlocal count
                key = prefix + digest([kind, identifier, current])
                if ctx.source_events is None:
                    raise RuntimeError(
                        "CoS Reminders source receipt lookup is unavailable"
                    )
                existing = await ctx.source_events.get_by_idempotency(tenant_id, key)
                if existing is not None:
                    expected_scope = (
                        "service-private:" + self._config["service_principal_id"]
                    )
                    stored = existing.get("payload")
                    clock_fields = {"changed_at", "deleted_at", "last_modified_at"}
                    if (
                        existing.get("type") != kind
                        or existing.get("schema_version") != version
                        or existing.get("source_adapter") != self.name
                        or existing.get("source_account_id")
                        != self._selection.provider_account_id
                        or existing.get("owner_scope") != expected_scope
                        or existing.get("visibility_scope") != expected_scope
                        or not isinstance(stored, dict)
                        or {k: v for k, v in stored.items() if k not in clock_fields}
                        != {k: v for k, v in payload.items() if k not in clock_fields}
                    ):
                        raise ValueError("CoS Reminders retry receipt does not match")
                    # Preserve the original observation timestamps. Append-only
                    # dedupe rejects changed payloads under the same retry key.
                    payload = stored
                await self._emit(ctx, kind, version, payload, key)
                count += 1

            now = ctx.now_factory().isoformat()
            if list_digest != before["list_digest"]:
                if list_digest is None:
                    await emit(
                        "list.deleted", 2, {**self._owner(), "deleted_at": now}, "list"
                    )
                else:
                    await emit(
                        "list.created"
                        if before["list_digest"] is None
                        else "list.updated",
                        2,
                        {
                            **self._owner(),
                            "list_name": snapshot.list_name,
                            "is_default": False,
                            "provider_revision": list_digest,
                            "changed_at": now,
                        },
                        "list",
                    )
            for key, (payload, fingerprint) in items.items():
                prior = before["items"].get(key)
                if prior and prior["digest"] == fingerprint:
                    continue
                kind = (
                    "list_item.added"
                    if prior is None
                    else (
                        "list_item.completed"
                        if payload["completed"]
                        else "list_item.uncompleted"
                    )
                    if prior["completed"] != payload["completed"]
                    else "list_item.updated"
                )
                await emit(kind, 4, payload, key)
            for key in sorted(before["items"].keys() - items.keys()):
                await emit(
                    "list_item.deleted",
                    3,
                    {
                        **self._owner(),
                        "item_external_id": key,
                        "deleted_at": now,
                        "sync_cycle_id": prefix,
                    },
                    key,
                )
            if not store.record_success(
                coordinate,
                lease_owner=self._lease,
                provider_watermark=json.dumps(
                    current, sort_keys=True, separators=(",", ":")
                ),
            ):
                raise RuntimeError("CoS Reminders snapshot lease expired")
            self._status.state = "running"
            self._status.last_error = None
            self._status.last_cursor_advance_at = ctx.now_factory()
            return count
        finally:
            store.release(coordinate, self._lease)
