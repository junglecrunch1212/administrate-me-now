"""``Adapter`` Protocol, ``AdapterContext`` dataclass, and
``AdapterStatus`` dataclass canaries.

Asserts the Protocol's required attributes / methods, the frozen
contract on ``AdapterContext`` (per [§6 invariant 10] tests expect
configs cannot be mutated post-start), and the default values of
``AdapterStatus``.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from adminme.adapters.base import (
    Adapter,
    AdapterContext,
    AdapterStatus,
)


class _StubAdapter:
    name = "stub"
    family = "messaging"
    version = "0.0.0"
    sensitivity_floor = "normal"

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        return None

    async def stop(self) -> None:
        return None

    def status(self) -> AdapterStatus:
        return AdapterStatus()


def test_stub_adapter_satisfies_protocol() -> None:
    adapter: Adapter = _StubAdapter()
    assert isinstance(adapter, Adapter)
    assert adapter.name == "stub"
    assert adapter.family == "messaging"
    assert adapter.sensitivity_floor == "normal"


def test_object_missing_methods_does_not_satisfy_protocol() -> None:
    class _Incomplete:
        name = "x"
        family = "messaging"
        version = "0.0.0"
        sensitivity_floor = "normal"

    assert not isinstance(_Incomplete(), Adapter)


def test_adapter_context_is_frozen() -> None:
    """`AdapterContext` is frozen per §6 invariant 10 — adapters cannot
    mutate the context to lower sensitivity floor or rebind the event
    log after start. ``dataclasses.FrozenInstanceError`` enforces."""

    def _now() -> datetime:
        return datetime(2026, 5, 16, tzinfo=timezone.utc)

    ctx = AdapterContext(
        event_log=object(),  # type: ignore[arg-type]
        config={"k": "v"},
        correlation_id_factory=lambda: "corr-1",
        now_factory=_now,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.config = {}  # type: ignore[misc]


def test_adapter_status_defaults() -> None:
    status = AdapterStatus()
    assert status.state == "starting"
    assert status.last_error is None
    assert status.last_cursor_advance_at is None
    assert status.restart_count == 0
    assert status.next_backoff_s is None
    assert status.extra == {}


def test_adapter_status_records_error() -> None:
    """An error payload assignment mutates state and last_error — the
    supervisor uses this on the backoff path."""
    status = AdapterStatus()
    status.state = "backoff"
    status.last_error = {
        "severity": "transient",
        "error_kind": "rate_limited",
        "error_message": "429",
    }
    assert status.state == "backoff"
    assert status.last_error["error_kind"] == "rate_limited"
