"""Observe exact Triage provider effects without performing another write.

Native object identity, account, container, member and approval lineage must
all agree. Similar titles and model output never establish a provider match.
Both completion and observation trigger matching, so append order does not
lose an observation that arrived while a provider command was completing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from adminme.events.envelope import EventEnvelope


OBSERVATION_TYPES = (
    "calendar.event_added",
    "calendar.event_updated",
    "calendar.event_deleted",
    "list_item.added",
    "list_item.updated",
    "list_item.completed",
    "list_item.uncompleted",
    "list_item.deleted",
    "list.created",
    "list.updated",
    "list.deleted",
)
_OPERATIONS = {
    "google_calendar": {"create_event"},
    "apple_calendar": {"create_event"},
    "google_tasks": {"create_task"},
    "apple_reminders": {"create_reminder", "create_list"},
}
_COORDINATE_FIELDS = {
    "member_id",
    "calendar_id",
    "calendar_external_id",
    "tasklist_id",
    "target_list_id",
    "bridge_enrollment_id",
    "bridge_pack_id",
    "provider_account_id",
}
_CALENDAR_FIELDS = {
    "summary",
    "description",
    "location",
    "start",
    "end",
    "all_day",
    "event_type",
    "start_time_zone",
    "end_time_zone",
    "visibility",
    "availability",
    "use_default_alarms",
    "alarms",
    "recurrence",
    "attendees",
}
_REMINDER_FIELDS = {
    "title",
    "notes",
    "url",
    "priority",
    "start",
    "due",
    "alarms",
    "recurrence_rules",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _date(value: Any, *, date_only: bool) -> Any:
    if isinstance(value, dict):
        if not all(value.get(key) is not None for key in ("year", "month", "day")):
            return value
        day = f"{int(value['year']):04}-{int(value['month']):02}-{int(value['day']):02}"
        if date_only or value.get("all_day") or value.get("hour") is None:
            return day
        zone = value.get("time_zone")
        if isinstance(zone, str) and not value.get("floating"):
            try:
                parsed = datetime(
                    int(value["year"]),
                    int(value["month"]),
                    int(value["day"]),
                    int(value["hour"]),
                    int(value.get("minute") or 0),
                    int(value.get("second") or 0),
                    tzinfo=ZoneInfo(zone),
                )
                return parsed.astimezone(timezone.utc).isoformat()
            except (ValueError, ZoneInfoNotFoundError):
                return value
        # Do not erase floating EventKit semantics to obtain a match.
        return {key: item for key, item in value.items() if item is not None}
    if not isinstance(value, str):
        return value
    if date_only:
        return value.split("T", 1)[0]
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else value


def compare_provider_state(
    request: dict[str, Any], observation: dict[str, Any]
) -> tuple[str, dict[str, Any], dict[str, Any], list[str]]:
    """Compare only documented fields; unobservable requested fields block proof."""

    p = request["payload"]
    desired = dict(p.get("payload") or {})
    observed = observation["payload"]
    operation = p["operation"]
    if observation["type"].endswith(("_deleted", ".deleted")):
        return "missing", desired, {}, ["object"]
    if operation == "create_event":
        allowed = _CALENDAR_FIELDS
        aliases: dict[str, str] = {}
    elif operation == "create_list":
        allowed = {"title", "name", "list_name", "color"}
        aliases = {"title": "list_name", "name": "list_name"}
    else:
        allowed = _REMINDER_FIELDS | {"parent"}
        aliases = {"parent": "parent_external_id"}
    expected: dict[str, Any] = {}
    actual: dict[str, Any] = {}
    unavailable: list[str] = []
    for key, value in desired.items():
        if key in _COORDINATE_FIELDS or value is None:
            continue
        if key not in allowed:
            unavailable.append(key)
            continue
        observed_key = aliases.get(key, key)
        if observed_key not in observed:
            unavailable.append(key)
        observed_value = observed.get(observed_key)
        if key in {"due", "start", "end"}:
            date_only = p["provider"] == "google_tasks" or bool(desired.get("all_day"))
            value = _date(value, date_only=date_only)
            observed_value = _date(observed_value, date_only=date_only)
        expected[key] = value
        actual[key] = observed_value
    differences = [key for key in expected if expected[key] != actual[key]]
    if unavailable or not expected:
        return "incomplete", expected, actual, sorted(set(differences + unavailable))
    return ("drifted" if differences else "matched"), expected, actual, differences


class ProviderWriteReconciler:
    CONSUMER_VERSION = "triage-provider-observation-v1"

    def __init__(self, event_log: Any, event_sink: Any) -> None:
        self._log = event_log
        self._events = event_sink

    async def handle(self, event: dict[str, Any]) -> None:
        if event["type"] == "external_sync.write_completed":
            terminals = [event]
        elif event["type"] in OBSERVATION_TYPES:
            terminals = [
                terminal
                async for terminal in self._log.read_since(
                    owner_scope=event["owner_scope"],
                    types=["external_sync.write_completed"],
                )
            ]
        else:
            return
        for terminal in terminals:
            p = terminal["payload"]
            if (
                terminal["tenant_id"] != event["tenant_id"]
                or terminal.get("schema_version") != 2
                or p.get("suppressed")
                or not p.get("provider_called")
                or not (p.get("result") or {}).get("applied")
                or not p.get("external_id")
                or not p.get("origin_proposal_id")
                or p.get("operation") not in _OPERATIONS.get(p.get("provider"), set())
            ):
                continue
            request = await self._log.get(p["command_event_id"])
            if request is None or not self._valid_request(request, terminal):
                continue
            if event["type"] in OBSERVATION_TYPES:
                observations = [event]
            else:
                observations = [
                    source
                    async for source in self._log.read_since(
                        request["event_id"],
                        owner_scope=terminal["owner_scope"],
                        types=list(OBSERVATION_TYPES),
                    )
                    if self._matches(request, terminal, source)
                ]
                # Older observed revisions cannot supersede a later state.
                observations = observations[-1:]
            for source in observations:
                if not self._matches(request, terminal, source):
                    continue
                await self._record(request, terminal, source)

    @staticmethod
    def _valid_request(request: dict[str, Any], terminal: dict[str, Any]) -> bool:
        r, t = request["payload"], terminal["payload"]
        member = r.get("member_id")
        scope = f"private:{member}"
        return bool(
            member
            and request["type"] == "external_sync.write_requested"
            and request.get("schema_version") == 2
            and request["tenant_id"] == terminal["tenant_id"]
            and request["owner_scope"] == terminal["owner_scope"] == scope
            and request["visibility_scope"] == terminal["visibility_scope"] == scope
            and terminal.get("causation_id") == request["event_id"]
            and all(
                r.get(key) == t.get(key) and r.get(key) is not None
                for key in (
                    "command_id",
                    "member_id",
                    "provider",
                    "provider_account_id",
                    "operation",
                    "domain_approval_event_id",
                    "openclaw_approval_event_id",
                    "request_fingerprint",
                    "origin_proposal_id",
                )
            )
        )

    @staticmethod
    def _matches(
        request: dict[str, Any], terminal: dict[str, Any], source: dict[str, Any]
    ) -> bool:
        r, t, s = request["payload"], terminal["payload"], source["payload"]
        details = r.get("payload") or {}
        if r["operation"] == "create_event":
            family, object_key, container_key = (
                "calendar.",
                "external_event_id",
                "calendar_external_id",
            )
            container = details.get("calendar_external_id") or details.get(
                "calendar_id"
            )
        elif r["operation"] == "create_list":
            family, object_key, container_key = (
                "list.",
                "list_external_id",
                "list_external_id",
            )
            container = t["external_id"]
        else:
            family, object_key, container_key = (
                "list_item.",
                "item_external_id",
                "list_external_id",
            )
            container = details.get("tasklist_id") or details.get("target_list_id")
        return bool(
            source["type"].startswith(family)
            and source["event_id"] > request["event_id"]
            and source["tenant_id"] == request["tenant_id"]
            and source["owner_scope"] == request["owner_scope"]
            and source["visibility_scope"] == request["visibility_scope"]
            and s.get("member_id") == r["member_id"]
            and not s.get("service_principal_id")
            and s.get("provider") == r["provider"]
            and s.get("provider_account_id") == r["provider_account_id"]
            and s.get(object_key) == t["external_id"]
            and container
            and s.get(container_key) == container
            and not str(source.get("source_adapter", "")).startswith("external-sync")
            and source.get("causation_id")
            not in {request["event_id"], terminal["event_id"]}
        )

    async def _record(
        self, request: dict[str, Any], terminal: dict[str, Any], source: dict[str, Any]
    ) -> None:
        async for prior in self._log.read_since(
            owner_scope=source["owner_scope"], types=["external_sync.write_reconciled"]
        ):
            if (
                prior["tenant_id"] == source["tenant_id"]
                and prior["payload"]["terminal_event_id"] == terminal["event_id"]
                and prior["payload"]["observation_event_id"] >= source["event_id"]
            ):
                return
        outcome, expected, actual, differences = compare_provider_state(request, source)
        p = terminal["payload"]
        now = EventEnvelope.now_utc_iso()
        await self._events.write(
            EventEnvelope(
                event_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                tenant_id=source["tenant_id"],
                type="external_sync.write_reconciled",
                schema_version=1,
                occurred_at=now,
                source_adapter="triage-provider-observation",
                source_account_id=p["provider_account_id"],
                owner_scope=source["owner_scope"],
                visibility_scope=source["visibility_scope"],
                sensitivity=source["sensitivity"],
                correlation_id=request.get("correlation_id"),
                causation_id=source["event_id"],
                idempotency_key=f"triage-provider-observation:{terminal['event_id']}:{source['event_id']}",
                payload={
                    "command_id": p["command_id"],
                    "command_event_id": request["event_id"],
                    "terminal_event_id": terminal["event_id"],
                    "observation_event_id": source["event_id"],
                    "origin_proposal_id": p["origin_proposal_id"],
                    "member_id": p["member_id"],
                    "provider": p["provider"],
                    "provider_account_id": p["provider_account_id"],
                    "external_id": p["external_id"],
                    "outcome": outcome,
                    "compared_fields": sorted(expected),
                    "differing_fields": differences,
                    "desired_state_sha256": _digest(expected),
                    "observed_state_sha256": _digest(actual),
                    "reconciled_at": now,
                },
            )
        )


def apply_triage_provider_reconciliation(envelope: dict[str, Any], conn: Any) -> None:
    """Apply only to the proposal's current terminal command; keep an audit trail."""

    p = envelope["payload"]
    conn.execute(
        """UPDATE triage_items SET resolution_reason=?, outcome_event_id=?, last_event_id=?
           WHERE tenant_id=? AND proposal_id=? AND owner_member_id=?
             AND owner_scope=? AND visibility_scope=? AND state='confirmed'
             AND materialization_event_id=? AND last_event_id < ?
             AND resolution_reason IN ('provider_completed_awaiting_observation',
               'provider_observed_matched','provider_observed_drifted',
               'provider_observed_missing','provider_observed_incomplete')""",
        (
            f"provider_observed_{p['outcome']}",
            envelope["event_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["origin_proposal_id"],
            p["member_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            p["command_event_id"],
            envelope["event_id"],
        ),
    )
