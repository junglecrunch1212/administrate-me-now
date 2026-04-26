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


registry.register("xlsx.regenerated", 1, XlsxRegeneratedV1)
registry.register("xlsx.reverse_projected", 1, XlsxReverseProjectedV1)
registry.register(
    "xlsx.reverse_skipped_during_forward", 1, XlsxReverseSkippedDuringForwardV1
)
registry.register("skill.call.failed", 1, SkillCallFailedV1)
registry.register("skill.call.suppressed", 1, SkillCallSuppressedV1)
