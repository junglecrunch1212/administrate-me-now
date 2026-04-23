"""
Integration test: rebuild correctness across all three projections.

Per SYSTEM_INVARIANTS.md §2 invariant 1: ``projection.rebuild(name)``
truncates the projection's tables and replays from event 0 producing
state equivalent to the live cursor. This test populates 500 mixed events
across all three projections, waits for catch-up, snapshots the row data,
calls rebuild per projection, and asserts byte-equivalence.
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
from adminme.projections.interactions import InteractionsProjection
from adminme.projections.parties import PartiesProjection
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"z" * 32


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


async def _wait_idle(bus: EventBus, subscriber_id: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await bus.subscriber_status(subscriber_id)
        if status["lag_count"] == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"subscriber {subscriber_id} stayed lagged: {status}")


def _snapshot(conn: Any, tables: list[str]) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    for t in tables:
        # Sort by rowid to get a deterministic order; since we compare
        # structurally, we sort each table's rows as tuples afterward.
        rows = [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")]
        out[t] = sorted(rows)
    return out


@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(PartiesProjection())
    runner.register(InteractionsProjection())
    runner.register(ArtifactsProjection())
    await runner.start()
    try:
        yield {"log": log, "bus": bus, "runner": runner}
    finally:
        await runner.stop()
        await log.close()


async def test_rebuild_preserves_all_three_projections(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # Populate ~500 mixed events.
    # 100 parties; 100 identifiers; 50 memberships; 50 relationships;
    # 150 messaging; 30 telephony; 20 artifacts.
    for i in range(100):
        await log.append(
            _envelope("party.created", {
                "party_id": f"p{i:03d}",
                "kind": "person",
                "display_name": f"Name{i}",
                "sort_name": f"Name{i:03d}",
            })
        )
    for i in range(100):
        await log.append(
            _envelope("identifier.added", {
                "identifier_id": f"id{i:03d}",
                "party_id": f"p{i:03d}",
                "kind": "email",
                "value": f"p{i}@example.com",
                "value_normalized": f"p{i}@example.com",
                "verified": False,
                "primary_for_kind": True,
            })
        )
    # household + 50 memberships
    await log.append(
        _envelope("party.created", {
            "party_id": "hh",
            "kind": "household",
            "display_name": "HH",
            "sort_name": "HH",
        })
    )
    for i in range(50):
        await log.append(
            _envelope("membership.added", {
                "membership_id": f"mem{i:03d}",
                "party_id": f"p{i:03d}",
                "parent_party_id": "hh",
                "role": "principal",
            })
        )
    for i in range(50):
        await log.append(
            _envelope("relationship.added", {
                "relationship_id": f"rel{i:03d}",
                "party_a": f"p{i:03d}",
                "party_b": f"p{(i + 1) % 100:03d}",
                "label": "sibling",
                "direction": "mutual",
            })
        )
    for i in range(150):
        if i % 2 == 0:
            await log.append(
                _envelope(
                    "messaging.received",
                    {
                        "source_channel": "gmail",
                        "from_identifier": f"p{i % 100}@example.com",
                        "to_identifier": "me@example.com",
                        "thread_id": f"t{i % 10}",
                        "received_at": f"2026-04-10T10:{i % 60:02d}:00Z",
                    },
                )
            )
        else:
            await log.append(
                _envelope(
                    "messaging.sent",
                    {
                        "source_channel": "gmail",
                        "to_identifier": f"p{i % 100}@example.com",
                        "thread_id": f"t{i % 10}",
                        "sent_at": f"2026-04-10T10:{i % 60:02d}:00Z",
                        "delivery_status": "sent",
                    },
                )
            )
    for i in range(30):
        await log.append(
            _envelope(
                "telephony.sms_received",
                {
                    "from_number": f"+1555{i:07d}",
                    "to_number": "+15550000000",
                    "body": f"sms {i}",
                    "received_at": f"2026-04-10T11:{i % 60:02d}:00Z",
                },
            )
        )
    for i in range(20):
        await log.append(
            _envelope(
                "artifact.received",
                {
                    "source": "test:uploader",
                    "external_artifact_id": None,
                    "mime_type": "application/pdf",
                    "size_bytes": 1024 * (i + 1),
                    "filename": f"doc{i}.pdf",
                    "sha256": f"{i:064x}",
                    "artifact_ref": f"artifact://{i:064x}",
                    "received_at": f"2026-04-10T12:{i % 60:02d}:00Z",
                },
            )
        )

    latest = await log.latest_event_id()
    assert latest is not None
    await bus.notify(latest)

    for sid in ("projection:parties", "projection:interactions", "projection:artifacts"):
        await _wait_idle(bus, sid, timeout=15.0)

    parties_tables = ["parties", "identifiers", "memberships", "relationships"]
    interactions_tables = [
        "interactions",
        "interaction_participants",
        "interaction_attachments",
    ]
    artifacts_tables = ["artifacts", "artifact_links"]

    pre_parties = _snapshot(runner.connection("parties"), parties_tables)
    pre_interactions = _snapshot(
        runner.connection("interactions"), interactions_tables
    )
    pre_artifacts = _snapshot(runner.connection("artifacts"), artifacts_tables)

    await runner.rebuild("parties")
    await runner.rebuild("interactions")
    await runner.rebuild("artifacts")

    post_parties = _snapshot(runner.connection("parties"), parties_tables)
    post_interactions = _snapshot(
        runner.connection("interactions"), interactions_tables
    )
    post_artifacts = _snapshot(runner.connection("artifacts"), artifacts_tables)

    assert pre_parties == post_parties
    assert pre_interactions == post_interactions
    assert pre_artifacts == post_artifacts

    # Sanity: rebuilt state has non-zero rows.
    assert len(post_parties["parties"]) == 101
    assert len(post_interactions["interactions"]) == 180  # 150 msg + 30 sms
    assert len(post_artifacts["artifacts"]) == 20
