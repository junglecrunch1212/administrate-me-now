"""
Raw Data sheet builder [bidirectional-shape, forward-only in 07b].

Per ADMINISTRATEME_BUILD.md §3.11 finance Sheet 1. Plaid-sourced rows
(``is_manual=FALSE``) have ``date``, ``account_last4``, ``merchant_name``,
``amount``, and ``plaid_category`` protected — Plaid is authoritative.
Principal-added rows (``is_manual=TRUE``) are fully editable.

``txn_id`` is always derived (backend-assigned). Rows with
``deleted_at IS NOT NULL`` are excluded — soft-deleted manual rows are
kept in the projection for rebuild correctness but not rendered.

``account_last4`` is resolved by joining to the accounts projection's
``attributes_json``; if not present, rendered blank.
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
    "txn_id",
    "date",
    "account_last4",
    "merchant_name",
    "merchant_category",
    "amount",
    "memo",
    "plaid_category",
    "assigned_category",
    "notes",
    "is_manual",
]

# Columns always derived (backend-assigned).
ALWAYS_DERIVED: set[str] = {"txn_id", "plaid_category"}

# Columns Plaid owns on non-manual rows.
PLAID_AUTHORITATIVE: set[str] = {
    "date",
    "account_last4",
    "merchant_name",
    "amount",
}


def _resolve_last4(ctx: XlsxQueryContext, tenant_id: str) -> dict[str, str]:
    rows = ctx.places_assets_accounts_conn.execute(
        "SELECT account_id, attributes_json FROM accounts WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        attrs_raw = r["attributes_json"] or "{}"
        try:
            attrs = json.loads(attrs_raw)
        except json.JSONDecodeError:
            attrs = {}
        last4 = attrs.get("last4")
        if last4:
            out[r["account_id"]] = str(last4)
    return out


def build(ws: Worksheet, ctx: XlsxQueryContext, *, tenant_id: str) -> None:
    write_header_row(ws, HEADERS)

    last4_by_account = _resolve_last4(ctx, tenant_id)

    rows = ctx.money_conn.execute(
        """
        SELECT flow_id, occurred_at, linked_account, amount_minor, currency,
               notes, category, is_manual
          FROM money_flows
         WHERE tenant_id = ?
           AND deleted_at IS NULL
         ORDER BY occurred_at DESC, flow_id ASC
        """,
        (tenant_id,),
    ).fetchall()

    for i, row in enumerate(rows, start=2):
        is_manual = bool(row["is_manual"])
        # Projection stores a single category column; on the sheet we split
        # into plaid_category (backend) vs assigned_category (principal).
        if is_manual:
            plaid_category = None
            assigned_category = row["category"]
        else:
            plaid_category = row["category"]
            assigned_category = None
        values = [
            row["flow_id"],
            row["occurred_at"],
            last4_by_account.get(row["linked_account"] or ""),
            None,  # merchant_name — not yet modelled at v1
            None,  # merchant_category
            row["amount_minor"] / 100.0 if row["amount_minor"] is not None else None,
            row["notes"],
            plaid_category,
            assigned_category,
            None,  # notes_2 column is unused; kept to preserve header shape
            is_manual,
        ]
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=i, column=col_idx, value=value)

        # Protection strategy:
        # - always-derived columns (txn_id, plaid_category) always locked.
        # - on manual rows: only always-derived are locked.
        # - on plaid rows: plaid-authoritative columns also locked.
        locked = set(ALWAYS_DERIVED)
        if not is_manual:
            locked |= PLAID_AUTHORITATIVE
        apply_row_protection(ws, i, HEADERS, locked_columns=locked)

    apply_sheet_protection(ws, readonly=False)
