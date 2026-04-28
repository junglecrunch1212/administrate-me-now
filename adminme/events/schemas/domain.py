"""
Domain spine schemas — commitments, tasks, skill calls.

Per SYSTEM_INVARIANTS.md §4 (commitments + tasks) and §7 item 5 (skill calls).
`commitment.proposed` v1 models the reference example at
ADMINISTRATEME_REFERENCE_EXAMPLES.md §5 — provenance (pipeline, skills,
source_event_id) lives in the envelope (source_adapter / source_account_id /
causation_id), not in the payload.

`skill.call.recorded` v2 is the first version AdministrateMe emits. v1 is
reserved (pre-OpenClaw era events that do not exist in this log); no v1
model is registered. ``registry.latest_version("skill.call.recorded")``
returns 2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class CommitmentProposedV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    kind: Literal[
        "reply", "task", "appointment", "payment", "document_return", "visit", "other"
    ]
    owed_by_member_id: str = Field(min_length=1)
    owed_to_party_id: str = Field(min_length=1)
    text_summary: str = Field(min_length=1, max_length=500)
    suggested_due: datetime | None = None
    urgency: Literal["today", "this_week", "this_month", "no_rush"] = "this_week"
    confidence: float = Field(ge=0.0, le=1.0)
    strength: Literal["confident", "weak"]
    source_interaction_id: str | None = None
    source_message_preview: str | None = Field(default=None, max_length=240)
    classify_reasons: list[str] = Field(default_factory=list)


class CommitmentSuppressedV1(BaseModel):
    """Emitted by ``commitment_extraction`` (and future thank_you_detection)
    when a candidate inbound message does NOT result in a
    ``commitment.proposed``. The audit trail makes silent drops debuggable
    per [REFERENCE_EXAMPLES.md §2 line 1024]. Registered at v1 per [D7]."""

    model_config = {"extra": "forbid"}
    reason: Literal[
        "below_confidence_threshold",
        "dedupe_hit",
        "skill_failure_defensive_default",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    source_event_id: str = Field(min_length=1)


class CommitmentConfirmedV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    confirmed_by_member_id: str = Field(min_length=1)
    confirmed_at: str = Field(min_length=1)
    note: str | None = None


class TaskCreatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    owner_member_id: str | None = None
    due: str | None = None
    energy: Literal["low", "medium", "high"] | None = None
    effort_min: int | None = Field(default=None, ge=0)
    source_commitment_id: str | None = None


class TaskCompletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    completed_by_member_id: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    note: str | None = None


class CommitmentCompletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    completed_by_party_id: str = Field(min_length=1)
    completion_note: str | None = None


class CommitmentDismissedV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    dismissed_at: str = Field(min_length=1)
    dismissed_by_party_id: str = Field(min_length=1)
    reason: str | None = None


class CommitmentEditedV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    edited_at: str = Field(min_length=1)
    edited_by_party_id: str = Field(min_length=1)
    field_updates: dict[str, Any]


class CommitmentSnoozedV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    snoozed_at: str = Field(min_length=1)
    snoozed_until: str = Field(min_length=1)
    snoozed_by_party_id: str = Field(min_length=1)


class CommitmentCancelledV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    cancelled_at: str = Field(min_length=1)
    cancelled_by_party_id: str = Field(min_length=1)
    reason: str | None = None


class CommitmentDelegatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    delegated_at: str = Field(min_length=1)
    delegated_by_party_id: str = Field(min_length=1)
    delegated_to_party_id: str = Field(min_length=1)


class CommitmentExpiredV1(BaseModel):
    """Emitted by prompt-10c's timer pipeline — 14-day stale-proposal sweep.
    Prompt 06 only registers the type; no pipeline emits it yet."""

    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    expired_at: str = Field(min_length=1)


class TaskUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    updated_by_party_id: str | None = None
    previous_status: str | None = None
    new_status: str | None = None
    field_updates: dict[str, Any]


class TaskDeletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    deleted_at: str = Field(min_length=1)
    deleted_by_party_id: str = Field(min_length=1)


class RecurrenceAddedV1(BaseModel):
    model_config = {"extra": "forbid"}
    recurrence_id: str = Field(min_length=1)
    linked_kind: Literal["party", "asset", "account", "household"]
    linked_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    rrule: str = Field(min_length=1)
    next_occurrence: str = Field(min_length=1)
    lead_time_days: int = Field(default=0, ge=0)
    trackable: bool = False
    notes: str | None = None


class RecurrenceCompletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    recurrence_id: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    completed_by_party_id: str | None = None
    occurrence_date: str | None = None


class RecurrenceUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    recurrence_id: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    field_updates: dict[str, Any]


class SkillCallRecordedV2(BaseModel):
    """First version actually emitted. v1 is reserved — see module docstring.

    Token / cost fields are Optional per [ADR-0002] graceful-degradation
    clause: when OpenClaw's `/tools/invoke` response envelope omits these
    fields, the wrapper records `None` rather than skipping the event."""

    model_config = {"extra": "forbid"}
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    openclaw_invocation_id: str | None = None
    inputs: dict
    outputs: dict
    provider: str = Field(min_length=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    duration_ms: int = Field(ge=0)


registry.register("commitment.proposed", 1, CommitmentProposedV1)
registry.register("commitment.suppressed", 1, CommitmentSuppressedV1)
registry.register("commitment.confirmed", 1, CommitmentConfirmedV1)
registry.register("commitment.completed", 1, CommitmentCompletedV1)
registry.register("commitment.dismissed", 1, CommitmentDismissedV1)
registry.register("commitment.edited", 1, CommitmentEditedV1)
registry.register("commitment.snoozed", 1, CommitmentSnoozedV1)
registry.register("commitment.cancelled", 1, CommitmentCancelledV1)
registry.register("commitment.delegated", 1, CommitmentDelegatedV1)
registry.register("commitment.expired", 1, CommitmentExpiredV1)
registry.register("task.created", 1, TaskCreatedV1)
registry.register("task.completed", 1, TaskCompletedV1)
registry.register("task.updated", 1, TaskUpdatedV1)
registry.register("task.deleted", 1, TaskDeletedV1)
registry.register("recurrence.added", 1, RecurrenceAddedV1)
registry.register("recurrence.completed", 1, RecurrenceCompletedV1)
registry.register("recurrence.updated", 1, RecurrenceUpdatedV1)
registry.register("skill.call.recorded", 2, SkillCallRecordedV2)
