"""
Unit tests for the money projection (prompt 07a).

Covers idempotency, rebuild correctness, soft-delete semantics, and the
three event-type state transitions (recorded, manually_added,
manually_deleted). Per §12 invariant 1, queries take tenant_id as an
explicit keyword.
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
from adminme.lib.session import Session, build_internal_session
from adminme.projections.money import MoneyProjection
from adminme.projections.money.queries import (
    category_totals,
    flows_by_account,
    flows_by_category,
    flows_in_range,
    get_money_flow,
    manual_flows,
)
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"m" * 32


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
    runner.register(MoneyProjection())
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


def _recorded_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "flow_id": "f1",
        "from_party_id": "m1",
        "to_party_id": "p-merchant",
        "amount_minor": 2500,
        "currency": "USD",
        "occurred_at": "2026-04-20T12:00:00Z",
        "kind": "paid",
        "category": "groceries",
        "linked_account": "ac1",
        "source_adapter": "plaid",
    }
    base.update(overrides)
    return base


def _manually_added_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "flow_id": "f-manual-1",
        "from_party_id": "m1",
        "to_party_id": "p-cash",
        "amount_minor": 1000,
        "currency": "USD",
        "occurred_at": "2026-04-21T12:00:00Z",
        "kind": "paid",
        "category": "cash",
        "notes": "farmer's market",
        "added_by_party_id": "m1",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# basic event application
# ------------------------------------------------------------------
async def test_money_flow_recorded_inserts_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(_envelope("money_flow.recorded", _recorded_payload()))
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:money", eid)

    conn = runner.connection("money")
    row = get_money_flow(conn, _S("tenant-a"), flow_id="f1")
    assert row is not None
    assert row["is_manual"] == 0
    assert row["deleted_at"] is None
    assert row["amount_minor"] == 2500
    assert row["source_adapter"] == "plaid"


async def test_money_flow_manually_added_inserts_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope("money_flow.manually_added", _manually_added_payload())
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:money", eid)

    conn = runner.connection("money")
    row = get_money_flow(conn, _S("tenant-a"), flow_id="f-manual-1")
    assert row is not None
    assert row["is_manual"] == 1
    assert row["added_by_party_id"] == "m1"
    assert row["source_adapter"] == "manual"


async def test_money_flow_manually_deleted_sets_deleted_at(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope("money_flow.manually_added", _manually_added_payload())
    )
    last = await log.append(
        _envelope(
            "money_flow.manually_deleted",
            {
                "flow_id": "f-manual-1",
                "deleted_at": "2026-04-22T12:00:00Z",
                "deleted_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    row = get_money_flow(conn, _S("tenant-a"), flow_id="f-manual-1")
    assert row is not None
    assert row["deleted_at"] == "2026-04-22T12:00:00Z"
    # Row persists — rebuild depends on it.
    assert row["is_manual"] == 1


async def test_deletion_before_addition_is_no_op(rig: dict[str, Any]) -> None:
    """Event-order quirk: a .manually_deleted arrives before the
    corresponding .manually_added. Handler must not crash; no row created.
    Uses CF-5 discipline — follow with an innocuous event to advance the
    checkpoint before asserting absence."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "money_flow.manually_deleted",
            {
                "flow_id": "f-ghost",
                "deleted_at": "2026-04-22T12:00:00Z",
                "deleted_by_party_id": "m1",
            },
        )
    )
    # Drive checkpoint past the deletion with a normal recorded event.
    last = await log.append(
        _envelope("money_flow.recorded", _recorded_payload(flow_id="f-real"))
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    ghost = get_money_flow(conn, _S("tenant-a"), flow_id="f-ghost")
    real = get_money_flow(conn, _S("tenant-a"), flow_id="f-real")
    assert ghost is None
    assert real is not None


async def test_recorded_idempotent(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    env = _envelope("money_flow.recorded", _recorded_payload())
    await log.append(env)
    last = await log.append(env)
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    count = conn.execute(
        "SELECT count(*) FROM money_flows WHERE tenant_id = ? AND flow_id = ?",
        ("tenant-a", "f1"),
    ).fetchone()[0]
    assert count == 1


# ------------------------------------------------------------------
# rebuild
# ------------------------------------------------------------------
async def test_rebuild_produces_equivalent_state(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    for i in range(20):
        await log.append(
            _envelope(
                "money_flow.recorded",
                _recorded_payload(
                    flow_id=f"f{i}",
                    amount_minor=100 + i,
                    category="groceries" if i % 2 == 0 else "transport",
                    occurred_at=f"2026-04-{i + 1:02d}T12:00:00Z",
                ),
            )
        )
    for i in range(5):
        await log.append(
            _envelope(
                "money_flow.manually_added",
                _manually_added_payload(
                    flow_id=f"fm{i}",
                    amount_minor=500 + i,
                    occurred_at=f"2026-04-{i + 10:02d}T12:00:00Z",
                ),
            )
        )
    # Delete 2 of the manual flows.
    for i in range(2):
        await log.append(
            _envelope(
                "money_flow.manually_deleted",
                {
                    "flow_id": f"fm{i}",
                    "deleted_at": "2026-04-30T12:00:00Z",
                    "deleted_by_party_id": "m1",
                },
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    pre = [tuple(r) for r in conn.execute("SELECT * FROM money_flows ORDER BY flow_id")]

    await runner.rebuild("money")

    conn = runner.connection("money")
    post = [tuple(r) for r in conn.execute("SELECT * FROM money_flows ORDER BY flow_id")]
    assert pre == post


# ------------------------------------------------------------------
# queries
# ------------------------------------------------------------------
async def test_flows_in_range_excludes_deleted(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "money_flow.manually_added",
            _manually_added_payload(
                flow_id="fm-a", occurred_at="2026-04-10T12:00:00Z"
            ),
        )
    )
    await log.append(
        _envelope(
            "money_flow.manually_added",
            _manually_added_payload(
                flow_id="fm-b", occurred_at="2026-04-15T12:00:00Z"
            ),
        )
    )
    last = await log.append(
        _envelope(
            "money_flow.manually_deleted",
            {
                "flow_id": "fm-a",
                "deleted_at": "2026-04-20T12:00:00Z",
                "deleted_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    rows = flows_in_range(conn, _S("tenant-a"), start_iso="2026-04-01T00:00:00Z",
        end_iso="2026-04-30T23:59:59Z",
    )
    ids = {r["flow_id"] for r in rows}
    assert ids == {"fm-b"}


async def test_category_totals_excludes_deleted_and_null(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(
                flow_id="f1",
                amount_minor=1000,
                category="groceries",
                occurred_at="2026-04-05T12:00:00Z",
            ),
        )
    )
    await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(
                flow_id="f2",
                amount_minor=2000,
                category="groceries",
                occurred_at="2026-04-06T12:00:00Z",
            ),
        )
    )
    await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(
                flow_id="f3",
                amount_minor=5000,
                category="transport",
                occurred_at="2026-04-07T12:00:00Z",
            ),
        )
    )
    # NULL category — excluded.
    await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(
                flow_id="f4",
                amount_minor=9999,
                category=None,
                occurred_at="2026-04-08T12:00:00Z",
            ),
        )
    )
    # Manually-added then deleted — excluded.
    await log.append(
        _envelope(
            "money_flow.manually_added",
            _manually_added_payload(
                flow_id="fm1",
                amount_minor=7777,
                category="groceries",
                occurred_at="2026-04-09T12:00:00Z",
            ),
        )
    )
    last = await log.append(
        _envelope(
            "money_flow.manually_deleted",
            {
                "flow_id": "fm1",
                "deleted_at": "2026-04-10T12:00:00Z",
                "deleted_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    totals = category_totals(conn, _S("tenant-a"), since_iso="2026-04-01T00:00:00Z"
    )
    assert totals == {"groceries": 3000, "transport": 5000}


async def test_manual_flows_returns_only_manual_non_deleted(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("money_flow.recorded", _recorded_payload(flow_id="f1")))
    await log.append(
        _envelope("money_flow.manually_added", _manually_added_payload(flow_id="fm1"))
    )
    await log.append(
        _envelope("money_flow.manually_added", _manually_added_payload(flow_id="fm2"))
    )
    last = await log.append(
        _envelope(
            "money_flow.manually_deleted",
            {
                "flow_id": "fm2",
                "deleted_at": "2026-04-22T12:00:00Z",
                "deleted_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    rows = manual_flows(conn, _S("tenant-a"))
    assert {r["flow_id"] for r in rows} == {"fm1"}


async def test_flows_by_account(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(flow_id="f1", linked_account="ac1"),
        )
    )
    await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(flow_id="f2", linked_account="ac1"),
        )
    )
    last = await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(flow_id="f3", linked_account="ac2"),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    rows = flows_by_account(conn, _S("tenant-a"), account_id="ac1")
    assert {r["flow_id"] for r in rows} == {"f1", "f2"}


async def test_flows_by_category(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(flow_id="f1", category="groceries"),
        )
    )
    await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(flow_id="f2", category="transport"),
        )
    )
    last = await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(flow_id="f3", category="groceries"),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    rows = flows_by_category(conn, _S("tenant-a"), category="groceries")
    assert {r["flow_id"] for r in rows} == {"f1", "f3"}


# ------------------------------------------------------------------
# multi-tenant & scope
# ------------------------------------------------------------------
async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(amount_minor=100),
            tenant_id="tenant-a",
        )
    )
    last = await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(amount_minor=999),
            tenant_id="tenant-b",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:money", last)

    conn = runner.connection("money")
    a = get_money_flow(conn, _S("tenant-a"), flow_id="f1")
    b = get_money_flow(conn, _S("tenant-b"), flow_id="f1")
    assert a is not None and a["amount_minor"] == 100
    assert b is not None and b["amount_minor"] == 999


async def test_scope_canary_privileged_drops_for_non_owner(
    rig: dict[str, Any],
) -> None:
    """Prompt 08a wired: a privileged-sensitivity money flow with
    shared:household owner_scope drops from a non-owner's read; the row
    is still in the projection table per [§6.4]."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "money_flow.recorded",
            _recorded_payload(flow_id="f-priv"),
            sensitivity="privileged",
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:money", eid)

    conn = runner.connection("money")
    row = get_money_flow(conn, _S("tenant-a"), flow_id="f-priv")
    assert row is None

    raw = conn.execute(
        "SELECT count(*) FROM money_flows WHERE tenant_id = ? AND flow_id = ?",
        ("tenant-a", "f-priv"),
    ).fetchone()
    assert raw[0] == 1
