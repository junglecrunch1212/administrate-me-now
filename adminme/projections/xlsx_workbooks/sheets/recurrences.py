"""
Recurrences sheet builder [bidirectional-shape, forward-only in 07b].

Per ADMINISTRATEME_BUILD.md §3.11 Sheet 2. ``next_due`` and
``last_completed_at`` are derived and protected.
"""

from __future__ import annotations

from openpyxl.worksheet.worksheet import Worksheet

from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sheets._common import (
    apply_row_protection,
    apply_sheet_protection,
    write_header_row,
)

HEADERS: list[str] = [
    "recurrence_id",
    "title",
    "cadence",
    "next_due",
    "assigned_member",
    "notes",
    "active",
    "last_completed_at",
]

DERIVED_COLUMNS: set[str] = {"next_due", "last_completed_at"}


def build(ws: Worksheet, ctx: XlsxQueryContext, *, tenant_id: str) -> None:
    write_header_row(ws, HEADERS)

    rows = ctx.recurrences_conn.execute(
        """
        SELECT recurrence_id, kind, rrule, next_occurrence,
               linked_kind, linked_id, notes
          FROM recurrences
         WHERE tenant_id = ?
         ORDER BY next_occurrence ASC, recurrence_id ASC
        """,
        (tenant_id,),
    ).fetchall()

    for i, row in enumerate(rows, start=2):
        assigned = row["linked_id"] if row["linked_kind"] == "party" else None
        values = [
            row["recurrence_id"],
            row["kind"],
            row["rrule"],
            row["next_occurrence"],
            assigned,
            row["notes"],
            True,
            None,  # last_completed_at — not tracked in recurrences projection
        ]
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=i, column=col_idx, value=value)
        apply_row_protection(ws, i, HEADERS, locked_columns=DERIVED_COLUMNS)

    apply_sheet_protection(ws, readonly=False)
