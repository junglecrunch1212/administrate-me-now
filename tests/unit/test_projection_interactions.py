"""
Unit tests for the interactions projection (prompt 05).

Covers both messaging channel_family events and telephony SMS, plus
rebuild correctness and tenant isolation.
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
from adminme.lib.session import Session, build_internal_session
from adminme.projections.interactions import InteractionsProjection
from adminme.projections.interactions.queries import (
    closeness_signals,
    recent_with,
    thread,
)
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"i" * 32


def _S(tenant_id: str = "tenant-a") -> Session:
    """Internal-actor Session for projection-read tests; carries tenant_id
    only. 08a + scope filtering use principal role so allowed_read accepts
    shared:household + private:<self> rows."""
    return build_internal_session("test_actor", "principal", tenant_id)



@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(InteractionsProjection())
    await runner.start()
    try:
        yield {"config": config, "log": log, "bus": bus, "runner": runner}
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


async def _wait_idle(bus: EventBus, subscriber_id: str) -> None:
    for _ in range(200):
        status = await bus.subscriber_status(subscriber_id)
        if status["lag_count"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"subscriber {subscriber_id} stayed lagged")


async def test_messaging_received_inserts_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    eid = await log.append(
        _envelope(
            "messaging.received",
            {
                "source_channel": "gmail",
                "from_identifier": "a@example.com",
                "to_identifier": "b@example.com",
                "thread_id": "t1",
                "subject": "Hi",
                "body_text": "hello world",
                "received_at": "2026-04-10T10:00:00Z",
            },
        )
    )
    await bus.notify(eid)
    await _wait_idle(bus, "projection:interactions")

    conn = runner.connection("interactions")
    row = conn.execute(
        "SELECT * FROM interactions WHERE tenant_id=? AND interaction_id=?",
        ("tenant-a", f"ix_{eid}"),
    ).fetchone()
    assert row is not None
    assert row["direction"] == "inbound"
    assert row["channel_family"] == "messaging"
    assert row["channel_specific"] == "gmail"
    assert row["thread_id"] == "t1"
    parts = conn.execute(
        "SELECT party_id, role FROM interaction_participants "
        "WHERE tenant_id=? AND interaction_id=? ORDER BY role",
        ("tenant-a", f"ix_{eid}"),
    ).fetchall()
    assert {(p["party_id"], p["role"]) for p in parts} == {
        ("a@example.com", "from"),
        ("b@example.com", "to"),
    }


async def test_messaging_sent_is_outbound(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    eid = await log.append(
        _envelope(
            "messaging.sent",
            {
                "source_channel": "imessage",
                "to_identifier": "+15551234567",
                "thread_id": "t1",
                "subject": None,
                "body_text": "out",
                "sent_at": "2026-04-10T11:00:00Z",
                "delivery_status": "sent",
            },
        )
    )
    await bus.notify(eid)
    await _wait_idle(bus, "projection:interactions")

    conn = runner.connection("interactions")
    row = conn.execute(
        "SELECT direction FROM interactions WHERE interaction_id=?",
        (f"ix_{eid}",),
    ).fetchone()
    assert row["direction"] == "outbound"


async def test_telephony_sms_received(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    eid = await log.append(
        _envelope(
            "telephony.sms_received",
            {
                "from_number": "+15551111111",
                "to_number": "+15552222222",
                "body": "ping",
                "received_at": "2026-04-10T12:00:00Z",
            },
        )
    )
    await bus.notify(eid)
    await _wait_idle(bus, "projection:interactions")

    conn = runner.connection("interactions")
    row = conn.execute(
        "SELECT channel_family, channel_specific FROM interactions "
        "WHERE interaction_id=?",
        (f"ix_{eid}",),
    ).fetchone()
    assert row["channel_family"] == "telephony"
    assert row["channel_specific"] == "sms"


async def test_recent_with_window(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    from datetime import datetime, timedelta, timezone

    recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    old = (datetime.now(timezone.utc) - timedelta(days=60)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    for when in (recent, old):
        await log.append(
            _envelope(
                "messaging.received",
                {
                    "source_channel": "gmail",
                    "from_identifier": "a@example.com",
                    "to_identifier": "b@example.com",
                    "received_at": when,
                },
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_idle(bus, "projection:interactions")

    conn = runner.connection("interactions")
    got = recent_with(conn, _S("tenant-a"), party_id="a@example.com", days=30)
    assert len(got) == 1
    assert got[0]["occurred_at"] == recent


async def test_thread_returns_ordered_by_occurred_at(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    for when in ["2026-04-10T09:00:00Z", "2026-04-10T10:00:00Z"]:
        await log.append(
            _envelope(
                "messaging.received",
                {
                    "source_channel": "gmail",
                    "from_identifier": "a@example.com",
                    "to_identifier": "b@example.com",
                    "thread_id": "t9",
                    "received_at": when,
                },
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_idle(bus, "projection:interactions")

    conn = runner.connection("interactions")
    rows = thread(conn, _S("tenant-a"), thread_id="t9")
    assert len(rows) == 2
    assert rows[0]["occurred_at"] == "2026-04-10T09:00:00Z"
    assert rows[1]["occurred_at"] == "2026-04-10T10:00:00Z"


async def test_rebuild_equivalence(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    for i in range(8):
        await log.append(
            _envelope(
                "messaging.received",
                {
                    "source_channel": "gmail",
                    "from_identifier": f"p{i}@example.com",
                    "to_identifier": "me@example.com",
                    "received_at": f"2026-04-10T10:{i:02d}:00Z",
                },
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_idle(bus, "projection:interactions")

    conn = runner.connection("interactions")
    pre = conn.execute(
        "SELECT * FROM interactions ORDER BY interaction_id"
    ).fetchall()
    pre_parts = conn.execute(
        "SELECT * FROM interaction_participants "
        "ORDER BY interaction_id, party_id, role"
    ).fetchall()
    pre_dump = ([tuple(r) for r in pre], [tuple(r) for r in pre_parts])

    await runner.rebuild("interactions")

    conn = runner.connection("interactions")
    post = conn.execute(
        "SELECT * FROM interactions ORDER BY interaction_id"
    ).fetchall()
    post_parts = conn.execute(
        "SELECT * FROM interaction_participants "
        "ORDER BY interaction_id, party_id, role"
    ).fetchall()
    post_dump = ([tuple(r) for r in post], [tuple(r) for r in post_parts])
    assert pre_dump == post_dump


async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    await log.append(
        _envelope(
            "messaging.received",
            {
                "source_channel": "gmail",
                "from_identifier": "a@example.com",
                "to_identifier": "b@example.com",
                "received_at": "2026-04-10T10:00:00Z",
            },
            tenant_id="tenant-a",
        )
    )
    last = await log.append(
        _envelope(
            "messaging.received",
            {
                "source_channel": "gmail",
                "from_identifier": "a@example.com",
                "to_identifier": "b@example.com",
                "received_at": "2026-04-10T11:00:00Z",
            },
            tenant_id="tenant-b",
        )
    )
    await bus.notify(last)
    await _wait_idle(bus, "projection:interactions")

    conn = runner.connection("interactions")
    a = conn.execute(
        "SELECT count(*) FROM interactions WHERE tenant_id=?", ("tenant-a",)
    ).fetchone()[0]
    b = conn.execute(
        "SELECT count(*) FROM interactions WHERE tenant_id=?", ("tenant-b",)
    ).fetchone()[0]
    assert a == 1 and b == 1


async def test_closeness_signals_counts(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # One inbound, two outbound for a@example.com.
    await log.append(
        _envelope(
            "messaging.received",
            {
                "source_channel": "gmail",
                "from_identifier": "a@example.com",
                "to_identifier": "me@example.com",
                "received_at": "2026-04-10T10:00:00Z",
            },
        )
    )
    for i in range(2):
        await log.append(
            _envelope(
                "messaging.sent",
                {
                    "source_channel": "gmail",
                    "to_identifier": "a@example.com",
                    "sent_at": f"2026-04-10T1{i + 1}:00:00Z",
                    "delivery_status": "sent",
                },
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_idle(bus, "projection:interactions")

    conn = runner.connection("interactions")
    signals = closeness_signals(conn, _S("tenant-a"), party_id="a@example.com",
        since_iso="2026-01-01T00:00:00Z",
    )
    assert signals["inbound_count"] == 1
    assert signals["outbound_count"] == 2
    assert signals["last_contact_iso"] is not None
