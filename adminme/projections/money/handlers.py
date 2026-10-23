"""
money projection handlers — transaction ledger state transitions.

Per ADMINISTRATEME_BUILD.md §3.9 and SYSTEM_INVARIANTS.md §2. Handlers are
idempotent: re-applying the same event produces the same row state (§2
invariant 4). Keyed by ``(tenant_id, flow_id)``.

Subscribed event types:
- ``money_flow.recorded``         → INSERT with is_manual=0
- ``money_flow.manually_added``   → INSERT with is_manual=1
- ``money_flow.manually_deleted`` → UPDATE deleted_at (soft delete)

Per [§2.3], handlers do not validate cross-projection references — if
``linked_account`` does not exist in ``accounts``, the row still lands.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlcipher3

from adminme.finance.projection import apply_publication

_log = logging.getLogger(__name__)


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    dispatch = {
        "money_flow.recorded": apply_money_flow_recorded,
        "money_flow.manually_added": apply_money_flow_manually_added,
        "money_flow.manually_deleted": apply_money_flow_manually_deleted,
        "money_flow.provider_observed": apply_money_flow_provider_observed,
        "money_flow.provider_removed": apply_money_flow_provider_removed,
        "financial.account_observed": apply_financial_account_observed,
        "money_flow.classification_set": apply_money_flow_classification_set,
        "money_flow.manually_updated": apply_money_flow_manually_updated,
        "finance.assumption_set": apply_finance_assumption_set,
        "finance.budget_set": apply_finance_budget_set,
        "finance.scenario_set": apply_finance_scenario_set,
        "finance.goal_set": apply_finance_goal_set,
        "finance.goal_paused": apply_finance_goal_paused,
        "finance.goal_completed": apply_finance_goal_completed,
        "finance.goal_reopened": apply_finance_goal_reopened,
        "finance.workbook_published": apply_finance_workbook_published,
        "finance.publication_accepted": apply_publication,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        return
    handler(envelope, conn)


def apply_money_flow_recorded(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO money_flows (
            flow_id, tenant_id, from_party, to_party, amount_minor, currency,
            occurred_at, kind, category, linked_artifact, linked_account,
            linked_interaction, notes, source_adapter, is_manual,
            added_by_party_id, deleted_at, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL,
                  ?, ?, ?, ?)
        ON CONFLICT(tenant_id, flow_id) DO UPDATE SET
            from_party         = excluded.from_party,
            to_party           = excluded.to_party,
            amount_minor       = excluded.amount_minor,
            currency           = excluded.currency,
            occurred_at        = excluded.occurred_at,
            kind               = excluded.kind,
            category           = excluded.category,
            linked_artifact    = excluded.linked_artifact,
            linked_account     = excluded.linked_account,
            linked_interaction = excluded.linked_interaction,
            notes              = excluded.notes,
            source_adapter     = excluded.source_adapter,
            is_manual          = 0,
            added_by_party_id  = NULL,
            owner_scope        = excluded.owner_scope,
            visibility_scope   = excluded.visibility_scope,
            sensitivity        = excluded.sensitivity,
            last_event_id      = excluded.last_event_id
        """,
        (
            p["flow_id"],
            envelope["tenant_id"],
            p.get("from_party_id"),
            p.get("to_party_id"),
            p["amount_minor"],
            p["currency"],
            p["occurred_at"],
            p["kind"],
            p.get("category"),
            p.get("linked_artifact"),
            p.get("linked_account"),
            p.get("linked_interaction"),
            p.get("notes"),
            p["source_adapter"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_money_flow_manually_added(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO money_flows (
            flow_id, tenant_id, from_party, to_party, amount_minor, currency,
            occurred_at, kind, category, linked_artifact, linked_account,
            linked_interaction, notes, source_adapter, is_manual,
            added_by_party_id, deleted_at, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, 'manual',
                  1, ?, NULL, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, flow_id) DO UPDATE SET
            from_party         = excluded.from_party,
            to_party           = excluded.to_party,
            amount_minor       = excluded.amount_minor,
            currency           = excluded.currency,
            occurred_at        = excluded.occurred_at,
            kind               = excluded.kind,
            category           = excluded.category,
            notes              = excluded.notes,
            source_adapter     = 'manual',
            is_manual          = 1,
            added_by_party_id  = excluded.added_by_party_id,
            owner_scope        = excluded.owner_scope,
            visibility_scope   = excluded.visibility_scope,
            sensitivity        = excluded.sensitivity,
            last_event_id      = excluded.last_event_id
        """,
        (
            p["flow_id"],
            envelope["tenant_id"],
            p.get("from_party_id"),
            p.get("to_party_id"),
            p["amount_minor"],
            p["currency"],
            p["occurred_at"],
            p["kind"],
            p.get("category"),
            p.get("notes"),
            p["added_by_party_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_money_flow_manually_deleted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    # Soft-delete: row stays, deleted_at populated. Rebuild correctness
    # depends on the row persisting.
    p = envelope["payload"]
    cur = conn.execute(
        """
        UPDATE money_flows
           SET deleted_at    = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND flow_id = ?
        """,
        (
            p["deleted_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["flow_id"],
        ),
    )
    if cur.rowcount == 0:
        # Deletion before addition — event ordering quirk. No-op; log.
        _log.info(
            "money_flow.manually_deleted for unknown flow_id=%s tenant=%s",
            p["flow_id"],
            envelope["tenant_id"],
        )


def apply_money_flow_provider_observed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    """Upsert a protected provider observation without overwriting workbook edits."""

    p = envelope["payload"]
    flow_id = f"plaid:{p['transaction_id']}"
    conn.execute(
        """
        INSERT INTO money_flows (
            flow_id, tenant_id, amount_minor, currency, occurred_at, kind,
            category, linked_account, notes, memo, member_id, merchant_name,
            merchant_category, provider_category, assigned_category,
            provider_transaction_id, provider_item_id, pending, included,
            provider_revision, updated_at, removed_at, source_adapter,
            is_manual, owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?,
                  1, ?, ?, NULL, 'plaid', 0, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, flow_id) DO UPDATE SET
            amount_minor            = excluded.amount_minor,
            currency                = excluded.currency,
            occurred_at             = excluded.occurred_at,
            kind                    = excluded.kind,
            category                = COALESCE(money_flows.assigned_category,
                                               excluded.provider_category),
            linked_account          = excluded.linked_account,
            member_id               = excluded.member_id,
            merchant_name           = excluded.merchant_name,
            merchant_category       = excluded.merchant_category,
            provider_category       = excluded.provider_category,
            provider_item_id        = excluded.provider_item_id,
            pending                 = excluded.pending,
            provider_revision       = excluded.provider_revision,
            updated_at              = excluded.updated_at,
            removed_at              = NULL,
            owner_scope             = excluded.owner_scope,
            visibility_scope        = excluded.visibility_scope,
            sensitivity            = excluded.sensitivity,
            last_event_id           = excluded.last_event_id
        """,
        (
            flow_id,
            envelope["tenant_id"],
            p["amount_minor"],
            p["currency"],
            p["occurred_at"],
            p["kind"],
            p.get("provider_category"),
            p["account_id"],
            p.get("original_description"),
            p["member_id"],
            p.get("merchant_name"),
            p.get("merchant_category"),
            p.get("provider_category"),
            p["transaction_id"],
            p["plaid_item_id"],
            int(bool(p.get("pending"))),
            p["provider_revision"],
            p["observed_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_money_flow_provider_removed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE money_flows
           SET removed_at = ?, last_event_id = ?
         WHERE tenant_id = ? AND provider_transaction_id = ?
        """,
        (
            p["removed_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["transaction_id"],
        ),
    )


def apply_financial_account_observed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO financial_accounts (
            account_id, tenant_id, member_id, provider, provider_item_id,
            institution_id, institution_name, display_name, account_type,
            account_subtype, last4, current_balance_minor,
            available_balance_minor, limit_minor, currency, as_of,
            link_healthy, owner_scope, visibility_scope, sensitivity,
            last_event_id
        ) VALUES (?, ?, ?, 'plaid', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?)
        ON CONFLICT(tenant_id, account_id) DO UPDATE SET
            member_id               = excluded.member_id,
            provider_item_id        = excluded.provider_item_id,
            institution_id          = excluded.institution_id,
            institution_name        = excluded.institution_name,
            display_name            = excluded.display_name,
            account_type            = excluded.account_type,
            account_subtype         = excluded.account_subtype,
            last4                   = excluded.last4,
            current_balance_minor   = excluded.current_balance_minor,
            available_balance_minor = excluded.available_balance_minor,
            limit_minor             = excluded.limit_minor,
            currency                = excluded.currency,
            as_of                   = excluded.as_of,
            link_healthy            = excluded.link_healthy,
            owner_scope             = excluded.owner_scope,
            visibility_scope        = excluded.visibility_scope,
            sensitivity             = excluded.sensitivity,
            last_event_id            = excluded.last_event_id
        """,
        (
            p["account_id"],
            envelope["tenant_id"],
            p["member_id"],
            p["plaid_item_id"],
            p["institution_id"],
            p["institution_name"],
            p["display_name"],
            p["account_type"],
            p.get("account_subtype"),
            p.get("last4"),
            p.get("current_balance_minor"),
            p.get("available_balance_minor"),
            p.get("limit_minor"),
            p["currency"],
            p["as_of"],
            int(bool(p.get("link_healthy", True))),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_money_flow_classification_set(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    updates: dict[str, Any] = {"last_event_id": envelope["event_id"]}
    if "category" in p and p.get("category") is not None:
        updates["assigned_category"] = p["category"]
        updates["category"] = p["category"]
    for column in ("memo", "notes"):
        if column in p and p.get(column) is not None:
            updates[column] = p[column]
    if p.get("included") is not None:
        updates["included"] = int(bool(p["included"]))
    updates["updated_at"] = p["updated_at"]
    _update_flow(conn, envelope["tenant_id"], p["flow_id"], updates)


def apply_money_flow_manually_updated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    allowed = {
        "amount_minor",
        "currency",
        "occurred_at",
        "kind",
        "linked_account",
        "merchant_name",
        "merchant_category",
        "memo",
        "notes",
    }
    updates = {key: p[key] for key in allowed if p.get(key) is not None}
    if p.get("category") is not None:
        updates["category"] = p["category"]
        updates["assigned_category"] = p["category"]
    if p.get("included") is not None:
        updates["included"] = int(bool(p["included"]))
    updates["updated_at"] = p["updated_at"]
    updates["last_event_id"] = envelope["event_id"]
    cur = conn.execute(
        "SELECT is_manual FROM money_flows WHERE tenant_id=? AND flow_id=?",
        (envelope["tenant_id"], p["flow_id"]),
    ).fetchone()
    if cur is None or not bool(cur[0]):
        return
    _update_flow(conn, envelope["tenant_id"], p["flow_id"], updates)


def _update_flow(
    conn: sqlcipher3.Connection,
    tenant_id: str,
    flow_id: str,
    updates: dict[str, Any],
) -> None:
    if not updates:
        return
    columns = list(updates)
    assignments = ", ".join(f"{column} = ?" for column in columns)
    conn.execute(
        f"UPDATE money_flows SET {assignments} WHERE tenant_id = ? AND flow_id = ?",
        [updates[column] for column in columns] + [tenant_id, flow_id],
    )


def apply_finance_assumption_set(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    _upsert_planning_row(
        conn,
        "finance_assumptions",
        "assumption_id",
        envelope,
        ("category", "label", "value_decimal", "unit", "notes", "as_of"),
    )


def apply_finance_budget_set(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    _upsert_planning_row(
        conn,
        "finance_budgets",
        "budget_id",
        envelope,
        ("category", "amount_minor", "currency", "cadence", "active", "notes", "as_of"),
        bool_columns={"active"},
        defaults={"currency": "USD", "cadence": "monthly", "active": True},
    )


def apply_finance_scenario_set(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    if p.get("is_baseline"):
        conn.execute(
            "UPDATE finance_scenarios SET is_baseline=0 WHERE tenant_id=?",
            (envelope["tenant_id"],),
        )
    _upsert_planning_row(
        conn,
        "finance_scenarios",
        "scenario_id",
        envelope,
        (
            "name",
            "is_baseline",
            "horizon_months",
            "opening_cash_delta_minor",
            "monthly_income_delta_minor",
            "monthly_expense_delta_minor",
            "one_time_month",
            "one_time_amount_minor",
            "notes",
            "active",
        ),
        bool_columns={"is_baseline", "active"},
        defaults={
            "is_baseline": False,
            "opening_cash_delta_minor": 0,
            "monthly_income_delta_minor": 0,
            "monthly_expense_delta_minor": 0,
            "one_time_amount_minor": 0,
            "active": True,
        },
    )


def apply_finance_goal_set(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    _upsert_planning_row(
        conn,
        "finance_goals",
        "goal_id",
        envelope,
        (
            "name",
            "target_minor",
            "current_minor",
            "target_date",
            "priority",
            "status",
            "notes",
        ),
        defaults={"current_minor": 0, "priority": 100, "status": "active"},
    )


def _apply_finance_goal_lifecycle(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    *,
    status: str,
    at_field: str,
    actor_field: str,
    reason_field: str | None = None,
) -> None:
    payload = envelope["payload"]
    reason = payload.get(reason_field) if reason_field is not None else None
    conn.execute(
        """UPDATE finance_goals
              SET status=?, lifecycle_at=?, lifecycle_actor=?,
                  lifecycle_reason=?, last_event_id=?
            WHERE tenant_id=? AND goal_id=? AND last_event_id < ?""",
        (
            status,
            payload[at_field],
            payload[actor_field],
            reason,
            envelope["event_id"],
            envelope["tenant_id"],
            payload["goal_id"],
            envelope["event_id"],
        ),
    )


def apply_finance_goal_paused(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    _apply_finance_goal_lifecycle(
        envelope,
        conn,
        status="paused",
        at_field="paused_at",
        actor_field="paused_by_member_id",
        reason_field="reason",
    )


def apply_finance_goal_completed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    _apply_finance_goal_lifecycle(
        envelope,
        conn,
        status="complete",
        at_field="completed_at",
        actor_field="completed_by_member_id",
        reason_field="note",
    )


def apply_finance_goal_reopened(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    _apply_finance_goal_lifecycle(
        envelope,
        conn,
        status="active",
        at_field="reopened_at",
        actor_field="reopened_by_member_id",
    )


def _upsert_planning_row(
    conn: sqlcipher3.Connection,
    table: str,
    id_column: str,
    envelope: dict[str, Any],
    payload_columns: tuple[str, ...],
    *,
    bool_columns: set[str] | None = None,
    defaults: dict[str, Any] | None = None,
) -> None:
    p = envelope["payload"]
    bool_columns = bool_columns or set()
    defaults = defaults or {}
    values = []
    for column in payload_columns:
        value = p[column] if column in p else defaults.get(column)
        values.append(int(bool(value)) if column in bool_columns else value)
    columns = [
        id_column,
        "tenant_id",
        *payload_columns,
        "owner_scope",
        "visibility_scope",
        "sensitivity",
        "last_event_id",
    ]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(
        f"{column}=excluded.{column}"
        for column in (
            *payload_columns,
            "owner_scope",
            "visibility_scope",
            "sensitivity",
            "last_event_id",
        )
    )
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(tenant_id, {id_column}) DO UPDATE SET {updates}",
        [
            p[id_column],
            envelope["tenant_id"],
            *values,
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ],
    )


def apply_finance_workbook_published(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO finance_workbook_state (
            tenant_id, workbook_name, workbook_sha256, workbook_schema_version,
            published_at, published_by_party_id, validation_state,
            validation_failures_json, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, workbook_name) DO UPDATE SET
            workbook_sha256=excluded.workbook_sha256,
            workbook_schema_version=excluded.workbook_schema_version,
            published_at=excluded.published_at,
            published_by_party_id=excluded.published_by_party_id,
            validation_state=excluded.validation_state,
            validation_failures_json=excluded.validation_failures_json,
            last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["workbook_name"],
            p["workbook_sha256"],
            p["workbook_schema_version"],
            p["published_at"],
            p["published_by_party_id"],
            p["validation_state"],
            json.dumps(p.get("validation_failures") or [], sort_keys=True),
            envelope["event_id"],
        ),
    )
