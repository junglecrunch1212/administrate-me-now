"""Content-free operator requests for exact failed forwarded submissions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class SubmissionRecoveryRequestedV1(BaseModel):
    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1, max_length=240)
    failed_assessment_event_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    expected_failure_reason: Literal[
        "language_seam_OpenClawTimeout", "language_seam_OpenClawUnreachable"
    ]
    request_id: str = Field(min_length=8, max_length=120)
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    recovery_policy: Literal["member-forwarded-submission-recovery-v1"]
    attempt_limit: Literal[3]
    recovery_attempt: int = Field(ge=1, le=3)
    requested_at: datetime


class SubmissionRecoveryRefusedV1(BaseModel):
    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1, max_length=240)
    request_event_id: str = Field(min_length=1, max_length=240)
    reason: str = Field(pattern=r"^[a-z_]+$", max_length=120)
    refused_at: datetime


registry.register("triage.intent.recovery_requested", 1, SubmissionRecoveryRequestedV1)
registry.register("triage.intent.recovery_refused", 1, SubmissionRecoveryRefusedV1)
