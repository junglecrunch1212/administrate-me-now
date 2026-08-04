"""Google Calendar adapter pack — fresh Cohort D Cat-B adapter (prompt 11b-iii).

Inherits :class:`adminme.lib.adapters.ExternalStateMirrorAdapter` per memo
05 §1.2 / §2.3 / §2.5 / §3.2 / [D19] / [PM-34] / [UT-27]. First fresh
Cat-B adapter under the canonical-build pattern: inbound poll of Google
Calendar v3 ``events.list`` maps onto EXISTING ``calendar.event_added@v1``
at ``adminme/events/schemas/ingest.py`` (no new schema per [D7]);
outbound ``create_event`` calls ``events.insert`` wrapped through
:func:`adminme.lib.observation.outbound` per [§6.20]. Asymmetric
``write_capabilities=["create_event"]`` per [UT-27]; ``update_event`` /
``delete_event`` are out of scope per parent draft.
"""

# [§8.2, D6] no LLM SDK imports under packs/adapters/.
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import AdapterContext, AdapterStatus, SensitivityFloor
from adminme.adapters.emission import write_adapter_event
from adminme.lib.adapters import (
    ChangeEnvelope,
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.observation import outbound as observation_outbound

_log = logging.getLogger(__name__)

GOOGLE_CALENDAR_SCOPES: list[str] = ["https://www.googleapis.com/auth/calendar"]

# [§6 invariant 10] privileged > normal; config may raise but not lower.
_FLOOR_RANK: dict[str, int] = {"normal": 0, "privileged": 1}

_DEFAULT_POLL_INTERVAL_S: int = 60
_DEFAULT_PAGE_SIZE: int = 250

CredentialsResolver = Callable[[str], dict[str, Any]]
"""``op://`` ref → service-account JSON. Tests inject a stub; production
calls the 1Password CLI."""

ServiceFactory = Callable[[dict[str, Any], list[str], str], Any]
"""(credentials, scopes, subject_email) → Calendar service. Tests inject
a fake; production lazily imports the Google SDK."""


def _default_credentials_resolver(op_ref: str) -> dict[str, Any]:
    """Read service-account JSON from an ``op://`` reference; mirrors Gmail."""
    if not op_ref or not op_ref.startswith("op://"):
        raise ValueError(f"invalid op-ref: {op_ref!r}")
    op_bin = shutil.which("op")
    if op_bin is None:
        raise RuntimeError(f"1Password CLI `op` not on PATH; cannot resolve {op_ref!r}")
    proc = subprocess.run(
        [op_bin, "read", "--no-newline", op_ref],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return json.loads(proc.stdout.decode("utf-8"))


def _default_service_factory(
    credentials_json: dict[str, Any],
    scopes: list[str],
    subject_email: str,
) -> Any:
    """Build Calendar v3 service; lazy import keeps SDK out of CI."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_info(credentials_json, scopes=scopes)
    if subject_email:
        creds = creds.with_subject(subject_email)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _extract_when(when: dict[str, Any] | None) -> str:
    """``dateTime`` or ``date`` → str; ``""`` if neither so v1 schema's
    ``min_length=1`` surfaces it."""
    if not isinstance(when, dict):
        return ""
    return str(when.get("dateTime") or when.get("date") or "")


class GoogleCalendarAdapter(ExternalStateMirrorAdapter):
    """Google Calendar Cat-B standalone adapter (fresh canonical-build pack).

    Polls ``events.list`` every ``poll_interval_s`` and emits one
    ``calendar.event_added@v1`` per non-deduped event mapping Google's
    response onto the existing schema BYTE-FOR-BYTE per [D7] + canonical
    11a's "MAP DON'T REGISTER". Outbound :meth:`_apply_write_impl`
    accepts ``WriteOperation(kind="create_event")`` and routes
    ``events.insert`` through :func:`observation_outbound` per [§6.20].
    """

    name: ClassVar[str] = "calendaring-google-calendar"
    family: ClassVar[Literal["reminders", "calendaring", "contacts", "tasks"]] = (
        "calendaring"
    )
    version: ClassVar[str] = "1.0.0"
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    # [memo 05 §3.2, PM-35] class-level mirror of the manifest's
    # capabilities. One Cat-B entry declaring the EXISTING on-main
    # ``calendar.event_added`` family — NO new schema per [D7].
    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-B",
            "runtime": "central",
            "event_families": ["calendar.event_added"],
        },
    ]

    # [UT-27] asymmetric — declare ONLY what is implemented.
    write_capabilities: ClassVar[list[str]] = ["create_event"]

    # [memo 05 §2.5, PM-35] projection-row column for per-member vs
    # shared rows on the future calendar projection.
    sharing_model_discriminator: ClassVar[str] = "owner_member_id"

    PACK_ID: ClassVar[str] = "adapter:calendaring-google-calendar"
    HARDCODED_FLOOR: ClassVar[SensitivityFloor] = "normal"
    SCOPES: ClassVar[list[str]] = list(GOOGLE_CALENDAR_SCOPES)
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
        # Dedupe by stable event id; tracks ``updated`` so future
        # event_updated (out of scope) can diff without a re-fetch.
        self._seen_inbound: dict[str, str] = {}
        # [§6.20, PM-27] observation seam slots; Phase A leaves unset.
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
        op_ref = self._config.get("service_account_key_ref")
        calendar_id = self._config.get("calendar_id")
        user_email = self._config.get("user_email")
        if not isinstance(op_ref, str) or not op_ref.startswith("op://"):
            raise ValueError(
                "service_account_key_ref must be a 1Password reference "
                "(op://<vault>/<item>/<field>)"
            )
        if not isinstance(calendar_id, str) or not calendar_id:
            raise ValueError("calendar_id must be a non-empty string")
        if not isinstance(user_email, str) or "@" not in user_email:
            raise ValueError("user_email must be a valid email address")
        creds_json = self._credentials_resolver(op_ref)
        self._service = self._service_factory(creds_json, self.SCOPES, user_email)
        self._ctx = ctx
        self._status.started_at = ctx.now_factory()
        self._status.state = "running"
        # [D7, D8] adapter.authenticated v1; correlation_id from factory.
        await self._emit_authenticated(ctx=ctx, op_ref=op_ref)
        await self._poll_loop(ctx=ctx)

    async def stop(self) -> None:
        """Release the Calendar service handle. Idempotent."""
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
            try:
                await self._poll_once(ctx=ctx)
            except BaseException as exc:  # noqa: BLE001 — classify & emit
                severity, error_kind, retry_after_s = self._classify_calendar_error(exc)
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
            cycles += 1
            try:
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                return

    async def _poll_once(self, *, ctx: AdapterContext) -> int:
        """Fetch + dedupe + emit. ``_seen_inbound`` is the dedupe gate
        per F-3 — re-poll on the same event id is a no-op."""
        if self._service is None:
            raise RuntimeError(
                "Google Calendar service not initialized; call start() first"
            )
        calendar_id = str(self._config["calendar_id"])
        time_min = ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ")
        events = self._fetch_events(calendar_id=calendar_id, time_min=time_min)
        emitted = 0
        for event in events:
            event_id = event.get("id")
            if not isinstance(event_id, str) or not event_id:
                continue
            if event_id in self._seen_inbound:
                continue
            await self._emit_calendar_event_added(
                ctx=ctx, calendar_id=calendar_id, event=event
            )
            self._seen_inbound[event_id] = str(event.get("updated") or "")
            emitted += 1
        return emitted

    def _fetch_events(self, *, calendar_id: str, time_min: str) -> list[dict[str, Any]]:
        """Page ``events.list(singleEvents=True, orderBy=startTime,
        timeMin=...)`` until ``nextPageToken`` is absent."""
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "calendarId": calendar_id,
                "maxResults": self.PAGE_SIZE,
                "singleEvents": True,
                "orderBy": "startTime",
                "timeMin": time_min,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = self._service.events().list(**params).execute()
            records.extend(resp.get("items") or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return records

    async def _emit_calendar_event_added(
        self,
        *,
        ctx: AdapterContext,
        calendar_id: str,
        event: dict[str, Any],
    ) -> None:
        """Map a Google event onto ``CalendarEventAddedV1`` per [D7] +
        canonical 11a's "MAP DON'T REGISTER"."""
        await self._append(
            ctx=ctx,
            event_type="calendar.event_added",
            payload={
                "source": "google_calendar",
                "external_event_id": str(event.get("id") or ""),
                "calendar_id": calendar_id,
                "summary": str(event.get("summary") or ""),
                "start": _extract_when(event.get("start")),
                "end": _extract_when(event.get("end")),
                "location": event.get("location"),
                "attendees": list(event.get("attendees") or []),
                "body": event.get("description"),
            },
        )

    async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult:
        """[UT-27] gate already checked ``operation.kind``. Phase B routes
        ``events.insert`` through :func:`observation_outbound` per
        [§6.20]; Phase A direct-calls when seams are unset."""
        if operation.kind != "create_event":  # defense-in-depth
            raise WriteCapabilityError(
                f"GoogleCalendarAdapter._apply_write_impl received "
                f"undeclared kind={operation.kind!r}"
            )
        service = self._service
        if service is None:
            return WriteResult(applied=False, external_id=None, suppressed=False)

        payload = operation.payload
        calendar_id = str(
            payload.get("calendar_id") or self._config.get("calendar_id") or ""
        )
        event_body: dict[str, Any] = {
            "summary": str(payload.get("summary") or ""),
            "start": payload.get("start") or {},
            "end": payload.get("end") or {},
        }
        if (loc := payload.get("location")) is not None:
            event_body["location"] = loc
        if (desc := payload.get("description") or payload.get("body")) is not None:
            event_body["description"] = desc
        if (att := payload.get("attendees")) is not None:
            event_body["attendees"] = list(att)

        async def _do_insert() -> dict[str, Any]:
            return (
                service.events()
                .insert(calendarId=calendar_id, body=event_body)
                .execute()
            )

        session = self._observation_session
        manager = self._observation_manager
        event_log = self._observation_event_log

        response: dict[str, Any] = {}
        try:
            if session is not None and manager is not None and event_log is not None:
                # [§6.20] Phase B: observation seam wraps events.insert.
                outbound_result = await observation_outbound(
                    session,
                    "calendar.create_event",
                    {
                        "calendar_id": calendar_id,
                        "summary": event_body.get("summary", ""),
                        "start": event_body.get("start"),
                        "end": event_body.get("end"),
                    },
                    _do_insert,
                    manager=manager,
                    event_sink=event_log,
                    target_channel="calendaring-google-calendar",
                    target_identifier=calendar_id,
                )
                if outbound_result.suppressed:
                    # F-3 (HARD): suppressed → unapplied; caller does NOT
                    # emit externally-visible side effects.
                    return WriteResult(applied=False, external_id=None, suppressed=True)
                response = outbound_result.result or {}
            else:
                response = await _do_insert()
        except BaseException as exc:  # noqa: BLE001 — classify & emit
            ctx = self._ctx
            severity, error_kind, retry_after_s = self._classify_calendar_error(exc)
            if ctx is not None:
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=retry_after_s,
                )
            raise

        external_id = response.get("id")
        return WriteResult(
            applied=True,
            external_id=str(external_id) if external_id else None,
            suppressed=False,
        )

    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        """Phase A no-op satisfying the Cat-B ABC. Inbound flow runs
        through :meth:`_poll_once`; future push-notification ingress
        (out of scope per parent draft) will dispatch through this hook."""
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
                "scopes": list(self.SCOPES),
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
    ) -> None:
        """[§7.3, D17] append to the event log; never write projections."""
        try:
            await write_adapter_event(
                ctx,
                adapter_name=self.name,
                sensitivity_floor=self.sensitivity_floor,
                config=self._config,
                event_type=event_type,
                payload=payload,
                causation_id=causation_id,
            )
        except Exception:  # noqa: BLE001 — best-effort emission
            _log.exception(
                "google-calendar adapter failed to emit %s event", event_type
            )

    @staticmethod
    def _classify_calendar_error(
        exc: BaseException,
    ) -> tuple[str, str, int | None]:
        """``HttpError`` duck-typed via ``.resp.status`` so the SDK is
        not imported at module load — same pattern as Gmail."""
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


__all__ = [
    "CredentialsResolver",
    "GOOGLE_CALENDAR_SCOPES",
    "GoogleCalendarAdapter",
    "ServiceFactory",
]
