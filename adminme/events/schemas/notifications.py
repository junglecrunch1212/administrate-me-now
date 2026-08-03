"""Typed notification intent, policy decision, and delivery outcome events."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from adminme.events.registry import registry


def _openclaw_channel() -> list[Literal["openclaw"]]:
    return ["openclaw"]


class NotificationSignalRaisedV1(BaseModel):
    model_config = {"extra": "forbid"}

    signal_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    category: Literal["reminder", "digest", "relationship", "reward", "system"]
    urgency: Literal["low", "normal", "high", "critical"] = "normal"
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=4000)
    preferred_channels: list[Literal["openclaw"]] = Field(
        default_factory=_openclaw_channel, min_length=1, max_length=1
    )
    requires_approval: bool = False
    not_before: str | None = None
    expires_at: str | None = None
    related_kind: str | None = Field(default=None, max_length=120)
    related_id: str | None = Field(default=None, max_length=240)

    @field_validator("not_before", "expires_at")
    @classmethod
    def valid_timestamp(cls, value: str | None) -> str | None:
        if value is not None:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class NotificationDecisionMadeV1(BaseModel):
    model_config = {"extra": "forbid"}

    decision_id: str = Field(min_length=1, max_length=240)
    signal_id: str = Field(min_length=1, max_length=240)
    decision: Literal["route", "suppress", "hold", "retry"]
    route: Literal["openclaw"] | None = None
    reasons: list[str] = Field(min_length=1)
    approval_required: bool
    approval_granted: bool
    decided_at: str = Field(min_length=1)
    retry_at: str | None = None

    @model_validator(mode="after")
    def route_matches_decision(self) -> "NotificationDecisionMadeV1":
        if self.decision == "route" and self.route != "openclaw":
            raise ValueError("routed notifications must delegate to OpenClaw")
        if self.decision != "route" and self.route is not None:
            raise ValueError("only route decisions may declare a route")
        if self.decision == "retry" and self.retry_at is None:
            raise ValueError("retry decisions require retry_at")
        return self


class NotificationDeliveryResultV1(BaseModel):
    model_config = {"extra": "forbid"}

    attempt_id: str = Field(min_length=1, max_length=240)
    decision_id: str = Field(min_length=1, max_length=240)
    signal_id: str = Field(min_length=1, max_length=240)
    outcome: Literal["delivered", "failed", "retrying", "suppressed", "held"]
    route: Literal["openclaw"]
    attempted_at: str = Field(min_length=1)
    provider_receipt_id: str | None = Field(default=None, max_length=500)
    error_code: str | None = Field(default=None, max_length=120)
    next_retry_at: str | None = None

    @model_validator(mode="after")
    def retry_has_time(self) -> "NotificationDeliveryResultV1":
        if self.outcome == "retrying" and self.next_retry_at is None:
            raise ValueError("retrying delivery results require next_retry_at")
        return self


registry.register("notification.signal.raised", 1, NotificationSignalRaisedV1)
registry.register("notification.decision.made", 1, NotificationDecisionMadeV1)
registry.register("notification.delivery.result", 1, NotificationDeliveryResultV1)


__all__ = [
    "NotificationDecisionMadeV1",
    "NotificationDeliveryResultV1",
    "NotificationSignalRaisedV1",
]
