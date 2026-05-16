"""
Unit tests for :class:`adminme.lib.adapters.CommunicationAdapter`.

Covers the Cat-A contract per memo 05 §1.2 / §3.2:

- abstract-method enforcement at instantiation
- required class attributes (``family``, ``capabilities``)
- subclass satisfies the simple ``Adapter`` Protocol from
  ``adminme/adapters/base.py`` at runtime
- ``send`` is callable on a concrete subclass and can be wired to a
  fake observation wrapper (representing :func:`adminme.lib.observation
  .outbound`)
- ``_on_inbound`` hook is callable
- :class:`OutboundMessage` / :class:`SendResult` / :class:`InboundEnvelope`
  shape canaries
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from adminme.adapters.base import Adapter, AdapterContext, AdapterStatus
from adminme.lib.adapters import (
    CommunicationAdapter,
    InboundEnvelope,
    OutboundMessage,
    SendResult,
)


class _FakeCommunicationAdapter(CommunicationAdapter):
    """Minimal concrete subclass used across tests in this module."""

    name = "fake-messaging"
    version = "0.0.1"
    capabilities = [{"kind": "Cat-A", "runtime": "central"}]

    def __init__(self) -> None:
        self.send_calls: list[OutboundMessage] = []
        self.inbound_calls: list[InboundEnvelope] = []
        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self._status.state = "running"

    async def stop(self) -> None:
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def send(self, message: OutboundMessage) -> SendResult:
        # In production the subclass routes through
        # adminme.lib.observation.outbound; the test injects a fake
        # callable to simulate the wrapping (see the wiring test below).
        self.send_calls.append(message)
        suppressed = bool(getattr(self, "_fake_suppressed", False))
        return SendResult(
            delivered=not suppressed,
            external_id=None if suppressed else "ext-1",
            suppressed=suppressed,
        )

    async def _on_inbound(self, envelope: InboundEnvelope) -> None:
        self.inbound_calls.append(envelope)


# ---------------------------------------------------------------------------
# Abstract-method enforcement
# ---------------------------------------------------------------------------


def test_cannot_instantiate_communication_adapter_directly() -> None:
    """The ABC itself cannot be instantiated — abstract methods present."""
    with pytest.raises(TypeError):
        CommunicationAdapter()  # type: ignore[abstract]


def test_rejects_subclass_missing_send() -> None:
    """Subclass missing ``send`` cannot instantiate (abstractmethod)."""

    class _NoSend(CommunicationAdapter):
        name = "x"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-A"}]

        async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
            return None

        async def stop(self) -> None:
            return None

        def status(self) -> AdapterStatus:
            return AdapterStatus()

        async def _on_inbound(self, envelope: InboundEnvelope) -> None:
            return None

    with pytest.raises(TypeError):
        _NoSend()  # type: ignore[abstract]


def test_rejects_subclass_missing_on_inbound() -> None:
    class _NoInbound(CommunicationAdapter):
        name = "x"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-A"}]

        async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
            return None

        async def stop(self) -> None:
            return None

        def status(self) -> AdapterStatus:
            return AdapterStatus()

        async def send(self, message: OutboundMessage) -> SendResult:  # noqa: ARG002
            return SendResult(delivered=True)

    with pytest.raises(TypeError):
        _NoInbound()  # type: ignore[abstract]


def test_rejects_subclass_missing_lifecycle_methods() -> None:
    """Subclasses must also implement start/stop/status (re-declared
    abstract on the ABC so a forgetful subclass can't slip past)."""

    class _NoLifecycle(CommunicationAdapter):
        name = "x"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-A"}]

        async def send(self, message: OutboundMessage) -> SendResult:  # noqa: ARG002
            return SendResult(delivered=True)

        async def _on_inbound(self, envelope: InboundEnvelope) -> None:  # noqa: ARG002
            return None

    with pytest.raises(TypeError):
        _NoLifecycle()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Class attribute requirements
# ---------------------------------------------------------------------------


def test_class_attribute_family_is_messaging_literal() -> None:
    """Cat-A family is pinned to ``"messaging"`` on the ABC default."""
    assert CommunicationAdapter.family == "messaging"
    assert _FakeCommunicationAdapter.family == "messaging"


def test_class_attribute_capabilities_required() -> None:
    """The ``capabilities`` ClassVar is declared on the ABC (empty
    default) and subclasses set a list with ≥1 Cat-A entry."""
    assert hasattr(CommunicationAdapter, "capabilities")
    assert isinstance(_FakeCommunicationAdapter.capabilities, list)
    assert len(_FakeCommunicationAdapter.capabilities) >= 1
    assert _FakeCommunicationAdapter.capabilities[0]["kind"] == "Cat-A"


# ---------------------------------------------------------------------------
# Protocol contract via fake subclass
# ---------------------------------------------------------------------------


def test_concrete_subclass_satisfies_simple_adapter_protocol() -> None:
    """A concrete CommunicationAdapter satisfies the runtime-checkable
    ``Adapter`` Protocol from ``adminme/adapters/base.py``."""
    adapter: Adapter = _FakeCommunicationAdapter()
    assert isinstance(adapter, Adapter)
    assert adapter.name == "fake-messaging"
    assert adapter.family == "messaging"
    assert adapter.version == "0.0.1"
    assert adapter.sensitivity_floor == "normal"


async def test_concrete_subclass_send_returns_send_result() -> None:
    adapter = _FakeCommunicationAdapter()
    msg = OutboundMessage(channel="email", recipient="x@y", body="hi")
    result = await adapter.send(msg)
    assert isinstance(result, SendResult)
    assert result.delivered is True
    assert adapter.send_calls == [msg]


async def test_concrete_subclass_send_can_be_observation_suppressed() -> None:
    """Wire a fake outbound() suppression into the subclass's send via a
    flag flip — the prompt expects ``send`` to invoke observation
    suppression. The base class does not own the ``Session`` /
    ``ObservationManager`` / ``EventLog`` so it cannot wrap
    automatically, but the contract is that subclasses route through
    :func:`adminme.lib.observation.outbound`. This test simulates the
    suppression path returning the expected ``SendResult`` shape."""
    adapter = _FakeCommunicationAdapter()
    adapter._fake_suppressed = True  # type: ignore[attr-defined]
    result = await adapter.send(
        OutboundMessage(channel="email", recipient="x@y", body="hi")
    )
    assert result.suppressed is True
    assert result.delivered is False
    assert result.external_id is None


async def test_on_inbound_hook_callable() -> None:
    adapter = _FakeCommunicationAdapter()
    env = InboundEnvelope(
        external_id="msg-1",
        channel="email",
        sender="x@y",
        received_at="2026-05-16T00:00:00Z",
        payload={"subject": "hi"},
    )
    await adapter._on_inbound(env)
    assert adapter.inbound_calls == [env]


# ---------------------------------------------------------------------------
# Dataclass shape canaries
# ---------------------------------------------------------------------------


def test_outbound_message_is_frozen() -> None:
    msg = OutboundMessage(channel="email", recipient="x@y", body="hi")
    with pytest.raises(Exception):  # FrozenInstanceError
        msg.channel = "sms"  # type: ignore[misc]


def test_send_result_defaults() -> None:
    r = SendResult(delivered=True)
    assert r.external_id is None
    assert r.suppressed is False


def test_inbound_envelope_required_fields() -> None:
    env = InboundEnvelope(
        external_id="x",
        channel="email",
        sender="a@b",
        received_at="2026-05-16T00:00:00Z",
        payload={},
    )
    assert env.external_id == "x"
    assert env.payload == {}


def test_send_is_async() -> None:
    """The contract requires ``send`` to be an async coroutine — the
    install-time validator (11-β) will check this; the canary here
    catches accidental sync overrides early."""
    assert inspect.iscoroutinefunction(CommunicationAdapter.send)
    assert inspect.iscoroutinefunction(_FakeCommunicationAdapter.send)


def test_on_inbound_is_async() -> None:
    assert inspect.iscoroutinefunction(CommunicationAdapter._on_inbound)
