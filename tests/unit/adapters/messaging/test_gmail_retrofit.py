"""Framework-level tests for the Gmail Cohort C retrofit.

Closes prompt 11r-a commit 3. Verifies the inheritance flip and the
abstract-method stubs satisfy the :class:`CommunicationAdapter` contract
declared at ``adminme/lib/adapters/communication_base.py``, that the
``pack.yaml`` capabilities block round-trips through
:func:`parse_adapter_manifest`, and that the 11-β validator returns NO
``advisory_pending_retrofit`` finding post-retrofit per
``docs/08-architecture-amendment-adapter-rebase.md`` §3.3.

These tests live under ``tests/unit/adapters/messaging/`` (framework
location) rather than the pack-internal ``packs/adapters/messaging/
gmail/tests/`` directory because they exercise the framework's contract
layer (the 5 ABCs + the validator + the manifest parser), not the pack's
internal polling logic. The pack-internal tests at
``test_gmail_adapter_pack_internal.py`` continue to cover the Gmail-
specific inbound flow unchanged.

``packs/`` is not a Python package under poetry's ``packages`` config,
so the GmailAdapter class is loaded via the file-path importlib pattern
shared with the pack-internal tests and the supervisor integration test.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from adminme.lib.adapters import (
    CommunicationAdapter,
    InboundEnvelope,
    OutboundMessage,
)
from adminme.lib.pack_registry.parser import parse_adapter_manifest
from adminme.lib.pack_registry.validator import validate_adapter_pack

REPO_ROOT = Path(__file__).resolve().parents[4]
GMAIL_PACK_DIR = REPO_ROOT / "packs" / "adapters" / "messaging" / "gmail"


def _load_gmail_adapter_class() -> Any:
    """Load ``GmailAdapter`` from the pack root by file path.

    Mirrors ``test_gmail_adapter_pack_internal._load_adapter_module`` and
    ``test_adapter_supervisor_gmail._load_gmail_module``: ``packs/`` is
    not on the import path under poetry's package config, so file-path
    importlib is the canonical loader for pack code from framework tests.
    """
    spec = importlib.util.spec_from_file_location(
        "gmail_adapter_retrofit_under_test", GMAIL_PACK_DIR / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GmailAdapter


GmailAdapter: Any = _load_gmail_adapter_class()


def _make_adapter() -> Any:
    """Instantiate ``GmailAdapter`` with the test op-ref shape the
    pack-internal tests use; no live 1Password CLI required."""
    return GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
        },
    )


# ---------------------------------------------------------------------------
# 1) ABC inheritance — the inheritance flip closed the advisory
# ---------------------------------------------------------------------------


def test_gmail_adapter_inherits_communication_adapter() -> None:
    """The retrofit declares ``class GmailAdapter(CommunicationAdapter)``
    per memo 08 §3.3. This is the core contract change the validator's
    ``base_class_conformance`` check observes via AST walk."""
    assert issubclass(GmailAdapter, CommunicationAdapter)


def test_gmail_adapter_inherits_exactly_one_framework_abc() -> None:
    """The 5 ABCs are mutually exclusive per [PM-34]; Gmail is Cat-A
    only. Importing the other four and asserting non-membership pins
    the constraint so a future drift (e.g. accidental dual-inheritance)
    surfaces as a test failure rather than as a validator error at
    install time."""
    from adminme.lib.adapters import (
        ExternalStateMirrorAdapter,
        InboundDataAdapter,
        OutboundActionAdapter,
        PersonalKnowledgeAdapter,
    )

    assert not issubclass(GmailAdapter, ExternalStateMirrorAdapter)
    assert not issubclass(GmailAdapter, InboundDataAdapter)
    assert not issubclass(GmailAdapter, OutboundActionAdapter)
    assert not issubclass(GmailAdapter, PersonalKnowledgeAdapter)


# ---------------------------------------------------------------------------
# 2) Manifest extension round-trips through the parser
# ---------------------------------------------------------------------------


def test_pack_yaml_capabilities_block_parses() -> None:
    """``pack.yaml`` declares one Cat-A capability with runtime=central
    and ``messaging.received`` in event_families. The parser MUST round-
    trip this cleanly with no Pydantic validation errors."""
    manifest = parse_adapter_manifest(GMAIL_PACK_DIR)

    assert len(manifest.capabilities) == 1
    cap = manifest.capabilities[0]
    assert cap.kind == "Cat-A"
    assert cap.runtime == "central"
    assert cap.event_families == ["messaging.received"]


def test_pack_yaml_extension_fields_round_trip() -> None:
    """The retrofit's four extension fields — observation_mode_required,
    sharing_model_discriminator, dedup_strategy — populate the manifest
    per memo 05 §3.1. ``sensitivity_default`` defaults to ``"normal"``
    (the AdapterManifest default; matches Gmail's
    ``sensitivity_floor="normal"``)."""
    manifest = parse_adapter_manifest(GMAIL_PACK_DIR)

    assert manifest.observation_mode_required is True
    assert manifest.sharing_model_discriminator == "owner_member_id"
    assert manifest.dedup_strategy == {"kind": "external_id"}
    assert manifest.sensitivity_default == "normal"
    # Cat-A's write surface is :meth:`send`, not a per-operation list.
    # The retrofit must leave write_capabilities empty per [UT-27].
    assert manifest.write_capabilities == []


# ---------------------------------------------------------------------------
# 3) Validator — advisory_pending_retrofit is GONE post-retrofit
# ---------------------------------------------------------------------------


def test_validator_no_pending_retrofit_advisory() -> None:
    """The 11-β validator's ``base_class_conformance`` check previously
    surfaced ``advisory_pending_retrofit`` for Gmail (no ABC, no
    capabilities). The retrofit closes both gaps; the advisory MUST be
    absent from the result. This is the goal of the entire 11r-a prompt."""
    result = validate_adapter_pack(GMAIL_PACK_DIR)

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
    result = validate_adapter_pack(GMAIL_PACK_DIR)

    assert not result.has_errors(), (
        f"validator reports errors post-retrofit: {result.errors()}"
    )


# ---------------------------------------------------------------------------
# 4) `send` — real outbound shipped in 11a c2 (replaces the 11r-a stub)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_no_longer_raises_not_implemented() -> None:
    """Prompt 11a c2 replaced the 11r-a ``NotImplementedError`` stub
    with a real Gmail v1 ``users.messages.send`` implementation. The
    Phase-A path (observation seams unset) calls the Gmail service
    directly. The deep-coverage assertions live in
    ``test_gmail_send.py``; this test pins the retrofit advisory's
    closure — :meth:`send` no longer raises ``NotImplementedError`` from
    the framework's perspective."""
    adapter = _make_adapter()
    captured_send: list[dict[str, Any]] = []

    class _FakeExecute:
        def execute(self) -> dict[str, Any]:
            return {"id": "gmail-msg-r1", "threadId": "thr-r1"}

    class _FakeMessages:
        def send(self, **kwargs: Any) -> Any:
            captured_send.append(kwargs)
            return _FakeExecute()

    class _FakeUsers:
        def messages(self) -> Any:
            return _FakeMessages()

    class _FakeService:
        def users(self) -> Any:
            return _FakeUsers()

    adapter._service = _FakeService()
    message = OutboundMessage(
        channel="gmail",
        recipient="someone@example.test",
        body="hello",
        metadata={"subject": "ping"},
    )

    result = await adapter.send(message)

    # No NotImplementedError. Gmail API was actually called.
    assert len(captured_send) == 1
    assert captured_send[0]["userId"] == "me"
    assert "raw" in captured_send[0]["body"]
    assert result.delivered is True
    assert result.external_id == "gmail-msg-r1"
    assert result.suppressed is False


@pytest.mark.asyncio
async def test_send_routes_through_observation_when_seams_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the supervisor (Phase B) populates the three observation-
    seam slots, :meth:`send` MUST route the action through
    :func:`adminme.lib.observation.outbound` rather than calling the
    Gmail API directly — the 11r-c Phase-A-conditional pattern applied
    to Cat-A's send. The deep payload/SendResult coverage lives in
    ``test_gmail_send.py``; this test pins the routing contract at the
    framework boundary."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gmail_adapter_obs_routing_test", GMAIL_PACK_DIR / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    GmailAdapterModule = module.GmailAdapter

    routed: list[str] = []

    async def fake_outbound(
        session: Any,
        action: str,
        payload: dict[str, Any],
        action_fn: Any,
        *,
        manager: Any,
        event_sink: Any,
        target_channel: str,
        target_identifier: str,
    ) -> Any:
        routed.append(action)
        # Importing OutboundResult lazily so the retrofit test file does
        # not pick up unused-import lint when the test is skipped.
        from adminme.lib.observation import OutboundResult

        return OutboundResult(suppressed=False, result={"id": "x", "threadId": "y"})

    monkeypatch.setattr(module, "outbound", fake_outbound)

    class _FakeExecute:
        def execute(self) -> dict[str, Any]:
            return {"id": "should-not-be-used", "threadId": "should-not"}

    class _FakeMessages:
        def send(self, **kwargs: Any) -> Any:
            return _FakeExecute()

    class _FakeUsers:
        def messages(self) -> Any:
            return _FakeMessages()

    class _FakeService:
        def users(self) -> Any:
            return _FakeUsers()

    adapter = GmailAdapterModule(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
        },
        observation_session=object(),
        observation_manager=object(),
        observation_event_log=object(),
    )
    adapter._service = _FakeService()
    message = OutboundMessage(
        channel="gmail",
        recipient="someone@example.test",
        body="seam routing test",
    )

    await adapter.send(message)

    assert routed == ["messaging.send"]


# ---------------------------------------------------------------------------
# 5) `_on_inbound` stub — Phase A no-op satisfying the ABC contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_inbound_is_no_op_satisfying_abc() -> None:
    """The ``_on_inbound`` hook satisfies the abstract method on
    :class:`CommunicationAdapter` so ``GmailAdapter()`` can instantiate
    at all. The existing ``_poll_loop`` → ``_emit_messaging_received``
    path stays in place per memo 08 §5; the hook is a deliberate no-op
    today (a future internal refactor can route through it for in-
    memory dedup or per-envelope metrics)."""
    adapter = _make_adapter()
    envelope = InboundEnvelope(
        external_id="msg-id-1",
        channel="gmail",
        sender="sender@example.test",
        received_at="2026-05-17T00:00:00Z",
        payload={"subject": "hello"},
    )

    result = await adapter._on_inbound(envelope)

    assert result is None


# ---------------------------------------------------------------------------
# 6) Backward-compat — simple Adapter Protocol surface preserved
# ---------------------------------------------------------------------------


def test_simple_adapter_protocol_surface_preserved() -> None:
    """The inheritance flip preserves the simple ``Adapter`` Protocol
    surface (name / family / version / sensitivity_floor) per memo 08
    §5 ("inheritance + manifest only, not a rewrite"). The supervisor's
    adapter-loading code path is unchanged because the
    :class:`CommunicationAdapter` ABC extends the same Protocol shape."""
    from adminme.adapters.base import Adapter

    adapter = _make_adapter()

    assert isinstance(adapter, Adapter)
    assert adapter.name == "messaging-gmail-api"
    assert adapter.family == "messaging"
    assert adapter.version == "1.0.0"
    assert adapter.sensitivity_floor == "normal"


def test_capabilities_class_attribute_mirrors_manifest() -> None:
    """:attr:`GmailAdapter.capabilities` is the class-level mirror of
    the manifest's ``capabilities`` block per memo 05 §3.2 (runtime
    introspection without re-parsing pack.yaml). The class attribute
    MUST agree with the manifest on the Cat-A entry."""
    manifest = parse_adapter_manifest(GMAIL_PACK_DIR)

    cls_caps = GmailAdapter.capabilities
    assert isinstance(cls_caps, list)
    assert len(cls_caps) == len(manifest.capabilities) == 1
    cls_entry = cls_caps[0]
    manifest_entry = manifest.capabilities[0]
    assert cls_entry["kind"] == manifest_entry.kind
    assert cls_entry["runtime"] == manifest_entry.runtime
    assert cls_entry["event_families"] == manifest_entry.event_families
