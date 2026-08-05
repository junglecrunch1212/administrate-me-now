"""
Unit tests for :class:`adminme.lib.adapters.PersonalKnowledgeAdapter`.

Covers the Cat-D contract per memo 05 §1.2 / §2.6 / §3.2 and memo 04 §2.2:

- abstract-method enforcement at instantiation
- required class attributes (``family``, ``runtime``, ``capabilities``,
  ``_owner_scope_immutable``)
- ``family`` pinned to ``"knowledge"`` and ``runtime`` pinned to
  ``"bridge"`` (Cat-D is bridge-only per memo 04 §2.1)
- ``_owner_scope_immutable`` is declared ``Final[bool] = True`` so the
  Tailscale-binds-owner-scope invariant per memo 04 §2.2 is legible in
  code (the bridge-ingest endpoint at ``:3337`` is the runtime
  enforcement point; this attribute is the authorship-time canary)
- concrete subclass satisfies the simple ``Adapter`` Protocol
- the ABC has NO write surface (Cat-D is ingestion-only)
"""

from __future__ import annotations

import inspect
import typing

import pytest

from adminme.adapters.base import Adapter, AdapterContext, AdapterStatus
from adminme.lib.adapters import PersonalKnowledgeAdapter


class _FakePersonalKnowledgeAdapter(PersonalKnowledgeAdapter):
    """Minimal concrete Cat-D subclass used across tests in this module.

    Models an Apple-Notes-shape adapter that would run on a member
    bridge. The bridge-ingest endpoint at ``:3337`` stamps
    ``owner_scope`` from the resolved Tailscale identity per memo 04
    §2.2; this fake does not participate in setting it.
    """

    name = "fake-knowledge"
    version = "0.0.1"
    capabilities = [{"kind": "Cat-D", "runtime": "bridge"}]

    def __init__(self) -> None:
        self._status = AdapterStatus()
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self.start_calls += 1
        self._status.state = "running"

    async def stop(self) -> None:
        self.stop_calls += 1
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status


# ---------------------------------------------------------------------------
# Abstract-method enforcement
# ---------------------------------------------------------------------------


def test_cannot_instantiate_personal_knowledge_adapter_directly() -> None:
    """The ABC itself cannot be instantiated — abstract lifecycle
    methods present."""
    with pytest.raises(TypeError):
        PersonalKnowledgeAdapter()  # type: ignore[abstract]


def test_rejects_subclass_missing_lifecycle_methods() -> None:
    """A subclass that forgets start/stop/status cannot instantiate."""

    class _NoLifecycle(PersonalKnowledgeAdapter):
        name = "x"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-D"}]

    with pytest.raises(TypeError):
        _NoLifecycle()  # type: ignore[abstract]


def test_rejects_subclass_missing_only_status() -> None:
    """Each lifecycle method is independently abstract."""

    class _NoStatus(PersonalKnowledgeAdapter):
        name = "x"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-D"}]

        async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
            return None

        async def stop(self) -> None:
            return None

    with pytest.raises(TypeError):
        _NoStatus()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Class attribute requirements — family + runtime pins
# ---------------------------------------------------------------------------


def test_class_attribute_family_pinned_to_knowledge() -> None:
    """Cat-D family is pinned to ``"knowledge"`` on the ABC default."""
    assert PersonalKnowledgeAdapter.family == "knowledge"
    assert _FakePersonalKnowledgeAdapter.family == "knowledge"


def test_class_attribute_runtime_pinned_to_bridge() -> None:
    """Cat-D runtime is pinned to ``"bridge"`` per memo 04 §2.1 — the
    central CoS Mac Mini never hosts a Cat-D adapter, so any other
    runtime value is contract-illegal."""
    assert PersonalKnowledgeAdapter.runtime == "bridge"
    assert _FakePersonalKnowledgeAdapter.runtime == "bridge"


def test_class_attribute_capabilities_required() -> None:
    """The ``capabilities`` ClassVar is declared (empty default on the
    ABC) and subclasses set a list with ≥1 Cat-D entry."""
    assert hasattr(PersonalKnowledgeAdapter, "capabilities")
    assert isinstance(_FakePersonalKnowledgeAdapter.capabilities, list)
    assert len(_FakePersonalKnowledgeAdapter.capabilities) >= 1
    assert _FakePersonalKnowledgeAdapter.capabilities[0]["kind"] == "Cat-D"


# ---------------------------------------------------------------------------
# The Tailscale-binds-owner-scope invariant — type contract via Final
# ---------------------------------------------------------------------------


def test_owner_scope_immutable_attribute_present_and_true() -> None:
    """The class attribute :attr:`_owner_scope_immutable` declares the
    rule per memo 04 §2.2 that the bridge-ingest endpoint stamps
    ``owner_scope``, not the adapter. The attribute must default to
    ``True`` on the ABC."""
    assert PersonalKnowledgeAdapter._owner_scope_immutable is True
    assert _FakePersonalKnowledgeAdapter._owner_scope_immutable is True


def test_owner_scope_immutable_declared_final() -> None:
    """The annotation is ``Final[bool]`` so a subclass that tries to
    override the attribute triggers a mypy error AND the 11-β
    validator failure. Inspect ``__annotations__`` directly to confirm
    the Final wrapping (introspection avoids importing typing-extensions
    at runtime)."""
    # ``from __future__ import annotations`` stores annotations as
    # strings; eval_str=True resolves them against the module's globals
    # so typing.get_origin can introspect the Final wrapping.
    annotations = inspect.get_annotations(PersonalKnowledgeAdapter, eval_str=True)
    assert "_owner_scope_immutable" in annotations
    final_anno = annotations["_owner_scope_immutable"]
    # typing.Final[bool] has origin typing.Final under runtime
    # introspection on 3.11+; the type arg is bool.
    origin = typing.get_origin(final_anno)
    assert origin is typing.Final, (
        f"_owner_scope_immutable must be Final, got origin={origin!r}"
    )
    args = typing.get_args(final_anno)
    assert args == (bool,), (
        f"_owner_scope_immutable args expected (bool,), got {args!r}"
    )


# ---------------------------------------------------------------------------
# Cat-D has no write surface
# ---------------------------------------------------------------------------


def test_personal_knowledge_adapter_has_no_write_methods() -> None:
    """Cat-D is ingestion-only — the ABC must not expose ``send`` /
    ``apply_write`` / ``execute_action`` to keep the contract pure."""
    forbidden = {"send", "apply_write", "execute_action", "write_capabilities"}
    public_surface = set(vars(PersonalKnowledgeAdapter).keys())
    overlap = forbidden & public_surface
    assert not overlap, f"Cat-D ABC must not declare write surface; found: {overlap!r}"


# ---------------------------------------------------------------------------
# Protocol contract via fake subclass
# ---------------------------------------------------------------------------


def test_concrete_subclass_satisfies_simple_adapter_protocol() -> None:
    """A concrete PersonalKnowledgeAdapter satisfies the
    runtime-checkable ``Adapter`` Protocol."""
    adapter: Adapter = _FakePersonalKnowledgeAdapter()
    assert isinstance(adapter, Adapter)
    assert adapter.name == "fake-knowledge"
    assert adapter.family == "knowledge"
    assert adapter.version == "0.0.1"
    assert adapter.sensitivity_floor == "normal"


def test_lifecycle_methods_are_async() -> None:
    """The contract requires ``start`` and ``stop`` to be coroutines;
    ``status`` is sync."""
    assert inspect.iscoroutinefunction(PersonalKnowledgeAdapter.start)
    assert inspect.iscoroutinefunction(PersonalKnowledgeAdapter.stop)
    assert not inspect.iscoroutinefunction(PersonalKnowledgeAdapter.status)
