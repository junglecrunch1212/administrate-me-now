"""Integration test: bridge supervisor → mock Cat-B :class:`StateMirrorBridgeAdapter`
→ :class:`BridgeEventSink` → consumed end-to-end.

Pins the 11b-ii-α-1 on-ramp contract: ``BridgeSupervisor`` accepts a Cat-B
adapter satisfying the sibling :class:`StateMirrorBridgeAdapter` Protocol;
the adapter emits envelopes via the injected :class:`BridgeEventSink`; and
both production-path (``IngestClientBridgeSink`` → central :3337 ingest
router → real :class:`EventLog`) and central-deployment-fallback test path
(``EventLogBridgeSink`` → real :class:`EventLog` directly per F-8) close
the loop. Closes the on-ramp's "mock Cat-B adapter end-to-end through the
supervisor → emit → sink → consumed" stop condition per §1 item 4.

[D8] addition 2 (correlation_id / causation_id propagation): asserted
end-to-end on the ``EventLogBridgeSink`` path — the envelope's
correlation_id is passed through to ``EventLog.append`` as an explicit
kwarg and persists alongside the row.

Uses ``httpx.ASGITransport`` to wire the bridge ``IngestClient`` to the
central FastAPI app in-process (no sockets, no threads, no port binding)
mirroring the 11c-i ``test_bridge_ingest_roundtrip.py`` rig shape."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

import adminme.events.schemas.bridge  # noqa: F401  — registers bridge.enrolled@v1
from adminme.events.envelope import EventEnvelope
from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.products.bridge.app import create_app
from adminme.products.bridge.auth import DictMemberIdResolver
from adminme_bridge.adapters.base import (
    AdapterStatus,
    BridgeContext,
)
from adminme_bridge.adapters.sinks import (
    BridgeEventSink,
    EmitResult,
    EventLogBridgeSink,
    IngestClientBridgeSink,
)
from adminme_bridge.config import BridgeConfig
from adminme_bridge.ingest_client import IngestClient
from adminme_bridge.main import BridgeSupervisor

TEST_KEY = b"B" * 32


def _envelope(*, correlation_id: str | None = None) -> EventEnvelope:
    """Build a valid ``bridge.enrolled@v1`` envelope.

    Reuses 11c-i's `c1` registered schema so the central ingest router
    accepts the envelope without registering a new α-1 schema (which
    would be out-of-scope per §1 "pure infrastructure")."""
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id="tenant-a",
        type="bridge.enrolled",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test-state-mirror",
        source_account_id="mock-cat-b-node",
        owner_scope="private:m1",
        visibility_scope="private:m1",
        sensitivity="normal",
        correlation_id=correlation_id,
        payload={
            "member_id": "m1",
            "bridge_node_id": "mock-cat-b-node",
            "tailscale_identity": "m1@example.ts.net",
            "adapters_active": ["mock-cat-b"],
            "enrolled_at": "2026-05-19T12:00:00+00:00",
        },
    )


@dataclass
class _MockCatBAdapter:
    """Mock :class:`StateMirrorBridgeAdapter` for end-to-end emit tests.

    Satisfies the Protocol structurally — no inheritance. ``family``
    mirrors the central Cat-B ABC's discriminator at
    ``adminme/lib/adapters/external_state_mirror_base.py``. On ``start``
    emits one envelope via the injected sink (closing the supervisor →
    adapter → sink → consumer loop) and records the :class:`EmitResult`
    for the test to assert against; the task then returns cleanly so
    ``supervisor.stop()`` does not need to cancel it."""

    family: ClassVar[str] = "reminders"
    sink: BridgeEventSink | None = None
    envelope: EventEnvelope | None = None
    emit_result: EmitResult | None = None
    started: bool = False
    stopped: bool = False
    _status: AdapterStatus = field(default_factory=AdapterStatus)

    async def start(self, *, ctx: BridgeContext) -> None:
        assert self.sink is not None and self.envelope is not None
        self.started = True
        self.emit_result = await self.sink.emit(self.envelope)
        # Return cleanly — the supervisor's stop() handles done tasks.

    async def stop(self) -> None:
        self.stopped = True

    def status(self) -> AdapterStatus:
        return self._status


def _config() -> BridgeConfig:
    return BridgeConfig(
        member_id="m1",
        central_endpoint="http://central",
        tailscale_identity="m1@example.ts.net",
    )


@pytest.fixture
async def central_rig(tmp_path: Path):
    """Central FastAPI app + real EventLog wired in-process via ASGITransport.

    Mirrors the 11c-i ``test_bridge_ingest_roundtrip.py`` fixture so the
    α-1 integration test's production-path assertion runs through the
    same auth + owner_scope-stamping seams an α-2 deployment will hit."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    await bus.start()
    app = create_app()
    app.state.event_sink = EventWriter(log, bus)
    app.state.member_id_resolver = DictMemberIdResolver({"m1@example.ts.net": "m1"})
    app.state.ingest_count = 0
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://central"
    ) as http:
        ingest = IngestClient(
            central_endpoint_url="http://central",
            tailscale_identity_header="m1@example.ts.net",
            client=http,
        )
        try:
            yield {"log": log, "ingest": ingest}
        finally:
            await bus.stop()
            await log.close()


async def test_mock_cat_b_adapter_emits_via_ingest_client_sink_end_to_end(
    central_rig: dict[str, Any],
) -> None:
    """Production path: ``BridgeSupervisor`` hosts a mock Cat-B adapter that
    emits via :class:`IngestClientBridgeSink` → central :3337 ingest router
    → real :class:`EventLog`. Closes the on-ramp's primary stop condition
    end-to-end. The envelope lands with ``owner_scope`` stamped from the
    Tailscale identity per [§6.19] / memo 04 §2.2 (F-3 invariant inherited
    from 11c-i)."""
    log: EventLog = central_rig["log"]
    ingest: IngestClient = central_rig["ingest"]
    sink = IngestClientBridgeSink(ingest)
    envelope = _envelope(correlation_id="cor-α1-prod")
    adapter = _MockCatBAdapter(sink=sink, envelope=envelope)

    supervisor = BridgeSupervisor(
        config=_config(),
        ingest_client=ingest,
        adapters=[adapter],
    )
    await supervisor.start()
    # Yield until the adapter's emit-and-return task completes.
    for _ in range(50):
        await asyncio.sleep(0)
        if adapter.emit_result is not None:
            break
    await supervisor.stop()

    assert adapter.started is True
    assert adapter.stopped is True
    assert adapter.emit_result is not None
    assert adapter.emit_result.ok is True, adapter.emit_result.error
    assert adapter.emit_result.event_id is not None

    stored = await log.get(adapter.emit_result.event_id)
    assert stored is not None
    assert stored["type"] == "bridge.enrolled"
    # F-3 inherited: owner_scope was stamped by the central router from
    # the Tailscale-resolved identity — the on-ramp does not weaken this.
    assert stored["owner_scope"] == "private:m1"
    assert stored["source_adapter"] == "test-state-mirror"
    assert stored["payload"]["bridge_node_id"] == "mock-cat-b-node"


async def test_mock_cat_b_adapter_emits_via_event_log_sink_for_test_fixture(
    tmp_path: Path,
) -> None:
    """Central-deployment-fallback path (F-8): mock Cat-B adapter emits via
    :class:`EventLogBridgeSink` wired directly to an in-process
    :class:`EventLog`; the envelope appends with ``correlation_id`` passed
    through as an explicit kwarg per [D8] addition 2. NOT a substitute for
    the production HTTP path (F-8 docstring on the sink) — this path exists
    so tests + central-deployment fallback can exercise the sink contract
    without standing up the central FastAPI app."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    await bus.start()
    try:
        sink = EventLogBridgeSink(EventWriter(log, bus))
        envelope = _envelope(correlation_id="cor-α1-fallback")
        adapter = _MockCatBAdapter(sink=sink, envelope=envelope)

        ingest_for_supervisor_only = IngestClient(
            central_endpoint_url="http://unused",
            tailscale_identity_header="m1@example.ts.net",
            client=httpx.AsyncClient(),
        )
        try:
            supervisor = BridgeSupervisor(
                config=_config(),
                ingest_client=ingest_for_supervisor_only,
                adapters=[adapter],
            )
            await supervisor.start()
            for _ in range(50):
                await asyncio.sleep(0)
                if adapter.emit_result is not None:
                    break
            await supervisor.stop()
        finally:
            await ingest_for_supervisor_only.aclose()

        assert adapter.started is True
        assert adapter.emit_result is not None
        assert adapter.emit_result.ok is True, adapter.emit_result.error
        assert adapter.emit_result.event_id is not None

        stored = await log.get(adapter.emit_result.event_id)
        assert stored is not None
        assert stored["type"] == "bridge.enrolled"
        # [D8] addition 2: correlation_id flowed envelope → sink → kwarg → row.
        assert stored["correlation_id"] == "cor-α1-fallback"
        # F-8: EventLogBridgeSink writes the envelope's declared owner_scope
        # directly (no central router stamping in this path) — the sink does
        # not gain Tailscale-identity binding, hence "tests + central-deployment
        # fallback ONLY" per the sink's docstring.
        assert stored["owner_scope"] == "private:m1"
    finally:
        await bus.stop()
        await log.close()
