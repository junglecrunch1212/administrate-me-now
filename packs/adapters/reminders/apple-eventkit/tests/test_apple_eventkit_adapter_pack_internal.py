"""Apple Reminders adapter pack-internal tests.

C2 ships:
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
     OUTBOUND boundary — symmetric with the inbound application landing
     in c3.
  7. Never-sync filter blocks outbound writes for ``"child"`` /
     ``"ambient"`` owning member roles per BUILD.md APPLE REMINDERS
     BIDIRECTIONAL §"Never-sync rules".

C3 adds (inbound poll + dedupe + never-sync filter on iCloud reads):
  8. Inbound poll fetches reminders; never-sync blocks "Work" lists
     symmetrically; dedupe-by-``remind_id``; cursor-milestone emission;
     dedupe permissive fallback when the projection is absent.

F-3 (10c-iii, reinforced by 11a + 11b): test basename is unique
(``test_apple_eventkit_adapter_pack_internal.py``) so collection from
the repo root does not collide with sibling pack-internal tests.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from adminme.adapters.base import Adapter, AdapterContext

PACK_ROOT = Path(__file__).resolve().parents[1]


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

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _ctx(event_log: _FakeEventLog) -> AdapterContext:
    """Build an AdapterContext with deterministic clock + correlation
    id. The Reminders adapter reads ``ctx.config`` indirectly via
    ``self._config`` so this ctx's ``config`` is left empty."""
    return AdapterContext(
        event_log=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-test",
        now_factory=lambda: _FROZEN_NOW,
    )


class _FakeEKReminder:
    """Stand-in for ``EKReminder``. ``calendarItemIdentifier`` is the
    field the outbound emit pulls back for round-trip traceability."""

    def __init__(
        self,
        *,
        list_identifier: str,
        title: str,
        notes: str | None,
        due_at: Any,
        priority: int,
        calendar_item_identifier: str,
    ) -> None:
        self.list_identifier = list_identifier
        self.title = title
        self.notes = notes
        self.due_at = due_at
        self.priority = priority
        self.calendarItemIdentifier = calendar_item_identifier


class _FakeEKEventStore:
    """Strict fake EKEventStore. Records every ``build_reminder`` +
    ``save`` invocation for assertion; lets the test pre-configure the
    ``list_name_for_identifier`` lookup so the never-sync filter sees
    the intended list name."""

    def __init__(
        self,
        *,
        list_names: dict[str, str],
        mint_calendar_item_identifiers: list[str] | None = None,
    ) -> None:
        self._list_names = dict(list_names)
        self._mint_queue = list(mint_calendar_item_identifiers or [])
        self.builds: list[dict[str, Any]] = []
        self.saves: list[tuple[Any, bool]] = []

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
            self._mint_queue.pop(0)
            if self._mint_queue
            else f"EKID-{len(self.builds)}"
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

    def fetch_reminders(
        self, *, list_identifier: str | None = None
    ) -> list[Any]:  # pragma: no cover
        return []

    def list_name_for_identifier(self, list_identifier: str) -> str:
        return self._list_names.get(list_identifier, list_identifier)


def _make_adapter(
    *,
    ek_store: _FakeEKEventStore,
    config_overrides: dict[str, Any] | None = None,
    member_role: str = "adult",
) -> Any:
    """Construct an adapter pre-bound to the fake store. Tests skip
    ``start()`` (which would block on the outbound task forever) by
    poking ``_ek_store`` directly."""
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

    adapter = AppleEventKitAdapter(
        config=config,
        ek_event_store_factory=factory,
        member_role_resolver=role_resolver,
    )
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
    assert manifest["triggers"]["schedule"] == "*/1 * * * *"

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
    written = adapter._drain_outbound_once(ctx=_ctx(event_log))

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
    written = adapter._drain_outbound_once(ctx=_ctx(event_log))

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
    adapter._drain_outbound_once(ctx=_ctx(event_log))

    synced = [
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    ]
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
    written = adapter._drain_outbound_once(ctx=_ctx(event_log))

    assert written == 0
    assert ek_store.builds == []
    assert ek_store.saves == []
    synced = [
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    ]
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
    written = adapter._drain_outbound_once(ctx=_ctx(event_log))

    assert written == 0
    assert ek_store.builds == []
    assert ek_store.saves == []


# ---------------------------------------------------------------------------
# 6) inbound stub raises NotImplementedError (replaced in c3)
# ---------------------------------------------------------------------------


async def test_inbound_poll_stub_raises_not_implemented() -> None:
    """c2's class layout fixes ``_inbound_poll`` as a method that
    raises ``NotImplementedError``. c3 replaces this with the real
    polling body; until then the stub is the placeholder."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    with pytest.raises(NotImplementedError, match="commit 3"):
        await adapter._inbound_poll(ctx=_ctx(_FakeEventLog()))
