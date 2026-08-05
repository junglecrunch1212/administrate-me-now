"""Adapter-supervisor canaries.

Covers the three c1 supervisor tests called out in prompt 11a §7
Deliverables: minimal channels.yaml parses, spawns + stops adapter
tasks cleanly, restart-on-crash with exponential backoff.

The supervisor uses a stub-loader callable (PM-27) so tests inject
adapters without going through the production pack discovery path."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext, AdapterStatus
from adminme.daemons.adapter_supervisor import (
    INITIAL_BACKOFF_S,
    MAX_BACKOFF_S,
    UPTIME_RESET_S,
    AdapterSupervisor,
)


class _FakeEventLog:
    """Captures synchronous append calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **_kwargs: Any) -> str:
        self.calls.append(envelope.model_dump())
        return f"event-{len(self.calls)}"


class _RecordingAdapter:
    name = "stub-adapter"
    family = "messaging"
    version = "0.0.0"
    sensitivity_floor = "normal"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self.started.set()
        # Sleep until cancelled — emulates a long-running poll loop.
        await asyncio.Event().wait()

    async def stop(self) -> None:
        self.stopped.set()

    def status(self) -> AdapterStatus:
        return self._status


class _CrashThenStableAdapter:
    name = "crashy-adapter"
    family = "financial"
    version = "0.0.0"
    sensitivity_floor = "privileged"

    def __init__(self) -> None:
        self.start_count = 0
        self._status = AdapterStatus()

    async def start(self, *, ctx: AdapterContext) -> None:  # noqa: ARG002
        self.start_count += 1
        if self.start_count <= 2:
            raise RuntimeError(f"crash #{self.start_count}")
        # Stable on the 3rd attempt — block until cancelled.
        await asyncio.Event().wait()

    async def stop(self) -> None:
        return None

    def status(self) -> AdapterStatus:
        return self._status


def _make_ctx_factory(event_log: _FakeEventLog) -> Any:
    def _factory() -> AdapterContext:
        return AdapterContext(
            event_sink=event_log,  # type: ignore[arg-type]
            config={},
            correlation_id_factory=lambda: "corr-test",
            now_factory=lambda: datetime(2026, 5, 16, tzinfo=timezone.utc),
        )

    return _factory


# ---------------------------------------------------------------------------
# config parsing
# ---------------------------------------------------------------------------


async def test_supervisor_parses_minimal_channels_yaml(tmp_path: Path) -> None:
    channels = tmp_path / "channels.yaml"
    channels.write_text(
        """
adapter_supervisor_host: 127.0.0.1
adapter_supervisor_port: 0
adapters:
  - kind: messaging-gmail-api
    family: messaging
    sensitivity_floor: normal
        """.strip()
        + "\n",
        encoding="utf-8",
    )
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
    )
    config = sup.load_config()
    assert config["adapter_supervisor_port"] == 0
    assert config["adapters"][0]["kind"] == "messaging-gmail-api"


async def test_supervisor_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    channels = tmp_path / "channels.yaml"
    channels.write_text("- not-a-mapping\n", encoding="utf-8")
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
    )
    with pytest.raises(ValueError):
        sup.load_config()


# ---------------------------------------------------------------------------
# lifecycle — start / stop cleanly
# ---------------------------------------------------------------------------


async def test_supervisor_spawns_and_stops_adapter_cleanly(tmp_path: Path) -> None:
    channels = tmp_path / "channels.yaml"
    channels.write_text("adapter_supervisor_port: 0\nadapters: []\n", encoding="utf-8")

    adapter = _RecordingAdapter()
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(_FakeEventLog()),
        adapter_loader=lambda _cfg: [adapter],  # type: ignore[list-item]
    )

    await sup.start()
    await asyncio.wait_for(adapter.started.wait(), timeout=1.0)
    snapshot = sup.snapshot()
    assert snapshot["adapters"]["stub-adapter"]["state"] == "running"

    await sup.stop()
    assert adapter.stopped.is_set()
    snapshot_after = sup.snapshot()
    assert snapshot_after["adapters"]["stub-adapter"]["state"] == "stopped"


# ---------------------------------------------------------------------------
# restart-on-crash with exponential backoff
# ---------------------------------------------------------------------------


async def test_supervisor_restarts_on_crash_with_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter crashes twice, then stabilises. The supervisor emits
    ``adapter.error`` on each crash with monotonically doubling
    ``retry_after_s`` and bumps ``restart_count``."""

    channels = tmp_path / "channels.yaml"
    channels.write_text("adapter_supervisor_port: 0\nadapters: []\n", encoding="utf-8")

    # Speed up the backoff window so the test doesn't sleep for seconds.
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _fast_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("adminme.daemons.adapter_supervisor.asyncio.sleep", _fast_sleep)

    event_log = _FakeEventLog()
    adapter = _CrashThenStableAdapter()
    sup = AdapterSupervisor(
        config_path=channels,
        ctx_factory=_make_ctx_factory(event_log),
        adapter_loader=lambda _cfg: [adapter],  # type: ignore[list-item]
    )
    await sup.start()

    # Wait for the third start (the stable one) to be reached.
    for _ in range(200):
        if adapter.start_count >= 3:
            break
        await asyncio.sleep(0.01)
    assert adapter.start_count >= 3

    snapshot = sup.snapshot()
    managed = snapshot["adapters"]["crashy-adapter"]
    assert managed["restart_count"] >= 2

    # adapter.error events emitted with doubling retry_after_s.
    error_calls = [c for c in event_log.calls if c.get("type") == "adapter.error"]
    assert len(error_calls) >= 2
    first_retry = error_calls[0]["payload"]["retry_after_s"]
    second_retry = error_calls[1]["payload"]["retry_after_s"]
    assert first_retry == int(INITIAL_BACKOFF_S)
    assert second_retry == int(INITIAL_BACKOFF_S * 2)
    # Backoff scheduled at least once with the initial window.
    assert any(abs(s - INITIAL_BACKOFF_S) < 1e-6 for s in sleeps)

    await sup.stop()


# ---------------------------------------------------------------------------
# constants live under the sane bounds the prompt declares
# ---------------------------------------------------------------------------


def test_supervisor_backoff_constants() -> None:
    assert INITIAL_BACKOFF_S == 1.0
    assert MAX_BACKOFF_S == 300.0
    assert UPTIME_RESET_S == 300.0
