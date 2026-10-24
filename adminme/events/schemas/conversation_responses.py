"""Authenticated conversation replies preserve exact origin and independent policy."""

from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator
from adminme.events.registry import registry
from adminme.events.schemas.cos_reminders import CoSResponseApprovedV3
from adminme.events.schemas.cos_responses import CoSResponseApprovedV1


class CoSResponseApprovedV4(CoSResponseApprovedV1):
    authorization_method: Literal[
        "exact",
        "standing_self_reply",
        "requested_self_reminder",
        "authenticated_conversation",
    ] = "exact"
    standing_authorization_event_id: str | None = None
    source_message_event_id: str | None = None
    self_reply_binding: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reply_index: int = Field(default=0, ge=0, le=15)
    reminder_event_id: str | None = None
    reminder_binding_event_id: str | None = None
    conversation_source_event_id: str | None = None
    thread_id: str | None = None
    in_reply_to: str | None = None

    @model_validator(mode="after")
    def authority_contract(self) -> "CoSResponseApprovedV4":
        if self.authorization_method == "authenticated_conversation":
            if (
                self.channel != "email"
                or not self.conversation_source_event_id
                or not self.thread_id
                or not self.in_reply_to
                or any(
                    (
                        self.standing_authorization_event_id,
                        self.source_message_event_id,
                        self.self_reply_binding,
                        self.reply_index,
                        self.reminder_event_id,
                        self.reminder_binding_event_id,
                    )
                )
            ):
                raise ValueError("conversation reply requires its exact email origin")
        else:
            if any(
                (self.conversation_source_event_id, self.thread_id, self.in_reply_to)
            ):
                raise ValueError("other replies cannot borrow conversation authority")
            CoSResponseApprovedV3.model_validate(
                self.model_dump(
                    exclude={"conversation_source_event_id", "thread_id", "in_reply_to"}
                )
            )
        return self


class ConversationReplyAuthorizedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str
    approval_event_id: str
    source_event_id: str
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorized_at: str
    host_decision: dict[str, Any]
    automatic_retry_allowed: Literal[False] = False


registry.register("cos.response.approved", 4, CoSResponseApprovedV4)
registry.register_upcaster("cos.response.approved", 3, lambda payload: dict(payload))
registry.register("cos.conversation.reply_authorized", 1, ConversationReplyAuthorizedV1)
