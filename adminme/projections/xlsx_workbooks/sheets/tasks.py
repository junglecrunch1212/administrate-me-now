"""
Tasks sheet builder [bidirectional-shape, forward-only in 07b].

Per ADMINISTRATEME_BUILD.md §3.11 Sheet 1. Columns marked ``[derived]``
are protected at the cell level so the reverse daemon (prompt 07c) does
not misinterpret them as principal edits.
"""

from __future__ import annotations

from openpyxl.worksheet.worksheet import Worksheet

from adminme.lib.session import Session
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sheets._common import (
    apply_row_protection,
    apply_sheet_protection,
    write_header_row,
)

HEADERS: list[str] = [
    "task_id",
    "title",
    "status",
    "assigned_member",
    "owed_to_party",
    "due_date",
    "urgency",
    "effort_min",
    "energy",
    "context",
    "notes",
    "created_at",
    "completed_at",
]

DERIVED_COLUMNS: set[str] = {"task_id", "created_at", "completed_at"}


def build(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    session: Session,
) -> None:
    write_header_row(ws, HEADERS)

    rows = ctx.tasks_conn.execute(
        """
        SELECT task_id, title, status, assignee_party, domain,
               due_date, energy, effort, notes, created_at, completed_at
          FROM tasks
         WHERE tenant_id = ?
         ORDER BY created_at ASC, task_id ASC
        """,
        (tenant_id,),
    ).fetchall()

    for i, row in enumerate(rows, start=2):
        values = [
            row["task_id"],
            row["title"],
            row["status"],
            row["assignee_party"],
            None,  # owed_to_party — tasks don't carry this directly
            row["due_date"],
            None,  # urgency — not modelled in tasks projection
            None,  # effort_min — projection stores enum, not minutes
            row["energy"],
            row["domain"],
            row["notes"],
            row["created_at"],
            row["completed_at"],
        ]
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=i, column=col_idx, value=value)
        apply_row_protection(ws, i, HEADERS, locked_columns=DERIVED_COLUMNS)

    apply_sheet_protection(ws, readonly=False)
