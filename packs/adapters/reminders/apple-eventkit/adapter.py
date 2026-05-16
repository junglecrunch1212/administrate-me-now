"""Apple Reminders bidirectional adapter — outbound write path (c2) +
inbound poll + dedupe + cursor (c3).

Implements the ``adminme.adapters.base.Adapter`` Protocol per the operator
decision in ``docs/06-split-memo-11.md`` §4. Phase 11c commit 2 shipped
the outbound half (event-log → iCloud writes on a 30s loop); commit 3
adds the inbound half (iCloud → event-log polling, never-sync filter
symmetric with outbound, dedupe-by-``remind_id``, milestone-only
``adapter.cursor_advanced`` emission). ``start()`` continues to spawn
only the outbound supervised task in c3; the concurrent inbound +
outbound supervision in ``start()`` lands in commit 4.

Bidirectional sync model [BUILD.md APPLE REMINDERS BIDIRECTIONAL —
DETAILED SPEC]. The adapter consumes three upstream event types and
translates each into an ``EKReminder`` write to the configured
household list:

  - ``reminder.dispatched`` → title = ``composed_text``, priority = high (1)
  - ``task.created``        → title = ``title``, priority = medium (5)
  - ``commitment.proposed`` → title = ``text_summary``, priority by ``urgency``

Each successful save emits one ``reminder.list_item.synced`` v1 event
with ``direction="outbound"`` and ``causation_id`` set to the upstream
event id — round-trip traceability per BUILD.md APPLE REMINDERS
BIDIRECTIONAL §"Outbound write cadence".

The inbound poll (c3) fetches reminders via ``fetch_reminders`` every
30s, applies the never-sync filter at the iCloud boundary, dedupes by
``remind_id`` against a constructor-injected resolver (the ``reminders``
projection slice in production; a permissive ``None``-returner in tests
and Phase A when the projection's writer pipeline is still deferred —
see prompt 11c §3 deferred-watch). Genuinely-new or modified reminders
emit ``reminder.list_item.synced`` with ``direction="inbound"`` and
``causation_id=None`` (iCloud is the source — no AdministrateMe event
triggered the emission, so there's no upstream id to chain). Cursor
state is per-list ``(list_identifier → last_modified_at)``;
``adapter.cursor_advanced`` emission is gated on the 100-events-or-
5-minutes milestone shared across the L1 adapter family.

Never-sync filter [BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Never-sync
rules", VISION §"Privacy by default"]. The predicate from
``never_sync.py`` (c1) is applied at BOTH boundaries: BEFORE every
outbound write (c2) and BEFORE every inbound emit (c3). The same three
rules short-circuit a block on either side: list-name substring (e.g.,
"Work"), item-tag substring (e.g., "#private"), owning-member role
(``"child"`` / ``"ambient"``).

Sensitivity floor [§6 invariant 10]. Hardcoded ``"normal"`` at the
class level. Adapters NEVER auto-elevate; any config attempt to set
``"privileged"`` raises at construction. The per-item privacy boundary
is the never-sync predicate, not the adapter-level floor — finer-
grained but lower base-rate than Plaid's ``"privileged"``-floor
financial adapter.

Per ``[§8.2, D6]``: no LLM SDK import in this module. Channel
transport is OpenClaw's job [§8 invariant 3]; this adapter only emits
typed events. ``[§7.3, D17]``: adapters never write projections. The
``reminders`` projection slice is READ-ONLY from c3's inbound dedupe
predicate; the writer pipeline ships in a future prompt.
"""

# [§8.2, D6] no LLM SDK imports under packs/adapters/ — channel
# transport is OpenClaw's; this module emits typed events only.
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)

_log = logging.getLogger(__name__)

_PACK_DIR = Path(__file__).resolve().parent


def _import_sibling(module_name: str) -> Any:
    """Load a sibling pack-internal Python file via importlib.

    Pack files do not live on Python's import path (``packs/`` isn't a
    package); the supervisor loads ``adapter.py`` via
    ``importlib.util.spec_from_file_location`` and pack-internal sibling
    modules need the same treatment. Caches by name so repeated
    invocations across test collection don't reload."""
    cached_name = f"_apple_eventkit_pack__{module_name}"
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


_never_sync_mod: Any = _import_sibling("never_sync")
_shim_mod: Any = _import_sibling("_eventkit_shim")

passes_never_sync: Callable[..., bool] = _never_sync_mod.passes_never_sync
DEFAULT_NEVER_SYNC_LIST_SUBSTRINGS: tuple[str, ...] = tuple(
    _never_sync_mod.DEFAULT_NEVER_SYNC_LIST_SUBSTRINGS
)
DEFAULT_NEVER_SYNC_TAG_SUBSTRINGS: tuple[str, ...] = tuple(
    _never_sync_mod.DEFAULT_NEVER_SYNC_TAG_SUBSTRINGS
)
EKEventStoreLike: Any = _shim_mod.EKEventStoreLike
EKReminderLike: Any = _shim_mod.EKReminderLike
NoopEKEventStore: Any = _shim_mod.NoopEKEventStore


# OS-level token-storage reference. EventKit access is granted at the OS
# level via ``EKEventStore.requestFullAccessToReminders(completion:)``
# per docs/reference/apple-eventkit/access.md; no 1Password secret is
# involved (unlike Gmail's service-account JSON or Plaid's item access
# token). The string is recorded on ``adapter.authenticated`` for audit.
_OS_TOKEN_STORAGE_REF = "os://reminders-permission"

_DEFAULT_INBOUND_INTERVAL_S = 30
_DEFAULT_OUTBOUND_INTERVAL_S = 30

_OUTBOUND_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "reminder.dispatched",
        "task.created",
        "commitment.proposed",
    }
)

# EventKit priority ordinal scale per
# ``docs/reference/apple-eventkit/ekreminder.md``: 0=none, 1=high,
# 5=medium, 9=low. The Reminders adapter maps urgency strings on
# ``commitment.proposed`` to these ordinals.
_PRIORITY_BY_URGENCY: dict[str, Literal[0, 1, 5, 9]] = {
    "today": 1,
    "this_week": 5,
    "this_month": 9,
    "no_rush": 0,
}

MemberRoleResolver = Callable[[str], str]
"""Maps ``member_id`` → role string (``"adult"`` / ``"child"`` /
``"ambient"``). Production wires the ``members`` projection slice from
prompt 05; tests inject a fake. The never-sync predicate's
ownership-exclusion rule consults this for every outbound write
candidate per BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Never-sync
rules"."""

EKEventStoreFactory = Callable[[], Any]
"""PM-27: constructor-injected callable that returns an
``EKEventStoreLike``. Default is ``NoopEKEventStore`` so the adapter
constructs cleanly without pyobjc; the supervisor wires the real
factory at startup (prompt 16 bootstrap wizard)."""

DedupeResolver = Callable[[str], datetime | None]
"""Maps ``remind_id`` → the projected ``last_modified_at`` for that
reminder, or ``None`` if the projection has no record of it. Production
reads the ``reminders`` projection slice; the permissive default
returns ``None`` for every input so inbound emission is permissive
when the projection's writer pipeline is absent (Phase A — see prompt
11c §3 deferred-watch). [§7.3, D17] adapters READ from projections;
they never write."""


def _noop_ek_event_store_factory() -> Any:
    """PM-27 default factory. Returns a ``NoopEKEventStore`` that raises
    on every operation — surfaces missing-factory bugs loudly instead of
    silently no-op-ing writes."""
    return NoopEKEventStore()


def _default_member_role_resolver(_member_id: str) -> str:
    """Permissive default — assumes ``"adult"`` so every candidate is
    sync-eligible by role. Production reads the ``members`` projection
    slice; this default is for tests + the Phase A operator-self-host
    case where the projection isn't wired yet."""
    return "adult"


def _default_dedupe_resolver(_remind_id: str) -> datetime | None:
    """Permissive default — returns ``None`` for every ``remind_id`` so
    the in-memory session-level ``_seen_inbound`` cache is the only
    dedupe gate. Phase A: the ``reminders`` projection slice's writer
    pipeline is deferred (prompt 11c §3); production tightens this once
    the projection's writer lands and the resolver consults it."""
    return None


class AppleEventKitAdapter:
    """Apple Reminders L1 standalone bidirectional adapter.

    Class attributes satisfy the ``Adapter`` Protocol's required fields.
    ``sensitivity_floor`` is hardcoded ``"normal"`` per [§6 invariant
    10]; any config attempt to set ``"privileged"`` raises at
    construction.

    The supervisor instantiates one of these per Reminders entry in
    ``config/channels.yaml`` and supervises ``start()``. Each adapter
    handles a single household member's EventKit grant; multi-member
    households run multiple supervised instances.
    """

    # [§6 invariant 10, D11 (Reminders posture)]: sensitivity floor
    # hardcoded at the class level. Reminders adapters cannot be
    # ELEVATED to ``privileged`` via config — adapters never
    # auto-elevate; that's a deliberate operator decision for a future
    # prompt. JSON-schema rejects most malformed inputs at load time;
    # ``__init__`` is the defense-in-depth runtime check.
    name: str = "reminders-apple-eventkit"
    family: str = "reminders"
    version: str = "1.0.0"
    sensitivity_floor: SensitivityFloor = "normal"

    PACK_ID: str = "adapter:reminders-apple-eventkit"

    OUTBOUND_INTERVAL_S: int = _DEFAULT_OUTBOUND_INTERVAL_S
    """In-process outbound write cadence. ``pack.yaml``'s ``*/1 * * * *``
    cron is a heartbeat; the 30s sub-cadence is the active outbound
    rate per BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Outbound write
    cadence"."""

    INBOUND_INTERVAL_S: int = _DEFAULT_INBOUND_INTERVAL_S
    """In-process inbound poll cadence (c3). Same rationale as the
    outbound: pack.yaml's cron is a heartbeat, the 30s sub-cadence is
    the active polling rate per §"Polling cadence"."""

    CURSOR_MILESTONE_EVENTS: int = 100
    CURSOR_MILESTONE_INTERVAL_S: int = 300
    """``adapter.cursor_advanced`` is emitted at whichever milestone
    fires first — 100 emissions since the last advance OR 5 minutes of
    wall clock. Shared across the L1 adapter family; matches Gmail's and
    Plaid's settings so dashboards aggregate cleanly. Anti-vision §"Quiet
    by default": pure per-poll emission would swamp the event log."""

    INBOUND_CURSOR_KIND: str = "list_identifier_last_modified"
    """Per-list ``(list_identifier → last_modified_at)`` cursor shape.
    JSON-serialized into the v1 schema's ``cursor_value: str`` field so
    the round-trip carries the entire reminder-family cursor map in one
    audit event."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        ek_event_store_factory: EKEventStoreFactory = _noop_ek_event_store_factory,
        member_role_resolver: MemberRoleResolver = _default_member_role_resolver,
        dedupe_resolver: DedupeResolver = _default_dedupe_resolver,
        max_outbound_cycles: int | None = None,
        max_inbound_cycles: int | None = None,
    ) -> None:
        self._config = dict(config)
        # [§6 invariant 10] re-assert the class-level floor against the
        # resolved config. Reminders does NOT allow raising to
        # ``privileged`` via config (unlike Gmail) because adapter-level
        # elevation would be a deliberate operator decision for a
        # future prompt — finer-grained item-level privacy lives in the
        # never-sync predicate, not the floor.
        configured_floor = self._config.get("sensitivity_floor", "normal")
        if configured_floor != "normal":
            raise ValueError(
                "Reminders adapter sensitivity_floor must be 'normal'; "
                f"got {configured_floor!r}. [§6 invariant 10] adapters "
                "never auto-elevate."
            )

        self._ek_event_store_factory = ek_event_store_factory
        self._member_role_resolver = member_role_resolver
        self._dedupe_resolver = dedupe_resolver
        self._never_sync_list_substrings: tuple[str, ...] = tuple(
            self._config.get(
                "never_sync_list_substrings", DEFAULT_NEVER_SYNC_LIST_SUBSTRINGS
            )
        )
        self._never_sync_tag_substrings: tuple[str, ...] = tuple(
            self._config.get(
                "never_sync_tag_substrings", DEFAULT_NEVER_SYNC_TAG_SUBSTRINGS
            )
        )
        # Test seams: bound each loop independently. ``None`` = run
        # forever (production); the supervisor stops both tasks on
        # lifecycle events.
        self._max_outbound_cycles = max_outbound_cycles
        self._max_inbound_cycles = max_inbound_cycles

        self._status = AdapterStatus()
        self._ek_store: Any = None
        self._ctx: AdapterContext | None = None
        self._outbound_queue: deque[dict[str, Any]] = deque()
        self._stop_requested: bool = False
        self._outbound_task: asyncio.Task[None] | None = None
        self._inbound_task: asyncio.Task[None] | None = None

        # Inbound cursor state — per-list ``last_modified_at`` watermark.
        # ``adapter.cursor_advanced`` packs the dict into a single
        # JSON-encoded ``cursor_value`` string per milestone fire.
        self._inbound_cursors: dict[str, datetime] = {}

        # Session-level dedupe cache. Bridges the gap between the
        # projection-resolver's view (which may be stale or absent under
        # Phase A — see [§3 deferred-watch]) and live state. Updated on
        # every successful emit; consulted on every inbound candidate
        # before the projection-resolver fallback.
        self._seen_inbound: dict[str, datetime] = {}

        # Cursor-advance milestone state. Mirrors Gmail's and Plaid's
        # pattern: count emissions since last advance + track wall-clock
        # since last advance; emit on whichever threshold fires first.
        self._events_since_inbound_advance: int = 0
        self._last_inbound_advance_at: datetime | None = None

    # ------------------------------------------------------------------
    # Adapter Protocol
    # ------------------------------------------------------------------
    async def start(self, *, ctx: AdapterContext) -> None:
        """Validate config, instantiate the EKEventStore, emit
        ``adapter.authenticated``, spawn BOTH supervised poll tasks
        (outbound + inbound) concurrently, and block until either
        completes or ``stop()`` is called.

        c4 closes the bidirectional surface: the inbound poll task
        joins the outbound task under ``asyncio.gather`` so a single
        ``stop()`` cancels both cleanly per BUILD.md APPLE REMINDERS
        BIDIRECTIONAL §"Polling cadence" + §"Outbound write cadence".
        Tests exercise the underlying helpers directly to avoid
        blocking on the loop; this method is the supervisor-facing
        lifecycle hook only."""
        target_list_identifier = self._config.get("target_list_identifier")
        member_id = self._config.get("member_id")
        if not isinstance(target_list_identifier, str) or not target_list_identifier:
            raise ValueError(
                "config.target_list_identifier must be a non-empty string "
                "(the iCloud Reminders list AdministrateMe writes to)"
            )
        if not isinstance(member_id, str) or not member_id:
            raise ValueError(
                "config.member_id must be a non-empty string (the household "
                "member whose EventKit grant this adapter uses)"
            )

        self._ek_store = self._ek_event_store_factory()
        self._ctx = ctx
        self._status.started_at = ctx.now_factory()
        self._status.state = "running"

        # [D7] adapter.authenticated registered at v1 in
        # adminme/events/schemas/adapters.py. [D8] correlation_id is
        # minted by the supervisor-provided factory.
        self._emit_authenticated(ctx=ctx)

        self._outbound_task = asyncio.create_task(self._outbound_poll(ctx=ctx))
        self._inbound_task = asyncio.create_task(self._inbound_poll(ctx=ctx))
        try:
            await asyncio.gather(
                self._outbound_task, self._inbound_task
            )
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        """Cancel both supervised tasks (outbound from c2; inbound from
        c3 once ``start()`` spawns it in c4), release the EKEventStore
        handle, and transition to the ``"stopped"`` state. Idempotent.
        """
        self._stop_requested = True
        for task in (self._outbound_task, self._inbound_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._ek_store = None
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    # ------------------------------------------------------------------
    # outbound: event-log → iCloud (c2)
    # ------------------------------------------------------------------
    def enqueue_outbound(self, event: dict[str, Any]) -> None:
        """Append an upstream event to the outbound queue.

        The supervisor's event-bus subscription routes
        ``reminder.dispatched`` / ``task.created`` /
        ``commitment.proposed`` events to this method; any other
        ``type`` is filtered + dropped (this adapter does not write
        ``crm.surfaced`` / ``digest.composed`` / etc. — those route
        elsewhere per §6 out-of-scope). Drained on each
        ``OUTBOUND_INTERVAL_S`` tick of ``_outbound_poll``."""
        event_type = event.get("type")
        if event_type not in _OUTBOUND_EVENT_TYPES:
            _log.debug(
                "reminders adapter: ignoring upstream type=%r "
                "(not in outbound trigger set)",
                event_type,
            )
            return
        self._outbound_queue.append(event)

    async def _outbound_poll(self, *, ctx: AdapterContext) -> None:
        """Outbound poll loop. Each tick drains the queue once, then
        sleeps ``OUTBOUND_INTERVAL_S``. Tests bound the loop via
        ``max_outbound_cycles``.

        Defensive catch around the drain mirrors the gmail/plaid
        pattern: any EventKit-side exception classifies into
        ``AdapterErrorV1`` severity + ``error_kind`` and the loop
        continues for ``transient`` cases or halts for ``fatal`` so the
        supervisor's restart-with-backoff takes over."""
        cycles = 0
        while not self._stop_requested:
            if (
                self._max_outbound_cycles is not None
                and cycles >= self._max_outbound_cycles
            ):
                return
            try:
                self._drain_outbound_once(ctx=ctx)
            except BaseException as exc:  # noqa: BLE001 — classify & emit
                severity, error_kind = _classify_eventkit_error(exc)
                self._emit_error(
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
                await asyncio.sleep(self.OUTBOUND_INTERVAL_S)
            except asyncio.CancelledError:
                return

    def _drain_outbound_once(self, *, ctx: AdapterContext) -> int:
        """Drain every queued upstream event in a single batch. Returns
        the count of writes that produced a ``reminder.list_item.synced``
        emission (i.e., the count post never-sync filter).

        One ``sync_cycle_id`` per drain — every event in the batch
        carries the same cycle id so downstream consumers can correlate
        the batch."""
        if self._ek_store is None:
            return 0
        sync_cycle_id = _next_sync_cycle_id()
        written = 0
        while self._outbound_queue:
            upstream = self._outbound_queue.popleft()
            if self._write_one_outbound(
                ctx=ctx,
                upstream=upstream,
                sync_cycle_id=sync_cycle_id,
            ):
                written += 1
        return written

    def _write_one_outbound(
        self,
        *,
        ctx: AdapterContext,
        upstream: dict[str, Any],
        sync_cycle_id: str,
    ) -> bool:
        """Translate one upstream event → EKReminder, apply the
        never-sync filter, call ``EKEventStore.save(_:commit:)``, emit
        ``reminder.list_item.synced`` with ``direction="outbound"``.
        Returns True iff a synced event was emitted (False on
        never-sync block / unmappable upstream).

        [Causation invariant] ``causation_id`` is the upstream event's
        id — every outbound emission traces back to the proactive
        pipeline event that triggered the write."""
        ek_store = self._ek_store
        if ek_store is None:
            return False
        target_list_id = str(self._config["target_list_identifier"])

        translation = self._translate_upstream(upstream)
        if translation is None:
            return False

        member_id = translation["member_id"]
        member_role = self._member_role_resolver(member_id)
        target_list_name = ek_store.list_name_for_identifier(target_list_id)

        # Never-sync filter at the OUTBOUND boundary — symmetric with the
        # inbound application in c3. A reminder routed to a "Work" list,
        # or carrying "#private", or owned by a child/ambient member,
        # is dropped here BEFORE the EventKit write. [VISION §"Privacy
        # by default", BUILD.md APPLE REMINDERS BIDIRECTIONAL].
        if not passes_never_sync(
            list_name=target_list_name,
            title=translation["title"],
            notes=translation.get("notes"),
            member_role=member_role,
            never_sync_list_substrings=self._never_sync_list_substrings,
            never_sync_tag_substrings=self._never_sync_tag_substrings,
        ):
            return False

        reminder = ek_store.build_reminder(
            list_identifier=target_list_id,
            title=translation["title"],
            notes=translation.get("notes"),
            due_at=translation.get("due_at"),
            priority=translation["priority"],
        )
        # ``save(_:commit:)`` per docs/reference/apple-eventkit/create.md
        # — ``commit=True`` flushes immediately rather than batching for
        # a later ``commit()`` call. Each outbound write is its own
        # transaction so iCloud sync churn stays per-reminder.
        ek_store.save(reminder, commit=True)

        # Apple stamps ``calendarItemIdentifier`` on save; read it back
        # for the round-trip-traceability field on the emitted event.
        remind_id = str(getattr(reminder, "calendarItemIdentifier", "") or "")
        now = ctx.now_factory()
        payload: dict[str, Any] = {
            "member_id": member_id,
            "remind_id": remind_id,
            "list_identifier": target_list_id,
            "list_name": target_list_name,
            "title": translation["title"],
            "notes": translation.get("notes"),
            "due_at": _maybe_iso(translation.get("due_at")),
            "priority": translation["priority"],
            "completed": False,
            "last_modified_at": _to_iso(now),
            "direction": "outbound",
            "sync_cycle_id": sync_cycle_id,
        }
        causation_id = str(upstream.get("id") or "") or None
        self._append(
            ctx=ctx,
            event_type="reminder.list_item.synced",
            payload=payload,
            causation_id=causation_id,
        )
        return True

    @staticmethod
    def _translate_upstream(
        upstream: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Map an upstream event to the field bundle the outbound
        EKReminder write consumes. Returns None for unmappable payloads
        (missing title / owner) so the caller drops the event quietly
        rather than crashing the drain loop on one malformed message."""
        event_type = upstream.get("type")
        payload = upstream.get("payload")
        if not isinstance(payload, dict):
            return None

        if event_type == "reminder.dispatched":
            text = str(payload.get("composed_text") or "").strip()
            owner = str(payload.get("owner_member_id") or "").strip()
            if not text or not owner:
                return None
            return {
                "member_id": owner,
                "title": text[:500],
                "notes": None,
                "due_at": payload.get("due_at"),
                "priority": 1,  # high — dispatched reminders are time-pressing
            }
        if event_type == "task.created":
            title = str(payload.get("title") or "").strip()
            owner = str(payload.get("owner_member_id") or "").strip()
            if not title or not owner:
                return None
            description = payload.get("description")
            notes = (
                description[:2000]
                if isinstance(description, str) and description
                else None
            )
            return {
                "member_id": owner,
                "title": title[:500],
                "notes": notes,
                "due_at": payload.get("due"),
                "priority": 5,  # medium — tasks default to medium urgency
            }
        if event_type == "commitment.proposed":
            summary = str(payload.get("text_summary") or "").strip()
            owner = str(payload.get("owed_by_member_id") or "").strip()
            if not summary or not owner:
                return None
            urgency = str(payload.get("urgency") or "this_week")
            preview = payload.get("source_message_preview")
            notes = (
                preview[:2000]
                if isinstance(preview, str) and preview
                else None
            )
            return {
                "member_id": owner,
                "title": summary[:500],
                "notes": notes,
                "due_at": payload.get("suggested_due"),
                "priority": _PRIORITY_BY_URGENCY.get(urgency, 5),
            }
        return None

    # ------------------------------------------------------------------
    # inbound: iCloud → event-log (c3)
    # ------------------------------------------------------------------
    async def _inbound_poll(self, *, ctx: AdapterContext) -> None:
        """Inbound poll loop. Each tick fetches reminders via the
        EKEventStore, applies the never-sync filter symmetrically with
        the outbound boundary, dedupes by ``remind_id``, and emits one
        ``reminder.list_item.synced`` v1 event with
        ``direction="inbound"`` per genuinely-new-or-modified item.

        Defensive catch mirrors the outbound loop: any EventKit-side
        exception classifies into ``AdapterErrorV1`` severity +
        ``error_kind``; transient stays in the loop, fatal halts so the
        supervisor's restart-with-backoff takes over. Tests bound the
        loop via ``max_inbound_cycles``."""
        cycles = 0
        while not self._stop_requested:
            if (
                self._max_inbound_cycles is not None
                and cycles >= self._max_inbound_cycles
            ):
                return
            try:
                self._inbound_poll_once(ctx=ctx)
            except BaseException as exc:  # noqa: BLE001 — classify & emit
                severity, error_kind = _classify_eventkit_error(exc)
                self._emit_error(
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
                await asyncio.sleep(self.INBOUND_INTERVAL_S)
            except asyncio.CancelledError:
                return

    def _inbound_poll_once(self, *, ctx: AdapterContext) -> int:
        """Run one inbound cycle. Returns the count of
        ``reminder.list_item.synced`` events emitted (i.e., post never-
        sync filter + dedupe). One ``sync_cycle_id`` covers every
        emission in the batch so downstream consumers can correlate.

        Anti-vision §"Quiet by default": the dedupe gate is what keeps
        emission load-bearing instead of noisy. Pure fetch-then-emit
        would produce one event per reminder per poll cycle even when
        nothing changed — the build-fail test in §9 asserts this."""
        if self._ek_store is None:
            return 0
        sync_cycle_id = _next_sync_cycle_id()
        reminders = self._fetch_reminders_filtered()
        emitted = 0
        for rem in reminders:
            if not self._is_new_or_modified(rem):
                continue
            self._emit_inbound_synced(
                ctx=ctx, reminder=rem, sync_cycle_id=sync_cycle_id
            )
            emitted += 1
        self._maybe_emit_inbound_cursor_advance(ctx=ctx)
        return emitted

    def _fetch_reminders_filtered(self) -> list[Any]:
        """Fetch every reminder across non-never-sync lists and apply
        the never-sync predicate per item. Symmetric with the outbound
        boundary's filter — the same three rules (list-name substring,
        item-tag substring, owning-member role) gate both directions.

        The adapter's configured ``member_id`` resolves the
        owning-member role for the inbound batch; iCloud does not carry
        AdministrateMe's household-member assignment, so the adapter
        instance's owner is the source of truth for ownership-exclusion
        per BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Never-sync rules"."""
        ek_store = self._ek_store
        if ek_store is None:
            return []
        all_reminders = ek_store.fetch_reminders()
        member_id = str(self._config.get("member_id", ""))
        member_role = self._member_role_resolver(member_id)
        passing: list[Any] = []
        for rem in all_reminders:
            list_id = str(getattr(rem, "list_identifier", "") or "")
            list_name = ek_store.list_name_for_identifier(list_id)
            title = str(getattr(rem, "title", "") or "")
            notes = getattr(rem, "notes", None)
            if passes_never_sync(
                list_name=list_name,
                title=title,
                notes=notes if isinstance(notes, str) else None,
                member_role=member_role,
                never_sync_list_substrings=self._never_sync_list_substrings,
                never_sync_tag_substrings=self._never_sync_tag_substrings,
            ):
                passing.append(rem)
        return passing

    def _is_new_or_modified(self, reminder: Any) -> bool:
        """Dedupe predicate. Returns ``True`` iff the reminder is
        genuinely new (no prior record) OR genuinely modified
        (``last_modified_at`` is strictly newer than the most recent
        timestamp we have on file). The session cache ``_seen_inbound``
        wins over the projection resolver because it's live; the
        resolver is the cold-start floor for the cross-session case.

        Phase A: the resolver default is permissive (returns ``None``
        for every input — see ``_default_dedupe_resolver``) so absence-
        of-record is treated as "new". Once the ``reminders``
        projection's writer pipeline ships, production tightens this
        path automatically — no adapter change required."""
        remind_id = str(getattr(reminder, "calendarItemIdentifier", "") or "")
        if not remind_id:
            return False
        current_lmod = getattr(reminder, "last_modified_at", None)
        prior_lmod = self._seen_inbound.get(remind_id)
        if prior_lmod is None:
            prior_lmod = self._dedupe_resolver(remind_id)
        if prior_lmod is None:
            return True
        if not isinstance(current_lmod, datetime):
            # The reminder has no usable timestamp; we've seen its id
            # before — treat as unchanged to avoid emit-on-every-poll.
            return False
        return current_lmod > prior_lmod

    def _emit_inbound_synced(
        self,
        *,
        ctx: AdapterContext,
        reminder: Any,
        sync_cycle_id: str,
    ) -> None:
        """Translate one iCloud-side reminder → a
        ``reminder.list_item.synced`` v1 event with
        ``direction="inbound"``, ``causation_id=None`` (iCloud is the
        source; no AdministrateMe event triggered this emission), and
        update the per-list cursor + session dedupe cache.

        ``priority`` is normalized to the EventKit ordinal scale
        ``{0, 1, 5, 9}``; any out-of-scale value lands as ``0`` (none)
        so the schema's ``Literal[0, 1, 5, 9]`` doesn't reject the event
        — the Pydantic model's enforcement is at registration time, not
        at adapter-emit time, but defending in depth is cheap."""
        member_id = str(self._config.get("member_id", ""))
        remind_id = str(getattr(reminder, "calendarItemIdentifier", "") or "")
        list_id = str(getattr(reminder, "list_identifier", "") or "")
        ek_store = self._ek_store
        list_name = (
            ek_store.list_name_for_identifier(list_id)
            if ek_store is not None
            else list_id
        )
        title = str(getattr(reminder, "title", "") or "")
        notes_raw = getattr(reminder, "notes", None)
        notes: str | None = (
            notes_raw[:2000] if isinstance(notes_raw, str) and notes_raw else None
        )
        due_at = getattr(reminder, "due_at", None)
        raw_priority = getattr(reminder, "priority", 0)
        priority: Literal[0, 1, 5, 9]
        if raw_priority == 1:
            priority = 1
        elif raw_priority == 5:
            priority = 5
        elif raw_priority == 9:
            priority = 9
        else:
            priority = 0
        completed = bool(getattr(reminder, "completed", False))
        lmod = getattr(reminder, "last_modified_at", None)
        emit_lmod: datetime = (
            lmod if isinstance(lmod, datetime) else ctx.now_factory()
        )

        payload: dict[str, Any] = {
            "member_id": member_id,
            "remind_id": remind_id,
            "list_identifier": list_id,
            "list_name": list_name,
            "title": title[:500],
            "notes": notes,
            "due_at": _maybe_iso(due_at),
            "priority": priority,
            "completed": completed,
            "last_modified_at": _to_iso(emit_lmod),
            "direction": "inbound",
            "sync_cycle_id": sync_cycle_id,
        }
        # [Causation invariant] inbound has no upstream AdministrateMe
        # event — iCloud is the source — so causation_id is left unset.
        self._append(
            ctx=ctx, event_type="reminder.list_item.synced", payload=payload
        )

        self._seen_inbound[remind_id] = emit_lmod
        prior_cursor = self._inbound_cursors.get(list_id)
        if prior_cursor is None or emit_lmod > prior_cursor:
            self._inbound_cursors[list_id] = emit_lmod
        self._events_since_inbound_advance += 1

    def _maybe_emit_inbound_cursor_advance(
        self, *, ctx: AdapterContext
    ) -> None:
        """Emit ``adapter.cursor_advanced`` v1 only on milestone.
        Gated on whichever fires first: 100 emissions since the last
        advance OR 5 minutes of wall clock since the last advance with
        at least one emission. The cursor_value packs the entire
        per-list watermark dict as a JSON string so a single audit event
        captures the full reminder-family cursor map.

        Anti-vision §"Quiet by default": gating is what keeps the
        cursor-advance signal load-bearing. Skipping this gate would
        produce one event per poll cycle."""
        now = ctx.now_factory()
        events = self._events_since_inbound_advance
        elapsed_s: float | None = None
        if self._last_inbound_advance_at is not None:
            elapsed_s = (now - self._last_inbound_advance_at).total_seconds()
        events_threshold = events >= self.CURSOR_MILESTONE_EVENTS
        time_threshold = (
            elapsed_s is not None
            and elapsed_s >= self.CURSOR_MILESTONE_INTERVAL_S
            and events > 0
        )
        if not (events_threshold or time_threshold):
            return
        cursor_value = json.dumps(
            {
                lid: _to_iso(ts)
                for lid, ts in sorted(self._inbound_cursors.items())
            },
            sort_keys=True,
        ) or "{}"
        payload = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "cursor_kind": self.INBOUND_CURSOR_KIND,
            "cursor_value": cursor_value,
            "events_since_last": events,
            "advanced_at": _to_iso(now),
        }
        self._append(
            ctx=ctx, event_type="adapter.cursor_advanced", payload=payload
        )
        self._events_since_inbound_advance = 0
        self._last_inbound_advance_at = now
        self._status.last_cursor_advance_at = now

    # ------------------------------------------------------------------
    # emission helpers (mirror gmail / plaid patterns)
    # ------------------------------------------------------------------
    def _emit_authenticated(self, *, ctx: AdapterContext) -> None:
        """Append ``adapter.authenticated`` v1. Carries the OS-level
        permission reference instead of a 1Password op:// ref — EventKit
        access is granted at the OS level, no secret to store."""
        payload = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "token_storage_ref": _OS_TOKEN_STORAGE_REF,
            "authenticated_at": _to_iso(ctx.now_factory()),
            "scopes": ["reminders:read", "reminders:write"],
        }
        self._append(
            ctx=ctx, event_type="adapter.authenticated", payload=payload
        )

    def _emit_error(
        self,
        *,
        ctx: AdapterContext,
        severity: str,
        error_kind: str,
        error_message: str,
        retry_after_s: int | None,
    ) -> None:
        """Append ``adapter.error`` v1. Severity drives the supervisor's
        backoff: ``transient`` stays in the loop; ``fatal`` halts the
        loop and the supervisor restarts with backoff."""
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
        self._append(ctx=ctx, event_type="adapter.error", payload=payload)

    def _append(
        self,
        *,
        ctx: AdapterContext,
        event_type: str,
        payload: dict[str, Any],
        causation_id: str | None = None,
    ) -> None:
        """Best-effort append. Mirrors the supervisor's duck-typed
        contract: any object with ``append_sync(...)`` or ``append(...)``
        works, and a failure on emit must not crash the adapter — the
        log itself may be down."""
        appender = getattr(ctx.event_log, "append_sync", None) or getattr(
            ctx.event_log, "append", None
        )
        if appender is None:
            return
        kwargs: dict[str, Any] = {
            "type": event_type,
            "schema_version": 1,
            "payload": payload,
            "correlation_id": ctx.correlation_id_factory(),
        }
        if causation_id is not None:
            kwargs["causation_id"] = causation_id
        try:
            appender(**kwargs)
        except Exception:  # noqa: BLE001 — best-effort emission
            _log.exception(
                "reminders adapter failed to emit %s event", event_type
            )


# ----------------------------------------------------------------------
# module-level helpers
# ----------------------------------------------------------------------
def _next_sync_cycle_id() -> str:
    """Mint a fresh sync_cycle_id per drain batch. Prefix is stable so
    log greps + dashboards can pivot on it."""
    return f"sync-{uuid.uuid4().hex}"


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _maybe_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_iso(value)
    if isinstance(value, str):
        return value
    return str(value)


def _classify_eventkit_error(exc: BaseException) -> tuple[str, str]:
    """Map an EventKit-side exception into the ``AdapterErrorV1``
    taxonomy. EventKit is local — most failures are transient
    (intermittent permission revocation race, iCloud-sync hiccup);
    auth-class errors map to fatal so the supervisor surfaces them
    for operator action (prompt 16 wizard re-grant)."""
    exc_name = type(exc).__name__.lower()
    if "permission" in exc_name or "auth" in exc_name:
        return ("fatal", "auth_failed")
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return ("transient", "network")
    if isinstance(exc, OSError):
        return ("transient", "network")
    return ("transient", "unknown")


__all__ = [
    "AppleEventKitAdapter",
    "DedupeResolver",
    "EKEventStoreFactory",
    "EKEventStoreLike",
    "EKReminderLike",
    "MemberRoleResolver",
    "NoopEKEventStore",
    "passes_never_sync",
]
