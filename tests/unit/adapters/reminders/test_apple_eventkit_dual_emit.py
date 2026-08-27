"""Framework-level tests for the Apple Reminders ``list_item.*@v1``
dual-emit (prompt 11b-i c2 implementation).

Closes prompt 11b-i §7 c3 deliverable 2 (framework dual-emit tests).
Verifies that ``AppleEventKitAdapter._write_one_outbound`` emits BOTH
``reminder.list_item.synced@v1`` (direction="outbound") AND
``list_item.added@v1`` for every successful outbound write — sharing
``causation_id`` (the upstream event's id per [§D8]) and
``correlation_id`` (ctx.correlation_id_factory() per [§D8]) so
round-trip traceability spans both event families. F-3 (HARD): when
``observation_outbound`` returns ``suppressed=True``, NEITHER emit
fires — observation mode silences BOTH legacy + dual or NEITHER, never
one but not the other. On the inbound side
:meth:`_emit_inbound_synced` derives the new event kind from session
cache state captured BEFORE the emit: first observation →
``list_item.added``; completion-flip ``false → true`` →
``list_item.completed``; other field change → ``list_item.updated``
(with ``field_updates``). Tests pin the kind-derivation contract per
§1 item 3 and assert payload-fidelity by ``model_validate``-ing the
emitted payloads against the canonical v1 schemas from
``adminme/events/schemas/lists.py``.

These tests live under ``tests/unit/adapters/reminders/`` (framework
location, sibling to ``test_apple_eventkit_retrofit.py`` from 11r-c c3)
because they exercise the canonical event-schema layer + the adapter's
emit contract, not the pack's internal bidirectional sync logic. The
pack-internal coverage (c4 deliverable) at
``test_apple_eventkit_dual_emit_pack_internal.py`` complements these
framework tests with the pack-level cache-state assertions.

F-4 (HARD/standing): basename ``test_apple_eventkit_dual_emit.py`` is
unique against ``test_apple_eventkit_retrofit.py``,
``test_apple_eventkit_adapter_pack_internal.py``,
``test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``,
``test_apple_eventkit_send_pack_internal.py`` — pytest collection from
the repo root does not collide.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.schemas.lists import (
    ListItemAddedV1,
    ListItemCompletedV1,
    ListItemUpdatedV1,
)
from adminme.lib.adapters import (
    WriteOperation,  # noqa: F401  — surfaces import path for downstream
)

REPO_ROOT = Path(__file__).resolve().parents[4]
REMINDERS_PACK_DIR = REPO_ROOT / "packs" / "adapters" / "reminders" / "apple-eventkit"


def _load_apple_eventkit_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path. Mirrors
    ``test_apple_eventkit_retrofit.py``'s loader so monkeypatching
    ``observation_outbound`` in the module namespace exercises the
    [§6.20] wrap path that the F-3 dual-emit-suppress test depends on."""
    spec = importlib.util.spec_from_file_location(
        "apple_eventkit_adapter_dual_emit_under_test",
        REMINDERS_PACK_DIR / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_apple_eventkit_module()
AppleEventKitAdapter: Any = _adapter_mod.AppleEventKitAdapter


_FROZEN_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


class _FakeEventLog:
    """Captures synchronous append calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"

    async def emit(self, envelope: Any) -> Any:
        event_id = await self.write(envelope)
        return SimpleNamespace(ok=True, event_id=event_id, error=None)


def _make_ctx(event_log: _FakeEventLog) -> AdapterContext:
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={
            "deployment_target": "bridge",
            "_bridge_member_id": "member-001",
            "_bridge_sink": event_log,
        },
        correlation_id_factory=lambda: "corr-dual-emit",
        now_factory=lambda: _FROZEN_NOW,
    )


def _emit_inbound_synced(adapter: Any, **kwargs: Any) -> None:
    asyncio.run(adapter._emit_inbound_synced(**kwargs))


class _FakeEKReminder:
    """Stand-in for ``EKReminder``. Carries the union of fields the
    outbound build path consumes (set by ``build_reminder``) plus the
    inbound emit path consumes (``last_modified_at`` / ``completed``)."""

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
    """Strict fake EKEventStore for dual-emit framework tests."""

    def __init__(
        self,
        *,
        list_names: dict[str, str] | None = None,
        mint_calendar_item_identifiers: list[str] | None = None,
    ) -> None:
        self._list_names = dict(list_names or {})
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

    def list_name_for_identifier(self, list_identifier: str) -> str:
        return self._list_names.get(list_identifier, list_identifier)


def _make_adapter(
    *,
    ek_store: _FakeEKEventStore | None = None,
    member_role: str = "adult",
) -> Any:
    store = (
        ek_store
        if ek_store is not None
        else _FakeEKEventStore(
            list_names={"list-household-001": "AdministrateMe Household"}
        )
    )
    adapter = AppleEventKitAdapter(
        config={
            "target_list_identifier": "list-household-001",
            "member_id": "member-001",
            "provider_account_id": "provider-account:reminders-test",
        },
        ek_event_store_factory=lambda: store,
        member_role_resolver=lambda _mid: member_role,
    )
    adapter._ek_store = store
    adapter._bridge_sink = None
    return adapter


def _reminder_dispatched(
    *,
    event_id: str = "evt-dispatch-1",
    composed_text: str = "Pick up Maya at 5pm",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "reminder.dispatched",
        "payload": {
            "commitment_id": "cmt-1",
            "owner_member_id": "member-001",
            "recipient_party_id": "party-1",
            "composed_text": composed_text,
            "channel": "imessage",
            "due_at": _FROZEN_NOW.isoformat(),
            "delivered": False,
            "validation_failed": False,
        },
    }


def _drive_outbound(
    adapter: Any,
    ctx: AdapterContext,
    upstream: dict[str, Any] | None = None,
    sync_cycle_id: str = "cycle-test-001",
) -> bool:
    return asyncio.run(
        adapter._write_one_outbound(
            ctx=ctx,
            upstream=upstream if upstream is not None else _reminder_dispatched(),
            sync_cycle_id=sync_cycle_id,
        )
    )


# ===========================================================================
# Outbound dual-emit
# ===========================================================================


def test_outbound_emits_list_item_added_alongside_legacy_synced() -> None:
    """Every successful outbound write emits ONE
    ``reminder.list_item.synced@v1`` AND ONE ``list_item.added@v1`` —
    the dual-emit landing per §1 item 2. Both target the same upstream
    event (one write → two emits)."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        mint_calendar_item_identifiers=["EKID-out-1"],
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    written = _drive_outbound(adapter, _make_ctx(event_log))

    assert written is True
    synced_calls = [
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    ]
    added_calls = [c for c in event_log.calls if c["type"] == "list_item.added"]
    assert len(synced_calls) == 1
    assert len(added_calls) == 1


def test_outbound_dual_emit_payload_validates_against_list_item_added_v1() -> None:
    """The emitted ``list_item.added@v1`` payload round-trips through
    ``ListItemAddedV1.model_validate`` — payload field-mapping fidelity
    is verified against the canonical v1 schema, not the adapter's own
    field set, so consumer-side ``registry.get('list_item.added', 1)``
    will accept the event."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        mint_calendar_item_identifiers=["EKID-out-payload"],
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    _drive_outbound(adapter, _make_ctx(event_log))

    added_call = next(c for c in event_log.calls if c["type"] == "list_item.added")
    payload = added_call["payload"]
    instance = ListItemAddedV1.model_validate(payload)
    assert instance.member_id == "member-001"
    assert instance.list_external_id == "list-household-001"
    assert instance.item_external_id == "EKID-out-payload"
    assert instance.list_name == "AdministrateMe Household"
    assert instance.completed is False
    assert instance.priority == 1  # reminder.dispatched maps to high
    assert instance.sync_cycle_id == "cycle-test-001"


def test_outbound_dual_emit_same_causation_id_as_legacy() -> None:
    """[§D8] Both emits carry the SAME ``causation_id`` (the upstream
    event's id). Round-trip traceability spans the event-family pair —
    a consumer correlating by ``causation_id`` sees both halves of the
    dual-emit pair."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        mint_calendar_item_identifiers=["EKID-out-caus"],
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    _drive_outbound(
        adapter,
        _make_ctx(event_log),
        upstream=_reminder_dispatched(event_id="evt-dispatch-42"),
    )

    synced_call = next(
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    )
    added_call = next(c for c in event_log.calls if c["type"] == "list_item.added")
    assert synced_call["causation_id"] == "evt-dispatch-42"
    assert added_call["causation_id"] == "evt-dispatch-42"
    assert synced_call["causation_id"] == added_call["causation_id"]


def test_outbound_dual_emit_same_correlation_id_as_legacy() -> None:
    """[§D8] Both emits share the SAME ``correlation_id`` — minted by
    ``ctx.correlation_id_factory()``. Cross-event-family correlation
    depends on this invariant."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        mint_calendar_item_identifiers=["EKID-out-corr"],
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    _drive_outbound(adapter, _make_ctx(event_log))

    synced_call = next(
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    )
    added_call = next(c for c in event_log.calls if c["type"] == "list_item.added")
    assert synced_call["correlation_id"] == "corr-dual-emit"
    assert added_call["correlation_id"] == "corr-dual-emit"


def test_outbound_dual_emit_legacy_appended_before_new() -> None:
    """Emission order: legacy ``reminder.list_item.synced@v1`` lands
    BEFORE the new ``list_item.added@v1`` — the c2 implementation
    appends them in that sequence. Pinning the order via index keeps
    downstream consumers' migration-window invariants stable."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        mint_calendar_item_identifiers=["EKID-out-order"],
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    _drive_outbound(adapter, _make_ctx(event_log))

    types_in_order = [c["type"] for c in event_log.calls]
    assert types_in_order == [
        "reminder.list_item.synced",
        "list_item.added",
    ]


def test_outbound_dual_emit_both_payloads_share_sync_cycle_id() -> None:
    """Both halves of the dual-emit pair carry the SAME
    ``sync_cycle_id`` (the drain batch's id) — downstream consumers can
    join on this field to reconcile the pair."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        mint_calendar_item_identifiers=["EKID-out-cycle"],
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    _drive_outbound(adapter, _make_ctx(event_log), sync_cycle_id="batch-A1B2")

    for call in event_log.calls:
        assert call["payload"]["sync_cycle_id"] == "batch-A1B2"


# ---------------------------------------------------------------------------
# F-3 (HARD): observation-suppressed path skips BOTH emits or NEITHER
# ---------------------------------------------------------------------------


def test_dual_emit_both_skipped_when_outbound_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-3 (HARD): when ``observation_outbound`` returns
    ``OutboundResult(suppressed=True)``, ``_apply_write_impl`` returns
    ``WriteResult(applied=False, suppressed=True)`` and
    ``_write_one_outbound`` early-returns before EITHER emit fires.
    Observation mode silences BOTH legacy + dual or NEITHER, never one
    but not the other — the invariant the parent draft §1 item 2 pins."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    # Populate the three observation seam slots so _apply_write_impl
    # takes the wrapped branch.
    adapter._observation_session = object()
    adapter._observation_manager = object()
    adapter._observation_event_log = object()

    async def _suppressing_outbound(
        _session: Any,
        _action: str,
        _payload: dict[str, Any],
        _action_fn: Any,
        **_kwargs: Any,
    ) -> Any:
        from adminme.lib.observation import OutboundResult

        # IMPORTANTLY: do NOT call the action_fn — observation suppress
        # is "did not perform the side effect." The save MUST NOT run.
        return OutboundResult(suppressed=True, result=None)

    monkeypatch.setattr(_adapter_mod, "observation_outbound", _suppressing_outbound)

    event_log = _FakeEventLog()
    written = _drive_outbound(adapter, _make_ctx(event_log))

    # No write applied → returns False from _write_one_outbound.
    assert written is False
    # NEITHER emit fired — the consistency invariant.
    assert [
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    ] == []
    assert [c for c in event_log.calls if c["type"] == "list_item.added"] == []
    # The wrapped save did NOT execute against the fake store.
    assert ek_store.saves == []


def test_dual_emit_both_skipped_when_translate_upstream_returns_none() -> None:
    """An unmappable upstream payload (no ``composed_text``) is dropped
    BEFORE the write — neither emit fires. Symmetric with the F-3
    invariant: the legacy and new emits live or die together."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    malformed = {
        "id": "evt-x",
        "type": "reminder.dispatched",
        "payload": {"owner_member_id": "member-001"},  # missing composed_text
    }
    written = _drive_outbound(adapter, _make_ctx(event_log), upstream=malformed)

    assert written is False
    assert event_log.calls == []


# ===========================================================================
# Inbound dual-emit — kind derivation from session cache state
# ===========================================================================


def test_inbound_first_observation_emits_list_item_added() -> None:
    """First observation of a ``remind_id`` (not in ``_seen_inbound``)
    → ``list_item.added@v1`` per §1 item 3. The legacy
    ``reminder.list_item.synced@v1`` is also emitted with
    ``direction="inbound"`` per the existing pattern."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    reminder = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Pick up milk",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-inbound-fresh",
        last_modified_at=_FROZEN_NOW,
        completed=False,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=reminder,
        sync_cycle_id="cycle-inbound-A",
    )

    types_emitted = [c["type"] for c in event_log.calls]
    assert types_emitted == [
        "reminder.list_item.synced",
        "list_item.added",
    ]
    added_call = event_log.calls[1]
    assert "field_updates" not in added_call["payload"]
    ListItemAddedV1.model_validate(added_call["payload"])


def test_inbound_completion_flip_emits_list_item_completed() -> None:
    """``completed: false → true`` transition on a re-seen reminder →
    ``list_item.completed@v1`` per §1 item 3. The schema's
    ``completed: Literal[True]`` is the load-bearing self-describing
    constraint — Pydantic ``model_validate`` enforces it."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    base_id = "EKID-inbound-flip"
    rem_open = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Pay rent",
        notes=None,
        due_at=None,
        priority=1,
        calendar_item_identifier=base_id,
        last_modified_at=_FROZEN_NOW,
        completed=False,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem_open,
        sync_cycle_id="cycle-inbound-B1",
    )
    # User checks off the reminder; second observation has completed=True.
    rem_done = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Pay rent",
        notes=None,
        due_at=None,
        priority=1,
        calendar_item_identifier=base_id,
        last_modified_at=_FROZEN_NOW + timedelta(minutes=5),
        completed=True,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem_done,
        sync_cycle_id="cycle-inbound-B2",
    )

    completed_calls = [c for c in event_log.calls if c["type"] == "list_item.completed"]
    assert len(completed_calls) == 1
    instance = ListItemCompletedV1.model_validate(completed_calls[0]["payload"])
    assert instance.completed is True
    assert instance.item_external_id == base_id


def test_inbound_other_field_change_emits_list_item_updated() -> None:
    """A re-seen reminder with a title change (no completion flip) →
    ``list_item.updated@v1`` per §1 item 3, with ``field_updates``
    naming the changed fields. Mirrors the
    ``CalendarEventUpdatedV1`` precedent."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    base_id = "EKID-inbound-edit"
    rem_v1 = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Buy milk",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier=base_id,
        last_modified_at=_FROZEN_NOW,
        completed=False,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem_v1,
        sync_cycle_id="cycle-edit-1",
    )
    rem_v2 = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Buy milk and bread",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier=base_id,
        last_modified_at=_FROZEN_NOW + timedelta(minutes=5),
        completed=False,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem_v2,
        sync_cycle_id="cycle-edit-2",
    )

    updated_calls = [c for c in event_log.calls if c["type"] == "list_item.updated"]
    assert len(updated_calls) == 1
    payload = updated_calls[0]["payload"]
    assert payload["title"] == "Buy milk and bread"
    instance = ListItemUpdatedV1.model_validate(payload)
    assert "title" in instance.field_updates
    assert instance.field_updates["title"] == "Buy milk and bread"


def test_inbound_list_item_updated_field_updates_excludes_unchanged_fields() -> None:
    """``field_updates`` carries ONLY the changed fields — unchanged
    fields are not present. A title-only edit produces a single-key
    ``field_updates`` (plus ``last_modified_at`` which always changes
    when the user edits)."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    base_id = "EKID-inbound-narrow"
    common_kwargs: dict[str, Any] = dict(
        list_identifier="list-household-001",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier=base_id,
        completed=False,
    )
    rem_v1 = _FakeEKReminder(
        title="Original",
        last_modified_at=_FROZEN_NOW,
        **common_kwargs,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem_v1,
        sync_cycle_id="cycle-1",
    )
    rem_v2 = _FakeEKReminder(
        title="Renamed",
        last_modified_at=_FROZEN_NOW + timedelta(minutes=2),
        **common_kwargs,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem_v2,
        sync_cycle_id="cycle-2",
    )

    updated_call = next(c for c in event_log.calls if c["type"] == "list_item.updated")
    field_updates = updated_call["payload"]["field_updates"]
    # `title` changed, `last_modified_at` changed; nothing else.
    assert set(field_updates.keys()) == {"title", "last_modified_at"}
    assert field_updates["title"] == "Renamed"
    # Unchanged fields must NOT appear.
    assert "notes" not in field_updates
    assert "priority" not in field_updates
    assert "completed" not in field_updates
    assert "due_at" not in field_updates


def test_inbound_dual_emit_legacy_and_new_share_correlation_id() -> None:
    """[§D8] Both inbound emits share the SAME ``correlation_id`` per
    poll cycle. Cross-event-family inbound correlation depends on this
    invariant (consumers subscribing to either family pivot on the
    shared correlation_id to reconcile the pair)."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()
    ctx = _make_ctx(event_log)

    rem = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Inbound corr test",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-corr-pair",
        last_modified_at=_FROZEN_NOW,
        completed=False,
    )
    _emit_inbound_synced(adapter, ctx=ctx, reminder=rem, sync_cycle_id="cyc-corr")

    synced_call = next(
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    )
    added_call = next(c for c in event_log.calls if c["type"] == "list_item.added")
    assert synced_call["correlation_id"] == added_call["correlation_id"]
    assert added_call["correlation_id"] == "corr-dual-emit"


def test_inbound_dual_emit_sync_cycle_id_matches_legacy() -> None:
    """The new emit's ``sync_cycle_id`` matches the legacy emit's per
    inbound poll cycle — both join on the same drain batch."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    rem = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Inbound sync_cycle test",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-cycle-pair",
        last_modified_at=_FROZEN_NOW,
        completed=False,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem,
        sync_cycle_id="cyc-shared-9",
    )

    synced_call = next(
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    )
    added_call = next(c for c in event_log.calls if c["type"] == "list_item.added")
    assert synced_call["payload"]["sync_cycle_id"] == "cyc-shared-9"
    assert added_call["payload"]["sync_cycle_id"] == "cyc-shared-9"


def test_inbound_dual_emit_member_id_propagates_from_adapter_config() -> None:
    """The new emit carries the adapter-configured ``member_id`` — same
    source as the legacy emit. iCloud does not carry AdministrateMe's
    household-member assignment; the adapter instance's configured
    member_id is the source of truth."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    rem = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Member id propagation test",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-member",
        last_modified_at=_FROZEN_NOW,
        completed=False,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem,
        sync_cycle_id="cyc-m",
    )

    added_call = next(c for c in event_log.calls if c["type"] == "list_item.added")
    assert added_call["payload"]["member_id"] == "member-001"


@pytest.mark.parametrize("ek_priority", [0, 1, 5, 9])
def test_inbound_dual_emit_priority_propagates_through_ordinal_scale(
    ek_priority: int,
) -> None:
    """Inbound ``priority`` flows through to the new emit unchanged for
    every EventKit ordinal in ``{0, 1, 5, 9}`` (F-6). ``ListItemAddedV1``
    accepts via ``Literal[0, 1, 5, 9]``."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    rem = _FakeEKReminder(
        list_identifier="list-household-001",
        title=f"Priority {ek_priority}",
        notes=None,
        due_at=None,
        priority=ek_priority,
        calendar_item_identifier=f"EKID-prio-{ek_priority}",
        last_modified_at=_FROZEN_NOW,
        completed=False,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem,
        sync_cycle_id="cyc-prio",
    )

    added_call = next(c for c in event_log.calls if c["type"] == "list_item.added")
    assert added_call["payload"]["priority"] == ek_priority
    # Schema-side validation pin: the canonical Literal accepts.
    ListItemAddedV1.model_validate(added_call["payload"])


def test_inbound_completion_flip_followed_by_field_change_emits_updated() -> None:
    """After a completion flip → ``list_item.completed`` cycle, a
    subsequent field change on the now-completed item does NOT re-emit
    ``list_item.completed`` (the flip already happened). The kind
    derivation falls through to ``list_item.updated``."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()
    ctx = _make_ctx(event_log)

    base_id = "EKID-then-edit"
    rem_open = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Original",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier=base_id,
        last_modified_at=_FROZEN_NOW,
        completed=False,
    )
    rem_done = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Original",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier=base_id,
        last_modified_at=_FROZEN_NOW + timedelta(minutes=1),
        completed=True,
    )
    rem_edited = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Original — edited",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier=base_id,
        last_modified_at=_FROZEN_NOW + timedelta(minutes=2),
        completed=True,
    )
    for rem, cycle in (
        (rem_open, "cyc-1"),
        (rem_done, "cyc-2"),
        (rem_edited, "cyc-3"),
    ):
        _emit_inbound_synced(adapter, ctx=ctx, reminder=rem, sync_cycle_id=cycle)

    kinds = [c["type"] for c in event_log.calls if c["type"].startswith("list_item.")]
    assert kinds == ["list_item.added", "list_item.completed", "list_item.updated"]


def test_inbound_dual_emit_legacy_payload_unchanged_from_pre_dual_shape() -> None:
    """The legacy ``reminder.list_item.synced@v1`` emit's payload is
    BYTE-FOR-BYTE unchanged by the dual-emit landing per §6 (47
    production consumers preserve byte-for-byte). The new emit lands
    alongside without modifying the legacy payload's field set or
    values."""
    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"}
    )
    adapter = _make_adapter(ek_store=ek_store)
    event_log = _FakeEventLog()

    rem = _FakeEKReminder(
        list_identifier="list-household-001",
        title="Legacy unchanged",
        notes="ignore",
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-legacy-stable",
        last_modified_at=_FROZEN_NOW,
        completed=False,
    )
    _emit_inbound_synced(
        adapter,
        ctx=_make_ctx(event_log),
        reminder=rem,
        sync_cycle_id="cyc-legacy",
    )

    synced_call = next(
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    )
    payload = synced_call["payload"]
    # Legacy shape contract — these are the exact keys ReminderListItemSyncedV1
    # declares. The dual-emit landing MUST NOT add or remove keys here.
    expected_keys = {
        "member_id",
        "remind_id",
        "list_identifier",
        "list_name",
        "title",
        "notes",
        "due_at",
        "priority",
        "completed",
        "last_modified_at",
        "direction",
        "sync_cycle_id",
    }
    assert set(payload.keys()) == expected_keys
    assert payload["direction"] == "inbound"
