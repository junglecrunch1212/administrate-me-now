"""Unit tests for adminme/projections/lists/queries.py.

Each public query MUST take a ``session: Session`` and route results
through ``filter_rows`` / ``filter_one`` per SYSTEM_INVARIANTS.md §6.1
+ §6.4. These tests seed rows via ``handlers.apply_list_item_added`` so
the query layer sees realistic shapes, then assert (a) scope-filtered
output, (b) ``completed = 0`` projection on ``open_items_for_member``,
(c) deterministic ordering, (d) child-session scope-filter drop.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adminme.lib.session import Session, build_internal_session
from adminme.projections.lists import handlers, queries


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


def _S(tenant_id: str = "tenant-a", role: str = "principal", actor: str = "actor-1") -> Session:
    return build_internal_session(actor, role, tenant_id)


def _seed(
    conn: sqlite3.Connection,
    *,
    list_external_id: str = "list-A",
    item_external_id: str,
    member_id: str = "m1",
    title: str = "Milk",
    due_at: datetime | None = None,
    last_modified_at: datetime | None = None,
    completed: bool = False,
    tenant_id: str = "tenant-a",
    owner_scope: str = "shared:household",
    visibility_scope: str = "shared:household",
    sensitivity: str = "normal",
    event_id: str | None = None,
) -> None:
    envelope: dict[str, Any] = {
        "event_id": event_id or f"evt-{item_external_id}",
        "type": "list_item.added",
        "tenant_id": tenant_id,
        "owner_scope": owner_scope,
        "visibility_scope": visibility_scope,
        "sensitivity": sensitivity,
        "payload": {
            "member_id": member_id,
            "list_external_id": list_external_id,
            "item_external_id": item_external_id,
            "list_name": "Groceries",
            "title": title,
            "notes": None,
            "due_at": due_at,
            "priority": 5,
            "completed": completed,
            "last_modified_at": last_modified_at
            or datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            "sync_cycle_id": "cycle-001",
        },
    }
    handlers.apply_event(envelope, conn)


def test_get_item_returns_filtered_row() -> None:
    conn = _conn()
    _seed(conn, item_external_id="item-1", title="Milk")
    row = queries.get_item(
        conn, _S(), list_external_id="list-A", item_external_id="item-1"
    )
    assert row is not None
    assert row["title"] == "Milk"
    assert row["item_external_id"] == "item-1"


def test_get_item_returns_none_for_missing() -> None:
    conn = _conn()
    row = queries.get_item(
        conn, _S(), list_external_id="list-A", item_external_id="nope"
    )
    assert row is None


def test_items_for_list_returns_filtered_rows() -> None:
    conn = _conn()
    _seed(conn, item_external_id="i1", title="Milk")
    _seed(conn, item_external_id="i2", title="Eggs")
    # Different list — should NOT appear in items_for_list("list-A").
    _seed(conn, list_external_id="list-B", item_external_id="i3", title="Hammer")
    rows = queries.items_for_list(conn, _S(), list_external_id="list-A")
    assert {r["item_external_id"] for r in rows} == {"i1", "i2"}


def test_items_for_member_returns_filtered_rows() -> None:
    conn = _conn()
    _seed(conn, item_external_id="i1", member_id="m1", title="A")
    _seed(conn, item_external_id="i2", member_id="m1", title="B")
    _seed(conn, item_external_id="i3", member_id="m2", title="C")
    rows = queries.items_for_member(conn, _S(), member_id="m1")
    assert {r["item_external_id"] for r in rows} == {"i1", "i2"}


def test_open_items_for_member_filters_completed_out() -> None:
    conn = _conn()
    _seed(conn, item_external_id="i1", member_id="m1", completed=False)
    _seed(conn, item_external_id="i2", member_id="m1", completed=True)
    _seed(conn, item_external_id="i3", member_id="m1", completed=False)
    rows = queries.open_items_for_member(conn, _S(), member_id="m1")
    assert {r["item_external_id"] for r in rows} == {"i1", "i3"}


def test_queries_apply_filter_rows_scope_discipline() -> None:
    # A child-role session cannot see private:other-member rows
    # [DIAGRAMS.md §5]. filter_rows enforces this on the way out.
    conn = _conn()
    _seed(
        conn,
        item_external_id="i-private",
        member_id="m1",
        owner_scope="private:other-member",
        visibility_scope="private:other-member",
    )
    _seed(
        conn,
        item_external_id="i-household",
        member_id="m1",
        owner_scope="shared:household",
        visibility_scope="shared:household",
    )
    rows = queries.items_for_member(
        conn,
        _S(role="child", actor="kid-actor"),
        member_id="m1",
    )
    # Only the shared:household row survives the scope filter.
    assert {r["item_external_id"] for r in rows} == {"i-household"}


def test_queries_apply_filter_one_for_get_item() -> None:
    # A sensitive privileged-owner row is dropped from a non-owner read
    # per [§6.9] / DIAGRAMS.md §5. The row is in the table; filter_one
    # excludes it.
    conn = _conn()
    _seed(
        conn,
        item_external_id="i-priv",
        owner_scope="private:other-member",
        visibility_scope="private:other-member",
        sensitivity="privileged",
    )
    row = queries.get_item(
        conn,
        _S(actor="reader-not-owner"),
        list_external_id="list-A",
        item_external_id="i-priv",
    )
    assert row is None
    # Confirm the row IS in the table (defense lives in the query layer).
    raw = conn.execute(
        "SELECT count(*) FROM list_items WHERE item_external_id = ?",
        ("i-priv",),
    ).fetchone()[0]
    assert raw == 1


def test_queries_order_by_due_at_then_last_modified() -> None:
    # items_for_list orders: NULL due_at last, then due_at ASC, then
    # last_modified_at ASC. Three rows assert all three buckets.
    conn = _conn()
    _seed(
        conn,
        item_external_id="i-no-due",
        due_at=None,
        last_modified_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    _seed(
        conn,
        item_external_id="i-early-due",
        due_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
        last_modified_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    _seed(
        conn,
        item_external_id="i-late-due",
        due_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        last_modified_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    rows = queries.items_for_list(conn, _S(), list_external_id="list-A")
    assert [r["item_external_id"] for r in rows] == [
        "i-early-due",
        "i-late-due",
        "i-no-due",
    ]
