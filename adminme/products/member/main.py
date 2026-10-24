"""Responsive member portal and its narrow, authenticated APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal, Sequence, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel, Field, ValidationError

from adminme.comms import validate_outbound_communication
from adminme.adapters.cos_channel_acquisition import member_bridge_capability_ceiling
from adminme.bridge_authorization import authorization_from_record
from adminme.bridge_enrollment import (
    BridgeEnrollmentError,
    validate_enrollment_transition,
)
from adminme.context_fabric import policy_scope_digest
from adminme.cos_response_policy import (
    POLICY_CONTRACT as COS_RESPONSE_POLICY_CONTRACT,
    POLICY_EVENT_TYPE as COS_RESPONSE_POLICY_EVENT,
    validate_recipient_entries,
)
from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.log import IdempotencyConflict
from adminme.extensions import ExtensionError
from adminme.lib.adapters.contacts_base import normalize_identifier
from adminme.events.schemas.triage import (
    TriageRouteV1,
    TriageSourceSurface,
    TriageTargetKind,
)
from adminme.events.schemas.triage_management import AttentionRuleV2
from adminme.events.schemas.knowledge import KnowledgeGrantPermission
from adminme.events.schemas.reminder_context import (
    ContextKind,
    ContextSource,
    CueOperator,
)
from adminme.events.schemas.routines import RoutineDefinedV1
from adminme.events.schemas.calendar_policy import (
    CalendarRuleConfiguredV1,
    CalendarSchedulePolicyConfiguredV1,
    ScheduleWindowV1,
)
from adminme.lib.scope import allowed_read, filter_one
from adminme.lib.session import Session
from adminme.message_context import authored_message_text
from adminme.messages_reply import (
    REPLY_APPROVAL_TTL_SECONDS,
    REPLY_CAPABILITY_VERSION,
    REPLY_CONTENT_GUARD,
    REPLY_CONTRACT,
    REPLY_POLICY_VERSION,
    body_sha256,
    canonical_sha256,
    participant_ids,
    reply_control_for,
    validate_exact_body,
)
from adminme.member_chat import MemberChatError
from adminme.member_chat_approvals import pending_cos_approvals, resolve_cos_approval
from adminme.products.auth import require_principal, session_from_request
from adminme.products.bridge.auth import EnrollmentMemberIdResolver
from adminme.products.comms import main as comms
from adminme.products.core import main as core
from adminme.products.member.surfaces import (
    _triage_owed_to_party_available,
    _triage_review_state,
    authorize_surface,
    build_surface,
    communication_accounts,
    crm_detail,
    crm_search,
    evaluate_query,
    experience_mode,
    navigation,
    reminder_accounts,
)
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.products.openclaw.main import (
    LifecycleRequest as OpenClawLifecycleRequest,
    Provenance,
    _task_lifecycle,
)
from adminme.projections.parties.queries import get_party
from adminme.projections.places_assets_accounts.queries import (
    get_account,
    get_asset,
    get_place,
)
from adminme.projections.interactions.queries import get_interaction, participants_for
from adminme.projections.calendars.queries import (
    get_calendar_event,
    get_calendar_outcome,
)
from adminme.projections.recurrences.queries import (
    get_recurrence,
    get_routine,
    get_routine_occurrence,
)
from adminme.projections.member_knowledge.queries import (
    get_visible as get_visible_note,
    grants_for_note,
    list_visible as list_visible_notes,
    search_visible as search_visible_notes,
)
from adminme.projections.triage.queries import (
    backlog_for_member as triage_backlog_for_member,
    get_item as get_triage_item,
)
from adminme.projections.context_fabric.queries import (
    feedback_scope_count,
    get_source_policy_proposal,
    get_source_policy_rule,
    get_policy_rule,
    reconciliation_for_proposal,
    source_policy_proposals_for_member,
    source_policy_rules_for_member,
    verified_feedback_for_scope,
)
from adminme.projections.tasks.queries import get_task
from adminme.projections.money.queries import get_finance_goal
from adminme.candidate_policy import validate_policy_override
from adminme.capability_catalog import catalog_item, compose_member_catalog
from adminme.settings_control import (
    compose_connections,
    compose_source_policy_v2_sources,
    member_label,
    public_capability_summary,
)
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.source_policy import (
    SourceCoordinate,
    SourcePolicyV2,
    policy_for_preset,
    route_for_policy,
    source_coordinate_for_event,
    source_coordinate_contains,
    source_policy_event_types,
)
from adminme.projections.lists.queries import (
    get_item as get_list_item,
    items_for_member as list_items_for_member,
    lists_for_member,
)
from adminme.temporary_surfaces import (
    CHILD_ACTIONS,
    CHILD_QUERIES,
    CreateTemporarySurface,
    TemporarySurfaceError,
    TemporarySurfaceSpec,
)
from adminme.triage_contract import candidate_update_fields, triage_destination
from adminme.triage_context import add_triage_source_context
from adminme.triage_management import (
    attention_match,
    attention_rule_expired,
    attention_rule_preview,
    backlog_reassessment_preview,
    independent_observation_key,
    source_policy_rule_preview,
)
from adminme.today import add_today_decision_context


router = APIRouter(tags=["member-portal"])
SessionDep = Annotated[Session, Depends(session_from_request)]
ASSET_ROOT = Path(__file__).with_name("assets")
_MEMBER_INDEX = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
_MEMBER_ASSET_VERSION = hashlib.sha256(
    (ASSET_ROOT / "member.css").read_bytes() + (ASSET_ROOT / "member.js").read_bytes()
).hexdigest()[:12]


def _member_index_response() -> Response:
    return Response(
        content=_MEMBER_INDEX.replace("__ASSET_VERSION__", _MEMBER_ASSET_VERSION),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


class ChatRequest(BaseModel):
    model_config = {"extra": "forbid"}
    message: str = Field(min_length=1, max_length=4_000)
    request_id: str = Field(min_length=8, max_length=120)


class ChatApprovalDecision(BaseModel):
    model_config = {"extra": "forbid"}
    approval_id: str = Field(pattern=r"^plugin:[a-f0-9-]{36}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["allow-once", "deny"]


class SettingsProfileRequest(BaseModel):
    model_config = {"extra": "forbid"}
    profile_id: str = Field(min_length=1, max_length=120)
    request_id: str = Field(min_length=8, max_length=120)


class SettingsPreferencesRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    values: dict[str, int | bool | str] = Field(default_factory=dict)


class SettingsConnectionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    action: Literal["pause", "resume"]


class SettingsAppleMessagesReplyRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    action: Literal["enable", "pause", "revoke"]


class SettingsCosRepliesRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0, strict=True)
    request_id: str = Field(min_length=8, max_length=120)
    entries: list[dict[str, str]] = Field(max_length=100)


class SettingsCandidatePolicyRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    mode: Literal["review", "manual", "quiet"]
    language_enabled: bool
    candidate_kinds: list[TriageTargetKind] = Field(min_length=1, max_length=11)


class SettingsSourcePolicyV2Request(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    coordinate: SourceCoordinate
    action: Literal["set", "reset"] = "set"
    preset: Literal["focused", "balanced", "review_everything", "manual", "custom"] = (
        "balanced"
    )
    policy: SourcePolicyV2 | None = None


class SettingsSourcePolicyV2PreviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    coordinate: SourceCoordinate
    preset: Literal["focused", "balanced", "review_everything", "manual", "custom"] = (
        "balanced"
    )
    policy: SourcePolicyV2 | None = None
    recent_limit: int = Field(default=100, ge=1, le=200)


class SettingsBridgeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    member_id: str = Field(min_length=1, max_length=240)
    device_class: Literal["this_mac", "another_mac"] = "another_mac"
    capabilities: list[
        Literal[
            "apple_calendar",
            "apple_reminders",
            "apple_contacts",
            "apple_notes",
            "apple_voice_memos",
            "obsidian",
            "apple_messages_read",
        ]
    ] = Field(min_length=1, max_length=8)


class SettingsBridgeControlRequest(BaseModel):
    model_config = {"extra": "forbid"}
    command_id: str = Field(min_length=8, max_length=120)
    action: Literal["pause", "resume", "repair", "retry", "revoke"]


class SettingsCapabilityRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=120)
    action: Literal["request", "cancel"]
    requested_scope: Literal["member_private", "household_shared"] = "member_private"
    provider_option: str | None = Field(default=None, min_length=1, max_length=200)


class PromotionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    audience_member_ids: list[str] = Field(min_length=1, max_length=20)


class TemporaryActionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    verb: Literal[
        "task.complete",
        "task.defer",
        "task.snooze",
        "task.block",
        "task.unblock",
        "task.cancel",
        "task.reopen",
    ]
    item_id: str = Field(min_length=1, max_length=160)
    expected_last_event_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=120)
    until: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class MemberLifecycleRequest(BaseModel):
    model_config = {"extra": "forbid"}
    item_kind: Literal["task", "commitment", "recurrence", "routine"]
    item_id: str = Field(min_length=1, max_length=160)
    action: Literal[
        "complete",
        "defer",
        "snooze",
        "block",
        "unblock",
        "cancel",
        "reopen",
        "pause",
        "resume",
        "end",
        "delegate",
    ]
    expected_last_event_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=120)
    until: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)
    delegate_to_party_id: str | None = Field(default=None, min_length=1)


class FinanceGoalLifecycleRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal["pause", "complete", "reopen"]
    reason: str | None = Field(default=None, max_length=500)


class RecurrenceDefinitionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str | None = Field(default=None, min_length=1)
    scope: Literal["member", "household"] = "member"
    kind: str = Field(min_length=1, max_length=160)
    rrule: str = Field(min_length=1, max_length=1000)
    next_occurrence: str = Field(min_length=1, max_length=80)
    lead_time_days: int = Field(default=0, ge=0, le=365)
    trackable: bool = False
    notes: str | None = Field(default=None, max_length=5000)


class RoutineStepRequest(BaseModel):
    model_config = {"extra": "forbid"}
    step_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    position: int = Field(ge=0, le=49)
    micro_step: str | None = Field(default=None, max_length=500)
    linked_task_id: str | None = Field(default=None, max_length=160)


class RoutineDefinitionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1, max_length=500)
    timezone: str = Field(min_length=1, max_length=100)
    starts_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    rrule: str = Field(min_length=1, max_length=1000)
    window_start_local: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    window_end_local: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    steps: list[RoutineStepRequest] = Field(min_length=1, max_length=50)


class CalendarOutcomeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1)
    outcome: Literal["attended", "missed"]


class CalendarRuleRequest(BaseModel):
    """A principal-approved interpretation of one observed calendar."""

    model_config = {"extra": "forbid"}
    source_member_id: str = Field(min_length=1, max_length=240)
    provider: Literal["google_calendar", "apple_calendar"]
    provider_account_id: str = Field(min_length=1, max_length=500)
    calendar_external_id: str = Field(min_length=1, max_length=1000)
    purpose: Literal[
        "personal",
        "work",
        "household",
        "coparenting",
        "property",
        "family_context",
        "holidays",
        "test",
        "other",
    ]
    effect: Literal["hard_block", "soft_hold", "context", "ignore"]
    tentative_effect: Literal["hard_block", "soft_hold", "context", "ignore"] = (
        "soft_hold"
    )
    caregiving_load: Literal["hard_block", "soft_hold", "context", "ignore"] = "ignore"
    caregiving_summary_values: list[str] = Field(default_factory=list, max_length=20)
    buffer_kind: Literal[
        "auto",
        "none",
        "remote",
        "appointment",
        "medical",
        "handoff",
        "deep_work",
    ] = "auto"
    write_target: bool = False
    destination_kind: (
        Literal["personal", "work", "household", "coparenting", "property"] | None
    ) = None
    enabled: bool = True


class CalendarOnboardingRequest(BaseModel):
    """Complete deterministic calendar policy for one household member."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    member_id: str = Field(min_length=1, max_length=240)
    timezone: str = Field(min_length=1, max_length=240)
    rules: list[CalendarRuleRequest] = Field(min_length=1, max_length=80)
    windows: list[ScheduleWindowV1] = Field(default_factory=list, max_length=40)
    buffer_minutes: dict[str, int] = Field(default_factory=dict)
    accepted_chores_required: bool = True
    one_confirm_write: bool = True


class RoutineStepOutcomeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1)
    outcome: Literal["completed", "skipped"]


class CommsCommandRequest(BaseModel):
    """One explicit member decision against a visible communication."""

    model_config = {"extra": "forbid"}
    operation: Literal[
        "reply",
        "mark_read",
        "mark_unread",
        "archive",
        "restore",
        "snooze",
        "unsnooze",
        "quiet",
        "unquiet",
    ]
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    request_id: str = Field(min_length=8, max_length=120)
    body: str | None = Field(default=None, max_length=20_000)
    snoozed_until: str | None = None


class CommsReplyCancelRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)


class ReminderCommandRequest(BaseModel):
    """One explicit member mutation of a provider-owned reminder/list."""

    model_config = {"extra": "forbid"}
    provider: Literal["apple_reminders", "google_tasks"]
    provider_account_id: str = Field(min_length=1, max_length=500)
    operation: Literal[
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "create_task",
        "update_task",
        "delete_task",
        "complete_task",
        "uncomplete_task",
        "move_task",
        "clear_completed_tasks",
        "create_list",
        "update_list",
        "delete_list",
    ]
    list_external_id: str | None = Field(default=None, max_length=1000)
    item_external_id: str | None = Field(default=None, max_length=1000)
    expected_last_event_id: str | None = Field(default=None, max_length=240)
    expected_provider_revision: str | None = Field(default=None, max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(min_length=8, max_length=120)


class ContactIdentifierCommandRequest(BaseModel):
    """One sparse, revision-guarded correction to an observed contact endpoint."""

    model_config = {"extra": "forbid"}
    provider_account_id: str = Field(min_length=1, max_length=500)
    container_external_id: str = Field(min_length=1, max_length=1000)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    expected_provider_revision: str = Field(min_length=1, max_length=2000)
    value: str = Field(min_length=1, max_length=2000)
    label: str | None = Field(default=None, max_length=240)
    request_id: str = Field(min_length=8, max_length=120)


class ReminderCueRequest(BaseModel):
    """A typed comparison against one scoped context signal."""

    model_config = {"extra": "forbid"}
    cue_id: str = Field(min_length=8, max_length=240)
    list_external_id: str = Field(min_length=1, max_length=1000)
    item_external_id: str = Field(min_length=1, max_length=1000)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    signal_key: str = Field(min_length=1, max_length=500)
    source: ContextSource
    kind: ContextKind
    operator: CueOperator
    expected_state: str | None = Field(default=None, max_length=500)
    expected_number: float | None = None
    enabled: bool = True


TaskItemType = Literal[
    "task",
    "purchase",
    "appointment",
    "research",
    "decision",
    "chore",
    "maintenance",
]


class MemberTaskCreateRequest(BaseModel):
    """Explicit member-authored canonical work; no model interpretation."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    title: str = Field(min_length=1, max_length=1000)
    notes: str | None = Field(default=None, max_length=20_000)
    due_date: str | None = Field(default=None, max_length=80)
    available_from: str | None = Field(default=None, max_length=80)
    energy: Literal["low", "medium", "high"] | None = None
    effort: Literal["tiny", "small", "medium", "large"] | None = None
    domain: str = Field(default="tasks", min_length=1, max_length=120)
    item_type: TaskItemType = "task"
    micro_script: str | None = Field(default=None, max_length=2000)


class MemberTaskUpdateRequest(BaseModel):
    """Closed task correction contract for the member portal."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    notes: str | None = Field(default=None, max_length=20_000)
    due_date: str | None = Field(default=None, max_length=80)
    available_from: str | None = Field(default=None, max_length=80)
    energy: Literal["low", "medium", "high"] | None = None
    effort: Literal["tiny", "small", "medium", "large"] | None = None
    domain: str | None = Field(default=None, min_length=1, max_length=120)
    item_type: TaskItemType | None = None
    micro_script: str | None = Field(default=None, max_length=2000)


def _validated_triage_candidate_updates(
    target_kind: str,
    updates: dict[str, Any],
    *,
    timezone_name: str,
    provider: str | None = None,
) -> dict[str, Any]:
    """Validate only member-editable fields for the chosen canonical type."""

    allowed = candidate_update_fields(target_kind, provider=provider)
    if not allowed:
        raise HTTPException(422, "unsupported triage target")
    unexpected = set(updates) - allowed
    if unexpected:
        raise HTTPException(
            422,
            f"unsupported {target_kind} enrichment fields: {sorted(unexpected)}",
        )
    values = dict(updates)
    for field_name in {
        "due",
        "due_date",
        "due_at",
        "available_from",
        "start",
        "end",
        "next_occurrence",
        "effective_date",
        "since",
    } & values.keys():
        value = values[field_name]
        if value is None or value == "":
            values[field_name] = None
        elif not isinstance(value, str):
            raise HTTPException(422, f"{field_name} must be a date or date and time")
        else:
            values[field_name] = _normalized_task_due(
                value, timezone_name=timezone_name
            )
    if "energy" in values and values["energy"] not in {None, "low", "medium", "high"}:
        raise HTTPException(422, "energy must be low, medium, or high")
    if "effort" in values and values["effort"] not in {
        None,
        "tiny",
        "small",
        "medium",
        "large",
    }:
        raise HTTPException(422, "effort must be tiny, small, medium, or large")
    if "item_type" in values and values["item_type"] not in {
        "task",
        "purchase",
        "appointment",
        "research",
        "decision",
        "chore",
        "maintenance",
    }:
        raise HTTPException(422, "unsupported task type")
    if "effort_min" in values and (
        not isinstance(values["effort_min"], int) or values["effort_min"] < 0
    ):
        raise HTTPException(422, "effort minutes must be a positive whole number")
    if "priority" in values and values["priority"] not in {0, 1, 5, 9}:
        raise HTTPException(422, "reminder priority must be none, high, medium, or low")
    for list_field in {"alarms", "recurrence_rules"} & values.keys():
        if not isinstance(values[list_field], list) or len(values[list_field]) > 20:
            raise HTTPException(422, f"{list_field} must be a short list")
    return values


class TriageEnrichmentRequest(BaseModel):
    """Member corrections applied before a candidate can be confirmed."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    target_kind: TriageTargetKind | None = None
    candidate_updates: dict[str, Any] = Field(default_factory=dict)
    route: TriageRouteV1 | None = None


class TriageContextCorrectionRequest(BaseModel):
    """Exact, reversible context corrections selected by the member."""

    model_config = {"extra": "forbid"}
    unrelated_source_event_ids: list[str] = Field(default_factory=list, max_length=10)
    quoted_or_forwarded_history: bool = False


class TriageDecisionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)
    feedback_reason: (
        Literal[
            "not_mine",
            "not_actionable",
            "already_done",
            "duplicate",
            "wrong_kind",
            "wrong_context",
            "wrong_owner",
            "recurring_normal",
            "low_signal",
            "spam",
            "provider_promotion",
            "other",
        ]
        | None
    ) = None
    context_correction: TriageContextCorrectionRequest | None = None


class ContextRelationshipConfirmRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    source_event_id: str = Field(min_length=1, max_length=240)
    related_source_event_id: str = Field(min_length=1, max_length=240)


class ContextSourceTextReinstateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    source_event_id: str = Field(min_length=1, max_length=240)
    correction_id: str = Field(min_length=1, max_length=240)


class TriagePolicyPreviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    scope_kind: Literal["candidate_kind", "sender", "context", "account", "workspace"]
    scope_value: str = Field(min_length=1, max_length=500)
    disposition: Literal["quiet", "always_surface", "route_to_low_signal"]


class TriagePolicyApproveRequest(TriagePolicyPreviewRequest):
    request_id: str = Field(min_length=8, max_length=120)
    expected_affected_count: int = Field(ge=0)


class TriagePolicyRevokeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    reason: str = Field(min_length=1, max_length=240)


class TriageManagementRequest(BaseModel):
    """One exact reversible action on an owned Triage candidate."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal[
        "link_existing",
        "mark_handled",
        "mark_duplicate",
        "keep_context",
        "route_alert",
        "dismiss_not_useful",
        "propose_suppression",
        "ask_clarification",
        "snooze",
        "reopen",
        "correct_extraction",
        "correct_ownership",
    ]
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=100)
    linked_object_kind: str | None = Field(default=None, max_length=120)
    linked_object_id: str | None = Field(default=None, max_length=240)
    duplicate_of_proposal_id: str | None = Field(default=None, max_length=240)
    snoozed_until: datetime | None = None
    corrected_title: str | None = Field(default=None, min_length=1, max_length=500)
    corrected_target_kind: str | None = Field(default=None, max_length=120)
    corrected_owner_member_id: str | None = Field(default=None, max_length=240)
    policy_proposal_id: str | None = Field(default=None, max_length=240)


TriagePolicyInstruction = Literal["always", "usually", "ask", "never"]
TriagePolicyOutcome = Literal[
    "dismiss_not_useful",
    "keep_context",
    "mark_duplicate",
    "correct_extraction",
    "correct_ownership",
    "reopen",
]


class TriageLearningPreviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    scope_kind: Literal["candidate_kind", "sender", "context", "account", "workspace"]
    scope_value: str = Field(min_length=1, max_length=500)
    instruction: TriagePolicyInstruction
    outcome_kind: TriagePolicyOutcome


class TriageLearningProposeRequest(TriageLearningPreviewRequest):
    request_id: str = Field(min_length=8, max_length=120)
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class TriageLearningDecisionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    decision: Literal["approve", "edit", "trial", "reject", "suppress"]
    instruction: TriagePolicyInstruction
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_proposal_ids: list[str] = Field(default_factory=list, max_length=100)


class TriageLearningReviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    decision: Literal["retain", "narrow", "suspend", "revoke"]
    negative_outcome: (
        Literal[
            "false_suppression",
            "missed_obligation",
            "member_correction",
            "scope_mismatch",
        ]
        | None
    ) = None
    replacement_instruction: TriagePolicyInstruction | None = None
    reason: str = Field(min_length=1, max_length=500)


class TriageIntentRequest(BaseModel):
    """Zero-tax exact text capture; everything else is optional."""

    model_config = {"extra": "forbid"}
    text: str = Field(min_length=1, max_length=32_000)
    source_surface: TriageSourceSurface
    kind_hint: TriageTargetKind | None = None
    source_event_id: str | None = Field(default=None, min_length=1, max_length=240)
    client_request_id: str = Field(min_length=8, max_length=120)


class KnowledgeGrantRequest(BaseModel):
    """One explicit owner decision to expose one note to one adult member."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    grantee_member_id: str = Field(min_length=1, max_length=240)
    permissions: list[KnowledgeGrantPermission] = Field(min_length=1, max_length=3)
    expires_at: datetime | None = None


class KnowledgeGrantRevokeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)


class PeopleProfileRequest(BaseModel):
    """Member-owned relationship cue policy; never inferred by a model."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str | None = Field(default=None, max_length=240)
    circle: str | None = Field(default=None, max_length=240)
    desired_cadence_days: int | None = Field(default=None, ge=1, le=3650)
    cues_enabled: bool = False
    favorite: bool = False
    suppressed: bool = False
    suppression_reason: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=50)


class PeopleLifecycleRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal["archive", "restore"]
    reason: str | None = Field(default=None, max_length=500)


class RegistryLifecycleRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal["archive", "restore"]
    reason: str | None = Field(default=None, max_length=500)


def _runtime(request: Request) -> Any:
    return request.app.state.runtime


def _require_self_settings(session: Session) -> None:
    if session.auth_role in {"child", "device", "ambient"}:
        raise HTTPException(403, "Settings changes require an authenticated adult")
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "Settings are read-only while viewing another member")


def _refresh_bridge_ingress(request: Request) -> None:
    runtime = _runtime(request)
    resolver = EnrollmentMemberIdResolver(runtime.settings.bridge_enrollments)
    runtime.member_id_resolver = resolver
    request.app.state.member_id_resolver = resolver
    request.app.state.bridge_enrollments = runtime.settings.bridge_enrollments


def _bridge_http_error(exc: BridgeEnrollmentError) -> HTTPException:
    status_code = 410 if exc.code == "expired" else 409
    return HTTPException(status_code, {"error": exc.code, "message": str(exc)})


def _require_bridge_acquisition(
    runtime: Any, capabilities: Sequence[str], *, member_id: str, request_id: str
) -> None:
    ceiling = member_bridge_capability_ceiling(runtime.settings.adapter_config)
    if ceiling is not None and (
        not ceiling
        or not set(capabilities) <= ceiling
        or runtime.settings.member_roles.get(member_id) != "principal"
    ):
        raise HTTPException(
            409,
            {
                "error": "enrollment_not_started",
                "message": (
                    "An adult can connect their own Calendar, Reminders and Notes for read-only use."
                    if ceiling
                    else "Member bridge setup is paused for the current data configuration."
                ),
                "request_id": request_id,
                "request_recorded": False,
            },
        )


def _settings_central_url(request: Request, *, allow_testserver: bool) -> str:
    host = request.headers.get("host", "").strip()
    if not host:
        raise HTTPException(400, "request host is missing")
    hostname = host.rsplit(":", 1)[0]
    if allow_testserver and hostname in {"testserver", "localhost"}:
        return "https://fixture-central.ts.net"
    if not allow_testserver and not hostname.endswith(".ts.net"):
        raise HTTPException(
            403, "bridge handoff requires the private Tailscale Serve origin"
        )
    return f"https://{host}"


async def _record_central_bridge_progress(
    runtime: Any,
    session: Session,
    *,
    request_id: str,
    member_id: str,
    previous_state: str,
    state: str,
    suffix: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    revision = runtime.settings_control.prepare_bridge_progress(
        member_id, request_id=request_id
    )
    payload = {
        "request_id": request_id,
        "enrollment_id": None,
        "member_id": member_id,
        "previous_state": previous_state,
        "state": state,
        "revision": revision,
        "progress_at": EventEnvelope.now_utc_iso(),
        "reported_by": "central_runtime",
        "signature_verified": True,
        "device_identity_sha256": None,
        "capability_id": None,
        "capability_state": None,
        "reason": None,
        "last_successful_observation": None,
        "content_exposed": False,
        "writes_authorized": False,
        "provider_effects": False,
        **(extra or {}),
    }
    fingerprint = _settings_request_fingerprint("bridge.enrollment_progressed", payload)
    await _write_settings_receipt(
        runtime,
        session,
        event_type="bridge.enrollment_progressed",
        request_id=f"{request_id}:{suffix}",
        request_fingerprint=fingerprint,
        owner_member_id=member_id,
        payload=payload,
    )
    mutation = runtime.settings_control.apply_bridge_enrollment_event(payload)
    return mutation.current


def _require_owned_source_coordinate(
    session: Session, coordinate: SourceCoordinate
) -> None:
    if coordinate.tenant_id != session.tenant_id:
        raise HTTPException(403, "source policy belongs to another tenant")
    if (
        coordinate.owner_kind != "member"
        or coordinate.owner_principal_id != session.auth_member_id
        or coordinate.context_member_id != session.auth_member_id
    ):
        raise HTTPException(403, "an adult may manage only their own source policy")
    if not coordinate.exact:
        raise HTTPException(
            422, "source policy requires an exact account and adapter coordinate"
        )


def _resolved_source_policy(
    preset: str,
    policy: SourcePolicyV2 | None,
    *,
    current_policy: SourcePolicyV2 | None = None,
) -> SourcePolicyV2:
    if preset == "custom":
        if policy is None or policy.preset != "custom":
            raise HTTPException(422, "custom source policy requires bounded selections")
        return policy
    if policy is not None:
        raise HTTPException(422, "preset source policy cannot include custom controls")
    try:
        return policy_for_preset(preset, current_policy=current_policy)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _require_observed_source_coordinate(
    runtime: Any, session: Session, coordinate: SourceCoordinate
) -> None:
    sources = compose_source_policy_v2_sources(
        runtime, session.auth_member_id, state=runtime.settings_control.read()
    )
    if not any(row.get("policy_key") == coordinate.key for row in sources):
        raise HTTPException(
            422,
            "source must be configured or attributed from retained evidence before setting a policy",
        )


async def _write_settings_receipt(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    request_fingerprint: str,
    owner_member_id: str,
    schema_version: int = 1,
    sensitivity: Sensitivity = "normal",
) -> str:
    if runtime.events is None:
        raise HTTPException(503, "Settings audit log is unavailable")
    now = EventEnvelope.now_utc_iso()
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type=event_type,
            schema_version=schema_version,
            occurred_at=now,
            source_adapter="member-settings-control-plane",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{owner_member_id}",
            visibility_scope=f"private:{owner_member_id}",
            sensitivity=sensitivity,
            correlation_id=session.correlation_id,
            actor_identity=session.auth_member_id,
            idempotency_key=(
                f"member-settings:{event_type}:{session.auth_member_id}:{request_id}"
            ),
            raw_ref=f"request-sha256:{request_fingerprint}",
            payload=payload,
        )
    )


def _settings_request_fingerprint(event_type: str, values: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"event_type": event_type, "values": values},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


async def _prior_settings_receipt(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    request_id: str,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    if runtime.event_log is None:
        raise HTTPException(503, "Settings audit log is unavailable")
    key = f"member-settings:{event_type}:{session.auth_member_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior.get("raw_ref") != f"request-sha256:{request_fingerprint}":
        raise HTTPException(409, "request id conflicts with a prior Settings command")
    return prior


_EXTERNAL_OPERATIONS = {
    "google_calendar": {
        "create_event",
        "update_event",
        "delete_event",
        "move_event",
        "respond_event",
    },
    "google_tasks": {
        "create_task",
        "update_task",
        "delete_task",
        "complete_task",
        "uncomplete_task",
        "move_task",
        "clear_completed_tasks",
        "create_list",
        "update_list",
        "delete_list",
    },
    "apple_calendar": {"create_event", "update_event", "delete_event", "move_event"},
    "apple_reminders": {
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "create_list",
        "update_list",
        "delete_list",
    },
}
_COMMS_EXTERNAL_OPERATIONS = {
    "gmail": {"reply", "mark_read", "mark_unread", "archive", "restore"},
    "microsoft_graph": {"reply", "mark_read", "mark_unread", "archive", "restore"},
    "twilio_sms": {"reply"},
    "twilio_whatsapp": {"reply"},
    "fixture": {"reply"},
    "bluebubbles": {"reply"},
}

_REMINDER_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "create_reminder": frozenset(
        {
            "title",
            "notes",
            "url",
            "priority",
            "start",
            "due",
            "alarms",
            "recurrence_rules",
        }
    ),
    "update_reminder": frozenset(
        {
            "title",
            "notes",
            "url",
            "priority",
            "start",
            "due",
            "alarms",
            "recurrence_rules",
        }
    ),
    "delete_reminder": frozenset(),
    "complete_reminder": frozenset(),
    "uncomplete_reminder": frozenset(),
    "move_reminder": frozenset({"destination_list_external_id"}),
    "create_task": frozenset({"title", "notes", "due", "parent", "previous"}),
    "update_task": frozenset({"title", "notes", "due"}),
    "delete_task": frozenset(),
    "complete_task": frozenset(),
    "uncomplete_task": frozenset(),
    "move_task": frozenset({"destination_list_external_id", "parent", "previous"}),
    "clear_completed_tasks": frozenset(),
    "create_list": frozenset({"title", "name", "color"}),
    "update_list": frozenset({"title", "name", "color"}),
    "delete_list": frozenset(),
}


async def _append_provider_action_confirmation(
    runtime: Any,
    session: Session,
    *,
    command_payload: dict[str, Any],
    request_id: str,
    request_fingerprint: str,
    source_adapter: str,
    sensitivity: Sensitivity,
    causation_id: str | None,
    origin_proposal_id: str | None = None,
) -> tuple[str, bool]:
    """Record the member decision without creating an external command."""

    key = f"provider-action:{session.auth_member_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is not None:
        if prior.get("raw_ref") != f"request-sha256:{request_fingerprint}":
            raise HTTPException(
                409, "request id conflicts with a prior provider action"
            )
        return str(prior["event_id"]), True
    from adminme.apple_service_permissions import bind_command_permission

    try:
        bind_command_permission(runtime, session.auth_member_id, command_payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    now = EventEnvelope.now_utc_iso()
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="member.provider_action.confirmed",
            schema_version=1,
            occurred_at=now,
            source_adapter=source_adapter,
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity=sensitivity,
            correlation_id=session.correlation_id,
            causation_id=causation_id,
            actor_identity=session.auth_member_id,
            idempotency_key=key,
            raw_ref=f"request-sha256:{request_fingerprint}",
            payload={
                "action_id": request_id,
                "member_id": session.auth_member_id,
                "provider": command_payload["provider"],
                "provider_account_id": command_payload["provider_account_id"],
                "operation": command_payload["operation"],
                "target_external_id": command_payload.get("target_external_id"),
                "expected_provider_revision": command_payload.get(
                    "expected_provider_revision"
                ),
                "command_payload": dict(command_payload.get("payload") or {}),
                "request_fingerprint": request_fingerprint,
                "confirmed_at": now,
                "origin_proposal_id": origin_proposal_id,
            },
        )
    )
    return event_id, False


def _owned_triage_item(
    runtime: Any, session: Session, proposal_id: str
) -> dict[str, Any]:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "Triage decisions are unavailable while viewing another member"
        )
    if runtime.projections is None:
        raise HTTPException(503, "triage projection unavailable")
    item = get_triage_item(
        runtime.projections.connection("triage"), session, proposal_id=proposal_id
    )
    if item is None or item["owner_member_id"] != session.auth_member_id:
        raise HTTPException(404, "triage item not found")
    return item


def _owned_reviewable_triage_item(
    runtime: Any,
    session: Session,
    proposal_id: str,
) -> dict[str, Any]:
    """Return current owned proposal truth plus the member review contract."""

    item = _owned_triage_item(runtime, session, proposal_id)
    item["review_state"] = _triage_review_state(
        item,
        owed_to_party_available=_triage_owed_to_party_available(runtime, session, item),
    )
    return item


def _require_current_triage(
    item: dict[str, Any],
    expected_last_event_id: str,
    *,
    allow_pending_interpretation: bool = False,
) -> None:
    allowed_states = {"pending_review"}
    if allow_pending_interpretation:
        allowed_states.add("pending_interpretation")
    if item["state"] not in allowed_states:
        raise HTTPException(409, "triage item is no longer awaiting review")
    if item["last_event_id"] != expected_last_event_id:
        raise HTTPException(409, "triage item changed; refresh before deciding")


def _triage_event(
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    request_fingerprint: str,
    causation_id: str | None,
    sensitivity: Sensitivity = "normal",
    schema_version: int = 1,
) -> EventEnvelope:
    now = EventEnvelope.now_utc_iso()
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=schema_version,
        occurred_at=now,
        source_adapter="member-portal",
        source_account_id=session.auth_member_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity=sensitivity,
        correlation_id=session.correlation_id,
        causation_id=causation_id,
        actor_identity=session.auth_member_id,
        idempotency_key=f"triage:{event_type}:{session.auth_member_id}:{request_id}",
        raw_ref=f"request-sha256:{request_fingerprint}",
        payload=payload,
    )


def _triage_request_fingerprint(proposal_id: str, body: BaseModel) -> str:
    canonical = json.dumps(
        {"proposal_id": proposal_id, "body": body.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _triage_state_fingerprint(item: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "proposal_id": item["proposal_id"],
            "target_kind": item["target_kind"],
            "owner_member_id": item["owner_member_id"],
            "title": item["title"],
            "candidate": item.get("candidate") or {},
            "route": item.get("route"),
            "sensitivity": item.get("sensitivity") or "normal",
            "last_event_id": item["last_event_id"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _triage_candidate_fingerprint(item: dict[str, Any]) -> str:
    """Stable candidate identity that survives approval-state transitions."""

    canonical = json.dumps(
        {
            "proposal_id": item["proposal_id"],
            "target_kind": item["target_kind"],
            "owner_member_id": item["owner_member_id"],
            "title": item["title"],
            "candidate": item.get("candidate") or {},
            "route": item.get("route"),
            "sensitivity": item.get("sensitivity") or "normal",
            "source_event_id": item["source_event_id"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _prior_triage_command(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    request_id: str,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    if runtime.event_log is None:
        raise HTTPException(503, "event log unavailable")
    key = f"triage:{event_type}:{session.auth_member_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior["raw_ref"] != f"request-sha256:{request_fingerprint}":
        raise HTTPException(409, "idempotency key conflicts with prior decision body")
    return prior


async def _guard_triage_write(
    runtime: Any, session: Session, action: str, payload: dict[str, Any]
) -> None:
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(session, action, payload)
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )


def _context_relationship_id(
    tenant_id: str, member_id: str, first_event_id: str, second_event_id: str
) -> str:
    first, second = sorted((first_event_id, second_event_id))
    digest = hashlib.sha256(
        f"{tenant_id}|{member_id}|{first}|{second}".encode()
    ).hexdigest()[:32]
    return f"context_relationship_{digest}"


def _context_source_correction_id(
    tenant_id: str,
    member_id: str,
    source_event_id: str,
    proposal_id: str,
    candidate_key: str,
    evidence_quote_digest: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{tenant_id}|{member_id}|{source_event_id}|{proposal_id}|"
            f"{candidate_key}|{evidence_quote_digest}"
        ).encode()
    ).hexdigest()[:32]
    return f"context_source_text_{digest}"


async def _owned_message_source(
    runtime: Any, session: Session, source_event_id: str
) -> dict[str, Any]:
    """Resolve one exact member-owned message without widening its scope."""

    if runtime.event_log is None:
        raise HTTPException(503, "event log unavailable")
    source = await runtime.event_log.get(source_event_id)
    expected_scope = f"private:{session.auth_member_id}"
    if (
        not isinstance(source, dict)
        or source.get("tenant_id") != session.tenant_id
        or source.get("type")
        not in {"messaging.received", "messaging.sent", "telephony.sms_received"}
        or source.get("owner_scope") != expected_scope
        or source.get("visibility_scope") != expected_scope
        or not allowed_read(
            session,
            str(source.get("sensitivity") or "normal"),
            str(source.get("visibility_scope") or ""),
        )
    ):
        raise HTTPException(404, "context source not found")
    return source


async def _owned_active_source_text_correction(
    runtime: Any,
    session: Session,
    correction_id: str,
    source_event_id: str,
) -> dict[str, Any]:
    """Resolve the latest exact correction state without trusting the request."""

    if runtime.event_log is None:
        raise HTTPException(503, "event log unavailable")
    latest: dict[str, Any] | None = None
    async for event in runtime.event_log.read_since(
        owner_scope=f"private:{session.auth_member_id}",
        types=["context.source_text.corrected", "context.source_text.reinstated"],
    ):
        payload = event.get("payload") or {}
        if (
            event.get("tenant_id") == session.tenant_id
            and payload.get("member_id") == session.auth_member_id
            and payload.get("correction_id") == correction_id
        ):
            latest = event
    if latest is None:
        raise HTTPException(404, "source-text correction not found")
    payload = latest.get("payload") or {}
    if payload.get("source_event_id") != source_event_id:
        raise HTTPException(422, "source-text correction does not match source")
    if latest.get("type") != "context.source_text.corrected":
        raise HTTPException(409, "source-text correction is already reinstated")
    return latest


def _require_same_context_coordinate(
    source: dict[str, Any], related: dict[str, Any]
) -> tuple[str, str, Literal["personal", "work"]]:
    source_payload = source.get("payload") or {}
    related_payload = related.get("payload") or {}
    provider = str(source_payload.get("provider") or source.get("source_adapter") or "")
    related_provider = str(
        related_payload.get("provider") or related.get("source_adapter") or ""
    )
    provider_account_id = str(
        source_payload.get("provider_account_id")
        or source.get("source_account_id")
        or ""
    )
    related_account_id = str(
        related_payload.get("provider_account_id")
        or related.get("source_account_id")
        or ""
    )
    workspace = source_payload.get("workspace")
    related_workspace = related_payload.get("workspace")
    if (
        not provider
        or provider != related_provider
        or not provider_account_id
        or provider_account_id != related_account_id
        or workspace not in {"personal", "work"}
        or workspace != related_workspace
    ):
        raise HTTPException(422, "context sources must share one provider coordinate")
    return provider, provider_account_id, cast(Literal["personal", "work"], workspace)


def _target_member(runtime: Any, item: dict[str, Any]) -> str:
    route = item.get("route") or {}
    member_id = str(route.get("member_id") or item["owner_member_id"])
    if member_id not in runtime.settings.member_roles:
        raise HTTPException(422, "route must name a configured household member")
    if runtime.settings.member_roles.get(member_id) == "device":
        raise HTTPException(422, "a device cannot own household truth")
    if member_id != item["owner_member_id"] and item.get("sensitivity") != "normal":
        raise HTTPException(
            403, "sensitive candidates cannot be rerouted to another member"
        )
    if (
        runtime.settings.member_roles.get(member_id) == "child"
        and member_id != item["owner_member_id"]
        and not (item.get("candidate") or {}).get("child_safe")
    ):
        raise HTTPException(
            403, "child routing requires an explicit child-safe classification"
        )
    return member_id


def _require_configured_external_route(
    runtime: Any,
    *,
    provider: str,
    target_member: str,
    provider_account_id: str,
    container_external_id: str | None,
) -> None:
    if provider in {"google_calendar", "google_tasks"}:
        expected_fragment = (
            "google-calendar" if provider == "google_calendar" else "google-tasks"
        )
        container_key = (
            "calendar_id" if provider == "google_calendar" else "tasklist_id"
        )
        for record in runtime.settings.adapter_config.get("adapters", []):
            if not isinstance(record, dict):
                continue
            connection_id = str(record.get("id") or "")
            if (
                connection_id
                and runtime.settings_control.connection_lifecycle(connection_id)
                == "paused"
            ):
                continue
            adapter_id = str(
                record.get("id")
                or record.get("kind")
                or record.get("name")
                or record.get("pack_id")
                or ""
            )
            raw_config = record.get("config")
            config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
            if (
                expected_fragment in adapter_id
                and config.get("member_id") == target_member
                and (config.get("provider_account_id") or config.get("user_email"))
                == provider_account_id
                and (
                    config.get(container_key) == container_external_id
                    or (
                        provider == "google_calendar"
                        and bool(config.get("discover_calendars"))
                        and container_external_id
                        not in set(config.get("exclude_calendar_ids") or [])
                        and (
                            not config.get("include_calendar_ids")
                            or container_external_id
                            in set(config.get("include_calendar_ids") or [])
                        )
                    )
                )
            ):
                if provider == "google_calendar" and config.get("discover_calendars"):
                    if runtime.projections is None:
                        raise HTTPException(503, "calendar projection unavailable")
                    row = (
                        runtime.projections.connection("calendars")
                        .execute(
                            """
                        SELECT 1 FROM calendar_member_rules r
                        JOIN calendar_sources s
                          ON s.tenant_id=r.tenant_id
                         AND s.provider=r.provider
                         AND s.provider_account_id=r.provider_account_id
                         AND s.calendar_external_id=r.calendar_external_id
                        WHERE r.tenant_id=? AND r.subject_member_id=?
                          AND r.provider='google_calendar'
                          AND r.provider_account_id=?
                          AND r.calendar_external_id=?
                          AND r.enabled=1 AND r.write_target=1 AND s.write_events=1
                        """,
                            (
                                runtime.settings.instance.tenant_id,
                                target_member,
                                provider_account_id,
                                container_external_id,
                            ),
                        )
                        .fetchone()
                    )
                    if row is None:
                        continue
                return
        raise HTTPException(
            422, "the Google provider route is not configured for this member"
        )
    if provider in {"apple_calendar", "apple_reminders", "apple_contacts"}:
        from adminme.apple_service_permissions import service_permission

        grant = service_permission(runtime, target_member, provider)
        if grant is not None and (
            grant["provider_account_id"] == provider_account_id
            and container_external_id in grant["allowed_container_ids"]
        ):
            return
        matches = []
        for record in runtime.settings.bridge_enrollments.values():
            if not isinstance(record, dict):
                continue
            authorization = authorization_from_record(record)
            if authorization is None or authorization.member_id != target_member:
                continue
            policy = authorization.policy_for_provider(provider)
            if policy is None or policy.container_key is None:
                continue
            config = authorization.pack_config.get(policy.pack, {})
            if (
                authorization.provider_account_for(policy.pack) == provider_account_id
                and config.get(policy.container_key) == container_external_id
            ):
                matches.append((authorization, policy))
        if len(matches) != 1:
            raise HTTPException(
                422,
                "the member's exact attested Apple provider route is unavailable",
            )
        return
    raise HTTPException(422, "the external route is not a configured writable adapter")


def _require_reviewed_calendar_destination(
    runtime: Any,
    *,
    target_member: str,
    provider: str,
    provider_account_id: str,
    calendar_external_id: str,
) -> None:
    """Fail closed unless provider capability and member policy approve the route."""

    # The explicit owner-reviewed Apple destination grant also covers an empty
    # calendar; an event observation must not be required before its first write.
    if provider == "apple_calendar":
        from adminme.apple_service_permissions import service_permission

        grant = service_permission(runtime, target_member, provider)
        if grant is not None and (
            grant["provider_account_id"] == provider_account_id
            and calendar_external_id in grant["allowed_container_ids"]
        ):
            return
    if runtime.projections is None:
        raise HTTPException(503, "calendar projection unavailable")
    row = (
        runtime.projections.connection("calendars")
        .execute(
            """
        SELECT 1
          FROM calendar_member_rules r
          JOIN calendar_sources s
            ON s.tenant_id=r.tenant_id
           AND s.member_id=r.source_member_id
           AND s.provider=r.provider
           AND s.provider_account_id=r.provider_account_id
           AND s.calendar_external_id=r.calendar_external_id
         WHERE r.tenant_id=? AND r.subject_member_id=?
           AND r.provider=? AND r.provider_account_id=?
           AND r.calendar_external_id=?
           AND r.enabled=1 AND r.write_target=1 AND s.write_events=1
         LIMIT 1
        """,
            (
                runtime.settings.instance.tenant_id,
                target_member,
                provider,
                provider_account_id,
                calendar_external_id,
            ),
        )
        .fetchone()
    )
    if row is None:
        raise HTTPException(
            422, "calendar route is not a reviewed writable destination"
        )


def _materialization_envelopes(
    runtime: Any,
    session: Session,
    item: dict[str, Any],
    *,
    request_id: str,
    causation_id: str | None = None,
) -> list[EventEnvelope]:
    """Translate a reviewed candidate through a closed, typed allowlist."""

    now = EventEnvelope.now_utc_iso()
    candidate = dict(item.get("candidate") or {})
    title = str(item.get("title") or candidate.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "a confirmed candidate requires a title")
    target_kind = item["target_kind"]
    target_member = _target_member(runtime, item)
    common: dict[str, Any] = {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": session.tenant_id,
        "occurred_at": now,
        "source_adapter": "member-portal",
        "source_account_id": session.auth_member_id,
        "owner_scope": f"private:{target_member}",
        "visibility_scope": f"private:{target_member}",
        "sensitivity": item.get("sensitivity") or "normal",
        "correlation_id": session.correlation_id,
        "causation_id": causation_id or item["last_event_id"],
        "actor_identity": session.auth_member_id,
        "raw_ref": f"triage-candidate-sha256:{_triage_candidate_fingerprint(item)}",
    }
    if target_kind == "task":
        due_date = (
            candidate["due_date"] if "due_date" in candidate else candidate.get("due")
        )
        notes = (
            candidate["notes"] if "notes" in candidate else candidate.get("description")
        )
        effort = candidate.get("effort")
        if effort is None and isinstance(candidate.get("effort_min"), int):
            minutes = int(candidate["effort_min"])
            effort = (
                "tiny"
                if minutes <= 10
                else "small"
                if minutes <= 30
                else "medium"
                if minutes <= 90
                else "large"
            )
        return [
            EventEnvelope(
                **common,
                type="task.created",
                schema_version=1,
                idempotency_key=f"triage:materialize:{item['proposal_id']}:task",
                payload={
                    "task_id": f"triage_task_{item['proposal_id']}",
                    "title": title,
                    "description": notes,
                    "owner_member_id": target_member,
                    "due_date": due_date,
                    "available_from": candidate.get("available_from"),
                    "energy": candidate.get("energy"),
                    "effort_min": candidate.get("effort_min"),
                    "effort": effort,
                    "domain": candidate.get("domain") or "tasks",
                    "item_type": candidate.get("item_type") or "task",
                    "micro_script": candidate.get("micro_script"),
                    "notes": notes,
                    "created_by": session.auth_member_id,
                    "source_system": "triage",
                    "source_commitment_id": candidate.get("source_commitment_id"),
                },
            )
        ]
    if target_kind == "recurrence":
        rrule = str(candidate.get("rrule") or "").strip()
        next_occurrence = str(candidate.get("next_occurrence") or "").strip()
        if not rrule or not next_occurrence:
            raise HTTPException(
                422, "a recurrence requires an RRULE and next occurrence"
            )
        linked_kind = str(candidate.get("linked_kind") or "party")
        linked_id = str(candidate.get("linked_id") or target_member)
        if linked_kind not in {"party", "asset", "account", "household"}:
            raise HTTPException(422, "unsupported recurrence relationship")
        return [
            EventEnvelope(
                **common,
                type="recurrence.added",
                schema_version=1,
                idempotency_key=(
                    f"triage:materialize:{item['proposal_id']}:recurrence"
                ),
                payload={
                    "recurrence_id": f"triage_recurrence_{item['proposal_id']}",
                    "linked_kind": linked_kind,
                    "linked_id": linked_id,
                    "kind": str(candidate.get("kind") or "household_rhythm"),
                    "rrule": rrule,
                    "next_occurrence": next_occurrence,
                    "lead_time_days": int(candidate.get("lead_time_days") or 0),
                    "trackable": bool(candidate.get("trackable", False)),
                    "notes": candidate.get("notes") or title,
                },
            )
        ]
    if target_kind == "commitment":
        owed_to = candidate.get("owed_to_party_id")
        if not owed_to:
            raise HTTPException(422, "a commitment requires an owed-to party")
        if runtime.projections is None:
            raise HTTPException(503, "parties projection unavailable")
        if (
            get_party(
                runtime.projections.connection("parties"),
                session,
                party_id=str(owed_to),
            )
            is None
        ):
            raise HTTPException(422, "commitment owed-to Party is unavailable")
        commitment_id = f"triage_commitment_{item['proposal_id']}"
        proposed = EventEnvelope(
            **common,
            type="commitment.proposed",
            schema_version=1,
            idempotency_key=f"triage:materialize:{item['proposal_id']}:commitment",
            payload={
                "commitment_id": commitment_id,
                "kind": candidate.get("kind", "other"),
                "owed_by_member_id": target_member,
                "owed_to_party_id": str(owed_to),
                "text_summary": title,
                "suggested_due": candidate.get("due"),
                "urgency": candidate.get("urgency", "this_week"),
                "confidence": float(item.get("confidence") or 1.0),
                "strength": "confident",
                "source_interaction_id": candidate.get("source_interaction_id"),
                "source_message_preview": candidate.get("source_message_preview"),
                "classify_reasons": list(
                    item.get("reasons") or ["member-confirmed triage"]
                ),
            },
        )
        confirmed = EventEnvelope(
            **common,
            type="commitment.confirmed",
            schema_version=1,
            idempotency_key=f"triage:materialize:{item['proposal_id']}:commitment-confirmed",
            payload={
                "commitment_id": commitment_id,
                "confirmed_by_member_id": session.auth_member_id,
                "confirmed_at": now,
                "note": "Confirmed through Triage",
            },
        )
        return [proposed, confirmed]
    if target_kind == "party_identifier":
        required = {
            "surviving_party_id",
            "candidate_kind",
            "candidate_value",
            "candidate_value_normalized",
        }
        if not required.issubset(candidate):
            raise HTTPException(422, "an identity candidate is missing required fields")
        identifier_kind = str(candidate["candidate_kind"])
        if identifier_kind not in {"email", "phone", "imessage_handle"}:
            raise HTTPException(422, "unsupported identity candidate kind")
        if runtime.projections is None:
            raise HTTPException(503, "parties projection unavailable")
        parties = runtime.projections.connection("parties")
        party_id = str(candidate["surviving_party_id"])
        if get_party(parties, session, party_id=party_id) is None:
            raise HTTPException(422, "identity candidate party is unavailable")
        existing = parties.execute(
            """
            SELECT party_id
              FROM identifiers
             WHERE tenant_id = ? AND kind = ? AND value_normalized = ?
             LIMIT 1
            """,
            (
                session.tenant_id,
                identifier_kind,
                str(candidate["candidate_value_normalized"]),
            ),
        ).fetchone()
        if existing is not None:
            raise HTTPException(409, "identity candidate is already assigned")
        return [
            EventEnvelope(
                **common,
                type="identifier.added",
                schema_version=1,
                idempotency_key=(
                    f"triage:materialize:{item['proposal_id']}:party-identifier"
                ),
                payload={
                    "identifier_id": f"triage_identifier_{item['proposal_id']}",
                    "party_id": party_id,
                    "kind": identifier_kind,
                    "value": str(candidate["candidate_value"]),
                    "value_normalized": str(candidate["candidate_value_normalized"]),
                    "verified": False,
                    "primary_for_kind": False,
                },
            )
        ]

    if target_kind == "party_candidate":
        party_kind = str(candidate.get("kind") or "")
        if party_kind not in {"person", "organization"}:
            raise HTTPException(
                422, "a People candidate must be a person or organization"
            )
        display_name = str(candidate.get("display_name") or title).strip()
        if not display_name:
            raise HTTPException(422, "a People candidate requires a display name")
        seed = f"{session.tenant_id}:{target_member}:{item['proposal_id']}"
        party_id = f"party_triage_{hashlib.sha256(seed.encode()).hexdigest()[:22]}"
        envelopes = [
            EventEnvelope(
                **common,
                type="party.created",
                schema_version=1,
                idempotency_key=f"triage:materialize:{item['proposal_id']}:party",
                payload={
                    "party_id": party_id,
                    "kind": party_kind,
                    "display_name": display_name,
                    "sort_name": str(
                        candidate.get("sort_name") or display_name
                    ).casefold(),
                    "nickname": candidate.get("nickname"),
                    "attributes": {
                        "organization_name": candidate.get("organization_name"),
                        "job_title": candidate.get("job_title"),
                    },
                },
            )
        ]
        identifier_value = str(candidate.get("identifier_value") or "").strip()
        normalized = str(candidate.get("identifier_value_normalized") or "").strip()
        identifier_kind = str(candidate.get("identifier_kind") or "").strip()
        if any((identifier_value, normalized, identifier_kind)):
            if not all((identifier_value, normalized, identifier_kind)):
                raise HTTPException(
                    422, "a People endpoint requires kind, value, and normalized value"
                )
            envelopes.append(
                EventEnvelope(
                    **common,
                    type="identifier.added",
                    schema_version=1,
                    idempotency_key=f"triage:materialize:{item['proposal_id']}:identifier",
                    payload={
                        "identifier_id": f"triage_identifier_{item['proposal_id']}",
                        "party_id": party_id,
                        "kind": identifier_kind,
                        "value": identifier_value,
                        "value_normalized": normalized,
                        "verified": False,
                        "primary_for_kind": True,
                    },
                )
            )
        return envelopes

    if target_kind == "party_relationship":
        party_a = str(candidate.get("party_a") or "")
        party_b = str(candidate.get("party_b") or "")
        direction = str(candidate.get("direction") or "")
        if direction not in {"a_to_b", "b_to_a", "mutual"}:
            raise HTTPException(422, "a relationship requires a supported direction")
        if not party_a or not party_b or party_a == party_b:
            raise HTTPException(422, "a relationship requires two different Parties")
        if runtime.projections is None:
            raise HTTPException(503, "parties projection unavailable")
        parties = runtime.projections.connection("parties")
        if any(
            get_party(parties, session, party_id=value) is None
            for value in (party_a, party_b)
        ):
            raise HTTPException(422, "a relationship Party is unavailable")
        return [
            EventEnvelope(
                **common,
                type="relationship.added",
                schema_version=1,
                idempotency_key=f"triage:materialize:{item['proposal_id']}:relationship",
                payload={
                    "relationship_id": f"triage_relationship_{item['proposal_id']}",
                    "party_a": party_a,
                    "party_b": party_b,
                    "label": str(candidate.get("label") or title),
                    "direction": direction,
                    "since": candidate.get("since"),
                    "attributes": {},
                },
            )
        ]

    if target_kind == "relationship_fact":
        fact_kind = str(candidate.get("kind") or "")
        if fact_kind not in {
            "birthday",
            "important_date",
            "employer",
            "role",
            "preference",
            "address",
            "context",
        }:
            raise HTTPException(422, "unsupported relationship-fact kind")
        party_id = str(candidate.get("party_id") or "")
        if (
            runtime.projections is None
            or get_party(
                runtime.projections.connection("parties"), session, party_id=party_id
            )
            is None
        ):
            raise HTTPException(422, "relationship-fact Party is unavailable")
        return [
            EventEnvelope(
                **common,
                type="relationship_fact.confirmed",
                schema_version=1,
                idempotency_key=f"triage:materialize:{item['proposal_id']}:relationship-fact",
                payload={
                    "fact_id": f"triage_fact_{item['proposal_id']}",
                    "member_id": target_member,
                    "party_id": party_id,
                    "kind": fact_kind,
                    "label": candidate.get("label"),
                    "value": str(candidate.get("value") or ""),
                    "effective_date": candidate.get("effective_date"),
                    "source_event_id": item["source_event_id"],
                    "evidence_quote": candidate.get("evidence_quote"),
                    "confirmed_at": now,
                    "confirmed_by_member_id": session.auth_member_id,
                },
            )
        ]

    route = item.get("route") or {}
    provider = route.get("provider")
    provider_account_id = route.get("provider_account_id")
    container = route.get("container_external_id")
    if not provider or not provider_account_id:
        raise HTTPException(
            422, "external candidates require a provider and account route"
        )
    _require_configured_external_route(
        runtime,
        provider=str(provider),
        target_member=target_member,
        provider_account_id=str(provider_account_id),
        container_external_id=str(container) if container is not None else None,
    )
    if target_kind == "calendar_event":
        if provider not in {"google_calendar", "apple_calendar"} or not container:
            raise HTTPException(
                422, "calendar candidates require a writable calendar route"
            )
        _require_reviewed_calendar_destination(
            runtime,
            target_member=target_member,
            provider=str(provider),
            provider_account_id=str(provider_account_id),
            calendar_external_id=str(container),
        )
        if not candidate.get("start") or not candidate.get("end"):
            raise HTTPException(422, "calendar candidates require start and end")
        operation = "create_event"
        candidate.setdefault("calendar_id", container)
        candidate.setdefault("calendar_external_id", container)
        candidate.setdefault("summary", title)
    elif target_kind in {"reminder", "list_item"}:
        if provider not in {"apple_reminders", "google_tasks"} or not container:
            raise HTTPException(
                422, "reminder candidates require a writable task-list route"
            )
        operation = (
            "create_reminder" if provider == "apple_reminders" else "create_task"
        )
        candidate = {
            key: value
            for key, value in candidate.items()
            if key in _REMINDER_PAYLOAD_FIELDS[operation]
        }
        if provider == "google_tasks" and isinstance(candidate.get("due"), str):
            candidate["due"] = candidate["due"].split("T", 1)[0]
        candidate.setdefault(
            "target_list_id" if provider == "apple_reminders" else "tasklist_id",
            container,
        )
        candidate.setdefault("title", title)
        candidate.setdefault("member_id", target_member)
    elif target_kind == "list":
        if provider != "apple_reminders":
            raise HTTPException(422, "list creation currently requires Apple Reminders")
        operation = "create_list"
        candidate = {
            key: value
            for key, value in candidate.items()
            if key in _REMINDER_PAYLOAD_FIELDS[operation]
        }
        candidate.setdefault("list_name", title)
        candidate.setdefault("member_id", target_member)
    else:
        raise HTTPException(422, "unsupported triage target")
    if operation not in _EXTERNAL_OPERATIONS.get(str(provider), set()):
        raise HTTPException(422, "the routed provider does not support this write")
    payload = {
        "command_id": f"triage:{item['proposal_id']}:{request_id}",
        "member_id": target_member,
        "provider": provider,
        "provider_account_id": provider_account_id,
        "operation": operation,
        "target_external_id": None,
        "expected_provider_revision": None,
        "payload": candidate,
        "requested_at": now,
    }
    return [
        EventEnvelope(
            **common,
            type="external_sync.write_requested",
            schema_version=1,
            idempotency_key=f"triage:materialize:{item['proposal_id']}:external",
            payload=payload,
        )
    ]


def _triage_destination(item: dict[str, Any]) -> dict[str, Any]:
    """Return the human-visible destination for one typed materialization."""
    return triage_destination(item)


_MATERIALIZATION_PROJECTION = {
    "task.created": "tasks",
    "commitment.proposed": "commitments",
    "commitment.confirmed": "commitments",
    "recurrence.added": "recurrences",
    "party.created": "parties",
    "identifier.added": "parties",
    "relationship.added": "parties",
    "relationship_fact.confirmed": "parties",
}


async def _wait_for_materialized_destinations(
    runtime: Any,
    envelopes: list[EventEnvelope],
    event_ids: list[str],
) -> None:
    """Do not return a local success receipt before its destination is readable."""

    for envelope, event_id in zip(envelopes, event_ids, strict=True):
        projection = _MATERIALIZATION_PROJECTION.get(envelope.type)
        if projection is not None:
            await runtime.wait_for_projection(projection, event_id)


def _reminder_account_for_command(
    runtime: Any, session: Session, body: ReminderCommandRequest
) -> dict[str, Any]:
    conn = runtime.projections.connection("lists")
    items = list_items_for_member(conn, session, member_id=session.auth_member_id)
    lists = lists_for_member(conn, session, member_id=session.auth_member_id)
    account = next(
        (
            account
            for account in reminder_accounts(
                runtime,
                session.auth_member_id,
                projected_items=items,
                projected_lists=lists,
            )
            if account["provider"] == body.provider
            and account["provider_account_id"] == body.provider_account_id
        ),
        None,
    )
    if account is None:
        raise HTTPException(404, "reminder account not found")
    if body.operation not in account["operations"]:
        raise HTTPException(
            409,
            account.get("remedy")
            or "this provider account does not support that reminder action",
        )
    return account


def _validate_reminder_command_target(
    runtime: Any,
    session: Session,
    body: ReminderCommandRequest,
    provider_payload: dict[str, Any],
) -> dict[str, Any] | None:
    conn = runtime.projections.connection("lists")
    owned_lists = lists_for_member(conn, session, member_id=session.auth_member_id)

    def owned_list(list_external_id: str | None) -> dict[str, Any] | None:
        if not list_external_id:
            return None
        return next(
            (
                row
                for row in owned_lists
                if row["provider"] == body.provider
                and row["provider_account_id"] == body.provider_account_id
                and row["list_external_id"] == list_external_id
            ),
            None,
        )

    item_operations = {
        "update_reminder",
        "delete_reminder",
        "complete_reminder",
        "uncomplete_reminder",
        "move_reminder",
        "update_task",
        "delete_task",
        "complete_task",
        "uncomplete_task",
        "move_task",
    }
    if body.operation in item_operations:
        if not body.list_external_id or not body.item_external_id:
            raise HTTPException(422, "this action requires a source list and item")
        item = get_list_item(
            conn,
            session,
            list_external_id=body.list_external_id,
            item_external_id=body.item_external_id,
        )
        if (
            item is None
            or item.get("member_id") != session.auth_member_id
            or item.get("provider") != body.provider
            or item.get("provider_account_id") != body.provider_account_id
        ):
            raise HTTPException(404, "reminder not found")
        if item.get("read_only_reason"):
            raise HTTPException(409, str(item["read_only_reason"]))
        if not body.expected_last_event_id:
            raise HTTPException(422, "an item action requires expected_last_event_id")
        if item["last_event_id"] != body.expected_last_event_id:
            raise HTTPException(409, "reminder changed; refresh before acting")
        if body.operation in {"move_reminder", "move_task"}:
            destination = provider_payload.get("destination_list_external_id")
            if not isinstance(destination, str) or not destination:
                raise HTTPException(422, "a move requires a destination list")
            if owned_list(destination) is None:
                raise HTTPException(404, "destination reminder list not found")
        return item

    if body.operation in {"create_reminder", "create_task", "clear_completed_tasks"}:
        if not body.list_external_id:
            raise HTTPException(422, "this action requires a destination list")
        row = owned_list(body.list_external_id)
        if row is None:
            raise HTTPException(404, "destination reminder list not found")
        if body.operation == "clear_completed_tasks":
            if not body.expected_last_event_id:
                raise HTTPException(
                    422, "clearing completed tasks requires expected_last_event_id"
                )
            if row["last_event_id"] != body.expected_last_event_id:
                raise HTTPException(409, "reminder list changed; refresh before acting")
        return row
    if body.operation in {"update_list", "delete_list"}:
        if not body.list_external_id:
            raise HTTPException(422, "this action requires a list")
        row = owned_list(body.list_external_id)
        if row is None:
            raise HTTPException(404, "reminder list not found")
        if not body.expected_last_event_id:
            raise HTTPException(422, "a list action requires expected_last_event_id")
        if row["last_event_id"] != body.expected_last_event_id:
            raise HTTPException(409, "reminder list changed; refresh before acting")
        return row
    return None


def _provider_reminder_payload(
    body: ReminderCommandRequest,
) -> tuple[dict[str, Any], frozenset[str]]:
    """Return the exact, provider-native payload or fail before governance."""

    allowed_fields = _REMINDER_PAYLOAD_FIELDS[body.operation]
    unexpected = set(body.payload) - allowed_fields
    if unexpected:
        raise HTTPException(
            422, f"unsupported provider fields: {', '.join(sorted(unexpected))}"
        )
    provider_payload = {
        key: body.payload[key] for key in body.payload if key in allowed_fields
    }
    if (
        body.operation in {"create_reminder", "create_task"}
        and not str(provider_payload.get("title") or "").strip()
    ):
        raise HTTPException(422, "a new reminder requires a title")

    if body.provider == "google_tasks":
        if body.operation in {"create_list", "update_list"}:
            unexpected_list_fields = set(provider_payload) - {"title"}
            if unexpected_list_fields:
                raise HTTPException(
                    422,
                    "Google Tasks lists only expose a title through this adapter",
                )
            if not str(provider_payload.get("title") or "").strip():
                raise HTTPException(422, "a Google Tasks list requires a title")
        due = provider_payload.get("due")
        if isinstance(due, str) and "T" not in due:
            # Google accepts RFC 3339 but stores only the date component.
            try:
                parsed_due = datetime.strptime(due, "%Y-%m-%d")
            except ValueError as exc:
                raise HTTPException(422, "due must be YYYY-MM-DD") from exc
            provider_payload["due"] = parsed_due.strftime("%Y-%m-%dT00:00:00.000Z")
    else:
        if body.operation in {"create_list", "update_list"}:
            unexpected_list_fields = set(provider_payload) - {"name", "color"}
            if unexpected_list_fields:
                raise HTTPException(
                    422,
                    "Apple Reminders lists expose name and color through this adapter",
                )
            if (
                body.operation == "create_list"
                and not str(provider_payload.get("name") or "").strip()
            ):
                raise HTTPException(422, "an Apple Reminders list requires a name")
            if "name" in provider_payload:
                provider_payload["list_name"] = provider_payload.pop("name")
        for field in ("start", "due"):
            value = provider_payload.get(field)
            if isinstance(value, str):
                try:
                    parsed = datetime.strptime(value, "%Y-%m-%d")
                except ValueError as exc:
                    raise HTTPException(
                        422,
                        f"Apple Reminders {field} must be YYYY-MM-DD or date components",
                    ) from exc
                provider_payload[field] = {
                    "year": parsed.year,
                    "month": parsed.month,
                    "day": parsed.day,
                    "all_day": True,
                    "floating": True,
                }
    return provider_payload, allowed_fields


def _normalized_task_due(value: str | None, *, timezone_name: str) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        if len(raw) == 10:
            return date.fromisoformat(raw).isoformat()
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            422, "due date must be a valid date or date and time"
        ) from exc
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(503, "instance timezone is unavailable") from exc
    return parsed.isoformat()


@router.post("/api/member/v1/tasks", status_code=201)
async def create_member_task(
    body: MemberTaskCreateRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    """Create explicit member-authored work and return its destination receipt."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "task creation is unavailable in view-as mode")
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "task state is unavailable")
    title = body.title.strip()
    domain = body.domain.strip()
    if not title:
        raise HTTPException(422, "task title cannot be blank")
    if not domain:
        raise HTTPException(422, "task area cannot be blank")
    values = body.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prior = await _prior_member_command(
        runtime,
        session,
        event_type="task.created",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("tasks", prior["event_id"])
        task_id = str(prior["payload"]["task_id"])
        return {
            "event_id": prior["event_id"],
            "task": get_task(
                runtime.projections.connection("tasks"), session, task_id=task_id
            ),
            "duplicate": True,
            "destination": {
                "surface": "reminders",
                "href": f"/member/reminders?item=task%3A{task_id}",
                "record_kind": "task",
                "record_id": task_id,
            },
        }
    task_seed = f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}"
    task_id = "task_" + hashlib.sha256(task_seed.encode()).hexdigest()[:28]
    payload = {
        "task_id": task_id,
        "title": title,
        "description": body.notes,
        "owner_member_id": session.auth_member_id,
        "due_date": _normalized_task_due(
            body.due_date, timezone_name=runtime.settings.instance.timezone
        ),
        "available_from": _normalized_task_due(
            body.available_from, timezone_name=runtime.settings.instance.timezone
        ),
        "energy": body.energy,
        "effort": body.effort,
        "domain": domain,
        "item_type": body.item_type,
        "micro_script": body.micro_script,
        "notes": body.notes,
        "created_by": session.auth_member_id,
        "source_system": "member-portal",
    }
    await _guard_member_write(runtime, session, "task.created", payload)
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="task.created",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="member-portal",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity="normal",
            correlation_id=session.correlation_id,
            actor_identity=session.auth_member_id,
            idempotency_key=(
                f"member-command:task.created:{session.auth_member_id}:"
                f"{body.request_id}"
            ),
            raw_ref=f"request-sha256:{fingerprint}",
            payload=payload,
        )
    )
    await runtime.wait_for_projection("tasks", event_id)
    return {
        "event_id": event_id,
        "task": get_task(
            runtime.projections.connection("tasks"), session, task_id=task_id
        ),
        "duplicate": False,
        "destination": {
            "surface": "reminders",
            "href": f"/member/reminders?item=task%3A{task_id}",
            "record_kind": "task",
            "record_id": task_id,
        },
    }


@router.patch("/api/member/v1/tasks/{task_id}")
async def update_member_task(
    task_id: str,
    body: MemberTaskUpdateRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Correct an owned canonical task through a closed field allowlist."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "task editing is unavailable in view-as mode")
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "task state is unavailable")
    row = get_task(runtime.projections.connection("tasks"), session, task_id=task_id)
    if row is None or row.get("assignee_party") not in {
        None,
        session.auth_member_id,
    }:
        raise HTTPException(404, "task not found")
    values = body.model_dump(mode="json", exclude_unset=True)
    fingerprint = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prior = await _prior_member_command(
        runtime,
        session,
        event_type="task.updated",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("tasks", prior["event_id"])
        return {
            "event_id": prior["event_id"],
            "task": get_task(
                runtime.projections.connection("tasks"), session, task_id=task_id
            ),
            "duplicate": True,
        }
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "task changed; refresh before saving")
    updates = {
        key: value
        for key, value in values.items()
        if key
        in {
            "title",
            "notes",
            "due_date",
            "available_from",
            "energy",
            "effort",
            "domain",
            "item_type",
            "micro_script",
        }
    }
    if not updates:
        raise HTTPException(422, "at least one task field must be changed")
    for required_field in ("title", "domain", "item_type"):
        if required_field in updates and updates[required_field] is None:
            raise HTTPException(422, f"{required_field} cannot be cleared")
    for text_field in ("title", "domain"):
        if text_field in updates:
            updates[text_field] = str(updates[text_field]).strip()
            if not updates[text_field]:
                raise HTTPException(422, f"{text_field} cannot be blank")
    if "due_date" in updates:
        updates["due_date"] = _normalized_task_due(
            updates["due_date"], timezone_name=runtime.settings.instance.timezone
        )
    if "available_from" in updates:
        updates["available_from"] = _normalized_task_due(
            updates["available_from"],
            timezone_name=runtime.settings.instance.timezone,
        )
    payload = {
        "task_id": task_id,
        "updated_at": EventEnvelope.now_utc_iso(),
        "updated_by_party_id": session.auth_member_id,
        "previous_status": row["status"],
        "new_status": None,
        "field_updates": updates,
    }
    await _guard_member_write(runtime, session, "task.updated", payload)
    event_id = await runtime.events.write(
        _member_command_event(
            session,
            event_type="task.updated",
            payload=payload,
            request_id=body.request_id,
            fingerprint=fingerprint,
            causation_id=row["last_event_id"],
            owner_scope=row["owner_scope"],
            visibility_scope=row["visibility_scope"],
            sensitivity=cast(Sensitivity, row.get("sensitivity") or "normal"),
        )
    )
    await runtime.wait_for_projection("tasks", event_id)
    return {
        "event_id": event_id,
        "task": get_task(
            runtime.projections.connection("tasks"), session, task_id=task_id
        ),
        "duplicate": False,
    }


@router.post("/api/member/v1/reminders/commands", status_code=202)
async def reminder_command(
    body: ReminderCommandRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    """Validate, authorize, and enqueue one provider-shaped reminder mutation."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "reminder actions are unavailable while viewing another member"
        )
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "reminder state is unavailable")
    _reminder_account_for_command(runtime, session, body)
    provider_payload, allowed_fields = _provider_reminder_payload(body)
    target = _validate_reminder_command_target(runtime, session, body, provider_payload)
    if body.provider == "apple_reminders" and body.operation == "move_reminder":
        provider_payload["destination_list_id"] = provider_payload.pop(
            "destination_list_external_id"
        )

    expected_provider_revision = body.expected_provider_revision or (
        (target or {}).get("provider_revision")
    )

    provider_payload.update(
        {
            "member_id": session.auth_member_id,
            "list_external_id": body.list_external_id,
            "tasklist_id": body.list_external_id,
            "item_external_id": body.item_external_id,
            "expected_provider_revision": expected_provider_revision,
            "content_privacy_guard": {
                "member_bound": True,
                "provider_fields_allowlisted": sorted(allowed_fields),
                "source_scope": f"private:{session.auth_member_id}",
            },
        }
    )
    target_external_id = body.item_external_id or body.list_external_id
    now = EventEnvelope.now_utc_iso()
    command_payload = {
        "command_id": body.request_id,
        "member_id": session.auth_member_id,
        "provider": body.provider,
        "provider_account_id": body.provider_account_id,
        "operation": body.operation,
        "target_external_id": target_external_id,
        "expected_provider_revision": expected_provider_revision,
        "payload": provider_payload,
        "requested_at": now,
    }
    decision = await runtime.guarded_write.check(
        session, "external_sync.write_requested", command_payload
    )
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    event_id, duplicate = await _append_provider_action_confirmation(
        runtime,
        session,
        command_payload=command_payload,
        request_id=body.request_id,
        request_fingerprint=fingerprint,
        source_adapter="member-reminders",
        sensitivity=cast(Sensitivity, (target or {}).get("sensitivity") or "normal"),
        causation_id=(target or {}).get("last_event_id"),
    )
    return {
        "accepted": True,
        "duplicate": duplicate,
        "command_id": body.request_id,
        "event_id": event_id,
        "domain_approval_event_id": event_id,
        "state": "awaiting_openclaw_approval",
        "observation_mode": runtime._observation_active,
    }


@router.post("/api/member/v1/reminders/cues", status_code=202)
async def configure_reminder_cue(
    body: ReminderCueRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Attach one deterministic cue rule to an owned reminder item."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "reminder cues are unavailable in view-as mode")
    runtime = _runtime(request)
    item = get_list_item(
        runtime.projections.connection("lists"),
        session,
        list_external_id=body.list_external_id,
        item_external_id=body.item_external_id,
    )
    if item is None or item.get("member_id") != session.auth_member_id:
        raise HTTPException(404, "reminder not found")
    if item["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "reminder changed; refresh before adding the cue")
    if body.operator in {"equals", "not_equals"} and body.expected_state is None:
        raise HTTPException(422, f"{body.operator} requires expected_state")
    if body.operator in {"above", "below"} and body.expected_number is None:
        raise HTTPException(422, f"{body.operator} requires expected_number")
    payload = {
        "cue_id": body.cue_id,
        "member_id": session.auth_member_id,
        "provider": item["provider"],
        "provider_account_id": item["provider_account_id"],
        "list_external_id": body.list_external_id,
        "item_external_id": body.item_external_id,
        "signal_key": body.signal_key,
        "source": body.source,
        "kind": body.kind,
        "operator": body.operator,
        "expected_state": body.expected_state,
        "expected_number": body.expected_number,
        "enabled": body.enabled,
        "configured_at": EventEnvelope.now_utc_iso(),
    }
    await _guard_member_write(runtime, session, "reminder.cue.configured", payload)
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="reminder.cue.configured",
            schema_version=1,
            occurred_at=payload["configured_at"],
            source_adapter="member-reminders",
            source_account_id=session.auth_member_id,
            owner_scope=item["owner_scope"],
            visibility_scope=item["visibility_scope"],
            sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
            correlation_id=session.correlation_id,
            causation_id=item["last_event_id"],
            actor_identity=session.auth_member_id,
            idempotency_key=f"reminder-cue:{session.auth_member_id}:{body.cue_id}",
            payload=payload,
        )
    )
    await runtime.wait_for_projection("lists", event_id)
    return {"accepted": True, "event_id": event_id, "cue_id": body.cue_id}


@router.post("/api/member/v1/triage/intents", status_code=202)
async def submit_triage_intent(
    body: TriageIntentRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    """Durably hold exact member text before deterministic interpretation."""

    runtime = _runtime(request)
    lock_key = f"{session.tenant_id}:{session.auth_member_id}:{body.client_request_id}"
    lock = runtime.triage_intent_command_locks.setdefault(lock_key, asyncio.Lock())
    runtime.triage_intent_command_lock_users[lock_key] = (
        runtime.triage_intent_command_lock_users.get(lock_key, 0) + 1
    )
    try:
        async with lock:
            return await _submit_triage_intent_locked(body, session, runtime)
    finally:
        remaining = runtime.triage_intent_command_lock_users[lock_key] - 1
        if remaining:
            runtime.triage_intent_command_lock_users[lock_key] = remaining
        else:
            runtime.triage_intent_command_lock_users.pop(lock_key, None)
            runtime.triage_intent_command_locks.pop(lock_key, None)


async def _submit_triage_intent_locked(
    body: TriageIntentRequest,
    session: Session,
    runtime: Any,
) -> dict[str, Any]:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "Add for review is unavailable in view-as mode")
    if runtime.events is None or runtime.event_log is None:
        raise HTTPException(503, "Triage capture is unavailable")

    source = None
    sensitivity: Sensitivity = "normal"
    if body.source_event_id is not None:
        source = await runtime.event_log.get(body.source_event_id)
        visible = filter_one(session, source)
        if visible is None:
            raise HTTPException(404, "linked source evidence is not visible")
        sensitivity = cast(Sensitivity, source.get("sensitivity") or "normal")

    request_values = body.model_dump(mode="json")
    request_fingerprint = hashlib.sha256(
        json.dumps(request_values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    intent_key = f"triage-intent:{session.auth_member_id}:{body.client_request_id}"
    prior_intent = await runtime.event_log.get_by_idempotency(
        session.tenant_id, intent_key
    )
    duplicate = prior_intent is not None
    if prior_intent is not None:
        if prior_intent.get("raw_ref") != f"request-sha256:{request_fingerprint}":
            raise HTTPException(409, "capture request id conflicts with prior text")
        intent_event_id = str(prior_intent["event_id"])
        intent_id = str(prior_intent["payload"]["intent_id"])
        submitted_at = datetime.fromisoformat(
            str(prior_intent["payload"]["submitted_at"])
        )
    else:
        intent_seed = (
            f"{session.tenant_id}|{session.auth_member_id}|{body.client_request_id}"
        )
        intent_id = "intent_" + hashlib.sha256(intent_seed.encode()).hexdigest()[:28]
        submitted_at = datetime.now(timezone.utc)
        intent_payload: dict[str, Any] = {
            "intent_id": intent_id,
            "member_id": session.auth_member_id,
            "text": body.text,
            "source_surface": body.source_surface,
            "kind_hint": body.kind_hint,
            "source_event_id": body.source_event_id,
            "submitted_at": submitted_at.isoformat(),
            "client_request_id": body.client_request_id,
            "submission_kind": "manual",
            "evidence_complete": True,
            "evidence_issues": [],
            "intake_channel": "member",
        }
        await _guard_triage_write(
            runtime, session, "triage.intent.submitted", intent_payload
        )
        intent_event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=session.tenant_id,
                type="triage.intent.submitted",
                schema_version=2,
                occurred_at=submitted_at.isoformat(),
                source_adapter=f"member-{body.source_surface}",
                source_account_id=session.auth_member_id,
                owner_scope=f"private:{session.auth_member_id}",
                visibility_scope=f"private:{session.auth_member_id}",
                sensitivity=sensitivity,
                correlation_id=session.correlation_id,
                causation_id=body.source_event_id,
                actor_identity=session.auth_member_id,
                idempotency_key=intent_key,
                raw_ref=f"request-sha256:{request_fingerprint}",
                payload=intent_payload,
            )
        )
        # The holding state exists before any extraction result is recorded.
        await runtime.wait_for_projection("triage", intent_event_id)

    intent_event = await runtime.event_log.get(intent_event_id)
    if intent_event is None or runtime.submission_interpreter is None:
        raise HTTPException(503, "submission interpretation is unavailable")
    result = await runtime.submission_interpreter.interpret(
        intent_event, session=session
    )
    return {**result, "duplicate": duplicate}


def _require_self_triage_policy(session: Session) -> None:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "Triage learning controls are read-only while viewing another member"
        )


def _triage_policy_digest(scope_kind: str, scope_value: str) -> str:
    return policy_scope_digest(scope_kind, scope_value)


def _require_human_triage_management(runtime: Any) -> None:
    if not runtime.settings.human_triage_management_enabled:
        raise HTTPException(
            409,
            "Human-centered Triage management is disabled until separately activated",
        )


def _learning_preview(
    runtime: Any,
    session: Session,
    body: TriageLearningPreviewRequest,
) -> dict[str, Any]:
    if body.outcome_kind not in {"dismiss_not_useful", "keep_context"}:
        raise HTTPException(
            422,
            "completion, ownership, duplication, and extraction corrections are not future attention preferences",
        )
    if runtime.projections is None:
        raise HTTPException(503, "context policy projection unavailable")
    evidence = verified_feedback_for_scope(
        runtime.projections.connection("context_fabric"),
        session,
        member_id=session.auth_member_id,
        scope_kind=body.scope_kind,
        scope_value=body.scope_value,
    )
    if not evidence:
        raise HTTPException(422, "a policy proposal requires a verified member outcome")
    return source_policy_rule_preview(
        scope_kind=body.scope_kind,
        scope_value_digest=_triage_policy_digest(body.scope_kind, body.scope_value),
        instruction=body.instruction,
        outcome_kind=body.outcome_kind,
        feedback_event_ids=[str(value["last_event_id"]) for value in evidence],
        proposal_ids=[str(value["proposal_id"]) for value in evidence],
    )


@router.get("/api/member/v1/triage/backlog-reassessment-preview")
async def preview_triage_backlog_reassessment(
    request: Request,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2000),
) -> dict[str, Any]:
    """Preview old/proposed dispositions without appending or deleting anything."""

    _require_self_triage_policy(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "triage projections unavailable")
    items = triage_backlog_for_member(
        runtime.projections.connection("triage"),
        session,
        member_id=session.auth_member_id,
        limit=500,
    )
    reconciliations: dict[str, dict[str, Any]] = {}
    context_conn = runtime.projections.connection("context_fabric")
    for item in items:
        receipt = reconciliation_for_proposal(
            context_conn,
            session,
            proposal_id=str(item["proposal_id"]),
            member_id=session.auth_member_id,
        )
        if receipt is not None:
            reconciliations[str(item["proposal_id"])] = receipt
    try:
        preview = backlog_reassessment_preview(
            items,
            reconciliations,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        **preview,
        "human_triage_management_active": (
            runtime.settings.human_triage_management_enabled
        ),
        "inventory_cap": 500,
        "recoverable_from_event_log": True,
    }


@router.post("/api/member/v1/triage/{proposal_id}/manage")
async def manage_triage_item(
    proposal_id: str,
    body: TriageManagementRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    _require_human_triage_management(runtime)
    item = _owned_triage_item(runtime, session, proposal_id)
    if item["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "triage item changed; refresh before deciding")
    if body.action == "correct_ownership" and body.corrected_owner_member_id not in {
        None,
        session.auth_member_id,
    }:
        # A member can reject ownership, but cannot place private evidence in
        # another adult's queue without that adult's own scoped authorization.
        raise HTTPException(403, "ownership correction cannot assign another member")
    required = {
        "link_existing": bool(body.linked_object_kind and body.linked_object_id),
        "mark_duplicate": bool(body.duplicate_of_proposal_id),
        "snooze": body.snoozed_until is not None,
        "correct_extraction": bool(body.corrected_title or body.corrected_target_kind),
        "correct_ownership": body.corrected_owner_member_id is not None,
        "propose_suppression": body.policy_proposal_id is not None,
    }
    if body.action in required and not required[body.action]:
        raise HTTPException(422, f"{body.action} is missing its exact target")
    fingerprint = _triage_request_fingerprint(proposal_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.item.managed",
        request_id=body.request_id,
        request_fingerprint=fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("triage", prior["event_id"])
        return {"event_id": prior["event_id"], "duplicate": True}
    revision = int((item.get("management") or {}).get("revision") or 0) + 1
    action_id = (
        "triage_action_"
        + hashlib.sha256(
            f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}".encode()
        ).hexdigest()[:32]
    )
    payload = {
        **body.model_dump(mode="json", exclude={"request_id"}),
        "action_id": action_id,
        "proposal_id": proposal_id,
        "member_id": session.auth_member_id,
        "action_revision": revision,
        "supersedes_action_id": (item.get("management") or {}).get("action_id"),
        "reversible": True,
        "provider_effects": False,
        "managed_at": EventEnvelope.now_utc_iso(),
    }
    await _guard_triage_write(runtime, session, "triage.item.managed", payload)
    event_id = await runtime.events.write(
        _triage_event(
            session,
            event_type="triage.item.managed",
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            causation_id=item["last_event_id"],
            sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
        )
    )
    await runtime.wait_for_projection("triage", event_id)
    return {
        "event_id": event_id,
        "action_id": action_id,
        "state": "recorded",
        "reversible": True,
        "provider_effects": 0,
        "duplicate": False,
    }


@router.post("/api/member/v1/triage-learning/preview")
async def preview_triage_learning_rule(
    body: TriageLearningPreviewRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    return _learning_preview(_runtime(request), session, body)


@router.post("/api/member/v1/triage-learning/propose")
async def propose_triage_learning_rule(
    body: TriageLearningProposeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    preview = _learning_preview(runtime, session, body)
    if preview["preview_digest"] != body.preview_digest:
        raise HTTPException(409, "verified outcomes changed; preview the rule again")
    fingerprint = _triage_request_fingerprint("source-policy-proposal", body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.source_policy_rule.proposed",
        request_id=body.request_id,
        request_fingerprint=fingerprint,
    )
    policy_proposal_id = (
        "triage_policy_proposal_"
        + hashlib.sha256(
            f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}".encode()
        ).hexdigest()[:28]
    )
    if prior is not None:
        await runtime.wait_for_projection("context_fabric", prior["event_id"])
        return {
            "event_id": prior["event_id"],
            "policy_proposal_id": policy_proposal_id,
            "state": "proposed",
            "duplicate": True,
        }
    payload = {
        "policy_proposal_id": policy_proposal_id,
        "member_id": session.auth_member_id,
        "scope_kind": body.scope_kind,
        "scope_value_digest": preview["scope_value_digest"],
        "instruction": body.instruction,
        "outcome_kind": body.outcome_kind,
        "verified_feedback_event_ids": preview["feedback_event_ids"],
        "affected_proposal_ids": preview["proposal_ids"],
        "preview_digest": preview["preview_digest"],
        "policy_diff": preview["policy_diff"],
        "explanation": preview["explanation"],
        "proposed_by_member_id": session.auth_member_id,
        "proposed_at": EventEnvelope.now_utc_iso(),
        "self_activates": False,
        "access_widening": False,
        "cross_member_effect": False,
        "provider_effects": False,
    }
    await _guard_triage_write(
        runtime, session, "triage.source_policy_rule.proposed", payload
    )
    event_id = await runtime.events.write(
        _triage_event(
            session,
            event_type="triage.source_policy_rule.proposed",
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            causation_id=preview["feedback_event_ids"][0],
        )
    )
    await runtime.wait_for_projection("context_fabric", event_id)
    return {
        "event_id": event_id,
        "policy_proposal_id": policy_proposal_id,
        "state": "proposed",
        "self_activated": False,
        "provider_effects": 0,
        "duplicate": False,
    }


@router.post("/api/member/v1/triage-learning/{policy_proposal_id}/decide")
async def decide_triage_learning_rule(
    policy_proposal_id: str,
    body: TriageLearningDecisionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    _require_human_triage_management(runtime)
    if runtime.projections is None:
        raise HTTPException(503, "context policy projection unavailable")
    proposal = get_source_policy_proposal(
        runtime.projections.connection("context_fabric"),
        session,
        member_id=session.auth_member_id,
        policy_proposal_id=policy_proposal_id,
    )
    if proposal is None:
        raise HTTPException(404, "source policy proposal not found")
    if proposal["preview_digest"] != body.preview_digest:
        raise HTTPException(409, "policy preview changed")
    if body.decision != "edit" and body.instruction != proposal["instruction"]:
        raise HTTPException(409, "changed instructions require a new preview")
    affected = set(proposal["affected_proposal_ids"])
    if body.decision == "trial" and (
        not body.trial_proposal_ids or not set(body.trial_proposal_ids) <= affected
    ):
        raise HTTPException(422, "trial must be a non-empty subset of previewed items")
    if body.decision != "trial" and body.trial_proposal_ids:
        raise HTTPException(422, "only a trial may name a bounded cohort")
    rule_id = (
        "triage_source_rule_"
        + hashlib.sha256(
            f"{session.tenant_id}|{session.auth_member_id}|{policy_proposal_id}".encode()
        ).hexdigest()[:28]
        if body.decision in {"approve", "trial"}
        else None
    )
    fingerprint = _triage_request_fingerprint(policy_proposal_id, body)
    payload = {
        "decision_id": "triage_policy_decision_"
        + hashlib.sha256(
            f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}".encode()
        ).hexdigest()[:28],
        "policy_proposal_id": policy_proposal_id,
        "rule_id": rule_id,
        "member_id": session.auth_member_id,
        "scope_kind": proposal["scope_kind"],
        "scope_value_digest": proposal["scope_value_digest"],
        "outcome_kind": proposal["outcome_kind"],
        "decision": body.decision,
        "instruction": body.instruction,
        "preview_digest": body.preview_digest,
        "trial_proposal_ids": body.trial_proposal_ids,
        "decided_by_member_id": session.auth_member_id,
        "decided_at": EventEnvelope.now_utc_iso(),
        "access_widening": False,
        "cross_member_effect": False,
        "provider_effects": False,
    }
    await _guard_triage_write(
        runtime, session, "triage.source_policy_rule.decided", payload
    )
    event_id = await runtime.events.write(
        _triage_event(
            session,
            event_type="triage.source_policy_rule.decided",
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            causation_id=str(proposal["last_event_id"]),
        )
    )
    await runtime.wait_for_projection("context_fabric", event_id)
    return {
        "event_id": event_id,
        "rule_id": rule_id,
        "state": body.decision,
        "provider_effects": 0,
    }


@router.post("/api/member/v1/triage-learning/rules/{rule_id}/review")
async def review_triage_learning_rule(
    rule_id: str,
    body: TriageLearningReviewRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    _require_human_triage_management(runtime)
    if runtime.projections is None:
        raise HTTPException(503, "context policy projection unavailable")
    rule = get_source_policy_rule(
        runtime.projections.connection("context_fabric"),
        session,
        member_id=session.auth_member_id,
        rule_id=rule_id,
    )
    if rule is None:
        raise HTTPException(404, "source policy rule not found")
    if body.negative_outcome and body.decision not in {"narrow", "suspend", "revoke"}:
        raise HTTPException(422, "negative outcomes may only narrow or deactivate")
    if (body.decision == "narrow") != (body.replacement_instruction is not None):
        raise HTTPException(422, "narrow review requires one replacement instruction")
    narrowing_order = {"always": 0, "usually": 1, "ask": 2, "never": 3}
    if (
        body.replacement_instruction is not None
        and narrowing_order[body.replacement_instruction]
        <= narrowing_order[str(rule["instruction"])]
    ):
        raise HTTPException(422, "replacement instruction must narrow admission")
    fingerprint = _triage_request_fingerprint(rule_id, body)
    payload = {
        **body.model_dump(mode="json", exclude={"request_id"}),
        "review_id": "triage_policy_review_"
        + hashlib.sha256(
            f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}".encode()
        ).hexdigest()[:28],
        "rule_id": rule_id,
        "member_id": session.auth_member_id,
        "previous_instruction": rule["instruction"],
        "reviewed_by_member_id": session.auth_member_id,
        "reviewed_at": EventEnvelope.now_utc_iso(),
        "access_widening": False,
        "cross_member_effect": False,
        "provider_effects": False,
    }
    await _guard_triage_write(
        runtime, session, "triage.source_policy_rule.reviewed", payload
    )
    event_id = await runtime.events.write(
        _triage_event(
            session,
            event_type="triage.source_policy_rule.reviewed",
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            causation_id=str(rule["last_event_id"]),
        )
    )
    await runtime.wait_for_projection("context_fabric", event_id)
    return {"event_id": event_id, "rule_id": rule_id, "state": body.decision}


@router.get("/api/member/v1/triage-learning")
async def list_triage_learning(
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "context policy projection unavailable")
    conn = runtime.projections.connection("context_fabric")
    return {
        "proposals": source_policy_proposals_for_member(
            conn, session, member_id=session.auth_member_id
        ),
        "rules": source_policy_rules_for_member(
            conn, session, member_id=session.auth_member_id
        ),
        "active": runtime.settings.human_triage_management_enabled,
        "provider_effects": 0,
    }


@router.post("/api/member/v1/triage-policy/preview")
async def preview_triage_policy(
    body: TriagePolicyPreviewRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "context policy projection unavailable")
    try:
        affected = feedback_scope_count(
            runtime.projections.connection("context_fabric"),
            session,
            member_id=session.auth_member_id,
            scope_kind=body.scope_kind,
            scope_value=body.scope_value,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "scope_kind": body.scope_kind,
        "scope_value_digest": _triage_policy_digest(body.scope_kind, body.scope_value),
        "disposition": body.disposition,
        "affected_feedback_count": affected,
        "enforcement_mode": "shadow",
        "provider_effects": 0,
        "explanation": (
            "Approval records a reversible shadow rule. It cannot suppress, "
            "confirm, route, or write a candidate in this rollout."
        ),
    }


@router.post("/api/member/v1/triage-policy/approve")
async def approve_triage_policy(
    body: TriagePolicyApproveRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "context policy projection unavailable")
    affected = feedback_scope_count(
        runtime.projections.connection("context_fabric"),
        session,
        member_id=session.auth_member_id,
        scope_kind=body.scope_kind,
        scope_value=body.scope_value,
    )
    if affected != body.expected_affected_count:
        raise HTTPException(409, "feedback pattern changed; preview the rule again")
    scope_digest = _triage_policy_digest(body.scope_kind, body.scope_value)
    rule_id = (
        "triage_rule_"
        + hashlib.sha256(
            (
                f"{session.tenant_id}|{session.auth_member_id}|{body.scope_kind}|"
                f"{scope_digest}|{body.disposition}"
            ).encode()
        ).hexdigest()[:28]
    )
    request_fingerprint = _triage_request_fingerprint(rule_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.policy_rule.approved",
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("context_fabric", prior["event_id"])
        return {
            "event_id": prior["event_id"],
            "rule_id": rule_id,
            "state": "shadow",
            "duplicate": True,
        }
    payload = {
        "rule_id": rule_id,
        "member_id": session.auth_member_id,
        "scope_kind": body.scope_kind,
        "scope_value_digest": scope_digest,
        "disposition": body.disposition,
        "enforcement_mode": "shadow",
        "preview_affected_count": affected,
        "approved_by_member_id": session.auth_member_id,
        "approved_at": EventEnvelope.now_utc_iso(),
        "expires_at": None,
    }
    await _guard_triage_write(runtime, session, "triage.policy_rule.approved", payload)
    event_id = await runtime.events.write(
        _triage_event(
            session,
            event_type="triage.policy_rule.approved",
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=request_fingerprint,
            causation_id=None,
        )
    )
    await runtime.wait_for_projection("context_fabric", event_id)
    return {
        "event_id": event_id,
        "rule_id": rule_id,
        "state": "shadow",
        "duplicate": False,
    }


@router.post("/api/member/v1/triage-policy/{rule_id}/revoke")
async def revoke_triage_policy(
    rule_id: str,
    body: TriagePolicyRevokeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "context policy projection unavailable")
    rule = get_policy_rule(
        runtime.projections.connection("context_fabric"),
        session,
        member_id=session.auth_member_id,
        rule_id=rule_id,
    )
    if rule is None:
        raise HTTPException(404, "triage policy rule not found")
    if rule["status"] != "active":
        return {"rule_id": rule_id, "state": "revoked", "duplicate": True}
    request_fingerprint = _triage_request_fingerprint(rule_id, body)
    payload = {
        "rule_id": rule_id,
        "member_id": session.auth_member_id,
        "revoked_by_member_id": session.auth_member_id,
        "revoked_at": EventEnvelope.now_utc_iso(),
        "reason": body.reason,
    }
    await _guard_triage_write(runtime, session, "triage.policy_rule.revoked", payload)
    event_id = await runtime.events.write(
        _triage_event(
            session,
            event_type="triage.policy_rule.revoked",
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=request_fingerprint,
            causation_id=str(rule["last_event_id"]),
        )
    )
    await runtime.wait_for_projection("context_fabric", event_id)
    return {"event_id": event_id, "rule_id": rule_id, "state": "revoked"}


@router.post("/api/member/v1/triage/{proposal_id}/enrich")
async def enrich_triage_item(
    proposal_id: str,
    body: TriageEnrichmentRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.triage_command_locks.setdefault(
        f"{session.tenant_id}:{proposal_id}", asyncio.Lock()
    )
    async with lock:
        return await _enrich_triage_item_locked(
            runtime, proposal_id, body, request, session
        )


async def _enrich_triage_item_locked(
    runtime: Any,
    proposal_id: str,
    body: TriageEnrichmentRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    item = _owned_triage_item(runtime, session, proposal_id)
    if item["source_kind"] == "commitment_proposal":
        if body.target_kind not in {None, "commitment"} or body.route is not None:
            raise HTTPException(
                422,
                "existing commitment proposals may change only description or due date",
            )
        unexpected = set(body.candidate_updates) - {"due", "due_at"}
        if unexpected:
            raise HTTPException(422, "unsupported commitment enrichment fields")
        result = await comms.edit_commitment(
            proposal_id,
            comms.EditRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                description=body.title,
                due_at=body.candidate_updates.get("due_at")
                or body.candidate_updates.get("due"),
            ),
            request,
            session,
        )
        await runtime.wait_for_projection("triage", result["event_id"])
        return {
            **result,
            "triage": _owned_reviewable_triage_item(runtime, session, proposal_id),
        }
    selected_target = str(body.target_kind or item.get("target_kind") or "")
    selected_route = (
        body.route.model_dump(mode="json")
        if body.route
        else dict(item.get("route") or {})
    )
    body.candidate_updates = _validated_triage_candidate_updates(
        selected_target,
        body.candidate_updates,
        timezone_name=runtime.settings.instance.timezone,
        provider=str(selected_route.get("provider") or "") or None,
    )
    request_fingerprint = _triage_request_fingerprint(proposal_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.item.enriched",
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("triage", prior["event_id"])
        return {
            "event_id": prior["event_id"],
            "triage": _owned_reviewable_triage_item(runtime, session, proposal_id),
            "duplicate": True,
        }
    _require_current_triage(
        item,
        body.expected_last_event_id,
        allow_pending_interpretation=True,
    )
    payload = {
        "proposal_id": proposal_id,
        "enriched_at": EventEnvelope.now_utc_iso(),
        "enriched_by_member_id": session.auth_member_id,
        "title": body.title,
        "target_kind": body.target_kind,
        "candidate_updates": body.candidate_updates,
        "route": body.route.model_dump(mode="json") if body.route else None,
    }
    if not any(
        (body.title, body.target_kind, body.candidate_updates, body.route is not None)
    ):
        raise HTTPException(422, "an enrichment must change at least one field")
    await _guard_triage_write(runtime, session, "triage.item.enriched", payload)
    envelope = _triage_event(
        session,
        event_type="triage.item.enriched",
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        causation_id=item["last_event_id"],
        sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
    )
    event_id = await runtime.events.write(envelope)
    await runtime.wait_for_projection("triage", event_id)
    return {
        "event_id": event_id,
        "triage": _owned_reviewable_triage_item(runtime, session, proposal_id),
    }


@router.post("/api/member/v1/triage/{proposal_id}/approve")
async def approve_triage_item(
    proposal_id: str,
    body: TriageDecisionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.triage_command_locks.setdefault(
        f"{session.tenant_id}:{proposal_id}", asyncio.Lock()
    )
    async with lock:
        return await _approve_triage_item_locked(
            runtime, proposal_id, body, request, session
        )


async def _approve_triage_item_locked(
    runtime: Any,
    proposal_id: str,
    body: TriageDecisionRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    item = _owned_triage_item(runtime, session, proposal_id)
    if item.get("decision_disposition") != "decision":
        raise HTTPException(
            409,
            "supporting or existing-object evidence cannot create duplicate household truth",
        )
    if item["source_kind"] == "commitment_proposal":
        result = await comms.approve_commitment(
            proposal_id,
            comms.ApproveRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                send_acknowledgement=False,
            ),
            request,
            session,
        )
        await runtime.wait_for_projection("triage", result["event_id"])
        return {**result, "triage_state": "confirmed"}

    request_fingerprint = _triage_request_fingerprint(proposal_id, body)
    prior_confirmation = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.item.confirmed",
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior_confirmation is not None:
        await runtime.wait_for_projection("triage", prior_confirmation["event_id"])
        if int(prior_confirmation.get("schema_version") or 1) == 1:
            return {
                "event_id": prior_confirmation["event_id"],
                "state": "confirmed",
                "materialized_event_ids": prior_confirmation["payload"][
                    "materialized_event_ids"
                ],
                "destination": {
                    **_triage_destination(item),
                    "state": "materialized",
                },
                "duplicate": True,
            }
        prior_receipt = await _prior_triage_command(
            runtime,
            session,
            event_type="triage.item.materialized",
            request_id=body.request_id,
            request_fingerprint=request_fingerprint,
        )
        if prior_receipt is not None:
            await runtime.wait_for_projection("triage", prior_receipt["event_id"])
            return {
                "event_id": prior_receipt["event_id"],
                "confirmation_event_id": prior_confirmation["event_id"],
                "state": "confirmed",
                "materialized_event_ids": prior_receipt["payload"][
                    "materialized_event_ids"
                ],
                "destination": {
                    **_triage_destination(item),
                    "state": "materialized",
                },
                "duplicate": True,
            }
        if (
            _triage_candidate_fingerprint(item)
            != prior_confirmation["payload"]["candidate_fingerprint"]
        ):
            raise HTTPException(
                409, "triage candidate changed after confirmation; run Doctor"
            )
        confirmation_event_id = str(prior_confirmation["event_id"])
    else:
        _require_current_triage(item, body.expected_last_event_id)
        # Validate the full closed materializer before recording the decision.
        _materialization_envelopes(runtime, session, item, request_id=body.request_id)
        confirm_payload_for_gate = {
            "proposal_id": proposal_id,
            "confirmed_by_member_id": session.auth_member_id,
            "target_kind": item["target_kind"],
        }
        await _guard_triage_write(
            runtime, session, "triage.item.confirmed", confirm_payload_for_gate
        )
        confirmed_at = EventEnvelope.now_utc_iso()
        confirmation = _triage_event(
            session,
            event_type="triage.item.confirmed",
            schema_version=2,
            payload={
                "proposal_id": proposal_id,
                "confirmed_at": confirmed_at,
                "confirmed_by_member_id": session.auth_member_id,
                "expected_last_event_id": body.expected_last_event_id,
                "candidate_fingerprint": _triage_candidate_fingerprint(item),
            },
            request_id=body.request_id,
            request_fingerprint=request_fingerprint,
            causation_id=item["last_event_id"],
            sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
        )
        confirmation_event_id = await runtime.events.write(confirmation)
        await runtime.wait_for_projection("triage", confirmation_event_id)

    # Projection acceptance, not append success, authorizes materialization.
    # Canonical truth can arrive after the API read but before this decision is
    # projected; in that race the handler deliberately rejects the transition.
    confirmed_item = _owned_triage_item(runtime, session, proposal_id)
    if not (
        confirmed_item.get("decision_disposition") == "decision"
        and confirmed_item.get("state") == "approved"
        and confirmed_item.get("domain_approval_event_id") == confirmation_event_id
        and confirmed_item.get("confirmation_event_id") == confirmation_event_id
        and confirmed_item.get("confirmation_request_id") == body.request_id
    ):
        raise HTTPException(
            409,
            "triage confirmation was superseded before materialization; refresh",
        )
    item = confirmed_item

    if item["target_kind"] in {"calendar_event", "reminder", "list", "list_item"}:
        [external_candidate] = _materialization_envelopes(
            runtime,
            session,
            item,
            request_id=body.request_id,
            causation_id=confirmation_event_id,
        )
        (
            pending_event_id,
            pending_duplicate,
        ) = await _append_provider_action_confirmation(
            runtime,
            session,
            command_payload=external_candidate.payload,
            request_id=f"triage:{proposal_id}:{body.request_id}",
            request_fingerprint=_triage_candidate_fingerprint(item),
            source_adapter="member-triage",
            sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
            causation_id=confirmation_event_id,
            origin_proposal_id=proposal_id,
        )
        await runtime.wait_for_projection("triage", pending_event_id)
        return {
            "event_id": confirmation_event_id,
            "confirmation_event_id": confirmation_event_id,
            "pending_provider_action_event_id": pending_event_id,
            "state": "approved_awaiting_openclaw",
            "materialized_event_ids": [],
            "destination": {
                **_triage_destination(item),
                "state": "awaiting_openclaw_approval",
            },
            "duplicate": prior_confirmation is not None and pending_duplicate,
        }

    materializations = _materialization_envelopes(
        runtime,
        session,
        item,
        request_id=body.request_id,
        causation_id=confirmation_event_id,
    )
    prior_materializations = [
        await runtime.event_log.get_by_idempotency(
            session.tenant_id, cast(str, envelope.idempotency_key)
        )
        for envelope in materializations
    ]
    if any(prior_materializations) and not all(prior_materializations):
        raise HTTPException(409, "triage materialization is incomplete; run Doctor")
    if all(prior_materializations) and any(
        cast(dict[str, Any], prior).get("raw_ref") != envelope.raw_ref
        for prior, envelope in zip(
            prior_materializations, materializations, strict=True
        )
    ):
        raise HTTPException(
            409, "triage candidate changed after materialization; run Doctor"
        )
    if all(prior_materializations):
        materialized_event_ids = [
            cast(dict[str, Any], prior)["event_id"] for prior in prior_materializations
        ]
    else:
        for envelope in materializations:
            await _guard_triage_write(runtime, session, envelope.type, envelope.payload)
        materialized_event_ids = await runtime.events.write_batch(materializations)
    await _wait_for_materialized_destinations(
        runtime, materializations, materialized_event_ids
    )
    materialized_at = EventEnvelope.now_utc_iso()
    receipt = _triage_event(
        session,
        event_type="triage.item.materialized",
        payload={
            "proposal_id": proposal_id,
            "confirmation_event_id": confirmation_event_id,
            "materialized_event_ids": materialized_event_ids,
            "materialized_at": materialized_at,
        },
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        causation_id=materialized_event_ids[-1],
        sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
    )
    event_id = await runtime.events.write(receipt)
    await runtime.wait_for_projection("triage", event_id)
    return {
        "event_id": event_id,
        "confirmation_event_id": confirmation_event_id,
        "state": "confirmed",
        "materialized_event_ids": materialized_event_ids,
        "destination": {**_triage_destination(item), "state": "materialized"},
    }


def _require_context_correction_principal(session: Session) -> None:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "Context corrections are unavailable while viewing another member"
        )


def _combined_sensitivity(*sources: dict[str, Any]) -> Sensitivity:
    rank = {"normal": 0, "sensitive": 1, "privileged": 2}
    return cast(
        Sensitivity,
        max(
            (str(source.get("sensitivity") or "normal") for source in sources),
            key=lambda value: rank.get(value, 0),
        ),
    )


@router.post("/api/member/v1/context-relationships/confirm")
async def confirm_context_relationship(
    body: ContextRelationshipConfirmRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Explicitly reverse/relink one exact member-owned source pair."""

    _require_context_correction_principal(session)
    if body.source_event_id == body.related_source_event_id:
        raise HTTPException(422, "context relationship sources must be distinct")
    runtime = _runtime(request)
    request_fingerprint = _triage_request_fingerprint(body.source_event_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="context.relationship.confirmed",
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("context_fabric", prior["event_id"])
        return {"event_id": prior["event_id"], "state": "confirmed", "duplicate": True}
    source = await _owned_message_source(runtime, session, body.source_event_id)
    related = await _owned_message_source(
        runtime, session, body.related_source_event_id
    )
    provider, provider_account_id, workspace = _require_same_context_coordinate(
        source, related
    )
    confirmed_at = EventEnvelope.now_utc_iso()
    payload = {
        "relationship_id": _context_relationship_id(
            session.tenant_id,
            session.auth_member_id,
            body.source_event_id,
            body.related_source_event_id,
        ),
        "source_event_id": body.source_event_id,
        "related_source_event_id": body.related_source_event_id,
        "member_id": session.auth_member_id,
        "provider": provider,
        "provider_account_id": provider_account_id,
        "workspace": workspace,
        "algorithm": "message-context-pack-v2",
        "confirmed_by_member_id": session.auth_member_id,
        "confirmed_at": confirmed_at,
        "reason": "member_reinstated",
    }
    await _guard_triage_write(
        runtime, session, "context.relationship.confirmed", payload
    )
    envelope = _triage_event(
        session,
        event_type="context.relationship.confirmed",
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        causation_id=body.source_event_id,
        sensitivity=_combined_sensitivity(source, related),
    )
    event_id = await runtime.events.write(envelope)
    await runtime.wait_for_projection("context_fabric", event_id)
    return {"event_id": event_id, "state": "confirmed"}


@router.post("/api/member/v1/context-source-text/reinstate")
async def reinstate_context_source_text(
    body: ContextSourceTextReinstateRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Reverse a quoted-history correction for one exact source."""

    _require_context_correction_principal(session)
    runtime = _runtime(request)
    request_fingerprint = _triage_request_fingerprint(body.source_event_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="context.source_text.reinstated",
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("context_fabric", prior["event_id"])
        return {"event_id": prior["event_id"], "state": "reinstated", "duplicate": True}
    source = await _owned_message_source(runtime, session, body.source_event_id)
    await _owned_active_source_text_correction(
        runtime,
        session,
        body.correction_id,
        body.source_event_id,
    )
    reinstated_at = EventEnvelope.now_utc_iso()
    payload = {
        "correction_id": body.correction_id,
        "source_event_id": body.source_event_id,
        "member_id": session.auth_member_id,
        "reinstated_by_member_id": session.auth_member_id,
        "reinstated_at": reinstated_at,
        "reason": "member_reinstated",
    }
    await _guard_triage_write(
        runtime, session, "context.source_text.reinstated", payload
    )
    envelope = _triage_event(
        session,
        event_type="context.source_text.reinstated",
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        causation_id=body.source_event_id,
        sensitivity=cast(Sensitivity, source.get("sensitivity") or "normal"),
    )
    event_id = await runtime.events.write(envelope)
    await runtime.wait_for_projection("context_fabric", event_id)
    return {"event_id": event_id, "state": "reinstated"}


@router.post("/api/member/v1/triage/{proposal_id}/dismiss")
async def dismiss_triage_item(
    proposal_id: str,
    body: TriageDecisionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.triage_command_locks.setdefault(
        f"{session.tenant_id}:{proposal_id}", asyncio.Lock()
    )
    async with lock:
        return await _dismiss_triage_item_locked(
            runtime, proposal_id, body, request, session
        )


async def _dismiss_triage_item_locked(
    runtime: Any,
    proposal_id: str,
    body: TriageDecisionRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    item = _owned_triage_item(runtime, session, proposal_id)
    correction = body.context_correction
    if correction is not None and body.feedback_reason != "wrong_context":
        raise HTTPException(422, "context correction requires wrong-context feedback")
    if correction is not None and not (
        correction.unrelated_source_event_ids or correction.quoted_or_forwarded_history
    ):
        raise HTTPException(422, "context correction must select an exact source")
    if item["source_kind"] == "commitment_proposal":
        if correction is not None:
            raise HTTPException(
                422, "context correction is unavailable for commitments"
            )
        result = await comms.dismiss_commitment(
            proposal_id,
            comms.DismissRequest(
                request_id=body.request_id,
                expected_last_event_id=body.expected_last_event_id,
                reason=body.reason,
            ),
            request,
            session,
        )
        await runtime.wait_for_projection("triage", result["event_id"])
        return {**result, "triage_state": "dismissed"}
    request_fingerprint = _triage_request_fingerprint(proposal_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.item.dismissed",
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("triage", prior["event_id"])
        return {"event_id": prior["event_id"], "state": "dismissed", "duplicate": True}
    _require_current_triage(
        item,
        body.expected_last_event_id,
        allow_pending_interpretation=True,
    )
    payload = {
        "proposal_id": proposal_id,
        "dismissed_at": EventEnvelope.now_utc_iso(),
        "dismissed_by_member_id": session.auth_member_id,
        "reason": body.reason,
    }
    await _guard_triage_write(runtime, session, "triage.item.dismissed", payload)
    envelope = _triage_event(
        session,
        event_type="triage.item.dismissed",
        payload=payload,
        request_id=body.request_id,
        request_fingerprint=request_fingerprint,
        causation_id=item["last_event_id"],
        sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
    )
    envelopes = [envelope]
    correction_envelope_indexes: list[int] = []
    if correction is not None:
        interpretation = item.get("interpretation") or {}
        context_sources = {
            str(value.get("source_event_id")): value
            for value in interpretation.get("context_sources") or []
            if isinstance(value, dict) and value.get("source_event_id")
        }
        allowed_related_source_ids = {
            str(value)
            for value in interpretation.get("related_source_event_ids") or []
            if value
        }
        allowed_related_source_ids.update(
            str(value.get("source_event_id"))
            for value in interpretation.get("context_sources") or []
            if isinstance(value, dict)
            and value.get("relation") == "related"
            and value.get("source_event_id")
        )
        requested_related_source_ids = list(
            dict.fromkeys(correction.unrelated_source_event_ids)
        )
        if any(
            event_id not in allowed_related_source_ids
            for event_id in requested_related_source_ids
        ):
            raise HTTPException(
                422, "an unrelated-source correction must select displayed context"
            )
        corrected_at = EventEnvelope.now_utc_iso()
        relationship_index = 0
        for related_source_event_id in requested_related_source_ids:
            related = await _owned_message_source(
                runtime, session, related_source_event_id
            )
            context_source = context_sources.get(related_source_event_id) or {}
            relationship_source_ids = list(
                dict.fromkeys(
                    str(value)
                    for value in context_source.get("relationship_source_event_ids", [])
                    if value
                )
            )
            if not relationship_source_ids:
                raise HTTPException(
                    422,
                    "displayed context lacks exact relationship-edge provenance",
                )
            for relationship_source_event_id in relationship_source_ids:
                relationship_source = await _owned_message_source(
                    runtime, session, relationship_source_event_id
                )
                provider, provider_account_id, workspace = (
                    _require_same_context_coordinate(relationship_source, related)
                )
                correction_payload = {
                    "relationship_id": _context_relationship_id(
                        session.tenant_id,
                        session.auth_member_id,
                        relationship_source_event_id,
                        related_source_event_id,
                    ),
                    "source_event_id": relationship_source_event_id,
                    "related_source_event_id": related_source_event_id,
                    "member_id": session.auth_member_id,
                    "provider": provider,
                    "provider_account_id": provider_account_id,
                    "workspace": workspace,
                    "algorithm": "message-context-pack-v2",
                    "rejected_by_member_id": session.auth_member_id,
                    "rejected_at": corrected_at,
                    "reason": "unrelated_sources",
                }
                await _guard_triage_write(
                    runtime,
                    session,
                    "context.relationship.rejected",
                    correction_payload,
                )
                correction_envelope_indexes.append(len(envelopes))
                envelopes.append(
                    _triage_event(
                        session,
                        event_type="context.relationship.rejected",
                        payload=correction_payload,
                        request_id=(f"{body.request_id}:context:{relationship_index}"),
                        request_fingerprint=request_fingerprint,
                        causation_id=item["last_event_id"],
                        sensitivity=cast(
                            Sensitivity, item.get("sensitivity") or "normal"
                        ),
                    )
                )
                relationship_index += 1
        if correction.quoted_or_forwarded_history:
            source = await _owned_message_source(
                runtime, session, str(item["source_event_id"])
            )
            evidence_quote = next(
                (
                    str(ref.get("quote") or "")
                    for ref in interpretation.get("evidence_refs") or []
                    if isinstance(ref, dict)
                    and str(ref.get("source_event_id") or "")
                    == str(item["source_event_id"])
                    and ref.get("quote")
                ),
                "",
            )
            authored = authored_message_text(source.get("payload") or {})
            if not authored.reliable or not evidence_quote:
                raise HTTPException(
                    422, "quoted-history correction requires reliable exact evidence"
                )
            evidence_start = authored.text.find(evidence_quote)
            if (
                evidence_start < 0
                or authored.text.find(evidence_quote, evidence_start + 1) >= 0
            ):
                raise HTTPException(
                    422,
                    "quoted-history evidence must occur exactly once in authored text",
                )
            evidence_end = evidence_start + len(evidence_quote)
            evidence_quote_digest = hashlib.sha256(
                evidence_quote.encode("utf-8")
            ).hexdigest()
            text_payload = {
                "correction_id": _context_source_correction_id(
                    session.tenant_id,
                    session.auth_member_id,
                    str(item["source_event_id"]),
                    proposal_id,
                    str(item.get("candidate_key") or "candidate"),
                    evidence_quote_digest,
                ),
                "source_event_id": str(item["source_event_id"]),
                "proposal_id": proposal_id,
                "candidate_key": str(item.get("candidate_key") or "candidate"),
                "member_id": session.auth_member_id,
                "algorithm": "authored-message-text-v2",
                "authored_text_digest": hashlib.sha256(
                    authored.text.encode("utf-8")
                ).hexdigest(),
                "evidence_start": evidence_start,
                "evidence_end": evidence_end,
                "corrected_by_member_id": session.auth_member_id,
                "corrected_at": corrected_at,
                "reason": "quoted_or_forwarded_history",
                "evidence_quote_digest": evidence_quote_digest,
            }
            await _guard_triage_write(
                runtime, session, "context.source_text.corrected", text_payload
            )
            correction_envelope_indexes.append(len(envelopes))
            envelopes.append(
                _triage_event(
                    session,
                    event_type="context.source_text.corrected",
                    payload=text_payload,
                    request_id=f"{body.request_id}:context:text",
                    request_fingerprint=request_fingerprint,
                    causation_id=item["last_event_id"],
                    sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
                )
            )
    if body.feedback_reason is not None:
        feedback_source = (
            await runtime.event_log.get(str(item["source_event_id"]))
            if runtime.event_log is not None
            else None
        )
        source_payload = (
            feedback_source.get("payload") or {}
            if isinstance(feedback_source, dict)
            else {}
        )
        sender = str(
            source_payload.get("from_identifier")
            or source_payload.get("from_number")
            or ""
        ).strip()
        recorded_at = EventEnvelope.now_utc_iso()
        feedback_id = (
            "feedback_"
            + hashlib.sha256(
                (
                    f"{session.tenant_id}|{session.auth_member_id}|{proposal_id}|"
                    f"{body.request_id}|{body.feedback_reason}"
                ).encode()
            ).hexdigest()[:28]
        )
        envelopes.append(
            _triage_event(
                session,
                event_type="triage.feedback.recorded",
                payload={
                    "feedback_id": feedback_id,
                    "proposal_id": proposal_id,
                    "member_id": session.auth_member_id,
                    "reason": body.feedback_reason,
                    "context_id": item.get("context_id") or None,
                    "target_kind": item.get("target_kind"),
                    "source_adapter": (
                        str(feedback_source.get("source_adapter"))
                        if feedback_source
                        else None
                    ),
                    "provider_account_id": (
                        str(
                            source_payload.get("provider_account_id")
                            or feedback_source.get("source_account_id")
                        )
                        if feedback_source
                        else None
                    ),
                    "workspace": (
                        source_payload.get("workspace")
                        if source_payload.get("workspace") in {"personal", "work"}
                        else None
                    ),
                    "sender_digest": (
                        hashlib.sha256(sender.casefold().encode()).hexdigest()
                        if sender
                        else None
                    ),
                    "recorded_at": recorded_at,
                },
                request_id=f"{body.request_id}:feedback",
                request_fingerprint=request_fingerprint,
                causation_id=item["last_event_id"],
                sensitivity=cast(Sensitivity, item.get("sensitivity") or "normal"),
            )
        )
    event_ids = await runtime.events.write_batch(envelopes)
    event_id = event_ids[0]
    await runtime.wait_for_projection("triage", event_id)
    if len(event_ids) > 1:
        await runtime.wait_for_projection("context_fabric", event_ids[-1])
    result = {"event_id": event_id, "state": "dismissed"}
    feedback_event_index = next(
        (
            index
            for index, value in enumerate(envelopes)
            if value.type == "triage.feedback.recorded"
        ),
        None,
    )
    if feedback_event_index is not None:
        result["feedback_event_id"] = event_ids[feedback_event_index]
    if correction_envelope_indexes:
        result["context_correction_event_ids"] = [
            event_ids[index] for index in correction_envelope_indexes
        ]
    return result


def _chat_policy(runtime: Any, session: Session) -> dict[str, Any]:
    policy = runtime.member_chat_policy(session.auth_member_id, session.auth_role)
    if session.auth_member_id != session.view_member_id:
        return {
            "state": "unauthorized",
            "enabled": False,
            "reason": "view_as_has_no_chat",
        }
    return policy


def _require_chat(runtime: Any, session: Session) -> tuple[str, str]:
    policy = _chat_policy(runtime, session)
    if not policy.get("enabled"):
        raise HTTPException(404, "chat not found")
    if policy.get("state") != "healthy":
        raise HTTPException(503, {"code": policy.get("reason", "chat_unavailable")})
    return runtime.member_chat_binding(session.auth_member_id)


def _safe_error(exc: MemberChatError, correlation_id: str) -> dict[str, Any]:
    return {
        "kind": "error",
        "code": exc.code,
        "retryable": exc.retryable,
        "correlation_id": correlation_id,
    }


def _sse(value: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(value, separators=(',', ':'))}\n\n".encode("utf-8")


async def _expire_due(runtime: Any, session: Session) -> None:
    for spec in runtime.temporary_surfaces.expire_due():
        await _audit_surface(runtime, session, spec, "expired")


async def _audit_surface(
    runtime: Any,
    session: Session,
    spec: TemporarySurfaceSpec,
    transition: Literal["created", "activated", "promoted", "expired", "archived"],
) -> str:
    now = EventEnvelope.now_utc_iso()
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type=f"temporary_surface.{transition}",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-portal",
            source_account_id="temporary-surfaces",
            owner_scope=f"private:{spec.owner_member_id}",
            visibility_scope=f"private:{spec.owner_member_id}",
            sensitivity="normal",
            actor_identity=session.auth_member_id,
            payload={
                "stable_id": spec.stable_id,
                "owner_member_id": spec.owner_member_id,
                "actor_member_id": session.auth_member_id,
                "transition": transition,
                "audience_member_ids": list(spec.audience_member_ids),
                "specification_version": spec.updated_version,
                "occurred_at": now,
            },
            idempotency_key=f"temporary-surface:{spec.stable_id}:{transition}:{spec.updated_version}",
        ),
        correlation_id=session.correlation_id,
    )


def _visible_spec(
    runtime: Any, session: Session, stable_id: str
) -> TemporarySurfaceSpec:
    spec = runtime.temporary_surfaces.get(stable_id)
    if (
        spec is None
        or spec.status != "active"
        or session.auth_member_id not in spec.audience_member_ids
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(404, "temporary surface not found")
    if session.auth_role in {"device", "ambient"}:
        raise HTTPException(404, "temporary surface not found")
    if session.auth_role == "child":
        if "child" not in spec.compatible_modes:
            raise HTTPException(404, "temporary surface not found")
        if {query["name"] for query in spec.queries} - CHILD_QUERIES:
            raise HTTPException(404, "temporary surface not found")
        if set(spec.action_verbs) - CHILD_ACTIONS:
            raise HTTPException(404, "temporary surface not found")
    return spec


def _member_command_fingerprint(item_kind: str, item_id: str, body: BaseModel) -> str:
    canonical = json.dumps(
        {
            "item_kind": item_kind,
            "item_id": item_id,
            "body": body.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _prior_member_command(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    request_id: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    if runtime.event_log is None:
        raise HTTPException(503, "event log unavailable")
    key = f"member-command:{event_type}:{session.auth_member_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior["raw_ref"] != f"request-sha256:{fingerprint}":
        raise HTTPException(409, "idempotency key conflicts with prior action body")
    return prior


async def _guard_member_write(
    runtime: Any, session: Session, event_type: str, payload: dict[str, Any]
) -> None:
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(session, event_type, payload)
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )


def _member_command_event(
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    fingerprint: str,
    causation_id: str | None,
    owner_scope: str,
    visibility_scope: str,
    sensitivity: Sensitivity,
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="member-portal",
        source_account_id=session.auth_member_id,
        owner_scope=owner_scope,
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,
        correlation_id=session.correlation_id,
        causation_id=causation_id,
        actor_identity=session.auth_member_id,
        idempotency_key=(
            f"member-command:{event_type}:{session.auth_member_id}:{request_id}"
        ),
        raw_ref=f"request-sha256:{fingerprint}",
        payload=payload,
    )


async def _recurrence_lifecycle(
    body: MemberLifecycleRequest, request: Request, session: Session
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "recurrence changes are unavailable while viewing another member"
        )
    if body.action not in {"complete", "pause", "resume", "end"}:
        raise HTTPException(422, "unsupported recurrence lifecycle action")
    runtime = _runtime(request)
    event_type = {
        "complete": "recurrence.completed",
        "pause": "recurrence.paused",
        "resume": "recurrence.resumed",
        "end": "recurrence.ended",
    }[body.action]
    fingerprint = _member_command_fingerprint("recurrence", body.item_id, body)
    prior = await _prior_member_command(
        runtime,
        session,
        event_type=event_type,
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        return {
            "event_id": prior["event_id"],
            "duplicate": True,
            "recurrence": get_recurrence(
                runtime.projections.connection("recurrences"),
                session,
                recurrence_id=body.item_id,
            ),
            "what_now": core._what_now(runtime, session),
        }
    row = get_recurrence(
        runtime.projections.connection("recurrences"),
        session,
        recurrence_id=body.item_id,
    )
    if row is None:
        raise HTTPException(404, "trackable recurrence not found")
    owned = (
        row["linked_kind"] == "party" and row["linked_id"] == session.auth_member_id
    ) or (row["linked_kind"] == "household" and session.auth_role == "principal")
    if not owned:
        raise HTTPException(404, "recurrence not found")
    if body.action == "complete" and not row.get("trackable"):
        raise HTTPException(409, "this recurrence does not track completion")
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "recurrence changed; refresh before changing it")
    now = EventEnvelope.now_utc_iso()
    status = str(row.get("status") or "active")
    payload: dict[str, Any]
    if body.action == "complete":
        if status != "active":
            raise HTTPException(409, "only active recurrences can be completed")
        payload = {
            "recurrence_id": body.item_id,
            "completed_at": now,
            "completed_by_party_id": session.auth_member_id,
            "occurrence_date": str(row["next_occurrence"])[:10],
        }
    elif body.action == "pause":
        if status != "active":
            raise HTTPException(409, "only active recurrences can be paused")
        payload = {
            "recurrence_id": body.item_id,
            "paused_at": now,
            "paused_by_member_id": session.auth_member_id,
            "reason": body.reason,
        }
    elif body.action == "resume":
        if status != "paused":
            raise HTTPException(409, "only paused recurrences can be resumed")
        payload = {
            "recurrence_id": body.item_id,
            "resumed_at": now,
            "resumed_by_member_id": session.auth_member_id,
        }
    else:
        if status not in {"active", "paused"}:
            raise HTTPException(409, "recurrence is already ended")
        payload = {
            "recurrence_id": body.item_id,
            "ended_at": now,
            "ended_by_member_id": session.auth_member_id,
            "reason": body.reason,
        }
    await _guard_member_write(runtime, session, event_type, payload)
    event = _member_command_event(
        session,
        event_type=event_type,
        payload=payload,
        request_id=body.request_id,
        fingerprint=fingerprint,
        causation_id=row["last_event_id"],
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=cast(Sensitivity, row.get("sensitivity") or "normal"),
    )
    try:
        event_id = await runtime.events.write(event)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior action body"
        ) from exc
    await runtime.wait_for_projection("recurrences", event_id)
    return {
        "event_id": event_id,
        "duplicate": False,
        "recurrence": get_recurrence(
            runtime.projections.connection("recurrences"),
            session,
            recurrence_id=body.item_id,
        ),
        "what_now": core._what_now(runtime, session),
    }


async def _routine_lifecycle(
    body: MemberLifecycleRequest, request: Request, session: Session
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "routine changes are unavailable while viewing another member"
        )
    if body.action not in {"pause", "resume", "end"}:
        raise HTTPException(422, "unsupported routine lifecycle action")
    runtime = _runtime(request)
    event_type = f"routine.{ {'pause': 'paused', 'resume': 'resumed', 'end': 'ended'}[body.action] }"
    fingerprint = _member_command_fingerprint("routine", body.item_id, body)
    prior = await _prior_member_command(
        runtime,
        session,
        event_type=event_type,
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    row = get_routine(
        runtime.projections.connection("recurrences"),
        session,
        routine_id=body.item_id,
    )
    if row is None or row.get("member_id") != session.auth_member_id:
        raise HTTPException(404, "routine not found")
    if prior is not None:
        return {
            "event_id": prior["event_id"],
            "duplicate": True,
            "routine": row,
            "what_now": core._what_now(runtime, session),
        }
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "routine changed; refresh before changing it")
    status = str(row.get("status") or "active")
    if body.action == "pause" and status != "active":
        raise HTTPException(409, "only active routines can be paused")
    if body.action == "resume" and status != "paused":
        raise HTTPException(409, "only paused routines can be resumed")
    if body.action == "end" and status not in {"active", "paused"}:
        raise HTTPException(409, "routine is already ended")
    now = EventEnvelope.now_utc_iso()
    prefix = {"pause": "paused", "resume": "resumed", "end": "ended"}[body.action]
    payload: dict[str, Any] = {
        "routine_id": body.item_id,
        f"{prefix}_at": now,
        f"{prefix}_by_member_id": session.auth_member_id,
    }
    if body.action in {"pause", "end"}:
        payload["reason"] = body.reason
    await _guard_member_write(runtime, session, event_type, payload)
    event = _member_command_event(
        session,
        event_type=event_type,
        payload=payload,
        request_id=body.request_id,
        fingerprint=fingerprint,
        causation_id=row["last_event_id"],
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=cast(Sensitivity, row.get("sensitivity") or "normal"),
    )
    try:
        event_id = await runtime.events.write(event)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior action body"
        ) from exc
    await runtime.wait_for_projection("recurrences", event_id)
    return {
        "event_id": event_id,
        "duplicate": False,
        "routine": get_routine(
            runtime.projections.connection("recurrences"),
            session,
            routine_id=body.item_id,
        ),
        "what_now": core._what_now(runtime, session),
    }


def _calendar_belongs_to_member(row: dict[str, Any], member_id: str) -> bool:
    if row.get("member_id") == member_id or row.get("owner_party") == member_id:
        return True
    try:
        attendees = json.loads(row.get("attendees_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        return False
    return any(
        attendee == member_id
        or (isinstance(attendee, dict) and attendee.get("party_id") == member_id)
        for attendee in attendees
    )


async def _record_calendar_outcome(
    calendar_event_id: str,
    body: CalendarOutcomeRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "calendar outcomes are unavailable while viewing another member"
        )
    runtime = _runtime(request)
    fingerprint = _member_command_fingerprint("calendar_event", calendar_event_id, body)
    prior = await _prior_member_command(
        runtime,
        session,
        event_type="calendar.event_outcome_recorded",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        return {
            "event_id": prior["event_id"],
            "duplicate": True,
            "brief": build_surface(runtime, session, "today")["brief"],
        }
    row = get_calendar_event(
        runtime.projections.connection("calendars"),
        session,
        calendar_event_id=calendar_event_id,
    )
    if row is None or not _calendar_belongs_to_member(row, session.auth_member_id):
        raise HTTPException(404, "calendar event not found")
    if row["calendar_source"] not in {"google_calendar", "apple_calendar"}:
        raise HTTPException(409, "this calendar source cannot record an outcome")
    prior_outcome = get_calendar_outcome(
        runtime.projections.connection("calendars"),
        session,
        member_id=session.auth_member_id,
        calendar_source=row["calendar_source"],
        external_uid=row["external_uid"],
    )
    if prior_outcome is not None:
        raise HTTPException(
            409, "a calendar outcome is already recorded; use a correction flow"
        )
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "calendar event changed; refresh before recording it")
    try:
        start = datetime.fromisoformat(str(row["start_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(409, "calendar event has an invalid start time") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) < start.astimezone(timezone.utc):
        raise HTTPException(
            409, "attendance cannot be recorded before the event starts"
        )
    payload = {
        "member_id": session.auth_member_id,
        "provider": row["calendar_source"],
        "external_event_id": row["external_uid"],
        "outcome": body.outcome,
        "attendance_status": body.outcome,
        "evidence": "member_confirmation",
        "confidence": 1.0,
        "recorded_at": EventEnvelope.now_utc_iso(),
        "linked_item_kind": row.get("linked_item_kind"),
        "linked_item_id": row.get("linked_item_id"),
        "completion_policy": row.get("completion_policy") or "manual",
    }
    await _guard_member_write(
        runtime, session, "calendar.event_outcome_recorded", payload
    )
    event = _member_command_event(
        session,
        event_type="calendar.event_outcome_recorded",
        payload=payload,
        request_id=body.request_id,
        fingerprint=fingerprint,
        causation_id=row["last_event_id"],
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=cast(Sensitivity, row.get("sensitivity") or "normal"),
    )
    try:
        event_id = await runtime.events.write(event)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior action body"
        ) from exc
    for projection in ("calendars", "tasks", "commitments"):
        await runtime.wait_for_projection(projection, event_id)
    return {
        "event_id": event_id,
        "duplicate": False,
        "brief": build_surface(runtime, session, "today")["brief"],
    }


async def _record_routine_step_outcome(
    routine_id: str,
    occurrence_date: str,
    step_id: str,
    body: RoutineStepOutcomeRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "routine progress is unavailable while viewing another member"
        )
    runtime = _runtime(request)
    fingerprint = _member_command_fingerprint(
        "routine_step", f"{routine_id}:{occurrence_date}:{step_id}", body
    )
    prior = await _prior_member_command(
        runtime,
        session,
        event_type="routine.step_outcome_recorded",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    now = datetime.now(timezone.utc)
    if prior is not None:
        return {
            "event_id": prior["event_id"],
            "duplicate": True,
            "routine": get_routine_occurrence(
                runtime.projections.connection("recurrences"),
                session,
                member_party_id=session.auth_member_id,
                routine_id=routine_id,
                occurrence_date=occurrence_date,
                as_of_iso=now.isoformat(),
            ),
            "brief": build_surface(runtime, session, "today")["brief"],
        }
    row = get_routine_occurrence(
        runtime.projections.connection("recurrences"),
        session,
        member_party_id=session.auth_member_id,
        routine_id=routine_id,
        occurrence_date=occurrence_date,
        as_of_iso=now.isoformat(),
    )
    if row is None:
        raise HTTPException(404, "routine occurrence not found")
    if row["status"] in {"completed", "completed_with_skips"}:
        raise HTTPException(409, "routine occurrence is already closed")
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "routine changed; refresh before recording progress")
    current_step = row.get("current_step")
    if not isinstance(current_step, dict) or current_step.get("step_id") != step_id:
        raise HTTPException(409, "routine steps must be handled in order")
    payload = {
        "routine_id": routine_id,
        "occurrence_date": occurrence_date,
        "step_id": step_id,
        "outcome": body.outcome,
        "recorded_at": EventEnvelope.now_utc_iso(),
        "recorded_by_member_id": session.auth_member_id,
    }
    await _guard_member_write(
        runtime, session, "routine.step_outcome_recorded", payload
    )
    event = _member_command_event(
        session,
        event_type="routine.step_outcome_recorded",
        payload=payload,
        request_id=body.request_id,
        fingerprint=fingerprint,
        causation_id=row["last_event_id"],
        owner_scope=row["owner_scope"],
        visibility_scope=row["visibility_scope"],
        sensitivity=cast(Sensitivity, row.get("sensitivity") or "normal"),
    )
    try:
        event_id = await runtime.events.write(event)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior action body"
        ) from exc
    await runtime.wait_for_projection("recurrences", event_id)
    refreshed_at = datetime.now(timezone.utc).isoformat()
    return {
        "event_id": event_id,
        "duplicate": False,
        "routine": get_routine_occurrence(
            runtime.projections.connection("recurrences"),
            session,
            member_party_id=session.auth_member_id,
            routine_id=routine_id,
            occurrence_date=occurrence_date,
            as_of_iso=refreshed_at,
        ),
        "brief": build_surface(runtime, session, "today")["brief"],
    }


async def _execute_lifecycle(
    body: MemberLifecycleRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    runtime = _runtime(request)
    if body.item_kind == "recurrence":
        lock = runtime.today_command_locks.setdefault(
            f"{session.tenant_id}:recurrence:{body.item_id}", asyncio.Lock()
        )
        async with lock:
            return await _recurrence_lifecycle(body, request, session)
    if body.item_kind == "routine":
        lock = runtime.today_command_locks.setdefault(
            f"{session.tenant_id}:routine:{body.item_id}:definition", asyncio.Lock()
        )
        async with lock:
            return await _routine_lifecycle(body, request, session)
    if body.item_kind == "task":
        if body.action not in {
            "complete",
            "defer",
            "snooze",
            "block",
            "unblock",
            "cancel",
            "reopen",
        }:
            raise HTTPException(422, "unsupported task lifecycle action")
        actor_id, signed_session_key = runtime.member_chat_binding(
            session.auth_member_id
        )
        return await _task_lifecycle(
            OpenClawLifecycleRequest(
                provenance=Provenance(
                    actor_id=actor_id,
                    agent_id="administrateme-member-portal",
                    session_key=signed_session_key,
                    session_id=None,
                    channel_id="administrateme-portal",
                    account_id="member-portal",
                    message_id=body.request_id,
                    thread_id=None,
                    correlation_id=session.correlation_id or body.request_id,
                    call_kind="tool",
                    openclaw_version=cast(Literal["2026.7.2"], OPENCLAW_VERSION),
                    openclaw_commit=cast(
                        Literal["ce67ffb70e83e5ebb56ea02e814e577153f84f61"],
                        OPENCLAW_COMMIT,
                    ),
                ),
                request_id=body.request_id,
                item_kind="task",
                item_id=body.item_id,
                action=cast(
                    Literal[
                        "complete",
                        "defer",
                        "snooze",
                        "block",
                        "unblock",
                        "cancel",
                        "reopen",
                    ],
                    body.action,
                ),
                expected_last_event_id=body.expected_last_event_id,
                until=body.until,
                reason=body.reason,
                note=body.note,
            ),
            request,
            session,
        )
    return await core.lifecycle(
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


@router.get("/", response_class=RedirectResponse)
async def dashboard_root(session: SessionDep) -> RedirectResponse:
    """Send an authenticated household member to the canonical portal URL."""
    return RedirectResponse(url="/member", status_code=307)


@router.get("/member")
@router.get("/member/{surface}")
async def member_page(
    request: Request,
    session: SessionDep,
    surface: str = "today",
) -> Response:
    if surface == "inbox":
        authorize_surface(session, "triage")
        return RedirectResponse(url="/member/triage", status_code=308)
    if surface == "knowledge":
        authorize_surface(session, "notes")
        return RedirectResponse(url="/member/notes", status_code=308)
    authorize_surface(session, surface)
    return _member_index_response()


@router.get("/member/quests/{stable_id}")
async def temporary_surface_page(
    stable_id: str, request: Request, session: SessionDep
) -> Response:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    spec = runtime.temporary_surfaces.get(stable_id)
    if spec is None or session.auth_member_id != session.view_member_id:
        raise HTTPException(404, "temporary surface not found")
    if spec.status == "active":
        _visible_spec(runtime, session, stable_id)
    elif spec.owner_member_id != session.auth_member_id:
        raise HTTPException(404, "temporary surface not found")
    return _member_index_response()


@router.get("/member-assets/{filename}")
async def member_asset(filename: str) -> FileResponse:
    if filename not in {"member.css", "member.js"}:
        raise HTTPException(404, "asset not found")
    media = "text/css" if filename.endswith(".css") else "text/javascript"
    return FileResponse(
        ASSET_ROOT / filename,
        media_type=media,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/member/v1/bootstrap")
async def bootstrap(request: Request, session: SessionDep) -> dict[str, Any]:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    return {
        "member": {
            "id": session.view_member_id,
            "role": session.view_role,
            "auth_role": session.auth_role,
            "display_name": member_label(runtime, session.view_member_id),
            "authenticated_id": session.auth_member_id,
            "authenticated_display_name": member_label(runtime, session.auth_member_id),
            "authenticated_role": session.auth_role,
            "authentication_method": "Tailscale",
            "viewing_as": session.auth_member_id != session.view_member_id,
        },
        "navigation": navigation(session),
        "mode": experience_mode(runtime, session),
        "observation_mode": runtime._observation_active,
        "chat": _chat_policy(runtime, session),
        "temporary_surfaces": [
            {
                "stable_id": spec.stable_id,
                "title": spec.title,
                "purpose": spec.purpose,
                "compatible_modes": list(spec.compatible_modes),
                "href": f"/member/quests/{spec.stable_id}",
            }
            for spec in runtime.temporary_surfaces.visible_to(session.auth_member_id)
            if session.auth_member_id == session.view_member_id
            and session.auth_role not in {"device", "ambient"}
            and (session.auth_role != "child" or "child" in spec.compatible_modes)
        ],
        "trust": {
            "operator_access": False,
            "actions_as": session.auth_member_id,
            "reads_as": session.view_member_id,
        },
    }


@router.get("/api/member/v1/surfaces/{surface}")
async def surface(
    surface: str,
    request: Request,
    session: SessionDep,
    calendar_date: date | None = Query(default=None, alias="date"),
    view: Literal["day", "week", "month", "agenda", "find", "rules"] = "week",
    triage_lens: Literal[
        "ready",
        "needs_details",
        "needs_interpretation",
        "low_signal",
        "recurring_exceptions",
        "learned_rules",
        "alerts",
        "all_candidates",
    ] = "all_candidates",
    triage_sort: Literal["attention", "oldest", "newest"] = "attention",
    triage_source: str | None = Query(default=None, max_length=240),
    triage_kind: str | None = Query(default=None, max_length=80),
    triage_cursor: str | None = Query(default=None, max_length=500),
    triage_limit: int = Query(default=30, ge=1, le=100),
    triage_item: str | None = Query(default=None, max_length=240),
) -> dict[str, Any]:
    runtime = _runtime(request)
    await runtime._refresh_capability_registry()
    if surface == "inbox":
        result = {
            **build_surface(runtime, session, "triage"),
            "deprecated_alias": "inbox",
            "canonical_path": "/api/member/v1/surfaces/triage",
        }
        await add_triage_source_context(runtime, session, result)
        return result
    if surface == "knowledge":
        return {
            **build_surface(runtime, session, "notes"),
            "deprecated_alias": "knowledge",
            "canonical_path": "/api/member/v1/surfaces/notes",
        }
    result = build_surface(
        runtime,
        session,
        surface,
        calendar_anchor=calendar_date,
        calendar_view=view,
        triage_lens=triage_lens,
        triage_sort=triage_sort,
        triage_source=triage_source,
        triage_kind=triage_kind,
        triage_cursor=triage_cursor,
        triage_limit=triage_limit,
        triage_item=triage_item,
    )
    if surface == "settings":
        if (
            session.auth_role == "principal"
            and session.auth_member_id == session.view_member_id
        ):
            result["control"]["cos_replies"] = {
                "visible": True,
                "can_mutate": True,
                **await runtime.settings_control.cos_response_policy(
                    runtime.event_log, session.tenant_id
                ),
            }
        else:
            result["control"]["cos_replies"] = {
                "visible": False,
                "can_mutate": False,
            }
        # This request already refreshed before composing its Settings views.
        diagnostics = await runtime.diagnostics(refresh_capabilities=False)
        diagnostics = {
            **diagnostics,
            "capabilities": public_capability_summary(
                diagnostics.get("capabilities") or {}
            ),
        }
        result["system"]["runtime"] = {
            **result["system"]["runtime"],
            **diagnostics,
            "projection_count": len(runtime.projections.projection_names()),
        }
        readiness = diagnostics.get("deployment_readiness") or {}
        readiness_dimensions = readiness.get("dimensions") or {}
        result["system"]["runtime"]["release_state"] = (
            "ready" if diagnostics.get("release_ready") is True else "not_ready"
        )
        backup_dimension = readiness_dimensions.get("backup_rollback") or {}
        result["system"]["runtime"]["backup_state"] = (
            backup_dimension.get("state") or "unknown"
        )
        if diagnostics.get("openclaw") == "incompatible":
            result["system"]["runtime"]["compatibility"] = "incompatible"
        result["control"]["readiness"] = readiness
        for dimension_name in readiness.get("blocking_dimensions") or []:
            dimension = (readiness.get("dimensions") or {}).get(dimension_name) or {}
            result["control"]["needs_attention"].append(
                {
                    "severity": "blocked",
                    "title": f"Resolve {dimension_name.replace('_', ' ')} readiness",
                    "detail": dimension.get("reason")
                    or "Required deployment evidence is unresolved.",
                    "section": "system",
                }
            )
    if surface in {"today", "triage"}:
        await add_triage_source_context(runtime, session, result)
    if surface == "today":
        add_today_decision_context(result)
    if surface == "scoreboard" and runtime.event_log is not None:
        rewards: list[dict[str, Any]] = []
        async for event in runtime.event_log.read_since(types=["reward.ready"]):
            visible = filter_one(session, event)
            if visible is None:
                continue
            payload = visible.get("payload") or {}
            if payload.get("member_id") != session.view_member_id:
                continue
            rewards.append(
                {
                    "title": str(payload.get("template_text") or "✓"),
                    "status": f"{str(payload.get('tier') or 'done').title()} reinforcement",
                    "reason": (
                        "Completion evidence: "
                        + str(visible.get("causation_id") or "unlinked")
                    ),
                    "triggering_task_id": payload.get("triggering_task_id"),
                    "triggering_commitment_id": payload.get("triggering_commitment_id"),
                    "last_event_id": visible["event_id"],
                    "occurred_at": visible["occurred_at"],
                }
            )
        result["scoreboard"]["reward_evidence"] = list(reversed(rewards[-50:]))
        result["scoreboard"]["dual_path_reward_evidence"] = True
    return result


@router.post("/api/member/v1/settings/profile", status_code=202)
async def change_settings_profile(
    body: SettingsProfileRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Activate one validated data-only profile for the authenticated member."""

    _require_self_settings(session)
    runtime = _runtime(request)
    fingerprint = _settings_request_fingerprint(
        "settings.profile_changed", {"profile_id": body.profile_id}
    )
    lock = runtime.settings_command_locks.setdefault("profile-catalog", asyncio.Lock())
    async with lock:
        prior_receipt = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.profile_changed",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior_receipt is not None:
            return {
                "accepted": True,
                "profile_id": prior_receipt["payload"]["profile_id"],
                "event_id": prior_receipt["event_id"],
                "idempotent": True,
                "deterministic": True,
                "authority_changes": False,
                "model_routing_changes": False,
            }
        try:
            requested_profile = runtime.extensions.get("profile", body.profile_id)
        except ExtensionError as exc:
            raise HTTPException(422, str(exc)) from exc
        if requested_profile.data.get("profile_format") in {"ambient", "scoreboard"}:
            raise HTTPException(422, "profile is unavailable to an adult member")
        prior = runtime.extensions.profile_for(session.auth_member_id)
        previous_id = prior.get("id") if isinstance(prior, dict) else None
        await _guard_member_write(
            runtime,
            session,
            "settings.profile_changed",
            {
                "member_id": session.auth_member_id,
                "previous_profile_id": previous_id,
                "profile_id": body.profile_id,
                "authority_changes": False,
                "model_routing_changes": False,
            },
        )
        activated = False
        if previous_id != body.profile_id:
            try:
                token = runtime.extensions.stage("profile", body.profile_id)
                tested = runtime.extensions.test_stage(token)
                if tested["authority_changes"] or tested["model_routing_changes"]:
                    raise HTTPException(409, "profile attempted to change authority")
                runtime.extensions.activate(token, member_id=session.auth_member_id)
                activated = True
            except ExtensionError as exc:
                raise HTTPException(422, str(exc)) from exc
        changed_at = EventEnvelope.now_utc_iso()
        try:
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.profile_changed",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=session.auth_member_id,
                payload={
                    "member_id": session.auth_member_id,
                    "previous_profile_id": previous_id,
                    "profile_id": body.profile_id,
                    "changed_by_member_id": session.auth_member_id,
                    "authority_changes": False,
                    "model_routing_changes": False,
                    "changed_at": changed_at,
                },
            )
        except Exception:
            if activated:
                runtime.extensions.rollback()
            raise
        return {
            "accepted": True,
            "profile_id": body.profile_id,
            "event_id": event_id,
            "deterministic": True,
            "authority_changes": False,
            "model_routing_changes": False,
        }


@router.post("/api/member/v1/settings/preferences", status_code=202)
async def change_settings_preferences(
    body: SettingsPreferencesRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Save bounded, replayable CoS presentation and cue preferences."""

    _require_self_settings(session)
    runtime = _runtime(request)
    fingerprint = _settings_request_fingerprint(
        "settings.preferences_changed",
        {"expected_revision": body.expected_revision, "values": body.values},
    )
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        prior_receipt = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.preferences_changed",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior_receipt is not None:
            return {
                "accepted": True,
                "revision": prior_receipt["payload"]["revision"],
                "event_id": prior_receipt["event_id"],
                "idempotent": True,
                "preferences": prior_receipt["payload"]["values"],
            }
        previous_state = runtime.settings_control.read()
        await _guard_member_write(
            runtime,
            session,
            "settings.preferences_changed",
            {
                "member_id": session.auth_member_id,
                "expected_revision": body.expected_revision,
                "values": body.values,
            },
        )
        try:
            mutation = runtime.settings_control.set_preferences(
                session.auth_member_id,
                body.values,
                expected_revision=body.expected_revision,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        changed_at = EventEnvelope.now_utc_iso()
        try:
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.preferences_changed",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=session.auth_member_id,
                payload={
                    "member_id": session.auth_member_id,
                    "changed_by_member_id": session.auth_member_id,
                    "revision": mutation.revision,
                    "values": mutation.current,
                    "changed_at": changed_at,
                },
            )
        except Exception:
            runtime.settings_control.save_candidate(previous_state)
            raise
        return {
            "accepted": True,
            "revision": mutation.revision,
            "event_id": event_id,
            "preferences": mutation.current,
        }


@router.post("/api/member/v1/settings/connections/{connection_id}", status_code=202)
async def change_settings_connection(
    connection_id: str,
    body: SettingsConnectionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Pause or resume one exact member-bound adapter instance."""

    _require_self_settings(session)
    runtime = _runtime(request)
    visible = {
        row["id"]: row for row in compose_connections(runtime, session.auth_member_id)
    }
    if connection_id not in visible:
        raise HTTPException(404, "connection not found")
    if runtime.adapters is None:
        raise HTTPException(503, "adapter supervisor is unavailable")
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    fingerprint = _settings_request_fingerprint(
        "settings.connection_lifecycle_changed",
        {
            "connection_id": connection_id,
            "expected_revision": body.expected_revision,
            "action": body.action,
        },
    )
    async with lock:
        prior_receipt = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.connection_lifecycle_changed",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior_receipt is not None:
            return {
                "accepted": True,
                "connection_id": connection_id,
                "state": (
                    "paused"
                    if prior_receipt["payload"]["action"] == "pause"
                    else "connected"
                ),
                "revision": prior_receipt["payload"]["revision"],
                "event_id": prior_receipt["event_id"],
                "idempotent": True,
            }
        previous_state = runtime.settings_control.read()
        if body.action not in visible[connection_id]["actions"]:
            raise HTTPException(
                409,
                "This connection is turned off by the current source settings.",
            )
        await _guard_member_write(
            runtime,
            session,
            "settings.connection_lifecycle_changed",
            {
                "connection_id": connection_id,
                "member_id": session.auth_member_id,
                "action": body.action,
                "expected_revision": body.expected_revision,
            },
        )
        try:
            candidate = runtime.settings_control.preview_connection(
                connection_id,
                body.action,
                expected_revision=body.expected_revision,
                actor_member_id=session.auth_member_id,
            )
            effective = runtime.settings_control.effective_adapter_config(
                runtime.settings.adapter_config, state=candidate
            )
            snapshot = await runtime.adapters.reconcile(effective)
            runtime.settings_control.save_candidate(candidate)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            try:
                await runtime.adapters.reconcile(
                    runtime.settings_control.effective_adapter_config(
                        runtime.settings.adapter_config, state=previous_state
                    )
                )
            except Exception:
                pass
            raise HTTPException(
                503, "connection change failed; the prior runtime was restored"
            ) from exc
        changed_at = EventEnvelope.now_utc_iso()
        try:
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.connection_lifecycle_changed",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=session.auth_member_id,
                payload={
                    "connection_id": connection_id,
                    "member_id": session.auth_member_id,
                    "action": body.action,
                    "revision": candidate["revision"],
                    "runtime_reconciled": True,
                    "changed_by_member_id": session.auth_member_id,
                    "changed_at": changed_at,
                },
            )
        except Exception as exc:
            try:
                await runtime.adapters.reconcile(
                    runtime.settings_control.effective_adapter_config(
                        runtime.settings.adapter_config, state=previous_state
                    )
                )
                runtime.settings_control.save_candidate(previous_state)
            except Exception as rollback_exc:
                raise HTTPException(
                    503,
                    "connection audit failed and rollback is incomplete; run Doctor",
                ) from rollback_exc
            raise HTTPException(
                503, "connection audit failed; the prior runtime was restored"
            ) from exc
        return {
            "accepted": True,
            "connection_id": connection_id,
            "state": "paused" if body.action == "pause" else "connected",
            "revision": candidate["revision"],
            "event_id": event_id,
            "runtime": snapshot,
        }


@router.post("/api/member/v1/settings/candidate-policies/{source_id}", status_code=202)
async def change_settings_candidate_policy(
    source_id: str,
    body: SettingsCandidatePolicyRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Change candidate admission without changing source truth or authority."""

    _require_self_settings(session)
    runtime = _runtime(request)
    try:
        normalized = validate_policy_override(
            source_id,
            mode=body.mode,
            language_enabled=body.language_enabled,
            candidate_kinds=list(body.candidate_kinds),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    values = {
        "source_id": source_id,
        "expected_revision": body.expected_revision,
        **normalized,
    }
    fingerprint = _settings_request_fingerprint(
        "settings.candidate_policy_changed", values
    )
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        prior_receipt = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.candidate_policy_changed",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior_receipt is not None:
            payload = prior_receipt["payload"]
            return {
                "accepted": True,
                "revision": payload["revision"],
                "event_id": prior_receipt["event_id"],
                "idempotent": True,
                "policy": {
                    "source_id": payload["source_id"],
                    "mode": payload["mode"],
                    "language_enabled": payload["language_enabled"],
                    "candidate_kinds": payload["candidate_kinds"],
                },
            }
        previous_state = runtime.settings_control.read()
        await _guard_member_write(
            runtime,
            session,
            "settings.candidate_policy_changed",
            {
                "member_id": session.auth_member_id,
                **values,
                "source_retention_changed": False,
                "routing_authority_changed": False,
            },
        )
        try:
            mutation = runtime.settings_control.set_candidate_policy(
                session.auth_member_id,
                source_id,
                mode=normalized["mode"],
                language_enabled=normalized["language_enabled"],
                candidate_kinds=normalized["candidate_kinds"],
                expected_revision=body.expected_revision,
            )
        except ValueError as exc:
            message = str(exc)
            status = 409 if "revision" in message.casefold() else 422
            raise HTTPException(status, message) from exc
        changed_at = EventEnvelope.now_utc_iso()
        try:
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.candidate_policy_changed",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=session.auth_member_id,
                payload={
                    "member_id": session.auth_member_id,
                    "source_id": source_id,
                    "mode": normalized["mode"],
                    "language_enabled": normalized["language_enabled"],
                    "candidate_kinds": normalized["candidate_kinds"],
                    "revision": mutation.revision,
                    "changed_by_member_id": session.auth_member_id,
                    "changed_at": changed_at,
                    "source_retention_changed": False,
                    "routing_authority_changed": False,
                },
            )
        except Exception:
            runtime.settings_control.save_candidate(previous_state)
            raise
        return {
            "accepted": True,
            "revision": mutation.revision,
            "event_id": event_id,
            "policy": runtime.settings_control.effective_candidate_policy(
                session.auth_member_id, source_id
            ),
        }


@router.post("/api/member/v1/settings/source-policies-v2/preview")
async def preview_settings_source_policy_v2(
    body: SettingsSourcePolicyV2PreviewRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Compare each assessed action and unmatched source without changing state."""

    _require_self_settings(session)
    _require_owned_source_coordinate(session, body.coordinate)
    runtime = _runtime(request)
    _require_observed_source_coordinate(runtime, session, body.coordinate)
    current_policy = SourcePolicyV2.model_validate(
        runtime.settings_control.effective_source_policy_v2(
            session.auth_member_id, body.coordinate
        )["policy"]
    )
    policy = _resolved_source_policy(
        body.preset, body.policy, current_policy=current_policy
    )
    if runtime.event_log is None:
        raise HTTPException(503, "source policy preview is unavailable")
    matches: list[dict[str, Any]] = []
    async for event in runtime.event_log.read_since(
        types=source_policy_event_types(runtime.settings.repository_root)
    ):
        if (
            event.get("tenant_id") != session.tenant_id
            or event.get("owner_scope") != f"private:{session.auth_member_id}"
            or event.get("visibility_scope") != f"private:{session.auth_member_id}"
        ):
            continue
        try:
            coordinate = source_coordinate_for_event(
                event, provider_bindings=runtime.settings.provider_bindings
            )
        except (TypeError, ValueError):
            continue
        if source_coordinate_contains(body.coordinate, coordinate):
            matches.append(event)
            if len(matches) > body.recent_limit:
                matches.pop(0)
    # Use code-owned persisted interpretations; provider flags cannot assign a
    # message purpose or an independent obligation. Include sources that never
    # generated a proposal so preview is not biased to the current queue.
    from adminme.reconciliation import assessed_signals

    source_ids = {str(event["event_id"]) for event in matches}
    proposals: dict[str, dict[str, dict[str, Any]]] = {}
    async for proposal in runtime.event_log.read_since(types=["triage.item.proposed"]):
        payload = proposal.get("payload") or {}
        source_id = str(payload.get("source_event_id") or "")
        if (
            source_id in source_ids
            and proposal.get("tenant_id") == session.tenant_id
            and proposal.get("owner_scope") == f"private:{session.auth_member_id}"
            and proposal.get("visibility_scope") == f"private:{session.auth_member_id}"
        ):
            proposal_id = str(payload.get("proposal_id") or proposal["event_id"])
            proposals.setdefault(source_id, {})[proposal_id] = proposal
    examples: list[dict[str, Any]] = []
    counts = {
        "source_facts": len(matches),
        "triage": 0,
        "alerts": 0,
        "context": 0,
        "provenance_only": 0,
        "assessed_actions": 0,
        "matching": 0,
        "nonmatching": 0,
        "changed": 0,
        "previous_triage": 0,
        "sources_without_candidates": 0,
    }
    for event in matches:
        event_coordinate = source_coordinate_for_event(
            event, provider_bindings=runtime.settings.provider_bindings
        )
        event_effective = runtime.settings_control.effective_source_policy_v2(
            session.auth_member_id, event_coordinate
        )
        event_current_policy = SourcePolicyV2.model_validate(event_effective["policy"])
        child_override = (
            event_coordinate.key != body.coordinate.key
            and event_effective["origin"] == "exact_override"
            and event_effective.get("inherited_from_policy_key") != body.coordinate.key
        )
        event_proposed_policy = event_current_policy if child_override else policy
        event_proposals = list(proposals.get(str(event["event_id"]), {}).values())
        if not event_proposals:
            counts["sources_without_candidates"] += 1
        actions: list[dict[str, Any] | None] = (
            list(event_proposals) if event_proposals else [None]
        )
        for action_proposal in actions:
            signals = assessed_signals(event, action_proposal)
            previous = route_for_policy(event_coordinate, event_current_policy, signals)
            route = route_for_policy(event_coordinate, event_proposed_policy, signals)
            matched = (
                not child_override
                and signals.purpose_evidence_verified
                and any(
                    rule.purpose == signals.message_purpose
                    for rule in policy.purpose_routes
                )
            )
            changed = (
                previous.triage,
                previous.alert,
                previous.context,
                previous.candidate_kinds,
            ) != (route.triage, route.alert, route.context, route.candidate_kinds)
            counts["assessed_actions"] += action_proposal is not None
            counts["matching"] += matched
            counts["nonmatching"] += not matched
            counts["changed"] += changed
            counts["previous_triage"] += previous.triage != "none"
            counts["triage"] += route.triage != "none"
            counts["alerts"] += route.alert != "none"
            counts["context"] += route.context != "none"
            counts["provenance_only"] += (
                route.triage == "none"
                and route.alert == "none"
                and route.context == "none"
            )
            source_payload = event.get("payload") or {}
            examples.append(
                {
                    "synthetic": False,
                    "source_event_id": event["event_id"],
                    "source_title": str(
                        source_payload.get("subject")
                        or source_payload.get("title")
                        or "Source without a title"
                    )[:160],
                    "proposal_id": (action_proposal.get("payload") or {}).get(
                        "proposal_id"
                    )
                    if action_proposal
                    else None,
                    "message_purpose": signals.message_purpose,
                    "purpose_evidence_verified": signals.purpose_evidence_verified,
                    "matched": matched,
                    "changed": changed,
                    "existing_container_override_preserved": child_override,
                    "previous_route": previous.model_dump(mode="json"),
                    "current_route": previous.model_dump(mode="json"),
                    "proposed_route": route.model_dump(mode="json"),
                    "route": route.model_dump(mode="json"),
                    "reason_codes": route.reason_codes,
                    "content_returned": False,
                }
            )
    # Keep both positive and negative examples visible when both exist.
    selected = [row for row in examples if row["matched"]][:5]
    selected.extend(
        row for row in examples if not row["matched"] and len(selected) < 10
    )
    return {
        "contract": "source-policy-v2-preview",
        "policy_key": body.coordinate.key,
        "policy": policy.model_dump(mode="json"),
        "estimated_recent_effect": counts,
        "examples": selected,
        "matching_count": counts["matching"],
        "nonmatching_count": counts["nonmatching"],
        "retained_candidate_count": counts["assessed_actions"],
        "coverage": "bounded_recent_authorized_sources",
        "mutation_performed": False,
        "reversible": True,
        "live_admission_changed": False,
        "provider_effects": False,
        "limitations": [
            "This compares recent authorized sources and persisted action assessments, including sources without candidates.",
            "No message body, prompt, model setting, credential, or raw configuration is returned; titles remain private to their source owner.",
            "Preview changes no settings or candidates. Missing assessments remain explicitly unverified.",
        ],
    }


@router.get("/api/member/v1/settings/source-policies-v2/why/{source_event_id}")
async def explain_settings_source_policy_v2(
    source_event_id: str,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Explain one owner-visible current and shadow source route."""

    _require_self_settings(session)
    runtime = _runtime(request)
    if runtime.event_log is None:
        raise HTTPException(503, "source policy explanation is unavailable")
    source = await runtime.event_log.get(source_event_id)
    if (
        source is None
        or source.get("tenant_id") != session.tenant_id
        or source.get("owner_scope") != f"private:{session.auth_member_id}"
        or source.get("visibility_scope") != f"private:{session.auth_member_id}"
    ):
        raise HTTPException(404, "source policy explanation was not found")
    evaluations = [
        event
        for event in await runtime.event_log.get_by_causation(source_event_id)
        if event.get("type") == "source.policy_v2.shadow_evaluated"
        and event.get("owner_scope") == f"private:{session.auth_member_id}"
    ]
    if not evaluations:
        raise HTTPException(404, "source policy shadow evaluation is still pending")
    payload = evaluations[-1]["payload"]
    current_route = payload["current_route"]
    shadow_route = payload["shadow_route"]
    active = runtime.settings.reconciliation_admission_enabled
    current_items = (
        runtime.projections.connection("triage")
        .execute(
            """SELECT proposal_id, admission_decision, management_state, learned_policy_state,
                  reconciliation_reasons_json FROM triage_items
           WHERE tenant_id=? AND source_event_id=? AND owner_member_id=?
             AND owner_scope=? AND visibility_scope=? ORDER BY proposal_id""",
            (
                session.tenant_id,
                source_event_id,
                session.auth_member_id,
                f"private:{session.auth_member_id}",
                f"private:{session.auth_member_id}",
            ),
        )
        .fetchall()
    )
    return {
        "contract": "source-policy-v2-why",
        "source_event_id": source_event_id,
        "admission_active": active,
        "current_local_items": [
            {
                "proposal_id": row["proposal_id"],
                "admission_decision": row["admission_decision"],
                "management_state": row["management_state"],
                "learned_policy_state": row["learned_policy_state"],
                "reason_codes": json.loads(row["reconciliation_reasons_json"] or "[]"),
            }
            for row in current_items
        ],
        "current": {
            "explanation": (
                "This is the legacy route recorded at intake; current local item decisions are listed separately."
                if active
                else "The live V1 policy sent this source to Triage review."
                if current_route["triage"] != "none"
                else "The live V1 policy did not send this source to Triage."
            ),
            "route": current_route,
        },
        "shadow": {
            "explanation": (
                "This intake comparison is historical. Current source preferences and corrections may have changed local admission."
                if active
                else "The proposed V2 policy would send this source to Triage."
                if shadow_route["triage"] != "none"
                else "The proposed V2 policy would keep this source out of Triage."
            ),
            "route": shadow_route,
        },
        "reason_codes": payload["reason_codes"],
        "provider_labels_authoritative": False,
        "urgency_authoritative": False,
        "llm_claims_authoritative": False,
        "provider_effects": False,
    }


@router.post("/api/member/v1/settings/source-policies-v2/{policy_key}", status_code=202)
async def change_settings_source_policy_v2(
    policy_key: str,
    body: SettingsSourcePolicyV2Request,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Append one owner-scoped attention policy without access/provider authority."""

    _require_self_settings(session)
    _require_owned_source_coordinate(session, body.coordinate)
    if body.coordinate.key != policy_key:
        raise HTTPException(422, "policy key does not match exact source coordinate")
    runtime = _runtime(request)
    admission_active = runtime.settings.reconciliation_admission_enabled
    _require_observed_source_coordinate(runtime, session, body.coordinate)
    current_policy = SourcePolicyV2.model_validate(
        runtime.settings_control.effective_source_policy_v2(
            session.auth_member_id, body.coordinate
        )["policy"]
    )
    policy = (
        None
        if body.action == "reset"
        else _resolved_source_policy(
            body.preset, body.policy, current_policy=current_policy
        )
    )
    if body.action == "reset" and body.policy is not None:
        raise HTTPException(422, "reset cannot include a source policy")
    values = {
        "policy_key": policy_key,
        "expected_revision": body.expected_revision,
        "coordinate": body.coordinate.model_dump(mode="json"),
        "action": body.action,
        "preset": body.preset,
        "policy": body.policy.model_dump(mode="json")
        if body.policy is not None
        else None,
    }
    fingerprint = _settings_request_fingerprint(
        "settings.source_policy_v2_changed", values
    )
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        try:
            await runtime.settings_control.replay_source_policy_v2_events(
                runtime.event_log
            )
        except (OSError, RuntimeError) as exc:
            raise HTTPException(
                503, "source policy settings could not be replayed"
            ) from exc
        prior = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.source_policy_v2_changed",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior is not None:
            effective = runtime.settings_control.effective_source_policy_v2(
                session.auth_member_id, body.coordinate
            )
            return {
                "accepted": True,
                "idempotent": True,
                "event_id": prior["event_id"],
                **effective,
                "live_admission_changed": admission_active,
                "admission_active": admission_active,
                "provider_effects": False,
            }
        try:
            revision = runtime.settings_control.prepare_source_policy_v2_change(
                session.auth_member_id,
                body.coordinate,
                expected_revision=body.expected_revision,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await _guard_member_write(
            runtime,
            session,
            "settings.source_policy_v2_changed",
            {
                "owner_principal_id": session.auth_member_id,
                **values,
                "scope_widening": False,
                "credential_creation": False,
                "provider_write_authority": False,
                "live_admission_changed": admission_active,
            },
        )
        changed_at = EventEnvelope.now_utc_iso()
        payload = {
            "owner_principal_id": session.auth_member_id,
            "owner_kind": "member",
            "policy_key": policy_key,
            "coordinate": body.coordinate.model_dump(mode="json"),
            "action": body.action,
            "policy": policy.model_dump(mode="json") if policy is not None else None,
            "revision": revision,
            "changed_by_principal_id": session.auth_member_id,
            "changed_at": changed_at,
            "scope_widening": False,
            "credential_creation": False,
            "provider_write_authority": False,
            "live_admission_changed": admission_active,
        }
        event_id = await _write_settings_receipt(
            runtime,
            session,
            event_type="settings.source_policy_v2_changed",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            owner_member_id=session.auth_member_id,
            payload=payload,
            schema_version=2 if admission_active else 1,
        )
        try:
            runtime.settings_control.apply_source_policy_v2_event(payload)
        except Exception as exc:
            raise HTTPException(
                503,
                "source policy audit was saved but its local view needs replay",
            ) from exc
        effective = runtime.settings_control.effective_source_policy_v2(
            session.auth_member_id, body.coordinate
        )
        return {
            "accepted": True,
            "event_id": event_id,
            **effective,
            "live_admission_changed": admission_active,
            "admission_active": admission_active,
            "provider_effects": False,
        }


@router.post("/api/member/v1/settings/capabilities/{capability_id}", status_code=202)
async def change_settings_capability_request(
    capability_id: str,
    body: SettingsCapabilityRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Record or cancel one inert capability request without an operational effect."""

    _require_self_settings(session)
    runtime = _runtime(request)
    state = runtime.settings_control.read()
    effective_catalog = compose_member_catalog(runtime, session, settings_state=state)
    capability = catalog_item(effective_catalog, capability_id)
    if capability is None:
        raise HTTPException(404, "capability is not in the reviewed catalog")
    provider_options = set(capability.get("provider_options") or [])
    provider_option = body.provider_option
    if provider_option is not None and provider_option not in provider_options:
        raise HTTPException(422, "provider option is not reviewed for this capability")
    if provider_option is None and len(provider_options) == 1:
        provider_option = next(iter(provider_options))
    values = {
        "capability_id": capability_id,
        "expected_revision": body.expected_revision,
        "action": body.action,
        "requested_scope": body.requested_scope,
        "provider_option": provider_option,
    }
    event_type = (
        "settings.capability_requested"
        if body.action == "request"
        else "settings.capability_request_cancelled"
    )
    fingerprint = _settings_request_fingerprint(event_type, values)
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
        prior = await _prior_settings_receipt(
            runtime,
            session,
            event_type=event_type,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior is not None:
            return {
                "accepted": True,
                "idempotent": True,
                "event_id": prior["event_id"],
                "request": prior["payload"],
                "provider_effect_count": 0,
                "operational_effects": False,
            }
        current_rows = {
            value["capability_id"]: value
            for value in runtime.settings_control.capability_requests_for(
                session.auth_member_id
            )
        }
        current = current_rows.get(capability_id)
        if body.action == "cancel" and (
            current is None or current.get("state") != "requested"
        ):
            raise HTTPException(409, "capability request is not active")
        if (
            body.action == "request"
            and current is not None
            and current.get("state") == "requested"
        ):
            raise HTTPException(409, "capability is already requested")
        try:
            revision = runtime.settings_control.prepare_capability_request_change(
                session.auth_member_id, expected_revision=body.expected_revision
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        changed_at = EventEnvelope.now_utc_iso()
        payload = {
            "member_id": session.auth_member_id,
            "capability_id": capability_id,
            "catalog_version": effective_catalog["catalog_version"],
            "desired_outcome": capability["outcome"],
            "requested_scope": body.requested_scope,
            "provider_option": provider_option,
            "state": "requested" if body.action == "request" else "cancelled",
            "revision": revision,
            "changed_by_member_id": session.auth_member_id,
            "changed_at": changed_at,
            "installs_software": False,
            "pairs_bridge": False,
            "enrolls_account": False,
            "changes_openclaw_configuration": False,
            "accesses_credentials": False,
            "grants_authority": False,
            "provider_effects": False,
        }
        await _guard_member_write(runtime, session, event_type, payload)
        event_id = await _write_settings_receipt(
            runtime,
            session,
            event_type=event_type,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            owner_member_id=session.auth_member_id,
            payload=payload,
        )
        try:
            mutation = runtime.settings_control.apply_capability_request_event(payload)
        except Exception as exc:
            raise HTTPException(
                503,
                "capability request audit was saved but its local view needs replay",
            ) from exc
        return {
            "accepted": True,
            "event_id": event_id,
            "request": mutation.current,
            "provider_effect_count": 0,
            "operational_effects": False,
        }


@router.get("/api/member/v1/settings/cos-replies")
async def get_cos_reply_recipients(
    request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_self_settings(session)
    if session.auth_role != "principal":
        raise HTTPException(403, "CoS reply recipients require a household principal")
    runtime = _runtime(request)
    return await runtime.settings_control.cos_response_policy(
        runtime.event_log, session.tenant_id
    )


@router.put("/api/member/v1/settings/cos-replies", status_code=202)
async def replace_cos_reply_recipients(
    body: SettingsCosRepliesRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    """Replace the recipient ceiling; empty entries revoke every destination."""

    _require_self_settings(session)
    if session.auth_role != "principal":
        raise HTTPException(403, "CoS reply recipients require a household principal")
    runtime = _runtime(request)
    members = {
        member
        for member, role in runtime.settings.member_roles.items()
        if role not in {"device", "ambient"}
    }
    try:
        entries = validate_recipient_entries(body.entries, members)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    fingerprint = _settings_request_fingerprint(
        COS_RESPONSE_POLICY_EVENT,
        {"expected_revision": body.expected_revision, "entries": entries},
    )
    lock = runtime.settings_command_locks.setdefault(
        f"cos-response-policy:{session.tenant_id}", asyncio.Lock()
    )
    async with lock:
        prior = await _prior_settings_receipt(
            runtime,
            session,
            event_type=COS_RESPONSE_POLICY_EVENT,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior is not None:
            return {
                "accepted": True,
                "idempotent": True,
                "event_id": prior["event_id"],
                "revision": prior["payload"]["revision"],
                "provider_effects": 0,
                "grants_provider_authority": False,
            }
        try:
            revision = await runtime.settings_control.prepare_cos_response_policy(
                runtime.event_log,
                session.tenant_id,
                expected_revision=body.expected_revision,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        payload = {
            "contract": COS_RESPONSE_POLICY_CONTRACT,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "revision": revision,
            "entries": entries,
            "changed_by_member_id": session.auth_member_id,
            "changed_at": EventEnvelope.now_utc_iso(),
            "grants_provider_authority": False,
            "provider_effects": False,
        }
        await _guard_member_write(runtime, session, COS_RESPONSE_POLICY_EVENT, payload)
        event_id = await _write_settings_receipt(
            runtime,
            session,
            event_type=COS_RESPONSE_POLICY_EVENT,
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            owner_member_id=session.auth_member_id,
            sensitivity="sensitive",
        )
        return {
            "accepted": True,
            "event_id": event_id,
            "revision": revision,
            "provider_effects": 0,
            "grants_provider_authority": False,
        }


@router.post("/api/member/v1/settings/apple-messages-replies", status_code=202)
async def control_apple_messages_replies(
    body: SettingsAppleMessagesReplyRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Change owner-only reply authority without creating a provider command."""

    _require_self_settings(session)
    if session.auth_role != "principal":
        raise HTTPException(403, "Messages reply authority requires its adult owner")
    runtime = _runtime(request)
    control = reply_control_for(runtime, session.auth_member_id)
    if body.action == "enable" and control["state"] not in {
        "available_but_disabled",
        "paused",
    }:
        raise HTTPException(
            409,
            control.get("reason")
            or f"Messages replies cannot be enabled from {control['state']}",
        )
    fingerprint = _settings_request_fingerprint(
        "settings.apple_messages_reply_controlled",
        {
            "expected_revision": body.expected_revision,
            "action": body.action,
            "coordinates_sha256": control["coordinates_sha256"],
        },
    )
    lock = runtime.settings_command_locks.setdefault(
        f"messages-reply:{session.auth_member_id}", asyncio.Lock()
    )
    async with lock:
        prior = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.apple_messages_reply_controlled",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior is not None:
            return {
                "accepted": True,
                "idempotent": True,
                "event_id": prior["event_id"],
                "state": prior["payload"]["state"],
                "revision": prior["payload"]["revision"],
                "provider_effects": 0,
            }
        previous_state = runtime.settings_control.read()
        try:
            revision = runtime.settings_control.prepare_message_reply_control(
                session.auth_member_id,
                expected_revision=body.expected_revision,
                action=body.action,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        requested_state = {
            "enable": "enabled_ask_every_time",
            "pause": "paused",
            "revoke": "revoked",
        }[body.action]
        changed_at = EventEnvelope.now_utc_iso()
        payload = {
            "member_id": session.auth_member_id,
            "action": body.action,
            "state": requested_state,
            "revision": revision,
            "coordinates": control["coordinates"],
            "coordinates_sha256": control["coordinates_sha256"],
            "ask_every_time": True,
            "permits_new_conversation": False,
            "permits_cos_autonomy": False,
            "changed_by_member_id": session.auth_member_id,
            "changed_at": changed_at,
            "provider_effects": False,
        }
        await _guard_member_write(
            runtime, session, "settings.apple_messages_reply_controlled", payload
        )
        try:
            mutation = runtime.settings_control.apply_message_reply_control_event(
                payload
            )
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.apple_messages_reply_controlled",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=session.auth_member_id,
                payload=payload,
            )
        except Exception:
            runtime.settings_control.save_candidate(previous_state)
            raise
        return {
            "accepted": True,
            "event_id": event_id,
            "state": mutation.current["state"],
            "revision": mutation.revision,
            "ask_every_time": True,
            "provider_effects": 0,
        }


@router.post("/api/member/v1/settings/bridges", status_code=202)
async def request_settings_bridge(
    body: SettingsBridgeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Prepare one scoped self-service handoff without returning its secret."""

    _require_self_settings(session)
    runtime = _runtime(request)
    role = runtime.settings.member_roles.get(body.member_id)
    if role is None or role in {"device", "ambient"}:
        raise HTTPException(422, "bridge target must be an enrolled household member")
    if body.member_id != session.auth_member_id:
        if session.auth_role != "principal" or role != "child":
            raise HTTPException(403, "an adult must enroll their own private bridge")
    if role == "child" and "obsidian" in body.capabilities:
        raise HTTPException(403, "Obsidian is unavailable on a child bridge")
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    fingerprint = _settings_request_fingerprint(
        "settings.bridge_enrollment_started",
        {
            "expected_revision": body.expected_revision,
            "member_id": body.member_id,
            "device_class": body.device_class,
            "capabilities": sorted(set(body.capabilities)),
        },
    )
    async with lock:
        try:
            runtime.bridge_enrollment.require_request_owner(
                request_id=body.request_id, member_id=body.member_id
            )
            # Failed/retired requests remain bound after their package and
            # secret are removed, and after a later request replaces the index.
            # These typed receipts contain no provider content.
            async for prior_start in runtime.event_log.read_since(
                types=["settings.bridge_enrollment_started"]
            ):
                prior_payload = prior_start.get("payload") or {}
                if (
                    prior_start.get("tenant_id") == session.tenant_id
                    and prior_payload.get("request_id") == body.request_id
                    and prior_payload.get("member_id") != body.member_id
                ):
                    raise BridgeEnrollmentError(
                        "request_id_unavailable",
                        "Choose a new bridge request identifier",
                    )
        except BridgeEnrollmentError as exc:
            raise _bridge_http_error(exc) from exc
        prior_receipt = await _prior_settings_receipt(
            runtime,
            session,
            event_type="settings.bridge_enrollment_started",
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        current = runtime.settings_control.bridge_request_for(body.member_id)
        if prior_receipt is None:
            _require_bridge_acquisition(
                runtime,
                body.capabilities,
                member_id=body.member_id,
                request_id=body.request_id,
            )
            try:
                revision = runtime.settings_control.prepare_bridge_start(
                    body.member_id, expected_revision=body.expected_revision
                )
            except ValueError as exc:
                # This refusal occurs under the command lock before any
                # enrollment receipt or lifecycle event is appended.
                raise HTTPException(
                    409,
                    {
                        "error": "enrollment_not_started",
                        "message": str(exc),
                        "request_id": body.request_id,
                        "request_recorded": False,
                    },
                ) from exc
            if (
                isinstance(current, dict)
                and current.get("state") in {"revoked", "expired", "failed"}
                and current.get("request_id") != body.request_id
            ):
                try:
                    with runtime.bridge_enrollment.member_operation(
                        session,
                        request_id=str(current["request_id"]),
                        member_id=body.member_id,
                    ):
                        runtime.bridge_enrollment.revoke(
                            request_id=str(current["request_id"]),
                            member_id=body.member_id,
                        )
                except BridgeEnrollmentError as exc:
                    if exc.code != "enrollment_missing_or_ambiguous":
                        raise _bridge_http_error(exc) from exc
                _refresh_bridge_ingress(request)
            requested_at = EventEnvelope.now_utc_iso()
            payload = {
                "request_id": body.request_id,
                "member_id": body.member_id,
                "requested_by_member_id": session.auth_member_id,
                "device_class": body.device_class,
                "capabilities": sorted(set(body.capabilities)),
                "state": "requested",
                "revision": revision,
                "requested_at": requested_at,
                "observation_only": True,
                "writes_authorized": False,
                "provider_effects": False,
            }
            await _guard_member_write(
                runtime, session, "settings.bridge_enrollment_started", payload
            )
            event_id = await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.bridge_enrollment_started",
                request_id=body.request_id,
                request_fingerprint=fingerprint,
                owner_member_id=body.member_id,
                payload=payload,
            )
            current = runtime.settings_control.apply_bridge_enrollment_event(
                payload
            ).current
            compatibility_payload = {
                "member_id": body.member_id,
                "requested_by_member_id": session.auth_member_id,
                "capabilities": sorted(set(body.capabilities)),
                "state": "enrollment_preparation_required",
                "revision": runtime.settings_control.read()["revision"],
                "requested_at": requested_at,
            }
            await _write_settings_receipt(
                runtime,
                session,
                event_type="settings.bridge_enrollment_requested",
                request_id=f"{body.request_id}:compat",
                request_fingerprint=_settings_request_fingerprint(
                    "settings.bridge_enrollment_requested", compatibility_payload
                ),
                owner_member_id=body.member_id,
                payload=compatibility_payload,
            )
        else:
            event_id = str(prior_receipt["event_id"])
        if (
            not isinstance(current, dict)
            or current.get("request_id") != body.request_id
        ):
            raise HTTPException(
                409, "bridge enrollment request conflicts with current state"
            )
        if prior_receipt is not None and current.get("state") in {
            "revoked",
            "expired",
            "failed",
        }:
            raise HTTPException(
                409, "start a new bridge request to regenerate enrollment"
            )
        if current.get("state") in {
            "package_ready",
            "awaiting_transfer",
            "package_opened",
            "instance_verified",
            "member_confirmed",
            "device_identity_verified",
            "bridge_paired",
            "awaiting_permissions",
            "capabilities_testing",
            "settling",
            "active_observation",
            "partially_active",
            "degraded",
            "paused",
        }:
            try:
                runtime.bridge_enrollment.require_acquisition_binding(
                    request_id=body.request_id, member_id=body.member_id
                )
            except BridgeEnrollmentError as exc:
                raise _bridge_http_error(exc) from exc
            return {
                "accepted": True,
                "member_id": body.member_id,
                "state": current["state"],
                "revision": runtime.settings_control.read()["revision"],
                "bridge_revision": current["revision"],
                "event_id": event_id,
                "idempotent": True,
                "handoff_url": f"/api/member/v1/settings/bridges/{body.request_id}/package",
                "next_step": "Transfer the short-lived package to the selected Mac.",
                "provider_effect_count": 0,
            }
        if current.get("state") == "requested":
            current = await _record_central_bridge_progress(
                runtime,
                session,
                request_id=body.request_id,
                member_id=body.member_id,
                previous_state="requested",
                state="package_preparing",
                suffix="package-preparing",
            )
        try:
            with runtime.bridge_enrollment.member_operation(
                session, request_id=body.request_id, member_id=body.member_id
            ):
                prepared = runtime.bridge_enrollment.prepare(
                    request_id=body.request_id,
                    member_id=body.member_id,
                    device_class=body.device_class,
                    capabilities=sorted(set(body.capabilities)),
                    central_url=_settings_central_url(
                        request,
                        allow_testserver=not runtime.settings.deployment_evidence_required,
                    ),
                    allow_testserver=not runtime.settings.deployment_evidence_required,
                )
            _refresh_bridge_ingress(request)
        except BridgeEnrollmentError as exc:
            if current.get("state") == "package_preparing":
                await _record_central_bridge_progress(
                    runtime,
                    session,
                    request_id=body.request_id,
                    member_id=body.member_id,
                    previous_state="package_preparing",
                    state="failed",
                    suffix="package-failed",
                    extra={"reason": exc.code},
                )
            raise _bridge_http_error(exc) from exc
        current = await _record_central_bridge_progress(
            runtime,
            session,
            request_id=body.request_id,
            member_id=body.member_id,
            previous_state="package_preparing",
            state="package_ready",
            suffix="package-ready",
            extra={
                "enrollment_id": prepared.enrollment_id,
                "expires_at": prepared.expires_at,
                "release_commit": prepared.release_commit,
                "package_sha256": prepared.package_sha256,
                "capability_set_sha256": prepared.capability_set_sha256,
            },
        )
        return {
            "accepted": True,
            "member_id": body.member_id,
            "request_id": body.request_id,
            "state": current["state"],
            "revision": runtime.settings_control.read()["revision"],
            "bridge_revision": current["revision"],
            "event_id": event_id,
            "expires_at": prepared.expires_at,
            "handoff_url": f"/api/member/v1/settings/bridges/{body.request_id}/package",
            "next_step": "Download and transfer the short-lived package to the selected Mac.",
            "contains_transfer_secret": False,
            "provider_effect_count": 0,
        }


@router.post("/api/member/v1/settings/bridges/{wizard_request_id}/package")
async def download_settings_bridge_package(
    wizard_request_id: str,
    request: Request,
    session: SessionDep,
) -> Response:
    """Return package bytes and a no-store clipboard code outside ordinary JSON."""

    _require_self_settings(session)
    runtime = _runtime(request)
    record = runtime.settings_control.bridge_request_for(session.auth_member_id)
    if not isinstance(record, dict) or record.get("request_id") != wizard_request_id:
        raise HTTPException(404, "bridge enrollment request was not found")
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        try:
            runtime.bridge_enrollment.require_acquisition_binding(
                request_id=wizard_request_id, member_id=session.auth_member_id
            )
        except BridgeEnrollmentError as exc:
            raise _bridge_http_error(exc) from exc
        if record.get("state") == "package_ready":
            record = await _record_central_bridge_progress(
                runtime,
                session,
                request_id=wizard_request_id,
                member_id=session.auth_member_id,
                previous_state="package_ready",
                state="awaiting_transfer",
                suffix="awaiting-transfer",
            )
        if record.get("state") not in {"awaiting_transfer", "package_opened"}:
            raise HTTPException(409, "bridge package is not available in this state")
        try:
            package, transfer_code = runtime.bridge_enrollment.download(
                request_id=wizard_request_id,
                member_id=session.auth_member_id,
            )
        except BridgeEnrollmentError as exc:
            if exc.code == "expired":
                await _record_central_bridge_progress(
                    runtime,
                    session,
                    request_id=wizard_request_id,
                    member_id=session.auth_member_id,
                    previous_state=str(record["state"]),
                    state="expired",
                    suffix="package-expired",
                    extra={"reason": "package_expired"},
                )
                _refresh_bridge_ingress(request)
            raise _bridge_http_error(exc) from exc
    return Response(
        package,
        media_type="application/vnd.administrateme.bridge-enrollment",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Content-Disposition": 'attachment; filename="AdministrateMe Bridge Enrollment.adminmebridge"',
            "X-AdministrateMe-Transfer-Code": transfer_code,
            "Access-Control-Expose-Headers": "X-AdministrateMe-Transfer-Code",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/member/v1/settings/bridges/{wizard_request_id}/control")
async def control_settings_bridge(
    wizard_request_id: str,
    body: SettingsBridgeControlRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Apply an exact owner-only pause, resume, repair, retry, or revoke."""

    _require_self_settings(session)
    runtime = _runtime(request)
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        record = runtime.settings_control.bridge_request_for(session.auth_member_id)
        if (
            not isinstance(record, dict)
            or record.get("request_id") != wizard_request_id
        ):
            raise HTTPException(404, "bridge enrollment request was not found")
        previous = str(record["state"])
        if body.action in {"resume", "repair", "retry"}:
            _require_bridge_acquisition(
                runtime,
                list(record["capabilities"]),
                member_id=session.auth_member_id,
                request_id=body.command_id,
            )
        retry_preparation = False
        if body.action == "pause":
            next_state = "paused"
        elif body.action == "resume":
            next_state = str(record.get("paused_from_state") or "awaiting_permissions")
        elif body.action in {"repair", "retry"}:
            if previous == "failed":
                next_state = "package_preparing"
                retry_preparation = True
            elif previous in {"awaiting_permissions", "partially_active", "degraded"}:
                next_state = "capabilities_testing"
            elif previous == "bridge_paired":
                next_state = "awaiting_permissions"
            else:
                raise HTTPException(
                    409, "repair or retry is unavailable in this bridge state"
                )
        else:
            next_state = "revoked"
        try:
            validate_enrollment_transition(previous, next_state)
        except BridgeEnrollmentError as exc:
            raise _bridge_http_error(exc) from exc

        if body.action == "pause":
            try:
                with runtime.bridge_enrollment.member_operation(
                    session,
                    request_id=wizard_request_id,
                    member_id=session.auth_member_id,
                ):
                    runtime.bridge_enrollment.pause(
                        request_id=wizard_request_id, member_id=session.auth_member_id
                    )
            except BridgeEnrollmentError as exc:
                raise _bridge_http_error(exc) from exc
        elif body.action == "resume":
            try:
                with runtime.bridge_enrollment.member_operation(
                    session,
                    request_id=wizard_request_id,
                    member_id=session.auth_member_id,
                ):
                    runtime.bridge_enrollment.resume(
                        request_id=wizard_request_id, member_id=session.auth_member_id
                    )
            except BridgeEnrollmentError as exc:
                raise _bridge_http_error(exc) from exc
        elif body.action == "revoke":
            try:
                with runtime.bridge_enrollment.member_operation(
                    session,
                    request_id=wizard_request_id,
                    member_id=session.auth_member_id,
                ):
                    runtime.bridge_enrollment.revoke(
                        request_id=wizard_request_id, member_id=session.auth_member_id
                    )
            except BridgeEnrollmentError as exc:
                if exc.code != "enrollment_missing_or_ambiguous":
                    raise _bridge_http_error(exc) from exc
        revision = runtime.settings_control.prepare_bridge_progress(
            session.auth_member_id, request_id=wizard_request_id
        )
        payload = {
            "request_id": wizard_request_id,
            "enrollment_id": record.get("enrollment_id"),
            "member_id": session.auth_member_id,
            "action": body.action,
            "previous_state": previous,
            "state": next_state,
            "revision": revision,
            "changed_by_member_id": session.auth_member_id,
            "changed_at": EventEnvelope.now_utc_iso(),
            "content_exposed": False,
            "writes_authorized": False,
            "provider_effects": False,
        }
        fingerprint = _settings_request_fingerprint(
            "settings.bridge_enrollment_controlled", payload
        )
        event_id = await _write_settings_receipt(
            runtime,
            session,
            event_type="settings.bridge_enrollment_controlled",
            request_id=body.command_id,
            request_fingerprint=fingerprint,
            owner_member_id=session.auth_member_id,
            payload=payload,
        )
        try:
            mutation = runtime.settings_control.apply_bridge_enrollment_event(payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        current = mutation.current
        if retry_preparation:
            try:
                with runtime.bridge_enrollment.member_operation(
                    session,
                    request_id=wizard_request_id,
                    member_id=session.auth_member_id,
                ):
                    prepared = runtime.bridge_enrollment.prepare(
                        request_id=wizard_request_id,
                        member_id=session.auth_member_id,
                        device_class=str(record["device_class"]),
                        capabilities=list(record["capabilities"]),
                        central_url=_settings_central_url(
                            request,
                            allow_testserver=not runtime.settings.deployment_evidence_required,
                        ),
                        allow_testserver=not runtime.settings.deployment_evidence_required,
                    )
            except BridgeEnrollmentError as exc:
                await _record_central_bridge_progress(
                    runtime,
                    session,
                    request_id=wizard_request_id,
                    member_id=session.auth_member_id,
                    previous_state="package_preparing",
                    state="failed",
                    suffix=f"control-{body.command_id}-failed",
                    extra={"reason": exc.code},
                )
                raise _bridge_http_error(exc) from exc
            current = await _record_central_bridge_progress(
                runtime,
                session,
                request_id=wizard_request_id,
                member_id=session.auth_member_id,
                previous_state="package_preparing",
                state="package_ready",
                suffix=f"control-{body.command_id}-ready",
                extra={
                    "enrollment_id": prepared.enrollment_id,
                    "expires_at": prepared.expires_at,
                    "release_commit": prepared.release_commit,
                    "package_sha256": prepared.package_sha256,
                    "capability_set_sha256": prepared.capability_set_sha256,
                },
            )
        _refresh_bridge_ingress(request)
        return {
            "accepted": True,
            "event_id": event_id,
            "state": current["state"],
            "provider_effect_count": 0,
        }


def _require_private_notes(session: Session) -> None:
    if session.auth_role in {"ambient", "device", "child"}:
        raise HTTPException(403, "Notes is private to an authenticated adult member")
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "Notes is unavailable while viewing another member")


def _notes_access_fingerprint(
    action: str,
    *,
    knowledge_id: str,
    grant_id: str | None,
    values: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "action": action,
            "knowledge_id": knowledge_id,
            "grant_id": grant_id,
            "values": values,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _prior_notes_access_command(
    runtime: Any,
    session: Session,
    *,
    action: str,
    request_id: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    if runtime.event_log is None:
        raise HTTPException(503, "Notes authorization storage unavailable")
    key = f"notes:{action}:{session.auth_member_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior["raw_ref"] != f"request-sha256:{fingerprint}":
        raise HTTPException(
            409, "idempotency key conflicts with prior Notes authorization body"
        )
    return prior


@router.get("/api/member/v1/notes")
async def notes_list(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "Notes projection unavailable")
    return {
        "member_id": session.auth_member_id,
        "items": list_visible_notes(
            runtime.projections.connection("member_knowledge"), session
        ),
    }


@router.get("/api/member/v1/notes/search")
async def notes_search(
    request: Request,
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    source: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "Notes projection unavailable")
    items = search_visible_notes(
        runtime.projections.connection("member_knowledge"),
        session,
        query=q,
        limit=limit,
        source_kind=source,
    )
    return {
        "member_id": session.auth_member_id,
        "query": q,
        "source": source,
        "items": items,
        "contract": "deterministic-authorized-full-text-v1",
    }


@router.get("/api/member/v1/notes/{knowledge_id}")
async def notes_detail(
    knowledge_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "Notes projection unavailable")
    conn = runtime.projections.connection("member_knowledge")
    note = get_visible_note(conn, session, knowledge_id=knowledge_id)
    if note is None:
        raise HTTPException(404, "note not found")
    note["grants"] = (
        grants_for_note(conn, session, knowledge_id=knowledge_id)
        if note.get("access_mode") == "owner"
        else []
    )
    return note


@router.post("/api/member/v1/notes/{knowledge_id}/grants", status_code=202)
async def grant_note_access(
    knowledge_id: str,
    body: KnowledgeGrantRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None or runtime.event_log is None:
        raise HTTPException(503, "Notes authorization storage unavailable")
    permissions = set(body.permissions)
    if "search" in permissions:
        permissions.add("read")
    expires_at = body.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise HTTPException(422, "note grant expiry requires a timezone")
        expires_at = expires_at.astimezone(timezone.utc)
    fingerprint = _notes_access_fingerprint(
        "grant",
        knowledge_id=knowledge_id,
        grant_id=None,
        values={
            "grantee_member_id": body.grantee_member_id,
            "permissions": sorted(permissions),
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    prior = await _prior_notes_access_command(
        runtime,
        session,
        action="grant",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        return {
            "accepted": True,
            "duplicate": True,
            "grant_id": prior["payload"]["grant_id"],
            "event_id": prior["event_id"],
        }
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "note grant expiry must be in the future")
    conn = runtime.projections.connection("member_knowledge")
    note = get_visible_note(conn, session, knowledge_id=knowledge_id)
    if note is None or note.get("access_mode") != "owner":
        raise HTTPException(404, "owned note not found")
    if note.get("sensitivity") == "privileged":
        raise HTTPException(403, "privileged notes cannot be shared")
    role = runtime.settings.member_roles.get(body.grantee_member_id)
    if role in {None, "device", "child"}:
        raise HTTPException(
            422, "Notes may only be shared with an enrolled adult member"
        )
    if body.grantee_member_id == session.auth_member_id:
        raise HTTPException(422, "a note is already visible to its owner")
    grant_id = (
        "note_grant_"
        + hashlib.sha256(
            f"{session.tenant_id}:{knowledge_id}:{body.grantee_member_id}".encode()
        ).hexdigest()[:24]
    )
    now = EventEnvelope.now_utc_iso()
    payload = {
        "grant_id": grant_id,
        "knowledge_id": knowledge_id,
        "owner_member_id": session.auth_member_id,
        "grantee_member_id": body.grantee_member_id,
        "permissions": sorted(permissions),
        "granted_at": now,
        "expires_at": expires_at,
    }
    await _guard_member_write(
        runtime, session, "member_knowledge.access_granted", payload
    )
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type="member_knowledge.access_granted",
        schema_version=1,
        occurred_at=now,
        source_adapter="member-portal",
        source_account_id=session.auth_member_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity="normal",
        actor_identity=session.auth_member_id,
        correlation_id=session.correlation_id,
        idempotency_key=f"notes:grant:{session.auth_member_id}:{body.request_id}",
        raw_ref=f"request-sha256:{fingerprint}",
        payload=payload,
    )
    try:
        event_id = await runtime.events.write(envelope)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior Notes authorization body"
        ) from exc
    await runtime.wait_for_projection("member_knowledge", event_id)
    return {
        "accepted": True,
        "duplicate": False,
        "grant_id": grant_id,
        "event_id": event_id,
    }


@router.post(
    "/api/member/v1/notes/{knowledge_id}/grants/{grant_id}/revoke",
    status_code=202,
)
async def revoke_note_access(
    knowledge_id: str,
    grant_id: str,
    body: KnowledgeGrantRevokeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.projections is None or runtime.event_log is None:
        raise HTTPException(503, "Notes authorization storage unavailable")
    fingerprint = _notes_access_fingerprint(
        "revoke",
        knowledge_id=knowledge_id,
        grant_id=grant_id,
        values={},
    )
    prior = await _prior_notes_access_command(
        runtime,
        session,
        action="revoke",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    if prior is not None:
        return {
            "accepted": True,
            "duplicate": True,
            "grant_id": prior["payload"]["grant_id"],
            "event_id": prior["event_id"],
        }
    conn = runtime.projections.connection("member_knowledge")
    grants = grants_for_note(conn, session, knowledge_id=knowledge_id)
    grant = next((row for row in grants if row["grant_id"] == grant_id), None)
    if grant is None:
        raise HTTPException(404, "note grant not found")
    now = EventEnvelope.now_utc_iso()
    payload = {
        "grant_id": grant_id,
        "owner_member_id": session.auth_member_id,
        "revoked_by_member_id": session.auth_member_id,
        "revoked_at": now,
    }
    await _guard_member_write(
        runtime, session, "member_knowledge.access_revoked", payload
    )
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type="member_knowledge.access_revoked",
        schema_version=1,
        occurred_at=now,
        source_adapter="member-portal",
        source_account_id=session.auth_member_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity="normal",
        actor_identity=session.auth_member_id,
        correlation_id=session.correlation_id,
        idempotency_key=f"notes:revoke:{session.auth_member_id}:{body.request_id}",
        raw_ref=f"request-sha256:{fingerprint}",
        payload=payload,
    )
    try:
        event_id = await runtime.events.write(envelope)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior Notes authorization body"
        ) from exc
    await runtime.wait_for_projection("member_knowledge", event_id)
    return {
        "accepted": True,
        "duplicate": False,
        "grant_id": grant_id,
        "event_id": event_id,
    }


@router.post("/api/member/v1/calendar/onboarding", status_code=202)
async def configure_calendar_policy(
    body: CalendarOnboardingRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Record principal-approved calendar semantics without model decisions."""

    require_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None or runtime.event_log is None:
        raise HTTPException(503, "calendar policy storage is unavailable")
    if body.member_id not in runtime.settings.member_roles:
        raise HTTPException(422, "calendar policy member is not configured")
    if runtime.settings.member_roles[body.member_id] == "device":
        raise HTTPException(422, "a device cannot own calendar policy")
    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            422, "calendar policy timezone is not an IANA zone"
        ) from exc
    fingerprint = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    now = EventEnvelope.now_utc_iso()
    conn = runtime.projections.connection("calendars")
    source_rows = conn.execute(
        "SELECT * FROM calendar_sources WHERE tenant_id=?",
        (session.tenant_id,),
    ).fetchall()
    sources = {
        (
            str(row["member_id"]),
            str(row["provider"]),
            str(row["provider_account_id"]),
            str(row["calendar_external_id"]),
        ): dict(row)
        for row in source_rows
    }
    routes: set[tuple[str, str, str, str]] = set()
    envelopes: list[EventEnvelope] = []
    for index, requested in enumerate(body.rules):
        route = (
            requested.source_member_id,
            requested.provider,
            requested.provider_account_id,
            requested.calendar_external_id,
        )
        if route in routes:
            raise HTTPException(422, "calendar policy contains a duplicate source")
        routes.add(route)
        source = sources.get(route)
        if source is None:
            raise HTTPException(422, "calendar source has not been observed")
        if requested.write_target and not bool(source.get("write_events")):
            raise HTTPException(422, "write destination is not provider-writable")
        route_hash = hashlib.sha256("\x1f".join(route).encode("utf-8")).hexdigest()[:20]
        payload = CalendarRuleConfiguredV1.model_validate(
            {
                **requested.model_dump(mode="json"),
                "rule_id": f"calendar-rule-{body.member_id}-{route_hash}",
                "subject_member_id": body.member_id,
                "configured_at": now,
            }
        ).model_dump(mode="json")
        envelopes.append(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000) + index,
                tenant_id=session.tenant_id,
                type="calendar.rule.configured",
                schema_version=1,
                occurred_at=now,
                source_adapter="member-calendar",
                source_account_id=session.auth_member_id,
                owner_scope=f"private:{body.member_id}",
                visibility_scope=f"private:{body.member_id}",
                sensitivity="normal",
                correlation_id=session.correlation_id,
                causation_id=source.get("last_event_id"),
                actor_identity=session.auth_member_id,
                idempotency_key=(
                    f"calendar-onboarding:{body.member_id}:{body.request_id}:rule:{route_hash}"
                ),
                raw_ref=f"request-sha256:{fingerprint}",
                payload=payload,
            )
        )
    schedule_payload = CalendarSchedulePolicyConfiguredV1.model_validate(
        {
            "member_id": body.member_id,
            "timezone": body.timezone,
            "windows": [window.model_dump(mode="json") for window in body.windows],
            "buffer_minutes": body.buffer_minutes,
            "accepted_chores_required": body.accepted_chores_required,
            "one_confirm_write": body.one_confirm_write,
            "configured_at": now,
        }
    ).model_dump(mode="json")
    envelopes.append(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000) + len(envelopes),
            tenant_id=session.tenant_id,
            type="calendar.schedule_policy.configured",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-calendar",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{body.member_id}",
            visibility_scope=f"private:{body.member_id}",
            sensitivity="normal",
            correlation_id=session.correlation_id,
            actor_identity=session.auth_member_id,
            idempotency_key=(
                f"calendar-onboarding:{body.member_id}:{body.request_id}:schedule"
            ),
            raw_ref=f"request-sha256:{fingerprint}",
            payload=schedule_payload,
        )
    )
    event_ids = await runtime.events.write_batch(envelopes)
    await runtime.wait_for_projection("calendars", event_ids[-1])
    return {
        "accepted": True,
        "member_id": body.member_id,
        "event_ids": event_ids,
        "rule_count": len(body.rules),
        "deterministic": True,
    }


@router.post("/api/member/v1/comms/{interaction_id}/commands")
async def communication_command(
    interaction_id: str,
    body: CommsCommandRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.communication_command_locks.setdefault(
        f"{session.auth_member_id}:{interaction_id}", asyncio.Lock()
    )
    async with lock:
        return await _communication_command_locked(
            interaction_id, body, request, session
        )


async def _communication_command_locked(
    interaction_id: str,
    body: CommsCommandRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    """Apply local focus state or enqueue one governed provider mutation."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "communication actions are unavailable while viewing another member"
        )
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "communication state is unavailable")
    interactions_conn = runtime.projections.connection("interactions")
    interaction = get_interaction(
        interactions_conn, session, interaction_id=interaction_id
    )
    if interaction is None or (
        interaction.get("member_id") != session.auth_member_id
        and interaction.get("owner_scope") != f"private:{session.auth_member_id}"
    ):
        raise HTTPException(404, "communication not found")
    if interaction["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "communication changed; refresh before acting")

    now = EventEnvelope.now_utc_iso()
    local_operations = {"snooze", "unsnooze", "quiet", "unquiet"}
    if body.operation in local_operations:
        if body.operation == "snooze":
            if body.snoozed_until is None:
                raise HTTPException(422, "snooze requires a wake time")
            try:
                wake = datetime.fromisoformat(body.snoozed_until.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(422, "snooze wake time is invalid") from exc
            if wake.tzinfo is None or wake <= datetime.now(timezone.utc):
                raise HTTPException(422, "snooze wake time must be in the future")
        payload = {
            "interaction_id": interaction_id,
            "member_id": session.auth_member_id,
            "operation": body.operation,
            "snoozed_until": body.snoozed_until if body.operation == "snooze" else None,
            "quieted": True
            if body.operation == "quiet"
            else False
            if body.operation == "unquiet"
            else None,
            "changed_at": now,
            "expected_last_event_id": body.expected_last_event_id,
        }
        decision = await runtime.guarded_write.check(
            session, "communication.state_changed", payload
        )
        if not decision.pass_:
            raise HTTPException(
                403,
                {
                    "reason": decision.reason,
                    "correlation_id": decision.correlation_id,
                },
            )
        event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=session.tenant_id,
                type="communication.state_changed",
                schema_version=1,
                occurred_at=now,
                source_adapter="member-portal",
                source_account_id=session.auth_member_id,
                owner_scope=f"private:{session.auth_member_id}",
                visibility_scope=f"private:{session.auth_member_id}",
                sensitivity=interaction.get("sensitivity") or "normal",
                correlation_id=decision.correlation_id,
                actor_identity=session.auth_member_id,
                idempotency_key=(
                    f"communication-state:{session.auth_member_id}:{body.request_id}"
                ),
                payload=payload,
            )
        )
        await runtime.wait_for_projection("interactions", event_id)
        projected = get_interaction(
            interactions_conn, session, interaction_id=interaction_id
        )
        applied = projected is not None and (
            (
                body.operation == "snooze"
                and projected.get("snoozed_until") == body.snoozed_until
            )
            or (body.operation == "unsnooze" and projected.get("snoozed_until") is None)
            or (body.operation == "quiet" and bool(projected.get("quieted")) is True)
            or (body.operation == "unquiet" and bool(projected.get("quieted")) is False)
        )
        if not applied:
            # A later interaction revision can win between the pre-append CAS
            # check and projection delivery. The event remains immutable, but
            # the API must not describe a non-applied state change as success.
            raise HTTPException(409, "communication changed; refresh before acting")
        return {
            "accepted": True,
            "local": True,
            "event_id": event_id,
            "interaction": projected,
        }

    provider = str(interaction.get("provider") or "")
    provider_account_id = str(interaction.get("provider_account_id") or "")
    operation_capability = {
        "reply": "reply",
        "mark_read": "mark_read",
        "mark_unread": "mark_read",
        "archive": "archive",
        "restore": "archive",
    }[body.operation]
    account = next(
        (
            item
            for item in communication_accounts(runtime, session.auth_member_id)
            if item["provider"] == provider
            and item["provider_account_id"] == provider_account_id
        ),
        None,
    )
    if account is None:
        raise HTTPException(409, "the source account is not bound to this member")
    if not account["capabilities"].get(operation_capability):
        raise HTTPException(
            409,
            account.get("write_access_remedy")
            or "the provider does not support this action",
        )
    if body.operation == "reply" and not (body.body or "").strip():
        raise HTTPException(422, "reply body cannot be empty")
    if body.operation not in _COMMS_EXTERNAL_OPERATIONS.get(provider, set()):
        raise HTTPException(422, "the provider does not support this action")

    participant_map = participants_for(
        interactions_conn, session, interaction_ids=[interaction_id]
    )
    fixture_recipient = next(
        (
            item.get("party_id")
            for item in participant_map.get(interaction_id, [])
            if item["role"] == "from" and item.get("party_id")
        ),
        None,
    )
    recipient = interaction.get("reply_to_identifier")
    if provider == "fixture":
        recipient = recipient or fixture_recipient
    try:
        content_privacy_guard = validate_outbound_communication(
            operation=body.operation,
            member_id=session.auth_member_id,
            interaction=interaction,
            account=account,
            recipient=recipient,
            body=body.body,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    provider_payload: dict[str, Any] = {
        "interaction_id": interaction_id,
        "expected_last_event_id": body.expected_last_event_id,
        "recipient": recipient,
        "body": (body.body or "").strip() or None,
        "thread_id": interaction.get("thread_id"),
        "subject": interaction.get("subject"),
        "rfc_message_id": interaction.get("rfc_message_id"),
        "workspace": interaction.get("workspace") or "personal",
        "content_privacy_guard": content_privacy_guard,
    }
    observation_active = runtime._observation_active
    if provider == "bluebubbles":
        try:
            exact_body = validate_exact_body(body.body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        source_participants = participant_ids(participant_map.get(interaction_id, []))
        if len(source_participants) < 2:
            raise HTTPException(
                409, "the exact Messages participant set is unavailable"
            )
        reply_control = reply_control_for(runtime, session.auth_member_id)
        if reply_control["state"] != "enabled_ask_every_time":
            raise HTTPException(
                409,
                reply_control.get("reason")
                or "enable ask-every-time Messages replies in Settings",
            )
        if (
            reply_control.get("provider_account_id") != provider_account_id
            or reply_control.get("bridge_enrollment_id")
            != interaction.get("bridge_enrollment_id")
            or reply_control.get("protected_source_id")
            != interaction.get("protected_source_id")
        ):
            raise HTTPException(409, "the Messages reply coordinates changed")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=REPLY_APPROVAL_TTL_SECONDS)
        ).isoformat()
        observation_active = (
            await runtime.observation.is_active()
            if runtime.observation is not None
            else True
        )
        provider_payload = {
            "contract": REPLY_CONTRACT,
            "command_id": body.request_id,
            "idempotency_key": f"messages-reply:{session.auth_member_id}:{body.request_id}",
            "member_owner_id": session.auth_member_id,
            "actor_principal_id": session.auth_member_id,
            "operator_principal_id": session.auth_member_id,
            "bridge_enrollment_id": interaction.get("bridge_enrollment_id"),
            "provider_account_id": provider_account_id,
            "protected_source_id": interaction.get("protected_source_id"),
            "native_chat_id": interaction.get("native_chat_id"),
            "in_reply_to_native_message_id": interaction.get("native_message_id"),
            "interaction_id": interaction_id,
            "expected_last_event_id": body.expected_last_event_id,
            "expected_native_revision": interaction.get("native_revision"),
            "participant_ids": source_participants,
            "participant_fingerprint": canonical_sha256(source_participants),
            "is_group": bool(interaction.get("is_group")),
            "body": exact_body,
            "body_fingerprint": body_sha256(exact_body),
            "capability_version": REPLY_CAPABILITY_VERSION,
            "policy_version": REPLY_POLICY_VERSION,
            "setting_revision": reply_control["revision"],
            "approval_basis": "authenticated_member_exact_preview",
            "approval_expires_at": expires_at,
            "correlation_id": session.correlation_id,
            "causation_id": body.expected_last_event_id,
            "observation_mode_disposition": (
                "suppress_without_transport_call"
                if observation_active
                else "dispatch_if_all_gates_pass"
            ),
            "command_authentication": {
                "scheme": "protected-central-event-log-v1",
                "audience_bridge_enrollment_id": interaction.get(
                    "bridge_enrollment_id"
                ),
            },
            "content_privacy_guard": {
                **content_privacy_guard,
                "policy": REPLY_CONTENT_GUARD,
                "plain_text_only": True,
                "attachments": [],
                "quoted_history_added": False,
                "signature_added": False,
            },
        }
    command_payload = {
        "command_id": body.request_id,
        "member_id": session.auth_member_id,
        "provider": provider,
        "provider_account_id": provider_account_id,
        "operation": body.operation,
        "target_external_id": interaction.get("native_message_id"),
        "expected_provider_revision": interaction.get("native_revision")
        if provider == "bluebubbles"
        else None,
        "payload": provider_payload,
        "requested_at": now,
    }
    decision = await runtime.guarded_write.check(
        session, "external_sync.write_requested", command_payload
    )
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {
                "reason": decision.reason,
                "correlation_id": decision.correlation_id,
            },
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    event_id, duplicate = await _append_provider_action_confirmation(
        runtime,
        session,
        command_payload=command_payload,
        request_id=body.request_id,
        request_fingerprint=fingerprint,
        source_adapter="member-portal",
        sensitivity=cast(Sensitivity, interaction.get("sensitivity") or "normal"),
        causation_id=body.expected_last_event_id,
    )
    return {
        "accepted": True,
        "local": False,
        "command_id": body.request_id,
        "event_id": event_id,
        "domain_approval_event_id": event_id,
        "state": "awaiting_openclaw_approval",
        "duplicate": duplicate,
        "observation_mode": observation_active,
    }


@router.post("/api/member/v1/comms/replies/{command_id}/cancel", status_code=202)
async def cancel_communication_reply(
    command_id: str,
    body: CommsReplyCancelRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Cancel a G11 reply before the independent OpenClaw approval is consumed."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "communication actions are unavailable in view-as")
    runtime = _runtime(request)
    domain_key = f"provider-action:{session.auth_member_id}:{command_id}"
    domain_event = await runtime.event_log.get_by_idempotency(
        session.tenant_id, domain_key
    )
    nested = (
        domain_event.get("payload", {}).get("command_payload")
        if isinstance(domain_event, dict)
        else None
    )
    if (
        not isinstance(domain_event, dict)
        or domain_event.get("type") != "member.provider_action.confirmed"
        or not isinstance(nested, dict)
        or nested.get("contract") != REPLY_CONTRACT
    ):
        raise HTTPException(404, "pending Messages reply not found")
    external = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"external-sync-approved:{domain_event['event_id']}"
    )
    if external is not None:
        raise HTTPException(409, "Messages reply was already dispatched")
    cancel_key = f"messages-reply-cancel:{session.auth_member_id}:{command_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, cancel_key)
    if prior is not None:
        return {
            "accepted": True,
            "idempotent": True,
            "event_id": prior["event_id"],
            "state": "canceled",
            "provider_effects": 0,
        }
    now = EventEnvelope.now_utc_iso()
    payload = {
        "command_id": command_id,
        "member_id": session.auth_member_id,
        "interaction_id": nested["interaction_id"],
        "domain_approval_event_id": domain_event["event_id"],
        "canceled_at": now,
        "provider_called": False,
    }
    await _guard_member_write(runtime, session, "messaging.reply.canceled", payload)
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="messaging.reply.canceled",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-portal",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity="sensitive",
            correlation_id=session.correlation_id,
            causation_id=domain_event["event_id"],
            actor_identity=session.auth_member_id,
            idempotency_key=cancel_key,
            payload=payload,
        )
    )
    await runtime.wait_for_projection("interactions", event_id)
    return {
        "accepted": True,
        "event_id": event_id,
        "state": "canceled",
        "provider_effects": 0,
    }


@router.post(
    "/api/member/v1/people/{party_id}/identifiers/{identifier_id}/commands",
    status_code=202,
)
async def contact_identifier_command(
    party_id: str,
    identifier_id: str,
    body: ContactIdentifierCommandRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Queue D23's sparse Apple Contacts identifier-correction write seam."""

    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "contact corrections are unavailable while viewing another member"
        )
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "People state is unavailable")
    row = (
        runtime.projections.connection("parties")
        .execute(
            """
        SELECT i.identifier_id, i.kind AS identifier_kind,
               i.value_normalized AS prior_value_normalized, i.label,
               i.primary_for_kind, i.sensitivity,
               c.contact_record_id, c.provider, c.provider_account_id,
               c.container_external_id, c.external_id, c.provider_revision,
               c.last_event_id
          FROM identifiers i
          JOIN contact_records c
            ON c.tenant_id=i.tenant_id AND c.contact_record_id=i.source_record_id
         WHERE i.tenant_id=? AND i.party_id=? AND i.identifier_id=?
           AND i.source_kind='contact_record' AND i.revoked_at IS NULL
           AND c.member_id=? AND c.deleted_at IS NULL
        """,
            (
                session.tenant_id,
                party_id,
                identifier_id,
                session.auth_member_id,
            ),
        )
        .fetchone()
    )
    if row is None or row["provider"] != "apple_contacts":
        raise HTTPException(404, "Apple contact identifier not found")
    if (
        row["provider_account_id"] != body.provider_account_id
        or row["container_external_id"] != body.container_external_id
    ):
        raise HTTPException(422, "contact correction provider route does not match")
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "contact changed; refresh before acting")
    if not row["provider_revision"] or (
        row["provider_revision"] != body.expected_provider_revision
    ):
        raise HTTPException(
            409, "contact provider revision changed; refresh before acting"
        )
    identifier_kind = str(row["identifier_kind"])
    if identifier_kind not in {"email", "phone", "imessage_handle"}:
        raise HTTPException(422, "this contact endpoint cannot be corrected")
    normalized = normalize_identifier(identifier_kind, body.value)
    if not normalized:
        raise HTTPException(422, "contact identifier cannot be blank")
    if normalized == row["prior_value_normalized"]:
        raise HTTPException(422, "contact identifier has not changed")
    _require_configured_external_route(
        runtime,
        provider="apple_contacts",
        target_member=session.auth_member_id,
        provider_account_id=body.provider_account_id,
        container_external_id=body.container_external_id,
    )
    now = EventEnvelope.now_utc_iso()
    provider_payload = {
        "member_id": session.auth_member_id,
        "container_external_id": body.container_external_id,
        "contact_record_id": row["contact_record_id"],
        "identifier_id": identifier_id,
        "identifier_kind": identifier_kind,
        "prior_value_normalized": row["prior_value_normalized"],
        "value": body.value.strip(),
        "value_normalized": normalized,
        "label": body.label if body.label is not None else row["label"],
        "primary": bool(row["primary_for_kind"]),
        "expected_provider_revision": body.expected_provider_revision,
        "content_privacy_guard": {
            "member_bound": True,
            "provider_fields_allowlisted": ["label", "value"],
            "source_scope": f"private:{session.auth_member_id}",
        },
    }
    command_payload = {
        "command_id": body.request_id,
        "member_id": session.auth_member_id,
        "provider": "apple_contacts",
        "provider_account_id": body.provider_account_id,
        "operation": "update_party_identifier",
        "target_external_id": row["external_id"],
        "expected_provider_revision": body.expected_provider_revision,
        "payload": provider_payload,
        "requested_at": now,
    }
    decision = await runtime.guarded_write.check(
        session, "external_sync.write_requested", command_payload
    )
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    event_id, duplicate = await _append_provider_action_confirmation(
        runtime,
        session,
        command_payload=command_payload,
        request_id=body.request_id,
        request_fingerprint=fingerprint,
        source_adapter="member-people",
        sensitivity=cast(Sensitivity, row["sensitivity"] or "normal"),
        causation_id=body.expected_last_event_id,
    )
    return {
        "accepted": True,
        "duplicate": duplicate,
        "command_id": body.request_id,
        "event_id": event_id,
        "domain_approval_event_id": event_id,
        "state": "awaiting_openclaw_approval",
        "observation_mode": runtime._observation_active,
    }


@router.post("/api/member/v1/people/{party_id}/profile", status_code=202)
async def set_people_profile(
    party_id: str,
    body: PeopleProfileRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "People settings are unavailable while viewing another member"
        )
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "People state is unavailable")
    parties = runtime.projections.connection("parties")
    person = get_party(parties, session, party_id=party_id)
    if person is None or person.get("qualification") != "qualified":
        raise HTTPException(404, "person not found")
    existing = parties.execute(
        "SELECT last_event_id FROM member_relationship_profiles "
        "WHERE tenant_id=? AND member_id=? AND party_id=?",
        (session.tenant_id, session.auth_member_id, party_id),
    ).fetchone()
    if (
        existing is not None
        and body.expected_last_event_id != existing["last_event_id"]
    ):
        raise HTTPException(409, "relationship settings changed; refresh before saving")
    if body.suppressed and not (body.suppression_reason or "").strip():
        raise HTTPException(422, "suppression requires a reason")
    now = EventEnvelope.now_utc_iso()
    profile_id = f"people_profile_{hashlib.sha256(f'{session.auth_member_id}:{party_id}'.encode()).hexdigest()[:22]}"
    payload = {
        "profile_id": profile_id,
        "member_id": session.auth_member_id,
        "party_id": party_id,
        "circle": body.circle,
        "desired_cadence_days": body.desired_cadence_days,
        "cues_enabled": body.cues_enabled,
        "favorite": body.favorite,
        "suppressed": body.suppressed,
        "suppression_reason": body.suppression_reason,
        "tags": sorted({value.strip() for value in body.tags if value.strip()}),
        "changed_at": now,
    }
    await _guard_member_write(
        runtime, session, "member_relationship.profile_set", payload
    )
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="member_relationship.profile_set",
            schema_version=1,
            occurred_at=now,
            source_adapter="member-people",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity="normal",
            correlation_id=session.correlation_id,
            causation_id=person["last_event_id"],
            actor_identity=session.auth_member_id,
            idempotency_key=f"people-profile:{session.auth_member_id}:{body.request_id}",
            payload=payload,
        )
    )
    await runtime.wait_for_projection("parties", event_id)
    return {"accepted": True, "event_id": event_id, "profile_id": profile_id}


@router.post("/api/member/v1/people/{party_id}/lifecycle", status_code=202)
async def change_people_lifecycle(
    party_id: str,
    body: PeopleLifecycleRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "People lifecycle is unavailable while viewing another member"
        )
    runtime = _runtime(request)
    if runtime.projections is None or runtime.events is None:
        raise HTTPException(503, "People state is unavailable")
    party = get_party(
        runtime.projections.connection("parties"), session, party_id=party_id
    )
    if party is None or party.get("owner_scope") != f"private:{session.auth_member_id}":
        raise HTTPException(404, "member-owned person not found")
    if party["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "person changed; refresh before acting")
    if body.action == "archive" and party.get("qualification") == "archived":
        raise HTTPException(409, "person is already archived")
    if body.action == "restore" and party.get("qualification") != "archived":
        raise HTTPException(409, "person is not archived")
    now = EventEnvelope.now_utc_iso()
    event_type = "party.archived" if body.action == "archive" else "party.restored"
    payload = (
        {
            "party_id": party_id,
            "archived_at": now,
            "archived_by_member_id": session.auth_member_id,
            "reason": body.reason,
        }
        if body.action == "archive"
        else {
            "party_id": party_id,
            "restored_at": now,
            "restored_by_member_id": session.auth_member_id,
        }
    )
    await _guard_member_write(runtime, session, event_type, payload)
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type=event_type,
            schema_version=1,
            occurred_at=now,
            source_adapter="member-people",
            source_account_id=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity=cast(Sensitivity, party.get("sensitivity") or "normal"),
            correlation_id=session.correlation_id,
            causation_id=party["last_event_id"],
            actor_identity=session.auth_member_id,
            idempotency_key=f"people-lifecycle:{session.auth_member_id}:{body.request_id}",
            payload=payload,
        )
    )
    await runtime.wait_for_projection("parties", event_id)
    return {"accepted": True, "event_id": event_id, "state": body.action}


@router.get("/api/member/v1/people/search")
async def people_search(
    request: Request, session: SessionDep, q: str = ""
) -> dict[str, Any]:
    query = q.strip()
    if len(query) < 2:
        return {
            "query": query,
            "results": [],
            "contract": "authorized-people-search-v1",
        }
    if len(query) > 200:
        raise HTTPException(422, "search is too long")
    return crm_search(_runtime(request), session, query)


@router.post(
    "/api/member/v1/registry/{object_kind}/{object_id}/lifecycle",
    status_code=202,
)
async def change_registry_lifecycle(
    object_kind: Literal["place", "asset", "account"],
    object_id: str,
    body: RegistryLifecycleRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403,
            "Household registry changes are unavailable while viewing another member",
        )
    runtime = _runtime(request)
    if (
        runtime.projections is None
        or runtime.events is None
        or runtime.event_log is None
    ):
        raise HTTPException(503, "household registry is unavailable")
    conn = runtime.projections.connection("places_assets_accounts")

    def current_item() -> dict[str, Any] | None:
        if object_kind == "place":
            return get_place(conn, session, place_id=object_id)
        if object_kind == "asset":
            return get_asset(conn, session, asset_id=object_id)
        return get_account(conn, session, account_id=object_id)

    row = current_item()
    if row is None:
        raise HTTPException(404, f"{object_kind} not found")
    event_type = (
        f"{object_kind}.{'archived' if body.action == 'archive' else 'restored'}"
    )
    fingerprint = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    idempotency_key = (
        f"registry-lifecycle:{object_kind}:{object_id}:{session.auth_member_id}:"
        f"{body.request_id}"
    )
    prior = await runtime.event_log.get_by_idempotency(
        session.tenant_id, idempotency_key
    )
    if prior is not None:
        if prior.get("raw_ref") != f"request-sha256:{fingerprint}":
            raise HTTPException(
                409, "request id conflicts with another registry action"
            )
        return {
            "accepted": True,
            "duplicate": True,
            "event_id": prior["event_id"],
            "item": current_item(),
        }
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, f"{object_kind} changed; refresh before acting")
    current = str(row.get("lifecycle_status") or "active")
    if body.action == "archive" and current == "archived":
        raise HTTPException(409, f"{object_kind} is already archived")
    if body.action == "restore" and current != "archived":
        raise HTTPException(409, f"{object_kind} is not archived")
    now = EventEnvelope.now_utc_iso()
    payload = (
        {
            f"{object_kind}_id": object_id,
            "archived_at": now,
            "archived_by_party_id": session.auth_member_id,
            "reason": body.reason,
        }
        if body.action == "archive"
        else {
            f"{object_kind}_id": object_id,
            "restored_at": now,
            "restored_by_party_id": session.auth_member_id,
        }
    )
    await _guard_member_write(runtime, session, event_type, payload)
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type=event_type,
            schema_version=1,
            occurred_at=now,
            source_adapter="member-settings",
            source_account_id=session.auth_member_id,
            owner_scope=cast(str, row["owner_scope"]),
            visibility_scope=cast(str, row["visibility_scope"]),
            sensitivity=cast(Sensitivity, row.get("sensitivity") or "normal"),
            correlation_id=session.correlation_id,
            causation_id=row["last_event_id"],
            actor_identity=session.auth_member_id,
            idempotency_key=idempotency_key,
            raw_ref=f"request-sha256:{fingerprint}",
            payload=payload,
        )
    )
    await runtime.wait_for_projection("places_assets_accounts", event_id)
    return {
        "accepted": True,
        "duplicate": False,
        "event_id": event_id,
        "item": current_item(),
    }


@router.get("/api/member/v1/people/{party_id}")
async def person_detail(
    party_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    return crm_detail(_runtime(request), session, party_id)


@router.get("/api/member/v1/chat/status")
async def chat_status(request: Request, session: SessionDep) -> dict[str, Any]:
    return _chat_policy(_runtime(request), session)


@router.get("/api/member/v1/chat/history")
async def chat_history(
    request: Request, session: SessionDep, limit: int = 30
) -> dict[str, Any]:
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    from adminme.finance.disclosure import FinanceDisclosureError, check_disclosure

    try:
        await check_disclosure(runtime, session, session_key)
        items = await runtime.member_chat.history(
            session_key=session_key, limit=min(max(limit, 1), 50)
        )
        await check_disclosure(runtime, session, session_key)
        _require_chat(runtime, session)
    except FinanceDisclosureError as exc:
        raise HTTPException(403, str(exc)) from None
    except MemberChatError as exc:
        runtime.member_chat_last_failure = {
            "code": exc.code,
            "retryable": exc.retryable,
        }
        await runtime._refresh_capability_registry()
        raise HTTPException(
            503, _safe_error(exc, session.correlation_id or str(uuid.uuid4()))
        ) from exc
    return {"items": items, "transcript_owner": "openclaw"}


@router.post("/api/member/v1/chat/stream")
async def chat_stream(
    body: ChatRequest, request: Request, session: SessionDep
) -> StreamingResponse:
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    decision = runtime.member_chat_limiter.decide(
        f"chat:{session.auth_member_id}", 60, 12
    )
    if not decision.allowed:
        raise HTTPException(
            429, {"code": "rate_limited", "retry_after_s": decision.retry_after_s}
        )
    correlation_id = session.correlation_id or str(uuid.uuid4())

    async def generate() -> AsyncIterator[bytes]:
        from adminme.finance.disclosure import (
            FinanceDisclosureError,
            check_disclosure,
            session_has_finance,
            session_marked_in_memory,
        )

        lock = runtime.member_chat_locks.setdefault(
            session.auth_member_id, asyncio.Lock()
        )
        try:
            async with lock:
                marked = await session_has_finance(runtime, session, session_key)
                await check_disclosure(runtime, session, session_key)
                buffer_text = marked or bool(
                    runtime.settings.finance_publications_enabled
                    and session.auth_member_id in runtime.settings.finance_model_members
                )
                pending_text = ""
                run_id = None
                saw_final = False
                async for chunk in runtime.member_chat.stream(
                    session_key=session_key,
                    message=body.message,
                    idempotency_key=f"member:{session.auth_member_id}:{body.request_id}",
                ):
                    runtime.member_chat_last_failure = None
                    if chunk.run_id is not None:
                        if run_id is not None and chunk.run_id != run_id:
                            raise FinanceDisclosureError(
                                "finance_disclosure_run_changed"
                            )
                        run_id = chunk.run_id
                    buffer_text = buffer_text or session_marked_in_memory(
                        runtime, session, session_key
                    )
                    if buffer_text and chunk.kind == "delta":
                        pending_text = (
                            (chunk.text or "")
                            if chunk.replace
                            else pending_text + (chunk.text or "")
                        )
                        if len(pending_text.encode("utf-8")) > 24_000:
                            raise FinanceDisclosureError(
                                "finance_disclosure_answer_too_large"
                            )
                        continue
                    if chunk.kind == "final":
                        verified_text = await check_disclosure(
                            runtime,
                            session,
                            session_key,
                            run_id=run_id,
                            require_run=True,
                            answer_text=pending_text if buffer_text else None,
                        )
                        if buffer_text:
                            pending_text = verified_text or ""
                        _require_chat(runtime, session)
                        saw_final = True
                        if pending_text:
                            yield _sse(
                                {
                                    "kind": "delta",
                                    "text": pending_text,
                                    "replace": True,
                                    "retryable": False,
                                    "correlation_id": correlation_id,
                                }
                            )
                            pending_text = ""
                    public = chunk.public()
                    public["correlation_id"] = correlation_id
                    yield _sse(public)
                    if await request.is_disconnected():
                        await runtime.member_chat.abort(session_key=session_key)
                        return
                if buffer_text and pending_text and not saw_final:
                    raise FinanceDisclosureError("finance_disclosure_final_required")
        except FinanceDisclosureError as exc:
            await runtime.member_chat.abort(session_key=session_key)
            yield _sse(
                {
                    "kind": "error",
                    "code": str(exc),
                    "retryable": False,
                    "correlation_id": correlation_id,
                }
            )
        except asyncio.CancelledError:
            await runtime.member_chat.abort(session_key=session_key)
            raise
        except MemberChatError as exc:
            runtime.member_chat_last_failure = {
                "code": exc.code,
                "retryable": exc.retryable,
            }
            await runtime._refresh_capability_registry()
            yield _sse(_safe_error(exc, correlation_id))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/api/member/v1/chat/abort")
async def chat_abort(request: Request, session: SessionDep) -> dict[str, bool]:
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    return {"aborted": await runtime.member_chat.abort(session_key=session_key)}


@router.get("/api/member/v1/chat/reply-approvals")
async def chat_reply_approvals(request: Request, session: SessionDep) -> dict[str, Any]:
    require_principal(session)
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    try:
        return {"items": await pending_cos_approvals(runtime, session, session_key)}
    except MemberChatError as exc:
        raise HTTPException(503, "Reply approval is temporarily unavailable") from exc


@router.post("/api/member/v1/chat/reply-approvals")
async def chat_reply_approval_decision(
    body: ChatApprovalDecision, request: Request, session: SessionDep
) -> dict[str, bool]:
    require_principal(session)
    runtime = _runtime(request)
    _, session_key = _require_chat(runtime, session)
    try:
        return await resolve_cos_approval(
            runtime, session, session_key, **body.model_dump()
        )
    except MemberChatError as exc:
        raise HTTPException(503, "No sending decision is confirmed") from exc


@router.get("/api/member/v1/evidence/{event_id}")
async def evidence(
    event_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    event = await runtime.event_log.get(event_id)
    if event is None:
        raise HTTPException(404, "evidence not found")
    visibility = str(event.get("visibility_scope") or "shared:household")
    if visibility not in session.allowed_scopes or not allowed_read(
        session,
        str(event.get("sensitivity") or "normal"),
        str(event.get("owner_scope") or "shared:household"),
    ):
        raise HTTPException(404, "evidence not found")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    safe_fields = {
        key: payload[key]
        for key in (
            "task_id",
            "commitment_id",
            "title",
            "description",
            "decision",
            "outcome",
            "state",
            "reason",
            "confidence",
            "approval_state",
            "delivery_result",
            "correction_of",
        )
        if key in payload
    }
    return {
        "event_id": event["event_id"],
        "type": event["type"],
        "source": {
            "adapter": event["source_adapter"],
            "account": event["source_account_id"],
        },
        "actor": event.get("actor_identity"),
        "occurred_at": event["occurred_at"],
        "ingested_at": event["recorded_at"],
        "confidence": payload.get("confidence"),
        "scope": {
            "owner": event["owner_scope"],
            "visibility": event["visibility_scope"],
            "sensitivity": event["sensitivity"],
        },
        "why": {
            "correlation_id": event.get("correlation_id"),
            "causation_id": event.get("causation_id"),
        },
        "correction": payload.get("correction_of"),
        "approval": payload.get("approval_state") or payload.get("decision"),
        "delivery": payload.get("delivery_result") or payload.get("outcome"),
        "subject": safe_fields,
    }


@router.post("/api/member/v1/lifecycle")
async def member_lifecycle(
    body: MemberLifecycleRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    result = await _execute_lifecycle(body, request, session)
    brief = build_surface(_runtime(request), session, "today")["brief"]
    return {**result, "next_action": result.get("what_now"), "brief": brief}


@router.put("/api/member/v1/recurrences/{recurrence_id}")
async def define_recurrence(
    recurrence_id: str,
    body: RecurrenceDefinitionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "recurrence changes are unavailable while viewing another member"
        )
    try:
        next_occurrence = datetime.fromisoformat(
            body.next_occurrence.replace("Z", "+00:00")
        )
        if next_occurrence.tzinfo is None:
            raise ValueError("timezone required")
        rule = rrulestr(body.rrule, dtstart=next_occurrence)
        if rule.after(next_occurrence, inc=True) is None:
            raise ValueError("rule has no occurrence")
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            422, "repeat rule and next occurrence must form a valid dated schedule"
        ) from exc
    if body.scope == "household" and session.auth_role != "principal":
        raise HTTPException(403, "only a principal can define a household recurrence")
    runtime = _runtime(request)
    event_type = (
        "recurrence.added"
        if body.expected_last_event_id is None
        else "recurrence.updated"
    )
    fingerprint = _member_command_fingerprint("recurrence", recurrence_id, body)
    prior = await _prior_member_command(
        runtime,
        session,
        event_type=event_type,
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    conn = runtime.projections.connection("recurrences")
    row = get_recurrence(conn, session, recurrence_id=recurrence_id)
    if prior is not None:
        return {
            "event_id": prior["event_id"],
            "duplicate": True,
            "recurrence": row,
        }
    if event_type == "recurrence.added":
        if row is not None:
            raise HTTPException(
                409, "recurrence already exists; refresh before editing"
            )
        linked_kind = "household" if body.scope == "household" else "party"
        linked_id = (
            "household" if linked_kind == "household" else session.auth_member_id
        )
        payload: dict[str, Any] = {
            "recurrence_id": recurrence_id,
            "linked_kind": linked_kind,
            "linked_id": linked_id,
            "kind": body.kind,
            "rrule": body.rrule,
            "next_occurrence": body.next_occurrence,
            "lead_time_days": body.lead_time_days,
            "trackable": body.trackable,
            "notes": body.notes,
        }
        owner_scope = (
            "shared:household"
            if linked_kind == "household"
            else f"private:{session.auth_member_id}"
        )
        visibility_scope = owner_scope
        sensitivity: Sensitivity = "normal"
        causation_id = None
    else:
        if row is None:
            raise HTTPException(404, "recurrence not found")
        owned = (
            row["linked_kind"] == "party" and row["linked_id"] == session.auth_member_id
        ) or (row["linked_kind"] == "household" and session.auth_role == "principal")
        if not owned:
            raise HTTPException(404, "recurrence not found")
        row_scope = "household" if row["linked_kind"] == "household" else "member"
        if body.scope != row_scope:
            raise HTTPException(409, "editing cannot move a recurrence between scopes")
        if row.get("status") == "ended":
            raise HTTPException(409, "an ended recurrence cannot be edited")
        if row["last_event_id"] != body.expected_last_event_id:
            raise HTTPException(409, "recurrence changed; refresh before editing")
        payload = {
            "recurrence_id": recurrence_id,
            "updated_at": EventEnvelope.now_utc_iso(),
            "field_updates": {
                "kind": body.kind,
                "rrule": body.rrule,
                "next_occurrence": body.next_occurrence,
                "lead_time_days": body.lead_time_days,
                "trackable": body.trackable,
                "notes": body.notes,
            },
        }
        owner_scope = row["owner_scope"]
        visibility_scope = row["visibility_scope"]
        sensitivity = cast(Sensitivity, row.get("sensitivity") or "normal")
        causation_id = row["last_event_id"]
    await _guard_member_write(runtime, session, event_type, payload)
    event = _member_command_event(
        session,
        event_type=event_type,
        payload=payload,
        request_id=body.request_id,
        fingerprint=fingerprint,
        causation_id=causation_id,
        owner_scope=owner_scope,
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,
    )
    try:
        event_id = await runtime.events.write(event)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior recurrence definition"
        ) from exc
    await runtime.wait_for_projection("recurrences", event_id)
    return {
        "event_id": event_id,
        "duplicate": False,
        "recurrence": get_recurrence(conn, session, recurrence_id=recurrence_id),
    }


@router.put("/api/member/v1/routines/{routine_id}")
async def define_routine(
    routine_id: str,
    body: RoutineDefinitionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(
            403, "routine changes are unavailable while viewing another member"
        )
    runtime = _runtime(request)
    payload = {
        "routine_id": routine_id,
        "member_id": session.auth_member_id,
        "title": body.title,
        "timezone": body.timezone,
        "starts_on": body.starts_on,
        "rrule": body.rrule,
        "window_start_local": body.window_start_local,
        "window_end_local": body.window_end_local,
        "steps": [step.model_dump(mode="json") for step in body.steps],
    }
    try:
        RoutineDefinedV1.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            422, f"invalid routine definition: {exc.errors()[0]['msg']}"
        ) from exc
    fingerprint = _member_command_fingerprint("routine", routine_id, body)
    prior = await _prior_member_command(
        runtime,
        session,
        event_type="routine.defined",
        request_id=body.request_id,
        fingerprint=fingerprint,
    )
    conn = runtime.projections.connection("recurrences")
    row = get_routine(conn, session, routine_id=routine_id)
    if prior is not None:
        return {"event_id": prior["event_id"], "duplicate": True, "routine": row}
    if body.expected_last_event_id is None:
        if row is not None:
            raise HTTPException(409, "routine already exists; refresh before editing")
        causation_id = None
        owner_scope = f"private:{session.auth_member_id}"
        visibility_scope = owner_scope
        sensitivity: Sensitivity = "normal"
    else:
        if row is None or row.get("member_id") != session.auth_member_id:
            raise HTTPException(404, "routine not found")
        if row.get("status") == "ended":
            raise HTTPException(409, "an ended routine cannot be edited")
        if row["last_event_id"] != body.expected_last_event_id:
            raise HTTPException(409, "routine changed; refresh before editing")
        causation_id = row["last_event_id"]
        owner_scope = row["owner_scope"]
        visibility_scope = row["visibility_scope"]
        sensitivity = cast(Sensitivity, row.get("sensitivity") or "normal")
    await _guard_member_write(runtime, session, "routine.defined", payload)
    event = _member_command_event(
        session,
        event_type="routine.defined",
        payload=payload,
        request_id=body.request_id,
        fingerprint=fingerprint,
        causation_id=causation_id,
        owner_scope=owner_scope,
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,
    )
    try:
        event_id = await runtime.events.write(event)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior routine definition"
        ) from exc
    await runtime.wait_for_projection("recurrences", event_id)
    return {
        "event_id": event_id,
        "duplicate": False,
        "routine": get_routine(conn, session, routine_id=routine_id),
    }


class FinanceChoiceRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    book_id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_.:-]+$")
    publication_id: str = Field(
        min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    expected_event_id: str | None = Field(default=None, min_length=1, max_length=160)


class FinanceGoalChoiceRequest(FinanceChoiceRequest):
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["reserve", "purchase", "education", "retirement", "support", "debt"]
    status: Literal["selected", "paused", "completed"] = "selected"
    target_minor: int | None = Field(default=None, strict=True, ge=0, le=10**15)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    target_date: date | None = None
    priority: Literal["unranked", "essential", "preferred", "optional"] = "unranked"


class FinancePreferenceChoiceRequest(FinanceChoiceRequest):
    selected_option_id: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$"
    )


def _finance_choice_owner(runtime: Any, session: Session) -> None:
    require_principal(session)
    if (
        session.is_view_as
        or session.service_context is not None
        or runtime.settings.member_roles.get(session.auth_member_id) != "principal"
        or not runtime.settings.finance_publications_enabled
    ):
        raise HTTPException(403, "finance_explicit_owner_choice_required")


@router.get("/api/member/v1/finance/choices")
async def finance_choices(
    request: Request,
    session: SessionDep,
    book_id: str | None = Query(default=None, max_length=96),
    offset: int = Query(default=0, ge=0, le=1000),
) -> dict[str, Any]:
    from adminme.finance.choices import list_choices
    from adminme.finance.queries import publication_status

    runtime = _runtime(request)
    _finance_choice_owner(runtime, session)
    conn = runtime.projections.connection("money")
    try:
        publication = publication_status(conn, session, book_id)
    except ValueError:
        return {"state": "book_selection_required", "can_record": False, "items": []}
    if not publication.get("publication_id"):
        return {
            "state": "accepted_publication_required",
            "can_record": False,
            "items": [],
        }
    return {
        "state": "available",
        "can_record": True,
        "publication": {
            key: publication[key] for key in ("book_id", "publication_id", "as_of")
        },
        **list_choices(conn, session, publication["book_id"], offset),
    }


async def _save_finance_choice(
    request: Request,
    session: Session,
    body: FinanceChoiceRequest,
    choice_id: str,
    kind: str,
) -> dict[str, Any]:
    from adminme.finance.choices import current_choice, validate_choice

    runtime = _runtime(request)
    _finance_choice_owner(runtime, session)
    if (
        not choice_id
        or len(choice_id) > 160
        or any(
            c
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
            for c in choice_id
        )
    ):
        raise HTTPException(422, "invalid_finance_choice_id")
    event_type = (
        "finance.goal_recorded" if kind == "goal" else "finance.preference_recorded"
    )
    key = f"finance-choice:{session.tenant_id}:{session.auth_member_id}:{body.book_id}:{kind}:{choice_id}"
    async with runtime.settings_command_locks.setdefault(key, asyncio.Lock()):
        fingerprint = _member_command_fingerprint(kind, choice_id, body)
        prior_command = await _prior_member_command(
            runtime,
            session,
            event_type=event_type,
            request_id=body.request_id,
            fingerprint=fingerprint,
        )
        conn = runtime.projections.connection("money")
        if prior_command is not None:
            await runtime.wait_for_projection("money", prior_command["event_id"])
            _finance_choice_owner(runtime, session)
            return {
                "event_id": prior_command["event_id"],
                "duplicate": True,
                "choice": current_choice(conn, session, body.book_id, kind, choice_id),
                "provider_effect_count": 0,
            }
        current = current_choice(conn, session, body.book_id, kind, choice_id)
        expected = current["event_id"] if current else None
        if body.expected_event_id != expected:
            raise HTTPException(409, "finance_choice_changed")
        now = EventEnvelope.now_utc_iso()
        scope = {
            "book_id": body.book_id,
            "owner_member_id": session.auth_member_id,
            "applicable_adult_ids": [session.auth_member_id],
            "audience_member_ids": [session.auth_member_id],
            "policy_revision": "explicit-owner-choice-v1",
            "baseline_publication_id": body.publication_id,
        }
        payload: dict[str, Any] = {"scope": scope, "recorded_at": now}
        if isinstance(body, FinanceGoalChoiceRequest):
            payload.update(
                {
                    key: value
                    for key, value in body.model_dump(mode="json").items()
                    if key
                    not in {
                        "request_id",
                        "book_id",
                        "publication_id",
                        "expected_event_id",
                    }
                }
            )
            payload.update(
                goal_id=choice_id,
                revision=current["record"]["revision"] + 1 if current else 1,
                supersedes_event_id=expected,
                selected_by_member_id=session.auth_member_id,
            )
        elif isinstance(body, FinancePreferenceChoiceRequest):
            payload.update(
                preference_id=body.request_id,
                chosen_by_member_id=session.auth_member_id,
                dimension=choice_id,
                selected_option_id=body.selected_option_id,
            )
        private = f"private:{session.auth_member_id}"
        event = _member_command_event(
            session,
            event_type=event_type,
            payload=payload,
            request_id=body.request_id,
            fingerprint=fingerprint,
            causation_id=expected,
            owner_scope=private,
            visibility_scope=private,
            sensitivity="privileged",
        )
        try:
            payload = validate_choice(conn, event.model_dump(mode="json"))
        except ValueError:
            raise HTTPException(
                422, "finance_choice_or_accepted_baseline_invalid"
            ) from None
        event = event.model_copy(update={"payload": payload})
        await _guard_member_write(
            runtime, session, event_type, {"operation": "record_private_finance_choice"}
        )
        _finance_choice_owner(runtime, session)
        try:
            event_id = await runtime.events.write(event)
        except IdempotencyConflict:
            raise HTTPException(409, "finance_choice_request_conflict") from None
        await runtime.wait_for_projection("money", event_id)
        _finance_choice_owner(runtime, session)
        return {
            "event_id": event_id,
            "duplicate": False,
            "choice": current_choice(conn, session, body.book_id, kind, choice_id),
            "provider_effect_count": 0,
        }


@router.post("/api/member/v1/finance/choices/goals/{goal_id}")
async def record_finance_goal_choice(
    goal_id: str, body: FinanceGoalChoiceRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    return await _save_finance_choice(request, session, body, goal_id, "goal")


@router.post("/api/member/v1/finance/choices/preferences/{dimension}")
async def record_finance_preference_choice(
    dimension: str,
    body: FinancePreferenceChoiceRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    return await _save_finance_choice(request, session, body, dimension, "preference")


@router.post("/api/member/v1/finance/goals/{goal_id}/lifecycle")
async def finance_goal_lifecycle(
    goal_id: str,
    body: FinanceGoalLifecycleRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "Finance changes are unavailable in view-as mode")
    runtime = _runtime(request)
    lock = runtime.today_command_locks.setdefault(
        f"{session.tenant_id}:finance-goal:{goal_id}", asyncio.Lock()
    )
    async with lock:
        event_type = {
            "pause": "finance.goal_paused",
            "complete": "finance.goal_completed",
            "reopen": "finance.goal_reopened",
        }[body.action]
        fingerprint = _member_command_fingerprint("finance_goal", goal_id, body)
        prior = await _prior_member_command(
            runtime,
            session,
            event_type=event_type,
            request_id=body.request_id,
            fingerprint=fingerprint,
        )
        row = get_finance_goal(
            runtime.projections.connection("money"), session, goal_id=goal_id
        )
        if row is None:
            raise HTTPException(404, "finance goal not found")
        if prior is not None:
            return {"event_id": prior["event_id"], "duplicate": True, "goal": row}
        if row["last_event_id"] != body.expected_last_event_id:
            raise HTTPException(409, "finance goal changed; refresh before changing it")
        status = str(row.get("status") or "active")
        if body.action in {"pause", "complete"} and status != "active":
            raise HTTPException(409, "only active finance goals can be changed")
        if body.action == "reopen" and status not in {"paused", "complete"}:
            raise HTTPException(409, "only paused or completed goals can be reopened")
        now = EventEnvelope.now_utc_iso()
        payload: dict[str, Any] = {"goal_id": goal_id}
        if body.action == "pause":
            payload.update(
                paused_at=now,
                paused_by_member_id=session.auth_member_id,
                reason=body.reason,
            )
        elif body.action == "complete":
            payload.update(
                completed_at=now,
                completed_by_member_id=session.auth_member_id,
                note=body.reason,
            )
        else:
            payload.update(
                reopened_at=now,
                reopened_by_member_id=session.auth_member_id,
                previous_status=status,
            )
        await _guard_member_write(runtime, session, event_type, payload)
        event = _member_command_event(
            session,
            event_type=event_type,
            payload=payload,
            request_id=body.request_id,
            fingerprint=fingerprint,
            causation_id=row["last_event_id"],
            owner_scope=row["owner_scope"],
            visibility_scope=row["visibility_scope"],
            sensitivity=cast(Sensitivity, row.get("sensitivity") or "privileged"),
        )
        try:
            event_id = await runtime.events.write(event)
        except IdempotencyConflict as exc:
            raise HTTPException(
                409, "idempotency key conflicts with prior finance-goal action"
            ) from exc
        await runtime.wait_for_projection("money", event_id)
        return {
            "event_id": event_id,
            "duplicate": False,
            "goal": get_finance_goal(
                runtime.projections.connection("money"), session, goal_id=goal_id
            ),
        }


@router.post("/api/member/v1/calendar/{calendar_event_id}/outcome")
async def record_calendar_outcome(
    calendar_event_id: str,
    body: CalendarOutcomeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.today_command_locks.setdefault(
        f"{session.tenant_id}:calendar:{calendar_event_id}", asyncio.Lock()
    )
    async with lock:
        return await _record_calendar_outcome(calendar_event_id, body, request, session)


@router.post(
    "/api/member/v1/routines/{routine_id}/occurrences/{occurrence_date}"
    "/steps/{step_id}/outcome"
)
async def record_routine_step_outcome(
    routine_id: str,
    occurrence_date: str,
    step_id: str,
    body: RoutineStepOutcomeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    runtime = _runtime(request)
    lock = runtime.today_command_locks.setdefault(
        f"{session.tenant_id}:routine:{routine_id}:{occurrence_date}", asyncio.Lock()
    )
    async with lock:
        return await _record_routine_step_outcome(
            routine_id,
            occurrence_date,
            step_id,
            body,
            request,
            session,
        )


@router.post("/api/member/v1/temporary-surfaces", status_code=201)
async def create_temporary_surface(
    body: CreateTemporarySurface, request: Request, session: SessionDep
) -> dict[str, Any]:
    if (
        session.auth_role in {"child", "device", "ambient"}
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(403, "adult owner authority required")
    try:
        spec = _runtime(request).temporary_surfaces.create(session.auth_member_id, body)
    except TemporarySurfaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    runtime = _runtime(request)
    event_id = await _audit_surface(runtime, session, spec, "created")
    activation_event_id = await _audit_surface(runtime, session, spec, "activated")
    return {
        "surface": spec.public(),
        "audit_event_id": event_id,
        "activation_event_id": activation_event_id,
    }


@router.get("/api/member/v1/temporary-surfaces/{stable_id}")
async def get_temporary_surface(
    stable_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    return {"surface": _visible_spec(runtime, session, stable_id).public()}


@router.get("/api/member/v1/temporary-surfaces/{stable_id}/evaluate")
async def evaluate_temporary_surface(
    stable_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    await _expire_due(runtime, session)
    spec = _visible_spec(runtime, session, stable_id)
    missing = [
        name
        for name in spec.required_capabilities
        if runtime.capabilities.get(name) is None
        or runtime.capabilities[name].state not in {"healthy", "observation_only"}
    ]
    if missing:
        return {
            "surface": {"stable_id": stable_id, "title": spec.title},
            "state": "degraded",
            "missing_capabilities": missing,
            "results": [],
        }
    results = [
        {
            "query": query["name"],
            "value": evaluate_query(
                runtime, session, query["name"], query.get("params", {})
            ),
        }
        for query in spec.queries
    ]
    return {
        "surface": {
            "stable_id": stable_id,
            "title": spec.title,
            "purpose": spec.purpose,
        },
        "mode": experience_mode(runtime, session),
        "state": "ready",
        "results": results,
        "actions": list(spec.action_verbs),
        "evaluated_fresh": True,
    }


@router.post("/api/member/v1/temporary-surfaces/{stable_id}/promote")
async def promote_temporary_surface(
    stable_id: str, body: PromotionRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    spec = runtime.temporary_surfaces.get(stable_id)
    if (
        spec is None
        or spec.status != "active"
        or spec.owner_member_id != session.auth_member_id
    ):
        raise HTTPException(404, "temporary surface not found")
    if (
        session.auth_role != "principal"
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(403, "adult review required")
    known = set(runtime.settings.member_roles)
    audience = set(body.audience_member_ids)
    if not audience or audience - known:
        raise HTTPException(422, "audience contains an unknown member")
    if any(
        runtime.settings.member_roles[item] in {"device", "ambient"}
        for item in audience
    ):
        raise HTTPException(422, "shared-screen audience is not supported")
    children = {
        item for item in audience if runtime.settings.member_roles[item] == "child"
    }
    if children and (
        "child" not in spec.compatible_modes
        or {query["name"] for query in spec.queries} - CHILD_QUERIES
        or set(spec.action_verbs) - CHILD_ACTIONS
    ):
        raise HTTPException(422, "child audience requires a guardian-safe view")
    try:
        promoted = runtime.temporary_surfaces.promote(
            stable_id,
            actor_member_id=session.auth_member_id,
            audience_member_ids=tuple(audience),
        )
    except TemporarySurfaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    event_id = await _audit_surface(runtime, session, promoted, "promoted")
    return {"surface": promoted.public(), "audit_event_id": event_id}


@router.post("/api/member/v1/temporary-surfaces/{stable_id}/archive")
async def archive_temporary_surface(
    stable_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    try:
        spec = runtime.temporary_surfaces.transition(
            stable_id, actor_member_id=session.auth_member_id, status="archived"
        )
    except TemporarySurfaceError as exc:
        raise HTTPException(404, "temporary surface not found") from exc
    event_id = await _audit_surface(runtime, session, spec, "archived")
    return {"archived": True, "audit_event_id": event_id}


@router.post("/api/member/v1/temporary-surfaces/{stable_id}/actions")
async def temporary_surface_action(
    stable_id: str, body: TemporaryActionRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    runtime = _runtime(request)
    spec = _visible_spec(runtime, session, stable_id)
    if body.verb not in spec.action_verbs:
        raise HTTPException(403, "action is not declared by this surface")
    result = await _execute_lifecycle(
        MemberLifecycleRequest(
            item_kind="task",
            item_id=body.item_id,
            action=cast(
                Literal["complete", "defer", "snooze", "block", "unblock"],
                body.verb.removeprefix("task."),
            ),
            expected_last_event_id=body.expected_last_event_id,
            request_id=body.request_id,
            until=body.until,
            reason=body.reason,
            note=body.note,
        ),
        request,
        session,
    )
    return {**result, "next_action": result.get("what_now")}


class AttentionRulePreviewRequest(AttentionRuleV2):
    pass


class AttentionRuleSaveRequest(AttentionRuleV2):
    request_id: str = Field(min_length=8, max_length=120)
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["active", "trial", "shadow"] = "trial"
    trial_days: int = Field(default=7, ge=1, le=30)
    trial_message_limit: int = Field(default=50, ge=1, le=100)


class AttentionRuleReviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    decision: Literal["retain", "suspend", "revoke"]
    negative_outcome: (
        Literal[
            "false_suppression",
            "missed_obligation",
            "member_correction",
            "scope_mismatch",
        ]
        | None
    ) = None
    reason: str = Field(min_length=1, max_length=500)


class AttentionFeedbackRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    proposal_id: str = Field(min_length=1, max_length=240)
    kind: Literal[
        "future_attention_preference",
        "interpretation_correction",
        "completion",
        "ownership",
        "duplicate",
    ]
    rule: AttentionRuleV2 | None = None


def _attention_rule_body(body: AttentionRuleV2) -> dict[str, Any]:
    return AttentionRuleV2.model_validate(
        body.model_dump(include=set(AttentionRuleV2.model_fields))
    ).model_dump(mode="json", exclude_none=True)


def _attention_request_fingerprint(proposal_id: str, body: BaseModel) -> str:
    payload = body.model_dump(mode="json")
    # An omitted V2 boundary must not invalidate retries of V1 requests.
    for rule in (payload, payload.get("rule")):
        if not isinstance(rule, dict):
            continue
        for terms in [rule.get("conditions", {}), *rule.get("exceptions", [])]:
            if terms.get("protected_source_id") is None:
                terms.pop("protected_source_id", None)
    canonical = json.dumps(
        {"proposal_id": proposal_id, "body": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _attention_event_version(payload: dict[str, Any]) -> int:
    rule = payload.get("rule") if "rule" in payload else payload
    if not isinstance(rule, dict):
        return 1
    return (
        2
        if any(
            terms.get("protected_source_id") is not None
            for terms in [rule.get("conditions", {}), *rule.get("exceptions", [])]
        )
        else 1
    )


def _attention_connection(runtime: Any) -> Any:
    if runtime.projections is None:
        raise HTTPException(503, "attention rule projection unavailable")
    return runtime.projections.connection("triage")


def _attention_rules(runtime: Any, session: Session) -> list[dict[str, Any]]:
    conn = _attention_connection(runtime)
    rows = conn.execute(
        "SELECT * FROM triage_attention_rules WHERE tenant_id=? AND member_id=? ORDER BY starts_at DESC,rule_id",
        (session.tenant_id, session.auth_member_id),
    ).fetchall()
    rules = []
    for row in rows:
        rule = dict(row)
        for key in ("conditions", "exceptions", "feedback_event_ids"):
            rule[key] = json.loads(rule.pop(f"{key}_json"))
        rule["conditions"] = {
            key: value for key, value in rule["conditions"].items() if value is not None
        }
        rule["exceptions"] = [
            {key: value for key, value in terms.items() if value is not None}
            for terms in rule["exceptions"]
        ]
        if rule["status"] in {"trial", "shadow"} and attention_rule_expired(
            rule, EventEnvelope.now_utc_iso()
        ):
            rule["status"] = "expired"
        rule["history"] = [
            dict(value)
            for value in conn.execute(
                """SELECT event_id,decision,reason,occurred_at FROM triage_attention_rule_history
               WHERE tenant_id=? AND rule_id=? ORDER BY event_id""",
                (session.tenant_id, rule["rule_id"]),
            ).fetchall()
        ]
        rule["provider_effects"] = 0
        rule["reversible"] = True
        rules.append(rule)
    return rules


async def _attention_preview(
    runtime: Any,
    session: Session,
    body: AttentionRuleV2,
) -> dict[str, Any]:
    conn = _attention_connection(runtime)
    rule = _attention_rule_body(body)
    rows = conn.execute(
        """SELECT * FROM triage_items WHERE tenant_id=? AND owner_member_id=?
           AND owner_scope=? AND visibility_scope=? ORDER BY proposed_at DESC,proposal_id LIMIT 500""",
        (
            session.tenant_id,
            session.auth_member_id,
            f"private:{session.auth_member_id}",
            f"private:{session.auth_member_id}",
        ),
    ).fetchall()
    items = [
        dict(value) for value in rows if filter_one(session, dict(value)) is not None
    ]
    evidence_ids = set(body.feedback_event_ids)
    if body.origin == "suggested":
        evidence = conn.execute(
            """SELECT * FROM triage_attention_feedback WHERE tenant_id=? AND member_id=?
               AND kind='future_attention_preference' ORDER BY recorded_at DESC""",
            (session.tenant_id, session.auth_member_id),
        ).fetchall()
        verified = []
        for value in evidence:
            if value["last_event_id"] not in evidence_ids:
                continue
            prior_rule = AttentionRuleV2.model_validate(
                json.loads(value["rule_json"] or "{}")
            ).model_dump(mode="json", exclude_none=True)
            if all(
                prior_rule.get(key) == rule[key]
                for key in ("conditions", "exceptions", "action")
            ):
                verified.append(value)
        if (
            set(str(value["last_event_id"]) for value in verified) != evidence_ids
            or len({value["observation_key"] for value in verified}) < 3
        ):
            raise HTTPException(
                422,
                "a suggested rule requires three independent matching future-preference corrections",
            )
    elif evidence_ids:
        raise HTTPException(
            422, "explicit rules do not borrow inferred feedback evidence"
        )
    source_only: list[dict[str, Any]] = []
    seen_sources = {str(item["source_event_id"]) for item in items}
    if runtime.event_log is not None:
        async for event in runtime.event_log.read_since(
            owner_scope=f"private:{session.auth_member_id}",
            types=source_policy_event_types(runtime.settings.repository_root),
        ):
            if (
                event.get("tenant_id") != session.tenant_id
                or filter_one(session, event) is None
            ):
                continue
            if str(event["event_id"]) in seen_sources:
                continue
            try:
                coordinate = source_coordinate_for_event(
                    event, provider_bindings=runtime.settings.provider_bindings
                )
            except (TypeError, ValueError):
                continue
            if (
                coordinate.provider_account_id != body.conditions.provider_account_id
                or coordinate.adapter_instance_id != body.conditions.adapter_instance_id
            ):
                continue
            source_only.append(
                {
                    "source_event_id": event["event_id"],
                    "last_event_id": event["event_id"],
                    "title": "Source without an assessed candidate",
                    "state": "reference",
                    "reconciliation": {"scope": coordinate.model_dump(mode="json")},
                    "assessment_unavailable": True,
                }
            )
            source_only = source_only[-250:]
    return {
        **attention_rule_preview(
            rule,
            items,
            source_only=source_only,
            as_of=EventEnvelope.now_utc_iso(),
            existing_rules=_attention_rules(runtime, session),
        ),
        "activation_available": runtime.settings.human_triage_management_enabled,
    }


@router.post("/api/member/v1/triage-learning/v2/preview")
async def preview_attention_rule(
    body: AttentionRulePreviewRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    return await _attention_preview(_runtime(request), session, body)


@router.get("/api/member/v1/triage-learning/v2/rules")
async def list_attention_rules(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    groups: dict[str, dict[str, Any]] = {}
    for row in (
        _attention_connection(runtime)
        .execute(
            """SELECT * FROM triage_attention_feedback WHERE tenant_id=? AND member_id=?
           AND kind='future_attention_preference' ORDER BY recorded_at DESC LIMIT 500""",
            (session.tenant_id, session.auth_member_id),
        )
        .fetchall()
    ):
        rule = AttentionRuleV2.model_validate(
            json.loads(row["rule_json"] or "{}")
        ).model_dump(mode="json", exclude_none=True)
        terms = {key: rule.get(key) for key in ("conditions", "exceptions", "action")}
        key = json.dumps(terms, sort_keys=True, separators=(",", ":"))
        group = groups.setdefault(key, {"rule": rule, "observations": {}})
        group["observations"].setdefault(row["observation_key"], row["last_event_id"])
    suggestions = []
    for group in groups.values():
        if len(group["observations"]) < 3:
            continue
        suggestions.append(
            {
                "rule": {
                    **group["rule"],
                    "origin": "suggested",
                    "feedback_event_ids": sorted(group["observations"].values())[:100],
                },
                "independent_observations": len(group["observations"]),
                "self_activates": False,
            }
        )
    return {
        "rules": _attention_rules(runtime, session),
        "suggestions": suggestions,
        "active": runtime.settings.human_triage_management_enabled,
        "provider_effects": 0,
    }


@router.post("/api/member/v1/triage-learning/v2/rules")
async def save_attention_rule(
    body: AttentionRuleSaveRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    _require_human_triage_management(runtime)
    fingerprint = _attention_request_fingerprint("attention-rule", body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.attention_rule.saved",
        request_id=body.request_id,
        request_fingerprint=fingerprint,
    )
    if prior is not None:
        await runtime.wait_for_projection("triage", prior["event_id"])
        return {
            "event_id": prior["event_id"],
            "rule_id": prior["payload"]["rule_id"],
            "duplicate": True,
            "provider_effects": 0,
        }
    preview = await _attention_preview(runtime, session, body)
    if preview["preview_digest"] != body.preview_digest:
        raise HTTPException(409, "observations changed; preview the rule again")
    now = datetime.now(timezone.utc)
    rule_id = (
        "attention_rule_"
        + hashlib.sha256(
            f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}".encode()
        ).hexdigest()[:28]
    )
    payload = {
        **_attention_rule_body(body),
        "rule_id": rule_id,
        "member_id": session.auth_member_id,
        "preview_digest": body.preview_digest,
        "mode": body.mode,
        "starts_at": now.isoformat(),
        "expires_at": (now + timedelta(days=body.trial_days)).isoformat()
        if body.mode != "active"
        else None,
        "max_observations": body.trial_message_limit if body.mode != "active" else None,
        "saved_by_member_id": session.auth_member_id,
        "access_widening": False,
        "cross_member_effect": False,
        "provider_effects": False,
    }
    await _guard_triage_write(runtime, session, "triage.attention_rule.saved", payload)
    event_id = await runtime.events.write(
        _triage_event(
            session,
            event_type="triage.attention_rule.saved",
            schema_version=_attention_event_version(payload),
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            causation_id=None,
        )
    )
    await runtime.wait_for_projection("triage", event_id)
    return {
        "event_id": event_id,
        "rule_id": rule_id,
        "state": body.mode,
        "duplicate": False,
        "provider_effects": 0,
    }


@router.post("/api/member/v1/triage-learning/v2/rules/{rule_id}/review")
async def review_attention_rule(
    rule_id: str,
    body: AttentionRuleReviewRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    _require_human_triage_management(runtime)
    fingerprint = _triage_request_fingerprint(rule_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.attention_rule.reviewed",
        request_id=body.request_id,
        request_fingerprint=fingerprint,
    )
    if prior is not None:
        return {
            "event_id": prior["event_id"],
            "rule_id": rule_id,
            "duplicate": True,
            "provider_effects": 0,
        }
    rule = next(
        (
            value
            for value in _attention_rules(runtime, session)
            if value["rule_id"] == rule_id
        ),
        None,
    )
    if rule is None:
        raise HTTPException(404, "attention rule not found")
    if rule["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "rule changed; refresh before reviewing")
    if body.decision == "retain" and (
        body.negative_outcome or rule["status"] in {"suspended", "revoked"}
    ):
        raise HTTPException(
            422, "a suspended rule or adverse evidence requires a new preview"
        )
    payload = {
        **body.model_dump(mode="json", exclude={"request_id"}),
        "rule_id": rule_id,
        "member_id": session.auth_member_id,
        "reviewed_by_member_id": session.auth_member_id,
        "reviewed_at": EventEnvelope.now_utc_iso(),
        "provider_effects": False,
    }
    await _guard_triage_write(
        runtime, session, "triage.attention_rule.reviewed", payload
    )
    event_id = await runtime.events.write(
        _triage_event(
            session,
            event_type="triage.attention_rule.reviewed",
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            causation_id=body.expected_last_event_id,
        )
    )
    await runtime.wait_for_projection("triage", event_id)
    return {
        "event_id": event_id,
        "rule_id": rule_id,
        "state": body.decision,
        "provider_effects": 0,
    }


@router.post("/api/member/v1/triage-learning/v2/feedback")
async def record_attention_feedback(
    body: AttentionFeedbackRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_self_triage_policy(session)
    runtime = _runtime(request)
    _require_human_triage_management(runtime)
    fingerprint = _attention_request_fingerprint(body.proposal_id, body)
    prior = await _prior_triage_command(
        runtime,
        session,
        event_type="triage.attention_feedback.recorded",
        request_id=body.request_id,
        request_fingerprint=fingerprint,
    )
    if prior is not None:
        return {"event_id": prior["event_id"], "duplicate": True, "provider_effects": 0}
    item = _owned_triage_item(runtime, session, body.proposal_id)
    if item["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "candidate changed; refresh before correcting")
    if (body.kind == "future_attention_preference") != (body.rule is not None):
        raise HTTPException(422, "only an explicit future preference supplies a rule")
    if (
        body.rule is not None
        and attention_match(_attention_rule_body(body.rule), item) != "match"
    ):
        raise HTTPException(
            422, "the example must match the rule and its protected exceptions"
        )
    observation = item
    if item.get("duplicate_of_proposal_id"):
        original = get_triage_item(
            _attention_connection(runtime),
            session,
            proposal_id=item["duplicate_of_proposal_id"],
        )
        if (
            original is not None
            and original["owner_member_id"] == session.auth_member_id
        ):
            observation = original
    payload = {
        "feedback_id": "attention_feedback_"
        + hashlib.sha256(
            f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}".encode()
        ).hexdigest()[:28],
        "member_id": session.auth_member_id,
        "proposal_id": body.proposal_id,
        "source_event_id": item["source_event_id"],
        "independent_observation_key": independent_observation_key(observation),
        "kind": body.kind,
        "rule": _attention_rule_body(body.rule) if body.rule else None,
        "recorded_by_member_id": session.auth_member_id,
        "recorded_at": EventEnvelope.now_utc_iso(),
        "provider_effects": False,
    }
    await _guard_triage_write(
        runtime, session, "triage.attention_feedback.recorded", payload
    )
    event_id = await runtime.events.write(
        _triage_event(
            session,
            event_type="triage.attention_feedback.recorded",
            schema_version=_attention_event_version(payload),
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            causation_id=item["last_event_id"],
        )
    )
    await runtime.wait_for_projection("triage", event_id)
    return {
        "event_id": event_id,
        "kind": body.kind,
        "future_rule_activated": False,
        "provider_effects": 0,
    }


__all__ = ["router"]


@router.get("/api/member/v1/finance/planning")
async def finance_planning(
    request: Request,
    session: SessionDep,
    book_id: str = Query(min_length=1, max_length=80),
    projection_id: str | None = Query(default=None, max_length=80),
    context_id: str | None = Query(default=None, max_length=80),
    report_id: Literal[
        "planning_contexts",
        "planning_projection",
        "planning_events",
        "planning_gaps",
        "planning_issues",
        "planning_evidence",
        "planning_sources",
        "planning_engines",
    ] = "planning_contexts",
    reference_id: str | None = Query(default=None, min_length=1, max_length=80),
    horizon_days: int = Query(default=30, ge=7, le=365),
    gap_group_id: str | None = Query(default=None, min_length=1, max_length=80),
    offset: int = Query(default=0, ge=0, le=10000),
) -> dict[str, Any]:
    from adminme.finance.planning_store import query_projection

    runtime = _runtime(request)
    _finance_choice_owner(runtime, session)
    try:
        result = await runtime.projections.read(
            "money",
            lambda conn: query_projection(
                conn,
                session,
                book_id=book_id,
                projection_id=projection_id,
                context_id=context_id,
                report_id=report_id,
                horizon_days=horizon_days,
                offset=offset,
                limit=50,
                reference_id=reference_id,
                gap_group_id=gap_group_id,
            ),
        )
    except ValueError:
        raise HTTPException(422, "finance_planning_selection_invalid") from None
    if session_from_request(request) != session:
        raise HTTPException(403, "finance_planning_identity_changed")
    _finance_choice_owner(runtime, session)
    return result
