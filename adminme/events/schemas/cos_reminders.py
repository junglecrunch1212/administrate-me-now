"""Durable requested self reminders; OpenClaw owns their timers."""

from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator
from adminme.events.registry import registry
from adminme.events.schemas.cos_responses import (
    CoSResponseApprovedV1,
    CoSResponseApprovedV2,
)


class CoSSelfReminderRequestedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str
    source_message_event_id: str
    self_reply_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    activation_event_id: str
    provenance: dict[str, Any]
    body: str = Field(min_length=1, max_length=3500)
    due_at: str
    requested_at: str


class CoSSelfReminderBoundV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str
    reminder_event_id: str
    job_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,120}$")


class CoSSelfReminderCancelledV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str
    reminder_event_id: str
    cancelled_at: str


class CoSResponseApprovedV3(CoSResponseApprovedV1):
    authorization_method: Literal[
        "exact", "standing_self_reply", "requested_self_reminder"
    ] = "exact"
    standing_authorization_event_id: str | None = None
    source_message_event_id: str | None = None
    self_reply_binding: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reply_index: int = Field(default=0, ge=0, le=15)
    reminder_event_id: str | None = None
    reminder_binding_event_id: str | None = None

    @model_validator(mode="after")
    def authority_contract(self) -> "CoSResponseApprovedV3":
        data = self.model_dump(
            exclude={"reminder_event_id", "reminder_binding_event_id"}
        )
        if self.authorization_method == "requested_self_reminder":
            if (
                not self.reminder_event_id
                or not self.reminder_binding_event_id
                or self.reply_index
            ):
                raise ValueError(
                    "requested reminder requires its exact durable request and timer"
                )
            data["authorization_method"] = "standing_self_reply"
        elif self.reminder_event_id or self.reminder_binding_event_id:
            raise ValueError("other replies cannot borrow reminder authority")
        CoSResponseApprovedV2.model_validate(data)
        return self


class CoSReminderHostAuthorizedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str
    approval_event_id: str
    standing_authorization_event_id: str
    source_message_event_id: str
    self_reply_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    reminder_event_id: str
    reminder_binding_event_id: str
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorized_at: str
    authorization_method: Literal["requested_self_reminder"] = "requested_self_reminder"
    automatic_retry_allowed: Literal[False] = False


registry.register("cos.reply.reminder_requested", 1, CoSSelfReminderRequestedV1)
registry.register("cos.reply.reminder_bound", 1, CoSSelfReminderBoundV1)
registry.register("cos.reply.reminder_cancelled", 1, CoSSelfReminderCancelledV1)
registry.register("cos.reply.reminder_authorized", 1, CoSReminderHostAuthorizedV1)
registry.register("cos.response.approved", 3, CoSResponseApprovedV3)
registry.register_upcaster(
    "cos.response.approved",
    2,
    lambda payload: {
        **payload,
        "reminder_event_id": None,
        "reminder_binding_event_id": None,
    },
)
