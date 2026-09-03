"""Typed lifecycle evidence for G11 Apple Messages replies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class MessagesReplyCancelledV1(BaseModel):
    model_config = {"extra": "forbid"}

    command_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    interaction_id: str = Field(min_length=1, max_length=240)
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    canceled_at: str = Field(min_length=1)
    provider_called: Literal[False] = False


class MessagesReplyObservedV1(BaseModel):
    model_config = {"extra": "forbid"}

    command_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    interaction_id: str = Field(min_length=1, max_length=240)
    command_event_id: str = Field(min_length=1, max_length=240)
    result_event_id: str = Field(min_length=1, max_length=240)
    observation_event_id: str = Field(min_length=1, max_length=240)
    native_message_id: str = Field(min_length=1, max_length=1000)
    match_basis: Literal["native_message_id", "single_exact_chat_body_participants"]
    observed_at: str = Field(min_length=1)


class MessagesReplyReconciledV1(MessagesReplyObservedV1):
    model_config = {"extra": "forbid"}

    observed_event_id: str = Field(min_length=1, max_length=240)
    outcome: Literal["reconciled"] = "reconciled"
    reconciled_at: str = Field(min_length=1)


registry.register("messaging.reply.canceled", 1, MessagesReplyCancelledV1)
registry.register("messaging.reply.observed", 1, MessagesReplyObservedV1)
registry.register("messaging.reply.reconciled", 1, MessagesReplyReconciledV1)


__all__ = [
    "MessagesReplyCancelledV1",
    "MessagesReplyObservedV1",
    "MessagesReplyReconciledV1",
]
