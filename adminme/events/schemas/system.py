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


registry.register("xlsx.regenerated", 1, XlsxRegeneratedV1)
