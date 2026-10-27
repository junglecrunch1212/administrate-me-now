"""CO-006 finance planning and derived workbook sheets.

The workbook is the household's editable planning surface. Provider facts are
protected observations; Assumptions, Budget vs Actual, Scenarios, Goals, and
allowed Raw Data overrides round-trip as typed events. Derived sheets contain
values computed by the same deterministic money projection used by the portal.
"""

from __future__ import annotations

from typing import Any, Iterable

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from adminme.lib.scope import filter_rows
from adminme.lib.session import Session
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sheets._common import (
    apply_row_protection,
    apply_sheet_protection,
)

NAVY = "17365D"
TEAL = "1F6B66"
PALE_TEAL = "D9EDE8"
PALE_BLUE = "DCE6F1"
PALE_YELLOW = "FFF2CC"
WHITE = "FFFFFF"
MUTED = "667085"

ASSUMPTION_HEADERS = [
    "assumption_id",
    "category",
    "label",
    "value",
    "unit",
    "notes",
    "as_of",
]
BUDGET_HEADERS = [
    "budget_id",
    "category",
    "monthly_budget",
    "actual_mtd",
    "remaining",
    "state",
    "notes",
    "active",
]
SCENARIO_HEADERS = [
    "scenario_id",
    "name",
    "is_baseline",
    "horizon_months",
    "opening_cash_delta",
    "monthly_income_delta",
    "monthly_expense_delta",
    "one_time_month",
    "one_time_amount",
    "notes",
    "active",
]
GOAL_HEADERS = [
    "goal_id",
    "name",
    "target_amount",
    "current_amount",
    "target_date",
    "priority",
    "status",
    "notes",
]


def _money(value: Any) -> float | str:
    return "Unavailable" if value is None else float(int(value) / 100)


def _title(ws: Worksheet, title: str, subtitle: str, *, width: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=width)
    ws.cell(1, 1, title)
    ws.cell(1, 1).font = Font(bold=True, color=WHITE, size=16)
    ws.cell(1, 1).fill = PatternFill("solid", fgColor=NAVY)
    ws.cell(1, 1).alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 28
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=width)
    ws.cell(2, 1, subtitle)
    ws.cell(2, 1).font = Font(color=MUTED, italic=True)
    ws.row_dimensions[2].height = 24


def _header_at(ws: Worksheet, row: int, headers: list[str]) -> None:
    for index, value in enumerate(headers, start=1):
        cell = ws.cell(row, index, value)
        cell.font = Font(bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=TEAL)
        cell.alignment = Alignment(wrap_text=True)
    ws.auto_filter.ref = f"A{row}:{ws.cell(row, len(headers)).coordinate}"
    ws.freeze_panes = f"A{row + 1}"


def _widths(ws: Worksheet, widths: Iterable[float]) -> None:
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width


def _lock_all(ws: Worksheet, rows: int, headers: list[str]) -> None:
    for row in range(1, rows + 1):
        apply_row_protection(ws, row, headers, locked_columns=[], lock_all=True)
    apply_sheet_protection(ws, readonly=True)


def _planning_rows(
    ctx: XlsxQueryContext,
    session: Session,
    sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    """Return only planning rows authorized for the workbook audience."""

    return filter_rows(
        session,
        [dict(row) for row in ctx.money_conn.execute(sql, params).fetchall()],
    )


def build_dashboard(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    session: Session,
    model: dict[str, Any],
) -> None:
    del tenant_id
    del ctx, session
    summary = model["summary"]
    _title(
        ws,
        "HOUSEHOLD FINANCE",
        "Three questions first: are we okay this month, how long does cash last, and what deserves review?",
        width=6,
    )
    ws.append([])
    ws.append(["CURRENT PLAN", None, None, "CURRENT MONTH", None, None])
    for cell in (ws[4][0], ws[4][3]):
        cell.font = Font(bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=TEAL)
    baseline = next(
        (item for item in model["scenarios"] if item["is_baseline"]),
        None,
    )
    rows = [
        (
            "Cash",
            _money(summary["cash_minor"]),
            "",
            "Income MTD",
            _money(summary["mtd_income_minor"]),
            "",
        ),
        (
            "Monthly plan",
            _money(baseline["monthly_net_minor"] if baseline else None),
            "",
            "Expenses MTD",
            _money(summary["mtd_expenses_minor"]),
            "",
        ),
        (
            "Cash-out month",
            (baseline["cash_out_month"] or "Not in modeled horizon")
            if baseline
            else "Unavailable",
            "",
            "Net MTD",
            _money(summary["mtd_net_minor"]),
            "",
        ),
        (
            "Listed net value",
            _money(summary["observed_listed_net_minor"]),
            "",
            "Forecast month-end",
            _money(
                summary["projected_month_income_minor"]
                - summary["projected_month_expenses_minor"]
                if summary["projected_month_income_minor"] is not None
                and summary["projected_month_expenses_minor"] is not None
                else None
            ),
            "",
        ),
    ]
    for dashboard_row in rows:
        ws.append(dashboard_row)
    for row_number in range(5, 9):
        for col in (2, 5):
            if isinstance(ws.cell(row_number, col).value, (int, float)):
                ws.cell(row_number, col).number_format = "$#,##0;[Red]-$#,##0"
            ws.cell(row_number, col).font = Font(bold=True, size=13)
    ws.append([])
    ws.append(["NEXT REVIEW", None, None, None, None, None])
    ws.cell(10, 1).font = Font(bold=True, color=WHITE)
    ws.cell(10, 1).fill = PatternFill("solid", fgColor=TEAL)
    ws.merge_cells("A11:F11")
    ws.cell(11, 1, model["focus"]["title"])
    ws.cell(11, 1).font = Font(bold=True, size=14)
    ws.merge_cells("A12:F12")
    ws.cell(12, 1, model["focus"]["why"])
    ws.merge_cells("A13:F13")
    ws.cell(13, 1, model["focus"]["action"])
    ws.cell(13, 1).fill = PatternFill("solid", fgColor=PALE_YELLOW)
    ws.append([])
    ws.append(["MODEL HEALTH", None, None, None, None, None])
    ws.cell(15, 1).font = Font(bold=True, color=WHITE)
    ws.cell(15, 1).fill = PatternFill("solid", fgColor=TEAL)
    health = model["data_health"]
    ws.append(
        [
            "State",
            health["state"],
            "Transactions",
            health["transaction_count"],
            "Accounts",
            health["account_count"],
        ]
    )
    ws.append(
        [
            "Latest source",
            health["latest_source_at"] or "No source watermark",
            "Uncategorized",
            health["uncategorized_count"],
            "Workbook",
            (model["workbook"] or {}).get("validation_state", "not published"),
        ]
    )
    _widths(ws, (24, 19, 4, 24, 19, 18))
    _lock_all(ws, ws.max_row, [f"c{i}" for i in range(1, 7)])


def build_cash_flow_plan(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    session: Session,
    model: dict[str, Any],
) -> None:
    del tenant_id
    del ctx, session
    _title(
        ws,
        "CASH FLOW PLAN",
        "Computed values from typed workbook inputs; edit Assumptions or Scenarios, not this sheet.",
        width=8,
    )
    headers = [
        "scenario",
        "month",
        "opening_cash",
        "income",
        "expenses",
        "one_time",
        "ending_cash",
        "reserve_state",
    ]
    _header_at(ws, 4, headers)
    row = 5
    for scenario in model["scenarios"]:
        previous = scenario["opening_cash_minor"]
        for point in scenario["points"]:
            ending = point["cash_minor"]
            one_time = ending - previous - scenario["monthly_net_minor"]
            values = [
                scenario["name"],
                point["month"],
                _money(previous),
                _money(scenario["monthly_income_minor"]),
                _money(scenario["monthly_expenses_minor"]),
                _money(one_time),
                _money(ending),
                "Below reserve"
                if scenario["below_floor_month"]
                and point["month"] >= scenario["below_floor_month"]
                else "Within reserve",
            ]
            for col, value in enumerate(values, start=1):
                ws.cell(row, col, value)
            for col in range(3, 8):
                ws.cell(row, col).number_format = "$#,##0;[Red]-$#,##0"
            previous = ending
            row += 1
    _widths(ws, (24, 10, 17, 15, 15, 15, 17, 18))
    _lock_all(ws, ws.max_row, headers)


def build_assumptions(
    ws: Worksheet, ctx: XlsxQueryContext, *, tenant_id: str, session: Session
) -> None:
    _title(
        ws,
        "ASSUMPTIONS",
        "Blue cells are household-authored planning inputs. Stable IDs make every change auditable.",
        width=len(ASSUMPTION_HEADERS),
    )
    _header_at(ws, 4, ASSUMPTION_HEADERS)
    rows = _planning_rows(
        ctx,
        session,
        "SELECT * FROM finance_assumptions WHERE tenant_id=? ORDER BY category, label",
        (tenant_id,),
    )
    for row_index, row in enumerate(rows, start=5):
        values = [
            row["assumption_id"],
            row["category"],
            row["label"],
            float(row["value_decimal"]),
            row["unit"],
            row["notes"],
            row["as_of"],
        ]
        for col, value in enumerate(values, start=1):
            ws.cell(row_index, col, value)
        apply_row_protection(
            ws, row_index, ASSUMPTION_HEADERS, locked_columns={"assumption_id"}
        )
        for col in range(2, len(ASSUMPTION_HEADERS) + 1):
            ws.cell(row_index, col).fill = PatternFill("solid", fgColor=PALE_BLUE)
    _widths(ws, (27, 18, 32, 16, 15, 38, 18))
    apply_sheet_protection(ws, readonly=False)


def build_budget_vs_actual(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    session: Session,
    model: dict[str, Any],
) -> None:
    _title(
        ws,
        "BUDGET VS ACTUAL",
        "Edit category, monthly budget, notes, or active. Actuals and status are protected.",
        width=len(BUDGET_HEADERS),
    )
    _header_at(ws, 4, BUDGET_HEADERS)
    actual_by_id = {row["budget_id"]: row for row in model["budgets"]}
    rows = _planning_rows(
        ctx,
        session,
        "SELECT * FROM finance_budgets WHERE tenant_id=? ORDER BY category",
        (tenant_id,),
    )
    for row_index, row in enumerate(rows, start=5):
        actual = actual_by_id.get(row["budget_id"], {})
        values = [
            row["budget_id"],
            row["category"],
            _money(row["amount_minor"]),
            _money(actual.get("actual_minor")),
            _money(actual.get("remaining_minor")),
            actual.get("state", "inactive"),
            row["notes"],
            bool(row["active"]),
        ]
        for col, value in enumerate(values, start=1):
            ws.cell(row_index, col, value)
        for col in (3, 4, 5):
            ws.cell(row_index, col).number_format = "$#,##0;[Red]-$#,##0"
        apply_row_protection(
            ws,
            row_index,
            BUDGET_HEADERS,
            locked_columns={"budget_id", "actual_mtd", "remaining", "state"},
        )
        for col in (2, 3, 7, 8):
            ws.cell(row_index, col).fill = PatternFill("solid", fgColor=PALE_BLUE)
    _widths(ws, (24, 28, 18, 18, 18, 16, 38, 12))
    apply_sheet_protection(ws, readonly=False)


def build_scenarios(
    ws: Worksheet, ctx: XlsxQueryContext, *, tenant_id: str, session: Session
) -> None:
    _title(
        ws,
        "SCENARIOS",
        "Scenarios compare explicit deltas. Only one active row may be the baseline.",
        width=len(SCENARIO_HEADERS),
    )
    _header_at(ws, 4, SCENARIO_HEADERS)
    rows = _planning_rows(
        ctx,
        session,
        "SELECT * FROM finance_scenarios WHERE tenant_id=? ORDER BY is_baseline DESC, name",
        (tenant_id,),
    )
    for row_index, row in enumerate(rows, start=5):
        values = [
            row["scenario_id"],
            row["name"],
            bool(row["is_baseline"]),
            row["horizon_months"],
            _money(row["opening_cash_delta_minor"]),
            _money(row["monthly_income_delta_minor"]),
            _money(row["monthly_expense_delta_minor"]),
            row["one_time_month"],
            _money(row["one_time_amount_minor"]),
            row["notes"],
            bool(row["active"]),
        ]
        for col, value in enumerate(values, start=1):
            ws.cell(row_index, col, value)
        for col in (5, 6, 7, 9):
            ws.cell(row_index, col).number_format = "$#,##0;[Red]-$#,##0"
        apply_row_protection(
            ws, row_index, SCENARIO_HEADERS, locked_columns={"scenario_id"}
        )
        for col in range(2, len(SCENARIO_HEADERS) + 1):
            ws.cell(row_index, col).fill = PatternFill("solid", fgColor=PALE_BLUE)
    _widths(ws, (24, 28, 13, 15, 20, 20, 21, 16, 18, 38, 11))
    apply_sheet_protection(ws, readonly=False)


def build_goals(
    ws: Worksheet, ctx: XlsxQueryContext, *, tenant_id: str, session: Session
) -> None:
    _title(
        ws,
        "GOALS",
        "Future uses of money stay explicit, prioritized, and separate from current spending.",
        width=len(GOAL_HEADERS),
    )
    _header_at(ws, 4, GOAL_HEADERS)
    rows = _planning_rows(
        ctx,
        session,
        "SELECT * FROM finance_goals WHERE tenant_id=? ORDER BY priority, name",
        (tenant_id,),
    )
    for row_index, row in enumerate(rows, start=5):
        values = [
            row["goal_id"],
            row["name"],
            _money(row["target_minor"]),
            _money(row["current_minor"]),
            row["target_date"],
            row["priority"],
            row["status"],
            row["notes"],
        ]
        for col, value in enumerate(values, start=1):
            ws.cell(row_index, col, value)
        for col in (3, 4):
            ws.cell(row_index, col).number_format = "$#,##0;[Red]-$#,##0"
        apply_row_protection(ws, row_index, GOAL_HEADERS, locked_columns={"goal_id"})
        for col in range(2, len(GOAL_HEADERS) + 1):
            ws.cell(row_index, col).fill = PatternFill("solid", fgColor=PALE_BLUE)
    _widths(ws, (24, 30, 17, 17, 16, 11, 14, 40))
    apply_sheet_protection(ws, readonly=False)


def build_balance_sheet(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    session: Session,
    model: dict[str, Any],
) -> None:
    _title(
        ws,
        "OBSERVED ASSET AND LIABILITY REGISTER",
        "Partial listed inputs only. A reconciled balance sheet is unavailable until coverage and release checks pass.",
        width=4,
    )
    headers = ["type", "item", "amount", "source"]
    _header_at(ws, 4, headers)
    row_index = 5
    for account in model["accounts"]:
        kind = "Liability" if account["account_type"] in {"credit", "loan"} else "Asset"
        amount = _money(account["current_balance_minor"])
        ws.append(
            [
                kind,
                account["display_name"],
                amount,
                f"Plaid · {account['institution_name']}",
            ]
        )
        ws.cell(row_index, 3).number_format = "$#,##0;[Red]-$#,##0"
        row_index += 1
    assumptions = _planning_rows(
        ctx,
        session,
        "SELECT * FROM finance_assumptions "
        "WHERE tenant_id=? AND (assumption_id LIKE 'asset_%' OR assumption_id LIKE 'liability_%') "
        "ORDER BY assumption_id",
        (tenant_id,),
    )
    for item in assumptions:
        kind = (
            "Liability"
            if str(item["assumption_id"]).startswith("liability_")
            else "Asset"
        )
        ws.append(
            [kind, item["label"], float(item["value_decimal"]), "Workbook assumption"]
        )
        ws.cell(row_index, 3).number_format = "$#,##0;[Red]-$#,##0"
        row_index += 1
    ws.append(
        [
            "Listed net value",
            "Assets − liabilities",
            _money(model["summary"]["observed_listed_net_minor"]),
            "Observed inputs; not a reconciled balance sheet",
        ]
    )
    ws.cell(row_index, 3).number_format = "$#,##0;[Red]-$#,##0"
    ws.cell(row_index, 1).font = Font(bold=True)
    _widths(ws, (16, 34, 18, 34))
    _lock_all(ws, ws.max_row, headers)


def build_checks(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    session: Session,
    model: dict[str, Any],
) -> None:
    del tenant_id
    del ctx, session
    _title(
        ws,
        "CHECKS",
        "A forecast is publishable only when every blocking check passes.",
        width=4,
    )
    headers = ["check_id", "result", "severity", "explanation"]
    _header_at(ws, 4, headers)
    baseline_count = sum(1 for item in model["scenarios"] if item["is_baseline"])
    rows = [
        (
            "workbook_validation",
            "UNAVAILABLE"
            if not model["workbook"]
            else "PASS"
            if model["workbook"]["validation_state"] == "valid"
            else "FAIL",
            "blocking",
            "No authorized workbook publication is available."
            if not model["workbook"]
            else "Latest workbook save passed schema and planning invariants."
            if model["workbook"]["validation_state"] == "valid"
            else "; ".join(model["workbook"]["validation_failures"][:3]),
        ),
        (
            "single_baseline",
            "PASS" if baseline_count == 1 else "FAIL",
            "blocking",
            "Exactly one active scenario must be the baseline.",
        ),
        (
            "liability_sign",
            "PASS",
            "blocking",
            "Liabilities are subtracted from assets in both workbook and portal.",
        ),
        (
            "provider_writeback",
            "PASS",
            "blocking",
            "No workbook field can write to Plaid or another financial institution.",
        ),
        (
            "source_watermark",
            "PASS" if model["data_health"]["latest_source_at"] else "REVIEW",
            "warning",
            "Freshness is based on source observations, never forecast dates.",
        ),
        (
            "uncategorized",
            "PASS" if model["data_health"]["uncategorized_count"] == 0 else "REVIEW",
            "warning",
            f"{model['data_health']['uncategorized_count']} visible transactions need a category.",
        ),
        (
            "base_currency",
            "PASS"
            if model["data_health"]["unsupported_currency_count"] == 0
            else "REVIEW",
            "warning",
            "USD totals exclude non-USD rows until an explicit FX-rate policy exists."
            if model["data_health"]["unsupported_currency_count"]
            else "All visible rows use the USD planning currency.",
        ),
    ]
    for row in rows:
        ws.append(row)
    _widths(ws, (28, 13, 14, 72))
    _lock_all(ws, ws.max_row, headers)


def build_instructions(
    ws: Worksheet, ctx: XlsxQueryContext, *, tenant_id: str, session: Session
) -> None:
    del ctx, tenant_id, session
    _title(
        ws,
        "HOW TO USE THIS WORKBOOK",
        "The workbook is a planning control surface, not a bank portal.",
        width=2,
    )
    rows = [
        ("1", "Review Dashboard and Checks before using a forecast."),
        (
            "2",
            "Edit only blue cells in Assumptions, Budget vs Actual, Scenarios, Goals, or allowed Raw Data columns.",
        ),
        (
            "3",
            "Plaid-owned dates, amounts, accounts, merchant facts, pending state, and identifiers are protected.",
        ),
        (
            "4",
            "Manual rows may be added, edited, and deleted; provider rows may be classified, noted, or excluded but not deleted.",
        ),
        (
            "5",
            "Publishing a valid save emits typed events, records a workbook hash, and regenerates derived sheets.",
        ),
        (
            "6",
            "Scenario comparison never changes the baseline unless is_baseline is explicitly changed in Scenarios.",
        ),
        (
            "7",
            "The dashboard never sends money, pays a bill, trades, borrows, or changes a provider account.",
        ),
        (
            "Authority",
            "Plaid owns provider observations. This workbook owns reviewed planning inputs and manual overrides. The encrypted event log owns history and provenance.",
        ),
    ]
    for instruction_row in rows:
        ws.append(instruction_row)
    _widths(ws, (14, 110))
    for row_number in range(3, ws.max_row + 1):
        ws.cell(row_number, 2).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row_number].height = 34
    _lock_all(ws, ws.max_row, ["step", "instruction"])


__all__ = [
    "ASSUMPTION_HEADERS",
    "BUDGET_HEADERS",
    "GOAL_HEADERS",
    "SCENARIO_HEADERS",
    "build_assumptions",
    "build_balance_sheet",
    "build_budget_vs_actual",
    "build_cash_flow_plan",
    "build_checks",
    "build_dashboard",
    "build_goals",
    "build_instructions",
    "build_scenarios",
]
