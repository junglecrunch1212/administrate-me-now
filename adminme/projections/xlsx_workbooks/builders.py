"""
Workbook-level builders — assemble sheets, write atomically.

Per ADMINISTRATEME_BUILD.md §3.11 forward-projection algorithm step 7:
write to a temp file, rename atomically. The lock is acquired and
released by the caller (the projection's ``_regenerate`` coroutine).

Per prompt 08a: each workbook builder constructs a single internal
Session via ``build_internal_session("xlsx_workbooks", "device", tenant_id)``
at entry [§6.1, BUILD.md L3-continued]. The session is passed to each
sheet builder; sheets that route reads through the projection
``queries.py`` modules (a future refactor) will use it directly. The
current sheet builders read via raw SQL on the projection connections —
they accept the session for forward-compatibility and to anchor the
session-construction site in one place per workbook.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook

from adminme.lib.session import build_internal_session
from adminme.projections.money.queries import finance_snapshot
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sheets import (
    accounts as accounts_sheet,
    commitments as commitments_sheet,
    metadata_finance,
    metadata_ops,
    finance_model,
    people as people_sheet,
    raw_data as raw_data_sheet,
    recurrences as recurrences_sheet,
    tasks as tasks_sheet,
)


_PRIVATE_FILE_MODE = 0o600


def _save_private_workbook(wb: Workbook, path: Path) -> None:
    """Atomically publish a workbook readable only by the service account."""
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            _PRIVATE_FILE_MODE,
        )
        os.close(fd)
        wb.save(str(tmp))
        os.chmod(tmp, _PRIVATE_FILE_MODE)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def build_ops_workbook(
    path: Path,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    last_event_id: str,
) -> None:
    """Regenerate adminme-ops.xlsx atomically at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Internal session anchored once per workbook build per [§6.1]; sheet
    # builders accept and propagate it for any query-function calls.
    session = build_internal_session("xlsx_workbooks", "device", tenant_id)
    wb = Workbook()
    # Remove the default sheet openpyxl creates.
    default = wb.active
    if default is not None:
        wb.remove(default)

    tasks_ws = wb.create_sheet("Tasks")
    tasks_sheet.build(tasks_ws, ctx, tenant_id=tenant_id, session=session)

    recurrences_ws = wb.create_sheet("Recurrences")
    recurrences_sheet.build(recurrences_ws, ctx, tenant_id=tenant_id, session=session)

    commitments_ws = wb.create_sheet("Commitments")
    commitments_sheet.build(commitments_ws, ctx, tenant_id=tenant_id, session=session)

    people_ws = wb.create_sheet("People")
    people_sheet.build(people_ws, ctx, tenant_id=tenant_id, session=session)

    metadata_ws = wb.create_sheet("Metadata")
    metadata_ops.build(
        metadata_ws,
        ctx,
        tenant_id=tenant_id,
        last_event_id=last_event_id,
        session=session,
    )

    _save_private_workbook(wb, path)


def build_finance_workbook(
    path: Path,
    ctx: XlsxQueryContext,
    *,
    tenant_id: str,
    last_event_id: str,
) -> None:
    """Regenerate adminme-finance.xlsx atomically at ``path``.

    CO-006 workbook contract: Dashboard, Cash Flow Plan, Assumptions, Budget
    vs Actual, Scenarios, Goals, Balance Sheet, Raw Data, Accounts, Checks,
    Metadata, and Instructions. Planning/input sheets are bidirectional;
    provider facts and derived outputs remain protected.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # A household workbook may read household-shared sensitive planning rows,
    # but never another adult's private privileged provider rows. The synthetic
    # principal has only shared scopes and therefore enforces that boundary.
    session = build_internal_session("xlsx_workbooks", "principal", tenant_id)
    # Capture the projection boundary time once. Every derived sheet in this
    # workbook must reflect the same month/day cut rather than independently
    # consulting the wall clock while the file is assembled.
    model = finance_snapshot(ctx.money_conn, session, as_of=datetime.now(timezone.utc))
    wb = Workbook()
    default = wb.active
    if default is not None:
        wb.remove(default)

    dashboard_ws = wb.create_sheet("Dashboard")
    finance_model.build_dashboard(
        dashboard_ws,
        ctx,
        tenant_id=tenant_id,
        session=session,
        model=model,
    )

    cash_flow_ws = wb.create_sheet("Cash Flow Plan")
    finance_model.build_cash_flow_plan(
        cash_flow_ws,
        ctx,
        tenant_id=tenant_id,
        session=session,
        model=model,
    )

    assumptions_ws = wb.create_sheet("Assumptions")
    finance_model.build_assumptions(
        assumptions_ws, ctx, tenant_id=tenant_id, session=session
    )

    budgets_ws = wb.create_sheet("Budget vs Actual")
    finance_model.build_budget_vs_actual(
        budgets_ws,
        ctx,
        tenant_id=tenant_id,
        session=session,
        model=model,
    )

    scenarios_ws = wb.create_sheet("Scenarios")
    finance_model.build_scenarios(
        scenarios_ws, ctx, tenant_id=tenant_id, session=session
    )

    goals_ws = wb.create_sheet("Goals")
    finance_model.build_goals(goals_ws, ctx, tenant_id=tenant_id, session=session)

    balance_ws = wb.create_sheet("Balance Sheet")
    finance_model.build_balance_sheet(
        balance_ws,
        ctx,
        tenant_id=tenant_id,
        session=session,
        model=model,
    )

    raw_ws = wb.create_sheet("Raw Data")
    raw_data_sheet.build(raw_ws, ctx, tenant_id=tenant_id, session=session)

    accounts_ws = wb.create_sheet("Accounts")
    accounts_sheet.build(accounts_ws, ctx, tenant_id=tenant_id, session=session)

    checks_ws = wb.create_sheet("Checks")
    finance_model.build_checks(
        checks_ws,
        ctx,
        tenant_id=tenant_id,
        session=session,
        model=model,
    )

    metadata_ws = wb.create_sheet("Metadata")
    metadata_finance.build(
        metadata_ws,
        ctx,
        tenant_id=tenant_id,
        last_event_id=last_event_id,
        session=session,
    )

    instructions_ws = wb.create_sheet("Instructions")
    finance_model.build_instructions(
        instructions_ws, ctx, tenant_id=tenant_id, session=session
    )

    _save_private_workbook(wb, path)
