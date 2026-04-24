"""
Accounts sheet builder [read-only].

Per ADMINISTRATEME_BUILD.md §3.11 finance Sheet 2. Fully protected.
``current_balance``, ``available_balance``, ``as_of``, ``plaid_linked``,
``link_healthy`` are not yet modelled at v1 — rendered blank.
"""

from __future__ import annotations

import json

from openpyxl.worksheet.worksheet import Worksheet

from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sheets._common import (
    apply_row_protection,
    apply_sheet_protection,
    write_header_row,
)

HEADERS: list[str] = [
    "account_id",
    "institution",
    "account_name",
    "account_type",
    "last4",
    "current_balance",
    "available_balance",
    "as_of",
    "plaid_linked",
    "link_healthy",
]


def build(ws: Worksheet, ctx: XlsxQueryContext, *, tenant_id: str) -> None:
    write_header_row(ws, HEADERS)

    rows = ctx.places_assets_accounts_conn.execute(
        """
        SELECT account_id, display_name, organization, kind, status,
               attributes_json
          FROM accounts
         WHERE tenant_id = ?
         ORDER BY display_name ASC, account_id ASC
        """,
        (tenant_id,),
    ).fetchall()

    for i, row in enumerate(rows, start=2):
        attrs_raw = row["attributes_json"] or "{}"
        try:
            attrs = json.loads(attrs_raw)
        except json.JSONDecodeError:
            attrs = {}
        last4 = attrs.get("last4")
        values = [
            row["account_id"],
            row["organization"],
            row["display_name"],
            row["kind"],
            last4,
            None,  # current_balance
            None,  # available_balance
            None,  # as_of
            None,  # plaid_linked
            None,  # link_healthy
        ]
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=i, column=col_idx, value=value)
        apply_row_protection(ws, i, HEADERS, locked_columns=[], lock_all=True)

    apply_sheet_protection(ws, readonly=True)
