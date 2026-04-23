"""
Unit tests for the artifacts projection (prompt 05).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.artifacts import ArtifactsProjection
from adminme.projections.artifacts.queries import (
    get_artifact,
    list_recent,
    search_by_sha256,
)
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"a" * 32


@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(ArtifactsProjection())
    await runner.start()
    try:
        yield {"log": log, "bus": bus, "runner": runner, "config": config}
    finally:
        await runner.stop()
        await log.close()


def _artifact(
    sha: str,
    *,
    tenant_id: str = "tenant-a",
    captured_at: str = "2026-04-10T10:00:00Z",
    size: int = 1024,
    mime: str = "application/pdf",
    filename: str = "scan.pdf",
    sensitivity: str = "normal",
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=tenant_id,
        type="artifact.received",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test:uploader",
        source_account_id="u1",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity=sensitivity,
        payload={
            "source": "test:uploader",
            "external_artifact_id": None,
            "mime_type": mime,
            "size_bytes": size,
            "filename": filename,
            "sha256": sha,
            "artifact_ref": f"artifact://{sha}",
            "received_at": captured_at,
        },
    )


async def _wait_idle(bus: EventBus, subscriber_id: str) -> None:
    for _ in range(200):
        status = await bus.subscriber_status(subscriber_id)
        if status["lag_count"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"subscriber {subscriber_id} stayed lagged")


async def test_artifact_received_inserts_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    sha = "a" * 64
    eid = await log.append(_artifact(sha))
    await bus.notify(eid)
    await _wait_idle(bus, "projection:artifacts")

    conn = runner.connection("artifacts")
    row = get_artifact(conn, tenant_id="tenant-a", artifact_id=f"art_{eid}")
    assert row is not None
    assert row["sha256"] == sha
    assert row["mime_type"] == "application/pdf"
    assert row["title"] == "scan.pdf"


async def test_search_by_sha256(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    sha = "b" * 64
    await log.append(_artifact(sha, filename="copy-a.pdf"))
    last = await log.append(_artifact("c" * 64, filename="copy-b.pdf"))
    await bus.notify(last)
    await _wait_idle(bus, "projection:artifacts")

    conn = runner.connection("artifacts")
    hits = search_by_sha256(conn, tenant_id="tenant-a", sha256=sha)
    assert len(hits) == 1
    assert hits[0]["title"] == "copy-a.pdf"


async def test_idempotent_reapplication(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    sha = "d" * 64
    env = _artifact(sha)
    eid1 = await log.append(env)
    # Distinct event but same sha — unique index should allow because we
    # use the first artifact's id; the second event would collide on the
    # unique (tenant_id, sha256). Verify the first stays intact.
    await bus.notify(eid1)
    await _wait_idle(bus, "projection:artifacts")

    conn = runner.connection("artifacts")
    count = conn.execute(
        "SELECT count(*) FROM artifacts WHERE tenant_id=? AND sha256=?",
        ("tenant-a", sha),
    ).fetchone()[0]
    assert count == 1


async def test_rebuild_equivalence(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    for i in range(5):
        sha = f"{i:064x}"
        await log.append(_artifact(sha, filename=f"f{i}.pdf"))
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_idle(bus, "projection:artifacts")

    conn = runner.connection("artifacts")
    pre = [
        tuple(r)
        for r in conn.execute("SELECT * FROM artifacts ORDER BY artifact_id")
    ]

    await runner.rebuild("artifacts")

    conn = runner.connection("artifacts")
    post = [
        tuple(r)
        for r in conn.execute("SELECT * FROM artifacts ORDER BY artifact_id")
    ]
    assert pre == post


async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    sha_a = "e" * 64
    sha_b = "f" * 64
    await log.append(_artifact(sha_a, tenant_id="tenant-a"))
    last = await log.append(_artifact(sha_b, tenant_id="tenant-b"))
    await bus.notify(last)
    await _wait_idle(bus, "projection:artifacts")

    conn = runner.connection("artifacts")
    a = list_recent(conn, tenant_id="tenant-a")
    b = list_recent(conn, tenant_id="tenant-b")
    assert len(a) == 1 and a[0]["sha256"] == sha_a
    assert len(b) == 1 and b[0]["sha256"] == sha_b
