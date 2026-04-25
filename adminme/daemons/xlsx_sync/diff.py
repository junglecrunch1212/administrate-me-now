"""
Pure-functional diff core for xlsx reverse-projection.

Given the live workbook rows and the sidecar baseline rows for a single
sheet plus its descriptor, ``diff_sheet`` computes the set of additions,
updates, deletions, and dropped (non-editable-only) edits. The diff core
is sync, side-effect-free, and does NOT import openpyxl, watchdog, the
event bus, or the event log; the daemon (07c-β) wraps it with I/O.

Comparison normalization handles the value-shape mismatches openpyxl
produces vs. JSON-roundtripped sidecar values:
- floats compared at abs-tolerance 1e-9
- ``datetime`` / ``date`` → ``isoformat()``
- ``None`` and ``""`` treated equal
- int / float compared as floats with the float tolerance

ID-column edits (principal blanks then re-types ``task_id``) surface as a
delete-of-old-id PLUS an add-of-new-id, NOT an update. The daemon emits
both with the delete using its undo-window.

Blank ids on new rows stay in ``added`` with the id field empty/None; the
daemon mints the id later (see ``new_id_prefix`` on the descriptor). The
diff core does NOT generate ids.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from adminme.daemons.xlsx_sync.sheet_schemas import (
    SheetDescriptor,
    editable_columns_for,
)

_FLOAT_TOL = 1e-9


@dataclass
class DiffResult:
    """Outcome of diffing a sheet's live rows against the sidecar baseline."""

    added: list[dict[str, Any]] = field(default_factory=list)
    # (row_dict, {column: (sidecar_value, current_value)} for editable columns
    # that actually changed.)
    updated: list[tuple[dict[str, Any], dict[str, tuple[Any, Any]]]] = field(
        default_factory=list
    )
    deleted: list[dict[str, Any]] = field(default_factory=list)
    # Rows where the only differences were on non-editable columns —
    # daemon logs INFO and drops.
    dropped_edits: list[tuple[dict[str, Any], dict[str, tuple[Any, Any]]]] = field(
        default_factory=list
    )


def _normalize(v: Any) -> Any:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def _equal(a: Any, b: Any) -> bool:
    a_n = _normalize(a)
    b_n = _normalize(b)
    if a_n is None and b_n is None:
        return True
    if a_n is None or b_n is None:
        return False
    # Numeric comparison with abs tolerance; covers int vs float as well.
    if isinstance(a_n, bool) or isinstance(b_n, bool):
        return bool(a_n) == bool(b_n)
    if isinstance(a_n, (int, float)) and isinstance(b_n, (int, float)):
        return math.isclose(float(a_n), float(b_n), abs_tol=_FLOAT_TOL)
    return a_n == b_n


def _row_id(row: dict[str, Any], id_column: str) -> Any:
    rid = row.get(id_column)
    if isinstance(rid, str) and rid == "":
        return None
    return rid


def diff_sheet(
    current_rows: list[dict[str, Any]],
    sidecar_rows: list[dict[str, Any]],
    descriptor: SheetDescriptor,
) -> DiffResult:
    """Compute the diff between live rows and the sidecar baseline.

    Both arguments are lists of dicts keyed by sheet header. The descriptor
    identifies the id column, editable columns, and derived columns.
    """
    result = DiffResult()

    by_sidecar_id: dict[Any, dict[str, Any]] = {}
    for r in sidecar_rows:
        rid = _row_id(r, descriptor.id_column)
        if rid is not None:
            by_sidecar_id[rid] = r

    seen_ids: set[Any] = set()

    for cur in current_rows:
        cur_id = _row_id(cur, descriptor.id_column)
        if cur_id is None:
            # New row with blank id. Daemon mints the id later.
            result.added.append(dict(cur))
            continue

        if cur_id not in by_sidecar_id:
            # New row with a freshly-typed id (or a re-typed id whose
            # original was blanked — surface as add; the corresponding
            # delete falls out of the seen_ids loop below).
            result.added.append(dict(cur))
            continue

        seen_ids.add(cur_id)
        side = by_sidecar_id[cur_id]
        editable = editable_columns_for(descriptor, cur)

        editable_changes: dict[str, tuple[Any, Any]] = {}
        non_editable_changes: dict[str, tuple[Any, Any]] = {}

        # Examine every column present on either side, except the id column
        # itself (we already matched by id).
        all_columns = set(cur) | set(side)
        for col in all_columns:
            if col == descriptor.id_column:
                continue
            sv = side.get(col)
            cv = cur.get(col)
            if not _equal(sv, cv):
                if col in editable:
                    editable_changes[col] = (sv, cv)
                else:
                    non_editable_changes[col] = (sv, cv)

        if editable_changes:
            result.updated.append((dict(cur), editable_changes))
        elif non_editable_changes:
            result.dropped_edits.append((dict(cur), non_editable_changes))

    for sid, srow in by_sidecar_id.items():
        if sid not in seen_ids:
            result.deleted.append(dict(srow))

    return result


__all__ = ["DiffResult", "diff_sheet"]
