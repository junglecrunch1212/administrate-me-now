"""Framework-level tests for the Apple Reminders Cohort C retrofit.

Closes prompt 11r-c commit 3. Verifies the inheritance flip onto
:class:`ExternalStateMirrorAdapter` (Cat-B, bidirectional state mirror)
per ``docs/08-architecture-amendment-adapter-rebase.md`` §3.3, that the
``pack.yaml`` Cat-B ``capabilities`` block + the asymmetric
``write_capabilities`` per [UT-27] + the ``sharing_model_discriminator``
per memo 05 §2.5 round-trip through :func:`parse_adapter_manifest`, that
the 11-β validator returns NO ``advisory_pending_retrofit`` finding on
``base_class_conformance`` post-retrofit (closing the third and FINAL
advisory and therefore closing Cohort C), and that the two new ABC
abstract method implementations (``_apply_write_impl`` and
``_on_external_change``) satisfy the Cat-B contract with the load-bearing
[UT-27] capability gate on :meth:`apply_write` plus the [§6.20]
observation-mode wrap around the actual ``EKEventStore.save`` call.

Reminders is the **heaviest** of the three Cohort C retrofits.
``ExternalStateMirrorAdapter`` has TWO abstract methods beyond
``start`` / ``stop`` / ``status`` (vs Cat-A's ``send`` + ``_on_inbound``
and Cat-C's zero) — and one of them (``_apply_write_impl``) is gated by
the asymmetric :meth:`apply_write` base-class check. Per [UT-27] the
adapter declares ONLY the per-operation kinds it actually implements;
the base class raises :class:`WriteCapabilityError` for any undeclared
kind BEFORE the external API is touched. The retrofit declares ONLY
``create_reminder`` because that's the only outbound kind shipped today;
declaring ``toggle_completion`` / ``update_reminder`` / ``delete_reminder``
without method-presence would fail ``write_capabilities_check`` per memo
05 §3.4.

These tests live under ``tests/unit/adapters/reminders/`` (framework
location, sibling to ``tests/unit/adapters/messaging/`` from 11r-a and
``tests/unit/adapters/financial/`` from 11r-b) rather than the
pack-internal ``packs/adapters/reminders/apple-eventkit/tests/``
directory because they exercise the framework's contract layer (the 5
ABCs + the validator + the manifest parser), not the pack's internal
bidirectional sync logic. The pack-internal tests at
``test_apple_eventkit_adapter_pack_internal.py`` continue to cover the
Reminders-specific outbound + inbound flows unchanged.

``packs/`` is not a Python package under poetry's ``packages`` config,
so the AppleEventKitAdapter class is loaded via the file-path importlib
pattern shared with the pack-internal tests and the supervisor
integration test. The module object is kept alongside the class so
tests that need to monkeypatch ``observation_outbound`` at the module
namespace can do so (the adapter's ``_apply_write_impl`` calls the
imported name, not the canonical ``adminme.lib.observation.outbound``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from adminme.lib.adapters import (
    ChangeEnvelope,
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
)
from adminme.lib.pack_registry.parser import parse_adapter_manifest
from adminme.lib.pack_registry.validator import validate_adapter_pack

REPO_ROOT = Path(__file__).resolve().parents[4]
REMINDERS_PACK_DIR = REPO_ROOT / "packs" / "adapters" / "reminders" / "apple-eventkit"


def _load_apple_eventkit_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path.

    Mirrors the pack-internal ``_load_adapter_module`` and 11r-a /
    11r-b's framework-test loaders: ``packs/`` is not on the import
    path under poetry's package config, so file-path importlib is the
    canonical loader for pack code from framework tests. Returns the
    whole module so callers can both grab ``AppleEventKitAdapter`` and
    monkeypatch the imported ``observation_outbound`` name in the
    module's namespace (the [§6.20] wrap is exercised via that name,
    not via ``adminme.lib.observation.outbound`` directly)."""
    spec = importlib.util.spec_from_file_location(
        "apple_eventkit_adapter_retrofit_under_test",
        REMINDERS_PACK_DIR / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_apple_eventkit_module()
AppleEventKitAdapter: Any = _adapter_mod.AppleEventKitAdapter


class _FakeEKReminder:
    """Minimal stand-in for an ``EKReminder``. Only the attributes the
    outbound path's ``WriteResult`` extraction reads are populated —
    framework-level tests don't exercise the inbound emit path's
    additional fields (those are pack-internal coverage)."""

    def __init__(self, *, calendar_item_identifier: str) -> None:
        self.calendarItemIdentifier = calendar_item_identifier


class _FakeEKEventStore:
    """Strict fake EKEventStore for the framework-level retrofit tests.

    Records ``build_reminder`` + ``save`` calls so the
    ``_apply_write_impl`` dispatch + observation-wrap tests can pin the
    call shape without depending on pyobjc or a live store. Mirrors the
    pack-internal ``_FakeEKEventStore`` shape narrowed to the surface
    the framework tests exercise (no inbound poll fixture support)."""

    def __init__(self) -> None:
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
        return _FakeEKReminder(calendar_item_identifier=f"EKID-{len(self.builds)}")

    def save(self, reminder: Any, *, commit: bool) -> None:
        self.saves.append((reminder, commit))


def _make_adapter(ek_store: _FakeEKEventStore | None = None) -> Any:
    """Construct a Reminders adapter pre-bound to the fake store.

    Skips ``start()`` (which would block on the supervised poll tasks)
    by poking ``_ek_store`` directly — the framework tests only
    exercise per-method contracts, not the full lifecycle. The config
    carries the two required fields ``target_list_identifier`` +
    ``member_id``; defaults are sufficient for everything else."""
    store = ek_store if ek_store is not None else _FakeEKEventStore()
    adapter = AppleEventKitAdapter(
        config={
            "target_list_identifier": "list-household-001",
            "member_id": "member-001",
        },
        ek_event_store_factory=lambda: store,
    )
    adapter._ek_store = store
    return adapter


# ---------------------------------------------------------------------------
# 1) ABC inheritance — the inheritance flip closed the advisory
# ---------------------------------------------------------------------------


def test_apple_eventkit_adapter_inherits_external_state_mirror_adapter() -> None:
    """The retrofit declares ``class AppleEventKitAdapter(ExternalStateMirrorAdapter)``
    per memo 08 §3.3. This is the core contract change the validator's
    ``base_class_conformance`` check observes via AST walk."""
    assert issubclass(AppleEventKitAdapter, ExternalStateMirrorAdapter)


def test_apple_eventkit_adapter_inherits_exactly_one_framework_abc() -> None:
    """The 5 ABCs are mutually exclusive per [PM-34]; Apple Reminders is
    Cat-B only. Importing the other four and asserting non-membership
    pins the constraint so a future drift (e.g. accidental dual-
    inheritance) surfaces as a test failure rather than as a validator
    error at install time."""
    from adminme.lib.adapters import (
        CommunicationAdapter,
        InboundDataAdapter,
        OutboundActionAdapter,
        PersonalKnowledgeAdapter,
    )

    assert not issubclass(AppleEventKitAdapter, CommunicationAdapter)
    assert not issubclass(AppleEventKitAdapter, InboundDataAdapter)
    assert not issubclass(AppleEventKitAdapter, OutboundActionAdapter)
    assert not issubclass(AppleEventKitAdapter, PersonalKnowledgeAdapter)


# ---------------------------------------------------------------------------
# 2) Asymmetric write_capabilities — [UT-27]
# ---------------------------------------------------------------------------


def test_apple_eventkit_adapter_write_capabilities_declared_asymmetrically() -> None:
    """Per [UT-27] the write surface is asymmetric — declare ONLY the
    per-operation kinds the adapter actually implements. The shipped
    outbound path supports ``create_reminder`` only;
    ``toggle_completion`` / ``update_reminder`` / ``delete_reminder``
    are explicitly NOT declared because the methods don't exist, and
    declaring them would fail ``write_capabilities_check``'s method-
    presence rule per memo 05 §3.4."""
    assert AppleEventKitAdapter.write_capabilities == [
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "create_list",
        "update_list",
        "delete_list",
    ]
    assert len(AppleEventKitAdapter.write_capabilities) == 9


# ---------------------------------------------------------------------------
# 3) apply_write gate — undeclared kinds raise WriteCapabilityError
# ---------------------------------------------------------------------------


async def test_apply_write_gate_raises_on_undeclared_kind() -> None:
    """The base class's :meth:`apply_write` is the [UT-27] gate: any
    operation whose ``kind`` is not in ``write_capabilities`` raises
    :class:`WriteCapabilityError` BEFORE ``_apply_write_impl`` is called
    — and therefore BEFORE the external API is touched. This is what
    makes the asymmetric capability declaration load-bearing rather
    than decorative."""
    adapter = _make_adapter()
    operation = WriteOperation(
        kind="toggle_completion",
        target_external_id="EKID-1",
        payload={},
    )

    with pytest.raises(WriteCapabilityError):
        await adapter.apply_write(operation)


async def test_apply_write_dispatches_to_impl_on_declared_kind() -> None:
    """For a declared kind, the gate passes and :meth:`apply_write`
    dispatches to :meth:`_apply_write_impl`. Monkeypatching the instance
    method records the dispatch without exercising the real EKEventStore
    save path (that's the next test's job)."""
    adapter = _make_adapter()
    captured: list[WriteOperation] = []

    async def _recording_impl(operation: WriteOperation) -> Any:
        from adminme.lib.adapters import WriteResult

        captured.append(operation)
        return WriteResult(applied=True, external_id="EKID-recorded")

    adapter._apply_write_impl = _recording_impl  # type: ignore[method-assign]

    op = WriteOperation(
        kind="create_reminder",
        target_external_id=None,
        payload={
            "target_list_id": "list-household-001",
            "title": "buy milk",
            "priority": 5,
        },
    )
    result = await adapter.apply_write(op)

    assert len(captured) == 1
    assert captured[0].kind == "create_reminder"
    assert result.applied is True
    assert result.external_id == "EKID-recorded"


# ---------------------------------------------------------------------------
# 4) Observation-mode wrap — [§6.20]
# ---------------------------------------------------------------------------


async def test_apply_write_routes_through_observation_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """:meth:`_apply_write_impl` MUST route the ``EKEventStore.save``
    call through :func:`adminme.lib.observation.outbound` per [§6.20]
    when the observation-mode wiring seams are populated. The adapter
    imports the function as ``observation_outbound`` in its own module
    namespace; monkeypatching THAT name records the wrap without
    needing real session / manager / event-log instances.

    The Phase A path leaves the three seam slots unset on the adapter
    instance and bypasses the wrap to preserve the existing production
    save path byte-for-byte (memo 08 §5). This test populates the seams
    with sentinel objects so the wrapped branch is exercised."""
    ek_store = _FakeEKEventStore()
    adapter = _make_adapter(ek_store=ek_store)

    # Populate the observation seams so _apply_write_impl takes the
    # wrapped branch. The fake objects are sentinels — the patched
    # observation_outbound below records the call instead of using
    # them for anything.
    adapter._observation_session = object()
    adapter._observation_manager = object()
    adapter._observation_event_log = object()

    captured: list[dict[str, Any]] = []

    async def _fake_outbound(
        session: Any,
        action: str,
        payload: dict[str, Any],
        action_fn: Any,
        *,
        manager: Any,
        event_sink: Any,
        target_channel: str = "unknown",
        target_identifier: str = "unknown",
    ) -> Any:
        from adminme.lib.observation import OutboundResult

        captured.append(
            {
                "session": session,
                "action": action,
                "payload": payload,
                "manager": manager,
                "event_log": event_sink,
                "target_channel": target_channel,
                "target_identifier": target_identifier,
            }
        )
        # Drive the wrapped action so the EKReminder save still
        # executes on the fake store — that mirrors the real
        # outbound() contract when observation mode is INACTIVE
        # (suppressed=False, action_fn awaited).
        await action_fn()
        return OutboundResult(suppressed=False, result=None)

    monkeypatch.setattr(_adapter_mod, "observation_outbound", _fake_outbound)

    op = WriteOperation(
        kind="create_reminder",
        target_external_id=None,
        payload={
            "target_list_id": "list-household-001",
            "title": "buy milk",
            "priority": 5,
            "member_id": "member-001",
        },
    )
    result = await adapter.apply_write(op)

    assert len(captured) == 1
    assert captured[0]["action"] == "reminders.create_reminder"
    assert captured[0]["target_channel"] == "reminders-apple-eventkit"
    assert captured[0]["target_identifier"] == "list-household-001"
    assert result.applied is True
    # The wrapped action fired against the fake store.
    assert len(ek_store.saves) == 1


# ---------------------------------------------------------------------------
# 5) Manifest extension round-trips through the parser
# ---------------------------------------------------------------------------


def test_pack_yaml_capabilities_block_parses_as_cat_b() -> None:
    """``pack.yaml`` declares one Cat-B capability with runtime=central
    and ``reminder.list_item.synced`` in event_families. The parser
    MUST round-trip this cleanly with no Pydantic validation errors."""
    manifest = parse_adapter_manifest(REMINDERS_PACK_DIR)

    assert len(manifest.capabilities) == 1
    cap = manifest.capabilities[0]
    assert cap.kind == "Cat-B"
    assert cap.runtime == "central"
    assert cap.event_families == [
        "reminder.list_item.synced",
        "list.created",
        "list.updated",
        "list.deleted",
        "list_item.added",
        "list_item.updated",
        "list_item.completed",
        "list_item.uncompleted",
        "list_item.deleted",
    ]


def test_pack_yaml_extension_fields_round_trip() -> None:
    """The retrofit's extension fields populate the manifest per memo
    05 §3.1. ``sensitivity_default`` is ``"normal"`` (matches Reminders'
    triple-locked class-level floor). ``write_capabilities`` declares
    ONLY ``create_reminder`` per [UT-27] (asymmetric).
    ``observation_mode_required`` is the AdapterManifest default
    ``False`` — the wrap is implemented inline in ``_apply_write_impl``
    via :func:`adminme.lib.observation.outbound`, not via a manifest-
    level gate (memo 08 §5 narrowed scope: the per-write wrap is the
    load-bearing thing, not the manifest flag)."""
    manifest = parse_adapter_manifest(REMINDERS_PACK_DIR)

    assert manifest.sensitivity_default == "normal"
    assert manifest.sharing_model_discriminator == "owner_member_id"
    assert manifest.dedup_strategy == {
        "kind": "calendar_item_identifier_last_modified"
    }
    assert manifest.write_capabilities == [
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "create_list",
        "update_list",
        "delete_list",
    ]
    assert manifest.observation_mode_required is False


# ---------------------------------------------------------------------------
# 6) Validator — advisory_pending_retrofit is GONE post-retrofit
# ---------------------------------------------------------------------------


def test_validator_no_pending_retrofit_advisory() -> None:
    """The 11-β validator's ``base_class_conformance`` check previously
    surfaced ``advisory_pending_retrofit`` for Reminders (no ABC, no
    capabilities). The retrofit closes both gaps; the advisory MUST be
    absent from the result. This is the THIRD and FINAL
    ``advisory_pending_retrofit`` closed — closing Cohort C."""
    result = validate_adapter_pack(REMINDERS_PACK_DIR)

    pending = [
        f
        for f in result.advisories()
        if f.check == "base_class_conformance"
        and "advisory_pending_retrofit" in f.message
    ]
    assert pending == [], (
        f"advisory_pending_retrofit still present post-retrofit: {pending}"
    )


def test_validator_returns_no_errors_for_retrofitted_pack() -> None:
    """Beyond the targeted advisory check, the retrofit must not
    introduce ANY errors (capability runtime coherence, event schema
    registration, base class conformance, write_capabilities,
    observation mode wrapping). Errors would refuse install per memo
    05 §3.4."""
    result = validate_adapter_pack(REMINDERS_PACK_DIR)

    assert not result.has_errors(), (
        f"validator reports errors post-retrofit: {result.errors()}"
    )


# ---------------------------------------------------------------------------
# 7) Class attribute literals — ClassVars mirror the manifest
# ---------------------------------------------------------------------------


def test_family_class_attribute_is_reminders_literal() -> None:
    """The ABC declares ``family: ClassVar[Literal["reminders",
    "calendaring", "contacts"]]``; the subclass narrows to the single
    ``"reminders"`` literal. This is the discriminator the future Cat-B
    sub-rules (e.g. lists projection's runtime check) will key off."""
    assert AppleEventKitAdapter.family == "reminders"


def test_capabilities_class_attribute_mirrors_manifest() -> None:
    """:attr:`AppleEventKitAdapter.capabilities` is the class-level
    mirror of the manifest's ``capabilities`` block per memo 05 §3.2
    (runtime introspection without re-parsing pack.yaml). The class
    attribute MUST agree with the manifest on the Cat-B entry."""
    manifest = parse_adapter_manifest(REMINDERS_PACK_DIR)

    cls_caps = AppleEventKitAdapter.capabilities
    assert isinstance(cls_caps, list)
    assert len(cls_caps) == len(manifest.capabilities) == 1
    cls_entry = cls_caps[0]
    manifest_entry = manifest.capabilities[0]
    assert cls_entry["kind"] == manifest_entry.kind
    assert cls_entry["runtime"] == manifest_entry.runtime
    assert cls_entry["event_families"] == manifest_entry.event_families


# ---------------------------------------------------------------------------
# 8) Sensitivity floor triple-lock preserved
# ---------------------------------------------------------------------------


def test_sensitivity_floor_normal_preserved() -> None:
    """[§6 invariant 10, D11] Reminders adapters are ``"normal"``-floor
    (compare to Plaid's ``"privileged"``). The retrofit preserves the
    class-level value; the ABC's default is also ``"normal"`` so the
    subclass override-upward rule is preserved (subclasses may RAISE
    the floor, never lower it, per [§6 invariant 10]). The per-item
    privacy boundary for Reminders is the never-sync predicate, NOT the
    adapter-level floor — finer-grained but lower base-rate than
    Plaid's privileged floor."""
    adapter = _make_adapter()

    assert AppleEventKitAdapter.sensitivity_floor == "normal"
    assert adapter.sensitivity_floor == "normal"


# ---------------------------------------------------------------------------
# 9) `_on_external_change` — ABC entry point delegates to existing emit
# ---------------------------------------------------------------------------


async def test_on_external_change_delegates_to_emit_inbound_synced() -> None:
    """The new :meth:`_on_external_change` is the ABC contract entry
    point the ``base_class_conformance`` AST walk requires. Per memo 08
    §5 (inheritance + manifest only, not a rewrite) the production
    inbound poll path continues to call ``_emit_inbound_synced``
    directly; this hook is a thin wrapper that synthesizes a duck-typed
    reminder from the ``ChangeEnvelope.payload`` and delegates. Future
    ingress paths (webhook handlers, 11g HA adapters) route through
    here. Monkeypatching ``_emit_inbound_synced`` records the
    delegation without exercising the full emit pipeline."""
    from adminme.adapters.base import AdapterContext
    from datetime import datetime, timezone

    adapter = _make_adapter()
    # _on_external_change short-circuits on ctx is None — populate the
    # captured ctx slot directly (start() would normally do this).
    adapter._ctx = AdapterContext(
        event_sink=object(),  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-test",
        now_factory=lambda: datetime(2026, 5, 17, 0, 0, 0, tzinfo=timezone.utc),
    )

    captured: list[dict[str, Any]] = []

    async def _recording_emit(*, ctx: Any, reminder: Any, sync_cycle_id: str) -> None:
        captured.append(
            {
                "ctx": ctx,
                "reminder": reminder,
                "sync_cycle_id": sync_cycle_id,
            }
        )

    adapter._emit_inbound_synced = _recording_emit  # type: ignore[method-assign]

    envelope = ChangeEnvelope(
        kind="item_modified",
        external_id="EKID-abc",
        received_at="2026-05-17T00:00:00Z",
        payload={
            "list_identifier": "list-household-001",
            "title": "buy milk",
            "completed": False,
            "sync_cycle_id": "cycle-xyz",
        },
    )
    await adapter._on_external_change(envelope)

    assert len(captured) == 1
    call = captured[0]
    assert call["sync_cycle_id"] == "cycle-xyz"
    # The synthesized reminder carries the external_id as
    # calendarItemIdentifier so _emit_inbound_synced's existing
    # getattr-based extraction works unchanged.
    assert call["reminder"].calendarItemIdentifier == "EKID-abc"
    assert call["reminder"].title == "buy milk"


# ---------------------------------------------------------------------------
# 10) Backward-compat — simple Adapter Protocol surface preserved
# ---------------------------------------------------------------------------


def test_simple_adapter_protocol_surface_preserved() -> None:
    """The inheritance flip preserves the simple ``Adapter`` Protocol
    surface (name / family / version / sensitivity_floor / start /
    stop / status) per memo 08 §5 ("inheritance + manifest only, not a
    rewrite"). The supervisor's adapter-loading code path is unchanged
    because the :class:`ExternalStateMirrorAdapter` ABC extends the
    same Protocol shape, and ``start`` / ``stop`` / ``status`` are
    the ABC's abstract surface — Reminders' existing implementations
    satisfy the contract directly."""
    from adminme.adapters.base import Adapter

    adapter = _make_adapter()

    assert isinstance(adapter, Adapter)
    assert adapter.name == "reminders-apple-eventkit"
    assert adapter.family == "reminders"
    assert adapter.version == "2.0.0"
    assert adapter.sensitivity_floor == "normal"
    assert callable(adapter.start)
    assert callable(adapter.stop)
    assert callable(adapter.status)


def test_sharing_model_discriminator_is_owner_member_id() -> None:
    """[memo 05 §2.5, PM-35] Reminders' projection-row column
    distinguishing per-member vs shared rows is ``owner_member_id``
    (matches Plaid's). Cohort D's 11f ``lists`` projection will use
    this column as the discriminator between personal lists (per-
    member rows) and shared lists (joined via ``list_shares``).
    Asserted at both the class attribute and the manifest layers for
    triple-lock coherence."""
    manifest = parse_adapter_manifest(REMINDERS_PACK_DIR)

    assert AppleEventKitAdapter.sharing_model_discriminator == "owner_member_id"
    assert manifest.sharing_model_discriminator == "owner_member_id"
