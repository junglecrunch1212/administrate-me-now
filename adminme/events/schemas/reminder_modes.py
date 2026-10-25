"""A closed reminder-only clarification, never scheduled-email authority."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.events.registry import registry
from adminme.events.schemas.reminder_choices import (
    ReminderChoiceDraftV1,
    ReminderChoiceContinuationBase,
    ReminderChoiceGroundedBase,
)


class ReminderModeOrigin(BaseModel):
    model_config = {"extra": "forbid"}
    channel_id: Literal["imessage", "apple_imessage", "gmail", "administrateme-portal"]
    account_id: str = Field(min_length=1, max_length=500)
    actor_id: str = Field(min_length=1, max_length=500)
    session_key: str = Field(min_length=1, max_length=1000)
    thread_id: str | None = Field(default=None, max_length=500)


class ReminderModeOfferedV1(ReminderChoiceDraftV1):
    resource_owner: Literal["service-principal:chief-of-staff"]
    clarification_kind: Literal["reminder_only_or_email"] = "reminder_only_or_email"
    # Exact selected writer/action is retained; the later answer cannot change it.
    action: dict[str, object]
    origin: ReminderModeOrigin
    prompt: str = Field(min_length=1, max_length=3500)


class ReminderModePresentedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str
    task_id: str
    offered_event_id: str
    source_event_id: str
    origin: ReminderModeOrigin
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    run_id: str = Field(min_length=1, max_length=240)
    presented_at: AwareDatetime
    issued_at_ms: int
    expires_at_ms: int
    host_signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    # For transport channels this is an audited sent result, not model narration.
    response_result_event_id: str | None = None
    presentation_kind: Literal["sent_reply", "native_portal_final"]
    provider_effect_count: Literal[0] = 0

    @model_validator(mode="after")
    def actual_surface(self) -> ReminderModePresentedV1:
        portal = self.origin.channel_id == "administrateme-portal"
        if (self.presentation_kind == "native_portal_final") != portal or (
            (self.response_result_event_id is None) != portal
        ):
            raise ValueError("presentation requires its actual conversation surface")
        return self


registry.register("cos.reminder.mode_offered", 1, ReminderModeOfferedV1)
registry.register("cos.reminder.mode_presented", 1, ReminderModePresentedV1)


class ReminderModeContinuationV1(ReminderChoiceContinuationBase):
    offered: ReminderModeOfferedV1
    presentation_event_id: str = Field(min_length=1, max_length=240)
    presentation: ReminderModePresentedV1


class ReminderModeGroundedV1(ReminderChoiceGroundedBase):
    rule_id: Literal["current-human-request.cos-reminder-mode-choice.v1"]
    continuation: ReminderModeContinuationV1


registry.register("cos.reminder.mode_grounded", 1, ReminderModeGroundedV1)
