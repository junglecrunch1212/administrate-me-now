"""
Tasks projection — household work (AdministrateMe-specific, not Hearth).

Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §13.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.tasks import handlers


class TasksProjection(Projection):
    name = "tasks"
    version = 10
    replay_retained_events_on_version_change = True
    subscribes_to = [
        "task.created",
        "openclaw.tool_call.recorded",
        "task.completed",
        "task.deferred",
        "task.snoozed",
        "task.blocked",
        "task.unblocked",
        "task.cancelled",
        "task.reopened",
        "task.updated",
        "task.deleted",
        "list_item.completed",
        "list_item.uncompleted",
        "calendar.event_outcome_recorded",
        "cos.work.state_changed",
        "coordination.created",
        "coordination.originator_receipt_recorded",
        "coordination.handoff_proposed",
        "coordination.handoff_decided",
        "coordination.handoff_withdrawn",
        "coordination.handoff_reassigned",
        "coordination.waiting_set",
        "coordination.waiting_cleared",
        "coordination.conversation_linked",
        "coordination.revoked",
        "cos.execution_envelope.granted",
        "cos.execution_envelope.revoked",
        "cos.delegated_action.prepared",
        "cos.delegated_action.domain_approved",
        "cos.delegated_action.dispatch_authorized",
        "external_sync.write_requested",
        "external_sync.write_completed",
        "external_sync.write_failed",
        "cos.delegated_action.observed",
        "cos.delegated_action.canonical_outcome_returned",
        "cos.delegated_action.reconciled",
        "cos.delegated_action.withdrawn",
        "cos.google_tasks_execution.prepared",
        "cos.google_tasks_execution.openclaw_approved",
        "cos.google_tasks_execution.contact_attempt_committed",
        "cos.google_tasks_execution.provider_result_received",
        "cos.google_tasks_execution.terminal",
        "cos.google_tasks_execution.observed",
        "cos.google_tasks_execution.reconciled",
        "cos.google_tasks_execution.revoked",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "available_from" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN available_from TEXT")
        if "lifecycle_action" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN lifecycle_action TEXT")
        if "lifecycle_at" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN lifecycle_at TEXT")
        if "lifecycle_reason" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN lifecycle_reason TEXT")
        if "work_kind" not in columns:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN work_kind "
                "TEXT NOT NULL DEFAULT 'household_work'"
            )
        if "owner_member_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN owner_member_id TEXT")
        if "owner_party_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN owner_party_id TEXT")
        if "responsible_party_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN responsible_party_id TEXT")
        if "beneficiary_party_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN beneficiary_party_id TEXT")
        additions = {
            "record_kind": "TEXT NOT NULL DEFAULT 'work'",
            "source_authorities_json": "TEXT NOT NULL DEFAULT '[]'",
            "service_assignment_role": "TEXT",
            "service_party_id": "TEXT",
            "authority_grant_id": "TEXT",
            "on_behalf_of_member_id": "TEXT",
            "delegated_by_member_id": "TEXT",
            "service_work_type": "TEXT",
            "coordination_id": "TEXT",
            "source_event_id": "TEXT",
            "source_invocation_id": "TEXT",
            "scheduled_start_at": "TEXT",
            "scheduled_end_at": "TEXT",
            "follow_up_at": "TEXT",
            "waiting_on_party_id": "TEXT",
            "waiting_on_member_id": "TEXT",
            "provider_representation_owner_kind": "TEXT NOT NULL DEFAULT 'none'",
            "provider_representation_owner_principal_id": "TEXT",
            "provider_id": "TEXT",
            "provider_account_reference": "TEXT",
            "provider_workspace_reference": "TEXT",
            "provider_external_reference_digest": "TEXT",
            "completed_by_party_id": "TEXT",
            "lifecycle_actor_principal_id": "TEXT",
            "lifecycle_actor_party_id": "TEXT",
            "lifecycle_authority_grant_id": "TEXT",
        }
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")
        coordination_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(coordination_records)")
        }
        if coordination_columns and "handoff_expires_at" not in coordination_columns:
            conn.execute(
                "ALTER TABLE coordination_records ADD COLUMN handoff_expires_at TEXT"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_tenant_available "
            "ON tasks(tenant_id, available_from)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_tenant_owner_party "
            "ON tasks(tenant_id, owner_party_id, work_kind)"
        )
        service_work_columns = (
            "tenant_id, service_party_id, beneficiary_party_id, status"
            if "status" in columns
            else "tenant_id, service_party_id, beneficiary_party_id"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_tenant_service_work "
            f"ON tasks({service_work_columns})"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_tenant_coordination "
            "ON tasks(tenant_id, coordination_id)"
        )


__all__ = ["TasksProjection"]
