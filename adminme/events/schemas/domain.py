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
        "privileged_input",
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
    due_date: str | None = None
    available_from: str | None = None
    energy: Literal["low", "medium", "high"] | None = None
    effort_min: int | None = Field(default=None, ge=0)
    effort: Literal["tiny", "small", "medium", "large"] | None = None
    domain: str | None = None
    item_type: (
        Literal[
            "task",
            "purchase",
            "appointment",
            "research",
            "decision",
            "chore",
            "maintenance",
        ]
        | None
    ) = None
    micro_script: str | None = None
    notes: str | None = None
    created_by: str | None = None
    source_system: str | None = None
    source_commitment_id: str | None = None


class TaskCompletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    completed_by_member_id: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    note: str | None = None


class TaskDeferredV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    deferred_at: str = Field(min_length=1)
    deferred_until: str = Field(min_length=1)
    deferred_by_member_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)


class TaskSnoozedV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    snoozed_at: str = Field(min_length=1)
    snoozed_until: str = Field(min_length=1)
    snoozed_by_member_id: str = Field(min_length=1)


class TaskBlockedV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    blocked_at: str = Field(min_length=1)
    blocked_by_member_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


class TaskUnblockedV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    unblocked_at: str = Field(min_length=1)
    unblocked_by_member_id: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)


class TaskCancelledV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    cancelled_at: str = Field(min_length=1)
    cancelled_by_member_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)


class TaskReopenedV1(BaseModel):
    model_config = {"extra": "forbid"}
    task_id: str = Field(min_length=1)
    reopened_at: str = Field(min_length=1)
    reopened_by_member_id: str = Field(min_length=1)
    previous_status: Literal["done", "cancelled", "dismissed"]


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


class CommitmentDeferredV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    deferred_at: str = Field(min_length=1)
    deferred_until: str = Field(min_length=1)
    deferred_by_party_id: str = Field(min_length=1)
    reason: str | None = None


class CommitmentBlockedV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    blocked_at: str = Field(min_length=1)
    blocked_by_party_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


class CommitmentUnblockedV1(BaseModel):
    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    unblocked_at: str = Field(min_length=1)
    unblocked_by_party_id: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)


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
    """Emitted after an OpenClaw tick drives the fixed 14-day proposal sweep."""

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


class RecurrencePausedV1(BaseModel):
    model_config = {"extra": "forbid"}
    recurrence_id: str = Field(min_length=1)
    paused_at: str = Field(min_length=1)
    paused_by_member_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)


class RecurrenceResumedV1(BaseModel):
    model_config = {"extra": "forbid"}
    recurrence_id: str = Field(min_length=1)
    resumed_at: str = Field(min_length=1)
    resumed_by_member_id: str = Field(min_length=1)


class RecurrenceEndedV1(BaseModel):
    model_config = {"extra": "forbid"}
    recurrence_id: str = Field(min_length=1)
    ended_at: str = Field(min_length=1)
    ended_by_member_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)


class RewardReadyV1(BaseModel):
    """Reward toast contract emitted by ``reward_dispatch`` reactive
    pipeline on ``task.completed`` / ``commitment.completed``.

    Canonical event name per [BUILD.md §1620, CONSOLE_PATTERNS.md §8 —
    supersedes §1210 typo `adminme.reward.dispatched`]; the console's
    SSE layer consumes by this name. Registered at v1 per [D7].

    Either ``triggering_task_id`` or ``triggering_commitment_id`` is set
    (whichever event triggered the dispatch); the other is None."""

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1)
    tier: Literal["done", "warm", "delight", "jackpot"]
    template_id: str = Field(min_length=1)
    template_text: str = Field(min_length=1)
    triggering_task_id: str | None = None
    triggering_commitment_id: str | None = None


class DigestComposedV1(BaseModel):
    """Morning-digest contract emitted by ``morning_digest`` proactive
    pipeline once per scheduled run per member.

    Per [BUILD.md §1289] validation-guard discipline: ``body_text``
    is the composed digest text or the sentinel "No morning brief
    available; underlying data changed." when the validation guard
    rejects the composition (every claimed calendar event /
    commitment / task id verified against projections post-compose;
    any fabrication zeroes the message). ``validation_failed=True``
    on the sentinel path; ``delivered=False`` in that case (no
    outbound() call). Registered at v1 per [D7]."""

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1)
    body_text: str
    profile_format: Literal["fog_aware", "compressed", "carousel", "child", "none"]
    validation_failed: bool
    delivered: bool
    today_iso: str = Field(min_length=10)


class ParalysisTriggeredV1(BaseModel):
    """Paralysis-detection contract emitted by the
    ``paralysis_detection`` proactive pipeline when all
    pre-conditions hold and a deterministic template is selected.

    Per [BUILD.md §1297-1302]: NEVER invokes an LLM; template comes
    from persona's ``paralysis_templates.yaml``; surface is
    Today/activity + optional outbound (the optional-outbound branch is
    deferred to a future per-profile-config-aware prompt — v1 emits
    the event only). Registered at v1 per [D7]."""

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    template_text: str = Field(min_length=1)
    triggered_at: str = Field(min_length=10)


class ReminderDispatchedV1(BaseModel):
    """Reminder-dispatch contract emitted by ``reminder_dispatch``
    proactive pipeline per due commitment within the lookahead window.

    Per [§1] 10c-iii grounding the sentinel-path discipline mirrors
    ``DigestComposedV1``: skill defensive-default leaves
    ``composed_text=""``, ``validation_failed=True``,
    ``delivered=False`` (no ``outbound()`` call). ``composed_text``
    clamped to SMS-friendly length per [BUILD.md §L4 reminder_dispatch].
    Registered at v1 per [D7]."""

    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    owner_member_id: str = Field(min_length=1)
    recipient_party_id: str = Field(min_length=1)
    composed_text: str = Field(max_length=280)
    channel: Literal["imessage", "sms", "email", "push"]
    due_at: datetime
    delivered: bool
    validation_failed: bool
    scheduled_send_at: datetime | None = None


class CrmSurfacedV1(BaseModel):
    """CRM-surface contract emitted by ``crm_surface`` proactive
    pipeline per opt-in member with stale-contact / upcoming-birthday /
    hosting-imbalance signals in the weekly scan.

    Per [§1] 10c-iii grounding ``gap_kind`` discriminates the three
    sub-use-cases stub prose described; ``surfaced_party_ids`` is the
    clamped list (at most ``max_surfaced_per_week``, default 5) of
    parties carrying the signal. Sentinel path mirrors
    ``DigestComposedV1``: skill failure leaves ``composed_text=""``,
    ``validation_failed=True``, ``delivered=False``. Registered at v1
    per [D7]."""

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1)
    surfaced_party_ids: list[str] = Field(min_length=1)
    gap_kind: Literal["stale_contact", "upcoming_birthday", "hosting_imbalance"]
    composed_text: str = Field(max_length=1000)
    channel: Literal["imessage", "sms", "email", "push"]
    week_of_date: str = Field(min_length=1)
    delivered: bool
    validation_failed: bool


class CustodyBriefComposedV1(BaseModel):
    """Custody-brief contract emitted by ``custody_brief`` proactive
    pipeline once per day per parent member with a coparent
    relationship set.

    Per [§1] 10c-iii grounding long-form daily brief carries handoff
    timing, allergen / med notes, next-day calendar. Sentinel path
    mirrors ``DigestComposedV1``: skill failure leaves
    ``composed_text=""``, ``validation_failed=True``,
    ``delivered=False``. Registered at v1 per [D7]."""

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1)
    coparent_member_id: str = Field(min_length=1)
    child_member_ids: list[str] = Field(min_length=1)
    composed_text: str = Field(max_length=1500)
    channel: Literal["imessage", "sms", "email", "push"]
    brief_date: str = Field(min_length=1)
    delivered: bool
    validation_failed: bool


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
registry.register("commitment.deferred", 1, CommitmentDeferredV1)
registry.register("commitment.blocked", 1, CommitmentBlockedV1)
registry.register("commitment.unblocked", 1, CommitmentUnblockedV1)
registry.register("commitment.cancelled", 1, CommitmentCancelledV1)
registry.register("commitment.delegated", 1, CommitmentDelegatedV1)
registry.register("commitment.expired", 1, CommitmentExpiredV1)
registry.register("task.created", 1, TaskCreatedV1)
registry.register("task.completed", 1, TaskCompletedV1)
registry.register("task.deferred", 1, TaskDeferredV1)
registry.register("task.snoozed", 1, TaskSnoozedV1)
registry.register("task.blocked", 1, TaskBlockedV1)
registry.register("task.unblocked", 1, TaskUnblockedV1)
registry.register("task.cancelled", 1, TaskCancelledV1)
registry.register("task.reopened", 1, TaskReopenedV1)
registry.register("task.updated", 1, TaskUpdatedV1)
registry.register("task.deleted", 1, TaskDeletedV1)
registry.register("recurrence.added", 1, RecurrenceAddedV1)
registry.register("recurrence.completed", 1, RecurrenceCompletedV1)
registry.register("recurrence.updated", 1, RecurrenceUpdatedV1)
registry.register("recurrence.paused", 1, RecurrencePausedV1)
registry.register("recurrence.resumed", 1, RecurrenceResumedV1)
registry.register("recurrence.ended", 1, RecurrenceEndedV1)
registry.register("reward.ready", 1, RewardReadyV1)
registry.register("digest.composed", 1, DigestComposedV1)
registry.register("paralysis.triggered", 1, ParalysisTriggeredV1)
registry.register("reminder.dispatched", 1, ReminderDispatchedV1)
registry.register("crm.surfaced", 1, CrmSurfacedV1)
registry.register("custody_brief.composed", 1, CustodyBriefComposedV1)
registry.register("skill.call.recorded", 2, SkillCallRecordedV2)
