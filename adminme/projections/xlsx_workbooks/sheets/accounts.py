"""
Accounts sheet builder [read-only].

Per ADMINISTRATEME_BUILD.md §3.11 finance Sheet 2. Fully protected.
``current_balance``, ``available_balance``, ``as_of``, ``plaid_linked``,
``link_healthy`` are not yet modelled at v1 — rendered blank.
"""

from __future__ import annotations

import json

from openpyxl.worksheet.worksheet import Worksheet

from adminme.lib.scope import filter_rows
from adminme.lib.session import Session
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


def build(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    session: Session,
) -> None:
    write_header_row(ws, HEADERS)

    finance_rows = filter_rows(
        session,
        [
            dict(row)
            for row in ctx.money_conn.execute(
                "SELECT * FROM financial_accounts WHERE tenant_id = ? "
                "ORDER BY display_name ASC, account_id ASC",
                (tenant_id,),
            ).fetchall()
        ],
    )
    if finance_rows:
        for i, row in enumerate(finance_rows, start=2):
            values = [
                row["account_id"],
                row["institution_name"],
                row["display_name"],
                row["account_type"],
                row["last4"],
                row["current_balance_minor"] / 100.0
                if row["current_balance_minor"] is not None
                else None,
                row["available_balance_minor"] / 100.0
                if row["available_balance_minor"] is not None
                else None,
                row["as_of"],
                True,
                bool(row["link_healthy"]),
            ]
            for col_idx, value in enumerate(values, start=1):
                ws.cell(row=i, column=col_idx, value=value)
            apply_row_protection(ws, i, HEADERS, locked_columns=[], lock_all=True)
        apply_sheet_protection(ws, readonly=True)
        return

    rows = filter_rows(
        session,
        [
            dict(row)
            for row in ctx.places_assets_accounts_conn.execute(
                "SELECT * FROM accounts WHERE tenant_id = ? "
                "ORDER BY display_name ASC, account_id ASC",
                (tenant_id,),
            ).fetchall()
        ],
    )

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
