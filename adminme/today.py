"""Deterministic Daily Brief composition for member and OpenClaw surfaces.

Today is a read-time synthesis of authorized projections. It never copies the
rows into a second source of truth and never asks a model to decide what a
member should do. Natural-language fields may already have passed through the
pinned OpenClaw extraction seam, but ordering, inclusion, actions, and summary
counts here are pure functions of typed state plus an explicit clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


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
    return {
        "id": row["calendar_event_id"],
        "kind": "calendar_event",
        "title": row.get("summary") or row.get("title_redacted") or "Busy",
        "start_at": row.get("start_at"),
        "end_at": row.get("end_at"),
        "all_day": bool(row.get("all_day")),
        "due": row.get("start_at"),
        "time_label": "All day"
        if row.get("all_day")
        else _time_label(start, end, zone),
        "status": state,
        "context": row.get("location") or row.get("calendar_name") or "calendar",
        "micro_step": (
            "Wrap up and record whether you attended."
            if state == "awaiting_outcome"
            else "Get only what you need for this event ready."
        ),
        "actions": actions,
        "last_event_id": row.get("last_event_id"),
        "evidence_event_id": row.get("outcome_event_id") or row.get("last_event_id"),
        "provider": row.get("calendar_source"),
        "external_event_id": row.get("external_uid"),
        "linked_item_kind": row.get("linked_item_kind"),
        "linked_item_id": row.get("linked_item_id"),
        "completion_policy": row.get("completion_policy") or "manual",
        "_sort": start or datetime.max.replace(tzinfo=timezone.utc),
    }


def _actionable_message(row: dict[str, Any]) -> bool:
    urgency = str(row.get("response_urgency") or "").casefold()
    classification = str(row.get("classification") or "").casefold()
    return (
        row.get("direction") == "inbound"
        and not bool(row.get("auto_handled"))
        and classification != "noise"
        and bool(
            row.get("suggested_action")
            or urgency in {"today", "urgent", "high", "critical"}
        )
    )


def _message_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["interaction_id"],
        "kind": "message",
        "title": row.get("subject") or row.get("summary") or "Message to review",
        "status": row.get("response_urgency") or "needs_review",
        "context": row.get("channel_specific") or row.get("channel_family"),
        "micro_step": row.get("suggested_action")
        or "Open it and decide whether to reply.",
        "actions": ["open_chat", "evidence"],
        "last_event_id": row.get("last_event_id"),
        "evidence_event_id": row.get("last_event_id"),
        "occurred_at": row.get("occurred_at"),
    }


def _question_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["proposal_id"],
        "kind": "clarifying_question",
        "title": row.get("title") or "A household detail needs your decision",
        "status": "waiting_for_you",
        "context": str(row.get("target_kind") or "household").replace("_", " "),
        "micro_step": "Check the proposed fact, correct it if needed, then confirm or dismiss.",
        "actions": ["open_triage", "evidence"],
        "last_event_id": row.get("last_event_id"),
        "evidence_event_id": row.get("source_event_id") or row.get("last_event_id"),
    }


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

    messages = [_message_item(row) for row in interactions if _actionable_message(row)]
    questions = [_question_item(row) for row in triage_items]

    next_event = next(
        (item for item in schedule if item["status"] in {"upcoming", "in_progress"}),
        None,
    )
    overdue = sum(
        1
        for item in day_work
        if (due := _parse_time(item.get("due"))) is not None
        and due < as_of
        and not (item.get("kind") == "routine" and item.get("status") == "in_progress")
    )
    stats = {
        "events": len(schedule),
        "moves": len(day_work),
        "messages": len(messages),
        "questions": len(questions),
        "overdue": overdue,
    }
    clauses = []
    if stats["events"]:
        clauses.append(
            f"{stats['events']} timed event{'s' if stats['events'] != 1 else ''}"
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
    add([item for item in schedule if item["status"] in {"in_progress", "upcoming"}])
    add(day_work[1:])
    add(questions)
    add(messages)
    add([item for item in schedule if item["status"] == "awaiting_outcome"])

    return {
        "contract": "deterministic-daily-brief-v1",
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
    }


__all__ = ["compose_daily_brief"]
