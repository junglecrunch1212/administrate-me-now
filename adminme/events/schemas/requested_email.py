"""Exact CoS email instructions retain their own policy-authority provenance."""

from typing import Any, Literal

from pydantic import Field, model_validator

from adminme.events.registry import registry
from adminme.events.schemas.conversation_responses import CoSResponseApprovedV4
from adminme.events.schemas.cos_responses import CoSResponseApprovedV1


class CoSResponseApprovedV5(CoSResponseApprovedV1):
    authorization_method: Literal[
        "exact",
        "standing_self_reply",
        "requested_self_reminder",
        "authenticated_conversation",
        "requested_email",
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
    action_id: str | None = Field(default=None, min_length=1, max_length=240)
    request_source_event_id: str | None = Field(default=None, min_length=1)
    request_authorization_event_id: str | None = Field(default=None, min_length=1)
    request_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request_action: dict[str, Any] | None = None

    @model_validator(mode="after")
    def authority_contract(self) -> "CoSResponseApprovedV5":
        requested_fields = {
            "action_id",
            "request_source_event_id",
            "request_authorization_event_id",
            "request_fingerprint",
            "request_action",
        }
        if self.authorization_method != "requested_email":
            if any(getattr(self, name) is not None for name in requested_fields):
                raise ValueError(
                    "legacy response cannot borrow current-request authority"
                )
            CoSResponseApprovedV4.model_validate(
                self.model_dump(exclude=requested_fields)
            )
            return self
        if self.channel != "email" or not all(
            getattr(self, name) for name in requested_fields
        ):
            raise ValueError("requested email needs its exact action and instruction")
        if any(
            (
                self.standing_authorization_event_id,
                self.source_message_event_id,
                self.self_reply_binding,
                self.reply_index,
                self.reminder_event_id,
                self.reminder_binding_event_id,
                self.conversation_source_event_id,
                self.thread_id,
                self.in_reply_to,
            )
        ):
            raise ValueError("requested email cannot borrow another response authority")
        return self


registry.register("cos.response.approved", 5, CoSResponseApprovedV5)
registry.register_upcaster("cos.response.approved", 4, lambda payload: dict(payload))
