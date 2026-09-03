"""Unit tests for ``adminme_bridge.adapters.base``.

Pins the structural Protocol surface so 11c-ii's Apple Notes / Voice
Notes / Obsidian adapters can rely on the shape without subclassing
(per [PM-34] — Cat-D adapters are Protocol-satisfied, not ABC-
inherited)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from typing import ClassVar

import pytest

from adminme_bridge.adapters.base import (
    AdapterStatus,
    BridgeContext,
    KnowledgeAdapter,
)


class _SatisfyingAdapter:
    """A class that structurally satisfies :class:`KnowledgeAdapter` —
    no inheritance. This is the bridge-side adapter shape 11c-ii will
    ship."""

    subkind: ClassVar[str] = "apple-notes"

    def __init__(self) -> None:
        self._status = AdapterStatus()

    async def start(self, *, ctx: BridgeContext) -> None:  # pragma: no cover
        return None

    async def stop(self) -> None:  # pragma: no cover
        return None

    def status(self) -> AdapterStatus:
        return self._status


def test_satisfying_class_passes_runtime_protocol_check() -> None:
    """``@runtime_checkable`` on :class:`KnowledgeAdapter` means
    ``isinstance`` works for duck-typed adapters — 11c-ii relies on this
    for ``KnowledgeAdapter`` -typed iteration over the supervisor's
    adapter list."""
    assert isinstance(_SatisfyingAdapter(), KnowledgeAdapter)


def test_non_satisfying_class_fails_runtime_protocol_check() -> None:
    """A class missing ``stop`` is NOT a :class:`KnowledgeAdapter`."""

    class _Broken:
        subkind: ClassVar[str] = "nope"

        async def start(self, *, ctx: BridgeContext) -> None:
            return None

        # NB: no `stop`, no `status`.

    assert not isinstance(_Broken(), KnowledgeAdapter)


def test_bridge_context_is_frozen_dataclass_with_required_fields() -> None:
    """:class:`BridgeContext` is frozen so adapters cannot rebind
    dependencies post-start, and exposes the fields the 11c-ii adapters
    consume plus the 11b-ii-α-1 ``sink`` add (optional, defaulted to
    ``None`` — F-3)."""
    ctx = BridgeContext(
        member_id="m1",
        ingest_client=object(),  # type: ignore[arg-type]
        correlation_id_factory=lambda: "cor-1",
        now_factory=lambda: datetime.now(timezone.utc),
    )
    assert ctx.member_id == "m1"
    assert ctx.correlation_id_factory() == "cor-1"
    assert isinstance(ctx.now_factory(), datetime)
    with pytest.raises(FrozenInstanceError):
        ctx.member_id = "m2"  # type: ignore[misc]
    field_names = {f.name for f in fields(BridgeContext)}
    assert field_names == {
        "member_id",
        "ingest_client",
        "correlation_id_factory",
        "now_factory",
        "sink",
        "tenant_id",
        "enrollment_id",
        "configuration_sha256",
        "device_identity_sha256",
    }
    assert ctx.tenant_id == "tenant-default"


def test_bridge_context_construction_with_optional_sink() -> None:
    """F-3: ``sink`` is added to :class:`BridgeContext` as an optional
    field defaulted to ``None`` so 11c-i callers that construct via the
    pre-α-1 four-kwarg shape continue working unchanged. α-2 adapters
    inject an explicit ``BridgeEventSink`` via the same field."""
    from adminme_bridge.adapters.sinks import (
        BridgeEventSink,
        EventLogBridgeSink,
    )

    # No sink → default None (11c-i transitional path).
    ctx = BridgeContext(
        member_id="m1",
        ingest_client=object(),  # type: ignore[arg-type]
        correlation_id_factory=lambda: "cor-1",
        now_factory=lambda: datetime.now(timezone.utc),
    )
    assert ctx.sink is None

    # Explicit sink kwarg → carried on the context. EventLogBridgeSink
    # is used here because it's the cheapest impl to construct in a
    # unit test (no IngestClient HTTP wiring).
    class _NoopSink:
        async def write(self, envelope: object, **kwargs: object) -> str:
            return "ev_x"

    sink = EventLogBridgeSink(_NoopSink())  # type: ignore[arg-type]
    ctx_with_sink = BridgeContext(
        member_id="m1",
        ingest_client=object(),  # type: ignore[arg-type]
        correlation_id_factory=lambda: "cor-1",
        now_factory=lambda: datetime.now(timezone.utc),
        sink=sink,
    )
    assert isinstance(ctx_with_sink.sink, BridgeEventSink)
    assert ctx_with_sink.sink is sink


def test_bridge_context_field_order_preserves_positional_arg_compatibility() -> None:
    """F-3 pin: ``sink`` lives at the END of the field list so the 11c-i
    positional construction shape ``(member_id, ingest_client,
    correlation_id_factory, now_factory)`` keeps working. If a future
    refactor inserts ``sink`` before ``now_factory``, this test fails
    loudly — the positional break would silently re-bind ``sink`` over
    callers that pass a callable for ``now_factory`` positionally."""
    field_order = [f.name for f in fields(BridgeContext)]
    assert field_order[:4] == [
        "member_id",
        "ingest_client",
        "correlation_id_factory",
        "now_factory",
    ]
    # Gate 6 appends tenant_id after sink, preserving the fifth positional
    # argument for callers that already supplied an explicit sink.
    assert field_order[4:] == [
        "sink",
        "tenant_id",
        "enrollment_id",
        "configuration_sha256",
        "device_identity_sha256",
    ]
    # And constructable via positional args (no `sink` supplied).
    ctx = BridgeContext(
        "m1",
        object(),  # type: ignore[arg-type]
        lambda: "cor-1",
        lambda: datetime.now(timezone.utc),
    )
    assert ctx.member_id == "m1"
    assert ctx.sink is None


def test_adapter_status_defaults_and_restart_counter_mutates() -> None:
    """:class:`AdapterStatus` is NOT frozen — the supervisor mutates
    ``restart_count`` on each crash-loop iteration. Verifies the
    default state + that the field is writable."""
    status = AdapterStatus()
    assert status.state == "starting"
    assert status.last_error is None
    assert status.restart_count == 0
    status.restart_count += 1
    status.state = "running"
    assert status.restart_count == 1
    assert status.state == "running"
