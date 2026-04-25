"""
Unit tests for the tasks projection (prompt 06).

Covers task.created/completed/updated/deleted with idempotency, rebuild
correctness, sub-task hierarchy (goal_ref), and multi-tenant isolation.
Per §12 invariant 1, queries take tenant_id as an explicit keyword.
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
from adminme.projections.runner import ProjectionRunner
from adminme.projections.tasks import TasksProjection
from adminme.projections.tasks.queries import (
    by_context,
    get_task,
    in_status,
    open_for_member,
    sub_tasks_of,
    today_for_member,
)

TEST_KEY = b"t" * 32


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
    runner.register(TasksProjection())
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


def _task_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task_id": "t1",
        "title": "Mow the lawn",
        "owner_member_id": "m1",
        "due": "2026-04-25",
        "energy": "medium",
    }
    base.update(overrides)
    return base


async def test_task_created_inserts_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(_envelope("task.created", _task_payload()))
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:tasks", eid)

    conn = runner.connection("tasks")
    row = get_task(conn, _S("tenant-a"), task_id="t1")
    assert row is not None
    assert row["title"] == "Mow the lawn"
    assert row["assignee_party"] == "m1"
    assert row["due_date"] == "2026-04-25"
    assert row["energy"] == "medium"
    # Defaults present
    assert row["status"] == "inbox"
    assert row["domain"] == "tasks"
    assert row["item_type"] == "task"


async def test_task_completed_marks_done(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("task.created", _task_payload()))
    last = await log.append(
        _envelope(
            "task.completed",
            {
                "task_id": "t1",
                "completed_by_member_id": "m1",
                "completed_at": "2026-04-25T14:00:00Z",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    row = get_task(conn, _S("tenant-a"), task_id="t1")
    assert row is not None
    assert row["status"] == "done"
    assert row["completed_at"] == "2026-04-25T14:00:00Z"
    assert row["completed_by"] == "m1"


async def test_task_updated_new_status_changes_status_only(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("task.created", _task_payload()))
    last = await log.append(
        _envelope(
            "task.updated",
            {
                "task_id": "t1",
                "updated_at": "2026-04-23T14:00:00Z",
                "previous_status": "inbox",
                "new_status": "in_progress",
                "field_updates": {},
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    row = get_task(conn, _S("tenant-a"), task_id="t1")
    assert row is not None
    assert row["status"] == "in_progress"
    # Other fields untouched
    assert row["title"] == "Mow the lawn"
    assert row["energy"] == "medium"


async def test_task_updated_field_updates(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("task.created", _task_payload()))
    last = await log.append(
        _envelope(
            "task.updated",
            {
                "task_id": "t1",
                "updated_at": "2026-04-23T14:00:00Z",
                "field_updates": {
                    "energy": "high",
                    "micro_script": "open garage",
                },
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    row = get_task(conn, _S("tenant-a"), task_id="t1")
    assert row is not None
    assert row["energy"] == "high"
    assert row["micro_script"] == "open garage"
    # status/title untouched
    assert row["status"] == "inbox"
    assert row["title"] == "Mow the lawn"


async def test_task_deleted_is_soft(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("task.created", _task_payload()))
    last = await log.append(
        _envelope(
            "task.deleted",
            {
                "task_id": "t1",
                "deleted_at": "2026-04-23T14:00:00Z",
                "deleted_by_party_id": "m1",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    row = get_task(conn, _S("tenant-a"), task_id="t1")
    assert row is not None  # row stays (soft delete)
    assert row["status"] == "dismissed"


async def test_sub_tasks_of_returns_children(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope("task.created", _task_payload(task_id="parent", title="Remodel"))
    )
    await log.append(_envelope("task.created", _task_payload(task_id="child1")))
    await log.append(_envelope("task.created", _task_payload(task_id="child2")))
    # Wire goal_ref via update
    await log.append(
        _envelope(
            "task.updated",
            {
                "task_id": "child1",
                "updated_at": "2026-04-23T14:00:00Z",
                "field_updates": {"goal_ref": "parent"},
            },
        )
    )
    last = await log.append(
        _envelope(
            "task.updated",
            {
                "task_id": "child2",
                "updated_at": "2026-04-23T14:00:00Z",
                "field_updates": {"goal_ref": "parent"},
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    children = sub_tasks_of(conn, _S("tenant-a"), goal_ref_task_id="parent")
    assert {c["task_id"] for c in children} == {"child1", "child2"}


async def test_today_for_member_filters_correctly(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # Due today → should appear.
    await log.append(
        _envelope(
            "task.created",
            _task_payload(task_id="t1", owner_member_id="m1", due="2026-04-20"),
        )
    )
    # Due in the future → should NOT appear.
    await log.append(
        _envelope(
            "task.created",
            _task_payload(task_id="t2", owner_member_id="m1", due="2026-05-01"),
        )
    )
    # Completed → should NOT appear.
    await log.append(
        _envelope(
            "task.created",
            _task_payload(task_id="t3", owner_member_id="m1", due="2026-04-20"),
        )
    )
    await log.append(
        _envelope(
            "task.completed",
            {
                "task_id": "t3",
                "completed_by_member_id": "m1",
                "completed_at": "2026-04-22T12:00:00Z",
            },
        )
    )
    # Household-shared task due today → should appear.
    await log.append(
        _envelope(
            "task.created",
            _task_payload(
                task_id="t4",
                owner_member_id=None,  # household
                due="2026-04-20",
            ),
        )
    )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    rows = today_for_member(conn, _S("tenant-a"), member_party_id="m1", today_iso="2026-04-23"
    )
    ids = {r["task_id"] for r in rows}
    assert ids == {"t1", "t4"}


async def test_task_created_idempotent(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    env = _envelope("task.created", _task_payload())
    await log.append(env)
    last = await log.append(env)
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    count = conn.execute(
        "SELECT count(*) FROM tasks WHERE tenant_id = ? AND task_id = ?",
        ("tenant-a", "t1"),
    ).fetchone()[0]
    assert count == 1


async def test_rebuild_produces_equivalent_state(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # 30 events: 20 created, 5 completed, 3 updated, 2 deleted.
    for i in range(20):
        await log.append(
            _envelope(
                "task.created",
                _task_payload(
                    task_id=f"t{i}",
                    title=f"Task {i}",
                    owner_member_id=f"m{i % 3}",
                ),
            )
        )
    for i in range(5):
        await log.append(
            _envelope(
                "task.completed",
                {
                    "task_id": f"t{i}",
                    "completed_by_member_id": f"m{i % 3}",
                    "completed_at": f"2026-04-{20 + i:02d}T12:00:00Z",
                },
            )
        )
    for i in range(5, 8):
        await log.append(
            _envelope(
                "task.updated",
                {
                    "task_id": f"t{i}",
                    "updated_at": "2026-04-23T12:00:00Z",
                    "previous_status": "inbox",
                    "new_status": "next",
                    "field_updates": {"energy": "high"},
                },
            )
        )
    for i in range(8, 10):
        await log.append(
            _envelope(
                "task.deleted",
                {
                    "task_id": f"t{i}",
                    "deleted_at": "2026-04-23T12:00:00Z",
                    "deleted_by_party_id": "m1",
                },
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    pre = [
        tuple(r)
        for r in conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
    ]

    await runner.rebuild("tasks")

    conn = runner.connection("tasks")
    post = [
        tuple(r)
        for r in conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
    ]
    assert pre == post


async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "task.created",
            _task_payload(task_id="t1", title="A-task"),
            tenant_id="tenant-a",
        )
    )
    last = await log.append(
        _envelope(
            "task.created",
            _task_payload(task_id="t1", title="B-task"),
            tenant_id="tenant-b",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    a = get_task(conn, _S("tenant-a"), task_id="t1")
    b = get_task(conn, _S("tenant-b"), task_id="t1")
    assert a is not None and a["title"] == "A-task"
    assert b is not None and b["title"] == "B-task"


async def test_scope_canary_privileged_drops_for_non_owner(
    rig: dict[str, Any],
) -> None:
    """Prompt 08a wired: a privileged-sensitivity row scoped at
    shared:household (an illegal combination per DIAGRAMS §5) is dropped
    from a non-owner's read. The row is still in the projection table —
    the scope filter excludes it on the way out."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "task.created",
            _task_payload(task_id="t1"),
            sensitivity="privileged",
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:tasks", eid)

    conn = runner.connection("tasks")
    # Non-owner reader does not see the privileged row.
    row = get_task(conn, _S("tenant-a"), task_id="t1")
    assert row is None

    # The row IS in the projection — confirm via raw SQL (defense-in-depth
    # belongs to the query layer, not the table).
    raw = conn.execute(
        "SELECT count(*) FROM tasks WHERE tenant_id = ? AND task_id = ?",
        ("tenant-a", "t1"),
    ).fetchone()
    assert raw[0] == 1


async def test_open_for_member_excludes_done(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("task.created", _task_payload(task_id="t1")))
    await log.append(_envelope("task.created", _task_payload(task_id="t2")))
    last = await log.append(
        _envelope(
            "task.completed",
            {
                "task_id": "t2",
                "completed_by_member_id": "m1",
                "completed_at": "2026-04-23T12:00:00Z",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    rows = open_for_member(conn, _S("tenant-a"), member_party_id="m1")
    assert {r["task_id"] for r in rows} == {"t1"}


async def test_by_context_and_in_status(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("task.created", _task_payload(task_id="t1")))
    # Second task has a different domain — update it via task.updated.
    await log.append(_envelope("task.created", _task_payload(task_id="t2")))
    last = await log.append(
        _envelope(
            "task.updated",
            {
                "task_id": "t2",
                "updated_at": "2026-04-23T12:00:00Z",
                "field_updates": {"domain": "home"},
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:tasks", last)

    conn = runner.connection("tasks")
    default_dom = by_context(conn, _S("tenant-a"), domain="tasks")
    home_dom = by_context(conn, _S("tenant-a"), domain="home")
    assert {r["task_id"] for r in default_dom} == {"t1"}
    assert {r["task_id"] for r in home_dom} == {"t2"}
    inboxes = in_status(conn, _S("tenant-a"), status="inbox")
    assert {r["task_id"] for r in inboxes} == {"t1", "t2"}
