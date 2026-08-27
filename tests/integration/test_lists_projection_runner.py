"""
Integration test: ``ListsProjection`` registered + replayed + rebuilt
through ``ProjectionRunner``.

Per SYSTEM_INVARIANTS.md §2 invariants 1 + 4 (rebuild byte-equivalence;
apply idempotent) and ADMINISTRATEME_BUILD.md §L3. Closes prompt 11f's
events → projection chain for the ``list_item.*`` family at the
integration seam.

The three tests below cover the contract:

1. ``test_lists_projection_registered_in_runner`` — ``ListsProjection``
   appears in ``DEFAULT_PROJECTION_CLASSES`` so bootstrap code and other
   integration tests find it without ad-hoc imports per [D18].
2. ``test_runner_replays_list_item_added_into_lists_db`` — appending a
   ``list_item.added@v1`` envelope through ``EventLog`` causes the bus
   to dispatch into ``apply_event`` and a row appears in ``list_items``
   with envelope-level scope fields propagated per parent draft §1
   item 3.
3. ``test_rebuild_produces_identical_state`` — calling
   ``runner.rebuild("lists")`` after populating the log produces
   byte-equivalent row state per §2 invariant 1.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.lists import ListsProjection
from adminme.projections.runner import DEFAULT_PROJECTION_CLASSES, ProjectionRunner

TEST_KEY = b"L" * 32


def test_existing_lists_database_receives_v3_columns_before_use() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE list_items (
            tenant_id TEXT NOT NULL, list_external_id TEXT NOT NULL,
            item_external_id TEXT NOT NULL, member_id TEXT NOT NULL,
            list_name TEXT NOT NULL, title TEXT NOT NULL, notes TEXT,
            due_at TEXT, priority INTEGER NOT NULL, completed INTEGER NOT NULL,
            last_modified_at TEXT NOT NULL, sync_cycle_id TEXT NOT NULL,
            owner_scope TEXT NOT NULL, visibility_scope TEXT NOT NULL,
            sensitivity TEXT NOT NULL, last_event_id TEXT NOT NULL,
            PRIMARY KEY (tenant_id, list_external_id, item_external_id)
        )
        """
    )
    projection = ListsProjection()
    conn.executescript(projection.schema_path.read_text(encoding="utf-8"))
    projection.migrate_schema(conn)
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(list_items)")}
    assert {
        "provider_position",
        "source_links_json",
        "provider_data_json",
        "read_only_reason",
    } <= columns
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"reminder_cues", "context_signals"} <= tables


def _envelope(
    payload: dict[str, Any],
    *,
    tenant_id: str = "tenant-a",
    owner_scope: str = "shared:household",
    visibility_scope: str = "shared:household",
    sensitivity: str = "normal",
    event_type: str = "list_item.added",
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
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,
        payload=payload,
    )


def _payload(
    *,
    item_external_id: str,
    list_external_id: str = "list-A",
    member_id: str = "m1",
    list_name: str = "Groceries",
    title: str = "Milk",
    notes: str | None = "2%, half gallon",
    due_at: str | None = "2026-05-20T12:00:00+00:00",
    priority: int = 5,
    completed: bool = False,
    last_modified_at: str = "2026-05-18T09:00:00+00:00",
    sync_cycle_id: str = "cycle-001",
) -> dict[str, Any]:
    return {
        "member_id": member_id,
        "list_external_id": list_external_id,
        "item_external_id": item_external_id,
        "list_name": list_name,
        "title": title,
        "notes": notes,
        "due_at": due_at,
        "priority": priority,
        "completed": completed,
        "last_modified_at": last_modified_at,
        "sync_cycle_id": sync_cycle_id,
    }


async def _wait_idle(bus: EventBus, subscriber_id: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await bus.subscriber_status(subscriber_id)
        if status["lag_count"] == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"subscriber {subscriber_id} stayed lagged: {status}")


def _snapshot(conn: Any) -> list[tuple[Any, ...]]:
    return sorted(tuple(r) for r in conn.execute("SELECT * FROM list_items"))


@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(ListsProjection())
    await runner.start()
    try:
        yield {"log": log, "bus": bus, "runner": runner}
    finally:
        await runner.stop()
        await log.close()


def test_lists_projection_registered_in_runner() -> None:
    """[D18]: ``ListsProjection`` is reachable via the runner's default
    registration tuple. Bootstrap and downstream integration tests rely
    on this anchor rather than per-call imports."""
    assert ListsProjection in DEFAULT_PROJECTION_CLASSES
    # And the class still satisfies the prompt 11f §7 contract surface:
    # name + version + subscribes_to + schema_path pinned at the
    # registry seam so a future refactor cannot silently rename them.
    instance = ListsProjection()
    assert instance.name == "lists"
    assert instance.version == 4
    assert list(instance.subscribes_to) == [
        "list.created",
        "list.updated",
        "list.deleted",
        "list_item.added",
        "list_item.updated",
        "list_item.completed",
        "list_item.uncompleted",
        "list_item.deleted",
        "context.signal.observed",
        "reminder.cue.configured",
        "reminder.cue.cleared",
    ]
    assert instance.schema_path.name == "schema.sql"


async def test_runner_replays_list_item_added_into_lists_db(
    rig: dict[str, Any],
) -> None:
    """End-to-end: append ``list_item.added@v1`` through ``EventLog``,
    the bus dispatches into ``apply_event``, and the row appears in
    ``list_items`` with envelope-level scope fields propagated."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            _payload(item_external_id="item-1"),
            owner_scope="private:m1",
            visibility_scope="private:m1",
            sensitivity="sensitive",
        )
    )
    await bus.notify(eid)
    await _wait_idle(bus, "projection:lists")

    conn = runner.connection("lists")
    row = conn.execute(
        "SELECT * FROM list_items WHERE list_external_id = ? AND item_external_id = ?",
        ("list-A", "item-1"),
    ).fetchone()
    assert row is not None
    assert row["tenant_id"] == "tenant-a"
    assert row["member_id"] == "m1"
    assert row["list_name"] == "Groceries"
    assert row["title"] == "Milk"
    assert row["priority"] == 5
    assert row["completed"] == 0
    # Envelope-level scope fields propagate per parent draft §1 item 3.
    assert row["owner_scope"] == "private:m1"
    assert row["visibility_scope"] == "private:m1"
    assert row["sensitivity"] == "sensitive"
    assert row["last_event_id"] == eid


async def test_rebuild_produces_identical_state(rig: dict[str, Any]) -> None:
    """§2 invariant 1: ``runner.rebuild("lists")`` drops the DB and
    replays the log producing byte-equivalent row state. Mix three
    distinct items across two lists plus a duplicate-id replay to
    exercise the composite PK + UPSERT branch."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid_first = await log.append(
        _envelope(_payload(item_external_id="i1", list_external_id="list-A"))
    )
    await log.append(
        _envelope(
            _payload(
                item_external_id="i2",
                list_external_id="list-A",
                title="Eggs",
                completed=True,
            )
        )
    )
    await log.append(
        _envelope(
            _payload(
                item_external_id="shared",
                list_external_id="list-B",
                title="Notebook",
            )
        )
    )
    # Replay of the first id under the same composite PK — UPSERT
    # branch; ``last_event_id`` should advance to this event after
    # both live delivery and rebuild.
    eid_last = await log.append(
        _envelope(_payload(item_external_id="i1", list_external_id="list-A"))
    )
    assert eid_last != eid_first

    await bus.notify(eid_last)
    await _wait_idle(bus, "projection:lists")

    pre = _snapshot(runner.connection("lists"))
    pre_last_event_id = (
        runner.connection("lists")
        .execute(
            "SELECT last_event_id FROM list_items "
            "WHERE list_external_id = ? AND item_external_id = ?",
            ("list-A", "i1"),
        )
        .fetchone()["last_event_id"]
    )
    assert pre_last_event_id == eid_last
    # 3 distinct composite-PK rows survive the duplicate-id replay.
    assert len(pre) == 3

    await runner.rebuild("lists")

    post = _snapshot(runner.connection("lists"))
    post_last_event_id = (
        runner.connection("lists")
        .execute(
            "SELECT last_event_id FROM list_items "
            "WHERE list_external_id = ? AND item_external_id = ?",
            ("list-A", "i1"),
        )
        .fetchone()["last_event_id"]
    )
    assert pre == post
    assert post_last_event_id == eid_last
