"""
Unit tests for adminme.projections.xlsx_workbooks.sidecar (07c-α).

The sidecar pathway underpins the xlsx round-trip: forward writes a per-sheet
JSON snapshot inside the same lock as the xlsx write, and the reverse daemon
(07c-β) diffs the live workbook against the sidecar to detect principal edits.

Tests cover:
- ``sidecar_dir`` is a SIBLING of the workbooks dir (not a child) — critical
  so that a future watchdog scoped to the workbooks dir cannot self-trigger
  on sidecar writes.
- ``sidecar_path`` interpolates workbook + sheet correctly.
- write/read roundtrips for both bidirectional and read-only shapes.
- ``read_*`` returns None when the file is missing.
- ``write_readonly_state`` stores ONLY ``content_hash`` (no row data) and
  hashes are deterministic 64-char SHA-256 hex.
- Atomic writes leave no ``.tmp.<pid>`` leftovers.
- Re-writing the same path overwrites cleanly.
"""

from __future__ import annotations

from pathlib import Path

from adminme.projections.xlsx_workbooks.sidecar import (
    hash_readonly_sheet,
    read_readonly_state,
    read_sheet_state,
    sidecar_dir,
    sidecar_path,
    write_readonly_state,
    write_sheet_state,
)


def test_sidecar_dir_is_sibling_of_workbooks_dir(tmp_path: Path) -> None:
    workbooks = tmp_path / "projections" / "xlsx_workbooks"
    expected = tmp_path / "projections" / ".xlsx-state"
    assert sidecar_dir(workbooks) == expected
    # Strictly NOT inside the workbooks dir; a watchdog over the workbooks
    # dir must not see sidecar writes.
    state = sidecar_dir(workbooks)
    assert workbooks not in state.parents
    assert state.parent == workbooks.parent


def test_sidecar_path_interpolates_workbook_and_sheet(tmp_path: Path) -> None:
    workbooks = tmp_path / "projections" / "xlsx_workbooks"
    p = sidecar_path(workbooks, "adminme-ops.xlsx", "Tasks")
    assert p == tmp_path / "projections" / ".xlsx-state" / "adminme-ops.xlsx" / "Tasks.json"


def test_write_then_read_sheet_state_roundtrip(tmp_path: Path) -> None:
    workbooks = tmp_path / "projections" / "xlsx_workbooks"
    rows = [
        {"task_id": "t1", "title": "do laundry", "status": "open"},
        {"task_id": "t2", "title": "buy milk", "status": "done"},
    ]
    write_sheet_state(workbooks, "adminme-ops.xlsx", "Tasks", rows)
    out = read_sheet_state(workbooks, "adminme-ops.xlsx", "Tasks")
    assert out == rows


def test_read_missing_returns_none_for_both_shapes(tmp_path: Path) -> None:
    workbooks = tmp_path / "projections" / "xlsx_workbooks"
    assert read_sheet_state(workbooks, "adminme-ops.xlsx", "Tasks") is None
    assert read_readonly_state(workbooks, "adminme-ops.xlsx", "People") is None


def test_readonly_state_stores_only_content_hash(tmp_path: Path) -> None:
    workbooks = tmp_path / "projections" / "xlsx_workbooks"
    rows = [["party_id", "display_name"], ["p1", "X"], ["p2", "Y"]]
    p = write_readonly_state(workbooks, "adminme-ops.xlsx", "People", rows)
    import json

    data = json.loads(p.read_text(encoding="utf-8"))
    assert "content_hash" in data
    assert "rows" not in data
    h = data["content_hash"]
    assert isinstance(h, str)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    assert read_readonly_state(workbooks, "adminme-ops.xlsx", "People") == h


def test_readonly_hash_determinism(tmp_path: Path) -> None:
    rows_a = [["party_id", "display_name"], ["p1", "X"]]
    rows_b = [["party_id", "display_name"], ["p1", "X"]]
    rows_c = [["party_id", "display_name"], ["p1", "Y"]]
    assert hash_readonly_sheet(rows_a) == hash_readonly_sheet(rows_b)
    assert hash_readonly_sheet(rows_a) != hash_readonly_sheet(rows_c)


def test_atomic_write_leaves_no_tmp_files(tmp_path: Path) -> None:
    workbooks = tmp_path / "projections" / "xlsx_workbooks"
    write_sheet_state(workbooks, "adminme-ops.xlsx", "Tasks", [{"task_id": "t1"}])
    write_readonly_state(
        workbooks, "adminme-ops.xlsx", "People", [["party_id"], ["p1"]]
    )
    state_dir = sidecar_dir(workbooks) / "adminme-ops.xlsx"
    leftovers = list(state_dir.glob("*.tmp.*"))
    assert leftovers == []


def test_write_overwrites_cleanly(tmp_path: Path) -> None:
    workbooks = tmp_path / "projections" / "xlsx_workbooks"
    write_sheet_state(workbooks, "adminme-ops.xlsx", "Tasks", [{"task_id": "t1"}])
    write_sheet_state(
        workbooks, "adminme-ops.xlsx", "Tasks", [{"task_id": "t2", "title": "new"}]
    )
    out = read_sheet_state(workbooks, "adminme-ops.xlsx", "Tasks")
    assert out == [{"task_id": "t2", "title": "new"}]
