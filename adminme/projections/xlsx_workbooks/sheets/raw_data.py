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

from adminme.lib.session import Session
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
    "member_id",
    "account_id",
    "currency",
    "included",
    "pending",
    "source_adapter",
]

# Columns always derived (backend-assigned).
# is_manual is derived from event type by the projection — never
# principal-authored on the sheet. Kept in sync with the descriptor's
# always_derived in adminme/daemons/xlsx_sync/sheet_schemas.py;
# equivalence is asserted in tests/unit/test_xlsx_finance_workbook.py.
ALWAYS_DERIVED: set[str] = {
    "txn_id",
    "account_last4",
    "plaid_category",
    "is_manual",
    "member_id",
    "pending",
    "source_adapter",
}

# Columns Plaid owns on non-manual rows.
PLAID_AUTHORITATIVE: set[str] = {
    "date",
    "account_last4",
    "merchant_name",
    "amount",
    "account_id",
    "currency",
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


def build(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    session: Session,
) -> None:
    write_header_row(ws, HEADERS)

    last4_by_account = _resolve_last4(ctx, tenant_id)

    rows = ctx.money_conn.execute(
        """
        SELECT flow_id, occurred_at, linked_account, amount_minor, currency,
               notes, memo, category, provider_category, assigned_category,
               member_id, merchant_name, merchant_category, pending, included,
               source_adapter, is_manual
          FROM money_flows
         WHERE tenant_id = ?
           AND deleted_at IS NULL
           AND removed_at IS NULL
           AND owner_scope = 'shared:household'
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
            plaid_category = row["provider_category"] or row["category"]
            assigned_category = row["assigned_category"]
        values = [
            row["flow_id"],
            row["occurred_at"],
            last4_by_account.get(row["linked_account"] or ""),
            row["merchant_name"],
            row["merchant_category"],
            row["amount_minor"] / 100.0 if row["amount_minor"] is not None else None,
            row["memo"],
            plaid_category,
            assigned_category,
            row["notes"],
            is_manual,
            row["member_id"],
            row["linked_account"],
            row["currency"],
            bool(row["included"]),
            bool(row["pending"]),
            row["source_adapter"],
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
