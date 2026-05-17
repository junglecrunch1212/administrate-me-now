"""Cross-module retrofit-contract integration tests for prompt 11r-b.

Sits alongside ``test_adapter_plaid_supervisor.py`` (which exercises the
full supervisor lifecycle against a fake Plaid SDK + is gated on
``requires_live_services`` per [PM-27] live-services discipline). These
retrofit tests run **without** ``requires_live_services`` because they
only exercise the framework-layer seam the retrofit introduces:

- the supervisor's loader path produces a :class:`PlaidAdapter`
  instance that is a subclass of :class:`InboundDataAdapter` (Cat-C);
- the loaded class has NO write surface (the Cat-C discriminator from
  Cat-A / B / E per memo 05 §1.2);
- the on-disk pack passes the 11-β validator with NO
  ``advisory_pending_retrofit`` finding on ``base_class_conformance``.

This is the integration analogue to the framework-level tests at
``tests/unit/adapters/financial/test_plaid_retrofit.py`` and the
pack-internal tests at
``packs/adapters/financial/plaid/tests/test_plaid_inbound_data_adapter_retrofit_pack_internal.py``.
The three together pin the retrofit contract at the unit + pack +
integration seams. Mirrors the 11r-a pattern in
``tests/integration/test_adapter_supervisor_gmail.py`` (the "6) Cohort
C retrofit" section) but adapted for Cat-C's no-write-surface shape.

The supervisor's full lifecycle test for Plaid stays in
``test_adapter_plaid_supervisor.py`` under the ``requires_live_services``
gate; this file is the lightweight retrofit-contract sibling that runs
in default CI.
"""

from __future__ import annotations

from pathlib import Path

from adminme.lib.adapters import (
    CommunicationAdapter,
    ExternalStateMirrorAdapter,
    InboundDataAdapter,
    OutboundActionAdapter,
    PersonalKnowledgeAdapter,
)
from adminme.lib.pack_registry.validator import validate_adapter_pack
from packs.adapters.financial.plaid.adapter import PlaidAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAID_PACK_ROOT = REPO_ROOT / "packs" / "adapters" / "financial" / "plaid"


def test_retrofit_supervisor_loaded_plaid_inherits_inbound_data_adapter() -> None:
    """The supervisor's adapter-loading code path (direct
    ``from packs...`` import — same path as the existing supervisor
    integration test) yields a class that inherits from
    :class:`InboundDataAdapter`. Per memo 08 §3.3, this is the contract
    change the retrofit ships — the validator's
    ``base_class_conformance`` check observes this via AST walk against
    the same file. Mutual-exclusion across the 5 ABCs is pinned here at
    the integration seam per [PM-34] — the framework test covers it at
    the unit seam, this is the second layer of defense-in-depth."""
    adapter = PlaidAdapter()

    assert isinstance(adapter, InboundDataAdapter)
    assert issubclass(PlaidAdapter, InboundDataAdapter)
    # [PM-34] mutual-exclusion across the 5 ABCs.
    assert not issubclass(PlaidAdapter, CommunicationAdapter)
    assert not issubclass(PlaidAdapter, ExternalStateMirrorAdapter)
    assert not issubclass(PlaidAdapter, OutboundActionAdapter)
    assert not issubclass(PlaidAdapter, PersonalKnowledgeAdapter)


def test_retrofit_plaid_has_no_write_surface_through_loader() -> None:
    """The :class:`InboundDataAdapter` ABC's abstract surface is exactly
    ``start`` / ``stop`` / ``status`` — Cat-C is the discriminator from
    Cat-A (``send``), Cat-B (``apply_write``), Cat-E (``execute_action``)
    per memo 05 §1.2. The retrofit MUST NOT have introduced any write
    method on the production class. The framework test pins this at the
    class-shape layer; this integration test pins it on the instance
    the supervisor would actually run, closing the loop between the
    contract (ABC abstract surface) and the supervisor's runtime
    invariant."""
    adapter = PlaidAdapter()

    assert not hasattr(adapter, "send")
    assert not hasattr(adapter, "apply_write")
    assert not hasattr(adapter, "execute_action")
    assert not hasattr(adapter, "_on_inbound")
    # The three lifecycle methods Cat-C does require are callable.
    assert callable(adapter.start)
    assert callable(adapter.stop)
    assert callable(adapter.status)


def test_retrofit_validator_clean_on_supervisor_loaded_pack() -> None:
    """End-to-end retrofit contract at the integration seam: the on-disk
    pack at ``packs/adapters/financial/plaid/`` — the same path the
    supervisor's loader reads — passes the 11-β validator with NO
    ``advisory_pending_retrofit`` finding on ``base_class_conformance``
    and NO errors overall. Closes the goal of the entire 11r-b prompt
    at the integration seam (the second of three Cohort C retrofits;
    11r-a closed Gmail, 11r-c will close Reminders)."""
    result = validate_adapter_pack(PLAID_PACK_ROOT)

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
