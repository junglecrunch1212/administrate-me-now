"""
Unit tests for
:class:`adminme.lib.adapters.ExternalStateMirrorAdapter`.

Covers the Cat-B contract per memo 05 §1.2 / §2.3 / §2.5 / §3.2,
[UT-27], [PM-34]:

- abstract-method enforcement at instantiation
- :meth:`apply_write` gates on :attr:`write_capabilities` membership per
  [UT-27]; raises :class:`WriteCapabilityError` on undeclared operations
- :meth:`_on_external_change` hook
- required class attributes (``family``, ``capabilities``,
  ``write_capabilities``, ``sharing_model_discriminator``)
- subclass satisfies the simple ``Adapter`` Protocol at runtime
"""

from __future__ import annotations

import inspect

import pytest

from adminme.adapters.base import Adapter, AdapterContext, AdapterStatus
from adminme.lib.adapters import (
    ChangeEnvelope,
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)


class _FakeRemindersAdapter(ExternalStateMirrorAdapter):
    """Minimal concrete Cat-B subclass — Apple-Reminders-shaped."""

    name = "fake-reminders"
    family = "reminders"
    version = "0.0.1"
    capabilities = [{"kind": "Cat-B", "runtime": "bridge"}]
    write_capabilities = [
        "create_list",
        "delete_item",
        "toggle_completion",
    ]
    sharing_model_discriminator = "owner_id"

    def __init__(self) -> None:
        self.applied: list[WriteOperation] = []
        self.changes: list[ChangeEnvelope] = []
        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self._status.state = "running"

    async def stop(self) -> None:
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def _apply_write_impl(
        self, operation: WriteOperation
    ) -> WriteResult:
        self.applied.append(operation)
        return WriteResult(applied=True, external_id="ext-1")

    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        self.changes.append(envelope)


# ---------------------------------------------------------------------------
# Abstract-method enforcement
# ---------------------------------------------------------------------------


def test_cannot_instantiate_external_state_mirror_adapter_directly() -> None:
    with pytest.raises(TypeError):
        ExternalStateMirrorAdapter()  # type: ignore[abstract]


def test_rejects_subclass_missing_apply_write_impl() -> None:
    class _NoApply(ExternalStateMirrorAdapter):
        name = "x"
        family = "reminders"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-B"}]
        write_capabilities = ["create_list"]
        sharing_model_discriminator = "owner_id"

        async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
            return None

        async def stop(self) -> None:
            return None

        def status(self) -> AdapterStatus:
            return AdapterStatus()

        async def _on_external_change(  # noqa: ARG002
            self, envelope: ChangeEnvelope
        ) -> None:
            return None

    with pytest.raises(TypeError):
        _NoApply()  # type: ignore[abstract]


def test_rejects_subclass_missing_on_external_change() -> None:
    class _NoChange(ExternalStateMirrorAdapter):
        name = "x"
        family = "reminders"
        version = "0.0.1"
        capabilities = [{"kind": "Cat-B"}]
        write_capabilities = ["create_list"]
        sharing_model_discriminator = "owner_id"

        async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
            return None

        async def stop(self) -> None:
            return None

        def status(self) -> AdapterStatus:
            return AdapterStatus()

        async def _apply_write_impl(  # noqa: ARG002
            self, operation: WriteOperation
        ) -> WriteResult:
            return WriteResult(applied=True)

    with pytest.raises(TypeError):
        _NoChange()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Class attribute requirements
# ---------------------------------------------------------------------------


def test_class_attribute_family_required() -> None:
    """Cat-B subclasses must set family to one of the Literal members."""
    assert _FakeRemindersAdapter.family == "reminders"


def test_class_attribute_sharing_model_discriminator_required() -> None:
    assert _FakeRemindersAdapter.sharing_model_discriminator == "owner_id"


def test_class_attribute_write_capabilities_is_list() -> None:
    assert isinstance(_FakeRemindersAdapter.write_capabilities, list)
    assert "create_list" in _FakeRemindersAdapter.write_capabilities


def test_class_attribute_capabilities_has_cat_b() -> None:
    assert _FakeRemindersAdapter.capabilities[0]["kind"] == "Cat-B"


# ---------------------------------------------------------------------------
# apply_write gate behavior (UT-27 asymmetric per-operation capabilities)
# ---------------------------------------------------------------------------


async def test_apply_write_dispatches_declared_operation() -> None:
    adapter = _FakeRemindersAdapter()
    op = WriteOperation(
        kind="create_list", target_external_id=None, payload={"title": "Groceries"}
    )
    result = await adapter.apply_write(op)
    assert isinstance(result, WriteResult)
    assert result.applied is True
    assert adapter.applied == [op]


async def test_apply_write_rejects_undeclared_operation() -> None:
    """Per [UT-27]: a Cat-B adapter that hasn't declared an operation
    cannot perform it. The gate raises BEFORE the subclass's
    ``_apply_write_impl`` runs."""
    adapter = _FakeRemindersAdapter()
    op = WriteOperation(
        kind="delete_list",  # not in write_capabilities
        target_external_id="list-1",
        payload={},
    )
    with pytest.raises(WriteCapabilityError) as excinfo:
        await adapter.apply_write(op)
    assert "delete_list" in str(excinfo.value)
    assert adapter.applied == []  # _apply_write_impl was never invoked


async def test_apply_write_gate_enumerates_declared_caps_in_error() -> None:
    adapter = _FakeRemindersAdapter()
    op = WriteOperation(
        kind="rename_list", target_external_id="list-1", payload={}
    )
    with pytest.raises(WriteCapabilityError) as excinfo:
        await adapter.apply_write(op)
    msg = str(excinfo.value)
    # Each declared capability should appear in the diagnostic so the
    # author can see what IS allowed.
    assert "create_list" in msg
    assert "delete_item" in msg


# ---------------------------------------------------------------------------
# _on_external_change hook
# ---------------------------------------------------------------------------


async def test_on_external_change_hook_callable() -> None:
    adapter = _FakeRemindersAdapter()
    env = ChangeEnvelope(
        kind="item_completed",
        external_id="item-1",
        received_at="2026-05-16T00:00:00Z",
        payload={"by": "ek-bridge"},
    )
    await adapter._on_external_change(env)
    assert adapter.changes == [env]


# ---------------------------------------------------------------------------
# Protocol contract
# ---------------------------------------------------------------------------


def test_concrete_subclass_satisfies_simple_adapter_protocol() -> None:
    adapter: Adapter = _FakeRemindersAdapter()
    assert isinstance(adapter, Adapter)
    assert adapter.family == "reminders"


# ---------------------------------------------------------------------------
# Dataclass shape canaries
# ---------------------------------------------------------------------------


def test_write_operation_is_frozen() -> None:
    op = WriteOperation(kind="create_list", target_external_id=None)
    with pytest.raises(Exception):  # FrozenInstanceError
        op.kind = "delete_item"  # type: ignore[misc]


def test_write_result_defaults() -> None:
    r = WriteResult(applied=False)
    assert r.external_id is None
    assert r.suppressed is False


def test_change_envelope_required_fields() -> None:
    env = ChangeEnvelope(
        kind="list_renamed",
        external_id="list-1",
        received_at="2026-05-16T00:00:00Z",
        payload={"new_title": "Errands"},
    )
    assert env.external_id == "list-1"


def test_apply_write_is_concrete_async() -> None:
    """``apply_write`` is concrete on the base (centralized gate) and
    must remain async to compose with the subclass's
    ``_apply_write_impl``."""
    assert inspect.iscoroutinefunction(
        ExternalStateMirrorAdapter.apply_write
    )
