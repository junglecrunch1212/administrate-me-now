"""
People sheet builder [read-only].

Per ADMINISTRATEME_BUILD.md §3.11 Sheet 4. A curated human-readable CRM
overview for weekly reviews. Fully protected — the real CRM surface is
in the console.

Only persons and organizations are included; households (containers) are
omitted. Tier / tags / open_commitments are not yet modelled at v1, so
those columns are rendered blank (future prompts will fill them).
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
    "party_id",
    "display_name",
    "tier",
    "tags",
    "primary_email",
    "primary_phone",
    "last_contact",
    "cadence_target",
    "open_commitments",
]


def build(ws: Worksheet, ctx: XlsxQueryContext, *, tenant_id: str) -> None:
    write_header_row(ws, HEADERS)

    rows = ctx.parties_conn.execute(
        """
        SELECT party_id, display_name, kind
          FROM parties
         WHERE tenant_id = ?
           AND kind IN ('person', 'organization')
         ORDER BY sort_name ASC, party_id ASC
        """,
        (tenant_id,),
    ).fetchall()

    for i, row in enumerate(rows, start=2):
        values = [
            row["party_id"],
            row["display_name"],
            None,  # tier — not yet modelled
            None,  # tags
            None,  # primary_email
            None,  # primary_phone
            None,  # last_contact
            None,  # cadence_target
            None,  # open_commitments
        ]
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=i, column=col_idx, value=value)
        apply_row_protection(ws, i, HEADERS, locked_columns=[], lock_all=True)

    apply_sheet_protection(ws, readonly=True)
