"""Framework-level tests for the Plaid Cohort C retrofit.

Closes prompt 11r-b commit 3. Verifies the inheritance flip onto
:class:`InboundDataAdapter` (Cat-C, read-only ingestion) per
``docs/08-architecture-amendment-adapter-rebase.md`` §3.3, that the
``pack.yaml`` Cat-C ``capabilities`` block round-trips through
:func:`parse_adapter_manifest`, and that the 11-β validator returns NO
``advisory_pending_retrofit`` finding post-retrofit.

Plaid is the **lightest** of the three Cohort C retrofits: ``InboundDataAdapter``
has NO ``send`` (Cat-A surface), NO ``apply_write`` (Cat-B), NO
``execute_action`` (Cat-E), NO ``_on_inbound`` hook. The ABC's abstract
surface is exactly ``start`` / ``stop`` / ``status`` — the same three
that Plaid's existing class already implements. The retrofit therefore
adds no stub methods; the entire diff is inheritance + ClassVars +
manifest extension. Tests below pin that contract.

These tests live under ``tests/unit/adapters/financial/`` (framework
location, sibling to ``tests/unit/adapters/messaging/`` created by
11r-a) rather than the pack-internal ``packs/adapters/financial/plaid/
tests/`` directory because they exercise the framework's contract layer
(the 5 ABCs + the validator + the manifest parser), not the pack's
internal ingestion logic. The pack-internal tests at
``test_plaid_adapter_pack_internal.py`` continue to cover the
Plaid-specific sync flow unchanged.

``packs/`` is not a Python package under poetry's ``packages`` config,
so the PlaidAdapter class is loaded via the file-path importlib pattern
shared with the pack-internal tests and the supervisor integration test.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from adminme.lib.adapters import InboundDataAdapter
from adminme.lib.pack_registry.parser import parse_adapter_manifest
from adminme.lib.pack_registry.validator import validate_adapter_pack

REPO_ROOT = Path(__file__).resolve().parents[4]
PLAID_PACK_DIR = REPO_ROOT / "packs" / "adapters" / "financial" / "plaid"


def _load_plaid_adapter_class() -> Any:
    """Load ``PlaidAdapter`` from the pack root by file path.

    Mirrors ``test_plaid_adapter_pack_internal``'s import pattern and
    11r-a's framework test loader: ``packs/`` is not on the import path
    under poetry's package config, so file-path importlib is the
    canonical loader for pack code from framework tests.
    """
    spec = importlib.util.spec_from_file_location(
        "plaid_adapter_retrofit_under_test", PLAID_PACK_DIR / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PlaidAdapter


PlaidAdapter: Any = _load_plaid_adapter_class()


def _make_adapter() -> Any:
    """Instantiate ``PlaidAdapter`` with the no-op constructor-injection
    factories that ship as defaults per [PM-27]. No live Plaid SDK
    needed; tests only inspect class-level attributes and the simple
    Adapter Protocol surface."""
    return PlaidAdapter()


# ---------------------------------------------------------------------------
# 1) ABC inheritance — the inheritance flip closed the advisory
# ---------------------------------------------------------------------------


def test_plaid_adapter_inherits_inbound_data_adapter() -> None:
    """The retrofit declares ``class PlaidAdapter(InboundDataAdapter)``
    per memo 08 §3.3. This is the core contract change the validator's
    ``base_class_conformance`` check observes via AST walk."""
    assert issubclass(PlaidAdapter, InboundDataAdapter)


def test_plaid_adapter_inherits_exactly_one_framework_abc() -> None:
    """The 5 ABCs are mutually exclusive per [PM-34]; Plaid is Cat-C
    only. Importing the other four and asserting non-membership pins
    the constraint so a future drift (e.g. accidental dual-inheritance)
    surfaces as a test failure rather than as a validator error at
    install time."""
    from adminme.lib.adapters import (
        CommunicationAdapter,
        ExternalStateMirrorAdapter,
        OutboundActionAdapter,
        PersonalKnowledgeAdapter,
    )

    assert not issubclass(PlaidAdapter, CommunicationAdapter)
    assert not issubclass(PlaidAdapter, ExternalStateMirrorAdapter)
    assert not issubclass(PlaidAdapter, OutboundActionAdapter)
    assert not issubclass(PlaidAdapter, PersonalKnowledgeAdapter)


# ---------------------------------------------------------------------------
# 2) No write surface — Cat-C discriminator from Cat-A / B / E
# ---------------------------------------------------------------------------


def test_plaid_adapter_has_no_write_surface() -> None:
    """``InboundDataAdapter`` is the discriminator from Cat-B per memo
    05 §1.2 — an adapter that writes (``send``, ``apply_write``,
    ``execute_action``) is Cat-A / B / E, never Cat-C. The retrofit
    must not have introduced a write method; if a future change does,
    the validator's ``write_capabilities_check`` will fire but this
    test fires earlier at the class-shape layer."""
    assert not hasattr(PlaidAdapter, "send")
    assert not hasattr(PlaidAdapter, "apply_write")
    assert not hasattr(PlaidAdapter, "execute_action")
    assert not hasattr(PlaidAdapter, "_on_inbound")


# ---------------------------------------------------------------------------
# 3) Manifest extension round-trips through the parser
# ---------------------------------------------------------------------------


def test_pack_yaml_capabilities_block_parses() -> None:
    """``pack.yaml`` declares one Cat-C capability with runtime=central
    and ``money_flow.plaid_synced`` in event_families. The parser MUST
    round-trip this cleanly with no Pydantic validation errors."""
    manifest = parse_adapter_manifest(PLAID_PACK_DIR)

    assert len(manifest.capabilities) == 1
    cap = manifest.capabilities[0]
    assert cap.kind == "Cat-C"
    assert cap.runtime == "central"
    assert cap.event_families == [
        "money_flow.provider_observed",
        "money_flow.provider_removed",
        "financial.account_observed",
        "money_flow.plaid_synced",
    ]


def test_pack_yaml_extension_fields_round_trip() -> None:
    """The retrofit's extension fields populate the manifest per memo
    05 §3.1. ``sensitivity_default`` is ``"privileged"`` matching the
    triple-lock at the manifest layer. ``observation_mode_required``
    is the default ``False`` (Cat-C is read-only — no outbound surface
    to wrap). ``write_capabilities`` MUST be empty per [UT-27] (Cat-C
    has no write surface; a non-empty list would fail the validator's
    ``write_capabilities_check``)."""
    manifest = parse_adapter_manifest(PLAID_PACK_DIR)

    assert manifest.sensitivity_default == "privileged"
    assert manifest.sharing_model_discriminator == "owner_member_id"
    assert manifest.dedup_strategy == {"kind": "external_id"}
    assert manifest.observation_mode_required is False
    assert manifest.write_capabilities == []


# ---------------------------------------------------------------------------
# 4) Validator — advisory_pending_retrofit is GONE post-retrofit
# ---------------------------------------------------------------------------


def test_validator_no_pending_retrofit_advisory() -> None:
    """The 11-β validator's ``base_class_conformance`` check previously
    surfaced ``advisory_pending_retrofit`` for Plaid (no ABC, no
    capabilities). The retrofit closes both gaps; the advisory MUST be
    absent from the result. This is the goal of the entire 11r-b
    prompt — the second of three ``advisory_pending_retrofit``
    findings closed."""
    result = validate_adapter_pack(PLAID_PACK_DIR)

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
    result = validate_adapter_pack(PLAID_PACK_DIR)

    assert not result.has_errors(), (
        f"validator reports errors post-retrofit: {result.errors()}"
    )


# ---------------------------------------------------------------------------
# 5) Class attribute literals — ClassVars mirror the manifest
# ---------------------------------------------------------------------------


def test_family_class_attribute_is_financial_literal() -> None:
    """The ABC declares ``family: ClassVar[Literal["financial", "iot",
    "telemetry"]]``; the subclass narrows to the single financial
    literal. This is the discriminator used by capability-runtime
    coherence's messaging sub-rule (it does not fire for financial)."""
    assert PlaidAdapter.family == "financial"


def test_capabilities_class_attribute_mirrors_manifest() -> None:
    """:attr:`PlaidAdapter.capabilities` is the class-level mirror of
    the manifest's ``capabilities`` block per memo 05 §3.2 (runtime
    introspection without re-parsing pack.yaml). The class attribute
    MUST agree with the manifest on the Cat-C entry."""
    manifest = parse_adapter_manifest(PLAID_PACK_DIR)

    cls_caps = PlaidAdapter.capabilities
    assert isinstance(cls_caps, list)
    assert len(cls_caps) == len(manifest.capabilities) == 1
    cls_entry = cls_caps[0]
    manifest_entry = manifest.capabilities[0]
    assert cls_entry["kind"] == manifest_entry.kind
    assert cls_entry["runtime"] == manifest_entry.runtime
    assert cls_entry["event_families"] == manifest_entry.event_families


# ---------------------------------------------------------------------------
# 6) Sensitivity floor triple-lock preserved
# ---------------------------------------------------------------------------


def test_sensitivity_floor_privileged_preserved() -> None:
    """[§6 invariant 10, D11] financial adapters are privileged-floor;
    cannot be downgraded via member config. The retrofit preserves the
    class-level ``sensitivity_floor = "privileged"`` (the ABC's default
    is ``"normal"`` but subclasses may RAISE, never lower, per [§6
    invariant 10]). This matches the manifest's
    ``sensitivity_default: privileged`` — the triple-lock holds at the
    class layer, the pack.yaml layer, and config.schema.json's
    ``"const": "privileged"`` (verified by the pack-internal tests)."""
    adapter = _make_adapter()

    assert PlaidAdapter.sensitivity_floor == "privileged"
    assert adapter.sensitivity_floor == "privileged"


# ---------------------------------------------------------------------------
# 7) Backward compat — simple Adapter Protocol surface preserved
# ---------------------------------------------------------------------------


def test_simple_adapter_protocol_surface_preserved() -> None:
    """The inheritance flip preserves the simple ``Adapter`` Protocol
    surface (name / family / version / sensitivity_floor / start /
    stop / status) per memo 08 §5 ("inheritance + manifest only, not
    a rewrite"). The supervisor's adapter-loading code path is
    unchanged because the :class:`InboundDataAdapter` ABC extends the
    same Protocol shape, and ``start`` / ``stop`` / ``status`` are
    exactly the ABC's abstract surface — Plaid's existing
    implementations satisfy the contract directly."""
    from adminme.adapters.base import Adapter

    adapter = _make_adapter()

    assert isinstance(adapter, Adapter)
    assert adapter.name == "financial-plaid"
    assert adapter.family == "financial"
    assert adapter.version == "1.0.0"
    assert adapter.sensitivity_floor == "privileged"
    assert callable(adapter.start)
    assert callable(adapter.stop)
    assert callable(adapter.status)
