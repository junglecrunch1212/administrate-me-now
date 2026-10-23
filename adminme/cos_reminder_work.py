"""Link requested reminders to service-owned work without scheduling or sending.

Existing member-private intent and provider receipts remain authoritative. This
durable bus consumer records the CoS's internal responsibility and observed
outcome; it never copies message text or turns a work state into send authority.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.projections.parties.queries import (
    ServiceDelegationResolutionError,
    resolve_service_delegation,
)
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
    service_private_scope,
)
from adminme.source_policy import source_coordinate_for_event

REQUESTED = "cos.reply.reminder_requested"
BOUND = "cos.reply.reminder_bound"
CANCELLED = "cos.reply.reminder_cancelled"
RESULT = "cos.response.result"
CONSUMER = "cos-reminders:work"


def reminder_work_id(reminder_id: str) -> str:
    return "cos-reminder-work:" + hashlib.sha256(reminder_id.encode()).hexdigest()[:32]


async def _related(runtime: Any, member: str, kind: str, request_id: str) -> Any:
    return await runtime.event_log.get_by_idempotency(
        runtime.settings.instance.tenant_id,
        f"openclaw:{member}:{kind}:{request_id}",
    )


async def reminder_receipt(runtime: Any, reminder: dict[str, Any]) -> dict[str, Any]:
    """Only callers which already verified the exact member may expose this."""
    member = reminder["payload"]["member_id"]
    reminder_id = reminder["event_id"]
    bound = await _related(runtime, member, BOUND, reminder_id)
    cancelled = await _related(runtime, member, CANCELLED, reminder_id)
    approval = await _related(runtime, member, "cos.response.approved", reminder_id)
    terminal = (
        await _related(runtime, member, RESULT, approval["event_id"])
        if approval
        else None
    )
    status = (
        terminal["payload"]["status"]
        if terminal
        else "delivery_pending_or_unknown"
        if approval
        else "cancelled"
        if cancelled
        else "waiting"
        if bound
        else "awaiting_timer"
    )
    return {
        "reminder_event_id": reminder_id,
        "task_id": reminder_work_id(reminder_id),
        "status": status,
        "due_at": reminder["payload"]["due_at"],
        "job_id": bound["payload"]["job_id"] if bound else None,
        "source_message_event_id": reminder["payload"]["source_message_event_id"],
        "binding_event_id": bound["event_id"] if bound else None,
        "cancellation_event_id": cancelled["event_id"] if cancelled else None,
        "approval_event_id": approval["event_id"] if approval else None,
        "terminal_event_id": terminal["event_id"] if terminal else None,
        "automatic_retry_allowed": False,
    }


class ReminderWorkReconciler:
    CONSUMER_VERSION = "cos-reminder-work-v1"

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def handle(self, event: dict[str, Any]) -> None:
        runtime = self.runtime
        if event["tenant_id"] != runtime.settings.instance.tenant_id:
            return
        reminder: dict[str, Any] | None
        if event["type"] == REQUESTED:
            reminder = event
        else:
            payload = event["payload"]
            if event["type"] == RESULT:
                approved = await runtime.event_log.get(payload["approval_event_id"])
                if not approved or approved["type"] != "cos.response.approved":
                    return
                if any(
                    approved[key] != event[key]
                    for key in ("tenant_id", "owner_scope", "visibility_scope")
                ) or approved["payload"].get("member_id") != payload.get("member_id"):
                    return
                payload = approved["payload"]
            reminder_id = payload.get("reminder_event_id")
            reminder = await runtime.event_log.get(reminder_id) if reminder_id else None
        if reminder is None or reminder["type"] != REQUESTED:
            return
        member = reminder["payload"]["member_id"]
        scope = f"private:{member}"
        if (
            reminder["tenant_id"] != runtime.settings.instance.tenant_id
            or reminder["owner_scope"] != scope
            or reminder["visibility_scope"] != scope
            or event["owner_scope"] != scope
            or event["visibility_scope"] != scope
            or event["payload"].get("member_id") != member
        ):
            return
        async with runtime.settings_command_locks.setdefault(
            f"cos-reminder-work:{reminder['event_id']}", asyncio.Lock()
        ):
            await self._reconcile(reminder)

    async def _append(
        self,
        reminder: dict[str, Any],
        cause: dict[str, Any],
        kind: str,
        version: int,
        payload: dict[str, Any],
    ) -> str:
        runtime = self.runtime
        key = f"cos-reminder-work:{reminder['event_id']}:{kind}:{cause['event_id']}"
        prior = await runtime.event_log.get_by_idempotency(reminder["tenant_id"], key)
        if prior:
            await runtime.wait_for_projection("tasks", prior["event_id"])
            return str(prior["event_id"])
        occurred = cause["occurred_at"]
        scope = service_private_scope(COS_SERVICE_PRINCIPAL_ID)
        event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(datetime.fromisoformat(occurred).timestamp() * 1000),
                tenant_id=reminder["tenant_id"],
                type=kind,
                schema_version=version,
                occurred_at=occurred,
                source_adapter="cos-reminder-workflow",
                source_account_id=COS_SERVICE_PRINCIPAL_ID,
                owner_scope=scope,
                visibility_scope=scope,
                sensitivity=reminder["sensitivity"],
                actor_identity=COS_SERVICE_PRINCIPAL_ID,
                idempotency_key=key,
                payload=payload,
            ),
            causation_id=cause["event_id"],
        )
        await runtime.wait_for_projection("tasks", event_id)
        return str(event_id)

    async def _reconcile(self, reminder: dict[str, Any]) -> None:
        runtime = self.runtime
        tenant = reminder["tenant_id"]
        principals = [
            principal
            for principal in runtime.settings.service_principals
            if principal.service_principal_id == COS_SERVICE_PRINCIPAL_ID
            and principal.kind == "chief_of_staff"
            and principal.party_id == COS_PARTY_ID
            and principal.private_scope
            == service_private_scope(COS_SERVICE_PRINCIPAL_ID)
        ]
        if len(principals) != 1:
            return
        member = reminder["payload"]["member_id"]
        task_id = reminder_work_id(reminder["event_id"])
        conn = runtime.projections.connection("tasks")
        row = conn.execute(
            "SELECT * FROM tasks WHERE tenant_id=? AND task_id=?", (tenant, task_id)
        ).fetchone()
        if row is None:
            # Projection consumers run independently. Wait for the grant view
            # before deciding whether an existing requested intent is readable.
            grant_event = await runtime.event_log.latest_event_id(
                types=[
                    "service.delegation.requested",
                    "service.delegation.approved",
                    "service.delegation.revoked",
                ]
            )
            if grant_event:
                await runtime.wait_for_projection("parties", grant_event)
            try:
                context = resolve_service_delegation(
                    runtime.projections.connection("parties"),
                    tenant_id=tenant,
                    service_principal_id=COS_SERVICE_PRINCIPAL_ID,
                    operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
                    context_scope="member_private",
                    context_member_id=member,
                )
            except ServiceDelegationResolutionError:
                return
            if "read_context" not in context.permissions:
                return
            coordinate = source_coordinate_for_event(
                reminder, provider_bindings=runtime.settings.provider_bindings
            )
            authority = {
                "source_event_id": reminder["event_id"],
                "authority_grant_id": context.authority_grant_id,
                "context_scope": "member_private",
                "context_member_id": member,
                "owner_party_id": None,
                "source_owner_scope": reminder["owner_scope"],
                "source_visibility_scope": reminder["visibility_scope"],
                "source_sensitivity": reminder["sensitivity"],
                "source_adapter": reminder["source_adapter"],
                "source_account_id": reminder["source_account_id"],
                "source_policy_key": coordinate.key,
                "source_policy_owner_id": coordinate.owner_principal_id,
                "source_audience": list(context.audience),
                "restriction": "internal_processing_only",
            }
            await self._append(
                reminder,
                reminder,
                "task.created",
                4,
                {
                    "task_id": task_id,
                    "title": "Deliver requested private reminder",
                    "due": reminder["payload"]["due_at"],
                    "work_kind": "service_operational",
                    "owner_party_id": COS_PARTY_ID,
                    "responsible_party_id": COS_PARTY_ID,
                    "assignee_party_id": COS_PARTY_ID,
                    "service_party_id": COS_PARTY_ID,
                    "service_assignment_role": "internal",
                    "authority_kind": "service_self",
                    "authority_grant_id": None,
                    "source_authorities": [authority],
                    "service_work_type": "reminder_like",
                    "created_by": COS_SERVICE_PRINCIPAL_ID,
                    "source_system": "cos-reminder-workflow",
                    "provider_effect_count": 0,
                },
            )
            row = conn.execute(
                "SELECT * FROM tasks WHERE tenant_id=? AND task_id=?", (tenant, task_id)
            ).fetchone()
        if row is None:
            return
        receipt = await reminder_receipt(runtime, reminder)
        cause_id = (
            receipt["terminal_event_id"]
            or receipt["approval_event_id"]
            or receipt["cancellation_event_id"]
            or receipt["binding_event_id"]
        )
        if cause_id is None:
            return
        cause = await runtime.event_log.get(cause_id)
        assert cause is not None
        if receipt["status"] == "sent":
            action, status, reason = "complete", "done", "completed"
        elif receipt["status"] == "cancelled":
            action, status, reason = "cancel", "cancelled", "cancelled"
        elif receipt["status"] == "waiting":
            action, status, reason = "hold", "deferred", "scheduled_hold"
        else:
            action, status, reason = "wait_on_other", "waiting_on", "reconciliation"
        await self._append(
            reminder,
            cause,
            "cos.work.state_changed",
            2,
            {
                "task_id": task_id,
                "action": action,
                "resulting_status": status,
                "changed_at": cause["occurred_at"],
                "actor_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "actor_service_party_id": COS_PARTY_ID,
                "operator_principal_id": COS_AUTOMATION_OPERATOR_ID,
                "authority_kind": "service_self",
                "authority_grant_id": None,
                "follow_up_at": reminder["payload"]["due_at"]
                if action == "hold"
                else None,
                "waiting_on_party_id": COS_PARTY_ID
                if action == "wait_on_other"
                else None,
                "reason_code": reason,
                "expected_last_event_id": row["last_event_id"],
                "provider_effect_count": 0,
            },
        )
