"""
Unit tests for :class:`adminme.lib.adapters.OutboundActionAdapter`.

Covers the Cat-E contract per memo 05 §1.2 / §3.2:

- abstract-method enforcement at instantiation
- required class attributes (``family``, ``capabilities``,
  ``observation_mode_required``)
- ``observation_mode_required`` defaults to ``True`` (Cat-E cannot opt
  out of observation suppression per [§6.20])
- ``execute_action`` is callable on a concrete subclass and can be wired
  to a fake observation wrapper (representing
  :func:`adminme.lib.observation.outbound`)
- subclass satisfies the simple ``Adapter`` Protocol from
  ``adminme/adapters/base.py``
- :class:`ActionRequest` / :class:`ActionResult` shape canaries
"""

from __future__ import annotations

import inspect

import pytest

from adminme.adapters.base import Adapter, AdapterContext, AdapterStatus
from adminme.lib.adapters import (
    ActionRequest,
    ActionResult,
    OutboundActionAdapter,
)


class _FakeOutboundActionAdapter(OutboundActionAdapter):
    """Minimal concrete Cat-E subclass used across tests in this module.

    Models a Home-Assistant-shape outbound dispatcher. The fake captures
    invocations and surfaces an ``_fake_suppressed`` flag to simulate
    the observation-mode short-circuit path that
    :func:`adminme.lib.observation.outbound` provides in production.
    """

    name = "fake-action"
    family = "home_automation"
    version = "0.0.1"
    capabilities = [{"kind": "Cat-E", "runtime": "central"}]

    def __init__(self) -> None:
        self.action_calls: list[ActionRequest] = []
        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self._status.state = "running"

    async def stop(self) -> None:
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def execute_action(self, request: ActionRequest) -> ActionResult:
        # In production the subclass routes through
        # adminme.lib.observation.outbound; the test injects an
        # ``_fake_suppressed`` flag to simulate the wrapping.
        self.action_calls.append(request)
        suppressed = bool(getattr(self, "_fake_suppressed", False))
        return ActionResult(
            executed=not suppressed,
            external_id=None if suppressed else "ext-act-1",
            suppressed=suppressed,
        )


# ---------------------------------------------------------------------------
# Abstract-method enforcement
# ---------------------------------------------------------------------------


def test_cannot_instantiate_outbound_action_adapter_directly() -> None:
    """The ABC itself cannot be instantiated — abstract methods present."""
    with pytest.raises(TypeError):
        OutboundActionAdapter()  # type: ignore[abstract]


def test_rejects_subclass_missing_execute_action() -> None:
    """Subclass missing ``execute_action`` cannot instantiate."""

    class _NoExecute(OutboundActionAdapter):
        name = "x"
        family = "home_automation"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-E"}]

        async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
            return None

        async def stop(self) -> None:
            return None

        def status(self) -> AdapterStatus:
            return AdapterStatus()

    with pytest.raises(TypeError):
        _NoExecute()  # type: ignore[abstract]


def test_rejects_subclass_missing_lifecycle_methods() -> None:
    """Subclass missing start/stop/status cannot instantiate."""

    class _NoLifecycle(OutboundActionAdapter):
        name = "x"
        family = "home_automation"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-E"}]

        async def execute_action(  # noqa: ARG002
            self, request: ActionRequest
        ) -> ActionResult:
            return ActionResult(executed=True)

    with pytest.raises(TypeError):
        _NoLifecycle()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Class attribute requirements
# ---------------------------------------------------------------------------


def test_class_attribute_observation_mode_required_defaults_true() -> None:
    """Per §6.20 / memo 05 §1.2 Cat-E: an action adapter MUST route
    external side effects through observation suppression. The ABC's
    default makes opting in the cheap path."""
    assert OutboundActionAdapter.observation_mode_required is True
    assert _FakeOutboundActionAdapter.observation_mode_required is True


def test_class_attribute_capabilities_required() -> None:
    """The ``capabilities`` ClassVar is declared (empty default) and
    subclasses set a list with ≥1 Cat-E entry."""
    assert hasattr(OutboundActionAdapter, "capabilities")
    assert isinstance(_FakeOutboundActionAdapter.capabilities, list)
    assert len(_FakeOutboundActionAdapter.capabilities) >= 1
    assert _FakeOutboundActionAdapter.capabilities[0]["kind"] == "Cat-E"


def test_class_attribute_family_is_typed_literal() -> None:
    """``family`` is a class attribute the subclass sets to one of the
    Cat-E-allowed literal values."""
    assert _FakeOutboundActionAdapter.family == "home_automation"


# ---------------------------------------------------------------------------
# Protocol contract via fake subclass
# ---------------------------------------------------------------------------


def test_concrete_subclass_satisfies_simple_adapter_protocol() -> None:
    """A concrete OutboundActionAdapter satisfies the runtime-checkable
    ``Adapter`` Protocol."""
    adapter: Adapter = _FakeOutboundActionAdapter()
    assert isinstance(adapter, Adapter)
    assert adapter.name == "fake-action"
    assert adapter.family == "home_automation"
    assert adapter.version == "0.0.1"
    assert adapter.sensitivity_floor == "normal"


async def test_concrete_subclass_execute_action_returns_action_result() -> None:
    adapter = _FakeOutboundActionAdapter()
    request = ActionRequest(
        kind="home_assistant.scene",
        target_external_id="scene.evening",
        payload={"brightness": 60},
        correlation_id="corr-1",
    )
    result = await adapter.execute_action(request)
    assert isinstance(result, ActionResult)
    assert result.executed is True
    assert result.external_id == "ext-act-1"
    assert result.suppressed is False
    assert adapter.action_calls == [request]


async def test_execute_action_can_be_observation_suppressed() -> None:
    """Wire a fake outbound() suppression into the subclass's
    ``execute_action`` via a flag flip — the prompt expects
    ``execute_action`` to invoke observation suppression. The base
    class does not own the ``Session`` / ``ObservationManager`` /
    ``EventLog`` so it cannot wrap automatically, but the contract is
    that subclasses route through
    :func:`adminme.lib.observation.outbound`."""
    adapter = _FakeOutboundActionAdapter()
    adapter._fake_suppressed = True  # type: ignore[attr-defined]
    request = ActionRequest(
        kind="home_assistant.scene",
        target_external_id="scene.evening",
        payload={},
    )
    result = await adapter.execute_action(request)
    assert result.suppressed is True
    assert result.executed is False
    assert result.external_id is None


# ---------------------------------------------------------------------------
# Dataclass shape canaries
# ---------------------------------------------------------------------------


def test_action_request_is_frozen() -> None:
    req = ActionRequest(kind="x", target_external_id=None)
    with pytest.raises(Exception):  # FrozenInstanceError
        req.kind = "y"  # type: ignore[misc]


def test_action_request_defaults() -> None:
    req = ActionRequest(kind="x", target_external_id=None)
    assert req.payload == {}
    assert req.correlation_id is None


def test_action_result_defaults() -> None:
    r = ActionResult(executed=True)
    assert r.external_id is None
    assert r.suppressed is False


def test_execute_action_is_async() -> None:
    """The contract requires ``execute_action`` to be an async
    coroutine — the install-time validator (11-β) will check this; the
    canary here catches accidental sync overrides early."""
    assert inspect.iscoroutinefunction(OutboundActionAdapter.execute_action)
    assert inspect.iscoroutinefunction(
        _FakeOutboundActionAdapter.execute_action
    )
