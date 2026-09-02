"""Minimum typed household coordination without private source evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.events.registry import registry


CoordinationKind = Literal[
    "household_obligation",
    "household_deadline",
    "handoff",
    "busy_availability",
    "joint_schedule",
    "resource_conflict",
]
CoordinationFieldName = Literal[
    "label",
    "deadline_at",
    "start_at",
    "end_at",
    "resource_id",
    "constraint_kind",
    "availability",
]


class CoordinationFieldV1(BaseModel):
    model_config = {"extra": "forbid"}

    name: CoordinationFieldName
    value: str = Field(min_length=1, max_length=500)
    audience_member_ids: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_value(self) -> CoordinationFieldV1:
        if len(self.audience_member_ids) != len(set(self.audience_member_ids)):
            raise ValueError("coordination field audience must be unique")
        if self.name in {"deadline_at", "start_at", "end_at"}:
            parsed = datetime.fromisoformat(self.value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("coordination times must include a timezone")
        if self.name == "availability" and self.value != "busy":
            raise ValueError("shared availability is busy-only")
        return self


class CoordinationCreatedV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    coordination_kind: CoordinationKind
    originator_member_id: str = Field(min_length=1, max_length=200)
    participant_member_ids: list[str] = Field(min_length=1, max_length=20)
    obligation_owner_kind: Literal["member", "household", "none"]
    obligation_owner_id: str | None = Field(default=None, min_length=1, max_length=200)
    responsible_party_id: str = Field(min_length=1, max_length=200)
    beneficiary_party_id: str | None = Field(default=None, min_length=1, max_length=200)
    linked_task_id: str | None = Field(default=None, min_length=1, max_length=200)
    linked_commitment_id: str | None = Field(default=None, min_length=1, max_length=200)
    cos_assignment_task_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    fields: list[CoordinationFieldV1] = Field(default_factory=list, max_length=20)
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    private_evidence_included: Literal[False] = False
    provider_projection_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_coordination(self) -> CoordinationCreatedV1:
        participants = self.participant_member_ids
        if len(participants) != len(set(participants)):
            raise ValueError("coordination participants must be unique")
        if self.originator_member_id not in participants:
            raise ValueError("originator must be a coordination participant")
        if self.obligation_owner_kind == "none":
            if self.obligation_owner_id is not None:
                raise ValueError("non-obligation coordination cannot name an owner")
        elif not self.obligation_owner_id:
            raise ValueError("coordination obligation requires an owner")
        if self.obligation_owner_kind == "member" and (
            self.obligation_owner_id not in participants
        ):
            raise ValueError("member obligation owner must participate")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("coordination fields must be unique")
        for field in self.fields:
            if not set(field.audience_member_ids).issubset(participants):
                raise ValueError("field audience must be a participant subset")
        values = {field.name: field.value for field in self.fields}
        if self.coordination_kind == "busy_availability":
            if set(values) != {"start_at", "end_at", "availability"}:
                raise ValueError("busy availability exposes time and busy state only")
        if self.coordination_kind in {"joint_schedule", "resource_conflict"}:
            if not {"start_at", "end_at", "resource_id"}.issubset(values):
                raise ValueError(
                    "joint/resource coordination requires time and resource"
                )
        if "start_at" in values and "end_at" in values:
            start = datetime.fromisoformat(values["start_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(values["end_at"].replace("Z", "+00:00"))
            if end <= start:
                raise ValueError("coordination interval must end after it starts")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("coordination expiry must follow creation")
        return self


class CoordinationOriginatorReceiptRecordedV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    originator_member_id: str = Field(min_length=1, max_length=200)
    receipt_status: Literal["recorded"] = "recorded"
    recorded_at: AwareDatetime
    private_evidence_included: Literal[False] = False


class CoordinationHandoffProposedV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    proposed_by_member_id: str = Field(min_length=1, max_length=200)
    destination_member_id: str = Field(min_length=1, max_length=200)
    proposed_at: AwareDatetime
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_expiry(self) -> CoordinationHandoffProposedV1:
        if self.expires_at is not None and self.expires_at <= self.proposed_at:
            raise ValueError("handoff expiry must follow its proposal")
        return self


class CoordinationHandoffDecidedV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    destination_member_id: str = Field(min_length=1, max_length=200)
    decision: Literal["accepted", "rejected"]
    decided_at: AwareDatetime
    reason_code: Literal["capacity", "ownership", "timing", "other"] | None = None


class CoordinationHandoffWithdrawnV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    withdrawn_by_member_id: str = Field(min_length=1, max_length=200)
    withdrawn_at: AwareDatetime


class CoordinationHandoffReassignedV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    reassigned_by_member_id: str = Field(min_length=1, max_length=200)
    previous_destination_member_id: str | None = Field(default=None, min_length=1)
    destination_member_id: str = Field(min_length=1, max_length=200)
    reassigned_at: AwareDatetime
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_expiry(self) -> CoordinationHandoffReassignedV1:
        if self.expires_at is not None and self.expires_at <= self.reassigned_at:
            raise ValueError("handoff expiry must follow its reassignment")
        return self


class CoordinationWaitingSetV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    waiting_on_member_id: str = Field(min_length=1, max_length=200)
    set_by_party_id: str = Field(min_length=1, max_length=200)
    waiting_since: AwareDatetime
    follow_up_at: AwareDatetime | None = None


class CoordinationWaitingClearedV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    cleared_by_party_id: str = Field(min_length=1, max_length=200)
    cleared_at: AwareDatetime


class CoordinationConversationLinkedV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    member_id: str = Field(min_length=1, max_length=200)
    conversation_reference: str = Field(min_length=1, max_length=240)
    linked_at: AwareDatetime
    transcript_included: Literal[False] = False


class CoordinationRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}

    coordination_id: str = Field(min_length=1, max_length=200)
    revoked_by_member_id: str = Field(min_length=1, max_length=200)
    revoked_at: AwareDatetime
    reason_code: Literal["withdrawn", "resolved_elsewhere", "privacy", "other"]


registry.register("coordination.created", 1, CoordinationCreatedV1)
registry.register(
    "coordination.originator_receipt_recorded",
    1,
    CoordinationOriginatorReceiptRecordedV1,
)
registry.register("coordination.handoff_proposed", 1, CoordinationHandoffProposedV1)
registry.register("coordination.handoff_decided", 1, CoordinationHandoffDecidedV1)
registry.register("coordination.handoff_withdrawn", 1, CoordinationHandoffWithdrawnV1)
registry.register("coordination.handoff_reassigned", 1, CoordinationHandoffReassignedV1)
registry.register("coordination.waiting_set", 1, CoordinationWaitingSetV1)
registry.register("coordination.waiting_cleared", 1, CoordinationWaitingClearedV1)
registry.register(
    "coordination.conversation_linked", 1, CoordinationConversationLinkedV1
)
registry.register("coordination.revoked", 1, CoordinationRevokedV1)


__all__ = [
    "CoordinationConversationLinkedV1",
    "CoordinationCreatedV1",
    "CoordinationFieldName",
    "CoordinationFieldV1",
    "CoordinationHandoffDecidedV1",
    "CoordinationHandoffProposedV1",
    "CoordinationHandoffReassignedV1",
    "CoordinationHandoffWithdrawnV1",
    "CoordinationKind",
    "CoordinationOriginatorReceiptRecordedV1",
    "CoordinationRevokedV1",
    "CoordinationWaitingClearedV1",
    "CoordinationWaitingSetV1",
]
