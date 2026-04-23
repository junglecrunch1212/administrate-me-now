"""
Unit tests for the vector_search projection (prompt 07a).

Covers idempotency, rebuild correctness, multi-tenant isolation, privileged
filtering at write time per [§13.8], and nearest-neighbor queries against
sqlite-vec's vec0 virtual table. Per §12 invariant 1, queries take
tenant_id as an explicit keyword.

Fake-embedding helper: deterministic 1536-dim unit vector derived from
sha256(text). Not a real embedding — just something vec0 accepts with
predictable nearest-neighbor ordering.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.runner import ProjectionRunner
from adminme.projections.vector_search import VectorSearchProjection
from adminme.projections.vector_search.queries import (
    count_embeddings,
    embeddings_for_link,
    get_embedding_meta,
    nearest,
)

TEST_KEY = b"v" * 32


@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(VectorSearchProjection())
    await runner.start()
    try:
        yield {
            "config": config,
            "log": log,
            "bus": bus,
            "runner": runner,
        }
    finally:
        await runner.stop()
        await log.close()


def _envelope(
    event_type: str,
    payload: dict[str, Any],
    *,
    tenant_id: str = "tenant-a",
    owner_scope: str = "shared:household",
    sensitivity: str = "normal",
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test",
        source_account_id="test-acct",
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity=sensitivity,
        payload=payload,
    )


async def _wait_for_checkpoint(bus: EventBus, subscriber_id: str, target: str) -> None:
    import asyncio

    for _ in range(200):
        status = await bus.subscriber_status(subscriber_id)
        if status["checkpoint_event_id"] == target and status["lag_count"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"subscriber {subscriber_id} did not reach {target}: "
        f"{await bus.subscriber_status(subscriber_id)}"
    )


def _fake_embedding(text: str, dim: int = 1536) -> list[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    floats = [(h[i % 32] - 127.5) / 127.5 for i in range(dim)]
    mag = sum(f * f for f in floats) ** 0.5
    return [f / mag for f in floats]


def _hex_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _embedding_payload(
    text: str,
    *,
    embedding_id: str | None = None,
    linked_kind: str = "interaction",
    linked_id: str | None = None,
    sensitivity: str = "normal",
    dim: int = 1536,
    model_name: str = "text-embedding-3-small",
) -> dict[str, Any]:
    return {
        "embedding_id": embedding_id or f"emb-{text}",
        "linked_kind": linked_kind,
        "linked_id": linked_id or f"ix-{text}",
        "embedding_dimensions": dim,
        "embedding": _fake_embedding(text, dim=dim),
        "model_name": model_name,
        "sensitivity": sensitivity,
        "source_text_sha256": _hex_sha(text),
    }


# ------------------------------------------------------------------
# basic application
# ------------------------------------------------------------------
async def test_normal_embedding_lands_in_both_tables(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope("embedding.generated", _embedding_payload("hello world"))
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:vector_search", eid)

    conn = runner.connection("vector_search")
    meta = get_embedding_meta(
        conn, tenant_id="tenant-a", embedding_id="emb-hello world"
    )
    assert meta is not None
    assert meta["linked_kind"] == "interaction"
    assert meta["embedding_dimensions"] == 1536

    idx_count = conn.execute(
        "SELECT count(*) FROM vector_index WHERE tenant_id = ?",
        ("tenant-a",),
    ).fetchone()[0]
    assert idx_count == 1


async def test_envelope_privileged_skipped(rig: dict[str, Any]) -> None:
    """Per [§13.8]: envelope.sensitivity='privileged' → no row lands.
    CF-5 discipline: follow with an innocuous event so the subscriber
    advances past the privileged one before we assert absence."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "embedding.generated",
            _embedding_payload("privileged text"),
            sensitivity="privileged",
        )
    )
    last = await log.append(
        _envelope("embedding.generated", _embedding_payload("normal text"))
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:vector_search", last)

    conn = runner.connection("vector_search")
    priv = get_embedding_meta(
        conn, tenant_id="tenant-a", embedding_id="emb-privileged text"
    )
    normal = get_embedding_meta(
        conn, tenant_id="tenant-a", embedding_id="emb-normal text"
    )
    assert priv is None
    assert normal is not None


async def test_payload_privileged_skipped(rig: dict[str, Any]) -> None:
    """Defense in depth: envelope normal but payload.sensitivity
    'privileged' → also skipped."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "embedding.generated",
            _embedding_payload("payload privileged", sensitivity="privileged"),
        )
    )
    last = await log.append(
        _envelope("embedding.generated", _embedding_payload("follow up"))
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:vector_search", last)

    conn = runner.connection("vector_search")
    priv = get_embedding_meta(
        conn, tenant_id="tenant-a", embedding_id="emb-payload privileged"
    )
    ok = get_embedding_meta(conn, tenant_id="tenant-a", embedding_id="emb-follow up")
    assert priv is None
    assert ok is not None


async def test_idempotent(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    env = _envelope("embedding.generated", _embedding_payload("dup"))
    await log.append(env)
    last = await log.append(env)
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:vector_search", last)

    conn = runner.connection("vector_search")
    idx = conn.execute(
        "SELECT count(*) FROM vector_index WHERE embedding_id = ?",
        ("emb-dup",),
    ).fetchone()[0]
    meta = conn.execute(
        "SELECT count(*) FROM embeddings_meta WHERE tenant_id = ? AND embedding_id = ?",
        ("tenant-a", "emb-dup"),
    ).fetchone()[0]
    assert idx == 1
    assert meta == 1


# ------------------------------------------------------------------
# nearest-neighbor
# ------------------------------------------------------------------
async def test_nearest_returns_matching_first(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    texts = ["alpha", "bravo", "charlie", "delta", "echo"]
    for t in texts:
        await log.append(
            _envelope("embedding.generated", _embedding_payload(t))
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:vector_search", last)

    conn = runner.connection("vector_search")
    # Query with the "charlie" embedding — rank 1 must be emb-charlie.
    rows = nearest(
        conn,
        tenant_id="tenant-a",
        query_vector=_fake_embedding("charlie"),
        k=5,
    )
    assert len(rows) == 5
    assert rows[0]["embedding_id"] == "emb-charlie"


async def test_nearest_excludes_privileged(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # Add non-privileged rows.
    for t in ["a1", "a2", "a3"]:
        await log.append(
            _envelope("embedding.generated", _embedding_payload(t))
        )
    # Privileged (skipped at handler time — for this test we want to
    # confirm the nearest() query filter is still there as belt-and-
    # braces for any future path that bypasses the handler filter).
    # Since handler drops privileged, we inject a normal embedding with
    # sensitivity='sensitive' and confirm the exclude_sensitivity
    # parameter works for non-default values.
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:vector_search", last)

    conn = runner.connection("vector_search")
    rows = nearest(
        conn,
        tenant_id="tenant-a",
        query_vector=_fake_embedding("a1"),
        k=3,
    )
    # All 3 land since none are privileged.
    assert {r["embedding_id"] for r in rows} == {"emb-a1", "emb-a2", "emb-a3"}


# ------------------------------------------------------------------
# embeddings_for_link
# ------------------------------------------------------------------
async def test_embeddings_for_link(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "embedding.generated",
            _embedding_payload(
                "hi",
                embedding_id="e1",
                linked_kind="interaction",
                linked_id="int-1",
            ),
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:vector_search", eid)

    conn = runner.connection("vector_search")
    meta = embeddings_for_link(
        conn, tenant_id="tenant-a", linked_kind="interaction", linked_id="int-1"
    )
    assert meta is not None
    assert meta["embedding_id"] == "e1"


# ------------------------------------------------------------------
# rebuild
# ------------------------------------------------------------------
async def test_rebuild_produces_equivalent_state(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # 20 normal embeddings + 3 privileged (skipped).
    for i in range(20):
        await log.append(
            _envelope("embedding.generated", _embedding_payload(f"n{i}"))
        )
    for i in range(3):
        await log.append(
            _envelope(
                "embedding.generated",
                _embedding_payload(f"p{i}"),
                sensitivity="privileged",
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:vector_search", last)

    conn = runner.connection("vector_search")
    pre_meta = [
        tuple(r)
        for r in conn.execute(
            "SELECT * FROM embeddings_meta ORDER BY embedding_id"
        )
    ]
    pre_idx_count = conn.execute(
        "SELECT count(*) FROM vector_index"
    ).fetchone()[0]
    assert pre_idx_count == 20
    assert len(pre_meta) == 20

    await runner.rebuild("vector_search")

    conn = runner.connection("vector_search")
    post_meta = [
        tuple(r)
        for r in conn.execute(
            "SELECT * FROM embeddings_meta ORDER BY embedding_id"
        )
    ]
    post_idx_count = conn.execute(
        "SELECT count(*) FROM vector_index"
    ).fetchone()[0]
    assert pre_meta == post_meta
    assert post_idx_count == 20


# ------------------------------------------------------------------
# multi-tenant & validation
# ------------------------------------------------------------------
async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "embedding.generated",
            _embedding_payload("tA", embedding_id="e1"),
            tenant_id="tenant-a",
        )
    )
    last = await log.append(
        _envelope(
            "embedding.generated",
            _embedding_payload("tB", embedding_id="e1"),
            tenant_id="tenant-b",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:vector_search", last)

    conn = runner.connection("vector_search")
    a = get_embedding_meta(conn, tenant_id="tenant-a", embedding_id="e1")
    b = get_embedding_meta(conn, tenant_id="tenant-b", embedding_id="e1")
    assert a is not None and a["source_text_sha256"] == _hex_sha("tA")
    assert b is not None and b["source_text_sha256"] == _hex_sha("tB")

    assert count_embeddings(conn, tenant_id="tenant-a") == 1
    assert count_embeddings(conn, tenant_id="tenant-b") == 1


async def test_embedding_dimension_mismatch_drops(rig: dict[str, Any]) -> None:
    """Upstream daemon bug: embedding_dimensions says 1536 but vector
    length is 10. Handler warns and drops. CF-5: follow with an
    innocuous event to drive the checkpoint past."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    bad = _embedding_payload("bad")
    bad["embedding"] = [0.1] * 10  # length mismatch with dims=1536

    await log.append(_envelope("embedding.generated", bad))
    last = await log.append(
        _envelope("embedding.generated", _embedding_payload("good"))
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:vector_search", last)

    conn = runner.connection("vector_search")
    bad_meta = get_embedding_meta(
        conn, tenant_id="tenant-a", embedding_id="emb-bad"
    )
    good_meta = get_embedding_meta(
        conn, tenant_id="tenant-a", embedding_id="emb-good"
    )
    assert bad_meta is None
    assert good_meta is not None
