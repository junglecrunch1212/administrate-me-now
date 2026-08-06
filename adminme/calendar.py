"""Deterministic household calendar composition.

This module deliberately contains no model seam.  Provider facts, principal-
approved calendar rules, explicit schedule windows, and an explicit clock are
the complete inputs to deduplication, effective-capacity, conflict, buffer,
and open-window calculations.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


_EFFECT_RANK = {"ignore": 0, "context": 1, "soft_hold": 2, "hard_block": 3}


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _parse(value: Any, zone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if "T" not in value:
            return datetime.combine(date.fromisoformat(value), time.min, tzinfo=zone)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _effect_max(left: str, right: str) -> str:
    return left if _EFFECT_RANK.get(left, 0) >= _EFFECT_RANK.get(right, 0) else right


def _route(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("calendar_source") or row.get("provider") or ""),
        str(row.get("provider_account_id") or ""),
        str(row.get("calendar_external_id") or ""),
    )


def _cluster_key(row: dict[str, Any]) -> str:
    start = str(row.get("start_at") or "")
    if row.get("ical_uid"):
        raw = f"uid\x1f{row['ical_uid']}\x1f{start}"
    else:
        raw = "\x1f".join(
            (
                "signature",
                str(row.get("summary") or "").strip().casefold(),
                start,
                str(row.get("end_at") or ""),
            )
        )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _rule_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": row.get("rule_id"),
        "subject_member_id": row.get("subject_member_id"),
        "source_member_id": row.get("source_member_id"),
        "provider": row.get("provider"),
        "provider_account_id": row.get("provider_account_id"),
        "calendar_external_id": row.get("calendar_external_id"),
        "calendar_name": row.get("calendar_name"),
        "purpose": row.get("purpose"),
        "effect": row.get("effect"),
        "tentative_effect": row.get("tentative_effect"),
        "caregiving_load": row.get("caregiving_load"),
        "caregiving_summary_values": _json(
            row.get("caregiving_summary_values_json"), []
        ),
        "buffer_kind": row.get("buffer_kind") or "auto",
        "write_target": bool(row.get("write_target")),
        "destination_kind": row.get("destination_kind"),
        "enabled": bool(row.get("enabled")),
        "access_role": row.get("access_role"),
        "write_events": bool(row.get("write_events")),
        "last_event_id": row.get("last_event_id"),
    }


def _source_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_id": row.get("member_id"),
        "provider": row.get("provider"),
        "provider_account_id": row.get("provider_account_id"),
        "calendar_external_id": row.get("calendar_external_id"),
        "calendar_name": row.get("calendar_name"),
        "access_role": row.get("access_role"),
        "primary": bool(row.get("is_primary")),
        "selected": bool(row.get("selected")),
        "hidden": bool(row.get("hidden")),
        "timezone": row.get("timezone"),
        "read_details": bool(row.get("read_details")),
        "read_free_busy": bool(row.get("read_free_busy")),
        "write_events": bool(row.get("write_events")),
        "observed_at": row.get("observed_at"),
        "last_event_id": row.get("last_event_id"),
    }


def _buffer_kind(row: dict[str, Any], rule: dict[str, Any]) -> str:
    configured = str(rule.get("buffer_kind") or "auto")
    if configured != "auto":
        return configured
    if bool(row.get("all_day")):
        return "none"
    if str(row.get("event_type") or "") == "focusTime":
        return "deep_work"
    if str(rule.get("purpose") or "") == "coparenting":
        return "handoff"
    if _json(row.get("conference_json"), None):
        return "remote"
    if row.get("location"):
        return "appointment"
    return "none"


def _event_effect(row: dict[str, Any], rule: dict[str, Any]) -> tuple[str, list[str]]:
    if not rule or not bool(rule.get("enabled", True)):
        effect = "soft_hold" if row.get("availability") != "free" else "context"
        return effect, ["No household rule is configured; treated cautiously."]
    effect = str(rule.get("effect") or "context")
    reasons = [f"{str(rule.get('purpose') or 'other').replace('_', ' ')} calendar"]
    if row.get("availability") == "free" and effect == "hard_block":
        effect = "context"
        reasons.append("Provider marks this event available, not busy.")
    if row.get("status") == "tentative":
        effect = str(rule.get("tentative_effect") or "soft_hold")
        reasons.append("Tentative events use the configured tentative policy.")
    markers = {
        str(value).strip().casefold()
        for value in _json(rule.get("caregiving_summary_values_json"), [])
        if str(value).strip()
    }
    if markers and str(row.get("summary") or "").strip().casefold() in markers:
        load = str(rule.get("caregiving_load") or "ignore")
        effect = _effect_max(effect, load)
        reasons.append("This custody marker applies the approved caregiving load.")
    if effect == "ignore":
        reasons.append("This source is quarantined from scheduling.")
    elif effect == "context":
        reasons.append("Visible for context; it does not consume capacity.")
    elif effect == "soft_hold":
        reasons.append("Warn about overlap but allow an explicit choice.")
    else:
        reasons.append("This time is unavailable unless the rule is changed.")
    return effect, reasons


def _expand_windows(
    policy: dict[str, Any] | None,
    *,
    range_start: datetime,
    range_end: datetime,
    zone: ZoneInfo,
) -> list[dict[str, Any]]:
    windows = list((policy or {}).get("windows") or [])
    expanded: list[dict[str, Any]] = []
    day = range_start.astimezone(zone).date()
    final_day = range_end.astimezone(zone).date()
    while day <= final_day:
        for rule in windows:
            if day.weekday() not in set(rule.get("days") or []):
                continue
            try:
                start_t = time.fromisoformat(str(rule["start_local"]))
                end_t = time.fromisoformat(str(rule["end_local"]))
            except (KeyError, ValueError):
                continue
            start = datetime.combine(day, start_t, tzinfo=zone)
            end = datetime.combine(day, end_t, tzinfo=zone)
            if end <= start:
                end += timedelta(days=1)
            if start < range_end and end > range_start:
                expanded.append(
                    {
                        "start_at": start.isoformat(),
                        "end_at": end.isoformat(),
                        "kind": rule.get("kind"),
                        "effect": rule.get("effect"),
                        "label": rule.get("label"),
                    }
                )
        day += timedelta(days=1)
    expanded.sort(key=lambda item: (item["start_at"], item["end_at"], item["label"]))
    return expanded


def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _open_windows(
    *,
    range_start: datetime,
    range_end: datetime,
    zone: ZoneInfo,
    blocked: list[tuple[datetime, datetime]],
) -> list[dict[str, str]]:
    merged = _merge(blocked)
    cursor = range_start
    result: list[dict[str, str]] = []
    for start, end in merged:
        if end <= cursor:
            continue
        if start > cursor and start - cursor >= timedelta(minutes=20):
            result.append({"start_at": cursor.isoformat(), "end_at": start.isoformat()})
        cursor = max(cursor, end)
    if range_end > cursor and range_end - cursor >= timedelta(minutes=20):
        result.append({"start_at": cursor.isoformat(), "end_at": range_end.isoformat()})
    return result


def compose_calendar_cockpit(
    *,
    member_id: str,
    timezone_name: str,
    as_of: datetime,
    range_start: datetime,
    range_end: datetime,
    events: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    schedule_policy: dict[str, Any] | None,
) -> dict[str, Any]:
    zone = ZoneInfo(timezone_name)
    as_of = as_of.astimezone(zone)
    range_start = range_start.astimezone(zone)
    range_end = range_end.astimezone(zone)
    rule_map = {_route(row): row for row in rules if row.get("enabled")}
    source_map = {
        (
            str(row.get("provider") or ""),
            str(row.get("provider_account_id") or ""),
            str(row.get("calendar_external_id") or ""),
        ): row
        for row in sources
    }
    buffers = dict((schedule_policy or {}).get("buffer_minutes") or {})
    decorated: list[dict[str, Any]] = []
    for row in events:
        start = _parse(row.get("start_at"), zone)
        end = _parse(row.get("end_at"), zone) or start
        if start is None or end is None or end <= start:
            continue
        route = _route(row)
        rule = rule_map.get(route, {})
        source = source_map.get(route, {})
        effect, reasons = _event_effect(row, rule)
        kind = _buffer_kind(row, rule)
        before = int(buffers.get(f"{kind}_before", 0))
        after = int(buffers.get(f"{kind}_after", 0))
        decorated.append(
            {
                "id": row.get("calendar_event_id"),
                "cluster_id": _cluster_key(row),
                "title": row.get("summary") or row.get("title_redacted") or "Busy",
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "buffered_start_at": (start - timedelta(minutes=before)).isoformat(),
                "buffered_end_at": (end + timedelta(minutes=after)).isoformat(),
                "all_day": bool(row.get("all_day")),
                "status": row.get("status"),
                "availability": row.get("availability"),
                "effect": effect,
                "purpose": rule.get("purpose") or "unclassified",
                "calendar_name": row.get("calendar_name") or source.get("calendar_name"),
                "provider": row.get("calendar_source"),
                "external_event_id": row.get("external_uid"),
                "provider_account_id": row.get("provider_account_id"),
                "calendar_external_id": row.get("calendar_external_id"),
                "provider_revision": row.get("provider_revision"),
                "location": row.get("location"),
                "html_link": row.get("html_link"),
                "buffer_kind": kind,
                "buffer_before_minutes": before,
                "buffer_after_minutes": after,
                "why": reasons,
                "can_write": bool(source.get("write_events")) and bool(rule.get("write_target")),
                "last_event_id": row.get("last_event_id"),
                "recorded_outcome": row.get("recorded_outcome"),
                "outcome_event_id": row.get("outcome_event_id"),
                "linked_item_kind": row.get("linked_item_kind"),
                "linked_item_id": row.get("linked_item_id"),
                "completion_policy": row.get("completion_policy") or "manual",
            }
        )

    clusters: dict[str, list[dict[str, Any]]] = {}
    for event in decorated:
        clusters.setdefault(event["cluster_id"], []).append(event)
    effective: list[dict[str, Any]] = []
    for cluster_id, copies in clusters.items():
        copies.sort(
            key=lambda item: (
                -_EFFECT_RANK.get(str(item.get("effect")), 0),
                str(item.get("calendar_name") or ""),
                str(item.get("id") or ""),
            )
        )
        representative = dict(copies[0])
        representative["cluster_id"] = cluster_id
        representative["copy_count"] = len(copies)
        representative["source_calendars"] = sorted(
            {str(item.get("calendar_name") or "Calendar") for item in copies}
        )
        representative["duplicate_collapsed"] = len(copies) > 1
        effective.append(representative)
    effective.sort(key=lambda item: (item["start_at"], item["end_at"], item["cluster_id"]))

    conflicts: list[dict[str, Any]] = []
    timed = [
        item
        for item in effective
        if not item["all_day"] and item["effect"] in {"hard_block", "soft_hold"}
    ]
    timed.sort(key=lambda item: (item["buffered_start_at"], item["buffered_end_at"]))
    for index, left in enumerate(timed):
        left_start = _parse(left["buffered_start_at"], zone)
        left_end = _parse(left["buffered_end_at"], zone)
        if left_start is None or left_end is None:
            continue
        for right in timed[index + 1 :]:
            right_start = _parse(right["buffered_start_at"], zone)
            right_end = _parse(right["buffered_end_at"], zone)
            if right_start is None or right_end is None:
                continue
            if right_start >= left_end:
                break
            overlap_start = max(left_start, right_start)
            overlap_end = min(left_end, right_end)
            if overlap_end <= overlap_start:
                continue
            hard = "hard_block" in {left["effect"], right["effect"]}
            conflicts.append(
                {
                    "kind": "conflict" if hard and left["effect"] == right["effect"] else "pressure",
                    "left_cluster_id": left["cluster_id"],
                    "right_cluster_id": right["cluster_id"],
                    "start_at": overlap_start.isoformat(),
                    "end_at": overlap_end.isoformat(),
                    "minutes": int((overlap_end - overlap_start).total_seconds() // 60),
                    "why": "Buffered hard blocks overlap."
                    if left["effect"] == right["effect"] == "hard_block"
                    else "A soft hold overlaps unavailable time; an explicit choice is allowed.",
                }
            )

    schedule_windows = _expand_windows(
        schedule_policy,
        range_start=range_start,
        range_end=range_end,
        zone=zone,
    )
    blocked: list[tuple[datetime, datetime]] = []
    for item in schedule_windows:
        if item["effect"] != "hard_block" or item["kind"] == "work":
            continue
        start = _parse(item["start_at"], zone)
        end = _parse(item["end_at"], zone)
        if start and end:
            blocked.append((max(start, range_start), min(end, range_end)))
    for item in effective:
        if item["effect"] != "hard_block":
            continue
        start = _parse(item["buffered_start_at"], zone)
        end = _parse(item["buffered_end_at"], zone)
        if start and end:
            blocked.append((max(start, range_start), min(end, range_end)))
    open_windows = _open_windows(
        range_start=range_start,
        range_end=range_end,
        zone=zone,
        blocked=blocked,
    )
    conditional_windows = [
        {**item, "requires_acceptance": True}
        for item in schedule_windows
        if item["kind"] == "chores_if_accepted"
    ]
    active_windows = [
        item
        for item in schedule_windows
        if (_parse(item["start_at"], zone) or range_end)
        <= as_of
        < (_parse(item["end_at"], zone) or range_start)
    ]
    active_windows.sort(key=lambda item: -_EFFECT_RANK.get(str(item["effect"]), 0))
    current_guardrail = active_windows[0] if active_windows else None
    counts = {name: 0 for name in _EFFECT_RANK}
    for item in effective:
        counts[str(item["effect"])] = counts.get(str(item["effect"]), 0) + 1
    configured_routes = {_route(row) for row in rules}
    unconfigured = [
        _source_public(row)
        for row in sources
        if (
            str(row.get("provider") or ""),
            str(row.get("provider_account_id") or ""),
            str(row.get("calendar_external_id") or ""),
        )
        not in configured_routes
    ]
    return {
        "contract": "deterministic-household-calendar-v1",
        "member_id": member_id,
        "timezone": timezone_name,
        "range": {"start_at": range_start.isoformat(), "end_at": range_end.isoformat()},
        "onboarding": {
            "state": "complete"
            if sources and not unconfigured and schedule_policy
            else "needs_review",
            "source_count": len(sources),
            "configured_rule_count": len(rules),
            "unconfigured_sources": unconfigured,
            "schedule_policy_configured": schedule_policy is not None,
        },
        "counts": {
            **counts,
            "effective_events": len(effective),
            "provider_copies": len(decorated),
            "duplicates_collapsed": len(decorated) - len(effective),
            "conflicts": sum(1 for item in conflicts if item["kind"] == "conflict"),
            "pressures": sum(1 for item in conflicts if item["kind"] == "pressure"),
        },
        "events": effective,
        "conflicts": conflicts,
        "open_windows": open_windows,
        "conditional_windows": conditional_windows,
        "schedule_windows": schedule_windows,
        "current_guardrail": current_guardrail,
        "sources": [_source_public(row) for row in sources],
        "rules": [_rule_public(row) for row in rules],
        "schedule_policy": schedule_policy,
        "write_destinations": [
            _rule_public(row)
            for row in rules
            if row.get("enabled") and row.get("write_target") and row.get("write_events")
        ],
        "provider_truth": {
            "duplicates_are_grouped_not_deleted": True,
            "transparent_events_do_not_block": True,
            "soft_holds_require_explicit_acceptance": True,
            "model_decisions": False,
        },
    }


__all__ = ["compose_calendar_cockpit"]
