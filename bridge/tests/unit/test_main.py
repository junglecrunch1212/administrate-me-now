"""Unit tests for ``adminme_bridge.main``.

11c-i ships the daemon shell only; concrete adapters land in 11c-ii.
These tests pin the supervisor lifecycle + SIGTERM handling so the
shell stays load-bearing for the 11c-ii adapter integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import ClassVar

import pytest

from adminme_bridge.adapters.base import AdapterStatus, BridgeContext
from adminme_bridge.config import BridgeConfig
from adminme_bridge.main import BridgeSupervisor, _run_until_signal


class _FakeIngestClient:
    """Bridge-side ingest client double — never opens a real socket."""

    def __init__(self) -> None:
        self.aclosed = False

    async def aclose(self) -> None:
        self.aclosed = True


@dataclass
class _RecordingAdapter:
    """Recording :class:`KnowledgeAdapter` for supervisor lifecycle tests."""

    subkind: ClassVar[str] = "test"
    started: bool = False
    stopped: bool = False
    last_ctx: BridgeContext | None = None
    _status: AdapterStatus = field(default_factory=AdapterStatus)

    async def start(self, *, ctx: BridgeContext) -> None:
        self.started = True
        self.last_ctx = ctx
        # Simulate a long-lived listener — sleep until cancelled.
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self._status.state = "stopped"
            raise

    async def stop(self) -> None:
        self.stopped = True

    def status(self) -> AdapterStatus:
        return self._status


def _config() -> BridgeConfig:
    return BridgeConfig(
        member_id="m1",
        central_endpoint="http://127.0.0.1:3337",
        tailscale_identity="m1@example.ts.net",
    )


async def test_supervisor_start_dispatches_into_adapter_start() -> None:
    """Per the supervisor pattern (mirrors ``adapter_supervisor.py``):
    ``supervisor.start()`` schedules every adapter's ``start(ctx=...)``
    on its own task with a BridgeContext-carrying ``member_id``."""
    adapter = _RecordingAdapter()
    supervisor = BridgeSupervisor(
        config=_config(),
        ingest_client=_FakeIngestClient(),  # type: ignore[arg-type]
        adapters=[adapter],
    )
    await supervisor.start()
    # Yield once so the supervisor task lands inside `adapter.start`.
    await asyncio.sleep(0)
    try:
        assert adapter.started is True
        assert adapter.last_ctx is not None
        assert adapter.last_ctx.member_id == "m1"
    finally:
        await supervisor.stop()
    assert adapter.stopped is True


async def test_run_until_signal_stops_supervisor_on_event() -> None:
    """``_run_until_signal`` awaits a stop event then cleanly shuts the
    supervisor — pinning the SIGTERM-equivalent shutdown path that
    ``run()`` wires to the real ``signal.SIGTERM`` / ``SIGINT``."""
    adapter = _RecordingAdapter()
    supervisor = BridgeSupervisor(
        config=_config(),
        ingest_client=_FakeIngestClient(),  # type: ignore[arg-type]
        adapters=[adapter],
    )
    # Schedule the supervisor run; then cancel it (the platform-portable
    # surrogate for a delivered signal in pytest's event loop).
    task = asyncio.create_task(_run_until_signal(supervisor))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Clean-shutdown path executed.
    assert adapter.stopped is True


async def test_supervisor_snapshot_reports_adapter_states() -> None:
    """``snapshot()`` is the operator-visible read-side; it must include
    adapter restart counts so 11c-ii's flakiness panel can render."""
    a1 = _RecordingAdapter()
    a2 = _RecordingAdapter()
    supervisor = BridgeSupervisor(
        config=_config(),
        ingest_client=_FakeIngestClient(),  # type: ignore[arg-type]
        adapters=[a1, a2],
    )
    snap = supervisor.snapshot()
    assert snap["supervisor"]["adapter_count"] == 2
    assert len(snap["adapters"]) == 2
    for entry in snap["adapters"]:
        # Default ``AdapterStatus`` shape per `adapters.base`.
        assert entry["state"] == "starting"
        assert entry["last_error"] is None
        assert entry["restart_count"] == 0
