"""
L1 ingest schemas — events emitted by inbound adapters.

Per ADMINISTRATEME_BUILD.md §L1. Five canonical v1 families ship in prompt 04;
additional adapters register further schemas in their own prompts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


class AppleMessagesParticipantV1(BaseModel):
    """Provider endpoint observed in one native Messages conversation.

    These identifiers are communication endpoints, not Party records. Identity
    resolution remains a separate, evidence-backed projection concern.
    """

    model_config = {"extra": "forbid"}

    participant_id: str = Field(min_length=1, max_length=500)
    role: Literal["sender", "recipient", "self"]
    is_self: bool = False


class AppleMessagesAttachmentMetadataV1(BaseModel):
    """Content-free attachment metadata with an opaque protected reference."""

    model_config = {"extra": "forbid"}

    attachment_id: str = Field(min_length=1, max_length=500)
    mime_type: str | None = Field(default=None, max_length=240)
    uti: str | None = Field(default=None, max_length=240)
    byte_size: int | None = Field(default=None, ge=0)
    filename: str | None = Field(default=None, max_length=500)
    transfer_state: str | None = Field(default=None, max_length=120)
    is_outgoing: bool = False
    is_sticker: bool = False
    protected_ref: str = Field(
        min_length=1,
        max_length=500,
        pattern=r"^protected-attachment:[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def _reject_paths(self) -> "AppleMessagesAttachmentMetadataV1":
        if self.filename and ("/" in self.filename or "\\" in self.filename):
            raise ValueError("attachment filename cannot expose a local path")
        return self


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


class MessagingReceivedV5(MessagingReceivedV4):
    """Explicit account owner and deterministic route for service channels.

    This is a provider-neutral representation only. A future adapter must
    resolve one reviewed route before constructing the envelope; unknown
    conversations use ``unresolved`` and remain outside member scopes.
    """

    # An additive schema version may widen a Pydantic field even though normal
    # Python subclass substitution would forbid it.
    member_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=240
    )
    service_principal_id: str | None = Field(default=None, min_length=1, max_length=240)
    workspace: Literal["personal", "work", "service"]  # type: ignore[assignment]
    routing_scope: Literal[
        "member_private", "household_shared", "external_contact", "unresolved"
    ]
    routing_target_id: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def _validate_owner_and_route(self) -> "MessagingReceivedV5":
        if (self.member_id is None) == (self.service_principal_id is None):
            raise ValueError("message account requires exactly one owner principal")
        target_required = self.routing_scope in {
            "member_private",
            "external_contact",
        }
        if target_required != (self.routing_target_id is not None):
            raise ValueError("message route target contradicts routing_scope")
        if self.member_id is not None and (
            self.routing_scope != "member_private"
            or self.routing_target_id != self.member_id
        ):
            raise ValueError("member-owned message route must remain member-private")
        return self


class MessagingReceivedV6(MessagingReceivedV5):
    """Service-owned direct intake with an exact trusted-metadata route key."""

    model_config = {"extra": "forbid"}
    channel_id: str = Field(min_length=1, max_length=240)
    trusted_route_key: str | None = Field(
        default=None, pattern=r"^route:[0-9a-f]{32,64}$"
    )
    route_source: Literal["exact_authenticated_metadata"]

    @model_validator(mode="after")
    def _validate_direct_source(self) -> "MessagingReceivedV6":
        if self.service_principal_id is None or self.member_id is not None:
            raise ValueError("direct service intake requires service ownership")
        if self.routing_scope != "unresolved" or self.routing_target_id is not None:
            raise ValueError(
                "source adapters preserve a pending opaque route; the central "
                "trusted route table alone resolves member scope"
            )
        return self


class MessagingReceivedV7(MessagingReceivedV5):
    """Read-only member-bridge observation from the reviewed Messages source."""

    model_config = {"extra": "forbid"}

    bridge_enrollment_id: str = Field(min_length=1, max_length=240)
    source_channel: Literal["imessage", "sms", "unknown"]  # type: ignore[assignment]
    from_identifier: str = Field(min_length=1, max_length=500)  # type: ignore[assignment]
    to_identifier: str = Field(min_length=1, max_length=500)  # type: ignore[assignment]
    subject: str | None = Field(default=None, max_length=1000)  # type: ignore[assignment]
    body_text: str | None = Field(default=None, max_length=50_000)  # type: ignore[assignment]
    body_html: None = None  # type: ignore[assignment]
    device_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_id: Literal["messaging-bluebubbles"]
    protected_source_id: str = Field(pattern=r"^protected-source:[0-9a-f]{64}$")
    native_chat_id: str = Field(min_length=1, max_length=500)
    native_revision: str = Field(min_length=1, max_length=500)
    native_revision_ms: int = Field(ge=0)
    direction: Literal["incoming"]
    participants: list[AppleMessagesParticipantV1] = Field(min_length=1, max_length=100)
    sender_id: str = Field(min_length=1, max_length=500)
    observed_at: str = Field(min_length=1)
    reply_to_native_message_id: str | None = Field(default=None, max_length=500)
    transport_class: Literal["imessage", "sms", "unknown"]
    is_group: bool
    message_state: Literal["active", "edited", "retracted"]
    attachments: list[AppleMessagesAttachmentMetadataV1] = Field(  # type: ignore[assignment]
        default_factory=list, max_length=100
    )
    transport_version: Literal["1.9.9"]
    transport_commit: Literal["ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1"]
    correlation_id: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _validate_bridge_observation(self) -> "MessagingReceivedV7":
        if self.member_id is None or self.routing_target_id != self.member_id:
            raise ValueError("Messages bridge observation must remain member-private")
        if self.provider != "bluebubbles" or self.workspace != "personal":
            raise ValueError("Messages bridge observation has the wrong provider scope")
        if self.native_message_id == self.native_chat_id:
            raise ValueError("message and chat identities must remain distinct")
        participant_ids = [item.participant_id for item in self.participants]
        if len(participant_ids) != len(set(participant_ids)):
            raise ValueError("message participants must be unique")
        self_id = f"self:{self.provider_account_id}"
        local_endpoints = [item for item in self.participants if item.is_self]
        if len(local_endpoints) != 1 or local_endpoints[0].participant_id != self_id:
            raise ValueError("message must identify the exact local endpoint once")
        for participant in self.participants:
            expected_role = (
                "self"
                if participant.participant_id == self_id
                else "sender"
                if participant.participant_id == self.sender_id
                else "recipient"
            )
            if participant.is_self != (participant.participant_id == self_id):
                raise ValueError("message participant self marker is contradictory")
            if participant.role != expected_role:
                raise ValueError("incoming participant role is contradictory")
        if self.sender_id == self_id or self.sender_id not in participant_ids:
            raise ValueError("incoming sender must be a remote participant")
        if self.from_identifier != self.sender_id or self.to_identifier != self_id:
            raise ValueError("incoming sender fields contradict each other")
        if (
            self.to_identifiers != [self_id]
            or self.cc_identifiers
            or self.bcc_identifiers
        ):
            raise ValueError("incoming recipient fields contradict the local endpoint")
        if self.transport_class != self.source_channel:
            raise ValueError("message transport fields contradict each other")
        if any(item.is_outgoing for item in self.attachments):
            raise ValueError("incoming attachment direction is contradictory")
        if self.reply_to_native_message_id == self.native_message_id:
            raise ValueError("message cannot reply to itself")
        return self


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


class MessagingSentV4(MessagingSentV3):
    """Outbound observation retaining the provider's HTML MIME alternative."""

    model_config = {"extra": "forbid"}
    body_html: str | None = None


class MessagingSentV5(MessagingSentV4):
    """Outbound service-principal fact that cannot impersonate a member."""

    # An additive schema version may widen a Pydantic field even though normal
    # Python subclass substitution would forbid it.
    member_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=240
    )
    service_principal_id: str | None = Field(default=None, min_length=1, max_length=240)
    workspace: Literal["personal", "work", "service"]  # type: ignore[assignment]
    send_as: Literal["member", "service_principal"]
    routing_scope: Literal["member_private", "household_shared", "external_contact"]
    routing_target_id: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def _validate_sender_and_route(self) -> "MessagingSentV5":
        if (self.member_id is None) == (self.service_principal_id is None):
            raise ValueError("sent message requires exactly one sender principal")
        if self.send_as == "member" and self.member_id is None:
            raise ValueError("member send_as requires member ownership")
        if self.send_as == "service_principal" and self.service_principal_id is None:
            raise ValueError("service send_as requires service-principal ownership")
        target_required = self.routing_scope in {
            "member_private",
            "external_contact",
        }
        if target_required != (self.routing_target_id is not None):
            raise ValueError("sent-message route target contradicts routing_scope")
        if self.member_id is not None and (
            self.routing_scope != "member_private"
            or self.routing_target_id != self.member_id
        ):
            raise ValueError(
                "member-owned sent-message route must remain member-private"
            )
        return self


class MessagingSentV6(MessagingSentV5):
    """Read-only observation of service-owned sent mail, never a send command."""

    routing_scope: Literal[  # type: ignore[assignment]
        "member_private", "household_shared", "external_contact", "unresolved"
    ]
    channel_id: str = Field(min_length=1, max_length=240)
    route_source: Literal["exact_authenticated_metadata"]

    @model_validator(mode="after")
    def _validate_service_observation(self) -> "MessagingSentV6":
        if self.service_principal_id is None or self.member_id is not None:
            raise ValueError("service sent observation requires service ownership")
        if self.send_as != "service_principal":
            raise ValueError("service sent observation cannot impersonate a member")
        if self.routing_scope == "unresolved" and self.routing_target_id is not None:
            raise ValueError("unresolved sent observation cannot name a target")
        return self


class MessagingSentV7(MessagingSentV5):
    """Read-only observation of a member-authored native Messages record."""

    model_config = {"extra": "forbid"}

    bridge_enrollment_id: str = Field(min_length=1, max_length=240)
    source_channel: Literal["imessage", "sms", "unknown"]  # type: ignore[assignment]
    to_identifier: str = Field(min_length=1, max_length=500)  # type: ignore[assignment]
    subject: str | None = Field(default=None, max_length=1000)  # type: ignore[assignment]
    body_text: str | None = Field(default=None, max_length=50_000)  # type: ignore[assignment]
    body_html: None = None  # type: ignore[assignment]
    device_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_id: Literal["messaging-bluebubbles"]
    protected_source_id: str = Field(pattern=r"^protected-source:[0-9a-f]{64}$")
    native_chat_id: str = Field(min_length=1, max_length=500)
    native_message_id: str = Field(min_length=1, max_length=240)  # type: ignore[assignment]
    native_revision: str = Field(min_length=1, max_length=500)
    native_revision_ms: int = Field(ge=0)
    direction: Literal["outgoing"]
    participants: list[AppleMessagesParticipantV1] = Field(min_length=1, max_length=100)
    sender_id: str = Field(min_length=1, max_length=500)
    observed_at: str = Field(min_length=1)
    reply_to_native_message_id: str | None = Field(default=None, max_length=500)
    transport_class: Literal["imessage", "sms", "unknown"]
    is_group: bool
    message_state: Literal["active", "edited", "retracted"]
    attachments: list[AppleMessagesAttachmentMetadataV1] = Field(
        default_factory=list, max_length=100
    )
    transport_version: Literal["1.9.9"]
    transport_commit: Literal["ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1"]
    correlation_id: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _validate_bridge_observation(self) -> "MessagingSentV7":
        if (
            self.member_id is None
            or self.send_as != "member"
            or self.routing_target_id != self.member_id
        ):
            raise ValueError("Messages sent observation must remain member-private")
        if self.provider != "bluebubbles" or self.workspace != "personal":
            raise ValueError("Messages bridge observation has the wrong provider scope")
        if self.native_message_id == self.native_chat_id:
            raise ValueError("message and chat identities must remain distinct")
        participant_ids = [item.participant_id for item in self.participants]
        if len(participant_ids) != len(set(participant_ids)):
            raise ValueError("message participants must be unique")
        self_id = f"self:{self.provider_account_id}"
        local_endpoints = [item for item in self.participants if item.is_self]
        if len(local_endpoints) != 1 or local_endpoints[0].participant_id != self_id:
            raise ValueError("message must identify the exact local endpoint once")
        for participant in self.participants:
            expected_role = (
                "self" if participant.participant_id == self_id else "recipient"
            )
            if participant.is_self != (participant.participant_id == self_id):
                raise ValueError("message participant self marker is contradictory")
            if participant.role != expected_role:
                raise ValueError("outgoing participant role is contradictory")
        if self.sender_id != self_id:
            raise ValueError("outgoing sender must be the exact local endpoint")
        if self.to_identifier == self_id or self.to_identifier not in participant_ids:
            raise ValueError("outgoing recipient must be a remote participant")
        if self.transport_class != self.source_channel:
            raise ValueError("message transport fields contradict each other")
        if any(not item.is_outgoing for item in self.attachments):
            raise ValueError("outgoing attachment direction is contradictory")
        if self.reply_to_native_message_id == self.native_message_id:
            raise ValueError("message cannot reply to itself")
        return self


class MessagingSourceObservedV1(BaseModel):
    """Content-free coverage and assurance receipt for one bounded read cycle."""

    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1, max_length=240)
    bridge_enrollment_id: str = Field(min_length=1, max_length=240)
    device_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_id: Literal["messaging-bluebubbles"]
    provider: Literal["bluebubbles"]
    provider_account_id: str = Field(pattern=r"^provider-account:[0-9a-f]{32,64}$")
    workspace: Literal["personal"]
    protected_source_id: str = Field(pattern=r"^protected-source:[0-9a-f]{64}$")
    transport_version: Literal["1.9.9"]
    transport_commit: Literal["ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1"]
    phase: Literal["history", "live", "reconcile"]
    coverage: Literal["complete", "partial", "unknown"]
    truncated: bool
    requested: int = Field(ge=0)
    accepted: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    failed: int = Field(ge=0)
    missing: int = Field(ge=0)
    watermark_ms: int = Field(ge=0)
    observed_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_counts(self) -> "MessagingSourceObservedV1":
        accounted = self.accepted + self.duplicates + self.quarantined + self.failed
        if accounted + self.missing != self.requested:
            raise ValueError("Messages source assurance counts must reconcile")
        if self.truncated and self.coverage == "complete":
            raise ValueError("truncated Messages coverage cannot be complete")
        return self


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


class CalendarEventV3(CalendarEventV2):
    """Current owner-aware calendar observation for member or CoS sources."""

    member_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=240
    )
    service_principal_id: str | None = Field(default=None, min_length=1, max_length=240)
    workspace: Literal["personal", "work", "service"]

    @model_validator(mode="after")
    def _validate_calendar_owner(self) -> "CalendarEventV3":
        if (self.member_id is None) == (self.service_principal_id is None):
            raise ValueError("calendar observation requires exactly one owner")
        if self.service_principal_id is not None and self.workspace != "service":
            raise ValueError("service calendar observation requires service workspace")
        if self.member_id is not None and self.workspace == "service":
            raise ValueError("member calendar observation cannot use service workspace")
        return self


class CalendarEventDeletedV3(CalendarEventDeletedV2):
    """Owner-aware deletion paired with :class:`CalendarEventV3`."""

    member_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=240
    )
    service_principal_id: str | None = Field(default=None, min_length=1, max_length=240)
    workspace: Literal["personal", "work", "service"]

    @model_validator(mode="after")
    def _validate_calendar_owner(self) -> "CalendarEventDeletedV3":
        if (self.member_id is None) == (self.service_principal_id is None):
            raise ValueError("calendar deletion requires exactly one owner")
        if self.service_principal_id is not None and self.workspace != "service":
            raise ValueError("service calendar deletion requires service workspace")
        if self.member_id is not None and self.workspace == "service":
            raise ValueError("member calendar deletion cannot use service workspace")
        return self


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
registry.register("messaging.received", 5, MessagingReceivedV5)
registry.register("messaging.received", 6, MessagingReceivedV6)
registry.register("messaging.received", 7, MessagingReceivedV7)
registry.register(
    "interaction.participant_resolved", 1, InteractionParticipantResolvedV1
)
registry.register("messaging.sent", 1, MessagingSentV1)
registry.register("messaging.sent", 2, MessagingSentV2)
registry.register("messaging.sent", 3, MessagingSentV3)
registry.register("messaging.sent", 4, MessagingSentV4)
registry.register("messaging.sent", 5, MessagingSentV5)
registry.register("messaging.sent", 6, MessagingSentV6)
registry.register("messaging.sent", 7, MessagingSentV7)
registry.register("messaging.source_observed", 1, MessagingSourceObservedV1)
registry.register("communication.state_changed", 1, CommunicationStateChangedV1)
registry.register("telephony.sms_received", 1, TelephonySmsReceivedV1)
registry.register("calendar.event_added", 1, CalendarEventAddedV1)
registry.register("calendar.event_updated", 1, CalendarEventUpdatedV1)
registry.register("calendar.event_deleted", 1, CalendarEventDeletedV1)
registry.register("calendar.event_added", 2, CalendarEventV2)
registry.register("calendar.event_updated", 2, CalendarEventV2)
registry.register("calendar.event_deleted", 2, CalendarEventDeletedV2)
registry.register("calendar.event_added", 3, CalendarEventV3)
registry.register("calendar.event_updated", 3, CalendarEventV3)
registry.register("calendar.event_deleted", 3, CalendarEventDeletedV3)
registry.register("calendar.event_outcome_recorded", 1, CalendarEventOutcomeRecordedV1)
registry.register("artifact.received", 1, ArtifactReceivedV1)
registry.register("messaging.classified", 1, MessagingClassifiedV1)
registry.register(
    "messaging.semantic_features_extracted",
    1,
    MessagingSemanticFeaturesExtractedV1,
)
