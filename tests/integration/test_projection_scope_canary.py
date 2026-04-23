"""
Scope canary (structural, prompt 05 level).

Per SYSTEM_INVARIANTS.md §6 invariant 4, every projection test ships a
canary that expects ``ScopeViolation`` on out-of-scope reads. Session /
ScopeViolation is prompt 08's deliverable; this test is structural for
now — it confirms privileged events land in each projection DB with
``sensitivity='privileged'`` set correctly so prompt 08 has somewhere to
hang the enforcement check.

Prompt 08 will extend each assertion with::

    with pytest.raises(ScopeViolation):
        read_as_other_member(...)
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

TEST_KEY = b"c" * 32


async def _wait_idle(bus: EventBus, subscriber_id: str) -> None:
    for _ in range(300):
        status = await bus.subscriber_status(subscriber_id)
        if status["lag_count"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"subscriber {subscriber_id} stayed lagged")


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


def _privileged_envelope(
    event_type: str,
    payload: dict[str, Any],
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id="tenant-a",
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test",
        source_account_id="test-acct",
        owner_scope="private:m1",
        visibility_scope="private:m1",
        sensitivity="privileged",
        payload=payload,
    )


async def test_parties_privileged_sensitivity_landed(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    eid = await log.append(
        _privileged_envelope(
            "party.created",
            {
                "party_id": "attorney-p1",
                "kind": "person",
                "display_name": "Attorney X",
                "sort_name": "Attorney X",
            },
        )
    )
    await bus.notify(eid)
    await _wait_idle(bus, "projection:parties")

    conn = runner.connection("parties")
    row = conn.execute(
        "SELECT sensitivity, owner_scope FROM parties WHERE party_id = ?",
        ("attorney-p1",),
    ).fetchone()
    assert row["sensitivity"] == "privileged"
    assert row["owner_scope"] == "private:m1"


async def test_interactions_privileged_sensitivity_landed(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    eid = await log.append(
        _privileged_envelope(
            "messaging.received",
            {
                "source_channel": "gmail",
                "from_identifier": "attorney@example.com",
                "to_identifier": "me@example.com",
                "received_at": "2026-04-10T10:00:00Z",
                "body_text": "privileged communication",
            },
        )
    )
    await bus.notify(eid)
    await _wait_idle(bus, "projection:interactions")

    conn = runner.connection("interactions")
    row = conn.execute(
        "SELECT sensitivity, owner_scope FROM interactions "
        "WHERE interaction_id = ?",
        (f"ix_{eid}",),
    ).fetchone()
    assert row["sensitivity"] == "privileged"
    assert row["owner_scope"] == "private:m1"


async def test_artifacts_privileged_sensitivity_landed(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    eid = await log.append(
        _privileged_envelope(
            "artifact.received",
            {
                "source": "test:uploader",
                "external_artifact_id": None,
                "mime_type": "application/pdf",
                "size_bytes": 2048,
                "filename": "contract.pdf",
                "sha256": "a" * 64,
                "artifact_ref": "artifact://" + ("a" * 64),
                "received_at": "2026-04-10T10:00:00Z",
            },
        )
    )
    await bus.notify(eid)
    await _wait_idle(bus, "projection:artifacts")

    conn = runner.connection("artifacts")
    row = conn.execute(
        "SELECT sensitivity, owner_scope FROM artifacts WHERE artifact_id = ?",
        (f"art_{eid}",),
    ).fetchone()
    assert row["sensitivity"] == "privileged"
    assert row["owner_scope"] == "private:m1"
