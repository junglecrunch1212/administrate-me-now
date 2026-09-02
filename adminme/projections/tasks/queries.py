"""
Tasks projection read queries.

Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §6, §13.
Every public query takes a ``session: Session`` per [§6.1] and runs
through ``scope.filter_rows`` before return. Child sessions also drop
rows whose ``tags`` overlap ``CHILD_FORBIDDEN_TAGS``
[CONSOLE_PATTERNS.md §6/§7] — handled inside ``filter_rows``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coordination_member_id(session: Session) -> str:
    context = session.service_context
    if context is not None:
        if "read_context" not in context.permissions:
            raise ValueError("service context cannot read member coordination")
        if context.context_scope == "household_shared":
            return session.auth_member_id
        if context.context_member_id is None:
            raise ValueError("service context cannot read member coordination")
        return context.context_member_id
    return session.auth_member_id


def get_task(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    task_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM tasks WHERE tenant_id = ? AND task_id = ?",
        (session.tenant_id, task_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def today_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    today_iso: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ?
           AND (assignee_party = ? OR assignee_party IS NULL)
           AND status NOT IN ('done', 'cancelled', 'dismissed', 'deferred')
           AND ((due_date IS NOT NULL AND due_date <= ?)
                OR status = 'in_progress')
         ORDER BY due_date ASC, created_at ASC
        """,
        (session.tenant_id, member_party_id, today_iso),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def open_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ?
           AND (assignee_party = ? OR assignee_party IS NULL)
           AND status IN ('inbox', 'next', 'in_progress', 'waiting_on')
         ORDER BY due_date ASC, created_at ASC
        """,
        (session.tenant_id, member_party_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def actionable_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    as_of_iso: str,
) -> list[dict[str, Any]]:
    """Return active work plus deferred work whose activation time arrived.

    The stored lifecycle state remains ``deferred``.  ``effective_status`` is a
    deterministic read-time signal for Today/What Now/Scoreboard, so elapsed
    snoozes reappear without inventing or mutating an event.
    """

    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ?
           AND (assignee_party = ? OR assignee_party IS NULL)
           AND (
                status IN ('inbox', 'next', 'in_progress', 'waiting_on')
                OR (status = 'deferred' AND COALESCE(available_from, due_date) IS NOT NULL)
           )
         ORDER BY due_date ASC, created_at ASC
        """,
        (session.tenant_id, member_party_id),
    ).fetchall()
    visible = filter_rows(session, [dict(r) for r in rows])
    try:
        as_of = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("as_of_iso must be an ISO date and time") from exc
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    actionable: list[dict[str, Any]] = []
    for row in visible:
        if row.get("status") == "deferred":
            raw = str(row.get("available_from") or row.get("due_date") or "")
            try:
                activation = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if activation.tzinfo is None:
                activation = activation.replace(tzinfo=timezone.utc)
            if activation.astimezone(timezone.utc) > as_of.astimezone(timezone.utc):
                continue
            row["effective_status"] = "next"
        else:
            row["effective_status"] = row.get("status")
        actionable.append(row)
    return actionable


def work_history_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_party_id: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return active, deferred, and recent completed work for Reminders.

    This remains a Tasks projection query.  It does not join or synthesize
    provider list items.
    """

    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ?
           AND (assignee_party = ? OR assignee_party IS NULL)
           AND status IN ('inbox', 'next', 'in_progress', 'waiting_on', 'deferred', 'done', 'cancelled', 'dismissed')
         ORDER BY CASE WHEN status IN ('done', 'cancelled', 'dismissed') THEN 1 ELSE 0 END,
                  due_date ASC,
                  COALESCE(completed_at, lifecycle_at, created_at) DESC
         LIMIT ?
        """,
        (session.tenant_id, member_party_id, limit),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def by_context(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    domain: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ? AND domain = ?
         ORDER BY due_date ASC, created_at DESC
        """,
        (session.tenant_id, domain),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def in_status(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    status: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ? AND status = ?
         ORDER BY due_date ASC, created_at DESC
        """,
        (session.tenant_id, status),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


# What-now uses this query for goal/dependency-aware ranking.
def sub_tasks_of(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    goal_ref_task_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id = ? AND goal_ref = ?
         ORDER BY created_at ASC
        """,
        (session.tenant_id, goal_ref_task_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def service_work_for_context(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    service_party_id: str,
) -> list[dict[str, Any]]:
    """Return CoS work only for its exact member context plus service-private work."""

    context = session.service_context
    if context is None:
        return []
    rows = conn.execute(
        """
        SELECT * FROM tasks
         WHERE tenant_id=? AND service_party_id=? AND assignee_party=?
           AND status NOT IN ('done','cancelled','dismissed')
         ORDER BY follow_up_at ASC, due_date ASC, created_at ASC
        """,
        (session.tenant_id, service_party_id, service_party_id),
    ).fetchall()
    visible = filter_rows(session, [dict(row) for row in rows])
    member_id = context.context_member_id
    return [
        row
        for row in visible
        if row.get("beneficiary_party_id") in {None, member_id}
        and row.get("on_behalf_of_member_id") in {None, member_id}
    ]


def get_coordination_record(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    coordination_id: str,
    as_of_iso: str,
) -> dict[str, Any] | None:
    """Shape one record to the active member's exact field audiences."""

    member_id = _coordination_member_id(session)
    participant = conn.execute(
        "SELECT 1 FROM coordination_participants "
        "WHERE tenant_id=? AND coordination_id=? AND member_id=?",
        (session.tenant_id, coordination_id, member_id),
    ).fetchone()
    if participant is None:
        return None
    raw = conn.execute(
        "SELECT * FROM coordination_records WHERE tenant_id=? AND coordination_id=?",
        (session.tenant_id, coordination_id),
    ).fetchone()
    if raw is None:
        return None
    record = dict(raw)
    as_of = _as_utc(as_of_iso)
    if record.get("revoked_at"):
        record["effective_status"] = "revoked"
    elif record.get("expires_at") and _as_utc(str(record["expires_at"])) <= as_of:
        record["effective_status"] = "expired"
    elif (
        record.get("status") == "handoff_proposed"
        and record.get("handoff_expires_at")
        and _as_utc(str(record["handoff_expires_at"])) <= as_of
    ):
        record["effective_status"] = "handoff_expired"
    else:
        record["effective_status"] = record["status"]
    fields: dict[str, str] = {}
    for row in conn.execute(
        "SELECT field_name, field_value, audience_member_ids_json "
        "FROM coordination_fields WHERE tenant_id=? AND coordination_id=? "
        "ORDER BY field_name",
        (session.tenant_id, coordination_id),
    ).fetchall():
        try:
            audience = set(json.loads(str(row["audience_member_ids_json"])))
        except (TypeError, json.JSONDecodeError):
            audience = set()
        if member_id in audience:
            fields[str(row["field_name"])] = str(row["field_value"])
    record["fields"] = fields
    conversation = conn.execute(
        "SELECT conversation_reference FROM coordination_conversations "
        "WHERE tenant_id=? AND coordination_id=? AND member_id=?",
        (session.tenant_id, coordination_id, member_id),
    ).fetchone()
    record["conversation_reference"] = (
        str(conversation["conversation_reference"]) if conversation else None
    )
    receipt = conn.execute(
        "SELECT receipt_status, recorded_at FROM coordination_originator_receipts "
        "WHERE tenant_id=? AND coordination_id=? AND originator_member_id=?",
        (session.tenant_id, coordination_id, member_id),
    ).fetchone()
    record["originator_receipt"] = (
        {
            "status": str(receipt["receipt_status"]),
            "recorded_at": receipt["recorded_at"],
        }
        if receipt
        else None
    )
    for private_column in (
        "deadline_at",
        "start_at",
        "end_at",
        "resource_id",
        "owner_scope",
        "visibility_scope",
        "sensitivity",
    ):
        record.pop(private_column, None)
    return record


def list_coordination_records(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    as_of_iso: str,
) -> list[dict[str, Any]]:
    member_id = _coordination_member_id(session)
    rows = conn.execute(
        """
        SELECT r.coordination_id
          FROM coordination_records r
          JOIN coordination_participants p
            ON p.tenant_id=r.tenant_id AND p.coordination_id=r.coordination_id
         WHERE r.tenant_id=? AND p.member_id=?
         ORDER BY r.created_at DESC, r.coordination_id
        """,
        (session.tenant_id, member_id),
    ).fetchall()
    return [
        result
        for row in rows
        if (
            result := get_coordination_record(
                conn,
                session,
                coordination_id=str(row["coordination_id"]),
                as_of_iso=as_of_iso,
            )
        )
        is not None
    ]


def coordination_conflicts(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    coordination_id: str,
    as_of_iso: str,
) -> list[dict[str, Any]]:
    """Return content-free interval conflicts for a visible coordination record."""

    current = get_coordination_record(
        conn, session, coordination_id=coordination_id, as_of_iso=as_of_iso
    )
    if current is None or current["effective_status"] in {"revoked", "expired"}:
        return []
    row = conn.execute(
        "SELECT start_at, end_at, resource_id FROM coordination_records "
        "WHERE tenant_id=? AND coordination_id=?",
        (session.tenant_id, coordination_id),
    ).fetchone()
    if row is None or not row["start_at"] or not row["end_at"]:
        return []
    member_id = _coordination_member_id(session)
    candidates = conn.execute(
        """
        SELECT DISTINCT r.coordination_id, r.start_at, r.end_at, r.resource_id,
                        r.expires_at, r.revoked_at
          FROM coordination_records r
          JOIN coordination_participants p
            ON p.tenant_id=r.tenant_id AND p.coordination_id=r.coordination_id
         WHERE r.tenant_id=? AND r.coordination_id<>?
           AND r.start_at IS NOT NULL AND r.end_at IS NOT NULL
           AND datetime(r.start_at) < datetime(?)
           AND datetime(r.end_at) > datetime(?)
           AND (p.member_id=? OR (r.resource_id IS NOT NULL AND r.resource_id=?))
        """,
        (
            session.tenant_id,
            coordination_id,
            row["end_at"],
            row["start_at"],
            member_id,
            row["resource_id"],
        ),
    ).fetchall()
    as_of = _as_utc(as_of_iso)
    conflicts: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["revoked_at"]:
            continue
        if candidate["expires_at"] and _as_utc(str(candidate["expires_at"])) <= as_of:
            continue
        conflicts.append(
            {
                "coordination_id": str(candidate["coordination_id"]),
                "start_at": str(candidate["start_at"]),
                "end_at": str(candidate["end_at"]),
                "availability": "busy",
                "resource_conflict": bool(
                    row["resource_id"]
                    and row["resource_id"] == candidate["resource_id"]
                ),
            }
        )
    conflicts.sort(key=lambda item: (item["start_at"], item["coordination_id"]))
    return conflicts


def _decode_execution_row(row: sqlcipher3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for source, target, default in (
        ("audience_ids_json", "audience_ids", []),
        ("desired_payload_json", "desired_payload", {}),
        ("desired_state_json", "desired_state", {}),
        ("fact_event_ids_json", "fact_event_ids", []),
        ("result_json", "result", {}),
        ("observed_state_json", "observed_state", {}),
    ):
        if source not in item:
            continue
        try:
            item[target] = json.loads(str(item.pop(source)))
        except (TypeError, json.JSONDecodeError):
            item[target] = default
    for key in (
        "provider_called",
        "suppressed",
        "retryable",
        "direct_provider_edit_detected",
    ):
        if key in item:
            item[key] = bool(item[key])
    return item


def get_delegated_action(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    action_id: str,
) -> dict[str, Any] | None:
    """Return one execution object with its separate evidence objects."""

    action = _decode_execution_row(
        conn.execute(
            "SELECT * FROM cos_delegated_actions WHERE tenant_id=? AND action_id=?",
            (session.tenant_id, action_id),
        ).fetchone()
    )
    visible = filter_one(session, action)
    if visible is None:
        return None
    visible["approvals"] = [
        _decode_execution_row(row)
        for row in conn.execute(
            "SELECT * FROM cos_delegated_action_approvals "
            "WHERE tenant_id=? AND action_id=? ORDER BY approved_at, approval_event_id",
            (session.tenant_id, action_id),
        ).fetchall()
    ]
    visible["commands"] = [
        _decode_execution_row(row)
        for row in conn.execute(
            "SELECT * FROM cos_delegated_action_commands "
            "WHERE tenant_id=? AND action_id=? ORDER BY requested_at, command_event_id",
            (session.tenant_id, action_id),
        ).fetchall()
    ]
    visible["results"] = [
        _decode_execution_row(row)
        for row in conn.execute(
            "SELECT * FROM cos_delegated_action_results "
            "WHERE tenant_id=? AND action_id=? ORDER BY terminal_at, result_event_id",
            (session.tenant_id, action_id),
        ).fetchall()
    ]
    visible["observations"] = [
        _decode_execution_row(row)
        for row in conn.execute(
            "SELECT * FROM cos_delegated_action_observations "
            "WHERE tenant_id=? AND action_id=? ORDER BY observed_at, observation_id",
            (session.tenant_id, action_id),
        ).fetchall()
    ]
    visible["reconciliations"] = [
        _decode_execution_row(row)
        for row in conn.execute(
            "SELECT * FROM cos_delegated_action_reconciliations "
            "WHERE tenant_id=? AND action_id=? ORDER BY reconciled_at, reconciliation_id",
            (session.tenant_id, action_id),
        ).fetchall()
    ]
    visible["state_history"] = [
        dict(row)
        for row in conn.execute(
            "SELECT state, source_event_id, changed_at "
            "FROM cos_delegated_action_state_history "
            "WHERE tenant_id=? AND action_id=? ORDER BY changed_at, source_event_id, state",
            (session.tenant_id, action_id),
        ).fetchall()
    ]
    return visible


def list_delegated_actions(
    conn: sqlcipher3.Connection,
    session: Session,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT action_id FROM cos_delegated_actions WHERE tenant_id=? "
        "ORDER BY prepared_at DESC, action_id",
        (session.tenant_id,),
    ).fetchall()
    return [
        item
        for row in rows
        if (
            item := get_delegated_action(conn, session, action_id=str(row["action_id"]))
        )
        is not None
    ]


def list_execution_envelopes(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    include_revoked: bool = True,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM cos_execution_envelopes WHERE tenant_id=? "
        "ORDER BY granted_at DESC, envelope_id",
        (session.tenant_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for raw in rows:
        item = _decode_execution_row(raw)
        visible = filter_one(session, item)
        if visible is not None and (include_revoked or not visible.get("revoked_at")):
            result.append(visible)
    return result


def matching_execution_envelopes(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    action: dict[str, Any],
    as_of_iso: str,
) -> list[dict[str, Any]]:
    """Return only exact, current standing envelopes for an action."""

    as_of = _as_utc(as_of_iso)
    matches: list[dict[str, Any]] = []
    for envelope in list_execution_envelopes(conn, session, include_revoked=False):
        try:
            expires_at = _as_utc(str(envelope["expires_at"]))
        except (KeyError, ValueError):
            continue
        if expires_at <= as_of:
            continue
        exact = {
            "authority_grant_id": action.get("authority_grant_id"),
            "action_class": action.get("action_class"),
            "canonical_object_kind": action.get("canonical_object_kind"),
            "canonical_object_id": action.get("canonical_object_id"),
            "occurrence_id": action.get("occurrence_id"),
            "series_id": action.get("series_id"),
            "recurrence_strategy": action.get("recurrence_strategy"),
            "desired_representation_id": action.get("desired_representation_id"),
            "provider_family": action.get("provider_family"),
            "provider_account_id": action.get("provider_account_id"),
            "provider_account_owner_principal_id": action.get(
                "provider_account_owner_principal_id"
            ),
            "destination_id": action.get("destination_id"),
            "representation_kind": action.get("representation_kind"),
            "target_external_id": action.get("target_external_id"),
            "expected_provider_revision": action.get("expected_provider_revision"),
            "desired_payload_digest": action.get("desired_payload_digest"),
        }
        if all(envelope.get(key) == value for key, value in exact.items()) and set(
            envelope.get("audience_ids") or []
        ) == set(action.get("audience_ids") or []):
            matches.append(envelope)
    return matches
