"""
L1 ingest schemas — events emitted by inbound adapters.

Per ADMINISTRATEME_BUILD.md §L1. Five canonical v1 families ship in prompt 04;
additional adapters register further schemas in their own prompts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class MessagingReceivedV1(BaseModel):
    model_config = {"extra": "forbid"}
    source_channel: str = Field(min_length=1)
    from_identifier: str = Field(min_length=1)
    to_identifier: str = Field(min_length=1)
    thread_id: str | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    received_at: str = Field(min_length=1)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class MessagingSentV1(BaseModel):
    model_config = {"extra": "forbid"}
    source_channel: str = Field(min_length=1)
    to_identifier: str = Field(min_length=1)
    thread_id: str | None = None
    subject: str | None = None
    body_text: str | None = None
    sent_at: str = Field(min_length=1)
    delivery_status: Literal["queued", "sent", "failed"]


class TelephonySmsReceivedV1(BaseModel):
    model_config = {"extra": "forbid"}
    from_number: str = Field(pattern=r"^\+?[0-9]{7,15}$")
    to_number: str = Field(pattern=r"^\+?[0-9]{7,15}$")
    body: str
    received_at: str = Field(min_length=1)
    carrier_message_id: str | None = None


class CalendarEventAddedV1(BaseModel):
    model_config = {"extra": "forbid"}
    source: str = Field(min_length=1)
    external_event_id: str = Field(min_length=1)
    calendar_id: str = Field(min_length=1)
    summary: str
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)
    location: str | None = None
    attendees: list[dict[str, Any]] = Field(default_factory=list)
    body: str | None = None


class CalendarEventUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    calendar_event_id: str = Field(min_length=1)
    calendar_source: str = Field(min_length=1)
    external_uid: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    field_updates: dict[str, Any]


class CalendarEventDeletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    calendar_event_id: str = Field(min_length=1)
    calendar_source: str = Field(min_length=1)
    external_uid: str = Field(min_length=1)
    deleted_at: str = Field(min_length=1)


class ArtifactReceivedV1(BaseModel):
    model_config = {"extra": "forbid"}
    source: str = Field(min_length=1)
    external_artifact_id: str | None = None
    mime_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    filename: str | None = None
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    artifact_ref: str = Field(min_length=1)
    received_at: str = Field(min_length=1)


registry.register("messaging.received", 1, MessagingReceivedV1)
registry.register("messaging.sent", 1, MessagingSentV1)
registry.register("telephony.sms_received", 1, TelephonySmsReceivedV1)
registry.register("calendar.event_added", 1, CalendarEventAddedV1)
registry.register("calendar.event_updated", 1, CalendarEventUpdatedV1)
registry.register("calendar.event_deleted", 1, CalendarEventDeletedV1)
registry.register("artifact.received", 1, ArtifactReceivedV1)
