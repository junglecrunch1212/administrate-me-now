"""Unit tests for ``adminme_bridge.main``.

11c-i ships the daemon shell only; concrete adapters land in 11c-ii.
These tests pin the supervisor lifecycle + SIGTERM handling so the
shell stays load-bearing for the 11c-ii adapter integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest

from adminme_bridge.adapters.base import AdapterStatus, BridgeContext
from adminme_bridge.config import BridgeConfig
from adminme_bridge.main import BridgeSupervisor, _run_until_signal, configured_packs
from adminme_bridge.packs import PackLoadError


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


@dataclass
class _RecordingStateMirrorAdapter:
    """Recording :class:`StateMirrorBridgeAdapter` for supervisor wire-up.

    11b-ii-α-1 Cat-B sibling of :class:`_RecordingAdapter`. ``family``
    mirrors the central Cat-B ABC's discriminator at
    ``adminme/lib/adapters/external_state_mirror_base.py``. Used to pin
    that the supervisor's adapter loop dispatches uniformly to both
    sibling Protocols (the on-ramp's stop condition per
    ``docs/10-split-memo-11b-ii-alpha.md`` §1)."""

    family: ClassVar[str] = "reminders"
    started: bool = False
    stopped: bool = False
    last_ctx: BridgeContext | None = None
    _status: AdapterStatus = field(default_factory=AdapterStatus)

    async def start(self, *, ctx: BridgeContext) -> None:
        self.started = True
        self.last_ctx = ctx
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
        central_endpoint="http://127.0.0.1:3330",
        tailscale_identity="m1@example.ts.net",
    )


def test_configured_builtin_knowledge_packs_are_real_and_opt_in(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        member_id="m1",
        tenant_id="tenant-one",
        central_endpoint="http://127.0.0.1:3330",
        tailscale_identity="m1@example.ts.net",
        packs_dir=str(tmp_path / "installed-packs"),
        enabled_packs=[
            "knowledge-apple-notes",
            "knowledge-apple-voice-memos",
            "knowledge-obsidian",
        ],
    )
    handles = configured_packs(config)
    assert [handle.name for handle in handles] == config.enabled_packs
    supervisor = BridgeSupervisor(
        config=config,
        ingest_client=_FakeIngestClient(),  # type: ignore[arg-type]
        packs=handles,
    )
    assert [adapter.name for adapter in supervisor.adapters] == config.enabled_packs

    config.enabled_packs = ["not-a-real-pack"]
    with pytest.raises(PackLoadError, match="not found"):
        configured_packs(config)


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


async def test_bridge_supervisor_accepts_mock_state_mirror_adapter() -> None:
    """11b-ii-α-1 supervisor wire-up: ``BridgeSupervisor.adapters: list[BridgeAdapter]``
    accepts a Cat-B :class:`StateMirrorBridgeAdapter` mock end-to-end, satisfying
    the on-ramp's stop condition. The Cat-D path (11c-i regression invariant per
    §4 A-6) is preserved — a list with only a Cat-D adapter still starts cleanly,
    and a list with only a Cat-B adapter starts via the same loop. The supervisor's
    snapshot reports each adapter's status uniformly regardless of Protocol kind."""
    cat_b = _RecordingStateMirrorAdapter()
    supervisor = BridgeSupervisor(
        config=_config(),
        ingest_client=_FakeIngestClient(),  # type: ignore[arg-type]
        adapters=[cat_b],
    )
    await supervisor.start()
    await asyncio.sleep(0)
    try:
        assert cat_b.started is True
        assert cat_b.last_ctx is not None
        assert cat_b.last_ctx.member_id == "m1"
        # ``family`` is the Cat-B discriminator the loader's
        # ``STATE_MIRROR_SUBKIND`` allow-list ultimately surfaces.
        assert cat_b.family == "reminders"
        snap = supervisor.snapshot()
        assert snap["supervisor"]["adapter_count"] == 1
        assert snap["adapters"][0]["state"] == "starting"
    finally:
        await supervisor.stop()
    assert cat_b.stopped is True


async def test_bridge_supervisor_start_dispatches_to_both_cat_d_and_cat_b_adapters_in_one_run() -> (
    None
):
    """The on-ramp's primary win: a single supervisor instance hosts BOTH a
    Cat-D :class:`KnowledgeAdapter` and a Cat-B :class:`StateMirrorBridgeAdapter`
    in one run. The start loop dispatches to each adapter's ``start(ctx=...)``
    uniformly (both Protocols share the signature per
    ``bridge/adminme_bridge/adapters/base.py``); the stop loop cleanly shuts
    both down. Existing 11c-i Cat-D behavior is preserved alongside the new
    Cat-B path — no regression on §4 A-6."""
    cat_d = _RecordingAdapter()
    cat_b = _RecordingStateMirrorAdapter()
    supervisor = BridgeSupervisor(
        config=_config(),
        ingest_client=_FakeIngestClient(),  # type: ignore[arg-type]
        adapters=[cat_d, cat_b],
    )
    await supervisor.start()
    await asyncio.sleep(0)
    try:
        assert cat_d.started is True
        assert cat_b.started is True
        assert cat_d.last_ctx is not None and cat_d.last_ctx.member_id == "m1"
        assert cat_b.last_ctx is not None and cat_b.last_ctx.member_id == "m1"
        snap = supervisor.snapshot()
        assert snap["supervisor"]["adapter_count"] == 2
    finally:
        await supervisor.stop()
    assert cat_d.stopped is True
    assert cat_b.stopped is True
