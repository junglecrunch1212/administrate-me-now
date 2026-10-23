"""
Tasks projection handlers — household work state transitions.

Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §13.
Handlers are idempotent: re-applying the same event produces the same
row state (§2 invariant 4). Keyed by ``(tenant_id, task_id)``.

Subscribed event types:
- ``task.created``    → INSERT row from payload
- ``task.completed``  → status='done', completed_at/completed_by
- typed lifecycle events preserve defer/snooze/block/cancel/reopen intent
- ``task.updated``    → apply field_updates (and new_status if given)
- ``task.deleted``    → soft-delete: status='dismissed' (row stays for
  rebuild correctness)

Per [§5.3]: ``due_date`` does not create calendar events. Per [§4.6]:
task completion does not auto-complete commitments.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)

_UPDATABLE_COLUMNS = frozenset(
    {
        "title",
        "assignee_party",
        "domain",
        "energy",
        "effort",
        "item_type",
        "due_date",
        "available_from",
        "micro_script",
        "linked_item_id",
        "linked_item_kind",
        "recurring_id",
        "depends_on_json",
        "goal_ref",
        "life_event",
        "auto_research",
        "waiting_on",
        "waiting_since",
        "notes",
    }
)


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    dispatch = {
        "task.created": apply_task_created,
        "openclaw.tool_call.recorded": apply_source_read_receipt,
        "task.completed": apply_task_completed,
        "task.deferred": apply_task_deferred,
        "task.snoozed": apply_task_snoozed,
        "task.blocked": apply_task_blocked,
        "task.unblocked": apply_task_unblocked,
        "task.cancelled": apply_task_cancelled,
        "task.reopened": apply_task_reopened,
        "task.updated": apply_task_updated,
        "task.deleted": apply_task_deleted,
        "list_item.completed": apply_external_completion,
        "list_item.uncompleted": apply_external_completion,
        "calendar.event_outcome_recorded": apply_calendar_completion,
        "cos.work.state_changed": apply_cos_work_state_changed,
        "coordination.created": apply_coordination_created,
        "coordination.originator_receipt_recorded": apply_coordination_receipt,
        "coordination.handoff_proposed": apply_coordination_handoff_proposed,
        "coordination.handoff_decided": apply_coordination_handoff_decided,
        "coordination.handoff_withdrawn": apply_coordination_handoff_withdrawn,
        "coordination.handoff_reassigned": apply_coordination_handoff_reassigned,
        "coordination.waiting_set": apply_coordination_waiting_set,
        "coordination.waiting_cleared": apply_coordination_waiting_cleared,
        "coordination.conversation_linked": apply_coordination_conversation_linked,
        "coordination.revoked": apply_coordination_revoked,
        "cos.execution_envelope.granted": apply_execution_envelope_granted,
        "cos.execution_envelope.revoked": apply_execution_envelope_revoked,
        "cos.delegated_action.prepared": apply_delegated_action_prepared,
        "cos.delegated_action.domain_approved": apply_delegated_action_domain_approved,
        "cos.delegated_action.dispatch_authorized": apply_delegated_action_dispatch_authorized,
        "external_sync.write_requested": apply_delegated_action_command,
        "external_sync.write_completed": apply_delegated_action_result,
        "external_sync.write_failed": apply_delegated_action_result,
        "cos.delegated_action.observed": apply_delegated_action_observed,
        "cos.delegated_action.canonical_outcome_returned": apply_delegated_action_canonical_outcome_returned,
        "cos.delegated_action.reconciled": apply_delegated_action_reconciled,
        "cos.delegated_action.withdrawn": apply_delegated_action_withdrawn,
        "cos.google_tasks_execution.prepared": apply_google_tasks_execution_prepared,
        "cos.google_tasks_execution.openclaw_approved": apply_google_tasks_execution_openclaw_approved,
        "cos.google_tasks_execution.contact_attempt_committed": apply_google_tasks_execution_contact_committed,
        "cos.google_tasks_execution.provider_result_received": apply_google_tasks_execution_provider_result_received,
        "cos.google_tasks_execution.terminal": apply_google_tasks_execution_terminal,
        "cos.google_tasks_execution.observed": apply_google_tasks_execution_observed,
        "cos.google_tasks_execution.reconciled": apply_google_tasks_execution_reconciled,
        "cos.google_tasks_execution.revoked": apply_google_tasks_execution_revoked,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        return
    handler(envelope, conn)


def _b03_state(
    conn: sqlcipher3.Connection,
    envelope: dict[str, Any],
    state: str,
    changed_at: str,
) -> None:
    command_id = str(envelope["payload"]["command_id"])
    conn.execute(
        "UPDATE cos_google_tasks_execution_v2 SET "
        "state=CASE WHEN revoked_at IS NOT NULL AND ? != 'revoked' THEN state ELSE ? END, "
        "last_event_id=? "
        "WHERE tenant_id=? AND command_id=? AND last_event_id <= ?",
        (
            state,
            state,
            envelope["event_id"],
            envelope["tenant_id"],
            command_id,
            envelope["event_id"],
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO cos_google_tasks_execution_history_v2 "
        "(tenant_id, command_id, state, source_event_id, changed_at) VALUES (?, ?, ?, ?, ?)",
        (envelope["tenant_id"], command_id, state, envelope["event_id"], changed_at),
    )


def apply_google_tasks_execution_prepared(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """INSERT OR IGNORE INTO cos_google_tasks_execution_v2 (
               tenant_id, command_id, state, execution_policy_version,
               action_class, envelope_id, envelope_revision, represented_cos,
               actual_operator, context_scope, on_behalf_of, authority_grant_id,
               operating_agreement_id, resource_owner, provider_owner, provider,
               provider_account, adapter_identity, destination_task_list,
               content_fingerprint, approval_fingerprint, desired_task_content_json,
               domain_approval_event_id, initiating_event_id, valid_from,
               expires_at, idempotency_key, maximum_effect_count,
               provider_effect_count, last_event_id, owner_scope,
               visibility_scope, sensitivity
           ) VALUES (?, ?, 'held_for_approval', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                     ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
        (
            envelope["tenant_id"],
            p["command_id"],
            p["execution_policy_version"],
            p["action_class"],
            p["execution_envelope_id"],
            p["execution_envelope_revision"],
            p["represented_cos"],
            p["actual_operator"],
            p["active_context"],
            p["on_behalf_of"],
            p["authority_basis"],
            p["operating_agreement_id"],
            p["resource_owner"],
            p["provider_owner"],
            p["provider"],
            p["provider_account_coordinate"],
            p["adapter_identity"],
            p["destination_task_list_coordinate"],
            p["approved_task_content_fingerprint"],
            p["approval_fingerprint"],
            json.dumps(
                p["desired_task_content"], sort_keys=True, separators=(",", ":")
            ),
            p["domain_approval_event_id"],
            envelope["event_id"],
            p["valid_from"],
            p["expires_at"],
            p["idempotency_key"],
            p["maximum_effect_count"],
            envelope["event_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
        ),
    )
    _b03_state(conn, envelope, "held_for_approval", str(p["created_at"]))


def apply_google_tasks_execution_openclaw_approved(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        "UPDATE cos_google_tasks_execution_v2 SET openclaw_approval_event_id=?, "
        "approval_fingerprint=?, last_event_id=? WHERE tenant_id=? AND command_id=? "
        "AND envelope_id=? AND envelope_revision=? AND domain_approval_event_id=?",
        (
            p["openclaw_approval_event_id"],
            p["approval_fingerprint"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["command_id"],
            p["execution_envelope_id"],
            p["execution_envelope_revision"],
            p["domain_approval_event_id"],
        ),
    )
    if conn.execute("SELECT changes()").fetchone()[0]:
        _b03_state(conn, envelope, "approved", str(p["approved_at"]))


def apply_google_tasks_execution_contact_committed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        "UPDATE cos_google_tasks_execution_v2 SET contact_attempt_event_id=?, "
        "provider_contact_state='contact_attempt_committed', used_at=?, last_event_id=? "
        "WHERE tenant_id=? AND command_id=? AND contact_attempt_event_id IS NULL",
        (
            envelope["event_id"],
            p["committed_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["command_id"],
        ),
    )
    if conn.execute("SELECT changes()").fetchone()[0]:
        _b03_state(conn, envelope, "contact_attempt_committed", str(p["committed_at"]))


def apply_google_tasks_execution_provider_result_received(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        "UPDATE cos_google_tasks_execution_v2 SET provider_result_event_id=?, "
        "native_task_id=?, provider_revision=?, provider_contact_state='result_received', "
        "provider_effect_count=1, last_event_id=? WHERE tenant_id=? AND command_id=? "
        "AND contact_attempt_event_id=? AND provider_result_event_id IS NULL",
        (
            envelope["event_id"],
            p["native_task_id"],
            p.get("provider_revision"),
            envelope["event_id"],
            envelope["tenant_id"],
            p["command_id"],
            p["contact_attempt_event_id"],
        ),
    )
    if conn.execute("SELECT changes()").fetchone()[0]:
        _b03_state(
            conn,
            envelope,
            "provider_result_received",
            str(p["result_received_at"]),
        )


def apply_google_tasks_execution_terminal(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    state = {
        "success": "terminal_success",
        "failure_before_contact": "terminal_failure_before_contact",
        "ambiguous_after_possible_contact": "ambiguous_result_after_possible_contact",
        "suppressed": "terminal_failure_before_contact",
    }[p["outcome"]]
    conn.execute(
        "UPDATE cos_google_tasks_execution_v2 SET terminal_result_event_id=?, "
        "terminal_result_state=?, native_task_id=?, provider_revision=?, "
        "provider_effect_count=?, last_event_id=? WHERE tenant_id=? AND command_id=? "
        "AND terminal_result_event_id IS NULL",
        (
            envelope["event_id"],
            p["outcome"],
            p.get("native_task_id"),
            p.get("provider_revision"),
            p["effect_count"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["command_id"],
        ),
    )
    if conn.execute("SELECT changes()").fetchone()[0]:
        _b03_state(conn, envelope, state, str(p["terminal_at"]))


def apply_google_tasks_execution_observed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        "UPDATE cos_google_tasks_execution_v2 SET observation_event_id=?, "
        "observation_state='later_observation_received', provider_revision=?, "
        "last_event_id=? WHERE tenant_id=? AND command_id=?",
        (
            envelope["event_id"],
            p.get("provider_revision"),
            envelope["event_id"],
            envelope["tenant_id"],
            p["command_id"],
        ),
    )
    _b03_state(conn, envelope, "later_observation_received", str(p["observed_at"]))


def apply_google_tasks_execution_reconciled(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    state = "reconciled" if p["outcome"] == "matched" else p["outcome"]
    conn.execute(
        "UPDATE cos_google_tasks_execution_v2 SET reconciliation_event_id=?, "
        "reconciliation_state=?, last_event_id=? WHERE tenant_id=? AND command_id=?",
        (
            envelope["event_id"],
            p["outcome"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["command_id"],
        ),
    )
    _b03_state(conn, envelope, state, str(p["reconciled_at"]))


def apply_google_tasks_execution_revoked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        "UPDATE cos_google_tasks_execution_v2 SET revoked_at=?, last_event_id=? "
        "WHERE tenant_id=? AND command_id=? AND envelope_id=? AND envelope_revision=?",
        (
            p["revoked_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["command_id"],
            p["execution_envelope_id"],
            p["execution_envelope_revision"],
        ),
    )
    _b03_state(conn, envelope, "revoked", str(p["revoked_at"]))


def _delegated_state(
    conn: sqlcipher3.Connection,
    envelope: dict[str, Any],
    *,
    action_id: str,
    state: str,
    changed_at: str | None = None,
) -> None:
    conn.execute(
        """UPDATE cos_delegated_actions SET state=?, last_event_id=?
             WHERE tenant_id=? AND action_id=? AND last_event_id <= ?""",
        (
            state,
            envelope["event_id"],
            envelope["tenant_id"],
            action_id,
            envelope["event_id"],
        ),
    )
    conn.execute(
        """INSERT OR IGNORE INTO cos_delegated_action_state_history (
               tenant_id, action_id, state, source_event_id, changed_at
           ) VALUES (?, ?, ?, ?, ?)""",
        (
            envelope["tenant_id"],
            action_id,
            state,
            envelope["event_id"],
            changed_at or envelope["occurred_at"],
        ),
    )


def apply_execution_envelope_granted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """INSERT OR IGNORE INTO cos_execution_envelopes (
               tenant_id, envelope_id, authority_grant_id,
               granted_by_member_id, represented_service_principal_id,
               action_class, canonical_object_kind, canonical_object_id,
               occurrence_id, series_id, recurrence_strategy,
               desired_representation_id, provider_family,
               provider_account_id, provider_account_owner_principal_id,
               destination_id, audience_ids_json, representation_kind,
               target_external_id, expected_provider_revision,
               desired_payload_digest, provider_execution_scope,
               openclaw_permission_included, provider_effect_count,
               granted_at, expires_at, owner_scope,
               visibility_scope, sensitivity, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            envelope["tenant_id"],
            p["envelope_id"],
            p["authority_grant_id"],
            p["granted_by_member_id"],
            p["represented_service_principal_id"],
            p["action_class"],
            p["canonical_object_kind"],
            p["canonical_object_id"],
            p.get("occurrence_id"),
            p.get("series_id"),
            p["recurrence_strategy"],
            p["desired_representation_id"],
            p["provider_family"],
            p["provider_account_id"],
            p["provider_account_owner_principal_id"],
            p["destination_id"],
            json.dumps(p["audience_ids"], separators=(",", ":"), sort_keys=True),
            p["representation_kind"],
            p.get("target_external_id"),
            p.get("expected_provider_revision"),
            p["desired_payload_digest"],
            p["provider_execution_scope"],
            int(bool(p["openclaw_permission_included"])),
            p["provider_effect_count"],
            p["granted_at"],
            p["expires_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_execution_envelope_revoked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """UPDATE cos_execution_envelopes
              SET revoked_at=?, revoked_by_member_id=?,
                  revocation_reason_code=?, last_event_id=?
            WHERE tenant_id=? AND envelope_id=? AND last_event_id < ?""",
        (
            p["revoked_at"],
            p["revoked_by_member_id"],
            p["reason_code"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["envelope_id"],
            envelope["event_id"],
        ),
    )


def apply_delegated_action_prepared(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """INSERT OR IGNORE INTO cos_delegated_actions (
               tenant_id, action_id, action_revision, state,
               canonical_object_kind, canonical_object_id, occurrence_id,
               series_id, recurrence_strategy, coordination_id,
               desired_representation_id, actor_principal_id, actor_party_id,
               operator_principal_id, operator_kind,
               represented_service_principal_id, active_context_scope,
               active_context_member_id, on_behalf_of_member_id,
               authority_grant_id, authority_grant_event_id,
               resource_owner_principal_id,
               provider_account_owner_principal_id, provider, provider_family,
               provider_account_id, production_adapter, adapter_architecture,
               destination_id, audience_ids_json, action_class, operation,
               representation_kind, expected_provider_revision,
               target_external_id, desired_payload_json, desired_state_json,
               desired_payload_digest, action_fingerprint, command_id,
               command_idempotency_key, fact_event_ids_json, facts_observed_at,
               facts_valid_until, policy_version, policy_digest,
               privacy_guard_version, content_guard_digest, risk_class,
               execution_mode, prepared_at, expires_at, owner_scope,
               visibility_scope, sensitivity, last_event_id
           ) VALUES (
               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
           )""",
        (
            envelope["tenant_id"],
            p["action_id"],
            p["action_revision"],
            p["initial_state"],
            p["canonical_object_kind"],
            p["canonical_object_id"],
            p.get("occurrence_id"),
            p.get("series_id"),
            p["recurrence_strategy"],
            p.get("coordination_id"),
            p["desired_representation_id"],
            p["actor_principal_id"],
            p["actor_party_id"],
            p["operator_principal_id"],
            p["operator_kind"],
            p["represented_service_principal_id"],
            p["active_context_scope"],
            p.get("active_context_member_id"),
            p.get("on_behalf_of_member_id"),
            p["authority_grant_id"],
            p["authority_grant_event_id"],
            p["resource_owner_principal_id"],
            p["provider_account_owner_principal_id"],
            p["provider"],
            p["provider_family"],
            p["provider_account_id"],
            p.get("production_adapter"),
            p["adapter_architecture"],
            p["destination_id"],
            json.dumps(p["audience_ids"], separators=(",", ":"), sort_keys=True),
            p["action_class"],
            p["operation"],
            p["representation_kind"],
            p.get("expected_provider_revision"),
            p.get("target_external_id"),
            json.dumps(p["desired_payload"], separators=(",", ":"), sort_keys=True),
            json.dumps(p["desired_state"], separators=(",", ":"), sort_keys=True),
            p["desired_payload_digest"],
            p["action_fingerprint"],
            p["command_id"],
            p["command_idempotency_key"],
            json.dumps(p["fact_event_ids"], separators=(",", ":"), sort_keys=True),
            p["facts_observed_at"],
            p["facts_valid_until"],
            p["policy_version"],
            p["policy_digest"],
            p["privacy_guard_version"],
            p["content_guard_digest"],
            p["risk_class"],
            p["execution_mode"],
            p["prepared_at"],
            p["expires_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    _delegated_state(
        conn,
        envelope,
        action_id=p["action_id"],
        state=p["initial_state"],
        changed_at=p["prepared_at"],
    )


def apply_delegated_action_domain_approved(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """INSERT OR IGNORE INTO cos_delegated_action_approvals (
               tenant_id, action_id, approval_event_id, approval_kind,
               approved_by_member_id, execution_envelope_id,
               action_fingerprint, approved_at, expires_at, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            envelope["tenant_id"],
            p["action_id"],
            envelope["event_id"],
            p["approval_kind"],
            p["approved_by_member_id"],
            p.get("execution_envelope_id"),
            p["action_fingerprint"],
            p["approved_at"],
            p["expires_at"],
            envelope["event_id"],
        ),
    )
    conn.execute(
        """UPDATE cos_delegated_actions
              SET domain_approval_event_id=?, execution_envelope_id=?
            WHERE tenant_id=? AND action_id=? AND action_fingerprint=?""",
        (
            envelope["event_id"],
            p.get("execution_envelope_id"),
            envelope["tenant_id"],
            p["action_id"],
            p["action_fingerprint"],
        ),
    )
    _delegated_state(
        conn,
        envelope,
        action_id=p["action_id"],
        state="awaiting_openclaw_approval",
        changed_at=p["approved_at"],
    )


def apply_delegated_action_dispatch_authorized(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """UPDATE cos_delegated_actions
              SET openclaw_approval_event_id=?, dispatch_authorization_event_id=?,
                  operator_principal_id=?, last_event_id=?
            WHERE tenant_id=? AND action_id=? AND action_fingerprint=?""",
        (
            p["openclaw_approval_event_id"],
            envelope["event_id"],
            p["operator_principal_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["action_id"],
            p["action_fingerprint"],
        ),
    )
    _delegated_state(
        conn,
        envelope,
        action_id=p["action_id"],
        state="dispatched",
        changed_at=p["authorized_at"],
    )


def apply_delegated_action_command(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    action_id = p.get("origin_proposal_id")
    command_payload = p.get("payload") or {}
    if not isinstance(action_id, str) or not command_payload.get("delegated_action"):
        return
    destination_id = command_payload.get("destination_id")
    if not isinstance(destination_id, str):
        return
    conn.execute(
        """INSERT OR IGNORE INTO cos_delegated_action_commands (
               tenant_id, action_id, command_id, command_event_id,
               domain_approval_event_id, openclaw_approval_event_id,
               request_fingerprint, provider, provider_account_id,
               destination_id, operation, requested_at, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            envelope["tenant_id"],
            action_id,
            p["command_id"],
            envelope["event_id"],
            p["domain_approval_event_id"],
            p["openclaw_approval_event_id"],
            p["request_fingerprint"],
            p["provider"],
            p["provider_account_id"],
            destination_id,
            p["operation"],
            p["requested_at"],
            envelope["event_id"],
        ),
    )
    conn.execute(
        """UPDATE cos_delegated_actions SET command_event_id=?
             WHERE tenant_id=? AND action_id=?""",
        (envelope["event_id"], envelope["tenant_id"], action_id),
    )
    _delegated_state(
        conn,
        envelope,
        action_id=action_id,
        state="awaiting_result",
        changed_at=p["requested_at"],
    )


def apply_delegated_action_result(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    command = conn.execute(
        "SELECT action_id FROM cos_delegated_action_commands "
        "WHERE tenant_id=? AND command_id=?",
        (envelope["tenant_id"], p["command_id"]),
    ).fetchone()
    if command is None:
        return
    action_id = str(command["action_id"])
    completed = envelope["type"] == "external_sync.write_completed"
    suppressed = bool(p.get("suppressed")) if completed else False
    state = (
        "suppressed"
        if suppressed
        else "awaiting_observation"
        if completed
        else "conflicted"
        if p.get("error_kind") == "conflict"
        else "failed"
    )
    terminal_at = p.get("completed_at") or p.get("failed_at") or envelope["occurred_at"]
    conn.execute(
        """INSERT OR IGNORE INTO cos_delegated_action_results (
               tenant_id, action_id, command_id, result_event_id, result_type,
               provider_called, suppressed, external_id, provider_revision,
               result_json, error_kind, retryable, terminal_at, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            envelope["tenant_id"],
            action_id,
            p["command_id"],
            envelope["event_id"],
            envelope["type"],
            int(bool(p.get("provider_called"))),
            int(suppressed),
            p.get("external_id"),
            p.get("provider_revision"),
            json.dumps(p.get("result") or {}, separators=(",", ":"), sort_keys=True),
            p.get("error_kind"),
            int(bool(p.get("retryable"))),
            terminal_at,
            envelope["event_id"],
        ),
    )
    conn.execute(
        """UPDATE cos_delegated_actions SET terminal_result_event_id=?
             WHERE tenant_id=? AND action_id=?""",
        (envelope["event_id"], envelope["tenant_id"], action_id),
    )
    _delegated_state(
        conn,
        envelope,
        action_id=action_id,
        state=state,
        changed_at=str(terminal_at),
    )


def apply_delegated_action_observed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """INSERT OR IGNORE INTO cos_delegated_action_observations (
               tenant_id, observation_id, action_id, command_id,
               result_event_id, provider, provider_family, provider_account_id,
               destination_id, external_id, provider_revision,
               observed_state_json, observation_fingerprint,
               direct_provider_edit_detected, observed_at, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            envelope["tenant_id"],
            p["observation_id"],
            p["action_id"],
            p["command_id"],
            p["result_event_id"],
            p["provider"],
            p["provider_family"],
            p["provider_account_id"],
            p["destination_id"],
            p["external_id"],
            p["provider_revision"],
            json.dumps(p["observed_state"], separators=(",", ":"), sort_keys=True),
            p["observation_fingerprint"],
            int(bool(p["direct_provider_edit_detected"])),
            p["observed_at"],
            envelope["event_id"],
        ),
    )
    conn.execute(
        """UPDATE cos_delegated_actions SET observation_event_id=?
             WHERE tenant_id=? AND action_id=?""",
        (envelope["event_id"], envelope["tenant_id"], p["action_id"]),
    )
    _delegated_state(
        conn,
        envelope,
        action_id=p["action_id"],
        state="conflicted" if p["direct_provider_edit_detected"] else "active",
        changed_at=p["observed_at"],
    )


def apply_delegated_action_reconciled(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """INSERT OR IGNORE INTO cos_delegated_action_reconciliations (
               tenant_id, reconciliation_id, action_id, observation_event_id,
               result_event_id, desired_state_digest, observed_state_digest,
               outcome, canonical_outcome, canonical_outcome_event_id,
               reason_code, reconciled_at, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            envelope["tenant_id"],
            p["reconciliation_id"],
            p["action_id"],
            p["observation_event_id"],
            p["result_event_id"],
            p["desired_state_digest"],
            p["observed_state_digest"],
            p["outcome"],
            p["canonical_outcome"],
            p.get("canonical_outcome_event_id"),
            p["reason_code"],
            p["reconciled_at"],
            envelope["event_id"],
        ),
    )
    conn.execute(
        """UPDATE cos_delegated_actions SET reconciliation_event_id=?
             WHERE tenant_id=? AND action_id=?""",
        (envelope["event_id"], envelope["tenant_id"], p["action_id"]),
    )
    _delegated_state(
        conn,
        envelope,
        action_id=p["action_id"],
        state=p["outcome"],
        changed_at=p["reconciled_at"],
    )


def apply_delegated_action_canonical_outcome_returned(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    """Reflect an observed fixture completion/reopening in canonical Task truth."""

    p = envelope["payload"]
    completed = p["outcome"] == "completed"
    conn.execute(
        """UPDATE tasks
              SET status=?, completed_at=?, completed_by=?,
                  completed_by_party_id=?, lifecycle_action=?, lifecycle_at=?,
                  lifecycle_reason=?, lifecycle_actor_principal_id=?,
                  lifecycle_actor_party_id=?, lifecycle_authority_grant_id=?,
                  last_event_id=?
            WHERE tenant_id=? AND task_id=? AND last_event_id < ?""",
        (
            "done" if completed else "next",
            p["returned_at"] if completed else None,
            p["on_behalf_of_member_id"] if completed else None,
            p["represented_service_party_id"] if completed else None,
            "complete_from_delegated_provider"
            if completed
            else "reopen_from_delegated_provider",
            p["returned_at"],
            f"delegated provider outcome:{p['action_id']}",
            p["operator_principal_id"],
            p["represented_service_party_id"],
            p["authority_grant_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["canonical_object_id"],
            envelope["event_id"],
        ),
    )


def apply_delegated_action_withdrawn(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    _delegated_state(
        conn,
        envelope,
        action_id=p["action_id"],
        state="withdrawn",
        changed_at=p["withdrawn_at"],
    )


def apply_calendar_completion(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    policy = p.get("completion_policy", "manual")
    qualifies = (
        policy == "on_elapsed" and p.get("outcome") in {"elapsed", "attended"}
    ) or (policy == "on_attended" and p.get("outcome") == "attended")
    if (
        p.get("linked_item_kind") != "task"
        or not p.get("linked_item_id")
        or not qualifies
    ):
        return
    conn.execute(
        """UPDATE tasks SET status='done', completed_at=?, completed_by=?,
                lifecycle_action='complete_from_calendar', lifecycle_at=?,
                lifecycle_reason='linked calendar outcome', last_event_id=?
             WHERE tenant_id=? AND task_id=? AND last_event_id < ?""",
        (
            p["recorded_at"],
            p["member_id"],
            p["recorded_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["linked_item_id"],
            envelope["event_id"],
        ),
    )


def apply_external_completion(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    """Mirror only reminders explicitly linked to a task by AdministrateMe."""

    p = envelope["payload"]
    if (
        p.get("linked_item_kind") != "task"
        or not p.get("linked_item_id")
        or p.get("completion_policy") != "mirror"
    ):
        return
    completed = envelope["type"] == "list_item.completed"
    conn.execute(
        """UPDATE tasks SET status=?, completed_at=?, completed_by=?,
                lifecycle_action=?, lifecycle_at=?, lifecycle_reason=?, last_event_id=?
             WHERE tenant_id=? AND task_id=? AND last_event_id < ?""",
        (
            "done" if completed else "next",
            str(p.get("completion_at") or p.get("last_modified_at"))
            if completed
            else None,
            p.get("member_id") if completed else None,
            "complete_from_provider" if completed else "reopen_from_provider",
            str(p.get("completion_at") or p.get("last_modified_at")),
            "linked provider item outcome",
            envelope["event_id"],
            envelope["tenant_id"],
            p["linked_item_id"],
            envelope["event_id"],
        ),
    )


def apply_task_created(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    # TaskCreatedV1's payload schema is minimal; fall back to sensible
    # defaults for the richer projection columns so row inserts succeed
    # without requiring every caller to fill every field.
    values: dict[str, Any] = {
        "task_id": p["task_id"],
        "tenant_id": envelope["tenant_id"],
        "title": p["title"],
        "status": p.get("status", "inbox"),
        "assignee_party": p.get("assignee_party_id")
        or p.get("assignee_party")
        or p.get("owner_member_id"),
        "work_kind": p.get("work_kind")
        or ("member_obligation" if p.get("owner_member_id") else "household_work"),
        "owner_member_id": p.get("owner_member_id"),
        "owner_party_id": p.get("owner_party_id") or p.get("owner_member_id"),
        "responsible_party_id": p.get("responsible_party_id"),
        "beneficiary_party_id": p.get("beneficiary_party_id"),
        "service_assignment_role": p.get("service_assignment_role"),
        "service_party_id": p.get("service_party_id"),
        "authority_grant_id": p.get("authority_grant_id"),
        "record_kind": p.get("record_kind", "work"),
        "source_authorities_json": json.dumps(
            p.get("source_authorities", []), sort_keys=True, separators=(",", ":")
        ),
        "on_behalf_of_member_id": p.get("on_behalf_of_member_id"),
        "delegated_by_member_id": p.get("delegated_by_member_id"),
        "service_work_type": p.get("service_work_type"),
        "coordination_id": p.get("coordination_id"),
        "source_event_id": p.get("source_event_id"),
        "source_invocation_id": p.get("source_invocation_id"),
        "scheduled_start_at": p.get("scheduled_start_at"),
        "scheduled_end_at": p.get("scheduled_end_at"),
        "follow_up_at": p.get("follow_up_at"),
        "waiting_on_party_id": p.get("waiting_on_party_id"),
        "waiting_on_member_id": p.get("waiting_on_member_id"),
        "provider_representation_owner_kind": p.get(
            "provider_representation_owner_kind", "none"
        ),
        "provider_representation_owner_principal_id": p.get(
            "provider_representation_owner_principal_id"
        ),
        "provider_id": p.get("provider_id"),
        "provider_account_reference": p.get("provider_account_reference"),
        "provider_workspace_reference": p.get("provider_workspace_reference"),
        "provider_external_reference_digest": p.get(
            "provider_external_reference_digest"
        ),
        "domain": p.get("domain") or "tasks",
        "energy": p.get("energy"),
        "effort": p.get("effort"),
        "item_type": p.get("item_type") or "task",
        "due_date": p.get("due_date") or p.get("due"),
        "available_from": p.get("available_from"),
        "micro_script": p.get("micro_script"),
        "linked_item_id": p.get("linked_item_id"),
        "linked_item_kind": p.get("linked_item_kind"),
        "recurring_id": p.get("recurring_id"),
        "depends_on_json": p.get("depends_on_json", "[]"),
        "goal_ref": p.get("goal_ref"),
        "life_event": p.get("life_event"),
        "auto_research": 1 if p.get("auto_research") else 0,
        "waiting_on": p.get("waiting_on"),
        "waiting_since": p.get("waiting_since"),
        "created_at": p.get("created_at") or envelope["occurred_at"],
        "created_by": p.get("created_by"),
        "completed_at": None,
        "completed_by": None,
        "completed_by_party_id": None,
        "lifecycle_actor_principal_id": None,
        "lifecycle_actor_party_id": None,
        "lifecycle_authority_grant_id": None,
        "source_system": p.get("source_system"),
        "notes": p.get("notes") or p.get("description"),
        "owner_scope": envelope["owner_scope"],
        "visibility_scope": envelope["visibility_scope"],
        "sensitivity": envelope["sensitivity"],
        "last_event_id": envelope["event_id"],
    }
    columns = tuple(values)
    updates = tuple(
        column
        for column in columns
        if column not in {"task_id", "tenant_id", "completed_at", "completed_by"}
    )
    conn.execute(
        f"INSERT INTO tasks ({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)}) "
        "ON CONFLICT(tenant_id, task_id) DO UPDATE SET "
        + ", ".join(f"{column}=excluded.{column}" for column in updates),
        tuple(values[column] for column in columns),
    )


def apply_cos_work_state_changed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    completed = p["action"] == "complete"
    waiting_on = p.get("waiting_on_member_id") or p.get("waiting_on_party_id")
    cur = conn.execute(
        """
        UPDATE tasks
           SET status=?, lifecycle_action=?, lifecycle_at=?, lifecycle_reason=?,
               waiting_on=?, waiting_since=?, waiting_on_party_id=?,
               waiting_on_member_id=?, follow_up_at=?, available_from=?,
               completed_at=?, completed_by=NULL, completed_by_party_id=?,
               lifecycle_actor_principal_id=?, lifecycle_actor_party_id=?,
               lifecycle_authority_grant_id=?, last_event_id=?
         WHERE tenant_id=? AND task_id=? AND last_event_id=?
           AND service_party_id=? AND assignee_party=?
        """,
        (
            p["resulting_status"],
            p["action"],
            p["changed_at"],
            p["reason_code"],
            waiting_on,
            p["changed_at"] if waiting_on else None,
            p.get("waiting_on_party_id"),
            p.get("waiting_on_member_id"),
            p.get("follow_up_at"),
            p.get("follow_up_at") if p["resulting_status"] == "deferred" else None,
            p["changed_at"] if completed else None,
            p["actor_service_party_id"] if completed else None,
            p["actor_service_principal_id"],
            p["actor_service_party_id"],
            p["authority_grant_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["task_id"],
            p["expected_last_event_id"],
            p["actor_service_party_id"],
            p["actor_service_party_id"],
        ),
    )
    if cur.rowcount != 1:
        raise ValueError("CoS work transition target or revision is invalid")


def _coordination_exists(
    conn: sqlcipher3.Connection, tenant_id: str, coordination_id: str
) -> Any:
    return conn.execute(
        "SELECT * FROM coordination_records WHERE tenant_id=? AND coordination_id=?",
        (tenant_id, coordination_id),
    ).fetchone()


def _coordination_participants(
    conn: sqlcipher3.Connection, tenant_id: str, coordination_id: str
) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT member_id FROM coordination_participants "
            "WHERE tenant_id=? AND coordination_id=?",
            (tenant_id, coordination_id),
        ).fetchall()
    }


def apply_coordination_created(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    fields = {str(item["name"]): str(item["value"]) for item in p.get("fields", [])}
    conn.execute(
        """
        INSERT INTO coordination_records (
            tenant_id, coordination_id, coordination_kind, status, last_action,
            originator_member_id, obligation_owner_kind, obligation_owner_id,
            responsible_party_id, beneficiary_party_id, linked_task_id,
            linked_commitment_id, cos_assignment_task_id,
            handoff_proposed_by_member_id, proposed_destination_member_id,
            handoff_expires_at, accepted_destination_member_id,
            waiting_on_member_id, waiting_since,
            follow_up_at, deadline_at, start_at, end_at, resource_id, expires_at,
            revoked_at, created_at, owner_scope, visibility_scope, sensitivity,
            last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,NULL,?,?,?,?,?)
        ON CONFLICT(tenant_id, coordination_id) DO UPDATE SET
            coordination_kind=excluded.coordination_kind,
            originator_member_id=excluded.originator_member_id,
            obligation_owner_kind=excluded.obligation_owner_kind,
            obligation_owner_id=excluded.obligation_owner_id,
            responsible_party_id=excluded.responsible_party_id,
            beneficiary_party_id=excluded.beneficiary_party_id,
            linked_task_id=excluded.linked_task_id,
            linked_commitment_id=excluded.linked_commitment_id,
            cos_assignment_task_id=excluded.cos_assignment_task_id,
            deadline_at=excluded.deadline_at, start_at=excluded.start_at,
            end_at=excluded.end_at, resource_id=excluded.resource_id,
            expires_at=excluded.expires_at, created_at=excluded.created_at,
            owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["coordination_id"],
            p["coordination_kind"],
            "open",
            "created",
            p["originator_member_id"],
            p["obligation_owner_kind"],
            p.get("obligation_owner_id"),
            p["responsible_party_id"],
            p.get("beneficiary_party_id"),
            p.get("linked_task_id"),
            p.get("linked_commitment_id"),
            p.get("cos_assignment_task_id"),
            fields.get("deadline_at"),
            fields.get("start_at"),
            fields.get("end_at"),
            fields.get("resource_id"),
            p.get("expires_at"),
            p["created_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    conn.execute(
        "DELETE FROM coordination_participants WHERE tenant_id=? AND coordination_id=?",
        (envelope["tenant_id"], p["coordination_id"]),
    )
    conn.executemany(
        "INSERT INTO coordination_participants "
        "(tenant_id, coordination_id, member_id, last_event_id) VALUES (?,?,?,?)",
        [
            (
                envelope["tenant_id"],
                p["coordination_id"],
                member_id,
                envelope["event_id"],
            )
            for member_id in p["participant_member_ids"]
        ],
    )
    conn.execute(
        "DELETE FROM coordination_fields WHERE tenant_id=? AND coordination_id=?",
        (envelope["tenant_id"], p["coordination_id"]),
    )
    conn.executemany(
        "INSERT INTO coordination_fields "
        "(tenant_id, coordination_id, field_name, field_value, "
        "audience_member_ids_json, last_event_id) VALUES (?,?,?,?,?,?)",
        [
            (
                envelope["tenant_id"],
                p["coordination_id"],
                item["name"],
                item["value"],
                json.dumps(item["audience_member_ids"], separators=(",", ":")),
                envelope["event_id"],
            )
            for item in p.get("fields", [])
        ],
    )


def apply_coordination_receipt(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    if _coordination_exists(conn, envelope["tenant_id"], p["coordination_id"]) is None:
        raise ValueError("coordination receipt has no record")
    conn.execute(
        """
        INSERT INTO coordination_originator_receipts (
            tenant_id, coordination_id, originator_member_id, receipt_status,
            recorded_at, owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, coordination_id, originator_member_id) DO UPDATE SET
            receipt_status=excluded.receipt_status, recorded_at=excluded.recorded_at,
            owner_scope=excluded.owner_scope, visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["coordination_id"],
            p["originator_member_id"],
            p["receipt_status"],
            p["recorded_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_coordination_handoff_proposed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    participants = _coordination_participants(
        conn, envelope["tenant_id"], p["coordination_id"]
    )
    if not {p["proposed_by_member_id"], p["destination_member_id"]}.issubset(
        participants
    ):
        raise ValueError("handoff principals must be coordination participants")
    conn.execute(
        """UPDATE coordination_records
              SET status='handoff_proposed', last_action='handoff_proposed',
                  handoff_proposed_by_member_id=?,
                  proposed_destination_member_id=?, handoff_expires_at=?,
                  last_event_id=?
            WHERE tenant_id=? AND coordination_id=? AND revoked_at IS NULL""",
        (
            p["proposed_by_member_id"],
            p["destination_member_id"],
            p.get("expires_at"),
            envelope["event_id"],
            envelope["tenant_id"],
            p["coordination_id"],
        ),
    )


def apply_coordination_handoff_decided(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    row = _coordination_exists(conn, envelope["tenant_id"], p["coordination_id"])
    if (
        row is None
        or row["proposed_destination_member_id"] != p["destination_member_id"]
    ):
        raise ValueError("only the proposed destination may decide a handoff")
    accepted = p["decision"] == "accepted"
    conn.execute(
        """UPDATE coordination_records
              SET status=?, last_action=?, responsible_party_id=?,
                  accepted_destination_member_id=?,
                  proposed_destination_member_id=NULL,
                  handoff_proposed_by_member_id=NULL, handoff_expires_at=NULL,
                  last_event_id=?
            WHERE tenant_id=? AND coordination_id=? AND revoked_at IS NULL""",
        (
            "active" if accepted else "handoff_rejected",
            f"handoff_{p['decision']}",
            p["destination_member_id"] if accepted else row["responsible_party_id"],
            p["destination_member_id"]
            if accepted
            else row["accepted_destination_member_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["coordination_id"],
        ),
    )


def apply_coordination_handoff_withdrawn(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    row = _coordination_exists(conn, envelope["tenant_id"], p["coordination_id"])
    if row is None or p["withdrawn_by_member_id"] not in {
        row["originator_member_id"],
        row["handoff_proposed_by_member_id"],
    }:
        raise ValueError("handoff withdrawal is not authorized")
    conn.execute(
        """UPDATE coordination_records SET status=?,
                  last_action='handoff_withdrawn',
                  handoff_proposed_by_member_id=NULL,
                  proposed_destination_member_id=NULL, handoff_expires_at=NULL,
                  last_event_id=?
            WHERE tenant_id=? AND coordination_id=? AND revoked_at IS NULL""",
        (
            "active" if row["accepted_destination_member_id"] else "open",
            envelope["event_id"],
            envelope["tenant_id"],
            p["coordination_id"],
        ),
    )


def apply_coordination_handoff_reassigned(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    participants = _coordination_participants(
        conn, envelope["tenant_id"], p["coordination_id"]
    )
    row = _coordination_exists(conn, envelope["tenant_id"], p["coordination_id"])
    if (
        row is None
        or p["destination_member_id"] not in participants
        or p["reassigned_by_member_id"]
        not in {row["originator_member_id"], row["responsible_party_id"]}
    ):
        raise ValueError("handoff reassignment is not authorized")
    conn.execute(
        """UPDATE coordination_records
              SET status='handoff_proposed', last_action='handoff_reassigned',
                  handoff_proposed_by_member_id=?,
                  proposed_destination_member_id=?, handoff_expires_at=?,
                  last_event_id=?
            WHERE tenant_id=? AND coordination_id=? AND revoked_at IS NULL""",
        (
            p["reassigned_by_member_id"],
            p["destination_member_id"],
            p.get("expires_at"),
            envelope["event_id"],
            envelope["tenant_id"],
            p["coordination_id"],
        ),
    )


def apply_coordination_waiting_set(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    if p["waiting_on_member_id"] not in _coordination_participants(
        conn, envelope["tenant_id"], p["coordination_id"]
    ):
        raise ValueError("waiting member must be a coordination participant")
    conn.execute(
        """UPDATE coordination_records
              SET status='waiting_on_member', last_action='waiting_set',
                  waiting_on_member_id=?, waiting_since=?, follow_up_at=?,
                  last_event_id=?
            WHERE tenant_id=? AND coordination_id=? AND revoked_at IS NULL""",
        (
            p["waiting_on_member_id"],
            p["waiting_since"],
            p.get("follow_up_at"),
            envelope["event_id"],
            envelope["tenant_id"],
            p["coordination_id"],
        ),
    )


def apply_coordination_waiting_cleared(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """UPDATE coordination_records
              SET status='active', last_action='waiting_cleared',
                  waiting_on_member_id=NULL, waiting_since=NULL, follow_up_at=NULL,
                  last_event_id=?
            WHERE tenant_id=? AND coordination_id=? AND revoked_at IS NULL""",
        (envelope["event_id"], envelope["tenant_id"], p["coordination_id"]),
    )


def apply_coordination_conversation_linked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    if p["member_id"] not in _coordination_participants(
        conn, envelope["tenant_id"], p["coordination_id"]
    ):
        raise ValueError("conversation owner must be a coordination participant")
    conn.execute(
        """
        INSERT INTO coordination_conversations (
            tenant_id, coordination_id, member_id, conversation_reference,
            linked_at, owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, coordination_id, member_id) DO UPDATE SET
            conversation_reference=excluded.conversation_reference,
            linked_at=excluded.linked_at, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["coordination_id"],
            p["member_id"],
            p["conversation_reference"],
            p["linked_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_coordination_revoked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """UPDATE coordination_records
              SET status='revoked', last_action='revoked', revoked_at=?,
                  last_event_id=?
            WHERE tenant_id=? AND coordination_id=? AND revoked_at IS NULL""",
        (
            p["revoked_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["coordination_id"],
        ),
    )


def apply_task_completed(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE tasks
           SET status        = 'done',
               completed_at  = ?,
               completed_by  = ?,
               lifecycle_action = 'complete',
               lifecycle_at = ?,
               lifecycle_reason = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND task_id = ?
        """,
        (
            p["completed_at"],
            p["completed_by_member_id"],
            p["completed_at"],
            p.get("note"),
            envelope["event_id"],
            envelope["tenant_id"],
            p["task_id"],
        ),
    )


def _apply_typed_lifecycle(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    *,
    status: str,
    action: str,
    at_field: str,
    reason_field: str | None = None,
    available_field: str | None = None,
    waiting_field: str | None = None,
    clear_completion: bool = False,
    clear_available: bool = False,
    clear_waiting: bool = False,
) -> None:
    p = envelope["payload"]
    reason = p.get(reason_field) if reason_field else None
    assignments = [
        "status = ?",
        "lifecycle_action = ?",
        "lifecycle_at = ?",
        "lifecycle_reason = ?",
        "last_event_id = ?",
    ]
    params: list[Any] = [
        status,
        action,
        p[at_field],
        reason,
        envelope["event_id"],
    ]
    if available_field:
        assignments.append("available_from = ?")
        params.append(p.get(available_field))
    elif clear_available:
        assignments.append("available_from = NULL")
    if waiting_field:
        assignments.extend(["waiting_on = ?", "waiting_since = ?"])
        params.extend([p.get(waiting_field), p[at_field]])
    elif clear_waiting:
        assignments.extend(["waiting_on = NULL", "waiting_since = NULL"])
    if clear_completion:
        assignments.extend(["completed_at = NULL", "completed_by = NULL"])
    params.extend([envelope["tenant_id"], p["task_id"]])
    conn.execute(
        f"UPDATE tasks SET {', '.join(assignments)} "
        "WHERE tenant_id = ? AND task_id = ?",
        params,
    )


def apply_task_deferred(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="deferred",
        action="defer",
        at_field="deferred_at",
        reason_field="reason",
        available_field="deferred_until",
        clear_waiting=True,
    )


def apply_task_snoozed(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="deferred",
        action="snooze",
        at_field="snoozed_at",
        available_field="snoozed_until",
        clear_waiting=True,
    )


def apply_task_blocked(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="waiting_on",
        action="block",
        at_field="blocked_at",
        reason_field="reason",
        waiting_field="reason",
    )


def apply_task_unblocked(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="next",
        action="unblock",
        at_field="unblocked_at",
        reason_field="note",
        clear_waiting=True,
    )


def apply_task_cancelled(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="cancelled",
        action="cancel",
        at_field="cancelled_at",
        reason_field="reason",
        clear_waiting=True,
    )


def apply_task_reopened(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    _apply_typed_lifecycle(
        envelope,
        conn,
        status="next",
        action="reopen",
        at_field="reopened_at",
        clear_completion=True,
        clear_available=True,
        clear_waiting=True,
    )


def apply_task_updated(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    updates: dict[str, Any] = dict(p.get("field_updates") or {})
    # new_status in the envelope is a top-level hint — promote to status.
    new_status = p.get("new_status")
    if new_status:
        updates["status"] = new_status

    columns: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        if key == "status" or key in _UPDATABLE_COLUMNS:
            columns.append(key)
            params.append(value)
    if not columns:
        return
    assignments = ", ".join(f"{c} = ?" for c in columns)
    assignments += (
        ", lifecycle_action = 'edit', lifecycle_at = ?, "
        "lifecycle_reason = NULL, last_event_id = ?"
    )
    params.extend([envelope["occurred_at"], envelope["event_id"]])
    params.extend([envelope["tenant_id"], p["task_id"]])
    conn.execute(
        f"UPDATE tasks SET {assignments} WHERE tenant_id = ? AND task_id = ?",
        params,
    )


def apply_task_deleted(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    # Soft-delete: row stays (rebuild correctness depends on it).
    p = envelope["payload"]
    conn.execute(
        """
        UPDATE tasks
           SET status        = 'dismissed',
               lifecycle_action = 'delete',
               lifecycle_at = ?,
               lifecycle_reason = 'deleted from reviewed workbook',
               last_event_id = ?
         WHERE tenant_id = ? AND task_id = ?
        """,
        (
            p["deleted_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["task_id"],
        ),
    )


def apply_source_read_receipt(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    """Index only the exact service-private typed audit, preserving mode floors."""
    p = envelope["payload"]
    principal = "service-principal:chief-of-staff"
    scope = f"service-private:{principal}"
    if (
        envelope["schema_version"] != 3
        or envelope["owner_scope"] != scope
        or envelope["visibility_scope"] != scope
        or envelope["source_adapter"] != "openclaw-plugin"
        or envelope["source_account_id"] != principal
        or p.get("actor_principal_id") != principal
        or p.get("represented_service_principal_id") != principal
        or p.get("context_scope") != "service_private"
        or p.get("authority_grant_id") is not None
        or p.get("operation")
        not in {"tools.service-context", "tools.service-private-state"}
    ):
        return
    for source in p["source_read_authorities"]:
        rank = {"metadata_only": 0, "typed_facts": 1, "typed_facts_safe_snippet": 2}[
            source.get("read_mode", "typed_facts")
        ]
        conn.execute(
            "INSERT INTO cos_source_read_receipts VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, service_principal_id, session_digest, source_event_id) "
            "DO UPDATE SET authority_json=json_set(cos_source_read_receipts.authority_json, "
            "'$.read_mode', json_extract(excluded.authority_json, '$.read_mode')), "
            "read_rank=excluded.read_rank, "
            "last_event_id=excluded.last_event_id "
            "WHERE cos_source_read_receipts.read_rank < excluded.read_rank",
            (
                envelope["tenant_id"],
                principal,
                p["source_read_session_digest"],
                source["source_event_id"],
                json.dumps(source, sort_keys=True),
                rank,
                envelope["event_id"],
            ),
        )
