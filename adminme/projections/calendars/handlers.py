"""Deterministic calendar snapshot and activity-evidence reducers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlcipher3


_V1_UPDATABLE = {
    "owner_party",
    "summary",
    "description",
    "location",
    "start_at",
    "end_at",
    "all_day",
    "attendees_json",
    "privacy",
    "title_redacted",
}


def _json(value: Any, default: Any) -> str:
    return json.dumps(default if value is None else value, separators=(",", ":"))


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    if event_type == "calendar.event_added":
        apply_calendar_snapshot(envelope, conn)
    elif event_type == "calendar.event_updated":
        if int(envelope.get("schema_version", 1)) >= 2:
            apply_calendar_snapshot(envelope, conn)
        else:
            apply_calendar_event_updated_v1(envelope, conn)
    elif event_type == "calendar.event_deleted":
        apply_calendar_event_deleted(envelope, conn)
    elif event_type == "calendar.event_outcome_recorded":
        apply_calendar_event_outcome(envelope, conn)
    elif event_type == "calendar.source_observed":
        apply_calendar_source_observed(envelope, conn)
    elif event_type == "calendar.rule.configured":
        apply_calendar_rule_configured(envelope, conn)
    elif event_type == "calendar.schedule_policy.configured":
        apply_calendar_schedule_policy_configured(envelope, conn)


def _calendar_event_id(payload: dict[str, Any], *, is_v2: bool) -> str:
    explicit = payload.get("calendar_event_id")
    if explicit:
        return str(explicit)
    source = payload.get("provider") or payload.get("calendar_source") or payload.get(
        "source"
    )
    external_uid = payload.get("external_event_id") or payload.get("external_uid")
    if not is_v2:
        return f"cal_{source}_{external_uid}"
    route = "\x1f".join(
        str(value or "")
        for value in (
            source,
            payload.get("provider_account_id"),
            payload.get("calendar_external_id") or payload.get("calendar_id"),
            external_uid,
        )
    )
    return "cal_" + hashlib.sha256(route.encode("utf-8")).hexdigest()[:32]


def apply_calendar_snapshot(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    is_v2 = int(envelope.get("schema_version", 1)) >= 2
    source = p.get("provider") or p.get("calendar_source") or p.get("source")
    external_uid = p.get("external_event_id") or p.get("external_uid")
    calendar_external_id = p.get("calendar_external_id") or p.get("calendar_id")
    event_id = _calendar_event_id(p, is_v2=is_v2)
    start_at = p.get("start_at") or p.get("start")
    end_at = p.get("end_at") or p.get("end") or start_at
    visibility = p.get("visibility", "default") if is_v2 else "default"
    attendees = p.get("attendees") or []
    conn.execute(
        """
        INSERT INTO calendar_events (
            calendar_event_id, tenant_id, calendar_source,
            provider_account_id, member_id, calendar_external_id, calendar_name,
            external_uid, ical_uid, provider_revision, owner_party, summary,
            description, location, structured_location_json, start_at, end_at,
            all_day, start_time_zone, end_time_zone, status, event_type,
            availability, provider_visibility, organizer_json, attendees_json,
            alarms_json, use_default_alarms, recurrence_json, sequence,
            conference_json, attachments_json, color_id, source_url, html_link,
            linked_item_kind, linked_item_id, completion_policy,
            provider_created_at, provider_updated_at, privacy, title_redacted,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        ON CONFLICT(tenant_id, calendar_event_id) DO UPDATE SET
            provider_account_id=excluded.provider_account_id,
            member_id=excluded.member_id,
            calendar_external_id=excluded.calendar_external_id,
            calendar_name=excluded.calendar_name,
            ical_uid=excluded.ical_uid,
            provider_revision=excluded.provider_revision,
            owner_party=excluded.owner_party,
            summary=excluded.summary,
            description=excluded.description,
            location=excluded.location,
            structured_location_json=excluded.structured_location_json,
            start_at=excluded.start_at,
            end_at=excluded.end_at,
            all_day=excluded.all_day,
            start_time_zone=excluded.start_time_zone,
            end_time_zone=excluded.end_time_zone,
            status=excluded.status,
            event_type=excluded.event_type,
            availability=excluded.availability,
            provider_visibility=excluded.provider_visibility,
            organizer_json=excluded.organizer_json,
            attendees_json=excluded.attendees_json,
            alarms_json=excluded.alarms_json,
            use_default_alarms=excluded.use_default_alarms,
            recurrence_json=excluded.recurrence_json,
            sequence=excluded.sequence,
            conference_json=excluded.conference_json,
            attachments_json=excluded.attachments_json,
            color_id=excluded.color_id,
            source_url=excluded.source_url,
            html_link=excluded.html_link,
            linked_item_kind=excluded.linked_item_kind,
            linked_item_id=excluded.linked_item_id,
            completion_policy=excluded.completion_policy,
            provider_created_at=excluded.provider_created_at,
            provider_updated_at=excluded.provider_updated_at,
            privacy=excluded.privacy,
            title_redacted=excluded.title_redacted,
            owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity,
            last_event_id=excluded.last_event_id
        """,
        (
            event_id,
            envelope["tenant_id"],
            source,
            p.get("provider_account_id"),
            p.get("member_id"),
            calendar_external_id,
            p.get("calendar_name"),
            external_uid,
            p.get("ical_uid"),
            p.get("provider_revision"),
            p.get("owner_party"),
            p.get("summary"),
            p.get("description") or p.get("body"),
            p.get("location"),
            _json(p.get("structured_location"), None),
            start_at,
            end_at,
            1 if p.get("all_day") else 0,
            p.get("start_time_zone"),
            p.get("end_time_zone"),
            p.get("status", "confirmed"),
            p.get("event_type", "default"),
            p.get("availability", "busy"),
            visibility,
            _json(p.get("organizer"), None),
            _json(attendees, []),
            _json(p.get("alarms"), []),
            1 if p.get("use_default_alarms") else 0,
            _json(p.get("recurrence"), {}),
            int(p.get("sequence") or 0),
            _json(p.get("conference"), None),
            _json(p.get("attachments"), []),
            p.get("color_id"),
            p.get("source_url"),
            p.get("html_link"),
            p.get("linked_item_kind"),
            p.get("linked_item_id"),
            p.get("completion_policy", "manual"),
            p.get("created_at"),
            p.get("updated_at"),
            p.get("privacy", "open"),
            p.get("title_redacted"),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


# Backwards-compatible name used by older tests/importers.
apply_calendar_event_added = apply_calendar_snapshot


def apply_calendar_event_updated_v1(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    updates = dict(p.get("field_updates") or {})
    columns: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        if key not in _V1_UPDATABLE:
            continue
        columns.append(key)
        if key == "all_day":
            params.append(1 if value else 0)
        elif key == "attendees_json" and not isinstance(value, str):
            params.append(_json(value, []))
        else:
            params.append(value)
    if not columns:
        return
    params.extend(
        [
            envelope["event_id"],
            envelope["tenant_id"],
            p["calendar_source"],
            p["external_uid"],
        ]
    )
    conn.execute(
        "UPDATE calendar_events SET "
        + ", ".join(f"{column}=?" for column in columns)
        + ", last_event_id=? WHERE tenant_id=? AND calendar_source=? AND external_uid=?",
        params,
    )


apply_calendar_event_updated = apply_calendar_event_updated_v1


def apply_calendar_event_deleted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    source = p.get("provider") or p.get("calendar_source")
    external_uid = p.get("external_event_id") or p.get("external_uid")
    if int(envelope.get("schema_version", 1)) >= 2:
        conn.execute(
            """
            DELETE FROM calendar_events
             WHERE tenant_id=? AND calendar_source=?
               AND ifnull(provider_account_id, '')=ifnull(?, '')
               AND ifnull(calendar_external_id, '')=ifnull(?, '')
               AND external_uid=?
            """,
            (
                envelope["tenant_id"],
                source,
                p.get("provider_account_id"),
                p.get("calendar_external_id"),
                external_uid,
            ),
        )
    else:
        conn.execute(
            "DELETE FROM calendar_events WHERE tenant_id=? AND calendar_source=? AND external_uid=?",
            (envelope["tenant_id"], source, external_uid),
        )


def apply_calendar_event_outcome(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO calendar_event_outcomes (
            tenant_id, member_id, calendar_source, external_uid, outcome,
            attendance_status, evidence, confidence, recorded_at,
            linked_item_kind, linked_item_id, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, member_id, calendar_source, external_uid) DO UPDATE SET
            outcome=excluded.outcome,
            attendance_status=excluded.attendance_status,
            evidence=excluded.evidence,
            confidence=excluded.confidence,
            recorded_at=excluded.recorded_at,
            linked_item_kind=excluded.linked_item_kind,
            linked_item_id=excluded.linked_item_id,
            last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["member_id"],
            p["provider"],
            p["external_event_id"],
            p["outcome"],
            p.get("attendance_status"),
            p["evidence"],
            p["confidence"],
            p["recorded_at"],
            p.get("linked_item_kind"),
            p.get("linked_item_id"),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_calendar_source_observed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO calendar_sources (
            tenant_id, member_id, provider, provider_account_id,
            calendar_external_id, calendar_name, access_role, is_primary,
            selected, hidden, timezone, color_id, default_reminders_json,
            read_details, read_free_busy, write_events, observed_at,
            owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(
            tenant_id, provider, provider_account_id, calendar_external_id
        ) DO UPDATE SET
            member_id=excluded.member_id,
            calendar_name=excluded.calendar_name,
            access_role=excluded.access_role,
            is_primary=excluded.is_primary,
            selected=excluded.selected,
            hidden=excluded.hidden,
            timezone=excluded.timezone,
            color_id=excluded.color_id,
            default_reminders_json=excluded.default_reminders_json,
            read_details=excluded.read_details,
            read_free_busy=excluded.read_free_busy,
            write_events=excluded.write_events,
            observed_at=excluded.observed_at,
            owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity,
            last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["member_id"],
            p["provider"],
            p["provider_account_id"],
            p["calendar_external_id"],
            p["calendar_name"],
            p["access_role"],
            1 if p.get("primary") else 0,
            1 if p.get("selected") else 0,
            1 if p.get("hidden") else 0,
            p.get("timezone"),
            p.get("color_id"),
            _json(p.get("default_reminders"), []),
            1 if p.get("read_details") else 0,
            1 if p.get("read_free_busy") else 0,
            1 if p.get("write_events") else 0,
            p["observed_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    # Google accepts the literal ``primary`` as an API alias, but calendarList
    # returns the canonical calendar id.  Once discovery reports that canonical
    # primary, keeping an older alias row would make one provider calendar look
    # like two onboarding decisions.  Projection replay may safely retire the
    # derived alias and its obsolete rule; event facts remain append-only.
    if (
        p["provider"] == "google_calendar"
        and p.get("primary")
        and p["calendar_external_id"] != "primary"
    ):
        route = (
            envelope["tenant_id"],
            p["provider"],
            p["provider_account_id"],
            "primary",
        )
        conn.execute(
            """
            DELETE FROM calendar_member_rules
             WHERE tenant_id=? AND provider=? AND provider_account_id=?
               AND calendar_external_id=?
            """,
            route,
        )
        conn.execute(
            """
            DELETE FROM calendar_sources
             WHERE tenant_id=? AND provider=? AND provider_account_id=?
               AND calendar_external_id=?
            """,
            route,
        )


def apply_calendar_rule_configured(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO calendar_member_rules (
            tenant_id, rule_id, subject_member_id, source_member_id, provider,
            provider_account_id, calendar_external_id, purpose, effect,
            tentative_effect, caregiving_load, caregiving_summary_values_json,
            buffer_kind, write_target, destination_kind,
            enabled, configured_at, owner_scope, visibility_scope, sensitivity,
            last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, rule_id) DO UPDATE SET
            subject_member_id=excluded.subject_member_id,
            source_member_id=excluded.source_member_id,
            provider=excluded.provider,
            provider_account_id=excluded.provider_account_id,
            calendar_external_id=excluded.calendar_external_id,
            purpose=excluded.purpose,
            effect=excluded.effect,
            tentative_effect=excluded.tentative_effect,
            caregiving_load=excluded.caregiving_load,
            caregiving_summary_values_json=excluded.caregiving_summary_values_json,
            buffer_kind=excluded.buffer_kind,
            write_target=excluded.write_target,
            destination_kind=excluded.destination_kind,
            enabled=excluded.enabled,
            configured_at=excluded.configured_at,
            owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity,
            last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["rule_id"],
            p["subject_member_id"],
            p["source_member_id"],
            p["provider"],
            p["provider_account_id"],
            p["calendar_external_id"],
            p["purpose"],
            p["effect"],
            p["tentative_effect"],
            p["caregiving_load"],
            _json(p.get("caregiving_summary_values"), []),
            p.get("buffer_kind", "auto"),
            1 if p.get("write_target") else 0,
            p.get("destination_kind"),
            1 if p.get("enabled", True) else 0,
            p["configured_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_calendar_schedule_policy_configured(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO calendar_schedule_policies (
            tenant_id, member_id, timezone, windows_json,
            buffer_minutes_json, accepted_chores_required, one_confirm_write,
            configured_at, owner_scope, visibility_scope, sensitivity,
            last_event_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, member_id) DO UPDATE SET
            timezone=excluded.timezone,
            windows_json=excluded.windows_json,
            buffer_minutes_json=excluded.buffer_minutes_json,
            accepted_chores_required=excluded.accepted_chores_required,
            one_confirm_write=excluded.one_confirm_write,
            configured_at=excluded.configured_at,
            owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity,
            last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            p["member_id"],
            p["timezone"],
            _json(p.get("windows"), []),
            _json(p.get("buffer_minutes"), {}),
            1 if p.get("accepted_chores_required", True) else 0,
            1 if p.get("one_confirm_write", True) else 0,
            p["configured_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
