"""Deterministic member-intent feature extraction and candidate eligibility.

This module deliberately contains no model call and no provider routing.  It
recognizes only bounded linguistic forms, preserves exact source spans, and
returns typed *proposal* candidates.  Ambiguous input remains durable in
Triage for a person to interpret.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from adminme.events.schemas.triage import TriageTargetKind
from adminme.message_context import MessageContextPack


EXTRACTOR_ID = "deterministic-intent-features-v1"
GROUNDED_NLP_EXTRACTOR_ID = "grounded-nlp-intent-features-v1"
MESSAGE_FALLBACK_EXTRACTOR_ID = "message-high-signal-intent-features-v1"
CONTEXTUAL_NLP_EXTRACTOR_ID = "privacy-scoped-message-context-v2"

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
    "calendar_event": frozenset({"start", "end", "all_day", "event_type", "location"}),
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
    interpretation: dict[str, Any] | None = None

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


def _fields_for(kind: str, clause: str, submitted_at: datetime) -> dict[str, Any]:
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
    for index, (clause, start, end) in enumerate(_clauses(text)[:20], start=1):
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


def _bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    return compact[:limit] or None


def extract_grounded_context_candidates(
    context_pack: MessageContextPack,
    raw_candidates: Any,
    *,
    source_rule_id: str = "private_member_message_context_v2",
) -> list[IntentCandidate]:
    """Admit conversation-aware model interpretations under fixed controls.

    The language seam may identify who an ask appears to concern and whether
    the words say it is still open.  Code retains only open/unknown asks that
    concern the member, the member's group, or require human ownership review.
    Every primary quote must occur verbatim in a same-thread message.  Related
    messages may explain status, but cannot create a candidate for this anchor.
    """

    if not isinstance(raw_candidates, list):
        return []
    aliases = context_pack.alias_map()
    admitted: list[IntentCandidate] = []
    for raw in raw_candidates[:20]:
        if not isinstance(raw, dict):
            continue
        alias = str(raw.get("evidence_source_id") or "")
        source = aliases.get(alias)
        if (
            source is None
            or source.relation != "thread"
            or not source.authored_text_reliable
        ):
            continue
        if raw.get("target_kind") == "commitment" and source.direction != "outbound":
            continue
        owner = str(raw.get("action_owner") or "unknown")
        lifecycle = str(raw.get("lifecycle_state") or "unknown")
        if owner not in {"member", "group_including_member", "unknown"}:
            continue
        if lifecycle not in {"open", "unknown"}:
            continue
        bases = extract_grounded_nlp_candidates(
            source.source_text(),
            [raw],
            source_rule_id=source_rule_id,
        )
        if not bases:
            continue
        base = bases[0]
        quote = base.title
        identity = hashlib.sha256(
            f"{context_pack.thread_key}|{base.target_kind}|{quote}".encode("utf-8")
        ).hexdigest()[:16]
        requested_related = raw.get("related_source_ids") or []
        related_source_event_ids = [
            aliases[value].source_event_id
            for value in requested_related[:10]
            if isinstance(value, str)
            and value in aliases
            and aliases[value].relation == "related"
        ]
        unresolved = [
            value
            for raw_value in (raw.get("unresolved_questions") or [])[:8]
            if (value := _bounded_text(raw_value, 240)) is not None
        ]
        interpretation = {
            "contract": "triage-context-interpretation-v2",
            "context_fingerprint": context_pack.fingerprint,
            "thread_key": context_pack.thread_key,
            "anchor_event_id": context_pack.anchor_event_id,
            "compact_summary": _bounded_text(raw.get("compact_summary"), 160)
            or quote[:160],
            "case_summary": _bounded_text(raw.get("case_summary"), 600),
            "why_member": _bounded_text(raw.get("why_member"), 240),
            "next_step": _bounded_text(raw.get("next_step"), 240),
            "thread_state": (
                str(raw.get("thread_state"))
                if raw.get("thread_state")
                in {
                    "new",
                    "in_progress",
                    "waiting_on_member",
                    "waiting_on_other",
                    "blocked",
                    "unclear",
                }
                else "unclear"
            ),
            "action_owner": owner,
            "lifecycle_state": lifecycle,
            "unresolved_questions": unresolved,
            "evidence_refs": [
                {
                    "source_event_id": source.source_event_id,
                    "quote": quote,
                }
            ],
            "context_source_event_ids": [
                message.source_event_id for message in context_pack.messages
            ],
            "context_sources": [
                {
                    "source_event_id": message.source_event_id,
                    "relation": message.relation,
                    "reason_codes": list(message.relation_reason_codes),
                    "relationship_source_event_ids": list(
                        message.relationship_source_event_ids
                    ),
                }
                for message in context_pack.messages
            ],
            "related_source_event_ids": list(dict.fromkeys(related_source_event_ids)),
        }
        rules = [
            *base.eligibility_rule_ids,
            "conversation_context_pack_v2",
            f"context_owner_{owner}_v1",
            f"context_lifecycle_{lifecycle}_v1",
        ]
        admitted.append(
            replace(
                base,
                candidate_key=f"context-{identity}",
                reasons=list(
                    dict.fromkeys(
                        [
                            *base.reasons,
                            "conversation_context_interpreted_v1",
                            *(
                                ["ownership_requires_member_review_v1"]
                                if owner == "unknown"
                                else []
                            ),
                            *(
                                ["lifecycle_requires_member_review_v1"]
                                if lifecycle == "unknown"
                                else []
                            ),
                        ]
                    )
                )[:20],
                eligibility_rule_ids=list(dict.fromkeys(rules))[:20],
                interpretation=interpretation,
            )
        )
    return admitted


_MESSAGE_BOILERPLATE = re.compile(
    r"\b(?:unsubscribe|view in browser|privacy policy|terms of service|"
    r"buy again|shop now|manage preferences|sent from my (?:iphone|ipad))\b|"
    r"https?://|www\.",
    re.IGNORECASE,
)
_INBOUND_DIRECTIVE = re.compile(
    r"^\s*(?:please\s+|action required\s*:?\s*|can you\s+|could you\s+|"
    r"would you\s+|need you to\s+)",
    re.IGNORECASE,
)
_INBOUND_PAYMENT_DIRECTIVE = re.compile(
    r"^\s*pay\b.{0,120}\b(?:bill|invoice|balance|premium)\b",
    re.IGNORECASE,
)
_INBOUND_DUE_OBLIGATION = re.compile(
    r"\b(?:bill|invoice|payment|balance|renewal|premium|form|document)\b"
    r".{0,120}\b(?:due|overdue|expires?|renew(?:s|al)?)\b|"
    r"\b(?:due|overdue|expires?|renew(?:s|al)?)\b"
    r".{0,120}\b(?:bill|invoice|payment|balance|premium|form|document)\b",
    re.IGNORECASE,
)
_OUTBOUND_PROMISE = re.compile(
    r"\b(?:i\s+will|i'll|i\s+promise|i\s+committed|i\s+owe)\b",
    re.IGNORECASE,
)
_INBOUND_REPLY_REQUEST = re.compile(
    r"^\s*(?:(?:please|can you|could you|would you|will you)\s+)?"
    r"(?:reply|respond|confirm|let me know)\b",
    re.IGNORECASE,
)


def is_inbound_reply_request(text: str) -> bool:
    """Identify reply-only obligations owned by commitment extraction."""

    return _INBOUND_REPLY_REQUEST.search(" ".join(text.split())) is not None


def extract_message_fallback_candidates(
    text: str,
    *,
    submitted_at: datetime,
    direction: str,
) -> list[IntentCandidate]:
    """Conservative model-loss fallback for human communication facts.

    Generic action verbs are appropriate when a member deliberately opens
    Add for review, but not across arbitrary email bodies.  This policy admits
    only an exact, bounded directive/obligation or an outbound first-person
    promise and rejects common message/marketing boilerplate.  The language
    seam remains the primary interpreter for richer wording.
    """

    candidates: list[IntentCandidate] = []
    for index, (clause, start, end) in enumerate(_clauses(text)[:20], start=1):
        compact = " ".join(clause.split())
        if not compact or len(compact) > 500 or _MESSAGE_BOILERPLATE.search(compact):
            continue
        if direction == "outbound":
            if not _OUTBOUND_PROMISE.search(compact):
                continue
            kind: TriageTargetKind = "commitment"
            rule = "outbound_first_person_promise_v1"
        else:
            # Reply obligations already have a distinct commitment pipeline.
            # Do not race it with a generic Task proposal for the same source.
            if is_inbound_reply_request(compact):
                continue
            if not (
                _INBOUND_DIRECTIVE.search(compact)
                or _INBOUND_PAYMENT_DIRECTIVE.search(compact)
                or _INBOUND_DUE_OBLIGATION.search(compact)
            ):
                continue
            kind = "task"
            rule = "inbound_explicit_directive_or_due_obligation_v1"
        # Use the original source span for every persisted value. ``compact``
        # exists only for bounded pattern matching; normalizing whitespace in
        # a candidate would break exact-source provenance.
        fields = _fields_for(kind, clause, submitted_at)
        candidates.append(
            IntentCandidate(
                candidate_key=f"message-fallback-{index}",
                target_kind=kind,
                title=clause[:1000],
                fields=fields,
                evidence_spans=[{"field": "title", "start": start, "end": end}],
                confidence=None,
                reasons=[rule],
                eligibility_rule_ids=[rule, f"candidate_fields_{kind}_v1"],
            )
        )
    return candidates


def extract_grounded_nlp_candidates(
    text: str,
    raw_candidates: Any,
    *,
    source_rule_id: str = "private_member_message_v1",
) -> list[IntentCandidate]:
    """Admit bounded model-extracted evidence into deterministic Triage.

    The model may interpret language, but it cannot choose authority or create
    household truth.  Fixed code requires an exact, contiguous source quote,
    an allowlisted target kind, and allowlisted JSON-scalar fields.  The exact
    quote becomes the review title so a generated paraphrase cannot be
    mistaken for source evidence.  Confidence remains display-only and never
    controls eligibility.
    """

    if not isinstance(raw_candidates, list):
        return []
    supported = {
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
    }
    candidates: list[IntentCandidate] = []
    for index, raw in enumerate(raw_candidates[:20], start=1):
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("target_kind") or "")
        quote = raw.get("evidence_quote")
        if kind not in supported or not isinstance(quote, str):
            continue
        quote = quote.strip()
        if not quote or len(quote) > 1000:
            continue
        start = text.find(quote)
        if start < 0:
            continue
        end = start + len(quote)
        supplied_fields = raw.get("candidate")
        fields: dict[str, Any] = {}
        if isinstance(supplied_fields, dict):
            allowlist = CANDIDATE_FIELD_ALLOWLISTS[kind]
            fields = {
                str(key): value
                for key, value in supplied_fields.items()
                if key in allowlist
                and (
                    value is None
                    or isinstance(value, (str, bool, int, float))
                    or (
                        isinstance(value, list)
                        and len(value) <= 20
                        and all(
                            item is None or isinstance(item, (str, bool, int, float))
                            for item in value
                        )
                    )
                )
            }
        # These human-readable fields must always remain verbatim source
        # evidence.  Structured dates/recurrence hints remain reviewable
        # suggestions until confirmation.
        if kind == "task":
            fields["description"] = quote
        elif kind == "commitment":
            commitment_kind = str(fields.get("kind") or "other")
            fields["kind"] = (
                commitment_kind
                if commitment_kind
                in {
                    "reply",
                    "task",
                    "appointment",
                    "payment",
                    "document_return",
                    "visit",
                    "other",
                }
                else "other"
            )
            urgency = fields.get("urgency")
            if urgency not in {"today", "this_week", "this_month", "no_rush"}:
                fields.pop("urgency", None)
            fields["source_message_preview"] = quote[:240]
        elif kind == "reminder":
            fields["notes"] = quote
        elif kind in {"list", "list_item"}:
            fields["notes"] = quote
        elif kind == "recurrence":
            fields["notes"] = quote
        elif kind == "calendar_event":
            fields.setdefault("event_type", "busy")

        confidence = raw.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = None
        elif not 0.0 <= float(confidence) <= 1.0:
            confidence = None
        reasons = [
            str(reason)[:500]
            for reason in raw.get("reasons") or []
            if isinstance(reason, str)
        ][:18]
        candidates.append(
            IntentCandidate(
                candidate_key=f"nlp-candidate-{index}",
                target_kind=kind,  # type: ignore[arg-type]
                title=quote,
                fields=fields,
                evidence_spans=[{"field": "title", "start": start, "end": end}],
                confidence=float(confidence) if confidence is not None else None,
                reasons=["exact_source_quote_grounded_v1", *reasons],
                eligibility_rule_ids=[
                    source_rule_id,
                    "exact_source_quote_grounded_v1",
                    f"candidate_fields_{kind}_v1",
                ],
            )
        )
    return candidates


def merge_intent_candidates(
    deterministic: list[IntentCandidate],
    grounded_nlp: list[IntentCandidate],
) -> list[IntentCandidate]:
    """Merge two bounded evidence sets without confidence ranking.

    Overlapping candidates of the same kind describe one review decision.
    The grounded NLP candidate is retained because it can carry normalized
    dates or recurrence hints; the deterministic rule IDs are preserved as
    additional audit evidence.
    """

    merged = list(deterministic)
    for candidate in grounded_nlp:
        overlapping_index: int | None = None
        candidate_span = candidate.evidence_spans[0]
        for index, existing in enumerate(merged):
            if existing.target_kind != candidate.target_kind:
                continue
            candidate_ref = (
                (candidate.interpretation or {}).get("evidence_refs") or [{}]
            )[0].get("source_event_id")
            existing_ref = (
                (existing.interpretation or {}).get("evidence_refs") or [{}]
            )[0].get("source_event_id")
            if candidate_ref and existing_ref and candidate_ref != existing_ref:
                continue
            if candidate_ref and not existing_ref:
                anchor = (candidate.interpretation or {}).get("anchor_event_id")
                if candidate_ref != anchor:
                    continue
            if existing_ref and not candidate_ref:
                anchor = (existing.interpretation or {}).get("anchor_event_id")
                if existing_ref != anchor:
                    continue
            existing_span = existing.evidence_spans[0]
            if (
                candidate_span["start"] < existing_span["end"]
                and existing_span["start"] < candidate_span["end"]
            ):
                overlapping_index = index
                break
        if overlapping_index is None:
            merged.append(candidate)
            continue
        existing = merged[overlapping_index]
        merged[overlapping_index] = IntentCandidate(
            candidate_key=existing.candidate_key,
            target_kind=candidate.target_kind,
            title=candidate.title,
            fields={**existing.fields, **candidate.fields},
            evidence_spans=candidate.evidence_spans,
            confidence=candidate.confidence,
            reasons=list(dict.fromkeys([*existing.reasons, *candidate.reasons]))[:20],
            eligibility_rule_ids=list(
                dict.fromkeys(
                    [
                        *existing.eligibility_rule_ids,
                        *candidate.eligibility_rule_ids,
                    ]
                )
            )[:20],
            interpretation=candidate.interpretation or existing.interpretation,
        )
    return merged[:20]


__all__ = [
    "CANDIDATE_FIELD_ALLOWLISTS",
    "CONTEXTUAL_NLP_EXTRACTOR_ID",
    "EXTRACTOR_ID",
    "GROUNDED_NLP_EXTRACTOR_ID",
    "MESSAGE_FALLBACK_EXTRACTOR_ID",
    "IntentCandidate",
    "extract_intent_candidates",
    "extract_message_fallback_candidates",
    "extract_grounded_nlp_candidates",
    "extract_grounded_context_candidates",
    "is_inbound_reply_request",
    "merge_intent_candidates",
]
