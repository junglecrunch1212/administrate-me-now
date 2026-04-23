"""
Shared sheet-builder utilities.

Per ADMINISTRATEME_BUILD.md §3.11 step 5: every cell in a ``[derived]``
column gets ``cell.protection = Protection(locked=True)``. Sheets tagged
``[read-only]`` lock every cell; sheets tagged ``[bidirectional-shape]``
lock only derived columns.

Sheet-level protection is enabled with a placeholder password — real
secret flow lands in bootstrap (prompt 16). This prompt uses
``adminme-placeholder`` everywhere.
"""

from __future__ import annotations

from typing import Iterable

from openpyxl.styles import Font, Protection
from openpyxl.worksheet.worksheet import Worksheet

SHEET_PASSWORD_PLACEHOLDER = "adminme-placeholder"


def write_header_row(ws: Worksheet, headers: list[str]) -> None:
    """Write the header row, freeze it, and bold the cells."""
    for col_idx, title in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"


def apply_row_protection(
    ws: Worksheet,
    row_idx: int,
    headers: list[str],
    *,
    locked_columns: Iterable[str],
    lock_all: bool = False,
) -> None:
    """Apply ``Protection(locked=...)`` to every cell in a row based on
    whether its column is in ``locked_columns`` (or ``lock_all`` is True).
    """
    locked = set(locked_columns)
    for col_idx, col_name in enumerate(headers, start=1):
        cell = ws.cell(row=row_idx, column=col_idx)
        should_lock = lock_all or (col_name in locked)
        cell.protection = Protection(locked=should_lock)


def apply_sheet_protection(ws: Worksheet, *, readonly: bool) -> None:
    """Enable sheet protection.

    ``readonly=True``: every cell locked (we assume the caller has already
    called ``apply_row_protection(..., lock_all=True)`` on each row).

    ``readonly=False``: mixed lock/unlock; sheet protection is still on so
    Excel honors cell-level locked settings.
    """
    ws.protection.sheet = True
    ws.protection.password = SHEET_PASSWORD_PLACEHOLDER
    # When readonly, also mark column/row insert-disallow at sheet level.
    if readonly:
        ws.protection.insertColumns = True
        ws.protection.insertRows = True
        ws.protection.deleteColumns = True
        ws.protection.deleteRows = True
