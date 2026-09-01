"""Authenticated loopback transport for the one native OpenClaw plugin.

OpenClaw authenticates the transport; AdministrateMe independently maps the
OpenClaw actor/channel/account tuple to a household member.  A gateway bearer
token, operator scope, or ``senderIsOwner`` value never authorizes household
data access.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.log import IdempotencyConflict
from adminme.lib.scope import filter_rows
from adminme.lib.session import Session, Source
from adminme.lib.skill_runner.wrapper import SkillContext
from adminme.openclaw_approvals import ApprovalReceiptError
from adminme.openclaw_topology import binding_fingerprints, member_agent_id
from adminme.products.comms import main as comms
from adminme.products.core import main as core
from adminme.products.member.surfaces import build_surface
from adminme.products.openclaw.constants import (
    BRIDGE_EVENT_KINDS,
    OPENCLAW_COMMIT,
    OPENCLAW_VERSION,
    PHASE3_SKILL_IDS,
    PLUGIN_ID,
)
from adminme.projections.tasks.queries import (
    get_task,
    list_coordination_records,
    service_work_for_context,
)
from adminme.projections.parties.queries import (
    ServiceDelegationResolutionError,
    effective_operating_agreements,
    resolve_service_delegation,
)
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
    DelegationPermission,
)

router = APIRouter(prefix="/api/openclaw/v1", tags=["openclaw"])


class Provenance(BaseModel):
    model_config = {"extra": "forbid"}
    actor_id: str = Field(min_length=1, max_length=300)
    agent_id: str | None = Field(default=None, max_length=200)
    session_key: str | None = Field(default=None, max_length=300)
    session_id: str | None = Field(default=None, max_length=300)
    channel_id: str = Field(min_length=1, max_length=200)
    account_id: str | None = Field(default=None, max_length=200)
    message_id: str | None = Field(default=None, max_length=300)
    thread_id: str | None = Field(default=None, max_length=300)
    correlation_id: str = Field(min_length=1, max_length=300)
    call_kind: Literal["tool", "dashboard", "slash_command", "standing_order", "bridge"]
    openclaw_version: Literal["2026.7.2"]
    openclaw_commit: Literal["ce67ffb70e83e5ebb56ea02e814e577153f84f61"]

    def audit_payload(self, *, member_id: str) -> dict[str, Any]:
        portal = self.channel_id == "administrateme-portal"
        return {
            "actor_id": self.actor_id,
            "member_id": member_id,
            "agent_id": self.agent_id,
            # The signed portal binding is validated in memory. Persisting it
            # would turn audit evidence into a retrievable session identifier.
            "session_key": None if portal else self.session_key,
            "session_id": None if portal else self.session_id,
            "channel_id": self.channel_id,
            "account_id": self.account_id,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "correlation_id": self.correlation_id,
            "call_kind": self.call_kind,
            "openclaw_version": self.openclaw_version,
            "openclaw_commit": self.openclaw_commit,
        }


class ContextRequest(BaseModel):
    model_config = {"extra": "forbid"}
    provenance: Provenance
    openclaw_approval_receipt_id: str | None = Field(
        default=None, min_length=1, max_length=240
    )


class ApprovalResolutionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    receipt_id: str = Field(min_length=1, max_length=240)
    tool_name: str = Field(min_length=1, max_length=120)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    decision: Literal["allow-once"]
    openclaw_version: Literal["2026.7.2"]
    openclaw_commit: Literal["ce67ffb70e83e5ebb56ea02e814e577153f84f61"]
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class ActionRequest(ContextRequest):
    request_id: str = Field(min_length=1, max_length=120)
    mode: Literal["propose", "capture"]
    kind: Literal["task", "commitment"]
    title: str = Field(min_length=1, max_length=500)
    due: str | None = None
    owed_to_party_id: str | None = Field(default=None, min_length=1)


class AddForReviewRequest(ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    text: str = Field(min_length=1, max_length=2000)
    kind_hint: (
        Literal[
            "task",
            "commitment",
            "calendar_event",
            "reminder",
            "list",
            "list_item",
            "recurrence",
            "party_identifier",
            "party_candidate",
            "party_relationship",
            "relationship_fact",
        ]
        | None
    ) = None


class ReviewRequest(ContextRequest):
    request_id: str = Field(min_length=1, max_length=120)
    action: Literal["inspect", "approve", "dismiss", "edit"]
    commitment_id: str | None = Field(default=None, min_length=1)
    expected_last_event_id: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    due_at: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    send_acknowledgement: bool = False


class LifecycleRequest(ContextRequest):
    request_id: str = Field(min_length=1, max_length=120)
    item_kind: Literal["task", "commitment"]
    item_id: str = Field(min_length=1)
    action: Literal[
        "complete",
        "defer",
        "snooze",
        "block",
        "unblock",
        "cancel",
        "reopen",
        "delegate",
    ]
    expected_last_event_id: str = Field(min_length=1)
    until: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)
    delegate_to_party_id: str | None = Field(default=None, min_length=1)


class CorrectionRequest(ContextRequest):
    request_id: str = Field(min_length=1, max_length=120)
    item_kind: Literal["task", "commitment"]
    item_id: str = Field(min_length=1)
    expected_last_event_id: str = Field(min_length=1)
    fields: dict[str, Any]


class SkillRequest(ContextRequest):
    skill_id: str = Field(min_length=1, max_length=120)
    inputs: dict[str, Any]
    input_sensitivities: dict[str, Literal["normal", "sensitive", "privileged"]] = (
        Field(default_factory=dict)
    )
    triggering_event_id: str | None = None


class ServiceWorkRequest(ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    context_scope: Literal["member_private", "household_shared"] = "member_private"
    service_assignment_role: Literal[
        "manages_member_obligation",
        "manages_household_obligation",
        "delegatee",
        "internal",
    ] = "internal"
    service_work_type: Literal[
        "operational_task",
        "waiting_for",
        "follow_up",
        "clarification",
        "exception",
        "preparation",
        "held_action",
        "provider_failure",
        "reconciliation",
        "operational_calendar",
        "reminder_like",
    ] = "operational_task"
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    due: AwareDatetime | None = None
    scheduled_start_at: AwareDatetime | None = None
    scheduled_end_at: AwareDatetime | None = None
    follow_up_at: AwareDatetime | None = None
    waiting_on_party_id: str | None = Field(default=None, min_length=1, max_length=200)
    linked_item_kind: (
        Literal[
            "task",
            "commitment",
            "calendar_event",
            "list_item",
            "provider_failure",
            "coordination",
        ]
        | None
    ) = None
    linked_item_id: str | None = Field(default=None, min_length=1, max_length=200)
    coordination_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_event_id: str | None = Field(default=None, min_length=1, max_length=240)
    recurrence_rrule: str | None = Field(default=None, min_length=1, max_length=500)
    recurrence_next_occurrence: AwareDatetime | None = None
    provider_representation_owner_kind: Literal["none", "member", "service"] = "none"
    provider_id: str | None = Field(default=None, min_length=1, max_length=120)
    provider_account_reference: str | None = Field(
        default=None, min_length=1, max_length=300
    )
    provider_workspace_reference: str | None = Field(
        default=None, min_length=1, max_length=300
    )
    provider_external_reference_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def validate_work_shape(self) -> ServiceWorkRequest:
        if (self.scheduled_start_at is None) != (self.scheduled_end_at is None):
            raise ValueError("scheduled work requires both start and end")
        if (
            self.scheduled_start_at is not None
            and self.scheduled_end_at is not None
            and self.scheduled_end_at <= self.scheduled_start_at
        ):
            raise ValueError("scheduled work must end after it starts")
        if (self.linked_item_kind is None) != (self.linked_item_id is None):
            raise ValueError("linked item kind and id must be supplied together")
        if (self.recurrence_rrule is None) != (self.recurrence_next_occurrence is None):
            raise ValueError("recurring work requires a rule and next occurrence")
        representation = (
            self.provider_id,
            self.provider_account_reference,
            self.provider_workspace_reference,
            self.provider_external_reference_digest,
        )
        if self.provider_representation_owner_kind == "none" and any(representation):
            raise ValueError("unrepresented work cannot carry provider coordinates")
        if self.provider_representation_owner_kind != "none" and not all(
            representation[:2]
        ):
            raise ValueError("provider representation requires provider and account")
        return self


class ServiceWorkTransitionRequest(ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    context_scope: Literal["member_private", "household_shared"] = "member_private"
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal[
        "start",
        "wait_on_member",
        "wait_on_other",
        "follow_up",
        "hold",
        "resume",
        "complete",
        "cancel",
    ]
    waiting_on_member_id: str | None = Field(default=None, min_length=1, max_length=200)
    waiting_on_party_id: str | None = Field(default=None, min_length=1, max_length=200)
    follow_up_at: AwareDatetime | None = None
    reason_code: Literal[
        "member_input",
        "external_dependency",
        "clarification",
        "provider_failure",
        "reconciliation",
        "scheduled_hold",
        "completed",
        "cancelled",
        "other",
    ]

    @model_validator(mode="after")
    def validate_transition_shape(self) -> ServiceWorkTransitionRequest:
        if self.action == "wait_on_member":
            if not self.waiting_on_member_id or self.waiting_on_party_id:
                raise ValueError("member wait requires exactly one member dependency")
        elif self.action == "wait_on_other":
            if not self.waiting_on_party_id or self.waiting_on_member_id:
                raise ValueError("external wait requires exactly one Party dependency")
        elif self.action in {"follow_up", "hold"}:
            if self.follow_up_at is None:
                raise ValueError("follow-up/hold requires a future activation")
            if self.waiting_on_member_id or self.waiting_on_party_id:
                raise ValueError("follow-up/hold cannot name a waiting dependency")
        elif (
            self.waiting_on_member_id
            or self.waiting_on_party_id
            or self.follow_up_at is not None
        ):
            raise ValueError(
                "this action cannot carry waiting or follow-up coordinates"
            )
        if self.follow_up_at is not None and self.follow_up_at <= datetime.now(
            timezone.utc
        ):
            raise ValueError("follow-up activation must be in the future")
        return self


class BridgeRequest(ContextRequest):
    event_kind: Literal["commitment_candidate", "household_action", "delivery_outcome"]
    content: str = Field(min_length=1, max_length=2000)
    consent: Literal[True]
    sensitivity: Literal["normal", "sensitive"] = "normal"


class StandingOrderRequest(ContextRequest):
    program_id: str = Field(min_length=1, max_length=120)
    automation_id: str = Field(min_length=1, max_length=200)
    trigger_id: str = Field(min_length=1, max_length=300)


class ModelCallRequest(ContextRequest):
    phase: Literal["started", "ended"]
    run_id: str = Field(min_length=1, max_length=300)
    provider: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=300)


class NotificationResultRequest(ContextRequest):
    attempt_id: str = Field(min_length=1, max_length=240)
    decision_id: str = Field(min_length=1, max_length=240)
    signal_id: str = Field(min_length=1, max_length=240)
    outcome: Literal["delivered", "failed", "retrying"]
    attempted_at: str = Field(min_length=1)
    provider_receipt_id: str | None = Field(default=None, max_length=500)
    error_code: str | None = Field(default=None, max_length=120)
    next_retry_at: str | None = None


class ExecutePendingProviderActionRequest(ContextRequest):
    domain_approval_event_id: str = Field(min_length=1, max_length=240)


class HealthReport(BaseModel):
    model_config = {"extra": "forbid"}
    state: Literal["healthy", "degraded", "unavailable", "incompatible"]
    reason: str | None = Field(default=None, max_length=500)
    openclaw_version: str = Field(min_length=1)
    openclaw_commit: str = Field(min_length=7)
    dm_scope: Literal["per_account_channel_peer"]
    member_agent_ids: list[str] = Field(max_length=50)
    binding_fingerprints: list[str] = Field(max_length=200)
    unique_agents: bool
    unique_accounts: bool


def _runtime(request: Request) -> Any:
    return request.app.state.runtime


def _authenticate(request: Request) -> None:
    runtime = _runtime(request)
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "OpenClaw transport is loopback-only")
    expected = runtime.settings.openclaw_shared_secret
    supplied = request.headers.get("Authorization", "")
    if not expected or not supplied.startswith("Bearer "):
        raise HTTPException(401, "OpenClaw transport credential required")
    if not hmac.compare_digest(supplied[7:], expected):
        raise HTTPException(401, "OpenClaw transport credential rejected")


def _session(
    request: Request,
    provenance: Provenance,
    *,
    required_permission: DelegationPermission = "read_context",
    context_scope: Literal["member_private", "household_shared"] = "member_private",
) -> Session:
    _authenticate(request)
    runtime = _runtime(request)
    account = provenance.account_id or "-"
    key = f"{provenance.channel_id}|{account}|{provenance.actor_id}"
    member_id = runtime.settings.openclaw_actor_identities.get(key)
    portal_binding = (
        provenance.channel_id == "administrateme-portal"
        and provenance.account_id == "member-portal"
        and provenance.call_kind == "tool"
    )
    if member_id is None and portal_binding:
        candidate = runtime.resolve_member_chat_actor(provenance.actor_id)
        if candidate is not None:
            expected_actor, expected_session = runtime.member_chat_binding(candidate)
            if (
                hmac.compare_digest(provenance.actor_id, expected_actor)
                and hmac.compare_digest(provenance.session_key or "", expected_session)
                and hmac.compare_digest(
                    provenance.agent_id or "", member_agent_id(candidate)
                )
            ):
                member_id = candidate
    if not member_id:
        raise HTTPException(403, "OpenClaw actor is not mapped to a household member")
    role = runtime.settings.member_roles.get(member_id)
    if role not in {"principal", "child", "device"}:
        raise HTTPException(
            403, "mapped OpenClaw actor has no interactive household role"
        )
    source: Source
    if portal_binding:
        source = "member_portal"
    elif provenance.call_kind == "slash_command":
        source = "openclaw_slash_command"
    elif provenance.call_kind == "standing_order":
        source = "openclaw_standing_order"
    else:
        source = "openclaw_plugin"
    service_context = None
    if role == "principal":
        if runtime.projections is None:
            raise HTTPException(503, "CoS delegation projection is unavailable")
        chief_of_staff = [
            principal
            for principal in runtime.settings.service_principals
            if principal.service_principal_id == COS_SERVICE_PRINCIPAL_ID
            and principal.kind == "chief_of_staff"
        ]
        if len(chief_of_staff) != 1:
            raise HTTPException(503, "exactly one household CoS is required")
        try:
            service_context = resolve_service_delegation(
                runtime.projections.connection("parties"),
                tenant_id=runtime.settings.instance.tenant_id,
                service_principal_id=chief_of_staff[0].service_principal_id,
                operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
                context_scope=context_scope,
                context_member_id=(
                    member_id if context_scope == "member_private" else None
                ),
            )
        except ServiceDelegationResolutionError as exc:
            raise HTTPException(403, str(exc)) from exc
        if required_permission not in service_context.permissions:
            raise HTTPException(
                403,
                f"CoS delegation does not authorize {required_permission}",
            )
    return Session(
        tenant_id=runtime.settings.instance.tenant_id,
        auth_member_id=member_id,
        auth_role=role,
        view_member_id=member_id,
        view_role=role,
        dm_scope="per_account_channel_peer",
        source=source,
        correlation_id=provenance.correlation_id,
        service_context=service_context,
    )


def _require_adult(session: Session) -> None:
    if session.auth_role == "child":
        raise HTTPException(
            403, "child chat tool is outside the guardian-safe allowlist"
        )


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def _append(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    source_account_id: str,
    owner_scope: str,
    visibility_scope: str,
    sensitivity: Sensitivity = "normal",
    request_id: str | None = None,
    causation_id: str | None = None,
    request_fingerprint: str | None = None,
    schema_version: int = 1,
) -> tuple[str, bool]:
    key = (
        f"openclaw:{session.auth_member_id}:{event_type}:{request_id}"
        if request_id
        else None
    )
    fingerprint = request_fingerprint or _fingerprint(payload)
    if key:
        prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
        if prior is not None:
            if prior["raw_ref"] != f"request-sha256:{fingerprint}":
                raise HTTPException(409, "idempotency key conflicts with prior body")
            return prior["event_id"], True
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=schema_version,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="openclaw-plugin",
        source_account_id=source_account_id,
        owner_scope=owner_scope,
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,
        actor_identity=session.event_actor_identity,
        idempotency_key=key,
        raw_ref=f"request-sha256:{fingerprint}" if key else None,
        payload=payload,
    )
    try:
        event_id = await runtime.events.write(
            envelope,
            correlation_id=session.correlation_id,
            causation_id=causation_id,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, "idempotency key conflicts with prior body") from exc
    return event_id, False


async def _prior_append(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    request_id: str,
    request_fingerprint: str,
) -> str | None:
    key = f"openclaw:{session.auth_member_id}:{event_type}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior["raw_ref"] != f"request-sha256:{request_fingerprint}":
        raise HTTPException(409, "idempotency key conflicts with prior body")
    return prior["event_id"]


async def _record_tool_call(
    request: Request,
    session: Session,
    provenance: Provenance,
    *,
    operation: str,
    result_event_ids: list[str] | None = None,
) -> str:
    schema_version = 1
    if session.service_context is None:
        payload = {
            "operation": operation,
            "result_event_ids": list(result_event_ids or []),
            **provenance.audit_payload(member_id=session.auth_member_id),
        }
    else:
        context = session.service_context
        portal = provenance.channel_id == "administrateme-portal"
        payload = {
            "operation": operation,
            **context.audit_payload(),
            "permissions": sorted(context.permissions),
            "openclaw_actor_id": provenance.actor_id,
            "agent_id": provenance.agent_id,
            "session_key": None if portal else provenance.session_key,
            "session_id": None if portal else provenance.session_id,
            "channel_id": provenance.channel_id,
            "account_id": provenance.account_id,
            "message_id": provenance.message_id,
            "thread_id": provenance.thread_id,
            "request_correlation_id": provenance.correlation_id,
            "causation_event_ids": list(
                result_event_ids[-1:] if result_event_ids else []
            ),
            "result_event_ids": list(result_event_ids or []),
            "observation_event_ids": [],
            "reconciliation_event_ids": [],
            "call_kind": provenance.call_kind,
            "openclaw_version": provenance.openclaw_version,
            "openclaw_commit": provenance.openclaw_commit,
        }
        schema_version = 2
    event_id, _ = await _append(
        _runtime(request),
        session,
        event_type="openclaw.tool_call.recorded",
        source_account_id=provenance.account_id or provenance.channel_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        payload=payload,
        request_id=f"{operation}:{provenance.correlation_id}",
        causation_id=result_event_ids[-1] if result_event_ids else None,
        schema_version=schema_version,
    )
    return event_id


async def _consume_openclaw_approval(
    request: Request,
    session: Session,
    provenance: Provenance,
    body: ContextRequest,
    *,
    tool_name: str,
    action: str,
    causation_id: str | None = None,
) -> str:
    """Consume the exact allow-once receipt and append its private audit fact."""

    receipt_id = body.openclaw_approval_receipt_id
    if not receipt_id:
        raise HTTPException(403, "independent OpenClaw allow-once approval is required")
    runtime = _runtime(request)
    if runtime.openclaw_approval_store is None:
        raise HTTPException(503, "OpenClaw approval storage is unavailable")
    values = body.model_dump(
        mode="json",
        exclude={"provenance", "openclaw_approval_receipt_id"},
        exclude_unset=True,
    )
    fingerprint = _fingerprint(values)
    try:
        receipt = runtime.openclaw_approval_store.consume(
            receipt_id=receipt_id,
            tool_name=tool_name,
            request_fingerprint=fingerprint,
            member_id=session.auth_member_id,
            allow_same_binding_recovery=True,
        )
    except ApprovalReceiptError as exc:
        raise HTTPException(403, str(exc)) from exc
    consumed_at = EventEnvelope.now_utc_iso()
    event_id, _ = await _append(
        runtime,
        session,
        event_type="openclaw.approval.received",
        source_account_id=provenance.account_id or provenance.channel_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        payload={
            "receipt_id": receipt.receipt_id,
            "member_id": session.auth_member_id,
            "tool_name": receipt.tool_name,
            "action": action,
            "request_fingerprint": receipt.request_fingerprint,
            "resource_fingerprint": receipt.request_fingerprint,
            "issued_at": datetime.fromtimestamp(
                receipt.issued_at_ms / 1000, tz=timezone.utc
            ).isoformat(),
            "expires_at": datetime.fromtimestamp(
                receipt.expires_at_ms / 1000, tz=timezone.utc
            ).isoformat(),
            "consumed_at": consumed_at,
            "single_use": True,
            "approval_method": "allow_once",
            "openclaw_version": receipt.openclaw_version,
            "openclaw_commit": receipt.openclaw_commit,
            "provenance": provenance.audit_payload(member_id=session.auth_member_id),
        },
        request_id=receipt.receipt_id,
        causation_id=causation_id,
        request_fingerprint=receipt.request_fingerprint,
    )
    return event_id


def _result_event_ids(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    value = result.get("event_id")
    return [value] if isinstance(value, str) and value else []


async def _guard(runtime: Any, session: Session, action: str, payload: dict) -> None:
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(session, action, payload)
    if not decision.pass_:
        raise HTTPException(
            403, {"code": "write_denied", "layer": decision.layer_failed}
        )


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    _authenticate(request)
    runtime = _runtime(request)
    return {
        "plugin_id": PLUGIN_ID,
        "expected_openclaw_version": OPENCLAW_VERSION,
        "expected_openclaw_commit": OPENCLAW_COMMIT,
        "compatible": True,
        "runtime": await runtime.diagnostics(),
    }


@router.post("/approvals/resolve")
async def resolve_approval(
    body: ApprovalResolutionRequest, request: Request
) -> dict[str, Any]:
    """Record an allow-once decision emitted only by OpenClaw's resolution hook."""

    _authenticate(request)
    runtime = _runtime(request)
    if runtime.openclaw_approval_store is None:
        raise HTTPException(503, "OpenClaw approval storage is unavailable")
    secret = runtime.settings.openclaw_shared_secret
    if not secret:
        raise HTTPException(503, "OpenClaw approval credential is unavailable")
    try:
        receipt = runtime.openclaw_approval_store.resolve(
            secret=secret,
            receipt_id=body.receipt_id,
            tool_name=body.tool_name,
            request_fingerprint=body.request_fingerprint,
            issued_at_ms=body.issued_at_ms,
            expires_at_ms=body.expires_at_ms,
            openclaw_version=body.openclaw_version,
            openclaw_commit=body.openclaw_commit,
            signature=body.signature,
        )
    except ApprovalReceiptError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {
        "accepted": True,
        "receipt_id": receipt.receipt_id,
        "expires_at_ms": receipt.expires_at_ms,
    }


@router.post("/health/report")
async def report_health(body: HealthReport, request: Request) -> dict[str, Any]:
    _authenticate(request)
    runtime = _runtime(request)
    compatible = (
        body.openclaw_version == OPENCLAW_VERSION
        and body.openclaw_commit == OPENCLAW_COMMIT
    )
    expected_members = set(runtime.settings.member_chat_enabled_members) | set(
        runtime.settings.openclaw_actor_identities.values()
    )
    expected_agents = {member_agent_id(member) for member in expected_members}
    expected_bindings = set(
        binding_fingerprints(
            runtime.settings.openclaw_actor_identities,
            runtime.settings.member_chat_enabled_members,
        )
    )
    topology_compatible = (
        set(body.member_agent_ids) == expected_agents
        and len(body.member_agent_ids) == len(expected_agents)
        and set(body.binding_fingerprints) == expected_bindings
        and len(body.binding_fingerprints) == len(expected_bindings)
        and body.unique_agents
        and body.unique_accounts
    )
    compatible = compatible and topology_compatible
    state = body.state if compatible else "incompatible"
    runtime.openclaw_topology_report = {
        "dm_scope": body.dm_scope,
        "member_ids_with_agents": sorted(expected_members)
        if set(body.member_agent_ids) == expected_agents
        else [],
        "member_ids_with_account_bindings": sorted(expected_members)
        if set(body.binding_fingerprints) == expected_bindings
        else [],
        "unique_agents": body.unique_agents
        and len(body.member_agent_ids) == len(set(body.member_agent_ids)),
        "unique_accounts": body.unique_accounts
        and len(body.binding_fingerprints) == len(set(body.binding_fingerprints)),
        "tested_at": EventEnvelope.now_utc_iso(),
        "content_exposed": False,
    }
    event_id: str | None = None
    if (
        runtime.openclaw_last_report_monotonic is None
        or runtime.openclaw_state != state
    ):
        session = Session(
            tenant_id=runtime.settings.instance.tenant_id,
            auth_member_id="openclaw-plugin",
            auth_role="device",
            view_member_id="openclaw-plugin",
            view_role="device",
            dm_scope="shared",
            source="openclaw_plugin",
            correlation_id=f"openclaw-health:{int(time.time())}",
        )
        event_id, _ = await _append(
            runtime,
            session,
            event_type="openclaw.integration.state_changed",
            source_account_id="health",
            owner_scope="shared:household",
            visibility_scope="shared:household",
            payload={
                "state": state,
                "observed_at": EventEnvelope.now_utc_iso(),
                "openclaw_version": body.openclaw_version,
                "openclaw_commit": body.openclaw_commit,
                "reason": body.reason,
            },
        )
    runtime.record_openclaw_health_report(state)
    await runtime._refresh_capability_registry()
    if not compatible:
        raise HTTPException(
            409, {"code": "incompatible_openclaw", "event_id": event_id}
        )
    return {"event_id": event_id, "state": state}


@router.post("/tools/state")
async def inspect_state(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = _runtime(request)
    now = EventEnvelope.now_utc_iso()
    result = {
        "what_now": core._what_now(runtime, session),
        "triage": await core.triage(request, session),
        "commitments": await core.active_commitments(request, session),
        "cos_work": service_work_for_context(
            runtime.projections.connection("tasks"),
            session,
            service_party_id=COS_PARTY_ID,
        ),
        "coordination": list_coordination_records(
            runtime.projections.connection("tasks"), session, as_of_iso=now
        ),
        "operating_agreements": effective_operating_agreements(
            runtime.projections.connection("parties"), session, as_of_iso=now
        ),
        "provider_effect_count": 0,
    }
    await _record_tool_call(request, session, body.provenance, operation="tools.state")
    return result


@router.post("/tools/what-now")
async def what_now(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    result = core._what_now(_runtime(request), session)
    await _record_tool_call(
        request, session, body.provenance, operation="tools.what-now"
    )
    return result


@router.post("/tools/service-work", status_code=201)
async def create_service_work(
    body: ServiceWorkRequest, request: Request
) -> dict[str, Any]:
    """Create typed CoS-managed or CoS-owned work without a provider write."""

    required_permission: DelegationPermission
    if body.service_assignment_role == "manages_member_obligation":
        required_permission = "manage_member_obligations"
    elif body.service_assignment_role == "manages_household_obligation":
        required_permission = "coordinate_household_work"
    else:
        required_permission = "create_internal_work"
    session = _session(
        request,
        body.provenance,
        required_permission=required_permission,
        context_scope=body.context_scope,
    )
    context = session.service_context
    if context is None or required_permission not in context.permissions:
        raise HTTPException(403, "CoS delegation does not authorize this work")
    if body.service_assignment_role in {
        "manages_member_obligation",
        "delegatee",
    } and (context.context_scope != "member_private" or not context.context_member_id):
        raise HTTPException(403, "member work requires one exact member context")
    if body.service_assignment_role == "manages_household_obligation" and (
        context.context_scope != "household_shared"
    ):
        raise HTTPException(403, "household work requires shared household authority")
    if body.provider_representation_owner_kind == "member" and (
        context.context_scope != "member_private" or not context.context_member_id
    ):
        raise HTTPException(403, "member provider representation requires its owner")
    runtime = _runtime(request)
    task_digest = hashlib.sha256(
        f"{session.tenant_id}|{context.authority_grant_id}|{body.request_id}".encode()
    ).hexdigest()[:32]
    task_id = f"cos-task:{task_digest}"
    household_party_id = str(
        runtime.settings.household_mapping.get("household_party_id")
        or "household:default"
    )
    member_id = context.context_member_id
    if body.service_assignment_role == "manages_member_obligation":
        work_kind = "member_obligation"
        owner_member_id = member_id
        owner_party_id = None
        delegated_by_member_id = member_id
        on_behalf_of_member_id = member_id
        beneficiary_party_id = member_id
    elif body.service_assignment_role == "manages_household_obligation":
        work_kind = "household_work"
        owner_member_id = None
        owner_party_id = household_party_id
        delegated_by_member_id = None
        on_behalf_of_member_id = None
        beneficiary_party_id = household_party_id
    else:
        work_kind = "service_operational"
        owner_member_id = None
        owner_party_id = COS_PARTY_ID
        delegated_by_member_id = (
            member_id if body.service_assignment_role == "delegatee" else None
        )
        on_behalf_of_member_id = (
            member_id if body.service_assignment_role == "delegatee" else None
        )
        beneficiary_party_id = member_id
    owner_scope = (
        "shared:household"
        if body.service_assignment_role == "manages_household_obligation"
        else (
            f"private:{member_id}"
            if member_id is not None
            else f"service-private:{COS_SERVICE_PRINCIPAL_ID}"
        )
    )
    recurrence_id = (
        f"cos-recurrence:{task_digest}" if body.recurrence_rrule is not None else None
    )
    representation_owner = (
        member_id
        if body.provider_representation_owner_kind == "member"
        else (
            COS_SERVICE_PRINCIPAL_ID
            if body.provider_representation_owner_kind == "service"
            else None
        )
    )
    payload = {
        "task_id": task_id,
        "title": body.title,
        "description": body.description,
        "due": body.due.isoformat() if body.due else None,
        "work_kind": work_kind,
        "owner_member_id": owner_member_id,
        "owner_party_id": owner_party_id,
        "responsible_party_id": COS_PARTY_ID,
        "assignee_party_id": COS_PARTY_ID,
        "beneficiary_party_id": beneficiary_party_id,
        "service_assignment_role": body.service_assignment_role,
        "service_party_id": COS_PARTY_ID,
        "authority_grant_id": context.authority_grant_id,
        "on_behalf_of_member_id": on_behalf_of_member_id,
        "delegated_by_member_id": delegated_by_member_id,
        "service_work_type": body.service_work_type,
        "linked_item_kind": body.linked_item_kind,
        "linked_item_id": body.linked_item_id,
        "recurring_id": recurrence_id,
        "scheduled_start_at": (
            body.scheduled_start_at.isoformat() if body.scheduled_start_at else None
        ),
        "scheduled_end_at": (
            body.scheduled_end_at.isoformat() if body.scheduled_end_at else None
        ),
        "follow_up_at": body.follow_up_at.isoformat() if body.follow_up_at else None,
        "waiting_on_party_id": body.waiting_on_party_id,
        "coordination_id": body.coordination_id,
        "source_event_id": body.source_event_id,
        "source_invocation_id": body.provenance.correlation_id,
        "provider_representation_owner_kind": (body.provider_representation_owner_kind),
        "provider_representation_owner_principal_id": representation_owner,
        "provider_id": body.provider_id,
        "provider_account_reference": body.provider_account_reference,
        "provider_workspace_reference": body.provider_workspace_reference,
        "provider_external_reference_digest": (body.provider_external_reference_digest),
        "provider_effect_count": 0,
        "created_by": context.actor_principal_id,
        "source_system": "household-cos",
    }
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="task.created",
        schema_version=3,
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        payload=payload,
        request_id=body.request_id,
    )
    await runtime.wait_for_projection("tasks", event_id)
    result_event_ids = [event_id]
    recurrence_event_id: str | None = None
    if recurrence_id is not None:
        assert body.recurrence_rrule is not None
        assert body.recurrence_next_occurrence is not None
        recurrence_event_id, _ = await _append(
            runtime,
            session,
            event_type="recurrence.added",
            source_account_id=(
                body.provenance.account_id or body.provenance.channel_id
            ),
            owner_scope=owner_scope,
            visibility_scope=owner_scope,
            payload={
                "recurrence_id": recurrence_id,
                "linked_kind": "household",
                "linked_id": household_party_id,
                "kind": f"cos:{body.service_work_type}",
                "rrule": body.recurrence_rrule,
                "next_occurrence": body.recurrence_next_occurrence.isoformat(),
                "lead_time_days": 0,
                "trackable": True,
                "notes": "Typed CoS work recurrence; does not invoke OpenClaw",
            },
            request_id=f"{body.request_id}:recurrence",
        )
        await runtime.wait_for_projection("recurrences", recurrence_event_id)
        result_event_ids.append(recurrence_event_id)
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.service-work",
        result_event_ids=result_event_ids,
    )
    return {
        "task_id": task_id,
        "event_id": event_id,
        "recurrence_event_id": recurrence_event_id,
        "duplicate": duplicate,
        "work_kind": work_kind,
        "service_assignment_role": body.service_assignment_role,
        "owner_member_id": owner_member_id,
        "owner_party_id": owner_party_id,
        "responsible_party_id": COS_PARTY_ID,
        "assignee_party_id": COS_PARTY_ID,
        "beneficiary_party_id": beneficiary_party_id,
        "member_obligation_created": work_kind == "member_obligation",
        "member_obligation_managed_by_cos": (
            body.service_assignment_role == "manages_member_obligation"
        ),
        "recurrence_invokes_openclaw": False,
        "provider_effects": 0,
    }


@router.post("/tools/service-work/{task_id}/state")
async def transition_service_work(
    task_id: str, body: ServiceWorkTransitionRequest, request: Request
) -> dict[str, Any]:
    """Advance CoS work under its exact grant without impersonating a member."""

    permission_by_role: dict[str, DelegationPermission] = {
        "manages_member_obligation": "manage_member_obligations",
        "manages_household_obligation": "coordinate_household_work",
        "delegatee": "manage_internal_work",
        "internal": "manage_internal_work",
    }
    # Resolve read context first so the task can select the narrower lifecycle
    # permission without accepting caller-provided ownership claims.
    session = _session(
        request,
        body.provenance,
        required_permission="read_context",
        context_scope=body.context_scope,
    )
    runtime = _runtime(request)
    row = get_task(runtime.projections.connection("tasks"), session, task_id=task_id)
    if row is None or row.get("service_party_id") != COS_PARTY_ID:
        raise HTTPException(404, "CoS work not found")
    role = str(row.get("service_assignment_role") or "")
    required = permission_by_role.get(role)
    context = session.service_context
    if required is None or context is None or required not in context.permissions:
        raise HTTPException(403, "CoS delegation does not authorize this transition")
    if row.get("authority_grant_id") != context.authority_grant_id:
        raise HTTPException(403, "CoS work belongs to a different authority grant")
    if row.get("last_event_id") != body.expected_last_event_id:
        raise HTTPException(409, "CoS work changed")
    if body.waiting_on_member_id is not None:
        if context.context_scope == "member_private" and (
            body.waiting_on_member_id != context.context_member_id
        ):
            raise HTTPException(403, "private work may wait only on its active member")
        if body.waiting_on_member_id not in runtime.settings.member_roles:
            raise HTTPException(422, "waiting member is not in the household")
    status_by_action = {
        "start": "in_progress",
        "wait_on_member": "waiting_on",
        "wait_on_other": "waiting_on",
        "follow_up": "deferred",
        "hold": "deferred",
        "resume": "next",
        "complete": "done",
        "cancel": "cancelled",
    }
    now = EventEnvelope.now_utc_iso()
    payload = {
        "task_id": task_id,
        "action": body.action,
        "resulting_status": status_by_action[body.action],
        "changed_at": now,
        "actor_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "actor_service_party_id": COS_PARTY_ID,
        "operator_principal_id": COS_AUTOMATION_OPERATOR_ID,
        "authority_grant_id": context.authority_grant_id,
        "active_member_id": context.context_member_id,
        "on_behalf_of_member_id": context.context_member_id,
        "waiting_on_party_id": body.waiting_on_party_id,
        "waiting_on_member_id": body.waiting_on_member_id,
        "follow_up_at": body.follow_up_at.isoformat() if body.follow_up_at else None,
        "reason_code": body.reason_code,
        "expected_last_event_id": body.expected_last_event_id,
        "provider_effect_count": 0,
    }
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="cos.work.state_changed",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=str(row["owner_scope"]),
        visibility_scope=str(row["visibility_scope"]),
        payload=payload,
        request_id=body.request_id,
    )
    await runtime.wait_for_projection("tasks", event_id)
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.service-work.state",
        result_event_ids=[event_id],
    )
    return {
        "task_id": task_id,
        "event_id": event_id,
        "duplicate": duplicate,
        "status": status_by_action[body.action],
        "actor_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "completed_by_member_id": None,
        "provider_effect_count": 0,
    }


@router.post("/tools/execute-pending-provider-action")
async def execute_pending_provider_action(
    body: ExecutePendingProviderActionRequest, request: Request
) -> dict[str, Any]:
    """Consume both independent approvals before creating one provider command."""

    session = _session(request, body.provenance)
    _require_adult(session)
    result = await _execute_pending_provider_action(
        request,
        session,
        body.provenance,
        body,
        domain_approval_event_id=body.domain_approval_event_id,
        tool_name="administrateme_execute_pending_provider_action",
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.execute-pending-provider-action",
        result_event_ids=[result["event_id"]],
    )
    return result


async def _execute_pending_provider_action(
    request: Request,
    session: Session,
    provenance: Provenance,
    approval_body: ContextRequest,
    *,
    domain_approval_event_id: str,
    tool_name: str,
) -> dict[str, Any]:
    """Bind a signed OpenClaw decision to one immutable member decision."""

    runtime = _runtime(request)
    domain_event = await runtime.event_log.get(domain_approval_event_id)
    if (
        domain_event is None
        or domain_event.get("type") != "member.provider_action.confirmed"
        or domain_event.get("tenant_id") != session.tenant_id
        or domain_event.get("owner_scope") != f"private:{session.auth_member_id}"
        or domain_event.get("payload", {}).get("member_id") != session.auth_member_id
    ):
        raise HTTPException(404, "pending provider action not found")
    prior_key = f"external-sync-approved:{domain_event['event_id']}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, prior_key)
    if prior is not None:
        return {
            "accepted": True,
            "duplicate": True,
            "event_id": prior["event_id"],
            "domain_approval_event_id": domain_event["event_id"],
        }
    payload = domain_event["payload"]
    request_fingerprint = str(payload["request_fingerprint"])
    approval_event_id = await _consume_openclaw_approval(
        request,
        session,
        provenance,
        approval_body,
        tool_name=tool_name,
        action=str(payload["operation"]),
        causation_id=domain_event["event_id"],
    )
    now = EventEnvelope.now_utc_iso()
    command_payload = {
        "command_id": payload["action_id"],
        "member_id": session.auth_member_id,
        "provider": payload["provider"],
        "provider_account_id": payload["provider_account_id"],
        "operation": payload["operation"],
        "target_external_id": payload.get("target_external_id"),
        "expected_provider_revision": payload.get("expected_provider_revision"),
        "payload": dict(payload.get("command_payload") or {}),
        "requested_at": now,
        "domain_approval_event_id": domain_event["event_id"],
        "openclaw_approval_event_id": approval_event_id,
        "request_fingerprint": request_fingerprint,
        "privacy_guard_version": "member-provider-action-v1",
        "origin_proposal_id": payload.get("origin_proposal_id"),
    }
    await _guard(runtime, session, "external_sync.write_requested", command_payload)
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="external_sync.write_requested",
            schema_version=2,
            occurred_at=now,
            source_adapter="openclaw-plugin",
            source_account_id=provenance.account_id or provenance.channel_id,
            owner_scope=domain_event["owner_scope"],
            visibility_scope=domain_event["visibility_scope"],
            sensitivity=domain_event["sensitivity"],
            correlation_id=session.correlation_id,
            causation_id=approval_event_id,
            actor_identity=session.auth_member_id,
            idempotency_key=prior_key,
            raw_ref=f"request-sha256:{request_fingerprint}",
            payload=command_payload,
        )
    )
    return {
        "accepted": True,
        "duplicate": False,
        "event_id": event_id,
        "domain_approval_event_id": domain_event["event_id"],
        "openclaw_approval_event_id": approval_event_id,
        "observation_mode": runtime._observation_active,
    }


@router.post("/tools/action")
async def propose_or_capture(body: ActionRequest, request: Request) -> dict[str, Any]:
    """Compatibility alias that can only propose; direct capture is superseded."""

    session = _session(request, body.provenance)
    _require_adult(session)
    runtime = _runtime(request)
    observation_active = await runtime.observation.is_active()
    payload = {
        "proposal_id": f"ocp_{body.request_id}",
        "kind": body.kind,
        "title": body.title,
        "owner_member_id": session.auth_member_id,
        "due": body.due,
        "reason": "observation_mode_active"
        if observation_active
        else "explicit_proposal",
        "provenance": body.provenance.audit_payload(member_id=session.auth_member_id),
    }
    await _guard(runtime, session, "household.action.proposed", payload)
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="household.action.proposed",
        source_account_id="actions",
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=_fingerprint(
            body.model_dump(mode="json", exclude={"provenance"})
        ),
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.action",
        result_event_ids=[event_id],
    )
    return {
        "state": "proposal_only",
        "observation_active": observation_active,
        "event_id": event_id,
        "duplicate": duplicate,
    }


@router.post("/tools/add-for-review")
async def add_for_review(body: AddForReviewRequest, request: Request) -> dict[str, Any]:
    """Native OpenClaw exact-text entry into the same member Triage contract."""

    session = _session(request, body.provenance)
    _require_adult(session)
    from adminme.products.member.main import (  # local import avoids router cycle
        TriageIntentRequest,
        submit_triage_intent,
    )

    result = await submit_triage_intent(
        TriageIntentRequest(
            text=body.text,
            source_surface="openclaw",
            kind_hint=body.kind_hint,
            client_request_id=body.request_id,
        ),
        request,
        session,
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.add-for-review",
        result_event_ids=[
            result["intent_event_id"],
            *result["proposal_event_ids"],
        ],
    )
    return result


@router.post("/tools/review")
async def review(body: ReviewRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    _require_adult(session)
    if body.action == "inspect":
        result = await comms.pending(request, session)
        await _record_tool_call(
            request, session, body.provenance, operation="tools.review.inspect"
        )
        return result
    if not body.commitment_id or not body.expected_last_event_id:
        raise HTTPException(
            422, "commitment_id and expected_last_event_id are required"
        )
    if body.action == "approve":
        result = await comms.approve_commitment(
            body.commitment_id,
            comms.ApproveRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                send_acknowledgement=body.send_acknowledgement,
            ),
            request,
            session,
        )
        pending_event_id = result.get("pending_provider_action_event_id")
        if body.send_acknowledgement and isinstance(pending_event_id, str):
            external = await _execute_pending_provider_action(
                request,
                session,
                body.provenance,
                body,
                domain_approval_event_id=pending_event_id,
                tool_name="administrateme_review",
            )
            result["external_write"] = external
            result["delivery"] = {
                "delivered": False,
                "suppressed": False,
                "held": True,
                "reason": "provider_result_pending",
                "command_event_id": external["event_id"],
            }
    elif body.action == "dismiss":
        result = await comms.dismiss_commitment(
            body.commitment_id,
            comms.DismissRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                reason=body.reason,
            ),
            request,
            session,
        )
    else:
        result = await comms.edit_commitment(
            body.commitment_id,
            comms.EditRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                description=body.description,
                due_at=body.due_at,
            ),
            request,
            session,
        )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"tools.review.{body.action}",
        result_event_ids=[
            *(_result_event_ids(result)),
            *(
                [result["external_write"]["event_id"]]
                if isinstance(result.get("external_write"), dict)
                else []
            ),
        ],
    )
    return result


async def _task_lifecycle(
    body: LifecycleRequest, request: Request, session: Session
) -> dict[str, Any]:
    runtime = _runtime(request)
    event_types = {
        "complete": "task.completed",
        "defer": "task.deferred",
        "snooze": "task.snoozed",
        "block": "task.blocked",
        "unblock": "task.unblocked",
        "cancel": "task.cancelled",
        "reopen": "task.reopened",
    }
    event_type = event_types[body.action]
    append_request_id = f"{body.item_id}:{body.request_id}"
    request_fingerprint = _fingerprint(
        body.model_dump(mode="json", exclude={"provenance"})
    )
    prior_event_id = await _prior_append(
        runtime,
        session,
        event_type=event_type,
        request_id=append_request_id,
        request_fingerprint=request_fingerprint,
    )
    row = get_task(
        runtime.projections.connection("tasks"), session, task_id=body.item_id
    )
    if row is None or (
        row["assignee_party"] not in {None, session.auth_member_id}
        or (row["assignee_party"] is None and session.auth_role != "principal")
    ):
        raise HTTPException(404, "task not found")
    if prior_event_id is not None:
        return {
            "event_id": prior_event_id,
            "task": row,
            "duplicate": True,
            "what_now": core._what_now(runtime, session),
        }
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(
            409, {"code": "stale_write", "current": row["last_event_id"]}
        )
    now = EventEnvelope.now_utc_iso()
    payload: dict[str, Any]
    active_states = {"inbox", "next", "in_progress", "waiting_on", "deferred"}
    if body.action == "complete":
        if row["status"] not in active_states:
            raise HTTPException(409, "only active tasks can be completed")
        payload = {
            "task_id": body.item_id,
            "completed_by_member_id": session.auth_member_id,
            "completed_at": now,
            "note": body.note,
        }
    elif body.action in {"defer", "snooze"}:
        if row["status"] not in active_states:
            raise HTTPException(409, "only active tasks can be held")
        if body.action in {"defer", "snooze"} and not body.until:
            raise HTTPException(409, "until is required")
        try:
            activation = datetime.fromisoformat(
                cast(str, body.until).strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise HTTPException(
                422, "until must be a valid date or date and time"
            ) from exc
        if activation.tzinfo is None:
            try:
                activation = activation.replace(
                    tzinfo=ZoneInfo(runtime.settings.instance.timezone)
                )
            except ZoneInfoNotFoundError as exc:
                raise HTTPException(503, "instance timezone is unavailable") from exc
        prefix = "deferred" if body.action == "defer" else "snoozed"
        payload = {
            "task_id": body.item_id,
            f"{prefix}_at": now,
            f"{prefix}_until": activation.isoformat(),
            f"{prefix}_by_member_id": session.auth_member_id,
        }
        if body.action == "defer":
            payload["reason"] = body.reason
    elif body.action == "block":
        if row["status"] not in {"inbox", "next", "in_progress"}:
            raise HTTPException(409, "only executable tasks can be blocked")
        if not body.reason:
            raise HTTPException(409, "reason is required")
        payload = {
            "task_id": body.item_id,
            "blocked_at": now,
            "blocked_by_member_id": session.auth_member_id,
            "reason": body.reason,
        }
    elif body.action == "unblock":
        if row["status"] != "waiting_on":
            raise HTTPException(409, "task is not blocked")
        payload = {
            "task_id": body.item_id,
            "unblocked_at": now,
            "unblocked_by_member_id": session.auth_member_id,
            "note": body.note,
        }
    elif body.action == "cancel":
        if row["status"] not in active_states:
            raise HTTPException(409, "only active tasks can be abandoned")
        payload = {
            "task_id": body.item_id,
            "cancelled_at": now,
            "cancelled_by_member_id": session.auth_member_id,
            "reason": body.reason,
        }
    else:
        if row["status"] not in {"done", "cancelled", "dismissed"}:
            raise HTTPException(
                409, "only completed or abandoned tasks can be reopened"
            )
        payload = {
            "task_id": body.item_id,
            "reopened_at": now,
            "reopened_by_member_id": session.auth_member_id,
            "previous_status": row["status"],
        }
    await _guard(runtime, session, event_type, payload)
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type=event_type,
        source_account_id="tasks",
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=row["sensitivity"],
        payload=payload,
        request_id=append_request_id,
        causation_id=row["last_event_id"],
        request_fingerprint=request_fingerprint,
    )
    await runtime.wait_for_projection("tasks", event_id)
    updated = get_task(
        runtime.projections.connection("tasks"), session, task_id=body.item_id
    )
    return {
        "event_id": event_id,
        "task": updated,
        "duplicate": duplicate,
        "what_now": core._what_now(runtime, session),
    }


@router.post("/tools/lifecycle")
async def lifecycle(body: LifecycleRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    if session.auth_role == "child" and body.item_kind != "task":
        raise HTTPException(403, "child chat may update only the child's own tasks")
    if body.item_kind == "task":
        if body.action == "delegate":
            raise HTTPException(
                422, "task delegation is not a commitment lifecycle action"
            )
        result = await _task_lifecycle(body, request, session)
    else:
        result = await core.lifecycle(
            body.item_id,
            body.action,
            core.LifecycleRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                until=body.until,
                reason=body.reason,
                note=body.note,
                delegate_to_party_id=body.delegate_to_party_id,
            ),
            request,
            session,
        )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"tools.lifecycle.{body.item_kind}.{body.action}",
        result_event_ids=_result_event_ids(result),
    )
    return result


@router.post("/tools/correct")
async def correct(body: CorrectionRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    _require_adult(session)
    if body.item_kind == "commitment":
        unsupported = set(body.fields) - {"description", "due_at"}
        if unsupported:
            raise HTTPException(
                422, f"unsupported commitment fields: {sorted(unsupported)}"
            )
        result = await comms.edit_commitment(
            body.item_id,
            comms.EditRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                description=body.fields.get("description"),
                due_at=body.fields.get("due_at"),
            ),
            request,
            session,
        )
        await _record_tool_call(
            request,
            session,
            body.provenance,
            operation="tools.correct.commitment",
            result_event_ids=_result_event_ids(result),
        )
        return result
    allowed = {"title", "due_date", "energy", "effort", "notes"}
    if not body.fields or set(body.fields) - allowed:
        raise HTTPException(422, "task correction contains unsupported fields")
    runtime = _runtime(request)
    append_request_id = f"{body.item_id}:{body.request_id}"
    request_fingerprint = _fingerprint(
        body.model_dump(mode="json", exclude={"provenance"})
    )
    prior_event_id = await _prior_append(
        runtime,
        session,
        event_type="task.updated",
        request_id=append_request_id,
        request_fingerprint=request_fingerprint,
    )
    row = get_task(
        runtime.projections.connection("tasks"), session, task_id=body.item_id
    )
    if row is None or row["assignee_party"] != session.auth_member_id:
        raise HTTPException(404, "task not found")
    if prior_event_id is not None:
        result = {"event_id": prior_event_id, "duplicate": True, "task": row}
        await _record_tool_call(
            request,
            session,
            body.provenance,
            operation="tools.correct.task",
            result_event_ids=[prior_event_id],
        )
        return result
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(
            409, {"code": "stale_write", "current": row["last_event_id"]}
        )
    payload = {
        "task_id": body.item_id,
        "updated_at": EventEnvelope.now_utc_iso(),
        "updated_by_party_id": session.auth_member_id,
        "previous_status": row["status"],
        "new_status": None,
        "field_updates": body.fields,
    }
    await _guard(runtime, session, "task.updated", payload)
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="task.updated",
        source_account_id="tasks",
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=row["sensitivity"],
        payload=payload,
        request_id=append_request_id,
        causation_id=row["last_event_id"],
        request_fingerprint=request_fingerprint,
    )
    await runtime.wait_for_projection("tasks", event_id)
    result = {
        "event_id": event_id,
        "duplicate": duplicate,
        "task": get_task(
            runtime.projections.connection("tasks"), session, task_id=body.item_id
        ),
    }
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.correct.task",
        result_event_ids=[event_id],
    )
    return result


@router.post("/tools/skill")
async def invoke_skill(body: SkillRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    _require_adult(session)
    if body.skill_id not in PHASE3_SKILL_IDS:
        raise HTTPException(403, "skill is outside the Phase 3 allowlist")
    runtime = _runtime(request)
    result = await runtime.run_skill_fn(
        body.skill_id,
        body.inputs,
        SkillContext(
            session=session,
            triggering_event_id=body.triggering_event_id,
            observation_mode_active=await runtime.observation.is_active(),
        ),
        input_sensitivities=body.input_sensitivities,
    )
    response = {
        "output": result.output,
        "openclaw_invocation_id": result.openclaw_invocation_id,
        "provider": result.provider,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "duration_ms": result.duration_ms,
    }
    await _record_tool_call(
        request, session, body.provenance, operation=f"tools.skill.{body.skill_id}"
    )
    return response


@router.post("/bridge/events")
async def bridge_event(body: BridgeRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    _require_adult(session)
    if body.event_kind not in BRIDGE_EVENT_KINDS:
        raise HTTPException(422, "domain event is not allowlisted")
    payload = {
        "event_kind": body.event_kind,
        "content": body.content,
        "consent": True,
        "agent_id": body.provenance.agent_id,
        "session_key": body.provenance.session_key,
        "session_id": body.provenance.session_id,
        "channel_id": body.provenance.channel_id,
        "account_id": body.provenance.account_id,
        "message_id": body.provenance.message_id,
        "thread_id": body.provenance.thread_id,
        "correlation_id": body.provenance.correlation_id,
    }
    event_id, _ = await _append(
        _runtime(request),
        session,
        event_type="openclaw.domain_event.observed",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity=body.sensitivity,
        payload=payload,
        request_id=(
            f"{body.provenance.channel_id}:{body.provenance.account_id or '-'}:"
            f"{body.provenance.message_id or body.provenance.correlation_id}"
        ),
        request_fingerprint=_fingerprint(
            body.model_dump(mode="json", exclude={"provenance"})
        ),
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"bridge.{body.event_kind}",
        result_event_ids=[event_id],
    )
    return {"accepted": True, "event_id": event_id}


@router.post("/notifications/result")
async def notification_result(
    body: NotificationResultRequest, request: Request
) -> dict[str, Any]:
    """Accept a delivery receipt only for this member's routed decision."""

    session = _session(request, body.provenance)
    _require_adult(session)
    runtime = _runtime(request)
    matched: dict[str, Any] | None = None
    async for event in runtime.event_log.read_since(
        types=["notification.decision.made"]
    ):
        payload = event["payload"]
        if (
            payload.get("decision_id") == body.decision_id
            and payload.get("signal_id") == body.signal_id
        ):
            matched = event
    if matched is None or matched["owner_scope"] != f"private:{session.auth_member_id}":
        raise HTTPException(404, "notification decision not found")
    if matched["payload"].get("decision") != "route":
        raise HTTPException(409, "notification decision was not routed")
    payload = {
        "attempt_id": body.attempt_id,
        "decision_id": body.decision_id,
        "signal_id": body.signal_id,
        "outcome": body.outcome,
        "route": "openclaw",
        "attempted_at": body.attempted_at,
        "provider_receipt_id": body.provider_receipt_id,
        "error_code": body.error_code,
        "next_retry_at": body.next_retry_at,
    }
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="notification.delivery.result",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=matched["owner_scope"],
        visibility_scope=matched["visibility_scope"],
        sensitivity=matched["sensitivity"],
        payload=payload,
        request_id=body.attempt_id,
        causation_id=matched["event_id"],
        request_fingerprint=_fingerprint(
            body.model_dump(mode="json", exclude={"provenance"})
        ),
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="notifications.delivery-result",
        result_event_ids=[event_id],
    )
    return {"accepted": True, "event_id": event_id, "duplicate": duplicate}


@router.post("/model-calls")
async def model_call(body: ModelCallRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    _require_adult(session)
    if not (body.provenance.session_key or "").startswith("adminme:"):
        return {"accepted": False, "reason": "not_an_administrateme_session"}
    event_id, _ = await _append(
        _runtime(request),
        session,
        event_type="openclaw.model_call.recorded",
        source_account_id="model-provenance",
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        payload={
            "phase": body.phase,
            "run_id": body.run_id,
            "provider": body.provider,
            "model": body.model,
            "agent_id": body.provenance.agent_id,
            "session_key": body.provenance.session_key,
            "channel_id": body.provenance.channel_id,
        },
        request_id=f"{body.run_id}:{body.phase}",
    )
    return {"accepted": True, "event_id": event_id}


@router.post("/standing-orders/trigger")
async def standing_order(
    body: StandingOrderRequest, request: Request
) -> dict[str, Any]:
    session = _session(request, body.provenance)
    _require_adult(session)
    runtime = _runtime(request)
    if body.provenance.call_kind != "standing_order":
        raise HTTPException(422, "standing-order provenance required")
    if body.program_id not in runtime.standing_orders:
        raise HTTPException(404, "standing order is not registered")
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="openclaw.standing_order.triggered",
        source_account_id="automations",
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        payload={
            "program_id": body.program_id,
            "automation_id": body.automation_id,
            "trigger_id": body.trigger_id,
            "triggered_at": EventEnvelope.now_utc_iso(),
            "agent_id": body.provenance.agent_id,
            "session_key": body.provenance.session_key,
        },
        request_id=f"{body.program_id}:{body.automation_id}:{body.trigger_id}",
        request_fingerprint=_fingerprint(
            body.model_dump(mode="json", exclude={"provenance"})
        ),
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"standing-order.{body.program_id}",
        result_event_ids=[event_id],
    )
    return {"accepted": True, "event_id": event_id, "duplicate": duplicate}


@router.post("/dashboard/today")
async def dashboard_today(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = _runtime(request)
    surface = build_surface(runtime, session, "today")
    events: list[dict[str, Any]] = []
    async for event in runtime.event_log.read_since(limit=250):
        events.append(event)
    visible = filter_rows(session, events)[-20:]
    result = {
        # Preserve the pinned dashboard's established What Now key while
        # publishing the same complete deterministic Day Brief as the portal.
        "today": surface["what_now"],
        "brief": surface["brief"],
        "activity": [
            {
                "event_id": event["event_id"],
                "type": event["type"],
                "occurred_at": event["occurred_at"],
            }
            for event in visible
        ],
    }
    await _record_tool_call(
        request, session, body.provenance, operation="dashboard.today"
    )
    return result


@router.post("/dashboard/triage")
async def dashboard_triage(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    _require_adult(session)
    result = await core.triage(request, session)
    await _record_tool_call(
        request, session, body.provenance, operation="dashboard.triage"
    )
    return result


@router.post("/dashboard/inbox", deprecated=True)
async def dashboard_inbox(body: ContextRequest, request: Request) -> dict[str, Any]:
    """Deprecated compatibility alias for the pinned pre-D26 plugin."""
    result = await dashboard_triage(body, request)
    return {
        **result,
        "deprecated_alias": "inbox",
        "canonical_path": "/api/openclaw/v1/dashboard/triage",
    }


@router.post("/dashboard/health")
async def dashboard_health(body: ContextRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    _require_adult(session)
    runtime = _runtime(request)
    result = {
        "openclaw_state": runtime.effective_openclaw_state(),
        "compatibility": {
            "version": OPENCLAW_VERSION,
            "commit": OPENCLAW_COMMIT,
        },
        "runtime": await runtime.diagnostics(),
    }
    await _record_tool_call(
        request, session, body.provenance, operation="dashboard.health"
    )
    return result


@router.post("/dashboard/knowledge", deprecated=True)
async def dashboard_knowledge(body: ContextRequest, request: Request) -> dict[str, Any]:
    """Deprecated compatibility alias for the pre-CO-002 Notes binding."""

    session = _session(request, body.provenance)
    _require_adult(session)
    result = build_surface(_runtime(request), session, "notes")
    await _record_tool_call(
        request, session, body.provenance, operation="dashboard.notes"
    )
    return {
        **result,
        "deprecated_alias": "knowledge",
        "canonical_path": "/api/openclaw/v1/dashboard/notes",
    }


@router.post("/dashboard/{surface}")
async def dashboard_surface(
    surface: Literal[
        "comms",
        "reminders",
        "people",
        "notes",
        "finance",
        "calendar",
        "scoreboard",
        "settings",
    ],
    body: ContextRequest,
    request: Request,
) -> dict[str, Any]:
    """Bind the remaining stable domain views into the pinned Control UI."""

    session = _session(request, body.provenance)
    _require_adult(session)
    result = build_surface(_runtime(request), session, surface)
    await _record_tool_call(
        request, session, body.provenance, operation=f"dashboard.{surface}"
    )
    return result


__all__ = ["router"]
