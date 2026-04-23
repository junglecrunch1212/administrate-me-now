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
from typing import Literal

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


class SkillCallRecordedV2(BaseModel):
    """First version actually emitted. v1 is reserved — see module docstring."""

    model_config = {"extra": "forbid"}
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    openclaw_invocation_id: str = Field(min_length=1)
    inputs: dict
    outputs: dict
    provider: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    duration_ms: int = Field(ge=0)


registry.register("commitment.proposed", 1, CommitmentProposedV1)
registry.register("commitment.confirmed", 1, CommitmentConfirmedV1)
registry.register("task.created", 1, TaskCreatedV1)
registry.register("task.completed", 1, TaskCompletedV1)
registry.register("skill.call.recorded", 2, SkillCallRecordedV2)
