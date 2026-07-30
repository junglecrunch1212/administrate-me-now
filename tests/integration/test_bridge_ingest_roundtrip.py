"""Integration test: bridge ``IngestClient`` → central ``:3337`` service
→ real :class:`EventLog` → read-back.

Pins the contract that the L1-bridge end-to-end pipeline shipped by
11c-i actually closes a loop: a synthetic event posted by the bridge
daemon's HTTP client lands in the central event log with the
``owner_scope`` STAMPED from the Tailscale-resolved identity per
memo 04 §2.2 + F-3 (HARD).

Uses ``httpx.ASGITransport`` to wire the bridge ingest client to the
central FastAPI app in-process — no sockets, no threads, no port
binding. The :class:`EventLog` is a real SQLCipher database under
``tmp_path``."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.products.bridge.app import create_app
from adminme.products.bridge.auth import DictMemberIdResolver
from adminme_bridge.ingest_client import IngestClient
import adminme.events.schemas.bridge  # noqa: F401  — registers bridge.enrolled@v1

TEST_KEY = b"B" * 32


def _envelope_dict(
    *,
    owner_scope: str = "private:m1",
    bridge_node_id: str = "bridge-node-001",
) -> dict[str, Any]:
    return {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": "tenant-a",
        "type": "bridge.enrolled",
        "schema_version": 1,
        "occurred_at": EventEnvelope.now_utc_iso(),
        "source_adapter": "bridge",
        "source_account_id": bridge_node_id,
        "owner_scope": owner_scope,
        "visibility_scope": owner_scope,
        "sensitivity": "normal",
        "payload": {
            "member_id": "m1",
            "bridge_node_id": bridge_node_id,
            "tailscale_identity": "m1@example.ts.net",
            "adapters_active": ["apple-notes", "voice-notes"],
            "enrolled_at": "2026-05-18T12:00:00+00:00",
        },
    }


@pytest.fixture
async def rig(tmp_path: Path):
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
            yield {"log": log, "ingest": ingest, "app": app}
        finally:
            await bus.stop()
            await log.close()


async def test_bridge_ingest_roundtrip_writes_to_event_log(
    rig: dict[str, Any],
) -> None:
    """Synthetic ``bridge.enrolled@v1`` event from the bridge daemon's
    HTTP client lands in the central event log and is read-back-able by
    ``event_id``. Closes the L1-bridge contract at the integration
    seam."""
    log = rig["log"]
    ingest: IngestClient = rig["ingest"]

    result = await ingest.submit(_envelope_dict())
    assert result.ok is True, result.error
    assert result.status_code == 202
    assert result.event_id and isinstance(result.event_id, str)

    stored = await log.get(result.event_id)
    assert stored is not None
    assert stored["type"] == "bridge.enrolled"
    assert stored["owner_scope"] == "private:m1"
    assert stored["payload"]["bridge_node_id"] == "bridge-node-001"


async def test_bridge_ingest_roundtrip_owner_scope_stamped_from_identity(
    rig: dict[str, Any],
) -> None:
    """F-3 (HARD) end-to-end: the Tailscale-resolved identity is the
    SOLE source of truth for ``owner_scope`` — verified by the success
    path (matching claim accepted + persisted) AND by the rejection
    path (mismatched claim → 403, nothing in log)."""
    log = rig["log"]
    ingest: IngestClient = rig["ingest"]

    # 1. Matching claim → accepted; owner_scope persisted as private:m1.
    ok = await ingest.submit(_envelope_dict(owner_scope="private:m1"))
    assert ok.ok is True
    stored = await log.get(ok.event_id)
    assert stored is not None
    assert stored["owner_scope"] == "private:m1"

    pre_count = await log.count()

    # 2. Mismatched claim — pretending to be m2 — must be HARD 403 and
    #    nothing appended. F-3 silent-overwrite guard end-to-end.
    bad = await ingest.submit(_envelope_dict(owner_scope="private:m2"))
    assert bad.ok is False
    assert bad.status_code == 403
    assert "owner_scope_claim_mismatch" in (bad.error or "")
    assert await log.count() == pre_count


async def test_bridge_ingest_batch_partial_failure_rejects_whole_batch_end_to_end(
    rig: dict[str, Any],
) -> None:
    """A batch with one bad envelope (claim mismatch) at offset 1
    rejects the whole batch at the central router AND the bridge
    client's :class:`SubmitBatchResult` surfaces ``ok=False`` with the
    failing chunk offset. Pins the all-or-nothing semantics across the
    full bridge → central seam."""
    log = rig["log"]
    ingest: IngestClient = rig["ingest"]
    pre_count = await log.count()

    envelopes = [
        _envelope_dict(bridge_node_id="bridge-node-001"),
        _envelope_dict(
            bridge_node_id="bridge-node-002", owner_scope="private:m2"
        ),  # bad
        _envelope_dict(bridge_node_id="bridge-node-003"),
    ]
    result = await ingest.submit_batch(envelopes)
    assert result.ok is False
    assert result.error_chunk_offset == 0  # all three fit one chunk
    # Whole-batch reject — nothing landed in the event log.
    assert await log.count() == pre_count
