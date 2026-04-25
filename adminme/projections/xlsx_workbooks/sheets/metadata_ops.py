"""
Metadata sheet builder for adminme-ops.xlsx [read-only].

Per ADMINISTRATEME_BUILD.md §3.11 Sheet 7. Small provenance sheet so the
principal can verify what projection state produced the file.
"""

from __future__ import annotations

from datetime import datetime, timezone

from openpyxl.worksheet.worksheet import Worksheet

from adminme.lib.session import Session
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sheets._common import (
    apply_row_protection,
    apply_sheet_protection,
    write_header_row,
)

HEADERS: list[str] = ["key", "value"]


def build(
    ws: Worksheet,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    last_event_id: str,
    session: Session,
) -> None:
    write_header_row(ws, HEADERS)

    rows: list[tuple[str, str]] = [
        ("workbook_name", "adminme-ops.xlsx"),
        ("projection", "xlsx_workbooks"),
        ("projection_version", "1"),
        ("generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("last_event_id_consumed", last_event_id or ""),
        ("tenant_id", tenant_id),
    ]

    for i, (key, value) in enumerate(rows, start=2):
        ws.cell(row=i, column=1, value=key)
        ws.cell(row=i, column=2, value=value)
        apply_row_protection(ws, i, HEADERS, locked_columns=[], lock_all=True)

    apply_sheet_protection(ws, readonly=True)
