"""Cross-module retrofit-contract integration tests for prompt 11r-c
**plus** ``list_item.*@v1`` dual-emit integration tests for prompt
11b-i (extended in c4 — 2 new tests below the original three).

Sits alongside ``test_adapter_reminders_supervisor.py`` (which exercises
the full supervisor lifecycle against a fake ``EKEventStore`` + is gated
on ``requires_live_services`` per [PM-27] live-services discipline).
These retrofit tests run **without** ``requires_live_services`` because
they only exercise the framework-layer seam the retrofit introduces:

- the supervisor's loader path produces an :class:`AppleEventKitAdapter`
  class that is a subclass of :class:`ExternalStateMirrorAdapter`
  (Cat-B);
- the loaded class declares the asymmetric ``write_capabilities``
  ``["create_reminder"]`` per [UT-27], and the base class's
  :meth:`apply_write` gate raises :class:`WriteCapabilityError` for any
  undeclared kind BEFORE :meth:`_apply_write_impl` (or the external
  ``EKEventStore.save``) is reached;
- the on-disk pack passes the 11-β validator with NO
  ``advisory_pending_retrofit`` finding on ``base_class_conformance``.

This is the integration analogue to the framework-level tests at
``tests/unit/adapters/reminders/test_apple_eventkit_retrofit.py`` and
the pack-internal tests at
``packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``.
The three together pin the retrofit contract at the unit + pack +
integration seams, mirroring the 11r-a (Gmail) and 11r-b (Plaid)
retrofit-sibling patterns adapted for Cat-B's bidirectional shape:
Cat-A's pattern lives in ``test_adapter_supervisor_gmail.py``'s "6)
Cohort C retrofit" section; Cat-C's lives in
``test_adapter_plaid_supervisor_retrofit.py``. Cat-B is the heaviest of
the three (TWO abstract methods + the [UT-27] gate + the [§6.20] wrap),
so the gate-rejection assertion in test (2) is the load-bearing
addition vs the Cat-A/C siblings.

The supervisor's full lifecycle test for Reminders stays in
``test_adapter_reminders_supervisor.py`` under the
``requires_live_services`` gate; this file is the lightweight
retrofit-contract sibling that runs in default CI.

``packs/adapters/reminders/apple-eventkit`` includes a hyphen in the
final directory segment and is therefore not a valid Python package
name; the adapter class is loaded via the file-path importlib pattern
shared with the framework retrofit tests and the pack-internal tests
(11r-b's Plaid sibling could use a direct ``from packs...`` import
because ``plaid`` has no hyphen; 11r-a's Gmail sibling and this one
cannot).
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.schemas.lists import (
    ListItemAddedV1,
    ListItemCompletedV1,
)
from adminme.events.schemas.reminders import ReminderListItemSyncedV1
from adminme.lib.adapters import (
    CommunicationAdapter,
    ExternalStateMirrorAdapter,
    InboundDataAdapter,
    OutboundActionAdapter,
    PersonalKnowledgeAdapter,
    WriteCapabilityError,
    WriteOperation,
)
from adminme.lib.pack_registry.validator import validate_adapter_pack

REPO_ROOT = Path(__file__).resolve().parents[2]
REMINDERS_PACK_ROOT = REPO_ROOT / "packs" / "adapters" / "reminders" / "apple-eventkit"


def _load_apple_eventkit_module() -> Any:
    """Load ``adapter.py`` from the Reminders pack root by file path.

    Mirrors the framework retrofit test's loader at
    ``tests/unit/adapters/reminders/test_apple_eventkit_retrofit.py``
    and the pack-internal loader at
    ``packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_adapter_pack_internal.py``.
    The supervisor's adapter loader uses the same file-path importlib
    pattern at runtime because ``apple-eventkit``'s hyphen forbids a
    direct ``from packs.adapters.reminders.apple-eventkit.adapter``
    import."""
    spec = importlib.util.spec_from_file_location(
        "apple_eventkit_adapter_retrofit_integration_under_test",
        REMINDERS_PACK_ROOT / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_apple_eventkit_module()
AppleEventKitAdapter: Any = _adapter_mod.AppleEventKitAdapter


def _make_adapter() -> Any:
    """Construct an :class:`AppleEventKitAdapter` skipping ``start()``.

    The supervisor's full lifecycle test exercises ``start`` / ``stop``;
    these integration tests only pin the retrofit contract at the
    loader + ABC + validator seam. The two required config fields are
    supplied; ``ek_event_store_factory`` returns a sentinel because no
    test here reaches the actual external save path (the [UT-27] gate
    rejects the undeclared kind BEFORE the factory is called)."""
    return AppleEventKitAdapter(
        config={
            "target_list_identifier": "list-household-001",
            "member_id": "member-001",
        },
        ek_event_store_factory=lambda: object(),
    )


def test_retrofit_supervisor_loaded_apple_eventkit_inherits_external_state_mirror_adapter() -> (
    None
):
    """The supervisor's adapter-loading code path (file-path importlib
    — same path as the existing supervisor integration test) yields a
    class that inherits from :class:`ExternalStateMirrorAdapter`. Per
    memo 08 §3.3, this is the contract change the retrofit ships — the
    validator's ``base_class_conformance`` check observes this via AST
    walk against the same file. Mutual-exclusion across the 5 ABCs is
    pinned here at the integration seam per [PM-34] — the framework
    test covers it at the unit seam, this is the second layer of
    defense-in-depth."""
    assert issubclass(AppleEventKitAdapter, ExternalStateMirrorAdapter)
    # [PM-34] mutual-exclusion across the 5 ABCs.
    assert not issubclass(AppleEventKitAdapter, CommunicationAdapter)
    assert not issubclass(AppleEventKitAdapter, InboundDataAdapter)
    assert not issubclass(AppleEventKitAdapter, OutboundActionAdapter)
    assert not issubclass(AppleEventKitAdapter, PersonalKnowledgeAdapter)


def test_retrofit_apple_eventkit_write_capabilities_gate_rejects_undeclared_kinds_through_loader() -> (
    None
):
    """The [UT-27] gate on the base-class :meth:`apply_write` is what
    makes the asymmetric ``write_capabilities`` declaration
    load-bearing rather than decorative. The shipped outbound surface
    is ``create_reminder`` ONLY; declaring ``toggle_completion`` /
    ``update_reminder`` / ``delete_reminder`` would fail
    ``write_capabilities_check`` per memo 05 §3.4 (method-presence
    rule). The framework test pins the gate at the class-shape layer;
    this integration test pins it on the instance the supervisor would
    actually run, closing the loop between the contract
    (``write_capabilities`` ClassVar + base-class gate) and the
    supervisor's runtime invariant. Cat-B's discriminator from Cat-A
    (``send``), Cat-C (no write surface), Cat-E (``execute_action``)
    is the per-operation gate observed here."""
    adapter = _make_adapter()
    assert AppleEventKitAdapter.write_capabilities == ["create_reminder"]
    assert hasattr(adapter, "apply_write")
    assert callable(adapter.apply_write)

    op = WriteOperation(
        kind="toggle_completion",
        target_external_id="EKID-1",
        payload={},
    )
    with pytest.raises(WriteCapabilityError):
        asyncio.run(adapter.apply_write(op))


def test_retrofit_validator_clean_on_supervisor_loaded_pack() -> None:
    """End-to-end retrofit contract at the integration seam: the
    on-disk pack at ``packs/adapters/reminders/apple-eventkit/`` — the
    same path the supervisor's loader reads — passes the 11-β
    validator with NO ``advisory_pending_retrofit`` finding on
    ``base_class_conformance`` and NO errors overall. Closes the goal
    of the entire 11r-c prompt at the integration seam (the third and
    FINAL of three Cohort C retrofits; 11r-a closed Gmail, 11r-b
    closed Plaid, this closes Reminders — and therefore Cohort C)."""
    result = validate_adapter_pack(REMINDERS_PACK_ROOT)

    pending = [
        f
        for f in result.advisories()
        if f.check == "base_class_conformance"
        and "advisory_pending_retrofit" in f.message
    ]
    assert pending == [], (
        f"advisory_pending_retrofit still present post-retrofit: {pending}"
    )
    assert not result.has_errors(), (
        f"validator reports errors post-retrofit: {result.errors()}"
    )


# ===========================================================================
# Prompt 11b-i c4 — ``list_item.*@v1`` dual-emit integration tests
# ===========================================================================
#
# Two end-to-end tests below verify that the dual-emit landed in c2 fires
# correctly through the supervisor-loaded adapter class on BOTH the
# outbound write path and the inbound poll-driven emit path. Sits
# alongside the framework tests at
# ``tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py``
# (which exercise ``_write_one_outbound`` / ``_emit_inbound_synced``
# directly) and the pack-internal tests at
# ``packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_dual_emit_pack_internal.py``
# (which pin the cache-state → kind-derivation contract). The integration
# layer's load-bearing addition is that the adapter CLASS is loaded by
# the same file-path importlib pattern the supervisor uses at runtime,
# so any future loader-side regression (e.g., the schemas module failing
# to register at adapter-load time, or the dual-emit imports breaking)
# fails this file first.


_INTEGRATION_FROZEN_NOW = datetime(2026, 5, 17, 19, 0, 0, tzinfo=timezone.utc)


class _IntegrationEventLog:
    """Captures typed asynchronous event-sink writes for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"


class _IntegrationEKReminder:
    """Stand-in for ``EKReminder`` for the dual-emit integration tests."""

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
        self.last_modified_at = last_modified_at or _INTEGRATION_FROZEN_NOW
        self.completed = completed


class _IntegrationEKEventStore:
    """Fake ``EKEventStore`` for the dual-emit integration path."""

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
    ) -> _IntegrationEKReminder:
        self._cid_counter += 1
        return _IntegrationEKReminder(
            list_identifier=list_identifier,
            title=title,
            notes=notes,
            due_at=due_at,
            priority=priority,
            calendar_item_identifier=f"EKID-integ-{self._cid_counter}",
        )

    def save(self, reminder: Any, *, commit: bool) -> None:
        self.saves.append((reminder, commit))

    def list_name_for_identifier(self, list_identifier: str) -> str:
        return self._list_names.get(list_identifier, list_identifier)


def _make_dual_emit_adapter(ek_store: _IntegrationEKEventStore) -> Any:
    """Construct a supervisor-loadable adapter wired with the fake
    ``EKEventStore`` — no ``start()`` invocation required (the dual-emit
    integration tests pin the emit contract directly on the
    ``_write_one_outbound`` + ``_emit_inbound_synced`` surface)."""
    adapter = AppleEventKitAdapter(
        config={
            "target_list_identifier": "list-household-001",
            "member_id": "member-integ",
        },
        ek_event_store_factory=lambda: ek_store,
        member_role_resolver=lambda _mid: "adult",
    )
    adapter._ek_store = ek_store
    return adapter


def _make_dual_emit_ctx(event_log: _IntegrationEventLog) -> AdapterContext:
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-integ-dual-emit",
        now_factory=lambda: _INTEGRATION_FROZEN_NOW,
    )


def test_supervisor_loaded_adapter_emits_both_event_families_on_outbound() -> None:
    """The supervisor-loaded :class:`AppleEventKitAdapter` (loaded via
    the same file-path importlib pattern the supervisor uses at runtime)
    dual-emits ``reminder.list_item.synced@v1`` AND ``list_item.added@v1``
    on every successful outbound write. Both events carry the SAME
    ``causation_id`` (the upstream event's id) and ``correlation_id``
    (minted by ``ctx.correlation_id_factory()``) per [§D8] — round-trip
    traceability spans both event families at the supervisor-loaded
    seam. Payload validates byte-for-byte against the canonical v1
    schemas; any drift between the adapter's emit shape and the on-main
    schema (``extra="forbid"`` enforced on both) would fail this test."""
    ek_store = _IntegrationEKEventStore(
        list_names={"list-household-001": "Integration Household"},
    )
    adapter = _make_dual_emit_adapter(ek_store)
    event_log = _IntegrationEventLog()

    upstream = {
        "id": "evt-integ-dispatch-1",
        "type": "reminder.dispatched",
        "payload": {
            "commitment_id": "cmt-integ-1",
            "owner_member_id": "member-integ",
            "recipient_party_id": "party-integ",
            "composed_text": "Drop off Maya at soccer practice",
            "channel": "imessage",
            "due_at": _INTEGRATION_FROZEN_NOW.isoformat(),
            "delivered": False,
            "validation_failed": False,
        },
    }

    written = asyncio.run(
        adapter._write_one_outbound(
            ctx=_make_dual_emit_ctx(event_log),
            upstream=upstream,
            sync_cycle_id="cycle-integ-outbound-1",
        )
    )
    assert written is True

    synced_calls = [
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    ]
    added_calls = [c for c in event_log.calls if c["type"] == "list_item.added"]
    assert len(synced_calls) == 1, (
        f"expected exactly one reminder.list_item.synced emit, got {len(synced_calls)}"
    )
    assert len(added_calls) == 1, (
        f"expected exactly one list_item.added emit alongside the legacy, "
        f"got {len(added_calls)}"
    )

    # Causation + correlation shared across both halves of the pair.
    assert synced_calls[0]["causation_id"] == "evt-integ-dispatch-1"
    assert added_calls[0]["causation_id"] == "evt-integ-dispatch-1"
    assert synced_calls[0]["correlation_id"] == "corr-integ-dual-emit"
    assert added_calls[0]["correlation_id"] == "corr-integ-dual-emit"

    # Payloads validate against the canonical v1 schemas — the
    # supervisor-loaded class wires through the same registry the on-main
    # schemas live in, so any drift fails here.
    synced_instance = ReminderListItemSyncedV1.model_validate(
        synced_calls[0]["payload"]
    )
    assert synced_instance.direction == "outbound"
    assert synced_instance.list_identifier == "list-household-001"

    added_instance = ListItemAddedV1.model_validate(added_calls[0]["payload"])
    assert added_instance.member_id == "member-integ"
    assert added_instance.list_external_id == "list-household-001"
    assert added_instance.list_name == "Integration Household"
    assert added_instance.completed is False
    assert added_instance.sync_cycle_id == "cycle-integ-outbound-1"
    # ``reminder.dispatched`` maps to the EventKit ``high`` ordinal (1).
    assert added_instance.priority == 1


def test_supervisor_loaded_adapter_emits_both_event_families_on_inbound() -> None:
    """The supervisor-loaded :class:`AppleEventKitAdapter` dual-emits
    ``reminder.list_item.synced@v1`` (direction="inbound") AND a
    ``list_item.*@v1`` event whose kind is derived from session cache
    state on every inbound poll-driven emit. Two sequential observations
    of the same id exercise the kind-derivation contract end-to-end at
    the supervisor-loaded seam: first observation → ``list_item.added``;
    completion-flip ``False → True`` → ``list_item.completed``.

    Both halves of each pair share the SAME ``sync_cycle_id`` for that
    poll cycle. The new emit's ``causation_id`` is left unset — inbound
    has no upstream AdministrateMe event (iCloud is the source) — and
    the legacy emit preserves the same unset-causation_id contract from
    pre-11b-i, so a consumer correlating inbound emits by
    ``causation_id`` sees the same behavior on both families during the
    migration window."""
    ek_store = _IntegrationEKEventStore(
        list_names={"list-household-001": "Integration Household"},
    )
    adapter = _make_dual_emit_adapter(ek_store)
    event_log = _IntegrationEventLog()
    ctx = _make_dual_emit_ctx(event_log)

    # ---- Observation #1: first-time appearance.
    reminder_initial = _IntegrationEKReminder(
        list_identifier="list-household-001",
        title="Renew car registration",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-integ-inbound-1",
    )
    reminder_initial.completed = False
    asyncio.run(
        adapter._emit_inbound_synced(
            ctx=ctx,
            reminder=reminder_initial,
            sync_cycle_id="cycle-integ-inbound-A",
        )
    )

    # ---- Observation #2: same id, completion flips False → True.
    reminder_completed = _IntegrationEKReminder(
        list_identifier="list-household-001",
        title="Renew car registration",
        notes=None,
        due_at=None,
        priority=5,
        calendar_item_identifier="EKID-integ-inbound-1",
    )
    reminder_completed.completed = True
    asyncio.run(
        adapter._emit_inbound_synced(
            ctx=ctx,
            reminder=reminder_completed,
            sync_cycle_id="cycle-integ-inbound-B",
        )
    )

    # Two legacy + two new emits; legacy + new interleave in append order.
    legacy_calls = [
        c for c in event_log.calls if c["type"] == "reminder.list_item.synced"
    ]
    new_family_calls = [
        c for c in event_log.calls if c["type"].startswith("list_item.")
    ]
    assert len(legacy_calls) == 2
    assert len(new_family_calls) == 2
    assert [c["type"] for c in new_family_calls] == [
        "list_item.added",
        "list_item.completed",
    ]

    # Same sync_cycle_id on the pair for each cycle.
    assert (
        legacy_calls[0]["payload"]["sync_cycle_id"]
        == new_family_calls[0]["payload"]["sync_cycle_id"]
        == "cycle-integ-inbound-A"
    )
    assert (
        legacy_calls[1]["payload"]["sync_cycle_id"]
        == new_family_calls[1]["payload"]["sync_cycle_id"]
        == "cycle-integ-inbound-B"
    )

    # Inbound: causation_id unset on BOTH halves (iCloud is the source —
    # no upstream AdministrateMe event to chain).
    for call in legacy_calls + new_family_calls:
        assert call["causation_id"] is None

    # Branch 1: first-observation payload validates against ListItemAddedV1.
    added_instance = ListItemAddedV1.model_validate(new_family_calls[0]["payload"])
    assert added_instance.item_external_id == "EKID-integ-inbound-1"
    assert added_instance.list_external_id == "list-household-001"
    assert added_instance.list_name == "Integration Household"
    assert added_instance.completed is False

    # Branch 2: completion-flip payload validates against
    # ListItemCompletedV1. The schema's ``Literal[True]`` on ``completed``
    # would reject a False value here — verifying the supervisor-loaded
    # class wires the kind derivation correctly.
    completed_instance = ListItemCompletedV1.model_validate(
        new_family_calls[1]["payload"]
    )
    assert completed_instance.completed is True
    assert completed_instance.item_external_id == "EKID-integ-inbound-1"
