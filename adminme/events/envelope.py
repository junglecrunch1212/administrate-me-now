"""
Typed event envelope — the row shape for every event in the log.

Per ADMINISTRATEME_BUILD.md §"L2: THE EVENT LOG" (15-column full schema),
SYSTEM_INVARIANTS.md §1 invariant 5, and DECISIONS.md §D16.

`payload` is validated by the schema registry
(adminme.events.registry.SchemaRegistry.validate) against the model
registered for `(type, schema_version)`. Unknown types are rejected by
EventLog.append unless ADMINME_ALLOW_UNKNOWN_SCHEMAS=1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Sensitivity = Literal["normal", "sensitive", "privileged"]


class EventEnvelope(BaseModel):
    """
    Typed envelope for every event in the log.

    Owner / visibility scopes must use one of the documented prefixes per
    SYSTEM_INVARIANTS.md §1 invariant 6:
    `private:<member_id>`, `shared:household`, `org:<id>`.
    """

    model_config = {"extra": "forbid"}

    event_id: str = ""
    event_at_ms: int
    tenant_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    occurred_at: str
    recorded_at: str = ""
    source_adapter: str = Field(min_length=1)
    source_account_id: str = Field(min_length=1)
    owner_scope: str = Field(min_length=1)
    visibility_scope: str = Field(min_length=1)
    sensitivity: Sensitivity = "normal"
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any]
    raw_ref: str | None = None
    actor_identity: str | None = None

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def _iso_utc_or_empty(cls, v: str) -> str:
        if v == "":
            return v
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"not a valid ISO 8601 datetime: {v}") from exc
        return v

    @staticmethod
    def now_utc_iso() -> str:
        """Current UTC timestamp in ISO 8601 (seconds precision, trailing Z)."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
