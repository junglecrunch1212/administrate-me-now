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


registry.register("xlsx.regenerated", 1, XlsxRegeneratedV1)
registry.register("xlsx.reverse_projected", 1, XlsxReverseProjectedV1)
registry.register(
    "xlsx.reverse_skipped_during_forward", 1, XlsxReverseSkippedDuringForwardV1
)
