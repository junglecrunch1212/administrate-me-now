"""
Commitments sheet builder [bidirectional-shape, forward-only in 07b].

Per ADMINISTRATEME_BUILD.md §3.11 Sheet 3. ``confidence``, ``strength``,
and ``source_summary`` are derived from the extraction pipeline and
protected.

Default row set is the full projection (not just active) per the prompt
spec; the principal can see cancelled/completed rows for context.
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
    "commitment_id",
    "owed_by_member",
    "owed_to_party",
    "kind",
    "text_summary",
    "suggested_due",
    "status",
    "confidence",
    "strength",
    "source_summary",
]

DERIVED_COLUMNS: set[str] = {"confidence", "strength", "source_summary"}


def build(ws: Worksheet, ctx: XlsxQueryContext, *, tenant_id: str) -> None:
    write_header_row(ws, HEADERS)

    rows = ctx.commitments_conn.execute(
        """
        SELECT commitment_id, owed_by_party, owed_to_party, kind,
               description, due_at, status, confidence, source_interaction_id
          FROM commitments
         WHERE tenant_id = ?
         ORDER BY proposed_at ASC, commitment_id ASC
        """,
        (tenant_id,),
    ).fetchall()

    for i, row in enumerate(rows, start=2):
        # strength is not stored on the commitments table; the schema
        # carries only confidence. 'strength' is computed at projection-time
        # from the extraction pipeline output; prompt 07b renders it as the
        # confidence bucket.
        confidence = row["confidence"]
        if confidence is None:
            strength = None
        elif confidence >= 0.75:
            strength = "confident"
        else:
            strength = "weak"
        values = [
            row["commitment_id"],
            row["owed_by_party"],
            row["owed_to_party"],
            row["kind"],
            row["description"],
            row["due_at"],
            row["status"],
            confidence,
            strength,
            row["source_interaction_id"],
        ]
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=i, column=col_idx, value=value)
        apply_row_protection(ws, i, HEADERS, locked_columns=DERIVED_COLUMNS)

    apply_sheet_protection(ws, readonly=False)
