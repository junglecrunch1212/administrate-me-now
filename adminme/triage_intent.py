"""Deterministic member-intent feature extraction and candidate eligibility.

This module deliberately contains no model call and no provider routing.  It
recognizes only bounded linguistic forms, preserves exact source spans, and
returns typed *proposal* candidates.  Ambiguous input remains durable in
Triage for a person to interpret.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from adminme.events.schemas.triage import TriageTargetKind


EXTRACTOR_ID = "deterministic-intent-features-v1"

CANDIDATE_FIELD_ALLOWLISTS: dict[str, frozenset[str]] = {
    "task": frozenset({"description", "due", "energy", "effort_min"}),
    "commitment": frozenset(
        {
            "kind",
            "owed_to_party_id",
            "due",
            "urgency",
            "source_interaction_id",
            "source_message_preview",
        }
    ),
    "calendar_event": frozenset(
        {"start", "end", "all_day", "event_type", "location"}
    ),
    "reminder": frozenset({"due", "notes", "priority"}),
    "list": frozenset({"notes"}),
    "list_item": frozenset({"due", "notes", "priority"}),
    "recurrence": frozenset(
        {
            "recurrence_text",
            "rrule",
            "next_occurrence",
            "kind",
            "lead_time_days",
            "trackable",
            "linked_kind",
            "linked_id",
            "notes",
        }
    ),
    "party_identifier": frozenset(
        {
            "surviving_party_id",
            "candidate_kind",
            "candidate_value",
            "candidate_value_normalized",
        }
    ),
    "party_candidate": frozenset(
        {
            "kind",
            "display_name",
            "sort_name",
            "nickname",
            "organization_name",
            "job_title",
            "identifier_kind",
            "identifier_value",
            "identifier_value_normalized",
        }
    ),
    "party_relationship": frozenset(
        {"party_a", "party_b", "label", "direction", "since"}
    ),
    "relationship_fact": frozenset(
        {"party_id", "kind", "label", "value", "effective_date", "evidence_quote"}
    ),
}


@dataclass(frozen=True)
class IntentCandidate:
    candidate_key: str
    target_kind: TriageTargetKind
    title: str
    fields: dict[str, Any]
    evidence_spans: list[dict[str, Any]]
    confidence: float | None
    reasons: list[str]
    eligibility_rule_ids: list[str]

    def feature_payload(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "target_kind": self.target_kind,
            "title": self.title,
            "fields": self.fields,
            "evidence_spans": self.evidence_spans,
            "confidence": self.confidence,
            "reasons": self.reasons,
        }


_INTENT_START = (
    r"(?:remind\s+me|reminder|every\s+(?:day|weekday|week|month|year|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"daily|weekly|monthly|yearly|annually|i\s+(?:promised|owe|committed)|"
    r"i\s+will\s+get\s+back\s+to|meeting|appointment|calendar\s+event|"
    r"event\s+on|create|make|start|add|call|email|send|finish|buy|pick\s+up|"
    r"pay|book|schedule|submit|clean|fix|prepare|review|do|take|bring|return|"
    r"(?:their|his|her)\s+(?:birthday|anniversary)|[^,.;!?]{1,80}\s+works\s+at)"
)
_CLAUSE_BREAK = re.compile(
    rf"(?:\n+|;|\b(?:and\s+also|also)\b|"
    rf",\s*(?:and\s+)?(?={_INTENT_START})|"
    rf"\s+\band\s+(?={_INTENT_START})|"
    rf"(?<=[.!?])\s+(?={_INTENT_START}))",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-2]\d:[0-5]\d(?::[0-5]\d)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
_RECURRENCE = re.compile(
    r"\b(every\s+(?:day|weekday|week|month|year|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"daily|weekly|monthly|yearly|annually)\b",
    re.IGNORECASE,
)


def _clauses(text: str) -> list[tuple[str, int, int]]:
    values: list[tuple[str, int, int]] = []
    cursor = 0
    for match in _CLAUSE_BREAK.finditer(text):
        raw = text[cursor : match.start()]
        stripped = raw.strip(" ,.-")
        if stripped:
            start = cursor + raw.find(stripped)
            values.append((stripped, start, start + len(stripped)))
        cursor = match.end()
    raw = text[cursor:]
    stripped = raw.strip(" ,.-")
    if stripped:
        start = cursor + raw.find(stripped)
        values.append((stripped, start, start + len(stripped)))
    return values or [(text.strip(), 0, len(text.strip()))]


def _kind_for(clause: str, hint: TriageTargetKind | None) -> tuple[str | None, str]:
    lower = clause.casefold()
    if hint is not None:
        return hint, "member_kind_hint_v1"
    if _RECURRENCE.search(clause):
        return "recurrence", "explicit_recurrence_language_v1"
    if re.search(r"^\s*(create|make|start)\b.{0,120}\blist\b", lower):
        return "list", "explicit_list_creation_v1"
    if re.search(r"\badd\b.+\bto\b.+\blist\b", lower):
        return "list_item", "explicit_list_item_v1"
    if re.search(r"\b(remind me|reminder)\b", lower):
        return "reminder", "explicit_reminder_v1"
    if re.search(r"\b(meeting|appointment|calendar event|event on)\b", lower):
        return "calendar_event", "explicit_calendar_language_v1"
    if re.search(r"\b(i promised|i owe|i committed|i will get back to)\b", lower):
        return "commitment", "explicit_commitment_language_v1"
    if re.search(
        r"\b(birthday|anniversary|works at|employer|likes|prefers|address is)\b",
        lower,
    ):
        return "relationship_fact", "explicit_relationship_fact_v1"
    if re.search(
        r"\b(call|email|send|finish|buy|pick up|pay|book|schedule|submit|"
        r"clean|fix|prepare|review|do|make|take|bring|return)\b",
        lower,
    ):
        return "task", "explicit_action_verb_v1"
    return None, "no_supported_intent_v1"


def _recurrence_fields(
    clause: str, submitted_at: datetime
) -> tuple[dict[str, Any], str | None]:
    match = _RECURRENCE.search(clause)
    if match is None:
        return {}, None
    phrase = match.group(1).casefold()
    by_day = {
        "monday": ("MO", 0),
        "tuesday": ("TU", 1),
        "wednesday": ("WE", 2),
        "thursday": ("TH", 3),
        "friday": ("FR", 4),
        "saturday": ("SA", 5),
        "sunday": ("SU", 6),
    }
    base = submitted_at.astimezone(timezone.utc)
    if phrase in {"daily", "every day"}:
        rule = "FREQ=DAILY"
        next_at = base + timedelta(days=1)
    elif phrase == "every weekday":
        rule = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
        next_at = base + timedelta(days=1)
        while next_at.weekday() > 4:
            next_at += timedelta(days=1)
    elif phrase in {"weekly", "every week"}:
        rule = "FREQ=WEEKLY"
        next_at = base + timedelta(days=7)
    elif phrase in {"monthly", "every month"}:
        rule = "FREQ=MONTHLY"
        next_at = base + timedelta(days=28)
    elif phrase in {"yearly", "annually", "every year"}:
        rule = "FREQ=YEARLY"
        next_at = base + timedelta(days=365)
    else:
        day_name = next((day for day in by_day if day in phrase), None)
        if day_name is None:
            return {}, phrase
        token, weekday = by_day[day_name]
        delta = (weekday - base.weekday()) % 7 or 7
        rule = f"FREQ=WEEKLY;BYDAY={token}"
        next_at = base + timedelta(days=delta)
    return {
        "recurrence_text": match.group(1),
        "rrule": rule,
        "next_occurrence": next_at.isoformat(),
        "kind": "household_rhythm",
        "lead_time_days": 0,
        "trackable": False,
        "notes": clause,
    }, match.group(1)


def _fields_for(
    kind: str, clause: str, submitted_at: datetime
) -> dict[str, Any]:
    dates = _ISO_DATE.findall(clause)
    if kind == "recurrence":
        fields, _ = _recurrence_fields(clause, submitted_at)
        return fields
    if kind == "task":
        return {"description": clause, **({"due": dates[0]} if dates else {})}
    if kind == "commitment":
        return {
            "kind": "other",
            "source_message_preview": clause,
            **({"due": dates[0]} if dates else {}),
        }
    if kind == "calendar_event":
        return {
            "event_type": "busy",
            **({"start": dates[0]} if dates else {}),
            **({"end": dates[1]} if len(dates) > 1 else {}),
        }
    if kind in {"reminder", "list_item"}:
        return {"notes": clause, **({"due": dates[0]} if dates else {})}
    if kind == "list":
        return {"notes": clause}
    if kind == "relationship_fact":
        lower = clause.casefold()
        fact_kind = (
            "birthday"
            if "birthday" in lower
            else "important_date"
            if "anniversary" in lower
            else "employer"
            if "works at" in lower or "employer" in lower
            else "address"
            if "address is" in lower
            else "preference"
        )
        return {
            "kind": fact_kind,
            "value": clause,
            "evidence_quote": clause,
            **({"effective_date": dates[0]} if dates else {}),
        }
    return {}


def extract_intent_candidates(
    text: str,
    *,
    submitted_at: datetime,
    kind_hint: TriageTargetKind | None = None,
) -> list[IntentCandidate]:
    """Return bounded typed features; confidence never controls eligibility."""

    candidates: list[IntentCandidate] = []
    for index, (clause, start, end) in enumerate(_clauses(text)[:10], start=1):
        kind, rule = _kind_for(clause, kind_hint)
        if kind is None:
            continue
        fields = _fields_for(kind, clause, submitted_at)
        allowlist = CANDIDATE_FIELD_ALLOWLISTS[kind]
        fields = {key: value for key, value in fields.items() if key in allowlist}
        candidates.append(
            IntentCandidate(
                candidate_key=f"candidate-{index}",
                target_kind=kind,  # type: ignore[arg-type]
                title=clause[:1000],
                fields=fields,
                evidence_spans=[{"field": "title", "start": start, "end": end}],
                confidence=None,
                reasons=[rule],
                eligibility_rule_ids=[rule, f"candidate_fields_{kind}_v1"],
            )
        )
    return candidates


__all__ = [
    "CANDIDATE_FIELD_ALLOWLISTS",
    "EXTRACTOR_ID",
    "IntentCandidate",
    "extract_intent_candidates",
]
