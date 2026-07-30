"""Apple Calendar adapter pack — fresh Cohort D Cat-B central pack (prompt 11b-ii-β-α).

Inherits :class:`adminme.lib.adapters.ExternalStateMirrorAdapter` per memo
05 §1.2 / §2.3 / §2.5 / §3.2 / [D19] / [PM-34] / [UT-27]. Inbound EKEventStore
poll maps onto EXISTING ``calendar.event_added@v1`` (no new schema per [D7]);
outbound ``create_event`` saves an ``EKEvent`` wrapped through
:func:`adminme.lib.observation.outbound` per [§6.20]. Asymmetric
``write_capabilities=["create_event"]`` per [UT-27]. Central deployment
ONLY (F-7) — dual-deployment is 11b-ii-β-β scope per
`docs/11-split-memo-11b-ii-beta.md` §2.
"""

# [§8.2, D6] no LLM SDK imports under packs/adapters/.
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import AdapterContext, AdapterStatus, SensitivityFloor
from adminme.adapters.emission import write_adapter_event
from adminme.events.envelope import EventEnvelope
from adminme.lib.adapters import (
    ChangeEnvelope,
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.observation import outbound as observation_outbound

_log = logging.getLogger(__name__)

_PACK_DIR = Path(__file__).resolve().parent


def _import_sibling(module_name: str) -> Any:
    """Load a pack-internal sibling via importlib (``packs/`` is not on
    the import path). Mirrors the Apple Reminders adapter's loader."""
    cached_name = f"_apple_calendar_pack__{module_name}"
    cached = sys.modules.get(cached_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        cached_name, _PACK_DIR / f"{module_name}.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load pack sibling {module_name!r}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[cached_name] = module
    spec.loader.exec_module(module)
    return module


_shim_mod: Any = _import_sibling("_eventkit_shim")
EKEventLike: Any = _shim_mod.EKEventLike
EKEventStoreLike: Any = _shim_mod.EKEventStoreLike
NoopEKEventStore: Any = _shim_mod.NoopEKEventStore

# [§6 invariant 10] privileged > normal; config may raise but not lower.
_FLOOR_RANK: dict[str, int] = {"normal": 0, "privileged": 1}

_DEFAULT_POLL_INTERVAL_S: int = 60
_DEFAULT_POLL_WINDOW_DAYS: int = 30

# OS-level access ref. EventKit grants Calendar access at the OS level
# (``requestFullAccessToEvents``); no 1Password secret involved. Same
# pattern as Apple Reminders.
_OS_TOKEN_STORAGE_REF: str = "op://local-system/calendar/permission"

_APPLE_CALENDAR_SCOPES: list[str] = ["calendar:read", "calendar:write"]


EKEventStoreFactory = Callable[[], Any]
"""PM-27: constructor-injected callable returning an ``EKEventStoreLike``.
Default builds a ``NoopEKEventStore`` that raises on every operation."""


def _noop_ek_event_store_factory() -> Any:
    """PM-27 default factory — surfaces missing-factory bugs loudly."""
    return NoopEKEventStore()


def _to_iso(value: Any) -> str:
    """Coerce an ``EKEvent`` date accessor value to ISO-8601. PyObjC's
    NSDate bridges to datetime; tests pass datetime directly. Empty
    input → ``""`` so the v1 schema's ``min_length=1`` surfaces it."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _attendees(value: Any) -> list[dict[str, Any]]:
    """Map ``EKEvent.attendees()`` onto the v1 schema's ``list[dict]``.
    Unstructured entries fall through as ``{"raw": str(p)}`` so the
    emit never drops attendee information silently."""
    if not value:
        return []
    out: list[dict[str, Any]] = []
    for participant in value:
        if isinstance(participant, dict):
            out.append(dict(participant))
            continue
        name = getattr(participant, "name", None)
        url = getattr(participant, "URL", None) or getattr(participant, "url", None)
        if callable(name):
            name = name()
        if callable(url):
            url = url()
        if name is None and url is None:
            out.append({"raw": str(participant)})
        else:
            entry: dict[str, Any] = {}
            if name is not None:
                entry["name"] = str(name)
            if url is not None:
                entry["url"] = str(url)
            out.append(entry)
    return out


class AppleCalendarAdapter(ExternalStateMirrorAdapter):
    """Apple Calendar Cat-B central pack. Polls ``EKEventStore`` every
    ``poll_interval_s`` over a forward-looking ``poll_window_days`` window
    and emits one ``calendar.event_added@v1`` per non-deduped ``EKEvent``
    per [D7] + canonical "MAP DON'T REGISTER". Outbound
    :meth:`_apply_write_impl` routes ``saveEvent_span_error_`` through
    :func:`observation_outbound` per [§6.20]. Central-only (F-7); β-β
    adds dual-deployment."""

    name: ClassVar[str] = "calendaring-apple-calendar"
    family: ClassVar[Literal["reminders", "calendaring", "contacts", "tasks"]] = (
        "calendaring"
    )
    version: ClassVar[str] = "1.0.0"
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    # [memo 05 §3.2, PM-35] one Cat-B entry declaring EXISTING
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

    PACK_ID: ClassVar[str] = "adapter:calendaring-apple-calendar"
    HARDCODED_FLOOR: ClassVar[SensitivityFloor] = "normal"
    SCOPES: ClassVar[list[str]] = list(_APPLE_CALENDAR_SCOPES)
    POLL_INTERVAL_S: ClassVar[int] = _DEFAULT_POLL_INTERVAL_S
    POLL_WINDOW_DAYS: ClassVar[int] = _DEFAULT_POLL_WINDOW_DAYS

    def __init__(
        self,
        *,
        config: dict[str, Any],
        ek_event_store_factory: EKEventStoreFactory = _noop_ek_event_store_factory,
        max_poll_cycles: int | None = None,
        observation_session: Any = None,
        observation_manager: Any = None,
        observation_event_log: Any = None,
    ) -> None:
        self._config = dict(config)
        self._ek_event_store_factory = ek_event_store_factory
        # [§6 invariant 10] resolve floor at construction; raise on downgrade.
        self.sensitivity_floor: SensitivityFloor = (  # type: ignore[misc]
            self._resolve_sensitivity_floor()
        )
        self._poll_interval_s: int = int(
            self._config.get("poll_interval_s", self.POLL_INTERVAL_S)
        )
        self._poll_window_days: int = int(
            self._config.get("poll_window_days", self.POLL_WINDOW_DAYS)
        )
        self._status = AdapterStatus()
        self._store: Any = None
        self._ctx: AdapterContext | None = None
        self._stop_requested: bool = False
        self._max_poll_cycles: int | None = max_poll_cycles
        # Dedupe by stable ``eventIdentifier``; tracks observation time
        # so a later event_updated (out of scope) can diff without re-fetch.
        self._seen_inbound: dict[str, datetime] = {}
        # [§6.20, PM-27] observation seam slots; Phase A leaves unset
        # (direct path); Phase B supervisor populates them.
        self._observation_session: Any = observation_session
        self._observation_manager: Any = observation_manager
        self._observation_event_log: Any = observation_event_log
        # [D18, D22, memo 04 §2.2] Dual-deployment instance state.
        # Defaults preserve the central path byte-for-byte (F-2); the
        # bridge branch flips these in :meth:`start`. ``_sharing_model_value``
        # surfaces the row discriminator (memo 05 §2.5 / §5.4) as an
        # instance attribute since ``calendar.event_added@v1`` is
        # ``extra="forbid"`` and β-β registers no new schemas.
        self._deployment_target: Literal["central", "bridge"] = "central"
        self._sharing_model_value: str = "shared_central"
        self._bridge_sink: Any = None
        # F-5: retain bridge emit Tasks so they can't be GC'd mid-flight
        # when scheduled from the sync ``_append`` call site.
        self._bridge_emit_tasks: set[asyncio.Task[None]] = set()

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
        """Authorize EventKit, emit ``adapter.authenticated``, run poll loop.

        β-β dual-deployment: ``ctx.config["deployment_target"]`` selects
        ``"central"`` (default — byte-for-byte β-α per F-2) or ``"bridge"``
        (per-member iCloud, emit via the wrapper-injected sink per memo
        04 §2.2 / [§6.19]).
        """
        # [D18, D22, docs/11-split-memo-11b-ii-beta.md §2] Default
        # ``"central"`` keeps existing instances backwards-compatible (F-2).
        deployment_target = ctx.config.get("deployment_target", "central")
        if deployment_target not in ("central", "bridge"):
            raise ValueError(
                "config.deployment_target must be 'central' or 'bridge'; "
                f"got {deployment_target!r}"
            )
        self._deployment_target = deployment_target
        # [A-2, memo 05 §2.5 / §5.4] row discriminator surfaced as an
        # instance attribute (schema is ``extra="forbid"``).
        self._sharing_model_value = (
            "per_member_isolated" if deployment_target == "bridge" else "shared_central"
        )

        if deployment_target == "bridge":
            # [A-3, memo 04 §2.2] Wrapper synthesizes ctx.config with the
            # per-member Apple ID identity + injected sink.
            bridge_member_id = ctx.config.get("_bridge_member_id") or ctx.config.get(
                "member_id"
            )
            if not isinstance(bridge_member_id, str) or not bridge_member_id:
                raise ValueError(
                    "bridge deployment requires ctx.config['_bridge_member_id'] "
                    "or ctx.config['member_id'] as a non-empty string"
                )
            self._config["member_id"] = bridge_member_id
            sink = ctx.config.get("_bridge_sink")
            if sink is None:
                raise ValueError(
                    "bridge deployment requires ctx.config['_bridge_sink'] "
                    "(an adminme_bridge.adapters.sinks.BridgeEventSink)"
                )
            self._bridge_sink = sink

        calendar_identifier = self._config.get("calendar_identifier")
        member_id = self._config.get("member_id")
        if not isinstance(calendar_identifier, str) or not calendar_identifier:
            raise ValueError(
                "config.calendar_identifier must be a non-empty string "
                "(the EKCalendar identifier resolved at bootstrap)"
            )
        if not isinstance(member_id, str) or not member_id:
            raise ValueError(
                "config.member_id must be a non-empty string (the household "
                "member whose EventKit grant this adapter uses)"
            )
        self._store = self._ek_event_store_factory()
        self._ctx = ctx
        self._status.started_at = ctx.now_factory()
        self._status.state = "running"
        # [D7, D8] adapter.authenticated v1; correlation_id from factory.
        await self._emit_authenticated(ctx=ctx)
        await self._poll_loop(ctx=ctx)

    async def stop(self) -> None:
        """Release the EKEventStore handle. Idempotent."""
        self._stop_requested = True
        self._store = None
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
                severity, error_kind = _classify_eventkit_error(exc)
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=None,
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
        per F-3 — re-poll on the same ``eventIdentifier`` is a no-op."""
        if self._store is None:
            raise RuntimeError(
                "Apple Calendar EKEventStore not initialized; call start() first"
            )
        calendar_identifier = str(self._config["calendar_identifier"])
        now = ctx.now_factory()
        window_end = now + timedelta(days=self._poll_window_days)
        events = self._store.fetch_events(
            calendar_identifier=calendar_identifier,
            start=now,
            end=window_end,
        )
        emitted = 0
        for event in events:
            event_id_raw = event.eventIdentifier()
            event_id = str(event_id_raw) if event_id_raw is not None else ""
            if not event_id:
                continue
            if event_id in self._seen_inbound:
                continue
            await self._emit_calendar_event_added(
                ctx=ctx,
                calendar_identifier=calendar_identifier,
                event=event,
            )
            self._seen_inbound[event_id] = now
            emitted += 1
        return emitted

    async def _emit_calendar_event_added(
        self,
        *,
        ctx: AdapterContext,
        calendar_identifier: str,
        event: Any,
    ) -> None:
        """Map an ``EKEvent`` onto ``CalendarEventAddedV1`` per [D7]."""
        title = event.title()
        location = event.location()
        notes = event.notes()
        await self._append(
            ctx=ctx,
            event_type="calendar.event_added",
            payload={
                "source": "apple_calendar",
                "external_event_id": str(event.eventIdentifier() or ""),
                "calendar_id": calendar_identifier,
                "summary": str(title) if title is not None else "",
                "start": _to_iso(event.startDate()),
                "end": _to_iso(event.endDate()),
                "location": str(location) if location is not None else None,
                "attendees": _attendees(event.attendees()),
                "body": str(notes) if notes is not None else None,
            },
        )

    async def _apply_write_impl(
        self,
        operation: WriteOperation,
    ) -> WriteResult:
        """[UT-27] gate already checked ``operation.kind``. Phase B routes
        the save through :func:`observation_outbound` per [§6.20]; Phase A
        direct-calls when seams are unset."""
        if operation.kind != "create_event":  # defense-in-depth
            raise WriteCapabilityError(
                f"AppleCalendarAdapter._apply_write_impl received "
                f"undeclared kind={operation.kind!r}"
            )
        store = self._store
        if store is None:
            return WriteResult(applied=False, external_id=None, suppressed=False)

        payload = operation.payload
        calendar_identifier = str(
            payload.get("calendar_id")
            or payload.get("calendar_identifier")
            or self._config.get("calendar_identifier")
            or ""
        )
        summary = str(payload.get("summary") or "")
        start_value = payload.get("start")
        end_value = payload.get("end")
        location = payload.get("location")
        notes = (
            payload.get("body") or payload.get("notes") or payload.get("description")
        )
        attendees_raw = payload.get("attendees")
        attendees = list(attendees_raw) if attendees_raw is not None else None

        event = store.build_event(
            calendar_identifier=calendar_identifier,
            summary=summary,
            start=start_value,
            end=end_value,
            location=location,
            notes=notes,
            attendees=attendees,
        )

        async def _do_save() -> None:
            # ``saveEvent_span_error_(event, EKSpanThisEvent, ...)``
            # per docs/reference/apple-eventkit/ekeventstore.md.
            store.save_event(event, commit=True)

        session = self._observation_session
        manager = self._observation_manager
        event_log = self._observation_event_log

        try:
            if session is not None and manager is not None and event_log is not None:
                # [§6.20] Phase B: observation seam wraps the save.
                outbound_result = await observation_outbound(
                    session,
                    "calendar.create_event",
                    {
                        "calendar_id": calendar_identifier,
                        "summary": summary,
                        "start": _to_iso(start_value)
                        if start_value is not None
                        else "",
                        "end": _to_iso(end_value) if end_value is not None else "",
                    },
                    _do_save,
                    manager=manager,
                    event_sink=event_log,
                    target_channel="calendaring-apple-calendar",
                    target_identifier=calendar_identifier,
                )
                if outbound_result.suppressed:
                    # F-3 (HARD): suppressed → unapplied; caller does NOT
                    # emit externally-visible side effects.
                    return WriteResult(
                        applied=False,
                        external_id=None,
                        suppressed=True,
                    )
            else:
                await _do_save()
        except BaseException as exc:  # noqa: BLE001 — classify & emit
            ctx = self._ctx
            severity, error_kind = _classify_eventkit_error(exc)
            if ctx is not None:
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=None,
                )
            raise

        external_id_raw = event.eventIdentifier()
        external_id = str(external_id_raw) if external_id_raw else None
        return WriteResult(
            applied=True,
            external_id=external_id,
            suppressed=False,
        )

    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        """Phase A no-op satisfying the Cat-B ABC. Inbound flow runs
        through :meth:`_poll_once`; future change-notification ingress
        will dispatch here."""
        return None

    async def _emit_authenticated(self, *, ctx: AdapterContext) -> None:
        """Append ``adapter.authenticated`` v1 with the OS-level access
        ref (EventKit grants at the OS level — no secret to store)."""
        await self._append(
            ctx=ctx,
            event_type="adapter.authenticated",
            payload={
                "adapter_name": self.name,
                "adapter_family": self.family,
                "token_storage_ref": _OS_TOKEN_STORAGE_REF,
                "authenticated_at": _to_iso(ctx.now_factory()),
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
            "occurred_at": _to_iso(ctx.now_factory()),
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
        """[§7.3, D17] append to the event log; never write projections.

        β-β: ``deployment_target=="bridge"`` dispatches through the
        wrapper-injected ``BridgeEventSink`` (memo 04 §2.2); central path
        preserved byte-for-byte (F-2). F-4: upstream observation gate is
        unchanged — suppressed writes skip ALL emits uniformly."""
        if self._deployment_target == "bridge":  # [D18, memo 04 §2.2]
            await self._append_via_bridge_sink(
                ctx=ctx,
                event_type=event_type,
                payload=payload,
                causation_id=causation_id,
            )
            return

        # ----- central path (byte-for-byte preserved from β-α) -----
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
            _log.exception("apple-calendar adapter failed to emit %s event", event_type)

    async def _append_via_bridge_sink(
        self,
        *,
        ctx: AdapterContext,
        event_type: str,
        payload: dict[str, Any],
        causation_id: str | None,
    ) -> None:
        """Bridge-deployment emit path. Build an ``EventEnvelope`` and
        schedule ``sink.emit(envelope)`` on the running loop. F-5: retain
        the Task in ``_bridge_emit_tasks`` (set + done-callback discard)
        so it can't be GC'd mid-flight. Memo 04 §2.2: provisional
        ``private:<member_id>`` scopes are re-stamped by the central
        :3337 router from Tailscale identity post-receipt."""
        sink = self._bridge_sink
        if sink is None:
            return
        member_id = str(self._config.get("member_id") or "")
        now = ctx.now_factory()
        provisional_owner_scope = (
            f"private:{member_id}" if member_id else "private:unknown"
        )
        envelope = EventEnvelope(
            event_at_ms=int(now.timestamp() * 1000),
            tenant_id=str(self._config.get("tenant_id") or "default"),
            type=event_type,
            schema_version=1,
            occurred_at=_to_iso(now),
            recorded_at="",
            source_adapter=self.name,
            source_account_id=member_id or self.name,
            owner_scope=provisional_owner_scope,
            visibility_scope=provisional_owner_scope,
            sensitivity=self.sensitivity_floor,
            correlation_id=ctx.correlation_id_factory(),
            causation_id=causation_id,
            payload=payload,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _log.exception(
                "apple-calendar adapter bridge emit skipped — no running "
                "event loop (type=%s)",
                event_type,
            )
            return
        # F-5: hold the Task reference to prevent GC mid-flight; done-
        # callback discards once the emit resolves so the set stays bounded.
        del loop
        await self._dispatch_bridge_emit(envelope)

    async def _dispatch_bridge_emit(self, envelope: EventEnvelope) -> None:
        """Best-effort sink dispatch — symmetric with the central path's
        exception swallow so a sink-side failure doesn't crash the loop."""
        sink = self._bridge_sink
        if sink is None:
            return
        try:
            await sink.emit(envelope)
        except Exception:  # noqa: BLE001 — best-effort emission
            _log.exception(
                "apple-calendar adapter bridge sink emit failed for %s",
                envelope.type,
            )


def _classify_eventkit_error(exc: BaseException) -> tuple[str, str]:
    """Map an EventKit-side exception into the ``AdapterErrorV1`` taxonomy.
    Auth-class → fatal (operator re-grant); other failures transient.
    Mirrors the Apple Reminders adapter."""
    exc_name = type(exc).__name__.lower()
    if "permission" in exc_name or "auth" in exc_name or "access" in exc_name:
        return ("fatal", "auth_failed")
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return ("transient", "network")
    return ("transient", "unknown")


__all__ = [
    "AppleCalendarAdapter",
    "EKEventLike",
    "EKEventStoreFactory",
    "EKEventStoreLike",
    "NoopEKEventStore",
]
