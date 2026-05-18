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
    dependencies post-start, and exposes exactly the four fields the
    11c-ii adapters consume."""
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
    }


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
