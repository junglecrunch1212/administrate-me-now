"""Shared deterministic contract for Triage property enrichment.

The browser renders this contract and the API derives its candidate-field
allowlist from the same object.  It is intentionally UI metadata rather than a
model decision: target kinds, field types, options, and provider limitations are
reviewed code.
"""

from __future__ import annotations

from typing import Any


TRIAGE_ENRICHMENT_CONTRACT: dict[str, Any] = {
    "version": "triage-enrichment-v3",
    "targets": {
        "task": {
            "label": "Task",
            "destination_surface": "reminders",
            "aliases": ["due", "description", "effort_min"],
            "fields": [
                {"name": "due_date", "label": "Due", "control": "date_time"},
                {
                    "name": "available_from",
                    "label": "Available from",
                    "control": "date_time",
                },
                {
                    "name": "energy",
                    "label": "Energy needed",
                    "control": "select",
                    "options": ["low", "medium", "high"],
                },
                {
                    "name": "effort",
                    "label": "Effort",
                    "control": "select",
                    "options": ["tiny", "small", "medium", "large"],
                },
                {
                    "name": "item_type",
                    "label": "Work type",
                    "control": "select",
                    "default": "task",
                    "options": [
                        "task",
                        "purchase",
                        "appointment",
                        "research",
                        "decision",
                        "chore",
                        "maintenance",
                    ],
                },
                {
                    "name": "domain",
                    "label": "Area",
                    "control": "text",
                    "default": "tasks",
                },
                {
                    "name": "micro_script",
                    "label": "First tiny step",
                    "control": "textarea",
                },
                {"name": "notes", "label": "Notes", "control": "textarea"},
            ],
            "capability_note": (
                "This creates canonical household work. It appears in Reminders and "
                "Today when active; it is not silently copied to a provider."
            ),
        },
        "commitment": {
            "label": "Commitment",
            "destination_surface": "today",
            "aliases": ["due_at", "kind"],
            "fields": [
                {
                    "name": "owed_to_party_id",
                    "label": "Owed to",
                    "control": "party",
                },
                {"name": "due", "label": "Due", "control": "date_time"},
                {
                    "name": "urgency",
                    "label": "Urgency",
                    "control": "select",
                    "options": ["today", "this_week", "later", "none"],
                },
            ],
        },
        "calendar_event": {
            "label": "Calendar event",
            "destination_surface": "calendar",
            "fields": [
                {"name": "all_day", "label": "All-day", "control": "checkbox"},
                {"name": "start", "label": "Starts", "control": "date_time"},
                {"name": "end", "label": "Ends", "control": "date_time"},
                {"name": "location", "label": "Location", "control": "text"},
                {
                    "name": "availability",
                    "label": "Shows as",
                    "control": "select",
                    "options": ["busy", "free", "tentative"],
                },
                {
                    "name": "description",
                    "label": "Notes",
                    "control": "textarea",
                },
            ],
        },
        "reminder": {
            "label": "Provider reminder",
            "destination_surface": "reminders",
            "fields": [
                {"name": "due", "label": "Date and time", "control": "date_time"},
                {
                    "name": "start",
                    "label": "Available from",
                    "control": "date_time",
                    "provider": "apple_reminders",
                },
                {
                    "name": "priority",
                    "label": "Priority",
                    "control": "priority",
                    "provider": "apple_reminders",
                },
                {
                    "name": "recurrence_rules",
                    "label": "Repeat",
                    "control": "recurrence",
                    "provider": "apple_reminders",
                },
                {
                    "name": "alarms",
                    "label": "Early reminder",
                    "control": "alarm_offset",
                    "provider": "apple_reminders",
                },
                {
                    "name": "url",
                    "label": "URL",
                    "control": "url",
                    "provider": "apple_reminders",
                },
                {"name": "notes", "label": "Notes", "control": "textarea"},
            ],
        },
        "list_item": {
            "label": "Provider list item",
            "destination_surface": "reminders",
            "fields_from": "reminder",
        },
        "list": {
            "label": "Reminder list",
            "destination_surface": "reminders",
            "fields": [
                {"name": "color", "label": "Color", "control": "color"},
            ],
        },
        "recurrence": {
            "label": "Household rhythm",
            "destination_surface": "today",
            "aliases": ["recurrence_text", "linked_kind", "linked_id", "kind"],
            "fields": [
                {"name": "rrule", "label": "Repeat", "control": "recurrence"},
                {
                    "name": "next_occurrence",
                    "label": "Next occurrence",
                    "control": "date_time",
                },
                {
                    "name": "lead_time_days",
                    "label": "Preparation days",
                    "control": "number",
                    "minimum": 0,
                    "maximum": 365,
                },
                {
                    "name": "trackable",
                    "label": "Track completion",
                    "control": "checkbox",
                },
                {"name": "notes", "label": "Notes", "control": "textarea"},
            ],
        },
        "party_identifier": {
            "label": "Person endpoint",
            "destination_surface": "people",
            "fields": [
                {
                    "name": "surviving_party_id",
                    "label": "Person",
                    "control": "party",
                },
                {
                    "name": "candidate_kind",
                    "label": "Endpoint type",
                    "control": "select",
                    "options": ["email", "phone", "handle"],
                },
                {"name": "candidate_value", "label": "Endpoint", "control": "text"},
                {
                    "name": "candidate_value_normalized",
                    "label": "Normalized endpoint",
                    "control": "text",
                },
            ],
        },
        "party_candidate": {
            "label": "Person or organization",
            "destination_surface": "people",
            "fields": [
                {
                    "name": "kind",
                    "label": "Kind",
                    "control": "select",
                    "options": ["person", "organization"],
                },
                {"name": "display_name", "label": "Name", "control": "text"},
                {
                    "name": "identifier_kind",
                    "label": "Endpoint type",
                    "control": "select",
                    "options": ["email", "phone", "handle"],
                },
                {"name": "identifier_value", "label": "Endpoint", "control": "text"},
                {
                    "name": "identifier_value_normalized",
                    "label": "Normalized endpoint",
                    "control": "text",
                },
            ],
        },
        "party_relationship": {
            "label": "Relationship",
            "destination_surface": "people",
            "fields": [
                {"name": "party_a", "label": "First person", "control": "party"},
                {"name": "party_b", "label": "Second person", "control": "party"},
                {"name": "label", "label": "Relationship", "control": "text"},
                {
                    "name": "direction",
                    "label": "Direction",
                    "control": "select",
                    "options": ["mutual", "a_to_b", "b_to_a"],
                },
                {"name": "since", "label": "Since", "control": "date"},
            ],
        },
        "relationship_fact": {
            "label": "Person fact",
            "destination_surface": "people",
            "fields": [
                {"name": "party_id", "label": "Person", "control": "party"},
                {
                    "name": "kind",
                    "label": "Fact type",
                    "control": "select",
                    "options": [
                        "birthday",
                        "important_date",
                        "employer",
                        "role",
                        "preference",
                        "address",
                        "context",
                    ],
                },
                {"name": "value", "label": "Confirmed value", "control": "text"},
                {"name": "label", "label": "Label", "control": "text"},
                {
                    "name": "effective_date",
                    "label": "Effective date",
                    "control": "date",
                },
            ],
        },
    },
    "provider_notes": {
        "apple_reminders": (
            "Apple EventKit supports due/start components, priority, URL, alarms, "
            "recurrence, and geofences. Tags, sections, attachments, templates, and "
            "message-person triggers are not exposed by this bridge."
        ),
        "google_tasks": (
            "Google Tasks API supports title, notes, hierarchy, order, and a scheduled "
            "date. Its API discards due time and exposes no priority, alarm, recurrence, "
            "or geofence fields."
        ),
    },
}


def target_contract(target_kind: str) -> dict[str, Any]:
    target = dict(TRIAGE_ENRICHMENT_CONTRACT["targets"].get(target_kind) or {})
    inherited = target.get("fields_from")
    if inherited:
        target["fields"] = list(
            TRIAGE_ENRICHMENT_CONTRACT["targets"][inherited]["fields"]
        )
    return target


def candidate_update_fields(
    target_kind: str, *, provider: str | None = None
) -> frozenset[str]:
    target = target_contract(target_kind)
    return frozenset(
        [
            str(field["name"])
            for field in target.get("fields", [])
            if provider is None or field.get("provider") in {None, provider}
        ]
        + [str(name) for name in target.get("aliases", [])]
    )


def triage_destination(item: dict[str, Any]) -> dict[str, Any]:
    """Return the stable member destination for a reviewed candidate.

    The mapping belongs beside the enrichment contract so the write response
    and the refreshed Triage read model cannot drift.  It does not imply that
    distinct domain records share a schema or lifecycle.
    """

    proposal_id = str(item["proposal_id"])
    target = str(item.get("target_kind") or "")
    if target == "task":
        record_id = f"triage_task_{proposal_id}"
        return {
            "surface": "reminders",
            "href": f"/member/reminders?item=task%3A{record_id}",
            "record_kind": "task",
            "record_id": record_id,
            "label": "Reminders",
        }
    surface_by_kind = {
        "commitment": "today",
        "calendar_event": "calendar",
        "reminder": "reminders",
        "list": "reminders",
        "list_item": "reminders",
        "recurrence": "today",
        "party_identifier": "people",
        "party_candidate": "people",
        "party_relationship": "people",
        "relationship_fact": "people",
    }
    surface = surface_by_kind.get(target, "triage")
    return {
        "surface": surface,
        "href": f"/member/{surface}",
        "record_kind": target,
        "record_id": proposal_id,
        "label": surface.title(),
    }


__all__ = [
    "TRIAGE_ENRICHMENT_CONTRACT",
    "candidate_update_fields",
    "target_contract",
    "triage_destination",
]
