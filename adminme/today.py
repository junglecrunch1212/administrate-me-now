"""Deterministic Daily Brief composition for member and OpenClaw surfaces.

Today is a read-time synthesis of authorized projections. It never copies the
rows into a second source of truth and never asks a model to decide what a
member should do. Natural-language fields may already have passed through the
pinned OpenClaw extraction seam, but ordering, inclusion, actions, and summary
counts here are pure functions of typed state plus an explicit clock.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from adminme.comms import message_attention_decision
from adminme.surface_contracts import surface_link


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{value}T23:59:59+00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_label(value: datetime) -> str:
    return value.strftime("%A, %B %d").replace(" 0", " ")


def _daypart(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _time_label(start: datetime | None, end: datetime | None, zone: ZoneInfo) -> str:
    if start is None:
        return "Any time"
    local_start = start.astimezone(zone)
    start_label = local_start.strftime("%I:%M %p").lstrip("0")
    if end is None:
        return start_label
    local_end = end.astimezone(zone)
    end_label = local_end.strftime("%I:%M %p").lstrip("0")
    return f"{start_label}–{end_label}"


def _calendar_item(
    row: dict[str, Any], *, as_of: datetime, zone: ZoneInfo
) -> dict[str, Any]:
    start = _parse_time(row.get("start_at"))
    end = _parse_time(row.get("end_at")) or start
    outcome = row.get("recorded_outcome")
    if outcome:
        state = str(outcome)
    elif start is not None and as_of < start:
        state = "upcoming"
    elif end is not None and as_of >= end:
        state = "awaiting_outcome"
    else:
        state = "in_progress"
    actions = ["evidence"]
    if not outcome and state in {"in_progress", "awaiting_outcome"}:
        actions = ["attended", "missed", "evidence"]
    effect = str(row.get("effect") or "hard_block")
    event_id = str(row.get("calendar_event_id") or row.get("id") or "")
    return {
        "id": event_id,
        "kind": "calendar_event",
        "title": row.get("summary")
        or row.get("title")
        or row.get("title_redacted")
        or "Busy",
        "start_at": row.get("start_at"),
        "end_at": row.get("end_at"),
        "all_day": bool(row.get("all_day")),
        "due": row.get("start_at"),
        "time_label": "All day"
        if row.get("all_day")
        else _time_label(start, end, zone),
        "status": state,
        "context": row.get("location") or row.get("calendar_name") or "calendar",
        "effect": effect,
        "why": row.get("why") or [],
        "micro_step": (
            "Wrap up and record whether you attended."
            if state == "awaiting_outcome"
            else "Get only what you need for this event ready."
        ),
        "actions": actions,
        "last_event_id": row.get("last_event_id"),
        "evidence_event_id": row.get("outcome_event_id") or row.get("last_event_id"),
        "provider": row.get("calendar_source") or row.get("provider"),
        "external_event_id": row.get("external_uid")
        or row.get("external_event_id")
        or row.get("id"),
        "linked_item_kind": row.get("linked_item_kind"),
        "linked_item_id": row.get("linked_item_id"),
        "completion_policy": row.get("completion_policy") or "manual",
        "surface_link": surface_link(
            "calendar",
            object_kind="event",
            object_id=event_id,
            evidence_event_ids=[str(row.get("last_event_id") or "")],
        ),
        "_sort": start or datetime.max.replace(tzinfo=timezone.utc),
    }


def _source_event_ids(row: dict[str, Any]) -> set[str]:
    value = row.get("raw_event_ids")
    if isinstance(value, list):
        return {str(event_id) for event_id in value if event_id}
    if not isinstance(value, str) or not value:
        return set()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return set()
    return (
        {str(event_id) for event_id in parsed if event_id}
        if isinstance(parsed, list)
        else set()
    )


def _message_attention(
    row: dict[str, Any], *, as_of: datetime, triage_sources: set[str]
) -> tuple[str, dict[str, Any], int]:
    triage_count = len(_source_event_ids(row) & triage_sources)
    bucket, attention = message_attention_decision(
        row, as_of, triage_count=triage_count
    )
    return bucket, attention, triage_count


def _message_thread_key(row: dict[str, Any]) -> str:
    provider = str(row.get("provider") or row.get("channel_specific") or "unknown")
    account = str(row.get("provider_account_id") or "unknown")
    native = (
        row.get("thread_id") or row.get("native_message_id") or row["interaction_id"]
    )
    return f"{provider}:{account}:{native}"


def _message_item(
    row: dict[str, Any], *, attention: dict[str, Any], triage_count: int
) -> dict[str, Any]:
    thread_key = _message_thread_key(row)
    return {
        "id": row["interaction_id"],
        "kind": "message",
        "title": row.get("subject") or row.get("summary") or "Message to review",
        "status": row.get("response_urgency") or "needs_review",
        "context": row.get("channel_specific") or row.get("channel_family"),
        "micro_step": row.get("suggested_action")
        or "Open it and decide whether to reply.",
        "actions": ["open_comms", "evidence"],
        "last_event_id": row.get("last_event_id"),
        "evidence_event_id": row.get("last_event_id"),
        "occurred_at": row.get("occurred_at"),
        "thread_key": thread_key,
        "attention": attention,
        "triage_count": triage_count,
        "surface_link": surface_link(
            "comms",
            object_kind="thread",
            object_id=thread_key,
            evidence_event_ids=[str(row.get("last_event_id") or "")],
        ),
    }


_TRIAGE_LANE_EXPLANATIONS = {
    "security": "A security signal is waiting for an explicit member decision.",
    "time_bound": "The source includes a time boundary that may make delay costly.",
    "financial": "The source may affect household cash flow, a bill, or an account.",
    "member_promise": "The source may contain something the member agreed to do.",
    "member_priority": "A member-confirmed context rule placed this matter here.",
    "recurring_exception": "A recurring pattern changed or needs an exception decision.",
    "standard": "The candidate is still unconfirmed and cannot enter household truth on its own.",
}


def _question_item(row: dict[str, Any]) -> dict[str, Any]:
    proposal_id = str(row["proposal_id"])
    matter_id = str(row.get("context_id") or proposal_id)
    proposed_title = row.get("title") or "A household detail needs your decision"
    return {
        "id": proposal_id,
        "candidate_id": proposal_id,
        "matter_id": matter_id,
        "kind": "clarifying_question",
        "title": proposed_title,
        "candidate_title": proposed_title,
        "target_kind": row.get("target_kind"),
        "queue_lane": row.get("queue_lane") or "standard",
        "status": "waiting_for_you",
        "context": str(row.get("target_kind") or "household").replace("_", " "),
        "micro_step": "Check the proposed fact, correct it if needed, then confirm or dismiss.",
        "actions": ["open_triage", "evidence"],
        "source_event_id": row.get("source_event_id"),
        "candidate": row.get("candidate") or {},
        "interpretation": row.get("interpretation"),
        "eligibility_rule_ids": row.get("eligibility_rule_ids") or [],
        "proposed_at": row.get("proposed_at"),
        "last_event_id": row.get("last_event_id"),
        "evidence_event_id": row.get("source_event_id") or row.get("last_event_id"),
        "surface_link": surface_link(
            "triage",
            object_kind="item",
            object_id=proposal_id,
            params={"matter": matter_id, "view": "all_candidates"},
            evidence_event_ids=[
                str(row.get("source_event_id") or row.get("last_event_id") or "")
            ],
        ),
    }


def _decision_prompt(item: dict[str, Any], unresolved: list[str]) -> str:
    if unresolved:
        prompt = unresolved[0].strip()
        if prompt:
            return prompt if prompt.endswith("?") else f"{prompt}?"
    target = str(item.get("target_kind") or "household item").replace("_", " ")
    title = str(item.get("candidate_title") or item.get("title") or "this proposal")
    return f"Should AdministrateMe add this {target}: {title.rstrip('.?')}?"


def add_today_decision_context(surface: dict[str, Any]) -> None:
    """Publish decision-grade cards after source context has been authorized.

    This is a read-only presentation contract. Fixed code chooses every field,
    option, consequence, and route; any bounded language interpretation stays
    labelled review aid and never becomes household truth or authority.
    """

    brief = surface.get("brief")
    if not isinstance(brief, dict):
        return
    questions = brief.get("questions")
    if not isinstance(questions, list):
        return
    for item in questions:
        if not isinstance(item, dict):
            continue
        source = item.get("source_context")
        source = source if isinstance(source, dict) else {}
        interpretation = source.get("interpretation")
        interpretation = interpretation if isinstance(interpretation, dict) else {}
        unresolved = [
            str(value).strip()
            for value in interpretation.get("unresolved_questions") or []
            if str(value).strip()
        ]
        sender = str(source.get("sender") or "").strip()
        subject = str(source.get("subject") or "").strip()
        channel = str(source.get("channel") or "").strip()
        occurred_at = source.get("occurred_at")
        source_parts = [value for value in (channel, sender, subject) if value]
        source_label = " · ".join(source_parts) or (
            "Source details withheld"
            if source.get("state") == "withheld"
            else "Source details unavailable"
        )
        what_happened = str(
            interpretation.get("case_summary")
            or (source.get("meaning") or {}).get("summary")
            or item.get("candidate_title")
            or "A source produced a candidate for review."
        )
        why_now = str(
            interpretation.get("why_member")
            or _TRIAGE_LANE_EXPLANATIONS.get(
                str(item.get("queue_lane") or "standard"),
                _TRIAGE_LANE_EXPLANATIONS["standard"],
            )
        )
        next_step = str(
            interpretation.get("next_step")
            or "Review the source-grounded proposal, correct it if needed, then confirm or dismiss it."
        )
        prompt = _decision_prompt(item, unresolved)
        target_label = str(item.get("target_kind") or "household item").replace(
            "_", " "
        )
        item["decision_context"] = {
            "contract": "member-decision-context-v1",
            "decision_maker": "member",
            "candidate_id": item.get("candidate_id") or item.get("id"),
            "matter_id": item.get("matter_id"),
            "decision_prompt": prompt,
            "what_happened": what_happened,
            "why_now": why_now,
            "source": {
                "state": source.get("state") or "unavailable",
                "label": source_label,
                "channel": channel or None,
                "sender": sender or None,
                "subject": subject or None,
                "occurred_at": occurred_at,
            },
            "uncertainties": unresolved,
            "choices": [
                {
                    "id": "confirm",
                    "label": "Confirm",
                    "consequence": f"Create or route the reviewed {target_label} only after every governance gate passes.",
                },
                {
                    "id": "correct",
                    "label": "Correct",
                    "consequence": "Change the proposed meaning or destination before making it official.",
                },
                {
                    "id": "dismiss",
                    "label": "Dismiss",
                    "consequence": "Keep the source as evidence, create no household object, and record why it was rejected.",
                },
            ],
            "smallest_next_step": next_step,
            "evidence_available": bool((source.get("evidence") or {}).get("quote")),
            "interpretation_disclosure": (
                "bounded_language_review_aid"
                if interpretation
                else "deterministic_source_facts"
            ),
        }
        item["title"] = prompt
        item["context"] = source_label
        item["micro_step"] = next_step

    # ``compose_daily_brief`` serializes the same logical question separately
    # into Day Brief and Carousel. Source authorization and decision enrichment
    # intentionally happen afterward, so publish the already-authorized
    # presentation fields to the matching Carousel copy. The question remains
    # the single read-time authority; Carousel never reinterprets source data.
    carousel = brief.get("carousel")
    carousel_items = carousel.get("items") if isinstance(carousel, dict) else None
    if not isinstance(carousel_items, list):
        return
    questions_by_candidate = {
        str(item.get("candidate_id") or item.get("id")): item
        for item in questions
        if isinstance(item, dict) and (item.get("candidate_id") or item.get("id"))
    }
    for item in carousel_items:
        if not isinstance(item, dict) or item.get("kind") != "clarifying_question":
            continue
        question = questions_by_candidate.get(
            str(item.get("candidate_id") or item.get("id") or "")
        )
        if question is None:
            continue
        for key in (
            "source_context",
            "decision_context",
            "title",
            "context",
            "micro_step",
        ):
            if key in question:
                item[key] = deepcopy(question[key])


def _is_for_local_day(value: Any, *, end_utc: datetime) -> bool:
    parsed = _parse_time(value)
    return parsed is not None and parsed < end_utc


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def compose_daily_brief(
    *,
    member_id: str,
    timezone_name: str,
    as_of: datetime,
    what_now: dict[str, Any],
    calendar_events: list[dict[str, Any]],
    interactions: list[dict[str, Any]],
    triage_items: list[dict[str, Any]],
    calendar_guidance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose a complete Day Sheet from already-authorized rows."""

    as_of = as_of.astimezone(timezone.utc).replace(second=0, microsecond=0)
    zone = ZoneInfo(timezone_name)
    local_now = as_of.astimezone(zone)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_utc = (local_start + timedelta(days=1)).astimezone(timezone.utc)

    schedule = [_calendar_item(row, as_of=as_of, zone=zone) for row in calendar_events]
    schedule.sort(key=lambda item: (item["_sort"], item["id"]))

    ranked = [dict(item) for item in what_now.get("ranked") or []]
    day_work: list[dict[str, Any]] = []
    for index, item in enumerate(ranked):
        if (
            index < 3
            or item.get("status") == "in_progress"
            or _is_for_local_day(item.get("due"), end_utc=end_utc)
        ):
            item.setdefault("evidence_event_id", item.get("last_event_id"))
            day_work.append(item)

    questions = [_question_item(row) for row in triage_items]
    triage_sources = {
        str(item.get("source_event_id"))
        for item in triage_items
        if item.get("source_event_id")
    }
    messages = []
    for row in interactions:
        bucket, attention, triage_count = _message_attention(
            row, as_of=as_of, triage_sources=triage_sources
        )
        if bucket == "focus" and not bool(row.get("auto_handled")):
            messages.append(
                _message_item(row, attention=attention, triage_count=triage_count)
            )

    next_event = next(
        (
            item
            for item in schedule
            if not item["all_day"]
            and item["effect"] != "context"
            and item["status"] in {"upcoming", "in_progress"}
        ),
        None,
    )
    overdue = sum(
        1
        for item in day_work
        if (due := _parse_time(item.get("due"))) is not None
        and due < as_of
        and not (item.get("kind") == "routine" and item.get("status") == "in_progress")
    )
    timed_schedule = [
        item for item in schedule if not item["all_day"] and item["effect"] != "context"
    ]
    day_context = [
        item for item in schedule if item["all_day"] or item["effect"] == "context"
    ]
    stats = {
        "events": len(timed_schedule),
        "timed_events": len(timed_schedule),
        "day_context": len(day_context),
        "moves": len(day_work),
        "messages": len(messages),
        "questions": len(questions),
        "overdue": overdue,
    }
    clauses = []
    if stats["timed_events"]:
        clauses.append(
            f"{stats['timed_events']} timed event{'s' if stats['timed_events'] != 1 else ''}"
        )
    if stats["day_context"]:
        clauses.append(
            f"{stats['day_context']} day-context item{'s' if stats['day_context'] != 1 else ''}"
        )
    if stats["moves"]:
        clauses.append(
            f"{stats['moves']} planned move{'s' if stats['moves'] != 1 else ''}"
        )
    if stats["questions"]:
        clauses.append(
            f"{stats['questions']} decision{'s' if stats['questions'] != 1 else ''}"
        )
    if stats["messages"]:
        clauses.append(
            f"{stats['messages']} message{'s' if stats['messages'] != 1 else ''} to notice"
        )
    summary = (
        ", ".join(clauses) + "."
        if clauses
        else "Your day is open. Use What Now when you want one small move."
    )

    carousel: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(items: list[dict[str, Any]]) -> None:
        for item in items:
            key = (str(item.get("kind")), str(item.get("id")))
            if key in seen:
                continue
            seen.add(key)
            carousel.append(item)

    urgent_schedule = [
        item
        for item in schedule
        if not item["all_day"]
        and item["effect"] != "context"
        and (
            item["status"] == "in_progress"
            or (
                item["status"] == "upcoming"
                and item["_sort"] <= as_of + timedelta(minutes=30)
            )
        )
    ]
    add(urgent_schedule)
    add(day_work[:1])
    add(
        [
            item
            for item in timed_schedule
            if item["status"] in {"in_progress", "upcoming"}
        ]
    )
    add(day_work[1:])
    add(questions)
    add(messages)
    add([item for item in timed_schedule if item["status"] == "awaiting_outcome"])

    return {
        "contract": "deterministic-daily-brief-v2",
        "member_id": member_id,
        "date_iso": local_now.date().isoformat(),
        "date_label": _date_label(local_now),
        "timezone": timezone_name,
        "as_of": as_of.isoformat(),
        "daypart": _daypart(local_now.hour),
        "summary_text": summary,
        "stats": stats,
        "next_event": _public_item(next_event) if next_event else None,
        "schedule": [_public_item(item) for item in schedule],
        "timed_schedule": [_public_item(item) for item in timed_schedule],
        "day_context": [_public_item(item) for item in day_context],
        "plan": [_public_item(item) for item in day_work],
        "messages": messages,
        "questions": questions,
        "carousel": {
            "items": [_public_item(item) for item in carousel[:24]],
            "total": min(len(carousel), 24),
            "later_is_view_only": True,
            "recommended_item": (
                {"kind": carousel[0]["kind"], "id": carousel[0]["id"]}
                if carousel
                else None
            ),
        },
        "what_now": what_now,
        "calendar_guidance": calendar_guidance or {},
    }


__all__ = ["add_today_decision_context", "compose_daily_brief"]
