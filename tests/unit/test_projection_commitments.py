"""
Unit tests for the commitments projection (prompt 06).

Covers idempotency, rebuild correctness, multi-tenant isolation, and each
of the nine event-type state transitions. Per §12 invariant 1, queries
take tenant_id as an explicit keyword.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.commitments import CommitmentsProjection
from adminme.projections.commitments.queries import (
    by_party,
    by_source_interaction,
    get_commitment,
    open_for_member,
    pending_approval,
)
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"c" * 32


@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(CommitmentsProjection())
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


def _proposed_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "commitment_id": "c1",
        "kind": "reply",
        "owed_by_member_id": "m1",
        "owed_to_party_id": "p-ext",
        "text_summary": "Reply to external party",
        "urgency": "this_week",
        "confidence": 0.9,
        "strength": "confident",
        "source_interaction_id": "ix-1",
    }
    base.update(overrides)
    return base


async def test_commitment_proposed_inserts_pending(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(_envelope("commitment.proposed", _proposed_payload()))
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:commitments", eid)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["status"] == "pending"
    assert row["owed_by_party"] == "m1"
    assert row["owed_to_party"] == "p-ext"
    assert row["kind"] == "reply"
    assert row["description"] == "Reply to external party"
    assert row["proposed_at"] is not None


async def test_commitment_confirmed_sets_confirmed_fields(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("commitment.proposed", _proposed_payload()))
    last = await log.append(
        _envelope(
            "commitment.confirmed",
            {
                "commitment_id": "c1",
                "confirmed_by_member_id": "m1",
                "confirmed_at": "2026-04-23T12:00:00Z",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["confirmed_at"] == "2026-04-23T12:00:00Z"
    assert row["confirmed_by"] == "m1"


async def test_commitment_completed_sets_done(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("commitment.proposed", _proposed_payload()))
    last = await log.append(
        _envelope(
            "commitment.completed",
            {
                "commitment_id": "c1",
                "completed_at": "2026-04-23T14:00:00Z",
                "completed_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["status"] == "done"
    assert row["completed_at"] == "2026-04-23T14:00:00Z"


async def test_commitment_dismissed_sets_cancelled(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("commitment.proposed", _proposed_payload()))
    last = await log.append(
        _envelope(
            "commitment.dismissed",
            {
                "commitment_id": "c1",
                "dismissed_at": "2026-04-23T14:00:00Z",
                "dismissed_by_party_id": "m1",
                "reason": "not a real commitment",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["status"] == "cancelled"


async def test_commitment_edited_updates_only_listed_fields(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("commitment.proposed", _proposed_payload()))
    last = await log.append(
        _envelope(
            "commitment.edited",
            {
                "commitment_id": "c1",
                "edited_at": "2026-04-23T14:00:00Z",
                "edited_by_party_id": "m1",
                "field_updates": {"description": "Edited description"},
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["description"] == "Edited description"
    # status untouched
    assert row["status"] == "pending"
    assert row["kind"] == "reply"


async def test_commitment_snoozed_sets_status_and_due(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("commitment.proposed", _proposed_payload()))
    last = await log.append(
        _envelope(
            "commitment.snoozed",
            {
                "commitment_id": "c1",
                "snoozed_at": "2026-04-23T14:00:00Z",
                "snoozed_until": "2026-05-01T00:00:00Z",
                "snoozed_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["status"] == "snoozed"
    assert row["due_at"] == "2026-05-01T00:00:00Z"


async def test_commitment_cancelled_sets_status(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("commitment.proposed", _proposed_payload()))
    last = await log.append(
        _envelope(
            "commitment.cancelled",
            {
                "commitment_id": "c1",
                "cancelled_at": "2026-04-23T14:00:00Z",
                "cancelled_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["completed_at"] == "2026-04-23T14:00:00Z"


async def test_commitment_delegated_updates_owed_by(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("commitment.proposed", _proposed_payload()))
    last = await log.append(
        _envelope(
            "commitment.delegated",
            {
                "commitment_id": "c1",
                "delegated_at": "2026-04-23T14:00:00Z",
                "delegated_by_party_id": "m1",
                "delegated_to_party_id": "m2",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["status"] == "delegated"
    assert row["owed_by_party"] == "m2"


async def test_commitment_expired_cancels_without_completed_by(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("commitment.proposed", _proposed_payload()))
    last = await log.append(
        _envelope(
            "commitment.expired",
            {
                "commitment_id": "c1",
                "expired_at": "2026-05-08T00:00:00Z",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["status"] == "cancelled"
    # expired → nobody acted; no completed_by.
    assert row["completed_at"] is None


async def test_commitment_proposed_idempotent(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    env = _envelope("commitment.proposed", _proposed_payload())
    await log.append(env)
    last = await log.append(env)
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    count = conn.execute(
        "SELECT count(*) FROM commitments WHERE tenant_id = ? AND commitment_id = ?",
        ("tenant-a", "c1"),
    ).fetchone()[0]
    assert count == 1


async def test_rebuild_produces_equivalent_state(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # 30 events across the commitment lifecycle. Mix kinds.
    # 10 proposed, 5 confirmed, 3 completed, 3 dismissed, 2 snoozed,
    # 2 delegated, 2 cancelled, 2 edited, 1 expired.
    for i in range(10):
        await log.append(
            _envelope(
                "commitment.proposed",
                _proposed_payload(
                    commitment_id=f"c{i}",
                    owed_by_member_id=f"m{i % 3}",
                    text_summary=f"Commitment {i}",
                ),
            )
        )
    for i in range(5):
        await log.append(
            _envelope(
                "commitment.confirmed",
                {
                    "commitment_id": f"c{i}",
                    "confirmed_by_member_id": f"m{i % 3}",
                    "confirmed_at": f"2026-04-{20 + i:02d}T12:00:00Z",
                },
            )
        )
    for i in range(3):
        await log.append(
            _envelope(
                "commitment.completed",
                {
                    "commitment_id": f"c{i}",
                    "completed_at": f"2026-04-{23 + i:02d}T12:00:00Z",
                    "completed_by_party_id": f"m{i % 3}",
                },
            )
        )
    for i in range(3, 6):
        await log.append(
            _envelope(
                "commitment.dismissed",
                {
                    "commitment_id": f"c{i}",
                    "dismissed_at": "2026-04-25T12:00:00Z",
                    "dismissed_by_party_id": f"m{i % 3}",
                },
            )
        )
    for i in range(6, 8):
        await log.append(
            _envelope(
                "commitment.snoozed",
                {
                    "commitment_id": f"c{i}",
                    "snoozed_at": "2026-04-25T12:00:00Z",
                    "snoozed_until": f"2026-05-{1 + i:02d}T00:00:00Z",
                    "snoozed_by_party_id": f"m{i % 3}",
                },
            )
        )
    for i in range(8, 10):
        await log.append(
            _envelope(
                "commitment.delegated",
                {
                    "commitment_id": f"c{i}",
                    "delegated_at": "2026-04-25T12:00:00Z",
                    "delegated_by_party_id": f"m{i % 3}",
                    "delegated_to_party_id": f"m{(i + 1) % 3}",
                },
            )
        )
    # 2 cancelled (on brand new commitments)
    for i in range(10, 12):
        await log.append(
            _envelope(
                "commitment.proposed",
                _proposed_payload(commitment_id=f"c{i}", owed_by_member_id="m0"),
            )
        )
        await log.append(
            _envelope(
                "commitment.cancelled",
                {
                    "commitment_id": f"c{i}",
                    "cancelled_at": "2026-04-26T12:00:00Z",
                    "cancelled_by_party_id": "m0",
                },
            )
        )
    # 2 edited
    for i in range(12, 14):
        await log.append(
            _envelope(
                "commitment.proposed",
                _proposed_payload(commitment_id=f"c{i}", owed_by_member_id="m0"),
            )
        )
        await log.append(
            _envelope(
                "commitment.edited",
                {
                    "commitment_id": f"c{i}",
                    "edited_at": "2026-04-26T12:00:00Z",
                    "edited_by_party_id": "m0",
                    "field_updates": {"description": f"Updated description {i}"},
                },
            )
        )
    # 1 expired
    await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(commitment_id="c14", owed_by_member_id="m0"),
        )
    )
    last = await log.append(
        _envelope(
            "commitment.expired",
            {
                "commitment_id": "c14",
                "expired_at": "2026-05-10T00:00:00Z",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    pre = [
        tuple(r)
        for r in conn.execute(
            "SELECT * FROM commitments ORDER BY commitment_id"
        ).fetchall()
    ]

    await runner.rebuild("commitments")

    conn = runner.connection("commitments")
    post = [
        tuple(r)
        for r in conn.execute(
            "SELECT * FROM commitments ORDER BY commitment_id"
        ).fetchall()
    ]
    assert pre == post


async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(commitment_id="c1", text_summary="Tenant A commitment"),
            tenant_id="tenant-a",
        )
    )
    last = await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(commitment_id="c1", text_summary="Tenant B commitment"),
            tenant_id="tenant-b",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    a = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    b = get_commitment(conn, tenant_id="tenant-b", commitment_id="c1")
    assert a is not None and a["description"] == "Tenant A commitment"
    assert b is not None and b["description"] == "Tenant B commitment"


async def test_scope_canary_stub_privileged_lands_with_sensitivity(
    rig: dict[str, Any],
) -> None:
    """Prompt 06 stub — a privileged envelope still lands in the projection
    because scope enforcement is not yet wired. Prompt 08 extends this to
    raise ScopeViolation when a query is made outside the allowed scope."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(commitment_id="c1"),
            sensitivity="privileged",
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:commitments", eid)

    conn = runner.connection("commitments")
    row = get_commitment(conn, tenant_id="tenant-a", commitment_id="c1")
    assert row is not None
    assert row["sensitivity"] == "privileged"


async def test_open_for_member_filters_by_status(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(commitment_id="c1", owed_by_member_id="m1"),
        )
    )
    await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(commitment_id="c2", owed_by_member_id="m1"),
        )
    )
    # Complete c2 so it no longer appears in open_for_member.
    last = await log.append(
        _envelope(
            "commitment.completed",
            {
                "commitment_id": "c2",
                "completed_at": "2026-04-23T12:00:00Z",
                "completed_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    rows = open_for_member(conn, tenant_id="tenant-a", member_party_id="m1")
    assert {r["commitment_id"] for r in rows} == {"c1"}


async def test_pending_approval_ordered_desc(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    ids = ["c1", "c2", "c3"]
    for cid in ids:
        await log.append(
            _envelope(
                "commitment.proposed",
                _proposed_payload(commitment_id=cid),
            )
        )
        import asyncio
        await asyncio.sleep(0.01)  # ensure distinct occurred_at

    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    rows = pending_approval(conn, tenant_id="tenant-a")
    assert len(rows) == 3
    # Proposed_at is envelope.occurred_at which has seconds precision;
    # secondary sort by insertion keeps rows stable. Validate newest id
    # appears before earliest via proposed_at >= comparison.
    proposed_ats = [r["proposed_at"] for r in rows]
    assert proposed_ats == sorted(proposed_ats, reverse=True)


async def test_by_source_interaction_groups_proposals(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(commitment_id="c1", source_interaction_id="ix-1"),
        )
    )
    await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(commitment_id="c2", source_interaction_id="ix-1"),
        )
    )
    last = await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(commitment_id="c3", source_interaction_id="ix-2"),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    from_ix1 = by_source_interaction(conn, tenant_id="tenant-a", interaction_id="ix-1")
    assert {r["commitment_id"] for r in from_ix1} == {"c1", "c2"}


async def test_by_party_returns_both_sides(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(
                commitment_id="c1",
                owed_by_member_id="m1",
                owed_to_party_id="p-ext",
            ),
        )
    )
    last = await log.append(
        _envelope(
            "commitment.proposed",
            _proposed_payload(
                commitment_id="c2",
                owed_by_member_id="p-ext",
                owed_to_party_id="m1",
            ),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:commitments", last)

    conn = runner.connection("commitments")
    rows = by_party(conn, tenant_id="tenant-a", party_id="p-ext")
    assert {r["commitment_id"] for r in rows} == {"c1", "c2"}
