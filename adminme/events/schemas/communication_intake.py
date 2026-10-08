"""Content-free cutover and terminal policy receipts for member submissions."""

from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class CommunicationIntakePolicyAppliedV1(BaseModel):
    model_config = {"extra": "forbid"}
    policy_id: Literal["explicit-member-submission-v1"]
    previous_source_watermark: str | None = Field(default=None, max_length=240)
    automatic_personal_obligations_enabled: Literal[False] = False


class CommunicationSourceSkippedV1(BaseModel):
    model_config = {"extra": "forbid"}
    policy_id: Literal["explicit-member-submission-v1"]
    source_event_id: str = Field(min_length=1, max_length=240)
    reason: Literal["explicit_member_submission_required"]
    disposition: Literal["not_selected"]
    language_called: Literal[False]
    candidate_count: Literal[0]


class CommunicationCommandRetiredV1(BaseModel):
    model_config = {"extra": "forbid"}
    policy_id: Literal["explicit-member-submission-v1"]
    command_event_id: str = Field(min_length=1, max_length=240)
    command_type: Literal[
        "source.assessment.recovery_requested",
        "source.assessment.revision_requested",
        "source.currentness.revision_requested",
        "triage.source_publication.planned",
        "triage.source_revision_publication.planned",
        "triage.currentness_publication.planned",
    ]
    reason: Literal["explicit_member_submission_required"]
    language_called: Literal[False]
    candidate_count: Literal[0]


registry.register(
    "communication_intake.policy_applied", 1, CommunicationIntakePolicyAppliedV1
)
registry.register(
    "communication_intake.source_skipped", 1, CommunicationSourceSkippedV1
)
registry.register(
    "communication_intake.command_retired", 1, CommunicationCommandRetiredV1
)
