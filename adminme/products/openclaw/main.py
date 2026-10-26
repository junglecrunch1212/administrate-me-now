"""Authenticated loopback transport for the one native OpenClaw plugin.

OpenClaw authenticates the transport; AdministrateMe independently maps the
OpenClaw actor/channel/account tuple to a household member.  A gateway bearer
token, operator scope, or ``senderIsOwner`` value never authorizes household
data access.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.capability_catalog import catalog_item, compose_member_catalog
from adminme.cos_compiled_context import compile_effective_cos_context
from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.log import IdempotencyConflict
from adminme.delegated_execution import (
    ACTION_SPECS,
    EXECUTION_FACT_MAX_AGE_SECONDS,
    EXECUTION_POLICY_VERSION,
    HARD_CEILING_RISKS,
    ActionClass,
    ExecutionMode,
    RecurrenceStrategy,
    RepresentationKind,
    RiskClass,
    action_fingerprint,
    desired_state_for,
    execution_policy_digest,
)
from adminme.lib.scope import filter_rows
from adminme.lib.session import Session, Source
from adminme.lib.skill_runner.wrapper import SkillContext
from adminme.openclaw_approvals import ApprovalReceiptError
from adminme.messages_reply import (
    REPLY_CONTRACT,
    approval_is_current,
    canonical_sha256,
    participant_ids,
)
from adminme.google_tasks_execution import (
    ACTION_CLASS as B03_ACTION_CLASS,
    ADAPTER_IDENTITY as B03_ADAPTER_IDENTITY,
    DELEGATION_PERMISSION as B03_PERMISSION,
    EXECUTION_POLICY_VERSION as B03_POLICY_VERSION,
    OPERATION as B03_OPERATION,
    PROVIDER as B03_PROVIDER,
    ExecutionCoordinate as B03ExecutionCoordinate,
    canonical_task_content as b03_canonical_task_content,
    public_envelope_state as b03_public_envelope_state,
    task_content_fingerprint as b03_task_content_fingerprint,
)
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
    get_google_tasks_execution,
    get_delegated_action,
    get_task,
    list_coordination_records,
    matching_execution_envelopes,
    service_work_for_context,
)
from adminme.projections.commitments.queries import get_commitment
from adminme.projections.calendars.queries import get_calendar_event
from adminme.projections.lists.queries import get_item as get_list_item
from adminme.projections.interactions.queries import get_interaction, participants_for
from adminme.projections.context_fabric.queries import (
    source_policy_proposals_for_member,
    source_policy_rules_for_member,
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
    cos_message_channel_for_account,
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


class CapabilityToolRequest(ContextRequest):
    capability_id: str | None = Field(default=None, min_length=1, max_length=240)


CalendarView = Literal["day", "week", "agenda", "month"]


class CalendarReadRequest(ContextRequest):
    anchor_date: date | None = None
    view: CalendarView = "week"
    context_scope: Literal["member_private", "household_shared"] = "member_private"
    calendar_external_ids: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=100, ge=1, le=200)

    @model_validator(mode="after")
    def bounded_calendar_ids(self) -> CalendarReadRequest:
        if any(not value or len(value) > 300 for value in self.calendar_external_ids):
            raise ValueError("calendar identifiers must be nonempty and bounded")
        if len(set(self.calendar_external_ids)) != len(self.calendar_external_ids):
            raise ValueError("calendar identifiers must be unique")
        return self


class CapabilityChangeRequest(ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    expected_revision: int = Field(ge=0)
    capability_id: str = Field(min_length=1, max_length=240)
    action: Literal["request", "cancel"]
    requested_scope: Literal["member_private", "household_shared"] = "member_private"
    provider_option: str | None = Field(default=None, min_length=1, max_length=200)


class PolicyProposalToolRequest(ContextRequest):
    action: Literal["inspect", "decide"]
    policy_proposal_id: str | None = Field(default=None, min_length=1, max_length=240)
    request_id: str | None = Field(default=None, min_length=8, max_length=120)
    decision: Literal["approve", "edit", "trial", "reject", "suppress"] | None = None
    instruction: Literal["always", "usually", "ask", "never"] | None = None
    preview_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    trial_proposal_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_action_shape(self) -> "PolicyProposalToolRequest":
        decision_fields = (
            self.request_id,
            self.decision,
            self.instruction,
            self.preview_digest,
        )
        if self.action == "inspect":
            if (
                any(value is not None for value in decision_fields)
                or self.trial_proposal_ids
            ):
                raise ValueError("inspect cannot include decision fields")
            return self
        if not self.policy_proposal_id or any(
            value is None for value in decision_fields
        ):
            raise ValueError(
                "decide requires the proposal, request, decision, and preview"
            )
        return self


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


class RequestApprovalResolutionRequest(ApprovalResolutionRequest):
    decision: Literal["authenticated_request"]  # type: ignore[assignment]
    authorization: dict[str, str]


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


class ForwardForReviewRequest(ContextRequest):
    """Explicit channel submission; model-selected ordinary chat is excluded."""

    request_id: str = Field(min_length=8, max_length=120)
    text: str = Field(min_length=1, max_length=32_000)
    consent: Literal[True]
    submission_gesture: Literal["explicit", "dedicated_intake"] = "explicit"
    unsupported_attachment_count: int = Field(default=0, ge=0, le=1000)
    sensitivity: Literal["normal", "sensitive"] = "normal"


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
    request_authorization_event_id: str | None = Field(
        default=None, min_length=1, max_length=240
    )


class DelegatedActionPrepareRequest(ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    context_scope: Literal["member_private", "household_shared"] = "member_private"
    action_class: ActionClass
    canonical_object_kind: str = Field(min_length=1, max_length=80)
    canonical_object_id: str = Field(min_length=1, max_length=240)
    occurrence_id: str | None = Field(default=None, min_length=1, max_length=240)
    series_id: str | None = Field(default=None, min_length=1, max_length=240)
    recurrence_strategy: RecurrenceStrategy = "none"
    coordination_id: str | None = Field(default=None, min_length=1, max_length=240)
    desired_representation_id: str = Field(min_length=1, max_length=240)
    operator_kind: Literal["openclaw_automation", "simulated_human"] = (
        "openclaw_automation"
    )
    provider_account_id: str = Field(min_length=1, max_length=500)
    provider_account_owner_principal_id: str = Field(min_length=1, max_length=200)
    destination_id: str = Field(min_length=1, max_length=500)
    audience_ids: list[str] = Field(min_length=1, max_length=20)
    representation_kind: RepresentationKind = "member_delegated"
    target_external_id: str | None = Field(default=None, max_length=500)
    expected_provider_revision: str | None = Field(default=None, max_length=500)
    desired_payload: dict[str, Any]
    fact_event_ids: list[str] = Field(min_length=1, max_length=100)
    facts_observed_at: AwareDatetime
    facts_valid_until: AwareDatetime
    risk_class: RiskClass = "ordinary"
    execution_mode: ExecutionMode = "execute"
    expires_at: AwareDatetime
    expected_policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_recurrence(self) -> "DelegatedActionPrepareRequest":
        if len(self.audience_ids) != len(set(self.audience_ids)):
            raise ValueError("delegated action audience must be unique")
        if len(self.fact_event_ids) != len(set(self.fact_event_ids)):
            raise ValueError("delegated action fact lineage must be unique")
        if self.recurrence_strategy == "provider_series" and not self.series_id:
            raise ValueError("stable recurrence requires an exact series")
        if (
            self.recurrence_strategy == "individual_occurrence"
            and not self.occurrence_id
        ):
            raise ValueError("variable recurrence requires an exact occurrence")
        if self.recurrence_strategy == "none" and (
            self.series_id or self.occurrence_id
        ):
            raise ValueError("non-recurring action cannot carry recurrence identity")
        if self.facts_valid_until <= self.facts_observed_at:
            raise ValueError("fact validity must follow observation")
        return self


class DelegatedActionExecuteRequest(ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    context_scope: Literal["member_private", "household_shared"] = "member_private"
    action_id: str = Field(min_length=1, max_length=240)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    expected_action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_kind: Literal["openclaw_automation", "simulated_human"] = (
        "openclaw_automation"
    )


class DelegatedActionObserveRequest(ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    context_scope: Literal["member_private", "household_shared"] = "member_private"
    action_id: str = Field(min_length=1, max_length=240)
    expected_last_event_id: str = Field(min_length=1, max_length=240)


class GoogleTasksCreatePrepareRequest(ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    context_scope: Literal["member_private", "household_shared"] = "member_private"
    command_id: str = Field(min_length=1, max_length=240)
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    expected_approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_content: dict[str, Any]


class GoogleTasksCreateExecuteRequest(ContextRequest):
    request_id: str = Field(min_length=8, max_length=120)
    context_scope: Literal["member_private", "household_shared"] = "member_private"
    command_id: str = Field(min_length=1, max_length=240)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    expected_approval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoogleTasksCreateStateRequest(ContextRequest):
    context_scope: Literal["member_private", "household_shared"] = "member_private"
    command_id: str = Field(min_length=1, max_length=240)


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
    if member_id is None and provenance.channel_id == "gmail":
        from adminme.conversation_instructions import gmail_member

        member_id = gmail_member(runtime, provenance)
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
    request_authorization: dict[str, str] | None = None,
) -> str:
    """Consume the exact independently evaluated receipt and append its private audit fact."""

    receipt_id = body.openclaw_approval_receipt_id
    if not receipt_id:
        raise HTTPException(403, "independent OpenClaw approval is required")
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
        pending_receipt = runtime.openclaw_approval_store.get(receipt_id)
        if pending_receipt.authorization != request_authorization:
            raise ApprovalReceiptError("approval policy binding is mismatched")
        receipt = runtime.openclaw_approval_store.consume(
            receipt_id=receipt_id,
            tool_name=tool_name,
            request_fingerprint=fingerprint,
            member_id=session.auth_member_id,
            allow_same_binding_recovery=True,
        )
    except ApprovalReceiptError as exc:
        raise HTTPException(403, str(exc)) from exc
    if receipt.consumed_at_ms is None:
        raise HTTPException(503, "OpenClaw approval consumption is unavailable")
    # Preserve the store's exact first consumption, including same-binding
    # recovery. A new whole-second timestamp can precede millisecond issuance
    # or misrepresent an approval recovered after its original consumption.
    consumed_at = datetime.fromtimestamp(
        receipt.consumed_at_ms / 1000, tz=timezone.utc
    ).isoformat()
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
            "approval_method": "authenticated_request"
            if request_authorization
            else "allow_once",
            **(request_authorization or {}),
            "openclaw_version": receipt.openclaw_version,
            "openclaw_commit": receipt.openclaw_commit,
            "provenance": provenance.audit_payload(member_id=session.auth_member_id),
        },
        request_id=receipt.receipt_id,
        causation_id=causation_id,
        request_fingerprint=receipt.request_fingerprint,
        schema_version=2 if request_authorization else 1,
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


@router.post("/approvals/authenticated-request")
async def resolve_request_approval(
    body: RequestApprovalResolutionRequest, request: Request
) -> dict[str, Any]:
    _authenticate(request)
    runtime = _runtime(request)
    if (
        runtime.openclaw_approval_store is None
        or not runtime.settings.openclaw_shared_secret
    ):
        raise HTTPException(503, "OpenClaw approval storage is unavailable")
    try:
        receipt = runtime.openclaw_approval_store.resolve(
            secret=runtime.settings.openclaw_shared_secret,
            **body.model_dump(exclude={"decision"}),
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


@router.post("/tools/capabilities")
async def inspect_capabilities(
    body: CapabilityToolRequest, request: Request
) -> dict[str, Any]:
    """Return the reviewed catalog and member-effective state without secrets."""

    session = _session(request, body.provenance)
    _require_adult(session)
    runtime = _runtime(request)
    result = compose_member_catalog(
        runtime, session, settings_state=runtime.settings_control.read()
    )
    if body.capability_id is not None:
        item = catalog_item(result, body.capability_id)
        if item is None:
            raise HTTPException(404, "capability is not in the reviewed catalog")
        result = {
            "contract": result["contract"],
            "catalog_version": result["catalog_version"],
            "item": item,
            "provider_effect_count": 0,
        }
    await _record_tool_call(
        request, session, body.provenance, operation="tools.capabilities"
    )
    return result


_CALENDAR_READ_EVENT_FIELDS = frozenset(
    {
        "calendar_event_id",
        "calendar_source",
        "provider_account_id",
        "calendar_external_id",
        "calendar_name",
        "external_uid",
        "provider_revision",
        "summary",
        "title",
        "kind",
        "start_at",
        "end_at",
        "all_day",
        "start_time_zone",
        "end_time_zone",
        "status",
        "availability",
        "provider_updated_at",
        "owner_scope",
        "visibility_scope",
        "sensitivity",
        "last_event_id",
        "owner_hint",
    }
)
_CALENDAR_READ_SOURCE_FIELDS = frozenset(
    {
        "provider",
        "provider_account_id",
        "calendar_external_id",
        "calendar_name",
        "member_id",
        "access_role",
        "read_details",
        "read_free_busy",
        "observed_at",
    }
)


async def _read_calendar(
    runtime: Any,
    session: Session,
    *,
    anchor_date: date | None,
    view: CalendarView,
    calendar_external_ids: list[str],
    limit: int,
) -> dict[str, Any]:
    # Reuse the dashboard's date/DST handling, member matching and privacy
    # filters. A calendar imported into this account retains its actual source
    # identity; its title never changes the authenticated member.
    surface = build_surface(
        runtime, session, "calendar", calendar_anchor=anchor_date, calendar_view=view
    )
    cockpit = surface["cockpit"]
    rows = surface["events"]
    sources = cockpit["sources"]
    if calendar_external_ids:
        selected = set(calendar_external_ids)
        rows = [row for row in rows if row.get("calendar_external_id") in selected]
        sources = [
            row for row in sources if row.get("calendar_external_id") in selected
        ]
    events = []
    for row in rows[:limit]:
        item = {
            key: value
            for key, value in row.items()
            if key in _CALENDAR_READ_EVENT_FIELDS
        }
        # Provider descriptions, attachments, links and arbitrary source payloads
        # are not part of this calendar read contract.
        for key in ("summary", "title", "calendar_name"):
            if isinstance(item.get(key), str):
                value = item[key]
                item[key] = value[:500]
                if len(value) > 500:
                    item["text_truncated"] = True
        original = await runtime.event_log.get(row["last_event_id"])
        item["source_event_id"] = row["last_event_id"]
        item["retained_change_at"] = original["recorded_at"] if original else None
        events.append(item)
    status = (await runtime.projections.status(include_row_counts=False))["calendars"]
    observed = [
        item["retained_change_at"] for item in events if item["retained_change_at"]
    ]
    return {
        "contract": "administrateme-member-calendar-read-v1",
        "member_id": session.auth_member_id,
        "context_scope": session.service_context.context_scope
        if session.service_context
        else None,
        "timezone": cockpit["timezone"],
        "range": cockpit["range"],
        "view": view,
        "calendar_external_ids": calendar_external_ids,
        "events": events,
        "sources": [
            {
                key: value
                for key, value in row.items()
                if key in _CALENDAR_READ_SOURCE_FIELDS
            }
            for row in sources
        ],
        "matching_event_count": len(rows),
        "truncated": len(rows) > limit,
        "coverage": {
            "basis": "current_scope_filtered_calendar_projection",
            "provider_live_read": False,
            "provider_range_completeness": "not_verified",
            "provider_last_successful_poll_at": None,
            "latest_returned_retained_change_at": max(observed, default=None),
            "source_observed_at_means": "source_metadata_change_not_last_poll",
            "projection_checkpoint_event_id": status["checkpoint"],
            "projection_lag_count": status["lag_count"],
            "projection_state": "degraded"
            if status["degraded"]
            else "processing"
            if status["lag_count"]
            else "caught_up",
            "empty_result_means": "no_matching_retained_events_not_proven_free_time",
        },
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "provider_effect_count": 0,
    }


@router.post("/tools/calendar")
async def inspect_calendar(
    body: CalendarReadRequest, request: Request
) -> dict[str, Any]:
    session = _session(request, body.provenance, context_scope=body.context_scope)
    _require_adult(session)
    result = await _read_calendar(
        _runtime(request),
        session,
        anchor_date=body.anchor_date,
        view=body.view,
        calendar_external_ids=body.calendar_external_ids,
        limit=body.limit,
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.calendar",
        result_event_ids=[item["source_event_id"] for item in result["events"]],
    )
    return result


@router.post("/tools/settings-context")
async def inspect_settings_context(
    body: ContextRequest, request: Request
) -> dict[str, Any]:
    """Compile minimum effective CoS context from typed authoritative state."""

    session = _session(request, body.provenance)
    _require_adult(session)
    result = compile_effective_cos_context(_runtime(request), session)
    await _record_tool_call(
        request, session, body.provenance, operation="tools.settings-context"
    )
    return result


@router.post("/tools/capability-request", status_code=202)
async def change_capability_request(
    body: CapabilityChangeRequest, request: Request
) -> dict[str, Any]:
    """Record an inert, secret-free capability request or cancellation."""

    session = _session(request, body.provenance)
    _require_adult(session)
    runtime = _runtime(request)
    state = runtime.settings_control.read()
    catalog = compose_member_catalog(runtime, session, settings_state=state)
    capability = catalog_item(catalog, body.capability_id)
    if capability is None:
        raise HTTPException(404, "capability is not in the reviewed catalog")
    provider_options = set(capability.get("provider_options") or [])
    provider_option = body.provider_option
    if provider_option is not None and provider_option not in provider_options:
        raise HTTPException(422, "provider option is not reviewed for this capability")
    if provider_option is None and len(provider_options) == 1:
        provider_option = next(iter(provider_options))
    event_type = (
        "settings.capability_requested"
        if body.action == "request"
        else "settings.capability_request_cancelled"
    )
    request_values = body.model_dump(
        mode="json",
        exclude={"provenance", "openclaw_approval_receipt_id"},
        exclude_unset=True,
    )
    request_fingerprint = _fingerprint(request_values)
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        try:
            await runtime.settings_control.replay_capability_request_events(
                runtime.event_log
            )
        except (OSError, RuntimeError) as exc:
            raise HTTPException(
                503, "capability requests could not be replayed"
            ) from exc
        prior_event_id = await _prior_append(
            runtime,
            session,
            event_type=event_type,
            request_id=body.request_id,
            request_fingerprint=request_fingerprint,
        )
        if prior_event_id is not None:
            result = {
                "accepted": True,
                "event_id": prior_event_id,
                "idempotent": True,
                "provider_effect_count": 0,
                "operational_effects": False,
            }
        else:
            rows = {
                value["capability_id"]: value
                for value in runtime.settings_control.capability_requests_for(
                    session.auth_member_id
                )
            }
            current = rows.get(body.capability_id)
            if (
                body.action == "request"
                and current is not None
                and current.get("state") == "requested"
            ):
                raise HTTPException(409, "capability is already requested")
            if body.action == "cancel" and (
                current is None or current.get("state") != "requested"
            ):
                raise HTTPException(409, "capability request is not active")
            try:
                revision = runtime.settings_control.prepare_capability_request_change(
                    session.auth_member_id,
                    expected_revision=body.expected_revision,
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            approval_event_id = await _consume_openclaw_approval(
                request,
                session,
                body.provenance,
                body,
                tool_name="administrateme_capability_request",
                action=f"settings.capability.{body.action}",
            )
            payload = {
                "member_id": session.auth_member_id,
                "capability_id": body.capability_id,
                "catalog_version": catalog["catalog_version"],
                "desired_outcome": capability["outcome"],
                "requested_scope": body.requested_scope,
                "provider_option": provider_option,
                "state": "requested" if body.action == "request" else "cancelled",
                "revision": revision,
                "changed_by_member_id": session.auth_member_id,
                "changed_at": EventEnvelope.now_utc_iso(),
                "installs_software": False,
                "pairs_bridge": False,
                "enrolls_account": False,
                "changes_openclaw_configuration": False,
                "accesses_credentials": False,
                "grants_authority": False,
                "provider_effects": False,
            }
            await _guard(runtime, session, event_type, payload)
            event_id, duplicate = await _append(
                runtime,
                session,
                event_type=event_type,
                source_account_id=body.provenance.account_id
                or body.provenance.channel_id,
                owner_scope=f"private:{session.auth_member_id}",
                visibility_scope=f"private:{session.auth_member_id}",
                payload=payload,
                request_id=body.request_id,
                causation_id=approval_event_id,
                request_fingerprint=request_fingerprint,
            )
            runtime.settings_control.apply_capability_request_event(payload)
            result = {
                "accepted": True,
                "event_id": event_id,
                "idempotent": duplicate,
                "request": payload,
                "provider_effect_count": 0,
                "operational_effects": False,
            }
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"tools.capability-request.{body.action}",
        result_event_ids=[str(result["event_id"])],
    )
    return result


@router.post("/tools/policy-proposal")
async def policy_proposal(
    body: PolicyProposalToolRequest, request: Request
) -> dict[str, Any]:
    """Inspect or decide member-owned Triage learning proposals."""

    session = _session(request, body.provenance)
    _require_adult(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "context policy projection unavailable")
    connection = runtime.projections.connection("context_fabric")
    if body.action == "inspect":
        proposals = source_policy_proposals_for_member(
            connection, session, member_id=session.auth_member_id
        )
        if body.policy_proposal_id is not None:
            proposals = [
                value
                for value in proposals
                if value["policy_proposal_id"] == body.policy_proposal_id
            ]
            if not proposals:
                raise HTTPException(404, "source policy proposal not found")
        result = {
            "proposals": proposals,
            "rules": source_policy_rules_for_member(
                connection, session, member_id=session.auth_member_id
            ),
            "provider_effect_count": 0,
        }
    else:
        await _consume_openclaw_approval(
            request,
            session,
            body.provenance,
            body,
            tool_name="administrateme_policy_proposal",
            action="triage.source_policy_rule.decided",
        )
        from adminme.products.member import main as member_portal

        result = await member_portal.decide_triage_learning_rule(
            cast(str, body.policy_proposal_id),
            member_portal.TriageLearningDecisionRequest(
                request_id=cast(str, body.request_id),
                decision=cast(Any, body.decision),
                instruction=cast(Any, body.instruction),
                preview_digest=cast(str, body.preview_digest),
                trial_proposal_ids=body.trial_proposal_ids,
            ),
            request,
            session,
        )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation=f"tools.policy-proposal.{body.action}",
        result_event_ids=_result_event_ids(result),
    )
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


_CALENDAR_PAYLOAD_FIELDS = frozenset(
    {
        "summary",
        "start",
        "end",
        "timezone",
        "location",
        "description",
        "recurrence",
        "destination_calendar_id",
        "send_updates",
    }
)
_TASK_PAYLOAD_FIELDS = frozenset(
    {
        "title",
        "notes",
        "due",
        "status",
        "completed",
        "tasklist_id",
        "parent",
        "previous",
        "destinationTasklist",
        "destination_list_external_id",
    }
)


def _guard_delegated_payload(provider_family: str, payload: dict[str, Any]) -> str:
    allowed = (
        _CALENDAR_PAYLOAD_FIELDS
        if provider_family in {"google_calendar", "apple_calendar"}
        else _TASK_PAYLOAD_FIELDS
        if provider_family in {"google_tasks", "apple_reminders"}
        else frozenset({"subject", "body", "recipient"})
    )
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(
            422, f"desired provider fields are not allowlisted: {sorted(unknown)}"
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > 16_384:
        raise HTTPException(422, "desired provider representation is too large")
    return hashlib.sha256(encoded.encode()).hexdigest()


def _require_fixture_coordinates(
    session: Session, body: DelegatedActionPrepareRequest
) -> None:
    member_id = session.auth_member_id
    if body.representation_kind == "cos_operational":
        if (
            body.provider_account_owner_principal_id != COS_SERVICE_PRINCIPAL_ID
            or body.provider_account_id != "fixture-account:service:chief-of-staff"
            or not body.destination_id.startswith("fixture:service:chief-of-staff:")
        ):
            raise HTTPException(403, "CoS operational destination is not service-owned")
    elif (
        body.provider_account_owner_principal_id != member_id
        or body.provider_account_id != f"fixture-account:member:{member_id}"
        or not body.destination_id.startswith(f"fixture:member:{member_id}:")
    ):
        raise HTTPException(403, "member fixture destination is not self-owned")
    if sorted(body.audience_ids) != [member_id]:
        raise HTTPException(
            403, "member-private delegated action has one exact audience"
        )


def _canonical_execution_object(
    runtime: Any,
    session: Session,
    *,
    kind: str,
    object_id: str,
) -> dict[str, Any] | None:
    if runtime.projections is None:
        return None
    if kind == "task":
        return get_task(
            runtime.projections.connection("tasks"), session, task_id=object_id
        )
    if kind == "commitment":
        return get_commitment(
            runtime.projections.connection("commitments"),
            session,
            commitment_id=object_id,
        )
    if kind == "calendar_event":
        return get_calendar_event(
            runtime.projections.connection("calendars"),
            session,
            calendar_event_id=object_id,
        )
    if kind == "coordination":
        from adminme.projections.tasks.queries import get_coordination_record

        return get_coordination_record(
            runtime.projections.connection("tasks"),
            session,
            coordination_id=object_id,
            as_of_iso=EventEnvelope.now_utc_iso(),
        )
    if kind == "list_item" and "::" in object_id:
        list_id, item_id = object_id.split("::", 1)
        return get_list_item(
            runtime.projections.connection("lists"),
            session,
            list_external_id=list_id,
            item_external_id=item_id,
        )
    return None


async def _execution_fact_events(
    runtime: Any,
    session: Session,
    event_ids: list[str],
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for event_id in event_ids:
        event = await runtime.event_log.get(event_id)
        if (
            event is None
            or event.get("tenant_id") != session.tenant_id
            or event.get("visibility_scope") not in session.allowed_scopes
        ):
            raise HTTPException(403, "delegated action fact lineage is unavailable")
        facts.append(event)
    return facts


def _operator_id(kind: str) -> str:
    return (
        COS_AUTOMATION_OPERATOR_ID
        if kind == "openclaw_automation"
        else "operator:fixture:individual-human"
    )


def _require_current_b03_agreement(
    runtime: Any, session: Session, action: dict[str, Any]
) -> None:
    agreements = effective_operating_agreements(
        runtime.projections.connection("parties"),
        session,
        as_of_iso=EventEnvelope.now_utc_iso(),
    )
    matches = []
    for agreement in agreements:
        if agreement.get("agreement_id") != action.get("operating_agreement_id"):
            continue
        rule = agreement.get("rule") or {}
        trust = rule.get("trust_scope") or {}
        ceilings = rule.get("ceilings") or {}
        if (
            agreement.get("authority_scope") == action.get("context_scope")
            and rule.get("represented_service_principal_id") == COS_SERVICE_PRINCIPAL_ID
            and B03_PROVIDER in set(rule.get("provider_ids") or [])
            and action.get("provider_account")
            in set(rule.get("provider_account_references") or [])
            and action.get("destination_task_list")
            in set(rule.get("destination_ids") or [])
            and B03_ACTION_CLASS in set(trust.get("capability_ids") or [])
            and B03_OPERATION in set(trust.get("action_verbs") or [])
            and trust.get("stage") == "supervised_trial"
            and rule.get("independent_openclaw_approval_required") is True
            and ceilings.get("maximum_frequency_count") == 1
            and ceilings.get("frequency_period") == "occurrence"
        ):
            matches.append(agreement)
    if len(matches) != 1:
        raise HTTPException(
            403, "exact B03 operating agreement is missing or ineffective"
        )


def _b03_binding_matches(runtime: Any, coordinate: B03ExecutionCoordinate) -> list[Any]:
    return [
        binding
        for binding in runtime.settings.provider_bindings
        if binding.b03_dispatch_coordinate == coordinate.dispatch_coordinate
    ]


@router.post("/tools/google-tasks-create/prepare", status_code=201)
async def prepare_google_tasks_create(
    body: GoogleTasksCreatePrepareRequest, request: Request
) -> dict[str, Any]:
    """Prepare the one production-v2 action from member-controlled approval."""

    session = _session(
        request,
        body.provenance,
        required_permission=cast(DelegationPermission, B03_PERMISSION),
        context_scope=body.context_scope,
    )
    _require_adult(session)
    runtime = _runtime(request)
    context = session.service_context
    if context is None or runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "B03 production execution is unavailable")
    approval_event = await runtime.event_log.get(body.domain_approval_event_id)
    if (
        approval_event is None
        or approval_event.get("tenant_id") != session.tenant_id
        or approval_event.get("type") != "cos.google_tasks_execution.domain_approved"
        or int(approval_event.get("schema_version") or 0) != 2
        or approval_event.get("visibility_scope") not in session.allowed_scopes
    ):
        raise HTTPException(403, "exact AdministrateMe domain approval is unavailable")
    p = dict(approval_event["payload"])
    desired = b03_canonical_task_content(body.task_content)
    desired_fingerprint = b03_task_content_fingerprint(desired)
    if (
        p.get("command_id") != body.command_id
        or p.get("approval_fingerprint") != body.expected_approval_fingerprint
        or p.get("approved_task_content_fingerprint") != desired_fingerprint
        or p.get("desired_task_content") != desired
        or p.get("active_context") != body.context_scope
        or p.get("authority_grant_id") != context.authority_grant_id
        or p.get("represented_cos") != COS_SERVICE_PRINCIPAL_ID
        or p.get("actual_operator") != COS_AUTOMATION_OPERATOR_ID
        or p.get("provider_owner") != COS_SERVICE_PRINCIPAL_ID
        or p.get("provider") != B03_PROVIDER
        or p.get("adapter_identity") != B03_ADAPTER_IDENTITY
        or p.get("maximum_effect_count") != 1
        or p.get("used") is not False
        or p.get("revoked") is not False
    ):
        raise HTTPException(403, "B03 domain approval coordinates do not match")
    if body.context_scope == "member_private" and (
        p.get("on_behalf_of") != session.auth_member_id
        or p.get("resource_owner") != session.auth_member_id
        or context.context_member_id != session.auth_member_id
        or context.resource_owner_principal_id != session.auth_member_id
    ):
        raise HTTPException(403, "B03 member or resource-owner coordinate changed")
    now = datetime.now(timezone.utc)
    if (
        datetime.fromisoformat(str(p["valid_from"]).replace("Z", "+00:00")) > now
        or datetime.fromisoformat(str(p["expires_at"]).replace("Z", "+00:00")) <= now
    ):
        raise HTTPException(403, "B03 domain approval expired or is not yet valid")
    coordinate = B03ExecutionCoordinate(
        command_id=body.command_id,
        initiating_event_id="pending-prepared-event",
        envelope_id=str(p["execution_envelope_id"]),
        envelope_revision=int(p["execution_envelope_revision"]),
        represented_cos=str(p["represented_cos"]),
        actual_operator=str(p["actual_operator"]),
        context_scope=str(p["active_context"]),
        on_behalf_of=str(p["on_behalf_of"]),
        authority_grant_id=str(p["authority_grant_id"]),
        operating_agreement_id=str(p["operating_agreement_id"]),
        resource_owner=str(p["resource_owner"]),
        provider_owner=str(p["provider_owner"]),
        provider=str(p["provider"]),
        provider_account=str(p["provider_account_coordinate"]),
        adapter_identity=str(p["adapter_identity"]),
        destination_task_list=str(p["destination_task_list_coordinate"]),
        content_fingerprint=desired_fingerprint,
        domain_approval_event_id=body.domain_approval_event_id,
        openclaw_approval_event_id="pending-openclaw-approval",
        valid_from=str(p["valid_from"]),
        expires_at=str(p["expires_at"]),
        idempotency_key=f"google-tasks-create:{body.command_id}",
    )
    coordinate.validate(now=now)
    if len(_b03_binding_matches(runtime, coordinate)) != 1:
        raise HTTPException(403, "B03 provider route is missing or ambiguous")
    agreement_action = {
        "operating_agreement_id": p["operating_agreement_id"],
        "context_scope": p["active_context"],
        "provider_account": p["provider_account_coordinate"],
        "destination_task_list": p["destination_task_list_coordinate"],
    }
    _require_current_b03_agreement(runtime, session, agreement_action)
    prepared_payload = {
        "execution_policy_version": B03_POLICY_VERSION,
        "action_class": B03_ACTION_CLASS,
        "command_id": body.command_id,
        "execution_envelope_id": p["execution_envelope_id"],
        "execution_envelope_revision": p["execution_envelope_revision"],
        "represented_cos": p["represented_cos"],
        "actual_operator": p["actual_operator"],
        "active_context": p["active_context"],
        "on_behalf_of": p["on_behalf_of"],
        "authority_basis": p["authority_grant_id"],
        "operating_agreement_id": p["operating_agreement_id"],
        "resource_owner": p["resource_owner"],
        "provider_owner": p["provider_owner"],
        "provider": B03_PROVIDER,
        "provider_account_coordinate": p["provider_account_coordinate"],
        "adapter_identity": B03_ADAPTER_IDENTITY,
        "destination_task_list_coordinate": p["destination_task_list_coordinate"],
        "approved_task_content_fingerprint": desired_fingerprint,
        "approval_fingerprint": p["approval_fingerprint"],
        "desired_task_content": desired,
        "domain_approval_event_id": body.domain_approval_event_id,
        "openclaw_approval_event_id": None,
        "created_at": now.isoformat(),
        "valid_from": p["valid_from"],
        "expires_at": p["expires_at"],
        "revoked": False,
        "used": False,
        "idempotency_key": coordinate.idempotency_key,
        "provider_contact_state": "not_contacted",
        "terminal_result_state": "missing",
        "later_observation_state": "missing",
        "reconciliation_state": "not_applicable",
        "maximum_effect_count": 1,
        "provider_effect_count": 0,
    }
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="cos.google_tasks_execution.prepared",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=str(approval_event["owner_scope"]),
        visibility_scope=str(approval_event["visibility_scope"]),
        sensitivity=cast(Sensitivity, approval_event["sensitivity"]),
        payload=prepared_payload,
        request_id=body.request_id,
        causation_id=body.domain_approval_event_id,
        request_fingerprint=body.expected_approval_fingerprint,
        schema_version=2,
    )
    await runtime.wait_for_projection("tasks", event_id)
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.google-tasks-create.prepare",
        result_event_ids=[event_id],
    )
    return {
        "command_id": body.command_id,
        "event_id": event_id,
        "duplicate": duplicate,
        "state": "held_for_approval",
        "execution_policy_version": B03_POLICY_VERSION,
        "action_class": B03_ACTION_CLASS,
        "content_fingerprint": desired_fingerprint,
        "domain_approval_event_id": body.domain_approval_event_id,
        "openclaw_approval_state": "required",
        "provider_effect_count": 0,
    }


@router.post("/tools/google-tasks-create/execute", status_code=202)
async def execute_google_tasks_create(
    body: GoogleTasksCreateExecuteRequest, request: Request
) -> dict[str, Any]:
    """Dispatch once after governance and an independent allow-once receipt."""

    session = _session(
        request,
        body.provenance,
        required_permission=cast(DelegationPermission, B03_PERMISSION),
        context_scope=body.context_scope,
    )
    _require_adult(session)
    runtime = _runtime(request)
    context = session.service_context
    if context is None or runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "B03 production execution is unavailable")
    action = get_google_tasks_execution(
        runtime.projections.connection("tasks"), session, command_id=body.command_id
    )
    if action is None:
        raise HTTPException(404, "B03 command not found")
    if runtime.effective_openclaw_state() != "healthy":
        raise HTTPException(
            403, "independent OpenClaw runtime is unavailable or degraded"
        )
    if action.get("contact_attempt_event_id") or action.get("used_at"):
        raise HTTPException(409, "B03 envelope was already used; retry is refused")
    if action.get("revoked_at"):
        raise HTTPException(403, "B03 envelope was revoked")
    now = datetime.now(timezone.utc)
    if (
        action.get("state") != "held_for_approval"
        or action.get("last_event_id") != body.expected_last_event_id
        or action.get("approval_fingerprint") != body.expected_approval_fingerprint
        or action.get("authority_grant_id") != context.authority_grant_id
        or action.get("actual_operator") != COS_AUTOMATION_OPERATOR_ID
        or action.get("represented_cos") != COS_SERVICE_PRINCIPAL_ID
        or action.get("provider_owner") != COS_SERVICE_PRINCIPAL_ID
        or action.get("provider") != B03_PROVIDER
        or action.get("adapter_identity") != B03_ADAPTER_IDENTITY
        or action.get("maximum_effect_count") != 1
        or action.get("provider_effect_count") != 0
        or datetime.fromisoformat(str(action["expires_at"]).replace("Z", "+00:00"))
        <= now
    ):
        raise HTTPException(403, "B03 envelope is changed, expired, or ineligible")
    coordinate = B03ExecutionCoordinate(
        command_id=str(action["command_id"]),
        initiating_event_id=str(action["initiating_event_id"]),
        envelope_id=str(action["envelope_id"]),
        envelope_revision=int(action["envelope_revision"]),
        represented_cos=str(action["represented_cos"]),
        actual_operator=str(action["actual_operator"]),
        context_scope=str(action["context_scope"]),
        on_behalf_of=str(action["on_behalf_of"]),
        authority_grant_id=str(action["authority_grant_id"]),
        operating_agreement_id=str(action["operating_agreement_id"]),
        resource_owner=str(action["resource_owner"]),
        provider_owner=str(action["provider_owner"]),
        provider=str(action["provider"]),
        provider_account=str(action["provider_account"]),
        adapter_identity=str(action["adapter_identity"]),
        destination_task_list=str(action["destination_task_list"]),
        content_fingerprint=str(action["content_fingerprint"]),
        domain_approval_event_id=str(action["domain_approval_event_id"]),
        openclaw_approval_event_id="pending-openclaw-approval",
        valid_from=str(action["valid_from"]),
        expires_at=str(action["expires_at"]),
        idempotency_key=str(action["idempotency_key"]),
    )
    coordinate.validate(now=now)
    if len(_b03_binding_matches(runtime, coordinate)) != 1:
        raise HTTPException(403, "B03 provider route is missing or ambiguous")
    _require_current_b03_agreement(runtime, session, action)
    governance_payload = {
        "command_id": body.command_id,
        "action_class": B03_ACTION_CLASS,
        "provider": B03_PROVIDER,
        "operation": B03_OPERATION,
        "provider_account_class": "service-owned",
        "destination_class": "pre-existing dedicated task list",
        "content_fingerprint": action["content_fingerprint"],
        "maximum_effect_count": 1,
    }
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(
        session,
        "external_sync.write_requested",
        governance_payload,
        domain_governance=(True, "exact_b03_domain_approval"),
    )
    if not decision.pass_:
        raise HTTPException(
            403,
            {
                "code": "write_denied",
                "layer": decision.layer_failed,
                "reason": decision.reason,
            },
        )
    openclaw_event_id = await _consume_openclaw_approval(
        request,
        session,
        body.provenance,
        body,
        tool_name="administrateme_execute_google_tasks_create_once",
        action=B03_OPERATION,
        causation_id=str(action["domain_approval_event_id"]),
    )
    if (
        b03_task_content_fingerprint(
            b03_canonical_task_content(dict(action["desired_task_content"]))
        )
        != action["content_fingerprint"]
    ):
        raise HTTPException(
            403, "B03 final privacy/content guard rejected changed content"
        )
    approval_event_id, _ = await _append(
        runtime,
        session,
        event_type="cos.google_tasks_execution.openclaw_approved",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=str(action["owner_scope"]),
        visibility_scope=str(action["visibility_scope"]),
        sensitivity=cast(Sensitivity, action["sensitivity"]),
        payload={
            "command_id": body.command_id,
            "execution_envelope_id": action["envelope_id"],
            "execution_envelope_revision": action["envelope_revision"],
            "domain_approval_event_id": action["domain_approval_event_id"],
            "openclaw_approval_event_id": openclaw_event_id,
            "approval_fingerprint": body.expected_approval_fingerprint,
            "approved_at": now.isoformat(),
            "expires_at": action["expires_at"],
            "maximum_effect_count": 1,
        },
        request_id=f"{body.request_id}:approval",
        causation_id=openclaw_event_id,
        request_fingerprint=body.expected_approval_fingerprint,
        schema_version=2,
    )
    await runtime.wait_for_projection("tasks", approval_event_id)
    command_payload = {
        "command_id": body.command_id,
        "member_id": session.auth_member_id,
        "provider": B03_PROVIDER,
        "provider_account_id": action["provider_account"],
        "operation": B03_OPERATION,
        "target_external_id": None,
        "expected_provider_revision": None,
        "payload": {
            **dict(action["desired_task_content"]),
            "tasklist_id": action["destination_task_list"],
            "b03_production_v2": True,
            "execution_envelope_id": action["envelope_id"],
            "execution_envelope_revision": action["envelope_revision"],
            "represented_cos": action["represented_cos"],
            "actual_operator": action["actual_operator"],
            "context_scope": action["context_scope"],
            "on_behalf_of": action["on_behalf_of"],
            "resource_owner": action["resource_owner"],
            "provider_owner": action["provider_owner"],
            "adapter_identity": action["adapter_identity"],
            "content_fingerprint": action["content_fingerprint"],
            "maximum_effect_count": 1,
            "expires_at": action["expires_at"],
        },
        "requested_at": now.isoformat(),
        "domain_approval_event_id": action["domain_approval_event_id"],
        "openclaw_approval_event_id": openclaw_event_id,
        "request_fingerprint": action["content_fingerprint"],
        "privacy_guard_version": "google-tasks-create-content-guard-v2",
        "origin_proposal_id": body.command_id,
    }
    command_event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="external_sync.write_requested",
            schema_version=2,
            occurred_at=now.isoformat(),
            source_adapter="openclaw-plugin",
            source_account_id=body.provenance.account_id or body.provenance.channel_id,
            owner_scope=str(action["owner_scope"]),
            visibility_scope=str(action["visibility_scope"]),
            sensitivity=cast(Sensitivity, action["sensitivity"]),
            correlation_id=session.correlation_id,
            causation_id=approval_event_id,
            actor_identity=COS_SERVICE_PRINCIPAL_ID,
            idempotency_key=f"b03-provider-command:{body.command_id}",
            raw_ref=f"content-sha256:{action['content_fingerprint']}",
            payload=command_payload,
        )
    )
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.google-tasks-create.execute",
        result_event_ids=[approval_event_id, command_event_id],
    )
    return {
        "accepted": True,
        "duplicate": False,
        "command_id": body.command_id,
        "event_id": command_event_id,
        "domain_approval_event_id": action["domain_approval_event_id"],
        "openclaw_approval_event_id": openclaw_event_id,
        "state": "approved",
        "maximum_effect_count": 1,
    }


@router.post("/tools/google-tasks-create/state")
async def google_tasks_create_state(
    body: GoogleTasksCreateStateRequest, request: Request
) -> dict[str, Any]:
    session = _session(
        request,
        body.provenance,
        required_permission=cast(DelegationPermission, B03_PERMISSION),
        context_scope=body.context_scope,
    )
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "B03 execution projection is unavailable")
    action = get_google_tasks_execution(
        runtime.projections.connection("tasks"), session, command_id=body.command_id
    )
    if action is None:
        raise HTTPException(404, "B03 command not found")
    public = b03_public_envelope_state(action)
    public.update(
        {
            "command_id": body.command_id,
            "terminal_result_event_id": action.get("terminal_result_event_id"),
            "observation_event_id": action.get("observation_event_id"),
            "reconciliation_event_id": action.get("reconciliation_event_id"),
            "provider_effect_count": action.get("provider_effect_count", 0),
        }
    )
    return public


@router.post("/tools/delegated-actions/prepare", status_code=201)
async def prepare_delegated_action(
    body: DelegatedActionPrepareRequest, request: Request
) -> dict[str, Any]:
    """Prepare or hold one desired fixture representation under an exact grant."""

    session = _session(
        request,
        body.provenance,
        required_permission="prepare_external_actions",
        context_scope=body.context_scope,
    )
    _require_adult(session)
    context = session.service_context
    runtime = _runtime(request)
    if context is None or runtime.projections is None:
        raise HTTPException(503, "CoS execution context is unavailable")
    if context.context_scope != "member_private" or not context.context_member_id:
        raise HTTPException(
            403, "G07 fixture execution requires one member-private context"
        )
    if body.expected_policy_digest != execution_policy_digest():
        raise HTTPException(409, "delegated execution policy changed")
    spec = ACTION_SPECS[body.action_class]
    if body.canonical_object_kind not in spec.canonical_object_kinds:
        raise HTTPException(
            422, "canonical object kind does not match the action class"
        )
    if spec.requires_target and not body.target_external_id:
        raise HTTPException(422, "this action class requires an exact provider object")
    if spec.recurrence and body.recurrence_strategy != "provider_series":
        raise HTTPException(
            422, "stable provider recurrence requires provider-series identity"
        )
    if not spec.recurrence and body.recurrence_strategy == "provider_series":
        raise HTTPException(
            422, "provider-series identity requires a stable recurrence action"
        )
    _require_fixture_coordinates(session, body)
    canonical = _canonical_execution_object(
        runtime,
        session,
        kind=body.canonical_object_kind,
        object_id=body.canonical_object_id,
    )
    if canonical is None:
        raise HTTPException(404, "canonical action object not found")
    fact_events = await _execution_fact_events(runtime, session, body.fact_event_ids)
    now = datetime.now(timezone.utc)
    if body.facts_observed_at > now + timedelta(seconds=30):
        raise HTTPException(422, "fact observation cannot be in the future")
    if body.facts_valid_until <= now:
        raise HTTPException(409, "delegated action facts are stale")
    canonical_last_event_id = str(canonical.get("last_event_id") or "")
    if (
        not canonical_last_event_id
        or canonical_last_event_id not in body.fact_event_ids
    ):
        raise HTTPException(
            409, "canonical object revision is absent from fact lineage"
        )
    authoritative_fact_at = max(
        datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00"))
        for event in fact_events
    )
    if (
        abs((body.facts_observed_at - authoritative_fact_at).total_seconds()) > 5
        or (now - authoritative_fact_at).total_seconds()
        > EXECUTION_FACT_MAX_AGE_SECONDS
        or body.facts_valid_until
        > authoritative_fact_at + timedelta(seconds=EXECUTION_FACT_MAX_AGE_SECONDS)
    ):
        raise HTTPException(409, "delegated action fact lineage is stale or misdated")
    if body.expires_at <= now or body.expires_at > now + timedelta(days=31):
        raise HTTPException(422, "delegated action must expire within 31 days")
    grant_row = (
        runtime.projections.connection("parties")
        .execute(
            "SELECT * FROM service_delegation_grants WHERE tenant_id=? AND grant_id=?",
            (session.tenant_id, context.authority_grant_id),
        )
        .fetchone()
    )
    if grant_row is None:
        raise HTTPException(403, "delegated execution grant disappeared")
    content_guard_digest = _guard_delegated_payload(
        spec.provider_family, body.desired_payload
    )
    desired_digest = action_fingerprint(body.desired_payload)
    desired_state = desired_state_for(
        spec,
        desired_payload=body.desired_payload,
        destination_id=body.destination_id,
    )
    material = {
        "canonical_object_kind": body.canonical_object_kind,
        "canonical_object_id": body.canonical_object_id,
        "occurrence_id": body.occurrence_id,
        "series_id": body.series_id,
        "recurrence_strategy": body.recurrence_strategy,
        "coordination_id": body.coordination_id,
        "desired_representation_id": body.desired_representation_id,
        "authority_grant_id": context.authority_grant_id,
        "resource_owner_principal_id": context.resource_owner_principal_id,
        "provider_account_owner_principal_id": body.provider_account_owner_principal_id,
        "provider_account_id": body.provider_account_id,
        "provider_family": spec.provider_family,
        "destination_id": body.destination_id,
        "audience_ids": sorted(body.audience_ids),
        "action_class": body.action_class,
        "operation": spec.operation,
        "representation_kind": body.representation_kind,
        "expected_provider_revision": body.expected_provider_revision,
        "target_external_id": body.target_external_id,
        "desired_payload_digest": desired_digest,
        "operator_principal_id": _operator_id(body.operator_kind),
        "operator_kind": body.operator_kind,
        "represented_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "active_context_scope": context.context_scope,
        "active_context_member_id": context.context_member_id,
        "on_behalf_of_member_id": context.on_behalf_of_member_id,
        "authority_grant_event_id": str(grant_row["last_event_id"]),
        "provider": "fixture",
        "production_adapter": spec.production_adapter,
        "adapter_architecture": spec.architecture,
        "fact_event_ids": sorted(body.fact_event_ids),
        "facts_observed_at": authoritative_fact_at.isoformat(),
        "facts_valid_until": body.facts_valid_until.isoformat(),
        "policy_version": EXECUTION_POLICY_VERSION,
        "policy_digest": execution_policy_digest(),
        "privacy_guard_version": "cos-delegated-content-guard-v1",
        "content_guard_digest": content_guard_digest,
        "risk_class": body.risk_class,
        "execution_mode": body.execution_mode,
        "expires_at": body.expires_at.isoformat(),
    }
    immutable_fingerprint = action_fingerprint(material)
    action_digest = hashlib.sha256(
        f"{session.tenant_id}|{context.authority_grant_id}|{body.request_id}".encode()
    ).hexdigest()[:32]
    action_id = f"cos-action:{action_digest}"
    command_id = f"cos-command:{action_digest}"
    initial_state = (
        "prohibited"
        if body.risk_class in HARD_CEILING_RISKS
        else "shadow"
        if body.execution_mode == "shadow"
        else "desired"
        if body.execution_mode == "desired_only"
        else "dashboard_only"
        if not spec.fixture_executable
        else "awaiting_member_approval"
    )
    owner_scope = f"private:{context.context_member_id}"
    payload = {
        "action_id": action_id,
        "action_revision": 1,
        "canonical_object_kind": body.canonical_object_kind,
        "canonical_object_id": body.canonical_object_id,
        "occurrence_id": body.occurrence_id,
        "series_id": body.series_id,
        "recurrence_strategy": body.recurrence_strategy,
        "coordination_id": body.coordination_id,
        "desired_representation_id": body.desired_representation_id,
        "actor_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "actor_party_id": COS_PARTY_ID,
        "operator_principal_id": _operator_id(body.operator_kind),
        "operator_kind": body.operator_kind,
        "represented_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "active_context_scope": context.context_scope,
        "active_context_member_id": context.context_member_id,
        "on_behalf_of_member_id": context.on_behalf_of_member_id,
        "authority_grant_id": context.authority_grant_id,
        "authority_grant_event_id": str(grant_row["last_event_id"]),
        "resource_owner_principal_id": str(context.resource_owner_principal_id),
        "provider_account_owner_principal_id": body.provider_account_owner_principal_id,
        "provider": "fixture",
        "provider_family": spec.provider_family,
        "provider_account_id": body.provider_account_id,
        "production_adapter": spec.production_adapter,
        "adapter_architecture": spec.architecture,
        "destination_id": body.destination_id,
        "audience_ids": sorted(body.audience_ids),
        "action_class": body.action_class,
        "operation": spec.operation,
        "representation_kind": body.representation_kind,
        "expected_provider_revision": body.expected_provider_revision,
        "target_external_id": body.target_external_id,
        "desired_payload": body.desired_payload,
        "desired_state": desired_state,
        "desired_payload_digest": desired_digest,
        "action_fingerprint": immutable_fingerprint,
        "command_id": command_id,
        "command_idempotency_key": f"cos-delegated-command:{action_id}",
        "fact_event_ids": body.fact_event_ids,
        "facts_observed_at": authoritative_fact_at.isoformat(),
        "facts_valid_until": body.facts_valid_until.isoformat(),
        "policy_version": EXECUTION_POLICY_VERSION,
        "policy_digest": execution_policy_digest(),
        "privacy_guard_version": "cos-delegated-content-guard-v1",
        "content_guard_digest": content_guard_digest,
        "risk_class": body.risk_class,
        "execution_mode": body.execution_mode,
        "initial_state": initial_state,
        "prepared_at": now.isoformat(),
        "expires_at": body.expires_at.isoformat(),
        "provider_effect_count": 0,
    }
    request_fingerprint = _fingerprint(
        body.model_dump(
            mode="json",
            exclude={"provenance", "openclaw_approval_receipt_id"},
        )
    )
    event_id, duplicate = await _append(
        runtime,
        session,
        event_type="cos.delegated_action.prepared",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    await runtime.wait_for_projection("tasks", event_id)
    action = get_delegated_action(
        runtime.projections.connection("tasks"), session, action_id=action_id
    )
    approval_event_id: str | None = None
    standing_matches: list[dict[str, Any]] = []
    if action is not None and initial_state == "awaiting_member_approval":
        standing_matches = matching_execution_envelopes(
            runtime.projections.connection("tasks"),
            session,
            action=action,
            as_of_iso=now.isoformat(),
        )
        if len(standing_matches) == 1:
            envelope = standing_matches[0]
            approval_expiry = min(
                body.expires_at,
                datetime.fromisoformat(
                    str(envelope["expires_at"]).replace("Z", "+00:00")
                ),
            )
            approval_event_id, _ = await _append(
                runtime,
                session,
                event_type="cos.delegated_action.domain_approved",
                source_account_id=body.provenance.account_id
                or body.provenance.channel_id,
                owner_scope=owner_scope,
                visibility_scope=owner_scope,
                payload={
                    "action_id": action_id,
                    "action_revision": 1,
                    "action_fingerprint": immutable_fingerprint,
                    "approval_kind": "standing_envelope",
                    "approved_by_member_id": envelope["granted_by_member_id"],
                    "approval_event_id": envelope["last_event_id"],
                    "execution_envelope_id": envelope["envelope_id"],
                    "approved_at": now.isoformat(),
                    "expires_at": approval_expiry.isoformat(),
                    "openclaw_permission_included": False,
                },
                request_id=f"{body.request_id}:standing-approval",
                causation_id=event_id,
                request_fingerprint=immutable_fingerprint,
            )
            await runtime.wait_for_projection("tasks", approval_event_id)
            initial_state = "awaiting_openclaw_approval"
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.delegated-actions.prepare",
        result_event_ids=[
            event_id,
            *([approval_event_id] if approval_event_id else []),
        ],
    )
    return {
        "action_id": action_id,
        "event_id": event_id,
        "domain_approval_event_id": approval_event_id,
        "duplicate": duplicate,
        "state": initial_state,
        "action_fingerprint": immutable_fingerprint,
        "policy_digest": execution_policy_digest(),
        "standing_match_count": len(standing_matches),
        "provider_execution_scope": "fixture_only",
        "provider_effect_count": 0,
    }


@router.post("/tools/delegated-actions/execute", status_code=202)
async def execute_delegated_action(
    body: DelegatedActionExecuteRequest, request: Request
) -> dict[str, Any]:
    """Create one provider command after both independent approvals."""

    session = _session(
        request,
        body.provenance,
        required_permission="execute_fixture_actions",
        context_scope=body.context_scope,
    )
    _require_adult(session)
    runtime = _runtime(request)
    context = session.service_context
    if context is None or runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "CoS execution context is unavailable")
    action = get_delegated_action(
        runtime.projections.connection("tasks"), session, action_id=body.action_id
    )
    if action is None:
        raise HTTPException(404, "delegated action not found")
    prior_command = action.get("command_event_id")
    if prior_command:
        if (
            action["action_fingerprint"] != body.expected_action_fingerprint
            or body.expected_policy_digest != execution_policy_digest()
            or action["policy_digest"] != execution_policy_digest()
            or action["operator_kind"] != body.operator_kind
        ):
            raise HTTPException(409, "duplicate execution does not match the command")
        return {
            "accepted": True,
            "duplicate": True,
            "event_id": prior_command,
            "action_id": body.action_id,
            "state": action["state"],
        }
    now = datetime.now(timezone.utc)
    domain_approvals = [
        approval
        for approval in action.get("approvals") or []
        if datetime.fromisoformat(str(approval["expires_at"]).replace("Z", "+00:00"))
        > now
        and approval.get("action_fingerprint") == action.get("action_fingerprint")
    ]
    domain_valid = len(domain_approvals) == 1
    domain_reason = "exact_domain_approval_required"
    if action["state"] != "awaiting_openclaw_approval":
        domain_valid = False
        domain_reason = f"invalid_action_state:{action['state']}"
    if action["last_event_id"] != body.expected_last_event_id:
        domain_valid = False
        domain_reason = "action_changed"
    if action["action_fingerprint"] != body.expected_action_fingerprint:
        domain_valid = False
        domain_reason = "action_content_changed"
    if (
        body.expected_policy_digest != execution_policy_digest()
        or action["policy_digest"] != execution_policy_digest()
    ):
        domain_valid = False
        domain_reason = "execution_policy_changed"
    if (
        datetime.fromisoformat(str(action["facts_valid_until"]).replace("Z", "+00:00"))
        <= now
    ):
        domain_valid = False
        domain_reason = "facts_stale"
    current_canonical = _canonical_execution_object(
        runtime,
        session,
        kind=str(action["canonical_object_kind"]),
        object_id=str(action["canonical_object_id"]),
    )
    if current_canonical is None or current_canonical.get("last_event_id") not in set(
        action.get("fact_event_ids") or []
    ):
        domain_valid = False
        domain_reason = "canonical_object_changed"
    if datetime.fromisoformat(str(action["expires_at"]).replace("Z", "+00:00")) <= now:
        domain_valid = False
        domain_reason = "action_expired"
    if action["operator_kind"] != body.operator_kind:
        domain_valid = False
        domain_reason = "operator_changed"
    if action["authority_grant_id"] != context.authority_grant_id:
        domain_valid = False
        domain_reason = "authority_grant_changed"
    grant = (
        runtime.projections.connection("parties")
        .execute(
            "SELECT last_event_id, revoked_at, expires_at FROM service_delegation_grants "
            "WHERE tenant_id=? AND grant_id=?",
            (session.tenant_id, action["authority_grant_id"]),
        )
        .fetchone()
    )
    if (
        grant is None
        or grant["revoked_at"] is not None
        or grant["last_event_id"] != action["authority_grant_event_id"]
        or (
            grant["expires_at"]
            and datetime.fromisoformat(str(grant["expires_at"]).replace("Z", "+00:00"))
            <= now
        )
    ):
        domain_valid = False
        domain_reason = "authority_grant_stale_or_revoked"
    approval = domain_approvals[0] if domain_valid else None
    if approval and approval.get("approval_kind") == "standing_envelope":
        matches = matching_execution_envelopes(
            runtime.projections.connection("tasks"),
            session,
            action=action,
            as_of_iso=now.isoformat(),
        )
        if len(matches) != 1 or matches[0]["envelope_id"] != approval.get(
            "execution_envelope_id"
        ):
            domain_valid = False
            domain_reason = "standing_envelope_missing_revoked_or_ambiguous"
    if runtime.effective_openclaw_state() != "healthy":
        domain_valid = False
        domain_reason = "openclaw_degraded"
    command_payload = {
        "command_id": action["command_id"],
        "member_id": context.context_member_id,
        "provider": "fixture",
        "provider_account_id": action["provider_account_id"],
        "operation": action["operation"],
        "target_external_id": action.get("target_external_id"),
        "expected_provider_revision": action.get("expected_provider_revision"),
        "payload": {
            "delegated_action": True,
            "action_id": action["action_id"],
            "action_class": action["action_class"],
            "provider_family": action["provider_family"],
            "provider_account_owner_principal_id": action[
                "provider_account_owner_principal_id"
            ],
            "destination_id": action["destination_id"],
            "desired_state": action["desired_state"],
            "occurrence_id": action.get("occurrence_id"),
            "series_id": action.get("series_id"),
            "recurrence_strategy": action["recurrence_strategy"],
            "action_fingerprint": action["action_fingerprint"],
        },
        "requested_at": now.isoformat(),
    }
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(
        session,
        "external_sync.write_requested",
        command_payload,
        domain_governance=(domain_valid, domain_reason),
    )
    if not decision.pass_:
        raise HTTPException(
            403,
            {
                "code": "write_denied",
                "layer": decision.layer_failed,
                "reason": decision.reason,
            },
        )
    assert approval is not None
    openclaw_approval_event_id = await _consume_openclaw_approval(
        request,
        session,
        body.provenance,
        body,
        tool_name="administrateme_execute_delegated_action",
        action=str(action["operation"]),
        causation_id=str(approval["approval_event_id"]),
    )
    guard_digest = _guard_delegated_payload(
        str(action["provider_family"]), dict(action["desired_payload"])
    )
    if guard_digest != action["content_guard_digest"]:
        raise HTTPException(
            403, "delegated action failed its final content/privacy guard"
        )
    dispatch_event_id, _ = await _append(
        runtime,
        session,
        event_type="cos.delegated_action.dispatch_authorized",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=action["owner_scope"],
        visibility_scope=action["visibility_scope"],
        sensitivity=cast(Sensitivity, action["sensitivity"]),
        payload={
            "action_id": action["action_id"],
            "action_revision": action["action_revision"],
            "action_fingerprint": action["action_fingerprint"],
            "domain_approval_event_id": approval["approval_event_id"],
            "openclaw_approval_event_id": openclaw_approval_event_id,
            "command_id": action["command_id"],
            "command_idempotency_key": action["command_idempotency_key"],
            "operator_principal_id": _operator_id(body.operator_kind),
            "authorized_at": now.isoformat(),
        },
        request_id=f"{body.action_id}:dispatch",
        causation_id=openclaw_approval_event_id,
        request_fingerprint=action["action_fingerprint"],
    )
    command_payload.update(
        {
            "domain_approval_event_id": approval["approval_event_id"],
            "openclaw_approval_event_id": openclaw_approval_event_id,
            "request_fingerprint": action["action_fingerprint"],
            "privacy_guard_version": action["privacy_guard_version"],
            "origin_proposal_id": action["action_id"],
        }
    )
    command_key = str(action["command_idempotency_key"])
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, command_key)
    if prior is None:
        command_event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=session.tenant_id,
                type="external_sync.write_requested",
                schema_version=2,
                occurred_at=now.isoformat(),
                source_adapter="openclaw-plugin",
                source_account_id=body.provenance.account_id
                or body.provenance.channel_id,
                owner_scope=action["owner_scope"],
                visibility_scope=action["visibility_scope"],
                sensitivity=cast(Sensitivity, action["sensitivity"]),
                correlation_id=session.correlation_id,
                causation_id=dispatch_event_id,
                actor_identity=COS_SERVICE_PRINCIPAL_ID,
                idempotency_key=command_key,
                raw_ref=f"request-sha256:{action['action_fingerprint']}",
                payload=command_payload,
            )
        )
        duplicate = False
    else:
        command_event_id = str(prior["event_id"])
        duplicate = True
    await runtime.wait_for_projection("tasks", command_event_id)
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.delegated-actions.execute",
        result_event_ids=[dispatch_event_id, command_event_id],
    )
    return {
        "accepted": True,
        "duplicate": duplicate,
        "action_id": body.action_id,
        "event_id": command_event_id,
        "dispatch_authorization_event_id": dispatch_event_id,
        "domain_approval_event_id": approval["approval_event_id"],
        "openclaw_approval_event_id": openclaw_approval_event_id,
        "state": "awaiting_result",
        "provider_execution_scope": "fixture_only",
    }


@router.post("/tools/delegated-actions/observe")
async def observe_delegated_action(
    body: DelegatedActionObserveRequest, request: Request
) -> dict[str, Any]:
    """Append later fixture observation and reconcile it to desired state."""

    session = _session(
        request,
        body.provenance,
        required_permission="execute_fixture_actions",
        context_scope=body.context_scope,
    )
    _require_adult(session)
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "CoS execution context is unavailable")
    action = get_delegated_action(
        runtime.projections.connection("tasks"), session, action_id=body.action_id
    )
    if action is None:
        raise HTTPException(404, "delegated action not found")
    if action["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "delegated action changed")
    if (
        action["state"] != "awaiting_observation"
        or len(action.get("results") or []) != 1
    ):
        raise HTTPException(409, "delegated action has no unambiguous terminal result")
    result = action["results"][0]
    if result.get("suppressed") or not result.get("external_id"):
        raise HTTPException(
            409, "suppressed action cannot be observed as a provider effect"
        )
    if runtime.fixture_action_adapter is None:
        raise HTTPException(503, "fixture observation adapter is unavailable")
    observation = runtime.fixture_action_adapter.observation(body.action_id)
    if observation is None:
        raise HTTPException(409, "fixture provider observation is missing")
    now = EventEnvelope.now_utc_iso()
    observed_state = dict(observation["state"])
    observation_digest = action_fingerprint(observed_state)
    observation_id = (
        f"observation:{hashlib.sha256(body.action_id.encode()).hexdigest()[:32]}"
    )
    observation_event_id, _ = await _append(
        runtime,
        session,
        event_type="cos.delegated_action.observed",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=action["owner_scope"],
        visibility_scope=action["visibility_scope"],
        sensitivity=cast(Sensitivity, action["sensitivity"]),
        payload={
            "observation_id": observation_id,
            "action_id": body.action_id,
            "command_id": action["command_id"],
            "result_event_id": result["result_event_id"],
            "provider": "fixture",
            "provider_family": action["provider_family"],
            "provider_account_id": action["provider_account_id"],
            "destination_id": action["destination_id"],
            "external_id": observation["external_id"],
            "provider_revision": observation["provider_revision"],
            "observed_state": observed_state,
            "observation_fingerprint": observation_digest,
            "observed_at": now,
            "direct_provider_edit_detected": observation[
                "direct_provider_edit_detected"
            ],
        },
        request_id=f"{body.request_id}:observation",
        causation_id=result["result_event_id"],
    )
    await runtime.wait_for_projection("tasks", observation_event_id)
    desired_digest = action_fingerprint(action["desired_state"])
    direct_edit = bool(observation["direct_provider_edit_detected"])
    exact = desired_digest == observation_digest
    outcome = "conflicted" if direct_edit else "reconciled" if exact else "drifted"
    reason = (
        "direct_provider_edit"
        if direct_edit
        else "exact_match"
        if exact
        else "provider_state_drift"
    )
    canonical_outcome = (
        "completed"
        if exact and action["operation"] in {"complete_task", "complete_reminder"}
        else "reopened"
        if exact and action["operation"] in {"uncomplete_task", "uncomplete_reminder"}
        else "withdrawn"
        if exact
        and action["operation"]
        in {"delete_event", "delete_task", "delete_list", "delete_reminder"}
        else "active"
        if exact
        else "none"
    )
    reconciliation_id = (
        f"reconciliation:{hashlib.sha256(body.action_id.encode()).hexdigest()[:32]}"
    )
    canonical_outcome_event_id: str | None = None
    if (
        outcome == "reconciled"
        and action["canonical_object_kind"] == "task"
        and canonical_outcome in {"completed", "reopened"}
    ):
        canonical_outcome_event_id, _ = await _append(
            runtime,
            session,
            event_type="cos.delegated_action.canonical_outcome_returned",
            source_account_id=body.provenance.account_id or body.provenance.channel_id,
            owner_scope=action["owner_scope"],
            visibility_scope=action["visibility_scope"],
            sensitivity=cast(Sensitivity, action["sensitivity"]),
            payload={
                "action_id": body.action_id,
                "canonical_object_kind": "task",
                "canonical_object_id": action["canonical_object_id"],
                "outcome": canonical_outcome,
                "observation_event_id": observation_event_id,
                "represented_service_principal_id": action[
                    "represented_service_principal_id"
                ],
                "represented_service_party_id": action["actor_party_id"],
                "operator_principal_id": action["operator_principal_id"],
                "operator_kind": action["operator_kind"],
                "on_behalf_of_member_id": action["on_behalf_of_member_id"],
                "authority_grant_id": action["authority_grant_id"],
                "returned_at": now,
            },
            request_id=f"{body.request_id}:canonical-outcome",
            causation_id=observation_event_id,
        )
        await runtime.wait_for_projection("tasks", canonical_outcome_event_id)
    reconciliation_event_id, _ = await _append(
        runtime,
        session,
        event_type="cos.delegated_action.reconciled",
        source_account_id=body.provenance.account_id or body.provenance.channel_id,
        owner_scope=action["owner_scope"],
        visibility_scope=action["visibility_scope"],
        sensitivity=cast(Sensitivity, action["sensitivity"]),
        payload={
            "reconciliation_id": reconciliation_id,
            "action_id": body.action_id,
            "observation_event_id": observation_event_id,
            "result_event_id": result["result_event_id"],
            "desired_state_digest": desired_digest,
            "observed_state_digest": observation_digest,
            "outcome": outcome,
            "canonical_outcome": canonical_outcome,
            "canonical_outcome_event_id": canonical_outcome_event_id,
            "reason_code": reason,
            "reconciled_at": now,
        },
        request_id=f"{body.request_id}:reconciliation",
        causation_id=observation_event_id,
    )
    await runtime.wait_for_projection("tasks", reconciliation_event_id)
    await _record_tool_call(
        request,
        session,
        body.provenance,
        operation="tools.delegated-actions.observe-reconcile",
        result_event_ids=[
            observation_event_id,
            *([canonical_outcome_event_id] if canonical_outcome_event_id else []),
            reconciliation_event_id,
        ],
    )
    return {
        "action_id": body.action_id,
        "observation_event_id": observation_event_id,
        "reconciliation_event_id": reconciliation_event_id,
        "state": outcome,
        "canonical_outcome": canonical_outcome,
        "canonical_outcome_event_id": canonical_outcome_event_id,
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
    request_authorization = None
    grounding_id = getattr(approval_body, "request_authorization_event_id", None)
    if grounding_id:
        from adminme.products.openclaw.request_authorization import (
            validate_request_authorization,
        )

        request_authorization = await validate_request_authorization(
            request, session, provenance, domain_event, grounding_id
        )
    canceled = await runtime.event_log.get_by_idempotency(
        session.tenant_id,
        f"messages-reply-cancel:{session.auth_member_id}:{domain_event['payload']['action_id']}",
    )
    if canceled is not None:
        raise HTTPException(409, "pending Messages reply was canceled")
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
    provider_payload = payload.get("command_payload")
    if (
        payload.get("provider") == "bluebubbles"
        and isinstance(provider_payload, dict)
        and provider_payload.get("contract") == REPLY_CONTRACT
    ):
        current, reason = approval_is_current(
            runtime,
            member_id=session.auth_member_id,
            command_payload=provider_payload,
        )
        if not current:
            raise HTTPException(409, f"Messages reply approval is stale: {reason}")
        if runtime.projections is None:
            raise HTTPException(503, "Messages conversation state is unavailable")
        interaction = get_interaction(
            runtime.projections.connection("interactions"),
            session,
            interaction_id=str(provider_payload.get("interaction_id") or ""),
        )
        if interaction is None:
            raise HTTPException(409, "Messages conversation became unavailable")
        expected = {
            "last_event_id": provider_payload.get("expected_last_event_id"),
            "native_chat_id": provider_payload.get("native_chat_id"),
            "native_message_id": provider_payload.get("in_reply_to_native_message_id"),
            "native_revision": provider_payload.get("expected_native_revision"),
            "bridge_enrollment_id": provider_payload.get("bridge_enrollment_id"),
            "provider_account_id": provider_payload.get("provider_account_id"),
            "protected_source_id": provider_payload.get("protected_source_id"),
        }
        if any(interaction.get(key) != value for key, value in expected.items()):
            raise HTTPException(409, "Messages conversation or destination changed")
        current_participants = participant_ids(
            participants_for(
                runtime.projections.connection("interactions"),
                session,
                interaction_ids=[str(interaction["interaction_id"])],
            ).get(str(interaction["interaction_id"]), [])
        )
        if current_participants != provider_payload.get(
            "participant_ids"
        ) or canonical_sha256(current_participants) != provider_payload.get(
            "participant_fingerprint"
        ):
            raise HTTPException(409, "Messages participants changed")
    from adminme.apple_service_permissions import (
        command_permission,
        requires_service_permission,
    )

    pending_command = {
        "member_id": session.auth_member_id,
        "provider": payload["provider"],
        "provider_account_id": payload["provider_account_id"],
        "operation": payload["operation"],
        "payload": dict(payload.get("command_payload") or {}),
    }
    from adminme.cos_reminders_commands import CONTRACT as COS_REMINDER_CONTRACT
    from adminme.adapters.cos_reminders_write_policy import RULE_ID as COS_REMINDER_RULE

    own_reminder = pending_command["payload"].get("contract") == COS_REMINDER_CONTRACT
    if own_reminder and (
        request_authorization is None
        or request_authorization.get("rule_id") != COS_REMINDER_RULE
    ):
        raise HTTPException(
            403, "CoS reminders require exact current request authority"
        )
    if not own_reminder and (
        requires_service_permission(
            runtime, session.auth_member_id, str(payload["provider"])
        )
        or pending_command["payload"].get("apple_service_permission")
    ):
        try:
            command_permission(runtime, session.auth_member_id, pending_command)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    request_fingerprint = str(payload["request_fingerprint"])
    approval_event_id = await _consume_openclaw_approval(
        request,
        session,
        provenance,
        approval_body,
        tool_name=tool_name,
        action=str(payload["operation"]),
        causation_id=domain_event["event_id"],
        request_authorization=request_authorization,
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
        "privacy_guard_version": (
            "apple-messages-plain-text-exact-v1"
            if payload.get("provider") == "bluebubbles"
            else COS_REMINDER_CONTRACT
            if own_reminder
            else "member-provider-action-v1"
        ),
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


@router.post("/submissions/forward")
async def forward_for_review(
    body: ForwardForReviewRequest, request: Request
) -> dict[str, Any]:
    """Persist a deliberate text forward from an enrolled CoS channel.

    OpenClaw retains ownership of the transport and conversation. Exact account,
    actor and channel bindings independently resolve service and member identity.
    Forwarding grants interpretation only; provider actions still need review.
    """

    session = _session(request, body.provenance)
    _require_adult(session)
    provenance = body.provenance
    if provenance.call_kind not in {"slash_command", "bridge"}:
        raise HTTPException(
            403, "forwarding requires an explicit member submission gesture"
        )
    if provenance.call_kind == "bridge" and not provenance.message_id:
        raise HTTPException(
            422, "channel submission requires its native receipt identity"
        )
    runtime = _runtime(request)
    channel = cos_message_channel_for_account(
        runtime.settings.service_channel_accounts,
        transport_channel_id=provenance.channel_id,
        account_id=provenance.account_id or "",
    )
    if channel is None:
        raise HTTPException(
            403, "exact enrolled service channel binding is unavailable"
        )
    assert provenance.account_id is not None
    if (
        body.submission_gesture == "dedicated_intake"
        and channel.intake_mode != "member_submission"
    ):
        raise HTTPException(403, "this service channel is configured for conversation")
    identity = [
        provenance.channel_id,
        provenance.account_id,
        provenance.actor_id,
        provenance.message_id if provenance.call_kind == "bridge" else body.request_id,
    ]
    identity_digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode()
    ).hexdigest()
    key = f"openclaw-forward:{identity_digest}"
    fingerprint = _fingerprint(
        {
            "text": body.text,
            "sensitivity": body.sensitivity,
            "unsupported_attachment_count": body.unsupported_attachment_count,
            "submission_gesture": body.submission_gesture,
        }
    )
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    duplicate = prior is not None
    if prior is not None:
        if (
            prior.get("owner_scope") != f"private:{session.auth_member_id}"
            or prior.get("payload", {}).get("member_id") != session.auth_member_id
        ):
            raise HTTPException(
                403,
                "native submission receipt belongs to a different reviewed member binding",
            )
        if prior.get("raw_ref") != f"request-sha256:{fingerprint}":
            raise HTTPException(
                409, "native submission receipt conflicts with prior content"
            )
        event_id = str(prior["event_id"])
        intent_id = str(prior["payload"]["intent_id"])
    else:
        issues = []
        if body.unsupported_attachment_count:
            issues.append("unsupported_channel_attachment")
        now = EventEnvelope.now_utc_iso()
        intent_id = "forward_" + identity_digest[:48]
        payload = {
            "intent_id": intent_id,
            "member_id": session.auth_member_id,
            "text": body.text,
            "source_surface": "openclaw",
            "kind_hint": None,
            "source_event_id": None,
            "submitted_at": now,
            "client_request_id": identity_digest,
            "submission_kind": "member_forwarded",
            "evidence_complete": not issues,
            "evidence_issues": issues,
            "intake_channel": channel.provider,
        }
        await _guard(runtime, session, "triage.intent.submitted", payload)
        envelope = EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="triage.intent.submitted",
            schema_version=2,
            occurred_at=now,
            source_adapter="openclaw-submission",
            source_account_id=provenance.account_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity=body.sensitivity,
            # This fact records the authenticated human's explicit submission,
            # not an action by the represented CoS service principal.
            actor_identity=session.auth_member_id,
            correlation_id=provenance.correlation_id,
            idempotency_key=key,
            raw_ref=f"request-sha256:{fingerprint}",
            payload=payload,
        )
        try:
            event_id = await runtime.events.write(envelope)
        except IdempotencyConflict:
            concurrent = await runtime.event_log.get_by_idempotency(
                session.tenant_id, key
            )
            if (
                concurrent is not None
                and concurrent.get("owner_scope") != f"private:{session.auth_member_id}"
            ):
                raise HTTPException(
                    403,
                    "native submission receipt belongs to a different reviewed member binding",
                ) from None
            if (
                concurrent is None
                or concurrent.get("raw_ref") != f"request-sha256:{fingerprint}"
            ):
                raise HTTPException(
                    409, "native submission receipt conflicts with prior content"
                ) from None
            event_id = str(concurrent["event_id"])
            duplicate = True
    await runtime.wait_for_projection("triage", event_id)
    await _record_tool_call(
        request,
        session,
        provenance,
        operation="submissions.forward",
        result_event_ids=[event_id],
    )
    return {
        "accepted": True,
        "intent_id": intent_id,
        "intent_event_id": event_id,
        "duplicate": duplicate,
        "state": "captured_for_review",
        "provider_effect_permitted": False,
    }


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
