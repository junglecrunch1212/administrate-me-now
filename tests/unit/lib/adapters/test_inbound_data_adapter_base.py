"""
Unit tests for :class:`adminme.lib.adapters.InboundDataAdapter`.

Covers the Cat-C contract per memo 05 §1.2 / §3.2, [D19], [PM-34]:

- abstract-method enforcement at instantiation
- the ABC declares NO write method (the discriminator from Cat-B)
- required class attributes (``family``, ``capabilities``)
- subclass satisfies the simple ``Adapter`` Protocol at runtime
"""

from __future__ import annotations

import pytest

from adminme.adapters.base import Adapter, AdapterContext, AdapterStatus
from adminme.lib.adapters import InboundDataAdapter


class _FakePlaidAdapter(InboundDataAdapter):
    """Minimal concrete Cat-C subclass — Plaid-shaped."""

    name = "fake-plaid"
    family = "financial"
    version = "0.0.1"
    sensitivity_floor = "privileged"  # financial is privileged per the pack
    capabilities = [{"kind": "Cat-C", "runtime": "central"}]

    def __init__(self) -> None:
        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self._status.state = "running"

    async def stop(self) -> None:
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status


# ---------------------------------------------------------------------------
# Abstract-method enforcement
# ---------------------------------------------------------------------------


def test_cannot_instantiate_inbound_data_adapter_directly() -> None:
    with pytest.raises(TypeError):
        InboundDataAdapter()  # type: ignore[abstract]


def test_rejects_subclass_missing_start() -> None:
    class _NoStart(InboundDataAdapter):
        name = "x"
        family = "iot"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-C"}]

        async def stop(self) -> None:
            return None

        def status(self) -> AdapterStatus:
            return AdapterStatus()

    with pytest.raises(TypeError):
        _NoStart()  # type: ignore[abstract]


def test_rejects_subclass_missing_stop() -> None:
    class _NoStop(InboundDataAdapter):
        name = "x"
        family = "iot"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-C"}]

        async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
            return None

        def status(self) -> AdapterStatus:
            return AdapterStatus()

    with pytest.raises(TypeError):
        _NoStop()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Cat-C has no write surface (discriminator from Cat-B)
# ---------------------------------------------------------------------------


def test_cat_c_abc_has_no_apply_write_method() -> None:
    """Cat-C is inbound-only by definition. The ABC must not declare an
    ``apply_write`` method — if it did, the discriminator from Cat-B
    would collapse."""
    assert not hasattr(InboundDataAdapter, "apply_write")
    assert not hasattr(InboundDataAdapter, "_apply_write_impl")


def test_cat_c_abc_has_no_send_method() -> None:
    """Cat-C is also not a messaging adapter; no ``send`` either."""
    assert not hasattr(InboundDataAdapter, "send")


def test_cat_c_abc_has_no_write_capabilities_attribute() -> None:
    """Per [UT-27]: only Cat-B / Cat-E declare ``write_capabilities``.
    The 11-β validator rejects Cat-C manifests with the field; surfacing
    the absence at the ClassVar layer keeps the rule legible."""
    assert "write_capabilities" not in InboundDataAdapter.__annotations__


# ---------------------------------------------------------------------------
# Class attribute requirements
# ---------------------------------------------------------------------------


def test_class_attribute_family_required() -> None:
    assert _FakePlaidAdapter.family == "financial"


def test_class_attribute_capabilities_has_cat_c() -> None:
    assert _FakePlaidAdapter.capabilities[0]["kind"] == "Cat-C"


# ---------------------------------------------------------------------------
# Protocol contract
# ---------------------------------------------------------------------------


def test_concrete_subclass_satisfies_simple_adapter_protocol() -> None:
    adapter: Adapter = _FakePlaidAdapter()
    assert isinstance(adapter, Adapter)
    assert adapter.family == "financial"
    assert adapter.sensitivity_floor == "privileged"


async def test_concrete_subclass_lifecycle_works() -> None:
    """Cat-C lifecycle is the same as the simple Adapter Protocol."""
    adapter = _FakePlaidAdapter()
    assert adapter.status().state == "starting"
    # The test_adapter_supervisor_* integration tests cover real start;
    # here we exercise the lifecycle methods exist and run.
    await adapter.start(ctx=None)  # type: ignore[arg-type]
    assert adapter.status().state == "running"
    await adapter.stop()
    assert adapter.status().state == "stopped"
