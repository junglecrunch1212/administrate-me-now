"""Cross-module retrofit-contract integration tests for prompt 11r-c.

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
from pathlib import Path
from typing import Any

import pytest

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
REMINDERS_PACK_ROOT = (
    REPO_ROOT / "packs" / "adapters" / "reminders" / "apple-eventkit"
)


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


def test_retrofit_supervisor_loaded_apple_eventkit_inherits_external_state_mirror_adapter() -> None:
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


def test_retrofit_apple_eventkit_write_capabilities_gate_rejects_undeclared_kinds_through_loader() -> None:
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
