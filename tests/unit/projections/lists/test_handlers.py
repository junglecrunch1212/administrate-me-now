"""Unit tests for adminme/projections/lists/handlers.py.

Handler-direct: open an in-memory SQLite connection, load schema.sql,
synthesize an envelope dict, call ``apply_event(envelope, conn)``, and
inspect the resulting row(s). Per SYSTEM_INVARIANTS.md §2 invariant 4 the
handler MUST be idempotent (same envelope twice → same row state).

The schema uses only standard SQLite features (no SQLCipher-specific
pragmas), so ``sqlite3.connect(":memory:")`` is sufficient for these unit
tests; production deploys via SQLCipher through ``ProjectionRunner`` which
is exercised separately in tests/integration/.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.projections.lists import handlers


SCHEMA_PATH = (
    Path(__file__).resolve().parents[4]
    / "adminme"
    / "projections"
    / "lists"
    / "schema.sql"
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return c


def _envelope(
    *,
    event_type: str = "list_item.added",
    event_id: str = "evt-1",
    tenant_id: str = "tenant-a",
    owner_scope: str = "shared:household",
    visibility_scope: str = "shared:household",
    sensitivity: str = "normal",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "type": event_type,
        "tenant_id": tenant_id,
        "owner_scope": owner_scope,
        "visibility_scope": visibility_scope,
        "sensitivity": sensitivity,
        "payload": payload if payload is not None else _payload(),
    }


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "member_id": "m1",
        "list_external_id": "list-A",
        "item_external_id": "item-1",
        "list_name": "Groceries",
        "title": "Milk",
        "notes": "2%, half gallon",
        "due_at": datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        "priority": 5,
        "completed": False,
        "last_modified_at": datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
        "sync_cycle_id": "cycle-001",
    }
    base.update(overrides)
    return base


def _row(conn: sqlite3.Connection, *, list_eid: str, item_eid: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM list_items
         WHERE list_external_id = ? AND item_external_id = ?
        """,
        (list_eid, item_eid),
    ).fetchone()


def test_apply_list_item_added_inserts_row_with_all_columns() -> None:
    conn = _conn()
    handlers.apply_event(_envelope(), conn)
    row = _row(conn, list_eid="list-A", item_eid="item-1")
    assert row is not None
    assert row["tenant_id"] == "tenant-a"
    assert row["list_external_id"] == "list-A"
    assert row["item_external_id"] == "item-1"
    assert row["member_id"] == "m1"
    assert row["list_name"] == "Groceries"
    assert row["title"] == "Milk"
    assert row["notes"] == "2%, half gallon"
    assert row["due_at"] == "2026-05-20T12:00:00+00:00"
    assert row["priority"] == 5
    assert row["completed"] == 0
    assert row["last_modified_at"] == "2026-05-18T09:00:00+00:00"
    assert row["sync_cycle_id"] == "cycle-001"


def test_apply_list_item_added_upserts_on_conflict_idempotent() -> None:
    conn = _conn()
    env = _envelope()
    handlers.apply_event(env, conn)
    handlers.apply_event(env, conn)  # replay — must be a no-op state-wise.
    count = conn.execute(
        "SELECT count(*) FROM list_items WHERE list_external_id = ? AND item_external_id = ?",
        ("list-A", "item-1"),
    ).fetchone()[0]
    assert count == 1
    # And the row content matches first apply.
    row = _row(conn, list_eid="list-A", item_eid="item-1")
    assert row is not None
    assert row["title"] == "Milk"


def test_apply_list_item_added_envelope_scope_fields_propagate() -> None:
    conn = _conn()
    env = _envelope(
        event_id="evt-99",
        owner_scope="private:m1",
        visibility_scope="private:m1",
        sensitivity="sensitive",
    )
    handlers.apply_event(env, conn)
    row = _row(conn, list_eid="list-A", item_eid="item-1")
    assert row is not None
    assert row["owner_scope"] == "private:m1"
    assert row["visibility_scope"] == "private:m1"
    assert row["sensitivity"] == "sensitive"
    assert row["last_event_id"] == "evt-99"


@pytest.mark.parametrize("priority", [0, 1, 5, 9])
def test_apply_list_item_added_priority_literal_values_accepted(priority: int) -> None:
    conn = _conn()
    handlers.apply_event(_envelope(payload=_payload(priority=priority)), conn)
    row = _row(conn, list_eid="list-A", item_eid="item-1")
    assert row is not None
    assert row["priority"] == priority


def test_apply_list_item_added_completed_bool_coerces_to_integer() -> None:
    conn = _conn()
    handlers.apply_event(
        _envelope(
            event_id="evt-true",
            payload=_payload(item_external_id="item-T", completed=True),
        ),
        conn,
    )
    handlers.apply_event(
        _envelope(
            event_id="evt-false",
            payload=_payload(item_external_id="item-F", completed=False),
        ),
        conn,
    )
    t = _row(conn, list_eid="list-A", item_eid="item-T")
    f = _row(conn, list_eid="list-A", item_eid="item-F")
    assert t is not None and f is not None
    assert t["completed"] == 1
    assert f["completed"] == 0


def test_apply_list_item_added_optional_notes_due_at_null_handling() -> None:
    conn = _conn()
    handlers.apply_event(
        _envelope(payload=_payload(notes=None, due_at=None)), conn
    )
    row = _row(conn, list_eid="list-A", item_eid="item-1")
    assert row is not None
    assert row["notes"] is None
    assert row["due_at"] is None
    # Required fields still populated.
    assert row["title"] == "Milk"
    assert row["last_modified_at"] == "2026-05-18T09:00:00+00:00"


def test_apply_event_unhandled_type_is_noop() -> None:
    conn = _conn()
    # list_item.updated has no handler dispatch in 11f c2 per F-7; the
    # dispatcher returns silently without raising or touching state.
    handlers.apply_event(_envelope(event_type="list_item.updated"), conn)
    handlers.apply_event(_envelope(event_type="list_item.completed"), conn)
    handlers.apply_event(_envelope(event_type="list_item.deleted"), conn)
    count = conn.execute("SELECT count(*) FROM list_items").fetchone()[0]
    assert count == 0


def test_apply_event_dispatch_only_for_subscribed_type() -> None:
    conn = _conn()
    handlers.apply_event(_envelope(event_type="task.created"), conn)
    handlers.apply_event(_envelope(event_type="random.unknown"), conn)
    handlers.apply_event(_envelope(event_type=""), conn)
    count = conn.execute("SELECT count(*) FROM list_items").fetchone()[0]
    assert count == 0
    # The one subscribed type does write.
    handlers.apply_event(_envelope(event_type="list_item.added"), conn)
    count = conn.execute("SELECT count(*) FROM list_items").fetchone()[0]
    assert count == 1


def test_apply_list_item_added_distinct_lists_distinct_rows() -> None:
    conn = _conn()
    # Same item_external_id but two different list_external_ids — composite
    # PK guards against the collision per parent draft §1 item 2.
    handlers.apply_event(
        _envelope(
            event_id="evt-A",
            payload=_payload(list_external_id="list-A", item_external_id="shared"),
        ),
        conn,
    )
    handlers.apply_event(
        _envelope(
            event_id="evt-B",
            payload=_payload(list_external_id="list-B", item_external_id="shared"),
        ),
        conn,
    )
    rows = conn.execute(
        "SELECT list_external_id FROM list_items WHERE item_external_id = ? ORDER BY list_external_id",
        ("shared",),
    ).fetchall()
    assert [r["list_external_id"] for r in rows] == ["list-A", "list-B"]


def test_apply_list_item_added_distinct_items_within_list_distinct_rows() -> None:
    conn = _conn()
    handlers.apply_event(
        _envelope(
            event_id="evt-1",
            payload=_payload(list_external_id="list-A", item_external_id="i1", title="Milk"),
        ),
        conn,
    )
    handlers.apply_event(
        _envelope(
            event_id="evt-2",
            payload=_payload(list_external_id="list-A", item_external_id="i2", title="Eggs"),
        ),
        conn,
    )
    rows = conn.execute(
        "SELECT title FROM list_items WHERE list_external_id = ? ORDER BY item_external_id",
        ("list-A",),
    ).fetchall()
    assert [r["title"] for r in rows] == ["Milk", "Eggs"]
