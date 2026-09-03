"""Deterministic member policy for source-to-Triage candidate identification.

The catalog is executable product truth, not an adapter-health claim.  It
describes every member entry surface and each reviewed current or planned
source family.  A member may change only candidate *admission* and whether a
grounded language result may contribute candidate fields.  Raw source
retention, source-assurance accounting, privacy, routing, authorization, and
human confirmation are never configurable here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast


CandidateMode = Literal["review", "manual", "quiet"]

CANDIDATE_KINDS = (
    "task",
    "commitment",
    "calendar_event",
    "reminder",
    "list",
    "list_item",
    "recurrence",
    "party_identifier",
    "party_candidate",
    "party_relationship",
    "relationship_fact",
)

PEOPLE_KINDS = (
    "party_identifier",
    "party_candidate",
    "party_relationship",
    "relationship_fact",
)


def _source(
    source_id: str,
    label: str,
    *,
    group: str,
    channel_type: str,
    projection: str,
    implementation: Literal["current", "planned"],
    signals: tuple[str, ...],
    candidate_kinds: tuple[str, ...],
    extraction_layers: tuple[str, ...],
    language_available: bool,
    default_mode: CandidateMode,
    adapter_kind: str | None = None,
    locked: bool = False,
    policy_execution: Literal["current", "planned"] = "current",
) -> dict[str, Any]:
    return {
        "id": source_id,
        "label": label,
        "group": group,
        "channel_type": channel_type,
        "projection": projection,
        "implementation": implementation,
        "signals": list(signals),
        "candidate_kinds": list(candidate_kinds),
        "extraction_layers": list(extraction_layers),
        "language_available": language_available,
        "default_mode": default_mode,
        "adapter_kind": adapter_kind,
        "locked": locked,
        "policy_execution": policy_execution,
    }


_DIRECT_SIGNALS = ("exact member text", "optional kind hint", "source link")
_DIRECT_LAYERS = ("raw fields", "bounded regex", "grounded language")

SOURCE_CATALOG: tuple[dict[str, Any], ...] = (
    *(
        _source(
            f"surface:{surface}",
            f"{label} · Add for review",
            group="Member entry points",
            channel_type="member-authored intent",
            projection=label,
            implementation="current",
            signals=_DIRECT_SIGNALS,
            candidate_kinds=CANDIDATE_KINDS,
            extraction_layers=_DIRECT_LAYERS,
            language_available=True,
            default_mode="review",
            locked=True,
        )
        for surface, label in (
            ("today", "Today"),
            ("triage", "Triage"),
            ("comms", "Comms"),
            ("reminders", "Reminders"),
            ("calendar", "Calendar"),
            ("people", "People"),
            ("notes", "Notes"),
            ("finance", "Finance"),
            ("scoreboard", "Scoreboard"),
            ("settings", "Settings"),
        )
    ),
    _source(
        "surface:openclaw",
        "Member CoS chat · Add for review",
        group="Member entry points",
        channel_type="member-authored chat intent",
        projection="OpenClaw chat",
        implementation="current",
        signals=_DIRECT_SIGNALS,
        candidate_kinds=CANDIDATE_KINDS,
        extraction_layers=_DIRECT_LAYERS,
        language_available=True,
        default_mode="review",
        locked=True,
    ),
    _source(
        "adapter:gmail",
        "Gmail",
        group="Messages and conversations",
        channel_type="email",
        projection="Comms",
        implementation="current",
        adapter_kind="messaging-gmail-api",
        signals=(
            "direction",
            "sender",
            "recipients",
            "subject",
            "body",
            "labels",
            "thread",
            "attachments",
        ),
        candidate_kinds=CANDIDATE_KINDS,
        extraction_layers=("provider metadata", "bounded regex", "grounded language"),
        language_available=True,
        default_mode="review",
    ),
    _source(
        "adapter:generic_messaging",
        "Other reviewed message channel",
        group="Messages and conversations",
        channel_type="email or message",
        projection="Comms",
        implementation="current",
        signals=(
            "direction",
            "sender",
            "recipients",
            "subject",
            "body",
            "thread",
            "attachments",
        ),
        candidate_kinds=CANDIDATE_KINDS,
        extraction_layers=("provider metadata", "bounded regex", "grounded language"),
        language_available=True,
        default_mode="review",
    ),
    _source(
        "adapter:microsoft_graph_mail",
        "Outlook / Microsoft 365 Mail",
        group="Messages and conversations",
        channel_type="email",
        projection="Comms",
        implementation="planned",
        adapter_kind="messaging-microsoft-graph",
        signals=(
            "direction",
            "sender",
            "recipients",
            "subject",
            "body",
            "flag",
            "importance",
            "thread",
            "attachments",
        ),
        candidate_kinds=CANDIDATE_KINDS,
        extraction_layers=("provider metadata", "bounded regex", "grounded language"),
        language_available=True,
        default_mode="review",
    ),
    _source(
        "adapter:bluebubbles",
        "iMessage / personal SMS",
        group="Messages and conversations",
        channel_type="chat message",
        projection="Comms",
        implementation="current",
        adapter_kind="messaging-bluebubbles",
        signals=(
            "direction",
            "sender",
            "participants",
            "body",
            "thread",
            "attachments",
        ),
        candidate_kinds=CANDIDATE_KINDS,
        extraction_layers=("provider metadata", "bounded regex", "grounded language"),
        language_available=True,
        default_mode="review",
    ),
    _source(
        "adapter:twilio_sms",
        "Twilio SMS",
        group="Messages and conversations",
        channel_type="SMS",
        projection="Comms",
        implementation="planned",
        adapter_kind="messaging-twilio-sms",
        signals=("direction", "sender", "recipient", "body", "media", "delivery state"),
        candidate_kinds=CANDIDATE_KINDS,
        extraction_layers=("provider metadata", "bounded regex", "grounded language"),
        language_available=True,
        default_mode="review",
    ),
    _source(
        "adapter:twilio_whatsapp",
        "WhatsApp through Twilio",
        group="Messages and conversations",
        channel_type="WhatsApp message",
        projection="Comms",
        implementation="planned",
        adapter_kind="messaging-twilio-whatsapp",
        signals=("direction", "sender", "recipient", "body", "media", "delivery state"),
        candidate_kinds=CANDIDATE_KINDS,
        extraction_layers=("provider metadata", "bounded regex", "grounded language"),
        language_available=True,
        default_mode="review",
    ),
    _source(
        "adapter:google_calendar",
        "Google Calendar event content",
        group="Schedules and work lists",
        channel_type="calendar event",
        projection="Calendar",
        implementation="current",
        adapter_kind="calendaring-google-calendar",
        signals=(
            "calendar",
            "organizer",
            "attendees",
            "title",
            "description",
            "location",
            "times",
            "recurrence",
            "status",
        ),
        candidate_kinds=(
            "task",
            "commitment",
            "reminder",
            "list_item",
            "recurrence",
            *PEOPLE_KINDS,
        ),
        extraction_layers=("provider metadata", "member-requested grounded language"),
        language_available=True,
        default_mode="manual",
        policy_execution="planned",
    ),
    _source(
        "adapter:apple_calendar",
        "Apple Calendar event content",
        group="Schedules and work lists",
        channel_type="calendar event",
        projection="Calendar",
        implementation="current",
        adapter_kind="calendaring-apple-calendar",
        signals=(
            "calendar",
            "organizer",
            "attendees",
            "title",
            "notes",
            "location",
            "times",
            "recurrence",
            "alarms",
        ),
        candidate_kinds=(
            "task",
            "commitment",
            "reminder",
            "list_item",
            "recurrence",
            *PEOPLE_KINDS,
        ),
        extraction_layers=("provider metadata", "member-requested grounded language"),
        language_available=True,
        default_mode="manual",
        policy_execution="planned",
    ),
    _source(
        "adapter:google_tasks",
        "Google Tasks item content",
        group="Schedules and work lists",
        channel_type="task list item",
        projection="Reminders",
        implementation="current",
        adapter_kind="tasks-google-tasks",
        signals=(
            "list",
            "title",
            "notes",
            "date",
            "status",
            "parent",
            "assignment source",
            "links",
        ),
        candidate_kinds=("calendar_event", "commitment", "recurrence", *PEOPLE_KINDS),
        extraction_layers=("provider metadata", "member-requested grounded language"),
        language_available=True,
        default_mode="manual",
        policy_execution="planned",
    ),
    _source(
        "adapter:apple_reminders",
        "Apple Reminders item content",
        group="Schedules and work lists",
        channel_type="reminder/list item",
        projection="Reminders",
        implementation="current",
        adapter_kind="reminders-apple-eventkit",
        signals=(
            "list",
            "title",
            "notes",
            "URL",
            "start",
            "due",
            "priority",
            "alarms",
            "geofence",
            "recurrence",
            "completion",
        ),
        candidate_kinds=("calendar_event", "commitment", "recurrence", *PEOPLE_KINDS),
        extraction_layers=("provider metadata", "member-requested grounded language"),
        language_available=True,
        default_mode="manual",
        policy_execution="planned",
    ),
    _source(
        "adapter:apple_notes",
        "Apple Notes",
        group="Notes, transcripts, and recordings",
        channel_type="note",
        projection="Notes",
        implementation="current",
        adapter_kind="knowledge-apple-notes",
        signals=(
            "folder",
            "title",
            "body",
            "checklist text",
            "attachments",
            "modified time",
            "sharing grant",
        ),
        candidate_kinds=CANDIDATE_KINDS,
        extraction_layers=("provider metadata", "grounded language"),
        language_available=True,
        default_mode="review",
    ),
    *(
        _source(
            source_id,
            label,
            group="Notes, transcripts, and recordings",
            channel_type=channel_type,
            projection="Notes",
            implementation="planned",
            signals=signals,
            candidate_kinds=CANDIDATE_KINDS,
            extraction_layers=("provider metadata", "grounded language"),
            language_available=True,
            default_mode="review",
        )
        for source_id, label, channel_type, signals in (
            (
                "adapter:apple_voice_memos",
                "Apple Voice Memos",
                "recording/transcript",
                ("recording metadata", "transcript", "speaker labels", "timestamps"),
            ),
            (
                "adapter:obsidian",
                "Obsidian vault",
                "Markdown note",
                (
                    "vault",
                    "path",
                    "frontmatter",
                    "body",
                    "links",
                    "tags",
                    "modified time",
                ),
            ),
            (
                "adapter:evernote",
                "Evernote",
                "note",
                ("notebook", "title", "body", "tags", "attachments", "modified time"),
            ),
            (
                "adapter:otter",
                "Otter.ai",
                "meeting transcript",
                (
                    "meeting",
                    "participants",
                    "transcript",
                    "speaker labels",
                    "timestamps",
                    "highlights",
                ),
            ),
            (
                "adapter:fathom",
                "Fathom",
                "meeting transcript",
                (
                    "meeting",
                    "participants",
                    "transcript",
                    "speaker labels",
                    "timestamps",
                    "provider action items",
                ),
            ),
        )
    ),
    _source(
        "adapter:plaid",
        "Plaid transaction and recurring streams",
        group="Money, people, and environment",
        channel_type="financial observation",
        projection="Finance",
        implementation="current",
        adapter_kind="financial-plaid",
        signals=(
            "account",
            "merchant",
            "amount",
            "date",
            "category",
            "pending/posted",
            "removed",
            "recurring stream",
        ),
        candidate_kinds=("task", "reminder", "recurrence"),
        extraction_layers=("provider fields", "deterministic recurrence rules"),
        language_available=False,
        default_mode="review",
    ),
    _source(
        "adapter:home_assistant",
        "Home Assistant states and events",
        group="Money, people, and environment",
        channel_type="sensor/context signal",
        projection="Today / Reminders",
        implementation="current",
        adapter_kind="automation-home-assistant",
        signals=(
            "entity",
            "state",
            "attributes",
            "device",
            "area",
            "time",
            "presence",
            "geolocation",
        ),
        candidate_kinds=("task", "reminder", "recurrence"),
        extraction_layers=("provider fields", "reviewed deterministic rules"),
        language_available=False,
        default_mode="review",
    ),
    *(
        _source(
            source_id,
            label,
            group="Money, people, and environment",
            channel_type="contact record",
            projection="People",
            implementation=cast(Literal["current", "planned"], implementation),
            adapter_kind=adapter_kind,
            signals=(
                "provider record",
                "name",
                "identifiers",
                "organization",
                "birthday",
                "important dates",
                "groups",
                "modified time",
            ),
            candidate_kinds=PEOPLE_KINDS,
            extraction_layers=(
                "provider fields",
                "deterministic identity rules",
                "member-requested grounded language",
            ),
            language_available=True,
            default_mode="manual",
            policy_execution="planned",
        )
        for source_id, label, implementation, adapter_kind in (
            (
                "adapter:apple_contacts",
                "Apple Contacts",
                "current",
                "contacts-apple-contacts",
            ),
            (
                "adapter:google_people",
                "Google Contacts",
                "current",
                "contacts-google-people",
            ),
            (
                "adapter:microsoft_graph_contacts",
                "Microsoft Contacts",
                "current",
                "contacts-microsoft-graph-contacts",
            ),
            (
                "adapter:carddav",
                "CardDAV Contacts",
                "current",
                "contacts-carddav-contacts",
            ),
        )
    ),
)

SOURCE_BY_ID: dict[str, dict[str, Any]] = {
    str(source["id"]): source for source in SOURCE_CATALOG
}


def source_id_for_event(event: Mapping[str, Any]) -> str | None:
    """Map one typed source fact to its policy row without inspecting content."""

    event_type = str(event.get("type") or "")
    payload = event.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    if event_type == "triage.intent.submitted":
        surface = str(payload.get("source_surface") or "triage")
        source_id = f"surface:{surface}"
        return source_id if source_id in SOURCE_BY_ID else "surface:triage"
    if event_type in {"messaging.received", "messaging.sent", "telephony.sms_received"}:
        channel = str(payload.get("source_channel") or "").casefold()
        adapter = str(event.get("source_adapter") or "").casefold()
        if "gmail" in channel or "gmail" in adapter:
            return "adapter:gmail"
        if "outlook" in channel or "microsoft" in channel or "graph" in adapter:
            return "adapter:microsoft_graph_mail"
        if "imessage" in channel or "bluebubbles" in adapter:
            return "adapter:bluebubbles"
        if "whatsapp" in channel or "whatsapp" in adapter:
            return "adapter:twilio_whatsapp"
        if event_type == "telephony.sms_received" or "sms" in channel:
            return "adapter:twilio_sms"
        return (
            "adapter:generic_messaging" if event_type.startswith("messaging.") else None
        )
    if event_type.startswith("calendar."):
        identity = " ".join(
            (
                str(event.get("source_adapter") or ""),
                str(payload.get("provider") or ""),
            )
        ).casefold()
        return (
            "adapter:apple_calendar"
            if "apple" in identity or "eventkit" in identity
            else "adapter:google_calendar"
        )
    if event_type.startswith(("list.", "list_item.")):
        identity = " ".join(
            (
                str(event.get("source_adapter") or ""),
                str(payload.get("provider") or ""),
            )
        ).casefold()
        return (
            "adapter:apple_reminders"
            if "apple" in identity or "eventkit" in identity
            else "adapter:google_tasks"
        )
    if event_type.startswith("contact."):
        identity = " ".join(
            (
                str(event.get("source_adapter") or ""),
                str(payload.get("provider") or ""),
            )
        ).casefold()
        for token, source_id in (
            ("apple", "adapter:apple_contacts"),
            ("google", "adapter:google_people"),
            ("microsoft", "adapter:microsoft_graph_contacts"),
            ("graph", "adapter:microsoft_graph_contacts"),
            ("carddav", "adapter:carddav"),
        ):
            if token in identity:
                return source_id
        return None
    if event_type.startswith(("knowledge.", "member_knowledge.")):
        source_kind = str(payload.get("source_kind") or "").casefold()
        for token, source_id in (
            ("apple_note", "adapter:apple_notes"),
            ("voice", "adapter:apple_voice_memos"),
            ("obsidian", "adapter:obsidian"),
            ("evernote", "adapter:evernote"),
            ("otter", "adapter:otter"),
            ("fathom", "adapter:fathom"),
        ):
            if token in source_kind:
                return source_id
        return "adapter:apple_notes"
    if event_type.startswith("money_flow."):
        return "adapter:plaid"
    if event_type.startswith("context."):
        return "adapter:home_assistant"
    return None


def default_policy(source_id: str) -> dict[str, Any]:
    source = SOURCE_BY_ID.get(source_id)
    if source is None:
        return {
            "source_id": source_id,
            "mode": "review",
            "language_enabled": False,
            "candidate_kinds": list(CANDIDATE_KINDS),
            "locked": True,
            "known_source": False,
        }
    return {
        "source_id": source_id,
        "mode": source["default_mode"],
        "language_enabled": bool(source["language_available"]),
        "candidate_kinds": list(source["candidate_kinds"]),
        "locked": bool(source["locked"]),
        "known_source": True,
    }


def validate_policy_override(
    source_id: str,
    *,
    mode: str,
    language_enabled: bool,
    candidate_kinds: list[str],
) -> dict[str, Any]:
    source = SOURCE_BY_ID.get(source_id)
    if source is None:
        raise ValueError("candidate source is not in the reviewed catalog")
    if source["locked"]:
        raise ValueError("explicit member entry points cannot be disabled")
    if mode not in {"review", "manual", "quiet"}:
        raise ValueError("candidate mode must be review, manual, or quiet")
    if not isinstance(language_enabled, bool):
        raise ValueError("language_enabled must be true or false")
    if language_enabled and not source["language_available"]:
        raise ValueError("this source has no language extraction layer")
    selected = list(dict.fromkeys(str(value) for value in candidate_kinds))
    if not selected:
        raise ValueError("select at least one candidate kind")
    if set(selected) - set(source["candidate_kinds"]):
        raise ValueError("candidate kind is not supported by this source")
    return {
        "mode": mode,
        "language_enabled": language_enabled,
        "candidate_kinds": selected,
    }


def effective_policy(
    source_id: str, override: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    value = default_policy(source_id)
    if override and value["known_source"] and not value["locked"]:
        normalized = validate_policy_override(
            source_id,
            mode=str(override.get("mode") or value["mode"]),
            language_enabled=bool(
                override.get("language_enabled", value["language_enabled"])
            ),
            candidate_kinds=list(
                override.get("candidate_kinds") or value["candidate_kinds"]
            ),
        )
        value.update(normalized)
        value["overridden"] = True
    else:
        value["overridden"] = False
    return value


def filter_candidates(candidates: list[Any], policy: Mapping[str, Any]) -> list[Any]:
    if policy.get("mode") != "review":
        return []
    allowed = set(policy.get("candidate_kinds") or [])
    return [
        candidate
        for candidate in candidates
        if str(
            getattr(candidate, "target_kind", None)
            or (candidate.get("target_kind") if isinstance(candidate, Mapping) else "")
        )
        in allowed
    ]


__all__ = [
    "CANDIDATE_KINDS",
    "SOURCE_BY_ID",
    "SOURCE_CATALOG",
    "default_policy",
    "effective_policy",
    "filter_candidates",
    "source_id_for_event",
    "validate_policy_override",
]
