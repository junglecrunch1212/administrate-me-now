"""
Governance / observation-mode schemas.

Per SYSTEM_INVARIANTS.md §6 (observation mode, governance) and DECISIONS.md §D5.
`observation.suppressed` is emitted by the outbound filter when observation
mode would have let a side-effect fire but instead swallowed it; the full
would-have-sent payload is preserved for the tenant's observation-review pane.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class ObservationSuppressedV1(BaseModel):
    model_config = {"extra": "forbid"}
    attempted_action: str = Field(min_length=1)
    attempted_at: str = Field(min_length=1)
    target_channel: str = Field(min_length=1)
    target_identifier: str = Field(min_length=1)
    would_have_sent_payload: dict
    reason: Literal[
        "observation_mode_active",
        "governance_review",
        "governance_deny",
        "governance_hard_refuse",
        "rate_limit",
    ]
    session_correlation_id: str | None = None


registry.register("observation.suppressed", 1, ObservationSuppressedV1)
