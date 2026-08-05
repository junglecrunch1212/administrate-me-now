"""RFC 5545 recurrence conversion for the PyObjC EventKit bridges.

EventKit exposes recurrence as object graphs while Google Calendar and the
canonical event log use RFC 5545 ``RRULE`` strings.  This module keeps that
translation at the adapter boundary and deliberately supports every selector
that ``EKRecurrenceRule`` makes public.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_WEEKDAYS = {"SU": 1, "MO": 2, "TU": 3, "WE": 4, "TH": 5, "FR": 6, "SA": 7}
_WEEKDAY_NAMES = {value: key for key, value in _WEEKDAYS.items()}


def parse_rrule(value: str) -> dict[str, Any]:
    """Parse one RFC 5545 RRULE into EventKit-oriented primitive values."""

    raw = value.strip()
    if raw.upper().startswith("RRULE:"):
        raw = raw.split(":", 1)[1]
    parts: dict[str, str] = {}
    for component in raw.split(";"):
        key, separator, item = component.partition("=")
        if not separator or not key or not item:
            raise ValueError(f"invalid RRULE component: {component!r}")
        parts[key.upper()] = item
    frequency = parts.get("FREQ", "").upper()
    if frequency not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
        raise ValueError(f"unsupported EventKit recurrence frequency: {frequency!r}")

    def integers(name: str) -> list[int] | None:
        return [int(item) for item in parts[name].split(",")] if name in parts else None

    weekdays: list[tuple[int, int]] | None = None
    if "BYDAY" in parts:
        weekdays = []
        for token in parts["BYDAY"].split(","):
            day_name = token[-2:].upper()
            if day_name not in _WEEKDAYS:
                raise ValueError(f"invalid RRULE weekday: {token!r}")
            ordinal_raw = token[:-2]
            weekdays.append((_WEEKDAYS[day_name], int(ordinal_raw) if ordinal_raw else 0))

    until: datetime | None = None
    if "UNTIL" in parts:
        raw_until = parts["UNTIL"]
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
            try:
                until = datetime.strptime(raw_until, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if until is None:
            raise ValueError(f"invalid RRULE UNTIL value: {raw_until!r}")

    return {
        "frequency": frequency,
        "interval": max(1, int(parts.get("INTERVAL", "1"))),
        "weekdays": weekdays,
        "month_days": integers("BYMONTHDAY"),
        "months": integers("BYMONTH"),
        "week_numbers": integers("BYWEEKNO"),
        "year_days": integers("BYYEARDAY"),
        "set_positions": integers("BYSETPOS"),
        "count": int(parts["COUNT"]) if "COUNT" in parts else None,
        "until": until,
    }


def build_eventkit_rule(eventkit: Any, value: str) -> Any:
    """Build an ``EKRecurrenceRule`` using a lazily supplied EventKit module."""

    parsed = parse_rrule(value)
    frequency = getattr(eventkit, f"EKRecurrenceFrequency{parsed['frequency'].title()}")
    days = None
    if parsed["weekdays"] is not None:
        days = []
        for weekday, ordinal in parsed["weekdays"]:
            cls = eventkit.EKRecurrenceDayOfWeek
            days.append(
                cls.dayOfWeek_weekNumber_(weekday, ordinal)
                if ordinal
                else cls.dayOfWeek_(weekday)
            )
    end = None
    if parsed["count"] is not None:
        end = eventkit.EKRecurrenceEnd.recurrenceEndWithOccurrenceCount_(parsed["count"])
    elif parsed["until"] is not None:
        from Foundation import NSDate

        native_until = NSDate.dateWithTimeIntervalSince1970_(parsed["until"].timestamp())
        end = eventkit.EKRecurrenceEnd.recurrenceEndWithEndDate_(native_until)
    return eventkit.EKRecurrenceRule.alloc().initRecurrenceWithFrequency_interval_daysOfTheWeek_daysOfTheMonth_monthsOfTheYear_weeksOfTheYear_daysOfTheYear_setPositions_end_(
        frequency,
        parsed["interval"],
        days,
        parsed["month_days"],
        parsed["months"],
        parsed["week_numbers"],
        parsed["year_days"],
        parsed["set_positions"],
        end,
    )


def eventkit_rule_to_rrule(rule: Any) -> str:
    """Serialize all public ``EKRecurrenceRule`` fields to an RRULE string."""

    def call(name: str, default: Any = None) -> Any:
        member = getattr(rule, name, None)
        return member() if callable(member) else (default if member is None else member)

    frequency = {0: "DAILY", 1: "WEEKLY", 2: "MONTHLY", 3: "YEARLY"}.get(
        int(call("frequency", -1))
    )
    if frequency is None:
        raise ValueError(f"unknown EventKit recurrence frequency: {call('frequency')!r}")
    parts = [f"FREQ={frequency}"]
    interval = int(call("interval", 1) or 1)
    if interval != 1:
        parts.append(f"INTERVAL={interval}")
    days = call("daysOfTheWeek", []) or []
    if days:
        encoded = []
        for day in days:
            weekday = getattr(day, "dayOfTheWeek", None)
            weekday = weekday() if callable(weekday) else weekday
            ordinal = getattr(day, "weekNumber", None)
            ordinal = ordinal() if callable(ordinal) else (ordinal or 0)
            encoded.append(f"{int(ordinal) if ordinal else ''}{_WEEKDAY_NAMES[int(weekday)]}")
        parts.append("BYDAY=" + ",".join(encoded))
    for field, name in (
        ("daysOfTheMonth", "BYMONTHDAY"),
        ("monthsOfTheYear", "BYMONTH"),
        ("weeksOfTheYear", "BYWEEKNO"),
        ("daysOfTheYear", "BYYEARDAY"),
        ("setPositions", "BYSETPOS"),
    ):
        values = call(field, []) or []
        if values:
            parts.append(f"{name}=" + ",".join(str(int(value)) for value in values))
    recurrence_end = call("recurrenceEnd")
    if recurrence_end is not None:
        count = getattr(recurrence_end, "occurrenceCount", None)
        count = count() if callable(count) else count
        end_date = getattr(recurrence_end, "endDate", None)
        end_date = end_date() if callable(end_date) else end_date
        if count:
            parts.append(f"COUNT={int(count)}")
        elif end_date is not None:
            timestamp = getattr(end_date, "timeIntervalSince1970", None)
            timestamp = timestamp() if callable(timestamp) else timestamp
            if timestamp is not None:
                until = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
                parts.append("UNTIL=" + until.strftime("%Y%m%dT%H%M%SZ"))
    return "RRULE:" + ";".join(parts)


__all__ = ["build_eventkit_rule", "eventkit_rule_to_rrule", "parse_rrule"]
