"""Encrypted exact-response approvals and content-free terminal receipts."""

from typing import Literal
from typing import Any

from pydantic import BaseModel, Field, model_validator

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


class CoSResponseApprovedV2(CoSResponseApprovedV1):
    authorization_method: Literal["exact", "standing_self_reply"] = "exact"
    standing_authorization_event_id: str | None = None
    source_message_event_id: str | None = None
    self_reply_binding: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reply_index: int = Field(default=0, ge=0, le=15)

    @model_validator(mode="after")
    def authority_contract(self) -> "CoSResponseApprovedV2":
        coordinates = (
            self.standing_authorization_event_id,
            self.source_message_event_id,
            self.self_reply_binding,
        )
        if self.authorization_method == "standing_self_reply":
            if (
                not all(coordinates)
                or self.channel != "imessage"
                or self.subject
                or len(self.body.encode("utf-16-le")) // 2 > 3500
            ):
                raise ValueError(
                    "standing reply requires complete private iMessage authority"
                )
        elif any(coordinates) or self.reply_index:
            raise ValueError("exact approval cannot borrow standing reply authority")
        return self


class CoSSelfReplyHostAuthorizedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str
    approval_event_id: str
    standing_authorization_event_id: str
    source_message_event_id: str
    self_reply_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorized_at: str
    authorization_method: Literal["standing_self_reply"] = "standing_self_reply"
    automatic_retry_allowed: Literal[False] = False


def upcast_response_approval(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "authorization_method": "exact",
        "standing_authorization_event_id": None,
        "source_message_event_id": None,
        "self_reply_binding": None,
        "reply_index": 0,
    }


registry.register("cos.response.approved", 2, CoSResponseApprovedV2)
registry.register_upcaster("cos.response.approved", 1, upcast_response_approval)
registry.register("cos.reply.openclaw_authorized", 1, CoSSelfReplyHostAuthorizedV1)
