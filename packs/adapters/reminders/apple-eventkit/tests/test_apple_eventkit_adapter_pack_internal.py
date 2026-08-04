"""Apple Reminders adapter pack-internal tests.

C2 shipped (outbound):
  1. Pack-load canary — ``pack.yaml`` parses, sentinel fields match.
  2. Adapter satisfies the L1 ``Adapter`` Protocol with the correct
     ``name`` / ``family`` / ``version`` / ``sensitivity_floor``.
  3. ``sensitivity_floor`` cannot be raised via config — reminders
     adapters never auto-elevate to ``"privileged"``.
  4. Outbound drain pops ``reminder.dispatched`` events and calls
     ``EKEventStore.save(_:commit:)`` per BUILD.md APPLE REMINDERS
     BIDIRECTIONAL §"Outbound write cadence".
  5. Outbound emits ``reminder.list_item.synced`` v1 with
     ``direction="outbound"`` and the returned
     ``calendarItemIdentifier`` for round-trip traceability.
  6. Never-sync filter blocks outbound writes to a "Work" list at the
     OUTBOUND boundary — symmetric with the inbound application in c3.
  7. Never-sync filter blocks outbound writes for ``"child"`` /
     ``"ambient"`` owning member roles per BUILD.md APPLE REMINDERS
     BIDIRECTIONAL §"Never-sync rules".

C3 adds (inbound poll + dedupe + never-sync filter on iCloud reads):
  8. Inbound poll fetches reminders from a recorded fixture and emits
     one ``reminder.list_item.synced`` v1 per item with
     ``direction="inbound"`` + ``causation_id=None``.
  9. Never-sync filter blocks 200 reminders from a "Work case files"
     list at the INBOUND boundary — anti-vision §"Quiet by default":
     0 events emitted regardless of fetch volume.
 10. Never-sync item-tag rule blocks ``"#private"`` reminders at the
     inbound boundary (third rule of the canonical never-sync ruleset).
 11. Dedupe predicate emits on first sight (new) and skips unchanged
     poll-cycle entries (a fresh second poll of the same set emits 0).
 12. Dedupe predicate emits when ``last_modified_at`` advances on a
     re-seen ``remind_id`` (genuinely-modified detection).
 13. Inbound dedupe is permissive when the projection resolver returns
     ``None`` (Phase A fallback per prompt 11c §3 deferred-watch — the
     ``reminders`` projection's writer pipeline is deferred).
 14. ``adapter.cursor_advanced`` v1 fires at the 100-emit milestone and
     carries the JSON-encoded per-list cursor map; pure per-cycle
     emission would defeat anti-vision §"Quiet by default".

C4 adds (supervisor lifecycle — both-poll-tasks supervision):
 15. ``start()`` spawns BOTH the inbound and outbound poll tasks
     concurrently and ``stop()`` cancels both cleanly. c2 + c3 spawned
     only the outbound task; c4 closes the bidirectional supervision
     surface per BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Polling
     cadence" + §"Outbound write cadence".
 16. A ``NoopEKEventStore`` factory (which raises on every operation)
     surfaces a ``RuntimeError`` through the supervised tasks; the
     adapter classifies it via ``_classify_eventkit_error`` and emits
     ``adapter.error`` v1 with the supervisor-expected shape so the
     supervisor's restart-with-backoff loop takes over per
     ``adminme/daemons/adapter_supervisor.py``.

F-3 (10c-iii, reinforced by 11a + 11b): test basename is unique
(``test_apple_eventkit_adapter_pack_internal.py``) so collection from
the repo root does not collide with sibling pack-internal tests.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from adminme.adapters.base import Adapter, AdapterContext

PACK_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root via importlib — same
    file-path import pattern the supervisor's loader uses. Packs are
    not a top-level Python package under poetry (only ``adminme`` is).
    """
    spec = importlib.util.spec_from_file_location(
        "apple_eventkit_adapter_under_test", PACK_ROOT / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_adapter_module()
AppleEventKitAdapter: Any = _adapter_mod.AppleEventKitAdapter


_FROZEN_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


class _FakeEventLog:
    """Captures synchronous append calls for assertion. Mirrors the
    gmail pack's ``_FakeEventLog`` — tolerant of any kwarg shape so
    causation_id / source / etc. all flow through to ``calls``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **kwargs: Any) -> str:
        self.calls.append({**envelope.model_dump(), **kwargs})
        return f"event-{len(self.calls)}"


def _ctx(event_log: _FakeEventLog) -> AdapterContext:
    """Build an AdapterContext with deterministic clock + correlation
    id. The Reminders adapter reads ``ctx.config`` indirectly via
    ``self._config`` so this ctx's ``config`` is left empty."""
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-test",
        now_factory=lambda: _FROZEN_NOW,
    )


class _FakeEKReminder:
    """Stand-in for ``EKReminder``. ``calendarItemIdentifier`` is the
    field the outbound emit pulls back for round-trip traceability. The
    inbound-path attributes (``last_modified_at`` / ``completed``)
    default sensibly so c2's outbound builder path stays unchanged."""

    def __init__(
        self,
        *,
        list_identifier: str,
        title: str,
        notes: str | None,
        due_at: Any,
        priority: int,
        calendar_item_identifier: str,
        last_modified_at: datetime | None = None,
        completed: bool = False,
    ) -> None:
        self.list_identifier = list_identifier
        self.title = title
        self.notes = notes
        self.due_at = due_at
        self.priority = priority
        self.calendarItemIdentifier = calendar_item_identifier
        self.last_modified_at = last_modified_at or _FROZEN_NOW
        self.completed = completed


class _FakeEKEventStore:
    """Strict fake EKEventStore. Records every ``build_reminder`` +
    ``save`` invocation for assertion; lets the test pre-configure the
    ``list_name_for_identifier`` lookup so the never-sync filter sees
    the intended list name. ``fetch_reminders`` returns the test's
    pre-loaded inbound batch for the c3 inbound-path tests."""

    def __init__(
        self,
        *,
        list_names: dict[str, str],
        mint_calendar_item_identifiers: list[str] | None = None,
        inbound_reminders: list[Any] | None = None,
    ) -> None:
        self._list_names = dict(list_names)
        self._mint_queue = list(mint_calendar_item_identifiers or [])
        self._inbound_reminders = list(inbound_reminders or [])
        self.builds: list[dict[str, Any]] = []
        self.saves: list[tuple[Any, bool]] = []
        self.fetch_calls: int = 0

    def build_reminder(
        self,
        *,
        list_identifier: str,
        title: str,
        notes: str | None,
        due_at: Any,
        priority: int,
    ) -> _FakeEKReminder:
        self.builds.append(
            {
                "list_identifier": list_identifier,
                "title": title,
                "notes": notes,
                "due_at": due_at,
                "priority": priority,
            }
        )
        cid = (
            self._mint_queue.pop(0) if self._mint_queue else f"EKID-{len(self.builds)}"
        )
        return _FakeEKReminder(
            list_identifier=list_identifier,
            title=title,
            notes=notes,
            due_at=due_at,
            priority=priority,
            calendar_item_identifier=cid,
        )

    def save(self, reminder: Any, *, commit: bool) -> None:
        self.saves.append((reminder, commit))

    def remove(self, reminder: Any, *, commit: bool) -> None:  # pragma: no cover
        return None

    def fetch_reminders(self, *, list_identifier: str | None = None) -> list[Any]:
        self.fetch_calls += 1
        if list_identifier is None:
            return list(self._inbound_reminders)
        return [
            r
            for r in self._inbound_reminders
            if getattr(r, "list_identifier", None) == list_identifier
        ]

    def set_inbound_reminders(self, reminders: list[Any]) -> None:
        """Test helper — replace the inbound batch so the next
        ``fetch_reminders`` call returns the new set. Mirrors the
        EventKit live-store semantics where a second poll sees the
        post-mutation reminder set."""
        self._inbound_reminders = list(reminders)

    def list_name_for_identifier(self, list_identifier: str) -> str:
        return self._list_names.get(list_identifier, list_identifier)


def _make_adapter(
    *,
    ek_store: _FakeEKEventStore,
    config_overrides: dict[str, Any] | None = None,
    member_role: str = "adult",
    dedupe_resolver: Any = None,
) -> Any:
    """Construct an adapter pre-bound to the fake store. Tests skip
    ``start()`` (which would block on the outbound task forever) by
    poking ``_ek_store`` directly. ``dedupe_resolver`` defaults to the
    permissive None-returner (Phase A); inbound dedupe tests pass an
    explicit resolver to verify the projection-fallback path."""
    config: dict[str, Any] = {
        "target_list_identifier": "list-household-001",
        "member_id": "member-001",
    }
    if config_overrides:
        config.update(config_overrides)

    def factory() -> Any:
        return ek_store

    def role_resolver(_member_id: str) -> str:
        return member_role

    kwargs: dict[str, Any] = {
        "config": config,
        "ek_event_store_factory": factory,
        "member_role_resolver": role_resolver,
    }
    if dedupe_resolver is not None:
        kwargs["dedupe_resolver"] = dedupe_resolver

    adapter = AppleEventKitAdapter(**kwargs)
    adapter._ek_store = ek_store
    return adapter


def _upstream_reminder_dispatched(
    *,
    composed_text: str = "Sign permission slip by tomorrow",
    owner_member_id: str = "member-001",
    event_id: str = "evt-dispatch-1",
) -> dict[str, Any]:
    """Build an envelope-shaped upstream ``reminder.dispatched`` event.
    ``id`` flows through to ``causation_id`` on the emitted
    ``reminder.list_item.synced``."""
    return {
        "id": event_id,
        "type": "reminder.dispatched",
        "payload": {
            "commitment_id": "cmt-1",
            "owner_member_id": owner_member_id,
            "recipient_party_id": "party-1",
            "composed_text": composed_text,
            "channel": "imessage",
            "due_at": _FROZEN_NOW.isoformat(),
            "delivered": False,
            "validation_failed": False,
        },
    }


# ---------------------------------------------------------------------------
# 1) pack-load canary
# ---------------------------------------------------------------------------


def test_pack_manifest_loads_cleanly() -> None:
    """``pack.yaml`` parses, declares ``kind: adapter``, family
    ``reminders``, the 1-minute external_poll heartbeat, and the seven
    event types the adapter touches in either direction (3
    cross-cutting + ``reminder.list_item.synced`` + the 3 outbound
    triggers)."""
    manifest_path = PACK_ROOT / "pack.yaml"
    with manifest_path.open() as f:
        manifest = yaml.safe_load(f)

    assert manifest["pack"]["id"] == "adapter:reminders-apple-eventkit"
    assert manifest["pack"]["kind"] == "adapter"
    assert manifest["pack"]["version"] == "1.0.0"
    # ``family`` lives inside the ``pack:`` block per the c1 manifest
    # layout (the Gmail pack hoists ``family`` to top-level — Reminders
    # nests it; both are valid manifest shapes for the loader).
    assert manifest["pack"]["family"] == "reminders"
    assert manifest["sensitivity_floor"] == "normal"
    assert manifest["triggers"]["kind"] == "external_poll"
    assert manifest["triggers"]["poll_interval_seconds"] == 30
    assert "schedule" not in manifest["triggers"]

    declared = set(manifest["depends_on"]["event_types"])
    for required in (
        "adapter.error",
        "adapter.authenticated",
        "adapter.cursor_advanced",
        "reminder.list_item.synced",
        "reminder.dispatched",
        "task.created",
        "commitment.proposed",
    ):
        assert required in declared, f"manifest missing {required!r}"

    # The two sibling config files exist and are referenced.
    assert (PACK_ROOT / manifest["config"]["schema"]).exists()
    assert (PACK_ROOT / manifest["config"]["example"]).exists()


def test_apple_eventkit_adapter_satisfies_protocol() -> None:
    """The runtime class implements the L1 ``Adapter`` Protocol — the
    supervisor's loader will not accept an instance that doesn't."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    assert isinstance(adapter, Adapter)
    assert adapter.name == "reminders-apple-eventkit"
    assert adapter.family == "reminders"
    assert adapter.version == "1.0.0"
    assert adapter.sensitivity_floor == "normal"


# ---------------------------------------------------------------------------
# 2) sensitivity-floor invariant — Reminders cannot elevate via config
# ---------------------------------------------------------------------------


def test_sensitivity_floor_cannot_be_raised_to_privileged() -> None:
    """Reminders adapters are hardcoded ``"normal"``. Attempting to set
    ``sensitivity_floor: "privileged"`` in config raises at
    construction — adapters never auto-elevate per [§6 invariant 10];
    that would be a deliberate operator decision for a future prompt.
    """
    with pytest.raises(ValueError, match="must be 'normal'"):
        AppleEventKitAdapter(
            config={
                "target_list_identifier": "list-household-001",
                "member_id": "member-001",
                "sensitivity_floor": "privileged",
            },
        )


# ---------------------------------------------------------------------------
# 3) outbound drains the queue + calls EventKit.save(_:commit:)
# ---------------------------------------------------------------------------


def test_outbound_drain_calls_save_on_eventkit() -> None:
    """``_drain_outbound_once`` pops the queued ``reminder.dispatched``,
    asks the EKEventStore to build an EKReminder with the upstream
    fields, and calls ``save(_:commit:)`` with ``commit=True``."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        mint_calendar_item_identifiers=["EKID-alpha"],
    )
    adapter = _make_adapter(ek_store=ek_store)
    adapter.enqueue_outbound(
        _upstream_reminder_dispatched(composed_text="Pick up Maya at 5pm")
    )

    event_log = _FakeEventLog()
    written = asyncio.run(adapter._drain_outbound_once(ctx=_ctx(event_log)))

    assert written == 1
    assert len(ek_store.builds) == 1
    build = ek_store.builds[0]
    assert build["title"] == "Pick up Maya at 5pm"
    assert build["list_identifier"] == "list-household-001"
    # ``reminder.dispatched`` maps to high priority (EventKit ordinal 1).
    assert build["priority"] == 1
    # save was called once with commit=True for the built reminder.
    assert len(ek_store.saves) == 1
    saved_reminder, commit_flag = ek_store.saves[0]
    assert commit_flag is True
    assert saved_reminder.calendarItemIdentifier == "EKID-alpha"


def test_outbound_drain_ignores_non_trigger_event_types() -> None:
    """``enqueue_outbound`` filters events whose ``type`` is not in the
    outbound trigger set — ``digest.composed`` / ``crm.surfaced`` /
    etc. route elsewhere per §6 out-of-scope."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    adapter.enqueue_outbound(
        {"id": "evt-x", "type": "digest.composed", "payload": {"x": 1}}
    )

    event_log = _FakeEventLog()
    written = asyncio.run(adapter._drain_outbound_once(ctx=_ctx(event_log)))

    assert written == 0
    assert ek_store.builds == []
    assert ek_store.saves == []


# ---------------------------------------------------------------------------
# 4) outbound emits reminder.list_item.synced with direction="outbound"
# ---------------------------------------------------------------------------


def test_outbound_emit_reminder_list_item_synced_with_direction_outbound() -> None:
    """Every successful outbound write emits one
    ``reminder.list_item.synced`` v1 event with
    ``direction="outbound"``, the ``calendarItemIdentifier`` returned by
    ``EKEventStore.save(_:commit:)`` as the ``remind_id``,
    ``causation_id`` set to the upstream event's id (round-trip
    traceability), and a fresh ``sync_cycle_id`` for the drain batch.
    """
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        mint_calendar_item_identifiers=["EKID-beta-42"],
    )
    adapter = _make_adapter(ek_store=ek_store)
    adapter.enqueue_outbound(
        _upstream_reminder_dispatched(
            composed_text="Mail rent check by Friday",
            event_id="evt-dispatch-42",
        )
    )

    event_log = _FakeEventLog()
    asyncio.run(adapter._drain_outbound_once(ctx=_ctx(event_log)))

    synced = [c for c in event_log.calls if c["type"] == "reminder.list_item.synced"]
    assert len(synced) == 1
    call = synced[0]
    assert call["schema_version"] == 1
    assert call["correlation_id"] == "corr-test"
    assert call["causation_id"] == "evt-dispatch-42"

    payload = call["payload"]
    assert payload["member_id"] == "member-001"
    assert payload["remind_id"] == "EKID-beta-42"
    assert payload["list_identifier"] == "list-household-001"
    assert payload["list_name"] == "AdministrateMe Household"
    assert payload["title"] == "Mail rent check by Friday"
    assert payload["direction"] == "outbound"
    assert payload["completed"] is False
    assert payload["priority"] == 1
    assert payload["sync_cycle_id"].startswith("sync-")
    assert payload["last_modified_at"] == "2026-05-16T12:00:00Z"


# ---------------------------------------------------------------------------
# 5) never-sync filter blocks outbound to a "Work" list
# ---------------------------------------------------------------------------


def test_never_sync_blocks_outbound_to_work_list() -> None:
    """Per VISION §"Privacy by default": writing to a list whose name
    contains a never-sync substring (``"Work"`` / ``"Case"`` /
    ``"Client"``) is blocked at the OUTBOUND boundary. No
    ``EKEventStore.save`` is attempted; no
    ``reminder.list_item.synced`` is emitted."""
    ek_store = _FakeEKEventStore(
        list_names={"list-work-cases": "Work case files"},
    )
    adapter = _make_adapter(
        ek_store=ek_store,
        config_overrides={"target_list_identifier": "list-work-cases"},
    )
    adapter.enqueue_outbound(
        _upstream_reminder_dispatched(composed_text="Sensitive client task")
    )

    event_log = _FakeEventLog()
    written = asyncio.run(adapter._drain_outbound_once(ctx=_ctx(event_log)))

    assert written == 0
    assert ek_store.builds == []
    assert ek_store.saves == []
    synced = [c for c in event_log.calls if c["type"] == "reminder.list_item.synced"]
    assert synced == []


def test_never_sync_blocks_outbound_for_child_member_role() -> None:
    """Per BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Never-sync rules":
    reminders owned by a ``"child"`` or ``"ambient"`` household member
    are blocked from sync. Symmetric at both boundaries; verified here
    at the outbound boundary."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
    )
    adapter = _make_adapter(ek_store=ek_store, member_role="child")
    adapter.enqueue_outbound(_upstream_reminder_dispatched())

    event_log = _FakeEventLog()
    written = asyncio.run(adapter._drain_outbound_once(ctx=_ctx(event_log)))

    assert written == 0
    assert ek_store.builds == []
    assert ek_store.saves == []


# ---------------------------------------------------------------------------
# 6) inbound poll — c3 implementation
# ---------------------------------------------------------------------------


def _load_inbound_fixture() -> dict[str, Any]:
    """Load the recorded EventKit fixture. The pack-internal directory
    layout follows F-3 (10c-iii); fixtures live under
    ``tests/fixtures/`` next to the test file."""
    path = FIXTURE_DIR / "inbound_basic.json"
    with path.open() as f:
        return json.load(f)


def _reminders_from_fixture(
    fixture: dict[str, Any],
) -> list[_FakeEKReminder]:
    """Materialize fixture JSON into a list of ``_FakeEKReminder``
    instances with parsed datetimes. ``last_modified_at`` is required
    for the dedupe predicate; ``due_at`` is optional."""
    out: list[_FakeEKReminder] = []
    for entry in fixture["reminders"]:
        lmod = datetime.fromisoformat(entry["last_modified_at"].replace("Z", "+00:00"))
        due_raw = entry.get("due_at")
        due_at = (
            datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
            if isinstance(due_raw, str)
            else None
        )
        out.append(
            _FakeEKReminder(
                list_identifier=entry["list_identifier"],
                title=entry["title"],
                notes=entry.get("notes"),
                due_at=due_at,
                priority=int(entry.get("priority", 0)),
                calendar_item_identifier=entry["calendarItemIdentifier"],
                last_modified_at=lmod,
                completed=bool(entry.get("completed", False)),
            )
        )
    return out


def test_inbound_poll_emits_reminder_list_item_synced_inbound() -> None:
    """The inbound poll fetches reminders, applies the never-sync
    filter, and emits one ``reminder.list_item.synced`` v1 event per
    new reminder. ``direction="inbound"`` and ``causation_id`` is
    absent (iCloud is the source — no upstream AdministrateMe event
    triggered the emission per the §9 causation invariant)."""
    fixture = _load_inbound_fixture()
    reminders = _reminders_from_fixture(fixture)
    ek_store = _FakeEKEventStore(
        list_names=fixture["lists"],
        inbound_reminders=reminders,
    )
    adapter = _make_adapter(ek_store=ek_store)

    event_log = _FakeEventLog()
    emitted = asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))

    # Fixture has 4 reminders. The "#private" one is never-sync; the
    # other 3 pass.
    assert emitted == 3
    synced = [c for c in event_log.calls if c["type"] == "reminder.list_item.synced"]
    assert len(synced) == 3
    for call in synced:
        assert call["schema_version"] == 1
        assert call["payload"]["direction"] == "inbound"
        # [Causation] inbound carries no causation_id — iCloud is the
        # source; nothing in AdministrateMe triggered this emission.
        assert call["causation_id"] is None
    # All emitted events share one sync_cycle_id from the drain batch.
    cycle_ids = {c["payload"]["sync_cycle_id"] for c in synced}
    assert len(cycle_ids) == 1
    # remind_ids match the fixture (minus the "#private" one).
    emitted_ids = {c["payload"]["remind_id"] for c in synced}
    assert "x-coredata://AdministrateMe/EKReminder/rem-private" not in emitted_ids
    assert "x-coredata://AdministrateMe/EKReminder/rem-001" in emitted_ids


def test_inbound_never_sync_blocks_200_work_case_files_reminders() -> None:
    """Anti-vision §"Quiet by default": a poll cycle returning 200
    reminders from a "Work case files" list produces ZERO events. The
    never-sync filter is applied symmetrically at the inbound boundary
    per BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Never-sync rules"."""
    work_list_id = "list-work-cases"
    work_list_name = "Work case files"
    reminders = [
        _FakeEKReminder(
            list_identifier=work_list_id,
            title=f"Sensitive client task {i}",
            notes=None,
            due_at=None,
            priority=5,
            calendar_item_identifier=f"x-coredata://work/rem-{i:04d}",
            last_modified_at=_FROZEN_NOW + timedelta(seconds=i),
        )
        for i in range(200)
    ]
    ek_store = _FakeEKEventStore(
        list_names={work_list_id: work_list_name},
        inbound_reminders=reminders,
    )
    adapter = _make_adapter(ek_store=ek_store)

    event_log = _FakeEventLog()
    emitted = asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))

    assert emitted == 0
    synced = [c for c in event_log.calls if c["type"] == "reminder.list_item.synced"]
    assert synced == []


def test_inbound_never_sync_item_tag_blocks_private_reminders() -> None:
    """Rule 2 (item-tag substring) blocks at the inbound boundary too.
    The fixture's ``"#private — therapy appt"`` reminder lives in the
    benign "Personal" list — only the tag-match rule excludes it."""
    fixture = _load_inbound_fixture()
    reminders = _reminders_from_fixture(fixture)
    # Isolate just the private reminder to keep the assertion direct.
    private_only = [
        r
        for r in reminders
        if r.calendarItemIdentifier
        == "x-coredata://AdministrateMe/EKReminder/rem-private"
    ]
    assert private_only, "fixture should include the private reminder"
    ek_store = _FakeEKEventStore(
        list_names=fixture["lists"],
        inbound_reminders=private_only,
    )
    adapter = _make_adapter(ek_store=ek_store)

    event_log = _FakeEventLog()
    emitted = asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))

    assert emitted == 0


def test_inbound_dedupe_skips_unchanged_reminders_on_second_poll() -> None:
    """Two consecutive polls with the same fixture: first emits, second
    is silent. The session-level ``_seen_inbound`` cache is the gate
    that keeps emission load-bearing instead of one-per-poll-cycle
    (anti-vision §"Quiet by default")."""
    fixture = _load_inbound_fixture()
    reminders = _reminders_from_fixture(fixture)
    ek_store = _FakeEKEventStore(
        list_names=fixture["lists"],
        inbound_reminders=reminders,
    )
    adapter = _make_adapter(ek_store=ek_store)

    event_log = _FakeEventLog()
    first = asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))
    second = asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))

    assert first == 3
    assert second == 0, (
        "second poll must emit zero events when nothing changed — "
        "anti-vision §'Quiet by default'"
    )


def test_inbound_dedupe_emits_when_last_modified_advances() -> None:
    """A re-seen ``remind_id`` whose ``last_modified_at`` advances is
    a genuinely-modified reminder and MUST emit a fresh
    ``reminder.list_item.synced`` event. The dedupe predicate compares
    timestamps strictly (`>`), so a tie-breaking equal timestamp does
    NOT re-emit."""
    base_id = "x-coredata://AdministrateMe/EKReminder/edit-me"
    initial_lmod = _FROZEN_NOW
    later_lmod = _FROZEN_NOW + timedelta(minutes=5)

    rem_v1 = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Pick up milk",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier=base_id,
        last_modified_at=initial_lmod,
    )
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        inbound_reminders=[rem_v1],
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    first = asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))
    assert first == 1

    # User edits the reminder in iCloud — last_modified_at advances.
    rem_v2 = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Pick up milk and bread",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier=base_id,
        last_modified_at=later_lmod,
    )
    ek_store.set_inbound_reminders([rem_v2])
    second = asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))
    assert second == 1, "modified reminder must re-emit"

    synced = [c for c in event_log.calls if c["type"] == "reminder.list_item.synced"]
    assert len(synced) == 2
    assert all(c["payload"]["remind_id"] == base_id for c in synced)
    assert synced[1]["payload"]["title"] == "Pick up milk and bread"


def test_inbound_dedupe_permissive_when_projection_resolver_absent() -> None:
    """Phase A fallback per prompt 11c §3 deferred-watch: the
    ``reminders`` projection's writer pipeline is deferred, so the
    default ``dedupe_resolver`` returns ``None`` for every ``remind_id``
    and the predicate treats absence-of-record as "new". Once the
    projection's writer lands, production wires a real resolver — no
    adapter change required."""
    fixture = _load_inbound_fixture()
    reminders = _reminders_from_fixture(fixture)
    ek_store = _FakeEKEventStore(
        list_names=fixture["lists"],
        inbound_reminders=reminders,
    )
    # No dedupe_resolver passed — falls through to the module-level
    # permissive default that returns None.
    adapter = _make_adapter(ek_store=ek_store)

    event_log = _FakeEventLog()
    emitted = asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))

    # All 3 non-never-sync reminders emit on first sight because the
    # resolver returns None for every id (permissive fallback).
    assert emitted == 3


def test_inbound_dedupe_consults_projection_resolver_when_present() -> None:
    """When the projection resolver returns a timestamp, the dedupe
    predicate compares against it. A resolver that returns the same
    ``last_modified_at`` as the live reminder must skip the emit; a
    resolver returning an older timestamp must emit (modified)."""
    base_id = "x-coredata://AdministrateMe/EKReminder/proj-known"
    seen_lmod = _FROZEN_NOW - timedelta(hours=1)
    current_lmod = _FROZEN_NOW
    rem = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Returning to inbox",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier=base_id,
        last_modified_at=current_lmod,
    )
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        inbound_reminders=[rem],
    )

    # Resolver reports an older timestamp → modified → emit.
    def resolver_old(_remind_id: str) -> Any:
        return seen_lmod

    adapter_emit = _make_adapter(ek_store=ek_store, dedupe_resolver=resolver_old)
    event_log_emit = _FakeEventLog()
    assert asyncio.run(adapter_emit._inbound_poll_once(ctx=_ctx(event_log_emit))) == 1

    # Resolver reports the same timestamp → unchanged → skip.
    def resolver_current(_remind_id: str) -> Any:
        return current_lmod

    adapter_skip = _make_adapter(ek_store=ek_store, dedupe_resolver=resolver_current)
    event_log_skip = _FakeEventLog()
    assert asyncio.run(adapter_skip._inbound_poll_once(ctx=_ctx(event_log_skip))) == 0


def test_inbound_cursor_advances_at_100_event_milestone() -> None:
    """Per the cross-cutting milestone discipline (Gmail + Plaid share
    the same threshold): ``adapter.cursor_advanced`` v1 fires after
    100 emissions since the last advance. The cursor_value packs the
    per-list watermark dict as a JSON-encoded string so a single audit
    event captures the whole reminder-family cursor map."""
    reminders = [
        _FakeEKReminder(
            list_identifier="list-household-001",
            title=f"Pick up item {i}",
            notes=None,
            due_at=None,
            priority=5,
            calendar_item_identifier=f"x-coredata://household/rem-{i:04d}",
            last_modified_at=_FROZEN_NOW + timedelta(seconds=i),
        )
        for i in range(100)
    ]
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        inbound_reminders=reminders,
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    emitted = asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))
    assert emitted == 100

    cursor_calls = [
        c for c in event_log.calls if c["type"] == "adapter.cursor_advanced"
    ]
    assert len(cursor_calls) == 1
    payload = cursor_calls[0]["payload"]
    assert payload["adapter_name"] == "reminders-apple-eventkit"
    assert payload["adapter_family"] == "reminders"
    assert payload["cursor_kind"] == "list_identifier_last_modified"
    assert payload["events_since_last"] == 100
    # cursor_value is a JSON-encoded string mapping list_identifier →
    # last_modified_at; the household list's watermark is the latest
    # last_modified_at across emitted reminders.
    cursor_map = json.loads(payload["cursor_value"])
    assert "list-household-001" in cursor_map
    expected_watermark = (_FROZEN_NOW + timedelta(seconds=99)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert cursor_map["list-household-001"] == expected_watermark


def test_inbound_cursor_does_not_advance_below_milestone() -> None:
    """Anti-vision §"Quiet by default": a poll cycle that emits 3
    events does NOT emit ``adapter.cursor_advanced`` — the 100-event
    threshold has not fired and < 5 minutes have elapsed. The cursor
    is still updated in-memory; the audit event just waits for the
    milestone."""
    fixture = _load_inbound_fixture()
    reminders = _reminders_from_fixture(fixture)
    ek_store = _FakeEKEventStore(
        list_names=fixture["lists"],
        inbound_reminders=reminders,
    )
    adapter = _make_adapter(ek_store=ek_store)

    event_log = _FakeEventLog()
    asyncio.run(adapter._inbound_poll_once(ctx=_ctx(event_log)))

    cursor_calls = [
        c for c in event_log.calls if c["type"] == "adapter.cursor_advanced"
    ]
    assert cursor_calls == []
    # In-memory state still advanced — a downstream c4 lifecycle test
    # could assert this; here we just confirm the state is non-empty.
    assert "list-household-001" in adapter._inbound_cursors
    assert "list-family-2" in adapter._inbound_cursors


async def test_inbound_poll_loop_bounded_via_max_inbound_cycles() -> None:
    """The async loop wrapper is testable via ``max_inbound_cycles``.
    One bounded cycle: ``_inbound_poll`` returns after a single sleep
    rather than blocking forever, mirroring the outbound seam from c2.
    """
    fixture = _load_inbound_fixture()
    reminders = _reminders_from_fixture(fixture)
    ek_store = _FakeEKEventStore(
        list_names=fixture["lists"],
        inbound_reminders=reminders,
    )
    adapter = _make_adapter(ek_store=ek_store)
    adapter._max_inbound_cycles = 1
    # Bypass the 30s sleep by overriding the interval directly on the
    # instance — the class default would otherwise stall the test.
    adapter.INBOUND_INTERVAL_S = 0  # type: ignore[misc]

    event_log = _FakeEventLog()
    await adapter._inbound_poll(ctx=_ctx(event_log))

    synced = [c for c in event_log.calls if c["type"] == "reminder.list_item.synced"]
    assert len(synced) == 3


# ---------------------------------------------------------------------------
# 7) c4 — supervisor lifecycle (both-poll-tasks supervision)
# ---------------------------------------------------------------------------


async def test_start_spawns_both_poll_tasks_and_stop_cancels_cleanly() -> None:
    """``start()`` spawns BOTH the inbound and outbound supervised
    tasks; ``stop()`` cancels both cleanly. c2 + c3 spawned only the
    outbound task; c4 closes the bidirectional supervision surface
    per BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Polling cadence" +
    §"Outbound write cadence"."""
    fixture = _load_inbound_fixture()
    reminders = _reminders_from_fixture(fixture)
    ek_store = _FakeEKEventStore(
        list_names=fixture["lists"],
        inbound_reminders=reminders,
    )
    adapter = _make_adapter(ek_store=ek_store)
    # Bound both loops so start() returns rather than blocking forever;
    # bypass the 30s inter-cycle sleeps so the test settles in
    # milliseconds.
    adapter._max_outbound_cycles = 1
    adapter._max_inbound_cycles = 1
    adapter.OUTBOUND_INTERVAL_S = 0  # type: ignore[misc]
    adapter.INBOUND_INTERVAL_S = 0  # type: ignore[misc]

    event_log = _FakeEventLog()

    import asyncio

    start_task = asyncio.create_task(adapter.start(ctx=_ctx(event_log)))
    # Let both tasks attach + complete their bounded cycles.
    await asyncio.wait_for(start_task, timeout=2.0)

    # Both supervised tasks were created (i.e. start() spawned both,
    # not just the outbound from c2/c3).
    assert adapter._outbound_task is not None
    assert adapter._inbound_task is not None
    assert adapter._outbound_task.done()
    assert adapter._inbound_task.done()

    # adapter.authenticated emitted exactly once at start().
    auth = [c for c in event_log.calls if c["type"] == "adapter.authenticated"]
    assert len(auth) == 1

    # Inbound emissions from the bounded inbound cycle made it into the
    # log — proves the inbound task actually ran, not just attached.
    synced_inbound = [
        c
        for c in event_log.calls
        if c["type"] == "reminder.list_item.synced"
        and c["payload"]["direction"] == "inbound"
    ]
    assert len(synced_inbound) == 3

    # stop() is idempotent and transitions state to "stopped".
    await adapter.stop()
    assert adapter.status().state == "stopped"


async def test_eventkit_permission_failure_emits_adapter_error_for_supervisor() -> None:
    """A ``NoopEKEventStore`` factory (no real EventKit grant wired)
    raises ``RuntimeError`` on every operation; the adapter's defensive
    catch classifies the exception via ``_classify_eventkit_error`` and
    emits an ``adapter.error`` v1 event with the
    supervisor-expected shape. ``_classify_eventkit_error`` maps a bare
    ``RuntimeError`` to ``(severity="transient", error_kind="unknown")``;
    the supervisor's restart-with-backoff loop is what closes the
    operator-surface gap once the permission grant is real (prompt 16
    bootstrap wizard)."""
    adapter_mod = _adapter_mod
    noop_store = adapter_mod.NoopEKEventStore()

    def factory() -> Any:
        return noop_store

    adapter = AppleEventKitAdapter(
        config={
            "target_list_identifier": "list-household-001",
            "member_id": "member-001",
        },
        ek_event_store_factory=factory,
    )
    adapter._ek_store = noop_store
    # Bound the outbound loop so the test does not hang; one cycle is
    # enough to trip the NoopEKEventStore raise inside the drain path.
    adapter._max_outbound_cycles = 1
    adapter.OUTBOUND_INTERVAL_S = 0  # type: ignore[misc]
    # Pre-queue an upstream event so the drain actually invokes the
    # NoopEKEventStore (an empty queue is a clean no-op).
    adapter.enqueue_outbound(_upstream_reminder_dispatched())

    event_log = _FakeEventLog()
    await adapter._outbound_poll(ctx=_ctx(event_log))

    errors = [c for c in event_log.calls if c["type"] == "adapter.error"]
    assert len(errors) == 1
    err = errors[0]
    assert err["schema_version"] == 1
    payload = err["payload"]
    assert payload["adapter_name"] == "reminders-apple-eventkit"
    assert payload["adapter_family"] == "reminders"
    # _classify_eventkit_error maps a bare RuntimeError to transient/unknown.
    assert payload["severity"] == "transient"
    assert payload["error_kind"] == "unknown"
    assert "NoopEKEventStore" in payload["error_message"]
    # The supervisor reads status() — a transient error transitions to
    # "backoff" so the supervisor's restart window can take over.
    assert adapter.status().state == "backoff"
