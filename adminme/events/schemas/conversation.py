"""Authenticated human instruction receipts, separate from conversation memory."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from adminme.events.registry import registry


class ConversationInstructionV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=32000)
    submitted_at: AwareDatetime
    channel_id: Literal["administrateme-portal", "gmail"]
    account_id: str = Field(min_length=1, max_length=500)
    actor_id: str = Field(min_length=1, max_length=240)
    agent_id: str = Field(min_length=1, max_length=120)
    session_key: str = Field(min_length=1, max_length=500)
    message_id: str = Field(min_length=1, max_length=240)
    thread_id: str | None = Field(default=None, min_length=1, max_length=240)
    source_message_event_id: str | None = Field(
        default=None, min_length=1, max_length=240
    )
    request_id: str = Field(min_length=8, max_length=120)
    # The immutable receipt is evidence of the request, not blanket authority.
    # Each action engine must independently evaluate its actual requested scope.
    authentication_rule: Literal[
        "authenticated_member_session", "enrolled_receiver_authenticated_sender"
    ]


registry.register("cos.conversation.instruction", 1, ConversationInstructionV1)


class ConversationActivationV1(BaseModel):
    model_config = {"extra": "forbid"}
    activated_at: AwareDatetime
    channel: Literal["gmail"]
    rule: Literal["new_authenticated_instructions_only"]


class ConversationDeliveryV1(BaseModel):
    model_config = {"extra": "forbid"}
    instruction_event_id: str = Field(min_length=1, max_length=240)
    state: Literal[
        "awaiting_live", "running", "answered", "unknown", "expired", "failed"
    ]
    reason: str = Field(min_length=1, max_length=120)
    recorded_at: AwareDatetime


registry.register("cos.conversation.activation", 1, ConversationActivationV1)
registry.register("cos.conversation.delivery", 1, ConversationDeliveryV1)
