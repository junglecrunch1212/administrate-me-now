"""Typed command/result events for provider state mirrors.

The command spine is shared by central Google adapters and per-member macOS
bridges.  It keeps intent and provider side effects in the append-only log,
supports optimistic provider revisions, and lets observation mode suppress the
last outbound step without losing the requested action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


ExternalProvider = Literal[
    "google_calendar",
    "google_tasks",
    "apple_calendar",
    "apple_reminders",
    "apple_notes",
]


class ExternalWriteRequestedV1(BaseModel):
    model_config = {"extra": "forbid"}
    command_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ExternalProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    operation: str = Field(min_length=1, max_length=120)
    target_external_id: str | None = Field(default=None, max_length=1000)
    expected_provider_revision: str | None = Field(default=None, max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    requested_at: datetime


class ExternalWriteCompletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    command_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ExternalProvider
    operation: str = Field(min_length=1, max_length=120)
    external_id: str | None = None
    provider_revision: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime
    suppressed: bool = False


class ExternalWriteFailedV1(BaseModel):
    model_config = {"extra": "forbid"}
    command_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider: ExternalProvider
    operation: str = Field(min_length=1, max_length=120)
    error_kind: Literal[
        "conflict",
        "permission",
        "unsupported",
        "not_found",
        "validation",
        "provider",
    ]
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool = False
    failed_at: datetime


registry.register("external_sync.write_requested", 1, ExternalWriteRequestedV1)
registry.register("external_sync.write_completed", 1, ExternalWriteCompletedV1)
registry.register("external_sync.write_failed", 1, ExternalWriteFailedV1)


__all__ = [
    "ExternalProvider",
    "ExternalWriteCompletedV1",
    "ExternalWriteFailedV1",
    "ExternalWriteRequestedV1",
]
