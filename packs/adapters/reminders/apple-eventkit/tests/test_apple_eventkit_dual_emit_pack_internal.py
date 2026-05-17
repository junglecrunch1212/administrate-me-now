"""Pack-internal tests for the ``list_item.*@v1`` dual-emit landed in
prompt 11b-i (cohort D step 2-α).

Sits alongside ``test_apple_eventkit_adapter_pack_internal.py`` (the
pre-retrofit pack-internal suite) and
``test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``
(the 11r-c retrofit's pack-internal suite). This file's three tests
close prompt 11b-i §7 c4 deliverable 1 — the pack-side guard that
the dual-emit landed in c2 stays wired to the canonical schemas in
``adminme/events/schemas/lists.py`` (registered in c1) without any
pack-side drift, and that the inbound kind derivation reads the
session caches in the order the c2 implementation pins.

Three load-bearing assertions:

1. ``test_dual_emit_imports_match_lists_schemas`` — ``adapter.py``
   imports ``ListItemAddedV1`` / ``ListItemUpdatedV1`` /
   ``ListItemCompletedV1`` from ``adminme.events.schemas.lists``. The
   import surface guards against accidental schema-source drift: if a
   future refactor moves the schemas out of ``schemas/lists.py`` the
   import would 500 at module-load and this test fails first. ALSO
   asserts ``ListItemDeletedV1`` is registered (the c1 module-load
   side effect) even though the adapter does not yet emit it — the
   schema must be subscribable on main for downstream consumers per
   the migration-window contract.

2. ``test_outbound_dual_emit_payloads_validate_against_on_main_schemas``
   — both ``reminder.list_item.synced@v1`` (the legacy emit, schema
   in ``adminme/events/schemas/reminders.py``) and ``list_item.added@v1``
   (the new emit, schema in ``adminme/events/schemas/lists.py``) emitted
   from one successful outbound write validate byte-for-byte via
   ``model_validate``. ``extra="forbid"`` on both schemas means any
   stray field would fail the model — this is the pack-side guard
   against accidental schema drift; the framework test at
   ``tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py``
   covers the framework-layer fidelity at the schema-source layer.

3. ``test_inbound_dual_emit_kinds_derive_from_seen_inbound_cache_state``
   — the kind-derivation contract per §1 item 3: first observation
   (id absent from ``_seen_inbound``) → ``list_item.added``;
   completion-flip ``False → True`` (prior snapshot has
   ``completed=False``, current observation has ``completed=True``) →
   ``list_item.completed``; other field change → ``list_item.updated``
   (with ``field_updates``). Pinned at the pack layer because the
   ``_seen_inbound`` + ``_inbound_snapshots`` cache pair is pack-private
   plumbing — the framework test (which loads the module via importlib)
   covers the same contract from the schema-fidelity angle, this test
   pins the cache-state-as-input invariant directly.

F-4 (HARD/standing) — basename ``test_apple_eventkit_dual_emit_pack_internal.py``
is unique against ``test_apple_eventkit_adapter_pack_internal.py``,
``test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``,
and ``test_apple_eventkit_send_pack_internal.py`` (the canonical 11a
Gmail pack-internal does not share basenames here — Gmail's pack tests
live under ``packs/adapters/messaging/gmail/tests/``). Pytest
collection from the repo root does not collide.

``packs/adapters/reminders/apple-eventkit`` includes a hyphen in the
final directory segment and is therefore not a valid Python package
name; the adapter is loaded via the file-path importlib pattern
shared with ``test_apple_eventkit_adapter_pack_internal._load_adapter_module``
and the framework dual-emit tests at
``tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adminme.events.registry import registry
from adminme.events.schemas.lists import (
    ListItemAddedV1,
    ListItemCompletedV1,
    ListItemDeletedV1,
    ListItemUpdatedV1,
)
from adminme.events.schemas.reminders import ReminderListItemSyncedV1
from adminme.adapters.base import AdapterContext

PACK_ROOT = Path(__file__).resolve().parents[1]


def _load_adapter_module() -> Any:
    """Load ``adapter.py`` via importlib — the apple-eventkit pack
    directory's hyphen forbids a direct ``from packs...`` import."""
    spec = importlib.util.spec_from_file_location(
        "apple_eventkit_adapter_dual_emit_pack_internal_under_test",
        PACK_ROOT / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_adapter_module()
AppleEventKitAdapter: Any = _adapter_mod.AppleEventKitAdapter

_FROZEN_NOW = datetime(2026, 5, 17, 18, 0, 0, tzinfo=timezone.utc)


class _FakeEventLog:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _make_ctx(event_log: _FakeEventLog) -> AdapterContext:
    return AdapterContext(
        event_log=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-pack-internal",
        now_factory=lambda: _FROZEN_NOW,
    )


class _FakeEKReminder:
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
        self.last_modified_at = _FROZEN_NOW
        self.completed = False


class _FakeEKEventStore:
    def __init__(self, *, list_names: dict[str, str]) -> None:
        self._list_names = dict(list_names)
        self._cid_counter = 0
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
        self._cid_counter += 1
        return _FakeEKReminder(
            list_identifier=list_identifier,
            title=title,
            notes=notes,
            due_at=due_at,
            priority=priority,
            calendar_item_identifier=f"EKID-pack-{self._cid_counter}",
        )

    def save(self, reminder: Any, *, commit: bool) -> None:
        self.saves.append((reminder, commit))

    def list_name_for_identifier(self, list_identifier: str) -> str:
        return self._list_names.get(list_identifier, list_identifier)


def _make_adapter(ek_store: _FakeEKEventStore) -> Any:
    adapter = AppleEventKitAdapter(
        config={
            "target_list_identifier": "list-household-001",
            "member_id": "member-pack",
        },
        ek_event_store_factory=lambda: ek_store,
        member_role_resolver=lambda _mid: "adult",
    )
    adapter._ek_store = ek_store
    return adapter


def _upstream_reminder_dispatched(event_id: str = "evt-pack-1") -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "reminder.dispatched",
        "payload": {
            "commitment_id": "cmt-pack-1",
            "owner_member_id": "member-pack",
            "recipient_party_id": "party-pack",
            "composed_text": "Buy milk on the way home",
            "channel": "imessage",
            "due_at": _FROZEN_NOW.isoformat(),
            "delivered": False,
            "validation_failed": False,
        },
    }


def test_dual_emit_imports_match_lists_schemas() -> None:
    """The dual-emit lives on the canonical ``adminme.events.schemas.lists``
    module per c1. The pack-side import surface guards against accidental
    schema-source drift: if a future refactor moves the schemas out of
    ``schemas/lists.py`` the import in ``adapter.py`` would fail at
    module-load and this assertion catches it first.

    ALSO asserts that ``ListItemDeletedV1`` is registered at v1 even
    though the adapter does not yet emit it (inbound-deletion derivation
    is deferred per prompt 11b-i §6 / §1 item 3) — the schema must be
    subscribable on main for downstream consumers during the migration
    window, NOT only when the emit lands."""
    # Pack-side imports point at the canonical module.
    assert ListItemAddedV1.__module__ == "adminme.events.schemas.lists"
    assert ListItemUpdatedV1.__module__ == "adminme.events.schemas.lists"
    assert ListItemCompletedV1.__module__ == "adminme.events.schemas.lists"
    assert ListItemDeletedV1.__module__ == "adminme.events.schemas.lists"

    # Adapter module surface — the three emit-active types are imported
    # directly; ListItemDeletedV1 is registered via the schema module's
    # module-load side effect (c1).
    assert getattr(_adapter_mod, "ListItemAddedV1") is ListItemAddedV1
    assert getattr(_adapter_mod, "ListItemUpdatedV1") is ListItemUpdatedV1
    assert getattr(_adapter_mod, "ListItemCompletedV1") is ListItemCompletedV1

    # Registry round-trip for all four — including ``list_item.deleted``,
    # which the adapter does not yet emit but which downstream consumers
    # must be able to subscribe to.
    assert registry.get("list_item.added", 1) is ListItemAddedV1
    assert registry.get("list_item.updated", 1) is ListItemUpdatedV1
    assert registry.get("list_item.completed", 1) is ListItemCompletedV1
    assert registry.get("list_item.deleted", 1) is ListItemDeletedV1

    # The legacy emit's schema is UNCHANGED on main per [D7] — the
    # dual-emit preserves the existing 47-consumer surface byte-for-byte.
    assert registry.get("reminder.list_item.synced", 1) is ReminderListItemSyncedV1


def test_outbound_dual_emit_payloads_validate_against_on_main_schemas() -> None:
    """Both halves of an outbound dual-emit pair validate against their
    canonical v1 schemas — ``reminder.list_item.synced@v1`` against
    ``ReminderListItemSyncedV1`` (in ``schemas/reminders.py``) and
    ``list_item.added@v1`` against ``ListItemAddedV1`` (in
    ``schemas/lists.py``). Both schemas declare ``extra="forbid"`` so any
    stray field on either payload would fail validation here. This is
    the pack-side guard against accidental schema drift; the framework
    test at ``tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py``
    covers the framework-layer fidelity from the schema-source angle."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "Household"},
    )
    adapter = _make_adapter(ek_store)
    event_log = _FakeEventLog()

    written = asyncio.run(
        adapter._write_one_outbound(
            ctx=_make_ctx(event_log),
            upstream=_upstream_reminder_dispatched(),
            sync_cycle_id="cycle-pack-1",
        )
    )
    assert written is True

    synced_calls = [
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    ]
    added_calls = [
        c for c in event_log.calls if c["type"] == "list_item.added"
    ]
    assert len(synced_calls) == 1
    assert len(added_calls) == 1

    # Legacy emit validates byte-for-byte against the on-main schema —
    # extras would fail extra="forbid".
    synced_instance = ReminderListItemSyncedV1.model_validate(
        synced_calls[0]["payload"]
    )
    assert synced_instance.member_id == "member-pack"
    assert synced_instance.direction == "outbound"
    assert synced_instance.list_identifier == "list-household-001"

    # New emit validates byte-for-byte against the on-main schema.
    added_instance = ListItemAddedV1.model_validate(added_calls[0]["payload"])
    assert added_instance.member_id == "member-pack"
    assert added_instance.list_external_id == "list-household-001"
    assert added_instance.list_name == "Household"
    assert added_instance.completed is False
    assert added_instance.sync_cycle_id == "cycle-pack-1"
    # ``reminder.dispatched`` maps to the EventKit ``high`` ordinal (1).
    assert added_instance.priority == 1


def test_inbound_dual_emit_kinds_derive_from_seen_inbound_cache_state() -> None:
    """The kind-derivation contract per §1 item 3:

      - first observation (id absent from ``_seen_inbound``) →
        ``list_item.added``
      - completion-flip ``False → True`` (prior snapshot has
        ``completed=False``, current observation has ``completed=True``)
        → ``list_item.completed``
      - any other field change (with a prior snapshot present and the
        completion-flip predicate NOT satisfied) → ``list_item.updated``
        (with ``field_updates`` carrying the changed fields)

    The pack-internal layer pins this contract directly against the
    ``_seen_inbound`` + ``_inbound_snapshots`` cache pair — pack-private
    plumbing that the framework test covers indirectly via the schema-
    fidelity assertions. Three sequential inbound emits exercise all
    three branches in one test."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "Household"},
    )
    adapter = _make_adapter(ek_store)
    event_log = _FakeEventLog()
    ctx = _make_ctx(event_log)

    # ---- Observation #1: first-time appearance of EKID-pack-inbound-1.
    reminder_initial = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Pick up dry cleaning",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-pack-inbound-1",
    )
    reminder_initial.completed = False
    adapter._emit_inbound_synced(
        ctx=ctx, reminder=reminder_initial, sync_cycle_id="cycle-inbound-A"
    )

    # ---- Observation #2: same id, completion flips False → True.
    reminder_completed = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Pick up dry cleaning",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-pack-inbound-1",
    )
    reminder_completed.completed = True
    adapter._emit_inbound_synced(
        ctx=ctx, reminder=reminder_completed, sync_cycle_id="cycle-inbound-B"
    )

    # ---- Observation #3: same id, title changes; completion stays True.
    reminder_retitled = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Pick up dry cleaning AND laundry",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-pack-inbound-1",
    )
    reminder_retitled.completed = True
    adapter._emit_inbound_synced(
        ctx=ctx, reminder=reminder_retitled, sync_cycle_id="cycle-inbound-C"
    )

    # Three legacy + three new emits, in the appended order.
    new_family_calls = [
        c for c in event_log.calls if c["type"].startswith("list_item.")
    ]
    assert [c["type"] for c in new_family_calls] == [
        "list_item.added",
        "list_item.completed",
        "list_item.updated",
    ]

    # Branch 1: first-observation → ``list_item.added``. Validates
    # byte-for-byte against ``ListItemAddedV1``.
    added_instance = ListItemAddedV1.model_validate(new_family_calls[0]["payload"])
    assert added_instance.item_external_id == "EKID-pack-inbound-1"
    assert added_instance.completed is False

    # Branch 2: completion-flip → ``list_item.completed``. The schema's
    # ``Literal[True]`` on ``completed`` would reject a False value here.
    completed_instance = ListItemCompletedV1.model_validate(
        new_family_calls[1]["payload"]
    )
    assert completed_instance.completed is True
    assert completed_instance.item_external_id == "EKID-pack-inbound-1"

    # Branch 3: other field change → ``list_item.updated`` with
    # ``field_updates`` populated. Only ``title`` (and ``last_modified_at``
    # / ``sync_cycle_id`` are NOT in field_updates because last_modified
    # IS in the diff field set per c2 — completed stayed True; title
    # changed) — verify ``title`` is the changed field.
    updated_instance = ListItemUpdatedV1.model_validate(
        new_family_calls[2]["payload"]
    )
    assert "title" in updated_instance.field_updates
    assert updated_instance.field_updates["title"] == "Pick up dry cleaning AND laundry"
    # ``completed`` was unchanged in this transition (True → True), so
    # it MUST NOT appear in field_updates per the c2 diff semantics.
    assert "completed" not in updated_instance.field_updates
