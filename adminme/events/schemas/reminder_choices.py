"""Durable member reminder clarification, distinct from execution authority."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.events.registry import registry


class ReminderListChoice(BaseModel):
    model_config = {"extra": "forbid"}
    list_id: str = Field(min_length=1, max_length=1000)
    label: str = Field(min_length=1, max_length=1000)


class ReminderChoiceOfferedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    action_id: str = Field(min_length=1, max_length=240)
    request_id: str = Field(min_length=8, max_length=120)
    original_source_event_id: str = Field(min_length=1, max_length=240)
    original_source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_submitted_at: AwareDatetime
    draft_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Literal["apple_reminders"]
    operation: Literal["create_reminder"]
    resource_owner: Literal["requester"]
    provider_account_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    due_at: AwareDatetime
    notes: str | None = Field(default=None, max_length=10000)
    choices: list[ReminderListChoice] = Field(min_length=1, max_length=100)
    offered_at: AwareDatetime
    pending_expires_at: AwareDatetime
    expiry_reason: Literal["requested_reminder_due_time"]
    provider_effect_count: Literal[0] = 0

    @model_validator(mode="after")
    def exact_pending_lifetime(self) -> ReminderChoiceOfferedV1:
        if not self.original_submitted_at <= self.offered_at < self.pending_expires_at:
            raise ValueError("pending choice requires an unexpired original request")
        if self.pending_expires_at != self.due_at:
            raise ValueError("pending choice expiry must remain its original due time")
        ids = [choice.list_id for choice in self.choices]
        if len(ids) != len(set(ids)):
            raise ValueError("list choices must have unique provider identities")
        return self


class ReminderChoiceFinishedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    action_id: str = Field(min_length=1, max_length=240)
    offered_event_id: str = Field(min_length=1, max_length=240)
    answer_source_event_id: str = Field(min_length=1, max_length=240)
    state: Literal["confirmed", "cancelled"]
    decided_at: AwareDatetime
    selected_list_id: str | None = Field(default=None, min_length=1, max_length=1000)
    domain_approval_event_id: str | None = Field(
        default=None, min_length=1, max_length=240
    )
    provider_effect_count: Literal[0] = 0

    @model_validator(mode="after")
    def distinct_result(self) -> ReminderChoiceFinishedV1:
        if self.state == "confirmed":
            if not self.selected_list_id or not self.domain_approval_event_id:
                raise ValueError(
                    "confirmation requires the exact list and domain receipt"
                )
        elif (
            self.selected_list_id is not None
            or self.domain_approval_event_id is not None
        ):
            raise ValueError("cancellation must not claim a provider confirmation")
        return self


registry.register("cos.reminder.choice_offered", 1, ReminderChoiceOfferedV1)
registry.register("cos.reminder.choice_finished", 1, ReminderChoiceFinishedV1)


class ReminderChoiceConversationV1(BaseModel):
    """Only a receiver-authenticated email thread disambiguates bare answers."""

    model_config = {"extra": "forbid"}
    channel_id: Literal["gmail"]
    account_id: str = Field(min_length=1, max_length=500)
    actor_id: str = Field(min_length=1, max_length=240)
    thread_id: str = Field(min_length=1, max_length=240)


class ReminderChoiceCandidateV1(BaseModel):
    model_config = {"extra": "forbid"}
    offered_event_id: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=240)
    title_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    pending_expires_at: AwareDatetime
    conversation: ReminderChoiceConversationV1 | None


class ReminderChoiceSelectionV1(BaseModel):
    """Immutable eligible tasks at the fresh answer, never model-selected context."""

    model_config = {"extra": "forbid"}
    answer_source_event_id: str = Field(min_length=1, max_length=240)
    answer_submitted_at: AwareDatetime
    answer_conversation: ReminderChoiceConversationV1 | None
    eligible_tasks: list[ReminderChoiceCandidateV1] = Field(max_length=1000)

    @model_validator(mode="after")
    def unique_live_tasks(self) -> ReminderChoiceSelectionV1:
        ids = [candidate.task_id for candidate in self.eligible_tasks]
        offers = [candidate.offered_event_id for candidate in self.eligible_tasks]
        if len(ids) != len(set(ids)) or len(offers) != len(set(offers)):
            raise ValueError("eligible tasks must have unique retained identities")
        if offers != sorted(offers) or any(
            candidate.pending_expires_at <= self.answer_submitted_at
            for candidate in self.eligible_tasks
        ):
            raise ValueError("eligible tasks must preserve the original answer cutoff")
        return self


class ReminderChoiceContinuationV1(BaseModel):
    model_config = {"extra": "forbid"}
    offered_event_id: str = Field(min_length=1, max_length=240)
    offered: ReminderChoiceOfferedV1
    original_instruction: str = Field(min_length=1, max_length=32000)
    original_evidence: list[dict[str, object]] = Field(min_length=1, max_length=8)
    selection: ReminderChoiceSelectionV1


class ReminderChoiceGroundedV1(BaseModel):
    """Fresh answer evidence augments, never rewrites, one offered assignment."""

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    action_id: str = Field(min_length=1, max_length=240)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_id: Literal["current-human-request.apple-reminder-list-choice.v1"]
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_event_id: str = Field(min_length=1, max_length=240)
    action: dict[str, object]
    evidence: list[dict[str, object]] = Field(min_length=1, max_length=8)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    continuation: ReminderChoiceContinuationV1

    @model_validator(mode="after")
    def one_task_lifetime(self) -> ReminderChoiceGroundedV1:
        from datetime import timedelta

        offered = self.continuation.offered
        if (
            offered.member_id != self.member_id
            or offered.action_id != self.action_id
            or self.source_event_id == offered.original_source_event_id
            or self.continuation.selection.answer_source_event_id
            != self.source_event_id
            or self.continuation.selection.answer_submitted_at > self.issued_at
            or not offered.offered_at <= self.issued_at < self.expires_at
            or self.expires_at > offered.pending_expires_at
            or self.expires_at - self.issued_at > timedelta(minutes=10)
        ):
            raise ValueError(
                "answer grounding must preserve one original task and expiry"
            )
        return self


registry.register("cos.reminder.choice_grounded", 1, ReminderChoiceGroundedV1)
