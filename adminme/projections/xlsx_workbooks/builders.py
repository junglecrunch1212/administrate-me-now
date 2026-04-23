"""
Workbook-level builders — assemble sheets, write atomically.

Per ADMINISTRATEME_BUILD.md §3.11 forward-projection algorithm step 7:
write to a temp file, rename atomically. The lock is acquired and
released by the caller (the projection's ``_regenerate`` coroutine).
"""

from __future__ import annotations

import os
from pathlib import Path

from openpyxl import Workbook

from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sheets import (
    commitments as commitments_sheet,
    metadata_ops,
    people as people_sheet,
    recurrences as recurrences_sheet,
    tasks as tasks_sheet,
)


def build_ops_workbook(
    path: Path,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    last_event_id: str,
) -> None:
    """Regenerate adminme-ops.xlsx atomically at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    # Remove the default sheet openpyxl creates.
    default = wb.active
    if default is not None:
        wb.remove(default)

    tasks_ws = wb.create_sheet("Tasks")
    tasks_sheet.build(tasks_ws, ctx, tenant_id=tenant_id)

    recurrences_ws = wb.create_sheet("Recurrences")
    recurrences_sheet.build(recurrences_ws, ctx, tenant_id=tenant_id)

    commitments_ws = wb.create_sheet("Commitments")
    commitments_sheet.build(commitments_ws, ctx, tenant_id=tenant_id)

    people_ws = wb.create_sheet("People")
    people_sheet.build(people_ws, ctx, tenant_id=tenant_id)

    metadata_ws = wb.create_sheet("Metadata")
    metadata_ops.build(
        metadata_ws, ctx, tenant_id=tenant_id, last_event_id=last_event_id
    )

    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        wb.save(str(tmp))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def build_finance_workbook(
    path: Path,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    last_event_id: str,
) -> None:
    """Regenerate adminme-finance.xlsx. Implemented in phase 07b-3."""
    raise NotImplementedError("build_finance_workbook lands in phase 07b-3")
