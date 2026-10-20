"""Encrypted exact-response approvals and content-free terminal receipts."""

from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class CoSResponseApprovedV1(BaseModel):
    model_config = {"extra": "forbid"}
    contract: Literal["cos-response-plain-text-v1"]
    member_id: str = Field(min_length=1, max_length=200)
    channel: Literal["email", "imessage"]
    service_channel_id: str = Field(min_length=1, max_length=240)
    provider_account_id: str = Field(min_length=1, max_length=320)
    recipient: str = Field(min_length=1, max_length=320)
    subject: str = Field(max_length=300)
    body: str = Field(min_length=1, max_length=8000)
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_event_id: str = Field(min_length=1, max_length=240)
    policy_revision: int = Field(ge=1)
    approved_at: str
    expires_at: str


class CoSResponseAttemptedV1(BaseModel):
    model_config = {"extra": "forbid"}
    contract: Literal["cos-response-plain-text-v1"]
    member_id: str = Field(min_length=1, max_length=200)
    approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: str = Field(min_length=1, max_length=240)
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_event_id: str = Field(min_length=1, max_length=240)
    attempted_at: str
    automatic_retry_allowed: Literal[False] = False


class CoSResponseResultV1(BaseModel):
    model_config = {"extra": "forbid"}
    contract: Literal["cos-response-plain-text-v1"]
    member_id: str = Field(min_length=1, max_length=200)
    approval_event_id: str = Field(min_length=1, max_length=240)
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["sent", "suppressed", "denied", "unknown"]
    reason: str = Field(min_length=1, max_length=120)
    provider_effect_count: Literal[0, 1] | None
    automatic_retry_allowed: Literal[False] = False
    completed_at: str


registry.register("cos.response.approved", 1, CoSResponseApprovedV1)
registry.register("cos.response.attempted", 1, CoSResponseAttemptedV1)
registry.register("cos.response.result", 1, CoSResponseResultV1)
