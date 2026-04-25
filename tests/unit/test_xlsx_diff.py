"""
Unit tests for adminme.daemons.xlsx_sync.diff (07c-α-4).

The diff core is sync, pure-functional, and does not touch openpyxl,
watchdog, or the event log. Daemon-side I/O lives in 07c-β.

Coverage:
- empty input → empty diff
- adds-only / deletes-only / editable-update / dropped-edit flows
- id-column edit surfaces as delete + add
- blank id on a new row stays in added (daemon mints id later)
- value normalization: float tolerance, None ≡ "", date / datetime → ISO
- Raw Data per-row callable: manual row's amount edit → update;
  Plaid row's amount edit → dropped_edit; same diff round.
"""

from __future__ import annotations

from datetime import date, datetime

from adminme.daemons.xlsx_sync.diff import diff_sheet
from adminme.daemons.xlsx_sync.sheet_schemas import (
    descriptor_for,
    BIDIRECTIONAL_DESCRIPTORS,
)
from adminme.projections.xlsx_workbooks import (
    FINANCE_WORKBOOK_NAME,
    OPS_WORKBOOK_NAME,
)


def _tasks() -> object:
    d = descriptor_for(OPS_WORKBOOK_NAME, "Tasks")
    assert d is not None
    return d


def _raw_data() -> object:
    d = descriptor_for(FINANCE_WORKBOOK_NAME, "Raw Data")
    assert d is not None
    return d


def test_descriptors_cover_expected_sheets() -> None:
    keys = {(d.workbook, d.sheet) for d in BIDIRECTIONAL_DESCRIPTORS}
    assert keys == {
        (OPS_WORKBOOK_NAME, "Tasks"),
        (OPS_WORKBOOK_NAME, "Commitments"),
        (OPS_WORKBOOK_NAME, "Recurrences"),
        (FINANCE_WORKBOOK_NAME, "Raw Data"),
    }


def test_no_changes_empty_diff() -> None:
    rows = [{"task_id": "t1", "title": "do laundry", "status": "open"}]
    out = diff_sheet(rows, list(rows), _tasks())
    assert out.added == []
    assert out.updated == []
    assert out.deleted == []
    assert out.dropped_edits == []


def test_added_only() -> None:
    out = diff_sheet(
        [{"task_id": "t1", "title": "x"}],
        [],
        _tasks(),
    )
    assert len(out.added) == 1
    assert out.added[0]["task_id"] == "t1"
    assert out.deleted == []
    assert out.updated == []


def test_deleted_only() -> None:
    out = diff_sheet(
        [],
        [{"task_id": "t1", "title": "x"}],
        _tasks(),
    )
    assert out.added == []
    assert len(out.deleted) == 1
    assert out.deleted[0]["task_id"] == "t1"


def test_editable_change_lands_in_updated() -> None:
    sidecar = [{"task_id": "t1", "title": "x", "status": "open"}]
    current = [{"task_id": "t1", "title": "y", "status": "open"}]
    out = diff_sheet(current, sidecar, _tasks())
    assert out.added == []
    assert out.deleted == []
    assert len(out.updated) == 1
    row, changes = out.updated[0]
    assert row["task_id"] == "t1"
    assert changes == {"title": ("x", "y")}


def test_derived_only_change_drops_to_dropped_edits() -> None:
    # ``created_at`` is in always_derived for Tasks. A change there is
    # non-editable → goes to dropped_edits, NOT updated.
    sidecar = [
        {"task_id": "t1", "title": "x", "created_at": "2026-01-01T00:00:00Z"}
    ]
    current = [
        {"task_id": "t1", "title": "x", "created_at": "2026-04-25T00:00:00Z"}
    ]
    out = diff_sheet(current, sidecar, _tasks())
    assert out.added == []
    assert out.deleted == []
    assert out.updated == []
    assert len(out.dropped_edits) == 1
    _, changes = out.dropped_edits[0]
    assert "created_at" in changes


def test_id_column_edit_surfaces_as_delete_plus_add() -> None:
    sidecar = [{"task_id": "t1", "title": "x"}]
    current = [{"task_id": "t2", "title": "x"}]
    out = diff_sheet(current, sidecar, _tasks())
    assert len(out.added) == 1
    assert out.added[0]["task_id"] == "t2"
    assert len(out.deleted) == 1
    assert out.deleted[0]["task_id"] == "t1"


def test_blank_id_on_new_row_stays_in_added() -> None:
    # Diff core does NOT mint ids; daemon does.
    sidecar: list[dict] = []
    current = [{"task_id": "", "title": "do dishes"}]
    out = diff_sheet(current, sidecar, _tasks())
    assert len(out.added) == 1
    assert out.added[0]["title"] == "do dishes"
    # id stays as blank/empty until daemon mints it.
    assert out.added[0]["task_id"] == ""


def test_float_tolerance_treats_close_values_as_equal() -> None:
    # ``effort_min`` is editable for Tasks; here we use ``urgency`` which
    # is also editable. 1.0 vs 1.0000000001 → equal under 1e-9 tolerance.
    sidecar = [{"task_id": "t1", "urgency": 1.0}]
    current = [{"task_id": "t1", "urgency": 1.0 + 1e-12}]
    out = diff_sheet(current, sidecar, _tasks())
    assert out.updated == []
    assert out.dropped_edits == []


def test_none_equals_empty_string() -> None:
    sidecar = [{"task_id": "t1", "notes": None}]
    current = [{"task_id": "t1", "notes": ""}]
    out = diff_sheet(current, sidecar, _tasks())
    assert out.updated == []


def test_datetime_normalized_to_iso_for_compare() -> None:
    iso = "2026-04-25T09:00:00"
    sidecar = [{"task_id": "t1", "due_date": iso}]
    current = [{"task_id": "t1", "due_date": datetime(2026, 4, 25, 9, 0, 0)}]
    out = diff_sheet(current, sidecar, _tasks())
    assert out.updated == []


def test_date_normalized_to_iso_for_compare() -> None:
    sidecar = [{"task_id": "t1", "due_date": "2026-04-25"}]
    current = [{"task_id": "t1", "due_date": date(2026, 4, 25)}]
    out = diff_sheet(current, sidecar, _tasks())
    assert out.updated == []


def test_raw_data_manual_row_amount_edit_yields_update() -> None:
    # Manual row: full editable set — amount edits land in updated.
    desc = _raw_data()
    sidecar = [
        {
            "txn_id": "flow_a",
            "date": "2026-04-20",
            "amount": 12.34,
            "is_manual": True,
            "assigned_category": "groceries",
            "notes": None,
            "memo": None,
        }
    ]
    current = [
        {
            "txn_id": "flow_a",
            "date": "2026-04-20",
            "amount": 99.99,
            "is_manual": True,
            "assigned_category": "groceries",
            "notes": None,
            "memo": None,
        }
    ]
    out = diff_sheet(current, sidecar, desc)
    assert len(out.updated) == 1
    _, changes = out.updated[0]
    assert "amount" in changes


def test_raw_data_plaid_row_amount_edit_drops() -> None:
    # Plaid row: amount is NOT in the editable set. Edit → dropped_edits.
    desc = _raw_data()
    sidecar = [
        {
            "txn_id": "flow_p",
            "date": "2026-04-20",
            "amount": 12.34,
            "is_manual": False,
            "assigned_category": None,
            "notes": None,
            "memo": None,
        }
    ]
    current = [
        {
            "txn_id": "flow_p",
            "date": "2026-04-20",
            "amount": 99.99,
            "is_manual": False,
            "assigned_category": None,
            "notes": None,
            "memo": None,
        }
    ]
    out = diff_sheet(current, sidecar, desc)
    assert out.updated == []
    assert len(out.dropped_edits) == 1
    _, changes = out.dropped_edits[0]
    assert "amount" in changes


def test_raw_data_callable_evaluated_per_row_in_same_diff() -> None:
    # One manual row + one Plaid row in the same diff — the callable is
    # invoked per-row so the manual row yields an update on amount and the
    # Plaid row yields a dropped_edit on amount.
    desc = _raw_data()
    sidecar = [
        {
            "txn_id": "flow_m",
            "amount": 1.0,
            "is_manual": True,
            "assigned_category": "x",
        },
        {
            "txn_id": "flow_p",
            "amount": 2.0,
            "is_manual": False,
            "assigned_category": "y",
        },
    ]
    current = [
        {
            "txn_id": "flow_m",
            "amount": 99.0,
            "is_manual": True,
            "assigned_category": "x",
        },
        {
            "txn_id": "flow_p",
            "amount": 88.0,
            "is_manual": False,
            "assigned_category": "y",
        },
    ]
    out = diff_sheet(current, sidecar, desc)
    assert len(out.updated) == 1
    assert out.updated[0][0]["txn_id"] == "flow_m"
    assert len(out.dropped_edits) == 1
    assert out.dropped_edits[0][0]["txn_id"] == "flow_p"
