"""
People sheet builder [read-only].

Per ADMINISTRATEME_BUILD.md §3.11 Sheet 4. A curated human-readable CRM
overview for weekly reviews. Fully protected — the real CRM surface is
the Core/Capture API and this workbook is a derived read surface.

Only persons and organizations are included; households (containers) are
omitted. Every included column is backed by a retained projection.
"""

from __future__ import annotations

from openpyxl.worksheet.worksheet import Worksheet

from adminme.lib.session import Session
from adminme.projections.commitments.queries import by_party
from adminme.projections.parties.queries import all_parties
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sheets._common import (
    apply_row_protection,
    apply_sheet_protection,
    write_header_row,
)

HEADERS: list[str] = [
    "party_id",
    "display_name",
    "primary_email",
    "primary_phone",
    "open_commitments",
]


def build(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    session: Session,
) -> None:
    write_header_row(ws, HEADERS)

    rows = [
        row
        for row in all_parties(ctx.parties_conn, session)
        if row["kind"] in {"person", "organization"}
    ]

    for i, row in enumerate(rows, start=2):
        identifiers = ctx.parties_conn.execute(
            """SELECT kind, value FROM identifiers
                 WHERE tenant_id = ? AND party_id = ?
                 ORDER BY primary_for_kind DESC, verified DESC, identifier_id""",
            (tenant_id, row["party_id"]),
        ).fetchall()
        primary_email = next(
            (item["value"] for item in identifiers if item["kind"] == "email"),
            None,
        )
        primary_phone = next(
            (item["value"] for item in identifiers if item["kind"] == "phone"),
            None,
        )
        commitments = by_party(ctx.commitments_conn, session, party_id=row["party_id"])
        open_commitments = sum(
            item["status"] in {"pending", "snoozed", "deferred", "blocked"}
            for item in commitments
        )
        values = [
            row["party_id"],
            row["display_name"],
            primary_email,
            primary_phone,
            open_commitments,
        ]
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=i, column=col_idx, value=value)
        apply_row_protection(ws, i, HEADERS, locked_columns=[], lock_all=True)

    apply_sheet_protection(ws, readonly=True)
