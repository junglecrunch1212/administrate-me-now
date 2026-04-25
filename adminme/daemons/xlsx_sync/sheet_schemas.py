"""
Per-sheet diff descriptors for the xlsx reverse daemon.

Source of truth for ``editable_columns`` and ``always_derived`` is the
matching sheet builder in ``adminme/projections/xlsx_workbooks/sheets/``;
the descriptor must match its ``HEADERS`` and ``DERIVED_COLUMNS`` exactly.
Drift breaks round-trip silently. Prompt 07.5 will audit equivalence.

The reverse daemon (07c-β) consumes a descriptor per bidirectional sheet
to decide:
- which column is the row id (``id_column``);
- which columns the principal may edit (``editable_columns`` — frozenset
  for static sheets, callable per-row for Raw Data because Plaid vs manual
  rows have different editable sets);
- which columns are always backend-assigned (``always_derived``);
- what event to emit when a row is added / updated / deleted (``*_emit_event``;
  None means "drop this disposition" — log at ``*_drop_log_level`` instead);
- whether deletes get the 5s undo window (``deletes_use_undo_window``);
- whether new ids should be backend-minted with a given prefix
  (``new_id_prefix``).

People / Accounts / Metadata sheets are NOT described here: they are read-
only. The reverse daemon handles them via a separate "WARN if hash drifted"
code path in 07c-β.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from adminme.projections.xlsx_workbooks import (
    FINANCE_WORKBOOK_NAME,
    OPS_WORKBOOK_NAME,
)


@dataclass(frozen=True)
class SheetDescriptor:
    """Reverse-projection metadata for a single bidirectional sheet."""

    workbook: str
    sheet: str
    id_column: str
    # Static frozenset, OR a per-row callable returning a frozenset. The
    # callable form is needed for Raw Data: manual vs Plaid rows have
    # different editable sets.
    editable_columns: frozenset[str] | Callable[[dict[str, Any]], frozenset[str]]
    always_derived: frozenset[str]

    adds_emit_event: str | None
    updates_emit_event: str | None
    deletes_emit_event: str | None

    deletes_use_undo_window: bool = False
    new_id_prefix: str | None = None

    add_drop_log_level: str = "INFO"
    update_drop_log_level: str = "INFO"
    delete_drop_log_level: str = "INFO"

    add_drop_reason: str = ""
    update_drop_reason: str = ""
    delete_drop_reason: str = ""

    plaid_authoritative_columns: frozenset[str] = field(default_factory=frozenset)


def _raw_data_editable(row: dict[str, Any]) -> frozenset[str]:
    """Editable column set for a Raw Data row, dispatched on ``is_manual``.

    Manual rows: full set including date/account/merchant/amount.
    Plaid rows: only ``assigned_category``, ``notes``, ``memo`` (Plaid is
    authoritative for the rest per [§10 invariant 4]).
    """
    is_manual = bool(row.get("is_manual"))
    if is_manual:
        return frozenset(
            {
                "date",
                "account_last4",
                "merchant_name",
                "merchant_category",
                "amount",
                "memo",
                "assigned_category",
                "notes",
            }
        )
    return frozenset({"assigned_category", "notes", "memo"})


_TASKS = SheetDescriptor(
    workbook=OPS_WORKBOOK_NAME,
    sheet="Tasks",
    id_column="task_id",
    editable_columns=frozenset(
        {
            "title",
            "status",
            "assigned_member",
            "owed_to_party",
            "due_date",
            "urgency",
            "effort_min",
            "energy",
            "context",
            "notes",
        }
    ),
    always_derived=frozenset({"task_id", "created_at", "completed_at"}),
    adds_emit_event="task.created",
    updates_emit_event="task.updated",
    deletes_emit_event="task.deleted",
    deletes_use_undo_window=True,
    new_id_prefix="tsk_",
)


_COMMITMENTS = SheetDescriptor(
    workbook=OPS_WORKBOOK_NAME,
    sheet="Commitments",
    id_column="commitment_id",
    editable_columns=frozenset(
        {
            "owed_by_member",
            "owed_to_party",
            "kind",
            "text_summary",
            "suggested_due",
            "status",
        }
    ),
    always_derived=frozenset({"confidence", "strength", "source_summary"}),
    adds_emit_event=None,
    updates_emit_event="commitment.edited",
    deletes_emit_event=None,
    add_drop_log_level="INFO",
    add_drop_reason="commitments are pipeline-proposed only per [§4.2]",
    delete_drop_log_level="INFO",
    delete_drop_reason="commitments cancel via API",
)


_RECURRENCES = SheetDescriptor(
    workbook=OPS_WORKBOOK_NAME,
    sheet="Recurrences",
    id_column="recurrence_id",
    editable_columns=frozenset(
        {"title", "cadence", "assigned_member", "notes", "active"}
    ),
    always_derived=frozenset({"next_due", "last_completed_at"}),
    adds_emit_event="recurrence.added",
    updates_emit_event="recurrence.updated",
    deletes_emit_event=None,
    new_id_prefix="rec_",
    delete_drop_log_level="INFO",
    delete_drop_reason="recurrences not deletable in v1",
)


_RAW_DATA = SheetDescriptor(
    workbook=FINANCE_WORKBOOK_NAME,
    sheet="Raw Data",
    id_column="txn_id",
    editable_columns=_raw_data_editable,
    always_derived=frozenset({"txn_id", "plaid_category", "is_manual"}),
    plaid_authoritative_columns=frozenset(
        {"date", "account_last4", "merchant_name", "amount"}
    ),
    # Adds / updates / deletes are dispatched per-row inside the daemon
    # based on is_manual, with non-manual rows hitting the drop reasons
    # below at WARN level. Static add/update/delete events are listed for
    # the manual-row case; the daemon checks the row's is_manual before
    # emitting.
    adds_emit_event="money_flow.manually_added",
    updates_emit_event=None,  # money_flow.recategorized not registered yet
    deletes_emit_event="money_flow.manually_deleted",
    deletes_use_undo_window=True,
    new_id_prefix="flow_",
    add_drop_log_level="WARN",
    add_drop_reason="non-manual row added via xlsx; rejecting per [§13.4]",
    update_drop_log_level="INFO",
    update_drop_reason=(
        "Plaid-authoritative; assigned_category/notes/memo edits deferred"
    ),
    delete_drop_log_level="WARN",
    delete_drop_reason="Plaid row deletion via xlsx ignored",
)


BIDIRECTIONAL_DESCRIPTORS: tuple[SheetDescriptor, ...] = (
    _TASKS,
    _COMMITMENTS,
    _RECURRENCES,
    _RAW_DATA,
)


def descriptor_for(workbook: str, sheet: str) -> SheetDescriptor | None:
    """Return the descriptor for ``(workbook, sheet)``; None if not bidirectional.

    People / Accounts / Metadata are read-only and have no descriptor here.
    """
    for d in BIDIRECTIONAL_DESCRIPTORS:
        if d.workbook == workbook and d.sheet == sheet:
            return d
    return None


def editable_columns_for(
    descriptor: SheetDescriptor, row: dict[str, Any]
) -> frozenset[str]:
    """Resolve the editable column set for a row, handling the callable form."""
    ec = descriptor.editable_columns
    if callable(ec):
        return ec(row)
    return ec


__all__ = [
    "SheetDescriptor",
    "BIDIRECTIONAL_DESCRIPTORS",
    "descriptor_for",
    "editable_columns_for",
]
