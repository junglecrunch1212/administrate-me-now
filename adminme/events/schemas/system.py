"""
System-event schemas.

System events are observability-only. They do not represent facts about the
world and do not participate in domain reasoning. They exist so downstream
observers (reverse daemons, status surfaces, diagnostics) can react to
platform-internal state transitions.

Per SYSTEM_INVARIANTS.md §2.2: projections never emit DOMAIN events. System
events are explicitly outside that scope — they emit a signal that a
regeneration cycle completed, nothing more.

Currently contains:
- xlsx.regenerated — emitted after xlsx_workbooks forward daemon writes.
- xlsx.reverse_projected — emitted by the xlsx reverse daemon after a
  successful detection cycle (07c-β).
- xlsx.reverse_skipped_during_forward — emitted by the xlsx reverse daemon
  when a detection cycle is dropped because the forward lock is held.
- skill.call.failed — emitted by the skill runner wrapper when a call
  fails for any reason short of a successful recorded call (09a).
- skill.call.suppressed — emitted by the skill runner wrapper when the call
  is short-circuited by observation mode or dry-run (09a).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class XlsxRegeneratedV1(BaseModel):
    model_config = {"extra": "forbid"}
    workbook_name: Literal["adminme-ops.xlsx", "adminme-finance.xlsx"]
    generated_at: str = Field(min_length=1)
    last_event_id_consumed: str
    sheets_regenerated: list[str]
    duration_ms: int = Field(ge=0)


class XlsxReverseProjectedV1(BaseModel):
    model_config = {"extra": "forbid"}
    workbook_name: Literal["adminme-ops.xlsx", "adminme-finance.xlsx"]
    detected_at: str = Field(min_length=1)
    sheets_affected: list[str]
    events_emitted: list[str]
    duration_ms: int = Field(ge=0)


class XlsxReverseSkippedDuringForwardV1(BaseModel):
    model_config = {"extra": "forbid"}
    workbook_name: Literal["adminme-ops.xlsx", "adminme-finance.xlsx"]
    detected_at: str = Field(min_length=1)
    skip_reason: Literal["forward_lock_held"]


class SkillCallFailedV1(BaseModel):
    """Skill-runner wrapper failure record [§7, ADR-0002].

    Closed-enum `failure_class` so dashboards can fan out reliably; the
    free-text `error_detail` carries the operator-facing diagnostic.
    """

    model_config = {"extra": "forbid"}
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    failure_class: Literal[
        "input_invalid",
        "sensitivity_refused",
        "scope_insufficient",
        "openclaw_unreachable",
        "openclaw_timeout",
        "openclaw_malformed_response",
        "handler_raised",
        "output_invalid",
    ]
    error_detail: str
    correlation_id: str | None = None
    provider_attempted: str | None = None
    duration_ms_until_failure: int | None = Field(default=None, ge=0)


class SkillCallSuppressedV1(BaseModel):
    """Skill-runner wrapper observation/dry-run short-circuit [§6.13–6.16,
    ADR-0002]."""

    model_config = {"extra": "forbid"}
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    reason: Literal["observation_mode_active", "dry_run"]
    would_have_sent: dict
    correlation_id: str | None = None


class RuntimeStartedV1(BaseModel):
    model_config = {"extra": "forbid"}
    projection_names: list[str]
    reactive_pipeline_ids: list[str]
    standing_order_ids: list[str]
    observation_active: bool
    capability_state: Literal["healthy", "observation_only", "degraded", "blocked"]


class SourceAssessmentRecoveryRequestedV1(BaseModel):
    """Content-free command to reassess one exact retained source fact."""

    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    batch_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_id: str = Field(min_length=1, max_length=240)
    failed_assessment_event_id: str = Field(min_length=1, max_length=240)
    expected_failure_reason: Literal[
        "language_seam_IdempotencyConflict",
        "language_seam_OpenClawTimeout",
        "language_seam_OpenClawUnreachable",
    ]
    owner_member_id: str = Field(min_length=1, max_length=240)
    requested_by_actor: str = Field(min_length=1, max_length=240)
    authority: Literal["member_principal", "guarded_release_exception"]
    policy: Literal["source-assessment-recovery-v1"]
    attempt_limit: Literal[3]
    max_concurrency: Literal[2]


class SourceAssessmentRecoveryRefusedV1(BaseModel):
    """Stable refusal receipt; it contains identifiers and reason only."""

    model_config = {"extra": "forbid"}
    request_event_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    reason: Literal[
        "observation_inactive",
        "source_not_found",
        "unsupported_source_type",
        "source_not_private",
        "source_privileged",
        "tenant_mismatch",
        "source_scope_mismatch",
        "request_scope_mismatch",
        "failed_assessment_not_current",
        "assessment_not_failed",
        "failure_reason_changed",
        "failure_not_retryable",
    ]
    policy: Literal["source-assessment-recovery-v1"]


class SourceAssessmentContractRecoveryRequestedV1(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=8, max_length=120)
    batch_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_id: str = Field(min_length=1, max_length=240)
    expected_latest_assessment_event_id: str = Field(min_length=1, max_length=240)
    failed_assessment_event_id: str = Field(min_length=1, max_length=240)
    expected_failure_reason: Literal[
        "language_seam_OpenClawTimeout", "language_seam_OpenClawUnreachable"
    ]
    required_assessment_contract: Literal["personal-obligation-assessment-v1"]
    owner_member_id: str = Field(min_length=1, max_length=240)
    requested_by_actor: str = Field(min_length=1, max_length=240)
    authority: Literal["member_principal", "guarded_release_exception"]
    policy: Literal["source-assessment-recovery-v2"]
    attempt_limit: Literal[3]
    max_concurrency: Literal[2]


class SourceAssessmentContractRecoveryRefusedV1(BaseModel):
    model_config = {"extra": "forbid"}
    request_event_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    reason: Literal[
        "source_scope_mismatch",
        "assessment_not_current",
        "contract_boundary_not_proven",
        "failure_identity_changed",
        "observation_inactive",
        "request_scope_mismatch",
    ]
    policy: Literal["source-assessment-recovery-v2"]


class SourceAssessmentContractRecoveryResultV1(BaseModel):
    model_config = {"extra": "forbid"}
    request_event_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    assessment_idempotency_key: str = Field(min_length=1, max_length=1000)
    required_assessment_contract: Literal["personal-obligation-assessment-v1"]
    completed_assessment_contract: Literal["personal-obligation-assessment-v1"] | None
    completion_kind: Literal["language", "deterministic_terminal", "failed"]
    classifier_version: str | None = Field(default=None, max_length=40)
    skill_call_event_id: str | None = Field(default=None, max_length=240)
    publication_plan_idempotency_key: str | None = Field(default=None, max_length=1200)


class SourceAssessmentContractRecoveryAttemptStartedV1(BaseModel):
    model_config = {"extra": "forbid"}
    request_event_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    required_assessment_contract: Literal["personal-obligation-assessment-v1"]
    invocation_identity: str = Field(min_length=1, max_length=1000)
    classifier_version: str = Field(min_length=1, max_length=40)
    slot: int = Field(ge=0, le=2)


registry.register(
    "source.assessment.contract_recovery_requested",
    1,
    SourceAssessmentContractRecoveryRequestedV1,
)
registry.register(
    "source.assessment.contract_recovery_refused",
    1,
    SourceAssessmentContractRecoveryRefusedV1,
)
registry.register(
    "source.assessment.contract_recovery_result",
    1,
    SourceAssessmentContractRecoveryResultV1,
)
registry.register(
    "source.assessment.contract_recovery_attempt_started",
    1,
    SourceAssessmentContractRecoveryAttemptStartedV1,
)


registry.register("xlsx.regenerated", 1, XlsxRegeneratedV1)
registry.register("xlsx.reverse_projected", 1, XlsxReverseProjectedV1)
registry.register(
    "xlsx.reverse_skipped_during_forward", 1, XlsxReverseSkippedDuringForwardV1
)
registry.register("skill.call.failed", 1, SkillCallFailedV1)
registry.register("skill.call.suppressed", 1, SkillCallSuppressedV1)
registry.register("runtime.started", 1, RuntimeStartedV1)
registry.register(
    "source.assessment.recovery_requested", 1, SourceAssessmentRecoveryRequestedV1
)
registry.register(
    "source.assessment.recovery_refused", 1, SourceAssessmentRecoveryRefusedV1
)
