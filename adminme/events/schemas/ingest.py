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


class MessagingReceivedV2(MessagingReceivedV1):
    """Provider-identifiable communication fact used by current Cat-A adapters.

    Version 2 is additive: v1 remains readable, while every new adapter event
    carries the provider-native immutable message identifier needed for
    request and delivery deduplication.
    """

    native_message_id: str = Field(min_length=1, max_length=240)


class MessagingReceivedV3(MessagingReceivedV2):
    """Account-bound, loss-aware communication fact for the Comms cockpit.

    The envelope still carries the authoritative owner/sensitivity scope.  These
    fields preserve the provider/account/workspace coordinates required to form
    a combined inbox without erasing the isolation boundary that each message
    crossed.  Provider labels are observations, never AdministrateMe truth.
    """

    member_id: str = Field(min_length=1, max_length=240)
    provider: Literal[
        "gmail",
        "microsoft_graph",
        "bluebubbles",
        "twilio_sms",
        "twilio_whatsapp",
        "openclaw_channel",
        "fixture",
    ]
    provider_account_id: str = Field(min_length=1, max_length=500)
    workspace: Literal["personal", "work"]
    to_identifiers: list[str] = Field(default_factory=list, max_length=100)
    cc_identifiers: list[str] = Field(default_factory=list, max_length=100)
    bcc_identifiers: list[str] = Field(default_factory=list, max_length=100)
    reply_to_identifier: str | None = Field(default=None, max_length=1000)
    rfc_message_id: str | None = Field(default=None, max_length=1000)
    references: list[str] = Field(default_factory=list, max_length=100)
    provider_labels: list[str] = Field(default_factory=list, max_length=200)
    unread: bool = True
    archived: bool = False
    starred: bool = False
    important: bool = False


class MessagingReceivedV4(MessagingReceivedV3):
    """Exact RFC reply continuity for conservative cross-thread context."""

    model_config = {"extra": "forbid"}
    in_reply_to: str | None = Field(default=None, max_length=1000)


class InteractionParticipantResolvedV1(BaseModel):
    """Identity pipeline result linking an interaction role to a Party."""

    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1)
    interaction_id: str = Field(min_length=1)
    party_id: str = Field(min_length=1)
    role: Literal["from", "to", "cc", "bcc", "mentioned"]
    identifier_kind: str = Field(min_length=1)
    identifier_normalized: str = Field(min_length=1)


class MessagingSentV1(BaseModel):
    model_config = {"extra": "forbid"}
    source_channel: str = Field(min_length=1)
    to_identifier: str = Field(min_length=1)
    thread_id: str | None = None
    subject: str | None = None
    body_text: str | None = None
    sent_at: str = Field(min_length=1)
    delivery_status: Literal["queued", "sent", "failed"]


class MessagingSentV2(MessagingSentV1):
    """Account-bound outbound observation emitted after the provider seam."""

    member_id: str = Field(min_length=1, max_length=240)
    provider: Literal[
        "gmail",
        "microsoft_graph",
        "bluebubbles",
        "twilio_sms",
        "twilio_whatsapp",
        "openclaw_channel",
        "fixture",
    ]
    provider_account_id: str = Field(min_length=1, max_length=500)
    workspace: Literal["personal", "work"]
    native_message_id: str | None = Field(default=None, max_length=240)
    in_reply_to_interaction_id: str | None = Field(default=None, max_length=240)


class MessagingSentV3(MessagingSentV2):
    """Outbound observation with exact RFC continuity metadata."""

    model_config = {"extra": "forbid"}
    rfc_message_id: str | None = Field(default=None, max_length=1000)
    references: list[str] = Field(default_factory=list, max_length=100)
    in_reply_to: str | None = Field(default=None, max_length=1000)


class CommunicationStateChangedV1(BaseModel):
    """Deterministic member-owned focus state, independent of provider state."""

    interaction_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    operation: Literal["snooze", "unsnooze", "quiet", "unquiet"]
    snoozed_until: str | None = None
    quieted: bool | None = None
    changed_at: str = Field(min_length=1)
    expected_last_event_id: str = Field(min_length=1, max_length=240)


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


CalendarProvider = Literal["google_calendar", "apple_calendar"]


class CalendarParticipantV2(BaseModel):
    """Provider-neutral attendee/organizer state.

    ``response_status`` intentionally keeps provider vocabulary: Google and
    EventKit expose slightly different status sets and losing that value at
    ingest would make a later round trip destructive.
    """

    model_config = {"extra": "forbid"}
    email: str | None = None
    display_name: str | None = None
    participant_id: str | None = None
    role: str | None = None
    participant_type: str | None = None
    response_status: str | None = None
    self_: bool = Field(default=False, alias="self")
    organizer: bool = False
    optional: bool = False
    resource: bool = False
    comment: str | None = None
    additional_guests: int = Field(default=0, ge=0)


class CalendarAlarmV2(BaseModel):
    model_config = {"extra": "forbid"}
    method: Literal["alert", "email", "sms", "default"] = "alert"
    trigger_at: str | None = None
    relative_offset_seconds: int | None = None
    provider_alarm_id: str | None = None


class CalendarAttachmentV2(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = None
    url: str = Field(min_length=1)
    mime_type: str | None = None
    provider_file_id: str | None = None


class CalendarRecurrenceV2(BaseModel):
    """Lossless iCalendar recurrence lines plus provider instance identity."""

    model_config = {"extra": "forbid"}
    lines: list[str] = Field(default_factory=list)
    recurring_event_id: str | None = None
    original_start: str | None = None
    this_and_future: bool = False


class CalendarEventV2(BaseModel):
    """Complete canonical calendar snapshot used for adds and updates."""

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    provider: CalendarProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    calendar_external_id: str = Field(min_length=1, max_length=1000)
    calendar_name: str | None = None
    external_event_id: str = Field(min_length=1, max_length=1000)
    ical_uid: str | None = None
    provider_revision: str | None = None
    status: Literal["confirmed", "tentative", "cancelled"] = "confirmed"
    event_type: str = "default"
    summary: str
    description: str | None = None
    location: str | None = None
    structured_location: dict[str, Any] | None = None
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)
    start_time_zone: str | None = None
    end_time_zone: str | None = None
    all_day: bool = False
    availability: Literal["busy", "free", "tentative", "unavailable"] = "busy"
    visibility: Literal["default", "public", "private", "confidential"] = "default"
    organizer: CalendarParticipantV2 | None = None
    attendees: list[CalendarParticipantV2] = Field(default_factory=list)
    alarms: list[CalendarAlarmV2] = Field(default_factory=list, max_length=5)
    use_default_alarms: bool = False
    recurrence: CalendarRecurrenceV2 = Field(default_factory=CalendarRecurrenceV2)
    sequence: int = Field(default=0, ge=0)
    conference: dict[str, Any] | None = None
    attachments: list[CalendarAttachmentV2] = Field(default_factory=list)
    color_id: str | None = None
    source_url: str | None = None
    html_link: str | None = None
    linked_item_kind: Literal["task", "commitment", "list_item"] | None = None
    linked_item_id: str | None = None
    completion_policy: Literal["manual", "on_attended", "on_elapsed"] = "manual"
    created_at: str | None = None
    updated_at: str = Field(min_length=1)


class CalendarEventDeletedV2(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    provider: CalendarProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    calendar_external_id: str = Field(min_length=1, max_length=1000)
    external_event_id: str = Field(min_length=1, max_length=1000)
    ical_uid: str | None = None
    provider_revision: str | None = None
    deleted_at: str = Field(min_length=1)


class CalendarEventOutcomeRecordedV1(BaseModel):
    """Activity evidence, deliberately distinct from a task completion.

    Calendar presence can prove that an event elapsed or was cancelled, but
    it cannot by itself prove attendance.  ``evidence`` and ``confidence``
    keep that distinction visible to downstream human-gate policy.
    """

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    provider: CalendarProvider
    external_event_id: str = Field(min_length=1, max_length=1000)
    outcome: Literal["elapsed", "attended", "missed", "cancelled", "rescheduled"]
    attendance_status: str | None = None
    evidence: Literal["provider_status", "member_confirmation", "calendar_elapsed"]
    confidence: float = Field(ge=0.0, le=1.0)
    recorded_at: str = Field(min_length=1)
    linked_item_kind: Literal["task", "commitment", "list_item"] | None = None
    linked_item_id: str | None = None
    completion_policy: Literal["manual", "on_attended", "on_elapsed"] = "manual"


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


class MessagingClassifiedV1(BaseModel):
    """Emitted by the ``noise_filtering`` pipeline (prompt 10b-i) tagging
    an inbound messaging event with a classification + skill provenance.

    Per [BUILD.md §1136-1138], the ``interactions`` projection uses this
    to decide whether to surface the message in authorized interactions or suppress to
    the noise bucket. The pipeline NEVER deletes the source event."""

    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1)
    classification: Literal[
        "noise", "transactional", "personal", "professional", "promotional"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)


class MessagingSemanticFeaturesExtractedV1(BaseModel):
    """Schema-bound NLP evidence used by deterministic Comms/Triage rules."""

    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1)
    reply_signal: Literal["required", "not_required", "unknown"] = "unknown"
    automated: bool = False
    transactional_due: bool = False
    security_code: bool = False
    person_to_person: bool = False
    deadline: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list, max_length=20)
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)


registry.register("messaging.received", 1, MessagingReceivedV1)
registry.register("messaging.received", 2, MessagingReceivedV2)
registry.register("messaging.received", 3, MessagingReceivedV3)
registry.register("messaging.received", 4, MessagingReceivedV4)
registry.register(
    "interaction.participant_resolved", 1, InteractionParticipantResolvedV1
)
registry.register("messaging.sent", 1, MessagingSentV1)
registry.register("messaging.sent", 2, MessagingSentV2)
registry.register("messaging.sent", 3, MessagingSentV3)
registry.register("communication.state_changed", 1, CommunicationStateChangedV1)
registry.register("telephony.sms_received", 1, TelephonySmsReceivedV1)
registry.register("calendar.event_added", 1, CalendarEventAddedV1)
registry.register("calendar.event_updated", 1, CalendarEventUpdatedV1)
registry.register("calendar.event_deleted", 1, CalendarEventDeletedV1)
registry.register("calendar.event_added", 2, CalendarEventV2)
registry.register("calendar.event_updated", 2, CalendarEventV2)
registry.register("calendar.event_deleted", 2, CalendarEventDeletedV2)
registry.register("calendar.event_outcome_recorded", 1, CalendarEventOutcomeRecordedV1)
registry.register("artifact.received", 1, ArtifactReceivedV1)
registry.register("messaging.classified", 1, MessagingClassifiedV1)
registry.register(
    "messaging.semantic_features_extracted",
    1,
    MessagingSemanticFeaturesExtractedV1,
)
