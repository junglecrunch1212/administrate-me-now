"""
money projection read queries.

Per ADMINISTRATEME_BUILD.md §3.9 and SYSTEM_INVARIANTS.md §2, §6, §12.
Every public query takes a ``session: Session`` per [§6.1] and runs
through ``scope.filter_rows`` before return. Coach-role sessions strip
``financial_*`` columns inside ``filter_rows`` per [§13] / DIAGRAMS §5.

Per DECISIONS.md §D4: the CRM/ops spine (which includes money) is a
shared L3 concern. Queries exclude soft-deleted rows (``deleted_at IS
NOT NULL``) unless a caller explicitly wants them.
"""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def get_money_flow(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    flow_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM money_flows WHERE tenant_id = ? AND flow_id = ?",
        (session.tenant_id, flow_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def flows_in_range(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    start_iso: str,
    end_iso: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM money_flows
         WHERE tenant_id = ?
           AND occurred_at BETWEEN ? AND ?
           AND deleted_at IS NULL
           AND removed_at IS NULL
           AND included = 1
         ORDER BY occurred_at DESC
        """,
        (session.tenant_id, start_iso, end_iso),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def flows_by_category(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    category: str,
    since_iso: str | None = None,
) -> list[dict[str, Any]]:
    if since_iso is None:
        rows = conn.execute(
            """
            SELECT * FROM money_flows
             WHERE tenant_id = ?
               AND category = ?
               AND deleted_at IS NULL
               AND removed_at IS NULL
               AND included = 1
             ORDER BY occurred_at DESC
            """,
            (session.tenant_id, category),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM money_flows
             WHERE tenant_id = ?
               AND category = ?
               AND occurred_at >= ?
               AND deleted_at IS NULL
               AND removed_at IS NULL
               AND included = 1
             ORDER BY occurred_at DESC
            """,
            (session.tenant_id, category, since_iso),
        ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def flows_by_account(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    account_id: str,
    since_iso: str | None = None,
) -> list[dict[str, Any]]:
    if since_iso is None:
        rows = conn.execute(
            """
            SELECT * FROM money_flows
             WHERE tenant_id = ?
               AND linked_account = ?
               AND deleted_at IS NULL
               AND removed_at IS NULL
               AND included = 1
             ORDER BY occurred_at DESC
            """,
            (session.tenant_id, account_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM money_flows
             WHERE tenant_id = ?
               AND linked_account = ?
               AND occurred_at >= ?
               AND deleted_at IS NULL
               AND removed_at IS NULL
               AND included = 1
             ORDER BY occurred_at DESC
            """,
            (session.tenant_id, account_id, since_iso),
        ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def category_totals(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    since_iso: str,
) -> dict[str, int]:
    """Sum ``amount_minor`` by category, restricted to rows the session
    may read (privileged-other rows are excluded from the total)."""
    rows = conn.execute(
        """
        SELECT * FROM money_flows
         WHERE tenant_id = ?
           AND occurred_at >= ?
           AND deleted_at IS NULL
           AND removed_at IS NULL
           AND included = 1
           AND category IS NOT NULL
        """,
        (session.tenant_id, since_iso),
    ).fetchall()
    visible = filter_rows(session, [dict(r) for r in rows])
    totals: dict[str, int] = {}
    for r in visible:
        cat = r.get("category")
        if cat is None:
            continue
        totals[cat] = totals.get(cat, 0) + int(r.get("amount_minor") or 0)
    return totals


def manual_flows(
    conn: sqlcipher3.Connection,
    session: Session,
) -> list[dict[str, Any]]:
    """Manually-added, not-yet-deleted flows. Used by prompt 07b to avoid
    double-counting when it regenerates the Raw Data sheet."""
    rows = conn.execute(
        """
        SELECT * FROM money_flows
         WHERE tenant_id = ?
           AND is_manual = 1
           AND deleted_at IS NULL
         ORDER BY occurred_at DESC
        """,
        (session.tenant_id,),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def finance_snapshot(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Compose the deterministic Finance projection consumed by workbook/UI.

    Inputs are typed projection rows only. No model call, heuristic
    classification, provider request, or wall-clock read occurs here; callers
    supply ``as_of`` so replay/process fixtures remain stable.
    """

    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    month_start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month = (
        month_start.replace(day=1)
        if month_start.month > 1
        else month_start.replace(year=month_start.year - 1, month=12)
    )
    if month_start.month > 1:
        previous_month = month_start.replace(month=month_start.month - 1)
    previous_start = previous_month.replace(day=1)
    # Compare like-for-like elapsed time, not a partial current month against a
    # full prior month. Cap at the prior month boundary so a long current month
    # following a shorter month can never pull current-month rows into the
    # comparison window.
    previous_end = min(
        previous_start + timedelta(seconds=(as_of - month_start).total_seconds()),
        month_start,
    )
    trailing_start_month = month_start.month - 3
    trailing_year = month_start.year
    while trailing_start_month <= 0:
        trailing_start_month += 12
        trailing_year -= 1
    trailing_start = month_start.replace(
        year=trailing_year, month=trailing_start_month, day=1
    )

    base_currency = "USD"
    observed_flows = _visible_rows(
        conn,
        session,
        """
        SELECT * FROM money_flows
         WHERE tenant_id=? AND deleted_at IS NULL AND removed_at IS NULL
           AND included=1
         ORDER BY occurred_at DESC, flow_id ASC
        """,
        (session.tenant_id,),
    )
    # Pending observations are visible for reconciliation but do not affect
    # cash-flow, budget, net, runway, or scenario calculations.
    all_accounts = _visible_rows(
        conn,
        session,
        "SELECT * FROM financial_accounts WHERE tenant_id=? ORDER BY display_name",
        (session.tenant_id,),
    )
    # CO-006 is a USD planning contract. Provider rows in another currency stay
    # visible as source-health exceptions but are never arithmetically combined
    # without an explicit, reviewed FX-rate fact.
    flows = [
        row
        for row in observed_flows
        if not row.get("pending") and row.get("currency") == base_currency
    ]
    accounts = [row for row in all_accounts if row.get("currency") == base_currency]
    assumptions = _visible_rows(
        conn,
        session,
        "SELECT * FROM finance_assumptions WHERE tenant_id=? ORDER BY category, label",
        (session.tenant_id,),
    )
    all_budgets = _visible_rows(
        conn,
        session,
        "SELECT * FROM finance_budgets WHERE tenant_id=? AND active=1 ORDER BY category",
        (session.tenant_id,),
    )
    budgets = [row for row in all_budgets if row.get("currency") == base_currency]
    scenarios = _visible_rows(
        conn,
        session,
        "SELECT * FROM finance_scenarios WHERE tenant_id=? AND active=1 "
        "ORDER BY is_baseline DESC, name",
        (session.tenant_id,),
    )
    goals = _visible_rows(
        conn,
        session,
        "SELECT * FROM finance_goals WHERE tenant_id=? ORDER BY priority, name",
        (session.tenant_id,),
    )

    workbook_row = conn.execute(
        "SELECT * FROM finance_workbook_state WHERE tenant_id=? "
        "ORDER BY published_at DESC LIMIT 1",
        (session.tenant_id,),
    ).fetchone()
    workbook = dict(workbook_row) if workbook_row is not None else None
    if workbook is not None:
        try:
            workbook["validation_failures"] = json.loads(
                str(workbook.pop("validation_failures_json"))
            )
        except (json.JSONDecodeError, TypeError):
            workbook["validation_failures"] = ["invalid validation metadata"]

    def in_period(row: dict[str, Any], start: datetime, end: datetime) -> bool:
        value = _parse_iso(row.get("occurred_at"))
        return value is not None and start <= value < end

    mtd = [row for row in flows if in_period(row, month_start, as_of)]
    prior = [row for row in flows if in_period(row, previous_start, previous_end)]
    trailing = [row for row in flows if in_period(row, trailing_start, month_start)]
    mtd_income, mtd_expenses = _income_expense(mtd)
    prior_income, prior_expenses = _income_expense(prior)
    trailing_income, trailing_expenses = _income_expense(trailing)
    trailing_monthly_income = trailing_income // 3
    trailing_monthly_expenses = trailing_expenses // 3

    assumption_values = {
        str(row["assumption_id"]): _assumption_minor(row) for row in assumptions
    }
    base_income = assumption_values.get("base_monthly_income", trailing_monthly_income)
    base_expenses = assumption_values.get(
        "base_monthly_expenses", trailing_monthly_expenses
    )
    minimum_cash = assumption_values.get("minimum_cash_floor", 0)
    cash_balance = sum(
        int(row.get("current_balance_minor") or 0)
        for row in accounts
        if row.get("account_type") == "depository"
    )
    if not accounts:
        cash_balance = assumption_values.get("opening_cash", 0)

    days_elapsed = max(1, as_of.day)
    days_in_month = monthrange(as_of.year, as_of.month)[1]
    projected_month_expenses = int(
        (
            Decimal(mtd_expenses) * Decimal(days_in_month) / Decimal(days_elapsed)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    projected_month_income = int(
        (Decimal(mtd_income) * Decimal(days_in_month) / Decimal(days_elapsed)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )

    category_actuals: dict[str, int] = {}
    for row in mtd:
        if row.get("kind") == "received":
            continue
        category = str(row.get("category") or "Uncategorized")
        category_actuals[category] = category_actuals.get(category, 0) + abs(
            int(row.get("amount_minor") or 0)
        )
    budget_rows: list[dict[str, Any]] = []
    for budget in budgets:
        actual = category_actuals.get(str(budget["category"]), 0)
        planned = int(budget["amount_minor"])
        budget_rows.append(
            {
                "budget_id": budget["budget_id"],
                "category": budget["category"],
                "planned_minor": planned,
                "actual_minor": actual,
                "remaining_minor": planned - actual,
                "state": "watch" if actual > planned else "within_plan",
            }
        )
    budget_rows.sort(key=lambda row: (row["remaining_minor"], row["category"]))

    scenario_rows = scenarios or [
        {
            "scenario_id": "base",
            "name": "Current plan",
            "is_baseline": 1,
            "horizon_months": 60,
            "opening_cash_delta_minor": 0,
            "monthly_income_delta_minor": 0,
            "monthly_expense_delta_minor": 0,
            "one_time_month": None,
            "one_time_amount_minor": 0,
            "notes": None,
        }
    ]
    forecasts = [
        _forecast_scenario(
            scenario,
            opening_cash=cash_balance,
            monthly_income=base_income,
            monthly_expenses=base_expenses,
            minimum_cash=minimum_cash,
        )
        for scenario in scenario_rows
    ]

    net_worth = 0
    for row in accounts:
        balance = int(row.get("current_balance_minor") or 0)
        if row.get("account_type") in {"credit", "loan"}:
            net_worth -= abs(balance)
        else:
            net_worth += balance
    net_worth += sum(
        value for key, value in assumption_values.items() if key.startswith("asset_")
    )
    net_worth -= sum(
        abs(value)
        for key, value in assumption_values.items()
        if key.startswith("liability_")
    )

    latest_source = max(
        (
            value
            for value in (
                *(
                    _parse_iso(row.get("updated_at") or row.get("occurred_at"))
                    for row in observed_flows
                ),
                *(_parse_iso(row.get("as_of")) for row in all_accounts),
            )
            if value is not None
        ),
        default=None,
    )
    stale = (
        latest_source is not None and (as_of - latest_source).total_seconds() > 172800
    )
    unsupported_currency_count = sum(
        1 for row in observed_flows if row.get("currency") != base_currency
    ) + sum(1 for row in all_accounts if row.get("currency") != base_currency)
    unsupported_currency_count += sum(
        1 for row in all_budgets if row.get("currency") != base_currency
    )
    focus = _finance_focus(
        workbook=workbook,
        flows=observed_flows,
        accounts=accounts,
        forecasts=forecasts,
        budgets=budget_rows,
        stale=stale,
        unsupported_currency_count=unsupported_currency_count,
    )
    if workbook and workbook.get("validation_state") == "rejected":
        health_state = "rejected"
    elif unsupported_currency_count:
        health_state = "currency_attention"
    elif not observed_flows and not all_accounts:
        health_state = (
            "planning_only"
            if assumptions or all_budgets or scenarios or goals or workbook
            else "not_configured"
        )
    elif stale:
        health_state = "stale"
    else:
        health_state = "ready"

    return {
        "contract_version": "deterministic-finance-cockpit-v1",
        "base_currency": base_currency,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "summary": {
            "cash_minor": cash_balance,
            "net_worth_minor": net_worth,
            "mtd_income_minor": mtd_income,
            "mtd_expenses_minor": mtd_expenses,
            "mtd_net_minor": mtd_income - mtd_expenses,
            "projected_month_income_minor": projected_month_income,
            "projected_month_expenses_minor": projected_month_expenses,
            "base_monthly_income_minor": base_income,
            "base_monthly_expenses_minor": base_expenses,
            "prior_month_income_minor": prior_income,
            "prior_month_expenses_minor": prior_expenses,
        },
        "focus": focus,
        "budgets": budget_rows,
        "scenarios": forecasts,
        "goals": goals,
        "accounts": accounts,
        "recent_flows": [
            row for row in observed_flows if row.get("currency") == base_currency
        ][:20],
        "workbook": workbook,
        "data_health": {
            "state": health_state,
            "latest_source_at": latest_source.isoformat().replace("+00:00", "Z")
            if latest_source
            else None,
            "transaction_count": len(observed_flows),
            "account_count": len(all_accounts),
            "uncategorized_count": sum(1 for row in flows if not row.get("category")),
            "pending_count": sum(1 for row in observed_flows if row.get("pending")),
            "unhealthy_account_count": sum(
                1 for row in all_accounts if not row.get("link_healthy")
            ),
            "unsupported_currency_count": unsupported_currency_count,
        },
    }


def _visible_rows(
    conn: sqlcipher3.Connection,
    session: Session,
    sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    return filter_rows(session, [dict(row) for row in conn.execute(sql, params)])


def _parse_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{text}T00:00:00+00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _income_expense(rows: list[dict[str, Any]]) -> tuple[int, int]:
    income = 0
    expenses = 0
    for row in rows:
        amount = abs(int(row.get("amount_minor") or 0))
        if row.get("kind") == "received":
            income += amount
        else:
            expenses += amount
    return income, expenses


def _assumption_minor(row: dict[str, Any]) -> int:
    try:
        value = Decimal(str(row.get("value_decimal") or "0"))
    except InvalidOperation:
        return 0
    unit = row.get("unit")
    if unit == "USD/year":
        value = value / Decimal(12)
    if unit in {"USD", "USD/month", "USD/year"}:
        value *= 100
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _forecast_scenario(
    scenario: dict[str, Any],
    *,
    opening_cash: int,
    monthly_income: int,
    monthly_expenses: int,
    minimum_cash: int,
) -> dict[str, Any]:
    opening_cash_with_delta = opening_cash + int(
        scenario.get("opening_cash_delta_minor") or 0
    )
    cash = opening_cash_with_delta
    income = monthly_income + int(scenario.get("monthly_income_delta_minor") or 0)
    expenses = monthly_expenses + int(scenario.get("monthly_expense_delta_minor") or 0)
    horizon = int(scenario.get("horizon_months") or 60)
    one_time_month = scenario.get("one_time_month")
    one_time_amount = int(scenario.get("one_time_amount_minor") or 0)
    points: list[dict[str, int]] = []
    floor_month: int | None = None
    zero_month: int | None = None
    for month in range(1, horizon + 1):
        cash += income - expenses
        if one_time_month is not None and month == int(one_time_month):
            cash += one_time_amount
        if floor_month is None and cash < minimum_cash:
            floor_month = month
        if zero_month is None and cash < 0:
            zero_month = month
        points.append({"month": month, "cash_minor": cash})
    return {
        "scenario_id": scenario.get("scenario_id"),
        "name": scenario.get("name"),
        "is_baseline": bool(scenario.get("is_baseline")),
        "horizon_months": horizon,
        "opening_cash_minor": opening_cash_with_delta,
        "monthly_income_minor": income,
        "monthly_expenses_minor": expenses,
        "monthly_net_minor": income - expenses,
        "ending_cash_minor": cash,
        "below_floor_month": floor_month,
        "cash_out_month": zero_month,
        "points": points,
        "notes": scenario.get("notes"),
    }


def _finance_focus(
    *,
    workbook: dict[str, Any] | None,
    flows: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    forecasts: list[dict[str, Any]],
    budgets: list[dict[str, Any]],
    stale: bool,
    unsupported_currency_count: int,
) -> dict[str, Any]:
    if workbook and workbook.get("validation_state") == "rejected":
        return {
            "state": "review_model",
            "title": "Review the workbook checks",
            "why": "The latest workbook save failed deterministic validation.",
            "action": "Open Checks before relying on the forecast.",
        }
    if unsupported_currency_count:
        return {
            "state": "currency_attention",
            "title": "Review non-USD financial rows",
            "why": (
                f"{unsupported_currency_count} source or planning row"
                f"{'s are' if unsupported_currency_count != 1 else ' is'} excluded "
                "because no reviewed FX-rate fact exists."
            ),
            "action": "Keep the USD plan separate or add an explicit FX policy later.",
        }
    if not flows and not accounts:
        return {
            "state": "connect_or_import",
            "title": "Add a financial source",
            "why": "No transaction or account observations are available yet.",
            "action": "Import the workbook now; connect Plaid later in Settings.",
        }
    unhealthy = next((item for item in accounts if not item.get("link_healthy")), None)
    if unhealthy is not None:
        return {
            "state": "source_attention",
            "title": f"Review {unhealthy['display_name']}",
            "why": "The latest provider account observation reports an unhealthy link.",
            "action": "Open Finance data health, then repair the connection in Settings.",
        }
    if stale:
        return {
            "state": "refresh",
            "title": "Refresh financial sources",
            "why": "The newest account or transaction observation is over 48 hours old.",
            "action": "Check the Plaid connection or publish the latest workbook.",
        }
    baseline = next((item for item in forecasts if item["is_baseline"]), forecasts[0])
    if baseline["cash_out_month"] is not None:
        return {
            "state": "cash_gap",
            "title": f"Cash crosses zero in month {baseline['cash_out_month']}",
            "why": "The baseline cash-flow math reaches a negative balance.",
            "action": "Compare a lower-expense or higher-income workbook scenario.",
        }
    if baseline["below_floor_month"] is not None:
        return {
            "state": "reserve_watch",
            "title": f"Cash falls below reserve in month {baseline['below_floor_month']}",
            "why": "The baseline crosses the household-authored minimum cash floor.",
            "action": "Compare an explicit timing, spending, or income scenario.",
        }
    over = next((item for item in budgets if item["state"] == "watch"), None)
    if over is not None:
        return {
            "state": "budget_watch",
            "title": f"Review {over['category']}",
            "why": "Month-to-date spending is above its workbook budget.",
            "action": "Reclassify transactions or change the plan in the workbook.",
        }
    uncategorized = sum(1 for item in flows if not item.get("category"))
    if uncategorized:
        return {
            "state": "categorize",
            "title": f"Classify {uncategorized} transaction{'s' if uncategorized != 1 else ''}",
            "why": "Uncategorized activity weakens the spending comparison.",
            "action": "Add reviewed categories in Raw Data; Plaid facts stay protected.",
        }
    return {
        "state": "steady",
        "title": "The current plan is internally consistent",
        "why": "No deterministic cash gap, workbook failure, or budget overage ranks higher.",
        "action": "Use Scenarios for the next planned decision.",
    }
