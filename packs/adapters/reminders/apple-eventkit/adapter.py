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
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.schemas.lists import (  # noqa: F401  [§D7, D18]
    ListItemAddedV1,
    ListItemCompletedV1,
    ListItemUpdatedV1,
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


class AppleEventKitAdapter(ExternalStateMirrorAdapter):
    """Apple Reminders L1 standalone bidirectional adapter.

    Retrofitted onto :class:`ExternalStateMirrorAdapter` per prompt 11r-c
    (Cohort C retrofit step 3 of 3; ``docs/08-architecture-amendment-adapter-rebase.md``
    §3.3). The inheritance flip + the ``capabilities`` / ``write_capabilities``
    / ``sharing_model_discriminator`` ClassVars + the
    :meth:`_apply_write_impl` and :meth:`_on_external_change` ABC method
    implementations close the THIRD and FINAL ``advisory_pending_retrofit``
    finding the 11-β validator surfaces on ``base_class_conformance``,
    closing Cohort C.

    Cat-B's write surface is **asymmetric per [UT-27]**:
    ``write_capabilities = ["create_reminder"]`` declares ONLY the kind
    the adapter actually implements today. Declaring ``toggle_completion``
    / ``update_reminder`` / ``delete_reminder`` without method-presence
    would fail ``write_capabilities_check`` per memo 05 §3.4. The base
    class's :meth:`apply_write` raises :class:`WriteCapabilityError` for
    any undeclared kind BEFORE the external API is touched — that's the
    [UT-27] gate this retrofit makes load-bearing on every outbound write.

    Class attributes satisfy the ``Adapter`` Protocol's required fields.
    ``sensitivity_floor`` is hardcoded ``"normal"`` per [§6 invariant
    10]; any config attempt to set ``"privileged"`` raises at
    construction. Reminders adapters cannot auto-elevate — that's a
    deliberate operator decision for a future prompt; finer-grained
    item-level privacy lives in the never-sync predicate, not the floor.

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
    name: ClassVar[str] = "reminders-apple-eventkit"
    family: ClassVar[Literal["reminders", "calendaring", "contacts"]] = "reminders"
    version: ClassVar[str] = "1.0.0"
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    # [memo 05 §3.2, PM-35] class-level mirror of the manifest's
    # ``capabilities`` block for runtime introspection without re-parsing
    # ``pack.yaml``. One Cat-B entry declaring ``reminder.list_item.synced``;
    # the manifest at ``packs/adapters/reminders/apple-eventkit/pack.yaml``
    # is the source of truth.
    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-B",
            "runtime": "central",
            "event_families": ["reminder.list_item.synced"],
        }
    ]

    # [UT-27] asymmetric write_capabilities — declare ONLY what's
    # implemented. ``toggle_completion`` / ``update_reminder`` /
    # ``delete_reminder`` are out of scope per memo 08 §5 (inheritance +
    # manifest only, not a rewrite); declaring them without method
    # presence would fail ``write_capabilities_check``.
    write_capabilities: ClassVar[list[str]] = ["create_reminder"]

    # [memo 05 §2.5, PM-35] projection-row column distinguishing
    # per-member vs shared rows on the future ``lists`` / ``reminders``
    # projection (Cohort D 11f). Single-deployment central in 11r-c.
    sharing_model_discriminator: ClassVar[str] = "owner_member_id"

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

        # Per-id snapshot of the last-observed payload-relevant fields,
        # populated by :meth:`_emit_inbound_synced`. Used ONLY by the
        # ``list_item.*@v1`` dual-emit kind-derivation per [§D18] —
        # the legacy ``reminder.list_item.synced@v1`` dedupe path
        # continues to consult ``_seen_inbound`` byte-for-byte.
        self._inbound_snapshots: dict[str, dict[str, Any]] = {}

        # Cursor-advance milestone state. Mirrors Gmail's and Plaid's
        # pattern: count emissions since last advance + track wall-clock
        # since last advance; emit on whichever threshold fires first.
        self._events_since_inbound_advance: int = 0
        self._last_inbound_advance_at: datetime | None = None

        # Observation-mode wiring slots for :meth:`_apply_write_impl`
        # per [§6.20]. Phase A leaves these unset; the Phase B prompt
        # extending the supervisor's adapter-loading path will populate
        # them so EKEventStore writes route through
        # :func:`adminme.lib.observation.outbound`. Until then,
        # :meth:`_apply_write_impl` calls ``ek_store.save(...)`` directly
        # to preserve byte-for-byte the existing production save path
        # (the never-sync filter at the outbound boundary is per-item
        # privacy, NOT a substitute for the observation-mode operator
        # gate per memo 08 §3.3).
        self._observation_session: Any = None
        self._observation_manager: Any = None
        self._observation_event_log: Any = None

        # [D18, D22, memo 04 §2.2] Dual-deployment instance state.
        # Defaults preserve the central path byte-for-byte (F-2); bridge
        # branch flips these in :meth:`start`. ``_sharing_model_value``
        # surfaces the per-A-1 row discriminator (memo 05 §2.5 / §5.4)
        # as an instance attribute rather than via payload, because the
        # registered schemas use ``extra="forbid"`` and α-2 registers
        # no new schemas (§1).
        self._deployment_target: Literal["central", "bridge"] = "central"
        self._sharing_model_value: str = "shared_central"
        self._bridge_sink: Any = None

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
        lifecycle hook only.

        Dual-deployment (α-2): ``ctx.config["deployment_target"]`` selects
        ``"central"`` (default — existing 11b-i + 11r-c behavior, byte-
        for-byte per F-2) or ``"bridge"`` (per-member iCloud, emit via
        the wrapper-injected sink per memo 04 §2.2 / [§6.19]).
        """
        # [D18, D22, docs/10-split-memo-11b-ii-alpha.md §2] Resolve the
        # deployment branch from ctx.config. Default ``"central"`` keeps
        # existing instances backwards-compatible (F-2).
        deployment_target = ctx.config.get("deployment_target", "central")
        if deployment_target not in ("central", "bridge"):
            raise ValueError(
                "config.deployment_target must be 'central' or 'bridge'; "
                f"got {deployment_target!r}"
            )
        self._deployment_target = deployment_target
        # [A-1, memo 05 §2.5 / §5.4] central rows on the ``lists``
        # projection carry ``"shared_central"``; bridge rows carry
        # ``"per_member_isolated"``. Surfaced as an instance attribute
        # for downstream projection annotation (schemas are
        # ``extra="forbid"`` — no payload modification per §1).
        self._sharing_model_value = (
            "per_member_isolated"
            if deployment_target == "bridge"
            else "shared_central"
        )

        if deployment_target == "bridge":
            # [§1.4, A-3, memo 04 §2.2] Wrapper synthesizes ctx.config
            # with the per-member Apple ID identity + injected sink.
            bridge_member_id = ctx.config.get(
                "_bridge_member_id"
            ) or ctx.config.get("member_id")
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
                await self._drain_outbound_once(ctx=ctx)
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

    async def _drain_outbound_once(self, *, ctx: AdapterContext) -> int:
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
            if await self._write_one_outbound(
                ctx=ctx,
                upstream=upstream,
                sync_cycle_id=sync_cycle_id,
            ):
                written += 1
        return written

    async def _write_one_outbound(
        self,
        *,
        ctx: AdapterContext,
        upstream: dict[str, Any],
        sync_cycle_id: str,
    ) -> bool:
        """Translate one upstream event → ``WriteOperation``, apply the
        never-sync filter, dispatch through the Cat-B ABC's
        :meth:`apply_write` gate (which exercises the [UT-27]
        ``write_capabilities`` check on every production outbound write),
        and on a successful :class:`WriteResult` emit
        ``reminder.list_item.synced`` with ``direction="outbound"``.

        Returns ``True`` iff a synced event was emitted (``False`` on
        never-sync block / unmappable upstream / observation-mode
        suppression).

        Per memo 08 §3.3 + the prompt 11r-c §1 paragraph 3 — the gate is
        what makes ``write_capabilities`` load-bearing instead of
        decorative; the per-operation kind check is enforced BEFORE the
        external API is touched, so any code path that tries to invoke
        an undeclared kind (``toggle_completion`` / ``delete_reminder``)
        raises :class:`WriteCapabilityError` cleanly at the gate.

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

        # [UT-27] dispatch through the ABC gate. ``apply_write`` raises
        # ``WriteCapabilityError`` for any kind not declared in
        # ``write_capabilities`` BEFORE invoking ``_apply_write_impl`` —
        # the gate is exercised on every production outbound write.
        operation = WriteOperation(
            kind="create_reminder",
            target_external_id=None,
            payload={
                "member_id": member_id,
                "target_list_id": target_list_id,
                "target_list_name": target_list_name,
                "title": translation["title"],
                "notes": translation.get("notes"),
                "due_at": translation.get("due_at"),
                "priority": translation["priority"],
            },
        )
        result = await self.apply_write(operation)
        if not result.applied:
            # Observation mode suppressed the side effect, OR the impl
            # declined to apply for some other internal reason. In either
            # case the synced event is NOT emitted (no external state
            # changed; emitting would lie about iCloud-side state).
            return False

        # Apple stamps ``calendarItemIdentifier`` on save; the
        # WriteResult.external_id carries it forward for the
        # round-trip-traceability field on the emitted event.
        remind_id = result.external_id or ""
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

        # [§D18, D7] Dual-emit ``list_item.added@v1`` alongside the
        # legacy ``reminder.list_item.synced@v1``. Same ``causation_id``
        # + correlation_id (via ``ctx.correlation_id_factory()``) per
        # [§D8] — round-trip traceability spans both event families.
        # F-3 observation-suppressed invariant: this point is only
        # reachable when ``result.applied=True`` (the early ``return
        # False`` above on ``not result.applied`` covers the suppressed
        # path), so the new emit fires iff the legacy emit fires —
        # NEITHER on suppress, BOTH on apply.
        list_item_added_payload: dict[str, Any] = {
            "member_id": member_id,
            "list_external_id": target_list_id,
            "item_external_id": remind_id,
            "list_name": target_list_name,
            "title": translation["title"],
            "notes": translation.get("notes"),
            "due_at": _maybe_iso(translation.get("due_at")),
            "priority": translation["priority"],
            "completed": False,
            "last_modified_at": _to_iso(now),
            "sync_cycle_id": sync_cycle_id,
        }
        self._append(
            ctx=ctx,
            event_type="list_item.added",
            payload=list_item_added_payload,
            causation_id=causation_id,
        )
        return True

    # ------------------------------------------------------------------
    # ExternalStateMirrorAdapter ABC — Cat-B abstract method
    # implementations per prompt 11r-c §7 c2 / [UT-27] / [§6.20].
    # ------------------------------------------------------------------
    async def _apply_write_impl(
        self, operation: WriteOperation
    ) -> WriteResult:
        """Per-operation write implementation. Invoked by
        :meth:`apply_write` only after the [UT-27] capability gate
        passes — the gate guarantees ``operation.kind`` is in
        ``write_capabilities``, so the per-kind switch below only needs
        to cover declared kinds (``create_reminder`` today).

        Routes the actual ``EKEventStore.save(_:commit:)`` call through
        :func:`adminme.lib.observation.outbound` per [§6.20] when the
        Phase B observation-mode wiring slots are populated by the
        supervisor; otherwise calls ``ek_store.save`` directly (Phase A
        preserves the existing production save path byte-for-byte per
        memo 08 §5 — the never-sync filter at the outbound boundary is
        per-item privacy, NOT a substitute for the operator-level
        observation-mode gate).

        Returns :class:`WriteResult` with ``applied=True`` +
        ``external_id`` on save; ``applied=False`` + ``suppressed=True``
        on observation-mode suppression.
        """
        if operation.kind != "create_reminder":  # defense-in-depth
            # [UT-27]: the apply_write gate should have caught this.
            # If we got here, write_capabilities and this dispatch are
            # out of sync — surface loudly rather than silently no-op.
            raise WriteCapabilityError(
                f"AppleEventKitAdapter._apply_write_impl received "
                f"undeclared kind={operation.kind!r}"
            )

        ek_store = self._ek_store
        if ek_store is None:
            # The supervisor calls start() before any outbound dispatch;
            # arriving here means a test invoked apply_write before
            # start(). Honest no-op rather than a crash.
            return WriteResult(applied=False, external_id=None, suppressed=False)

        payload = operation.payload
        reminder = ek_store.build_reminder(
            list_identifier=str(payload["target_list_id"]),
            title=str(payload["title"]),
            notes=payload.get("notes"),
            due_at=payload.get("due_at"),
            priority=payload["priority"],
        )

        # [§6.20] observation-mode wrap. The 11-β validator's
        # ``observation_mode_wrapping`` AST-walk finds the call site;
        # runtime suppression activates once the supervisor's adapter-
        # loading path wires the seam slots (Phase B).
        session = self._observation_session
        manager = self._observation_manager
        event_log = self._observation_event_log

        async def _do_save() -> None:
            # ``save(_:commit:)`` per docs/reference/apple-eventkit/create.md
            # — ``commit=True`` flushes immediately rather than batching
            # for a later ``commit()`` call. Each outbound write is its
            # own transaction so iCloud sync churn stays per-reminder.
            ek_store.save(reminder, commit=True)

        if session is not None and manager is not None and event_log is not None:
            outbound_result = await observation_outbound(
                session,
                "reminders.create_reminder",
                {
                    "member_id": str(payload.get("member_id", "")),
                    "list_identifier": str(payload["target_list_id"]),
                    "list_name": str(payload.get("target_list_name", "")),
                    "title": str(payload["title"]),
                    "priority": payload["priority"],
                },
                _do_save,
                manager=manager,
                event_log=event_log,
                target_channel="reminders-apple-eventkit",
                target_identifier=str(payload["target_list_id"]),
            )
            if outbound_result.suppressed:
                return WriteResult(
                    applied=False, external_id=None, suppressed=True
                )
        else:
            # Phase A — seams unset; the existing production save path
            # is preserved byte-for-byte.
            await _do_save()

        remind_id = str(
            getattr(reminder, "calendarItemIdentifier", "") or ""
        )
        return WriteResult(
            applied=True, external_id=remind_id or None, suppressed=False
        )

    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        """Cat-B ABC entry point for inbound change envelopes. Per memo
        08 §5 (inheritance + manifest only, not a rewrite) and prompt
        11r-c §1 paragraph 3, the existing :meth:`_inbound_poll_once`
        keeps calling :meth:`_emit_inbound_synced` directly for the
        production poll path; this hook is the framework contract entry
        point that ``base_class_conformance`` checks find via AST-walk
        and that future ingress paths (webhook handlers, 11g's HA Cat-C/
        Cat-E adapters routing real change envelopes) will dispatch
        through.

        Synthesizes a reminder-like duck-typed object from
        ``envelope.payload`` and delegates to the existing
        :meth:`_emit_inbound_synced`. The ``ctx`` is captured at
        :meth:`start` time; if the hook fires before start(), the call
        is an honest no-op.
        """
        ctx = self._ctx
        if ctx is None:
            return

        payload = envelope.payload
        sync_cycle_id = str(payload.get("sync_cycle_id") or _next_sync_cycle_id())

        # Build a duck-typed reminder shim from the envelope payload.
        # The existing _emit_inbound_synced reads attributes via
        # getattr(); a SimpleNamespace satisfies that contract without
        # depending on the EKReminderLike Protocol type.
        from types import SimpleNamespace

        last_modified_raw = payload.get("last_modified_at")
        last_modified_at: datetime | None
        if isinstance(last_modified_raw, datetime):
            last_modified_at = last_modified_raw
        elif isinstance(last_modified_raw, str) and last_modified_raw:
            try:
                last_modified_at = datetime.fromisoformat(
                    last_modified_raw.replace("Z", "+00:00")
                )
            except ValueError:
                last_modified_at = None
        else:
            last_modified_at = None

        synthetic = SimpleNamespace(
            calendarItemIdentifier=envelope.external_id,
            list_identifier=str(payload.get("list_identifier", "")),
            title=str(payload.get("title", "")),
            notes=payload.get("notes"),
            due_at=payload.get("due_at"),
            priority=payload.get("priority", 0),
            completed=bool(payload.get("completed", False)),
            last_modified_at=last_modified_at,
        )
        self._emit_inbound_synced(
            ctx=ctx, reminder=synthetic, sync_cycle_id=sync_cycle_id
        )

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

        # [§D18, D7] Dual-emit a ``list_item.*@v1`` event alongside the
        # legacy ``reminder.list_item.synced@v1``. The kind is derived
        # from session cache state captured BEFORE the emit:
        #   first observation        → ``list_item.added``
        #   completion-flip f→t       → ``list_item.completed``
        #   any other field change   → ``list_item.updated`` (with
        #                              ``field_updates``)
        # ``list_item.deleted@v1`` on the inbound path is deferred per
        # prompt 11b-i §6 (requires negative-set logic across cycles).
        prior_snapshot = self._inbound_snapshots.get(remind_id)
        is_first_observation = remind_id not in self._seen_inbound
        new_list_item_payload: dict[str, Any] = {
            "member_id": member_id,
            "list_external_id": list_id,
            "item_external_id": remind_id,
            "list_name": list_name,
            "title": title[:500],
            "notes": notes,
            "due_at": _maybe_iso(due_at),
            "priority": priority,
            "completed": completed,
            "last_modified_at": _to_iso(emit_lmod),
            "sync_cycle_id": sync_cycle_id,
        }
        if is_first_observation:
            new_event_type = "list_item.added"
        elif (
            prior_snapshot is not None
            and prior_snapshot.get("completed") is False
            and completed
        ):
            new_event_type = "list_item.completed"
        else:
            new_event_type = "list_item.updated"
            field_updates: dict[str, Any] = {}
            if prior_snapshot is not None:
                # Compare each canonical payload field (excluding the
                # stable identifiers + sync_cycle_id) against the prior
                # snapshot; surface every changed field for downstream
                # diffing per ``CalendarEventUpdatedV1`` precedent.
                for diff_field in (
                    "title",
                    "notes",
                    "due_at",
                    "priority",
                    "completed",
                    "last_modified_at",
                ):
                    if new_list_item_payload.get(diff_field) != prior_snapshot.get(
                        diff_field
                    ):
                        field_updates[diff_field] = new_list_item_payload.get(
                            diff_field
                        )
            new_list_item_payload["field_updates"] = field_updates
        self._append(
            ctx=ctx, event_type=new_event_type, payload=new_list_item_payload
        )

        self._seen_inbound[remind_id] = emit_lmod
        self._inbound_snapshots[remind_id] = {
            "title": title[:500],
            "notes": notes,
            "due_at": _maybe_iso(due_at),
            "priority": priority,
            "completed": completed,
            "last_modified_at": _to_iso(emit_lmod),
        }
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
        log itself may be down.

        α-2 dual-deployment branch: ``deployment_target=="bridge"``
        dispatches through the wrapper-injected ``BridgeEventSink``
        (memo 04 §2.2); central path preserved byte-for-byte (F-2). F-4:
        the observation-mode gate upstream of every emit site is
        unchanged, so suppressed writes skip ALL emits uniformly."""
        if self._deployment_target == "bridge":  # [§D18, memo 04 §2.2]
            self._append_via_bridge_sink(
                ctx=ctx,
                event_type=event_type,
                payload=payload,
                causation_id=causation_id,
            )
            return

        # ----- central path (byte-for-byte preserved from 11b-i / 11r-c) -----
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

    def _append_via_bridge_sink(
        self,
        *,
        ctx: AdapterContext,
        event_type: str,
        payload: dict[str, Any],
        causation_id: str | None,
    ) -> None:
        """Bridge-deployment emit path. Build an ``EventEnvelope`` and
        schedule ``sink.emit(envelope)`` on the running event loop —
        ``_append`` is sync and called from both async + sync sites, so
        a fire-and-forget task keeps the call shape symmetric with the
        central path. Memo 04 §2.2: provisional ``private:<member_id>``
        scopes are re-stamped by the central :3337 router from
        Tailscale identity post-receipt."""
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
                "reminders adapter bridge emit skipped — no running event "
                "loop (type=%s)",
                event_type,
            )
            return
        loop.create_task(self._dispatch_bridge_emit(envelope))

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
                "reminders adapter bridge sink emit failed for %s",
                envelope.type,
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
