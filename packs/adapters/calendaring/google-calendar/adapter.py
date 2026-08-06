"""Google Calendar adapter pack — fresh Cohort D Cat-B adapter (prompt 11b-iii).

Inherits :class:`adminme.lib.adapters.ExternalStateMirrorAdapter` per memo
05 §1.2 / §2.3 / §2.5 / §3.2 / [D19] / [PM-34] / [UT-27]. First fresh
Cat-B adapter under the canonical-build pattern: inbound poll of Google
Calendar v3 ``events.list`` maps onto EXISTING ``calendar.event_added@v1``
at ``adminme/events/schemas/ingest.py`` (no new schema per [D7]);
outbound ``create_event`` calls ``events.insert`` wrapped through
:func:`adminme.lib.observation.outbound` per [§6.20]. Asymmetric
``write_capabilities=["create_event"]`` per [UT-27]; ``update_event`` /
``delete_event`` are out of scope per parent draft.
"""

# [§8.2, D6] no LLM SDK imports under packs/adapters/.
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import AdapterContext, AdapterStatus, SensitivityFloor
from adminme.adapters.emission import write_adapter_event
from adminme.adapters.credentials import resolve_json_credentials
from adminme.lib.adapters import (
    ChangeEnvelope,
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.observation import outbound as observation_outbound

_log = logging.getLogger(__name__)

GOOGLE_CALENDAR_SCOPES: list[str] = ["https://www.googleapis.com/auth/calendar"]

# [§6 invariant 10] privileged > normal; config may raise but not lower.
_FLOOR_RANK: dict[str, int] = {"normal": 0, "privileged": 1}

_DEFAULT_POLL_INTERVAL_S: int = 60
_DEFAULT_PAGE_SIZE: int = 250

CredentialsResolver = Callable[[str], dict[str, Any]]
"""``op://`` ref → service-account JSON. Tests inject a stub; production
calls the 1Password CLI."""

ServiceFactory = Callable[[dict[str, Any], list[str], str], Any]
"""(credentials, scopes, subject_email) → Calendar service. Tests inject
a fake; production lazily imports the Google SDK."""


def _default_credentials_resolver(reference: str) -> dict[str, Any]:
    return resolve_json_credentials(reference)


def _default_service_factory(
    credentials_json: dict[str, Any],
    scopes: list[str],
    subject_email: str,
) -> Any:
    """Build Calendar v3 service; lazy import keeps SDK out of CI."""
    from googleapiclient.discovery import build

    if {"client_id", "client_secret", "refresh_token"} <= credentials_json.keys():
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=None,
            refresh_token=str(credentials_json["refresh_token"]),
            token_uri=str(
                credentials_json.get("token_uri")
                or "https://oauth2.googleapis.com/token"
            ),
            client_id=str(credentials_json["client_id"]),
            client_secret=str(credentials_json["client_secret"]),
            scopes=scopes,
        )
    else:
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(credentials_json, scopes=scopes)
        if subject_email:
            creds = creds.with_subject(subject_email)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _extract_when(when: dict[str, Any] | None) -> str:
    """``dateTime`` or ``date`` → str; ``""`` if neither so v1 schema's
    ``min_length=1`` surfaces it."""
    if not isinstance(when, dict):
        return ""
    return str(when.get("dateTime") or when.get("date") or "")


def _parse_google_when(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return None
    try:
        if "T" not in value:
            value = value + "T23:59:59+00:00"
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _participant(value: dict[str, Any], *, organizer: bool = False) -> dict[str, Any]:
    return {
        "email": value.get("email"),
        "display_name": value.get("displayName"),
        "participant_id": value.get("id"),
        "role": None,
        "participant_type": None,
        "response_status": value.get("responseStatus"),
        "self": bool(value.get("self")),
        "organizer": organizer or bool(value.get("organizer")),
        "optional": bool(value.get("optional")),
        "resource": bool(value.get("resource")),
        "comment": value.get("comment"),
        "additional_guests": int(value.get("additionalGuests") or 0),
    }


def _event_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy every Google-writable event field without provider read-only data."""
    aliases = {
        "body": "description",
        "color_id": "colorId",
        "conference": "conferenceData",
    }
    allowed = {
        "summary",
        "description",
        "location",
        "start",
        "end",
        "recurrence",
        "attendees",
        "reminders",
        "visibility",
        "transparency",
        "colorId",
        "conferenceData",
        "attachments",
        "extendedProperties",
        "source",
        "guestsCanInviteOthers",
        "guestsCanModify",
        "guestsCanSeeOtherGuests",
        "anyoneCanAddSelf",
        "status",
        "focusTimeProperties",
        "outOfOfficeProperties",
        "workingLocationProperties",
    }
    normalized = dict(payload)
    for source, target in aliases.items():
        if source in normalized and target not in normalized:
            normalized[target] = normalized[source]
    for name, zone_name in (("start", "start_time_zone"), ("end", "end_time_zone")):
        value = normalized.get(name)
        if isinstance(value, str):
            key = (
                "date" if normalized.get("all_day") and "T" not in value else "dateTime"
            )
            normalized[name] = {key: value}
            if normalized.get(zone_name):
                normalized[name]["timeZone"] = normalized[zone_name]
    recurrence = normalized.get("recurrence")
    if isinstance(recurrence, dict):
        normalized["recurrence"] = list(recurrence.get("lines") or [])
    if "alarms" in normalized or "use_default_alarms" in normalized:
        overrides = []
        for alarm in normalized.get("alarms") or []:
            offset = alarm.get("relative_offset_seconds")
            if offset is None:
                raise ValueError(
                    "Google Calendar supports event reminders as relative minute offsets"
                )
            overrides.append(
                {
                    "method": (
                        "popup"
                        if alarm.get("method") in (None, "alert", "default")
                        else alarm.get("method")
                    ),
                    "minutes": max(0, round(-int(offset) / 60)),
                }
            )
        normalized["reminders"] = {
            "useDefault": bool(normalized.get("use_default_alarms", False)),
            **({"overrides": overrides} if overrides else {}),
        }
    if "availability" in normalized:
        normalized["transparency"] = (
            "transparent" if normalized["availability"] == "free" else "opaque"
        )
    if "attendees" in normalized:
        normalized["attendees"] = [
            {
                key: value
                for key, value in {
                    "email": item.get("email"),
                    "displayName": item.get("display_name") or item.get("displayName"),
                    "optional": item.get("optional"),
                    "responseStatus": item.get("response_status")
                    or item.get("responseStatus"),
                    "comment": item.get("comment"),
                    "additionalGuests": item.get("additional_guests")
                    or item.get("additionalGuests"),
                }.items()
                if value is not None
            }
            for item in normalized.get("attendees") or []
        ]
    if "attachments" in normalized:
        normalized["attachments"] = [
            {
                key: value
                for key, value in {
                    "fileUrl": item.get("url") or item.get("fileUrl"),
                    "title": item.get("title"),
                    "mimeType": item.get("mime_type") or item.get("mimeType"),
                    "fileId": item.get("provider_file_id") or item.get("fileId"),
                }.items()
                if value is not None
            }
            for item in normalized.get("attachments") or []
        ]
    if normalized.get("source_url") and "source" not in normalized:
        normalized["source"] = {
            "url": normalized["source_url"],
            "title": normalized.get("summary") or "AdministrateMe",
        }
    if normalized.get("linked_item_kind") and normalized.get("linked_item_id"):
        private = dict(
            (normalized.get("extendedProperties") or {}).get("private") or {}
        )
        private.update(
            {
                "adminme_linked_kind": str(normalized["linked_item_kind"]),
                "adminme_linked_id": str(normalized["linked_item_id"]),
                "adminme_completion_policy": str(
                    normalized.get("completion_policy") or "manual"
                ),
            }
        )
        normalized["extendedProperties"] = {
            **dict(normalized.get("extendedProperties") or {}),
            "private": private,
        }
    return {key: normalized[key] for key in allowed if key in normalized}


class GoogleCalendarAdapter(ExternalStateMirrorAdapter):
    """Google Calendar Cat-B standalone adapter (fresh canonical-build pack).

    Polls ``events.list`` every ``poll_interval_s`` and emits one
    ``calendar.event_added@v1`` per non-deduped event mapping Google's
    response onto the existing schema BYTE-FOR-BYTE per [D7] + canonical
    11a's "MAP DON'T REGISTER". Outbound :meth:`_apply_write_impl`
    accepts ``WriteOperation(kind="create_event")`` and routes
    ``events.insert`` through :func:`observation_outbound` per [§6.20].
    """

    name: ClassVar[str] = "calendaring-google-calendar"
    family: ClassVar[Literal["reminders", "calendaring", "contacts", "tasks"]] = (
        "calendaring"
    )
    version: ClassVar[str] = "2.0.0"
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    # [memo 05 §3.2, PM-35] class-level mirror of the manifest's
    # capabilities. One Cat-B entry declaring the EXISTING on-main
    # ``calendar.event_added`` family — NO new schema per [D7].
    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-B",
            "runtime": "central",
            "event_families": [
                "calendar.event_added",
                "calendar.event_updated",
                "calendar.event_deleted",
            ],
        },
    ]

    # [UT-27] asymmetric — declare ONLY what is implemented.
    write_capabilities: ClassVar[list[str]] = [
        "create_event",
        "update_event",
        "delete_event",
        "respond_event",
        "move_event",
    ]

    # [memo 05 §2.5, PM-35] projection-row column for per-member vs
    # shared rows on the future calendar projection.
    sharing_model_discriminator: ClassVar[str] = "owner_member_id"

    PACK_ID: ClassVar[str] = "adapter:calendaring-google-calendar"
    HARDCODED_FLOOR: ClassVar[SensitivityFloor] = "normal"
    SCOPES: ClassVar[list[str]] = list(GOOGLE_CALENDAR_SCOPES)
    POLL_INTERVAL_S: ClassVar[int] = _DEFAULT_POLL_INTERVAL_S
    PAGE_SIZE: ClassVar[int] = _DEFAULT_PAGE_SIZE

    def __init__(
        self,
        *,
        config: dict[str, Any],
        credentials_resolver: CredentialsResolver | None = None,
        service_factory: ServiceFactory | None = None,
        max_poll_cycles: int | None = None,
        observation_session: Any = None,
        observation_manager: Any = None,
        observation_event_log: Any = None,
    ) -> None:
        self._config = dict(config)
        self._credentials_resolver = (
            credentials_resolver or _default_credentials_resolver
        )
        self._service_factory = service_factory or _default_service_factory
        # [§6 invariant 10] resolve floor at construction; raise on downgrade.
        self.sensitivity_floor: SensitivityFloor = (  # type: ignore[misc]
            self._resolve_sensitivity_floor()
        )
        self._poll_interval_s: int = int(
            self._config.get("poll_interval_s", self.POLL_INTERVAL_S)
        )
        self._status = AdapterStatus()
        self._service: Any = None
        self._ctx: AdapterContext | None = None
        self._stop_requested: bool = False
        self._max_poll_cycles: int | None = max_poll_cycles
        # Dedupe by stable event id; tracks ``updated`` so future
        # event_updated (out of scope) can diff without a re-fetch.
        self._seen_inbound: dict[str, str] = {}
        self._sync_token: str | None = None
        self._initial_sync_complete = False
        self._known_events: dict[str, dict[str, Any]] = {}
        self._elapsed_outcomes: set[str] = set()
        # [§6.20, PM-27] observation seam slots; Phase A leaves unset.
        self._observation_session: Any = observation_session
        self._observation_manager: Any = observation_manager
        self._observation_event_log: Any = observation_event_log

    def _resolve_sensitivity_floor(self) -> SensitivityFloor:
        """[§6 invariant 10] config may RAISE; never lower."""
        cfg_value = self._config.get("sensitivity_floor", self.HARDCODED_FLOOR)
        if cfg_value not in _FLOOR_RANK:
            raise ValueError(
                f"sensitivity_floor must be 'normal' or 'privileged', got {cfg_value!r}"
            )
        if _FLOOR_RANK[cfg_value] < _FLOOR_RANK[self.HARDCODED_FLOOR]:
            raise ValueError(
                f"config sensitivity_floor={cfg_value!r} lower than hardcoded "
                f"{self.HARDCODED_FLOOR!r}; refusing to downgrade "
                "[SYSTEM_INVARIANTS.md §6 invariant 10]"
            )
        return cfg_value  # type: ignore[return-value]

    async def start(self, *, ctx: AdapterContext) -> None:
        """Authenticate, emit ``adapter.authenticated``, run poll loop."""
        op_ref = self._config.get("oauth_credentials_ref") or self._config.get(
            "service_account_key_ref"
        )
        calendar_id = self._config.get("calendar_id")
        user_email = self._config.get("user_email")
        if not isinstance(op_ref, str) or not op_ref.startswith(
            ("op://", "keychain://")
        ):
            raise ValueError(
                "oauth_credentials_ref or service_account_key_ref must be a "
                "1Password or macOS Keychain reference"
            )
        if not isinstance(calendar_id, str) or not calendar_id:
            raise ValueError("calendar_id must be a non-empty string")
        if self._config.get("service_account_key_ref") and (
            not isinstance(user_email, str) or "@" not in user_email
        ):
            raise ValueError("user_email must be valid for service-account delegation")
        if not isinstance(user_email, str):
            user_email = str(self._config.get("provider_account_id") or "")
        creds_json = self._credentials_resolver(op_ref)
        self._service = self._service_factory(creds_json, self.SCOPES, user_email)
        self._ctx = ctx
        self._status.started_at = ctx.now_factory()
        self._status.state = "running"
        # [D7, D8] adapter.authenticated v1; correlation_id from factory.
        await self._emit_authenticated(ctx=ctx, op_ref=op_ref)
        await self._poll_loop(ctx=ctx)

    async def stop(self) -> None:
        """Release the Calendar service handle. Idempotent."""
        self._stop_requested = True
        self._service = None
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def _poll_loop(self, *, ctx: AdapterContext) -> None:
        """Inbound loop. Fatal errors halt; transient stays. Tests bound
        via ``max_poll_cycles``."""
        cycles = 0
        while not self._stop_requested:
            if self._max_poll_cycles is not None and cycles >= self._max_poll_cycles:
                return
            try:
                await self._poll_once(ctx=ctx)
            except BaseException as exc:  # noqa: BLE001 — classify & emit
                severity, error_kind, retry_after_s = self._classify_calendar_error(exc)
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=retry_after_s,
                )
                if severity == "fatal":
                    self._status.state = "crashed"
                    return
                self._status.state = "backoff"
            cycles += 1
            try:
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                return

    async def _poll_once(self, *, ctx: AdapterContext) -> int:
        """Fetch + dedupe + emit. ``_seen_inbound`` is the dedupe gate
        per F-3 — re-poll on the same event id is a no-op."""
        if self._service is None:
            raise RuntimeError(
                "Google Calendar service not initialized; call start() first"
            )
        calendar_id = str(self._config["calendar_id"])
        # Compatibility for the v1 fixture suite. Production v2 configs are
        # member-bound and use durable incremental synchronization below.
        if not self._config.get("member_id"):
            return await self._poll_once_v1(ctx=ctx, calendar_id=calendar_id)
        events, next_sync_token, incremental = self._fetch_event_changes(
            calendar_id=calendar_id,
            now=ctx.now_factory(),
        )
        emitted = 0
        was_initial_sync_complete = self._initial_sync_complete
        for event in events:
            event_id = str(event.get("id") or "")
            if not event_id:
                continue
            if event.get("status") == "cancelled":
                await self._append(
                    ctx=ctx,
                    event_type="calendar.event_deleted",
                    schema_version=2,
                    payload={
                        "member_id": str(self._config["member_id"]),
                        "provider": "google_calendar",
                        "provider_account_id": str(
                            self._config.get("provider_account_id")
                            or self._config.get("user_email")
                        ),
                        "calendar_external_id": calendar_id,
                        "external_event_id": event_id,
                        "ical_uid": event.get("iCalUID"),
                        "provider_revision": event.get("etag"),
                        "deleted_at": str(
                            event.get("updated") or ctx.now_factory().isoformat()
                        ),
                    },
                )
                if was_initial_sync_complete:
                    await self._emit_calendar_outcome(
                        ctx=ctx, event_id=event_id, outcome="cancelled", source=event
                    )
                    emitted += 1
                self._known_events.pop(event_id, None)
            else:
                kind = (
                    "calendar.event_updated" if incremental else "calendar.event_added"
                )
                await self._emit_calendar_snapshot(
                    ctx=ctx,
                    event_type=kind,
                    calendar_id=calendar_id,
                    event=event,
                )
                private = (event.get("extendedProperties") or {}).get("private", {})
                self._known_events[event_id] = {
                    "end": _extract_when(event.get("end")),
                    "linked_item_kind": private.get("adminme_linked_kind"),
                    "linked_item_id": private.get("adminme_linked_id"),
                    "completion_policy": private.get(
                        "adminme_completion_policy", "manual"
                    ),
                }
            self._seen_inbound[event_id] = str(
                event.get("etag") or event.get("updated") or ""
            )
            emitted += 1
        if was_initial_sync_complete:
            now = ctx.now_factory()
            for known_id, known in self._known_events.items():
                end_at = _parse_google_when(known.get("end"))
                if (
                    end_at is not None
                    and end_at <= now
                    and known_id not in self._elapsed_outcomes
                ):
                    await self._emit_calendar_outcome(
                        ctx=ctx, event_id=known_id, outcome="elapsed", source=known
                    )
                    self._elapsed_outcomes.add(known_id)
                    emitted += 1
        self._sync_token = next_sync_token
        self._initial_sync_complete = True
        return emitted

    async def _emit_calendar_outcome(
        self,
        *,
        ctx: AdapterContext,
        event_id: str,
        outcome: str,
        source: dict[str, Any],
    ) -> None:
        private = (source.get("extendedProperties") or {}).get("private", {})
        await self._append(
            ctx=ctx,
            event_type="calendar.event_outcome_recorded",
            payload={
                "member_id": str(self._config["member_id"]),
                "provider": "google_calendar",
                "external_event_id": event_id,
                "outcome": outcome,
                "attendance_status": None,
                "evidence": (
                    "provider_status" if outcome == "cancelled" else "calendar_elapsed"
                ),
                "confidence": 1.0 if outcome == "cancelled" else 0.55,
                "recorded_at": ctx.now_factory().isoformat(),
                "linked_item_kind": source.get("linked_item_kind")
                or private.get("adminme_linked_kind"),
                "linked_item_id": source.get("linked_item_id")
                or private.get("adminme_linked_id"),
                "completion_policy": source.get("completion_policy")
                or private.get("adminme_completion_policy", "manual"),
            },
        )

    async def _poll_once_v1(self, *, ctx: AdapterContext, calendar_id: str) -> int:
        time_min = ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ")
        events = self._fetch_events(calendar_id=calendar_id, time_min=time_min)
        emitted = 0
        for event in events:
            event_id = event.get("id")
            if not isinstance(event_id, str) or not event_id:
                continue
            if event_id in self._seen_inbound:
                continue
            await self._emit_calendar_event_added(
                ctx=ctx, calendar_id=calendar_id, event=event
            )
            self._seen_inbound[event_id] = str(event.get("updated") or "")
            emitted += 1
        return emitted

    def _fetch_event_changes(
        self, *, calendar_id: str, now: Any
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        incremental = self._sync_token is not None
        next_sync_token: str | None = self._sync_token
        while True:
            params: dict[str, Any] = {
                "calendarId": calendar_id,
                "maxResults": self.PAGE_SIZE,
                "showDeleted": True,
                "singleEvents": False,
            }
            if self._sync_token:
                params["syncToken"] = self._sync_token
            else:
                lookback = int(self._config.get("sync_lookback_days", 90))
                lookahead = int(self._config.get("sync_lookahead_days", 730))
                params["timeMin"] = (now - timedelta(days=lookback)).isoformat()
                params["timeMax"] = (now + timedelta(days=lookahead)).isoformat()
            if page_token:
                params["pageToken"] = page_token
            try:
                response = self._service.events().list(**params).execute()
            except BaseException as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status == 410 and self._sync_token is not None:
                    self._sync_token = None
                    return self._fetch_event_changes(calendar_id=calendar_id, now=now)
                raise
            records.extend(response.get("items") or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                next_sync_token = response.get("nextSyncToken") or next_sync_token
                break
        return records, next_sync_token, incremental

    def _fetch_events(self, *, calendar_id: str, time_min: str) -> list[dict[str, Any]]:
        """Page ``events.list(singleEvents=True, orderBy=startTime,
        timeMin=...)`` until ``nextPageToken`` is absent."""
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "calendarId": calendar_id,
                "maxResults": self.PAGE_SIZE,
                "singleEvents": True,
                "orderBy": "startTime",
                "timeMin": time_min,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = self._service.events().list(**params).execute()
            records.extend(resp.get("items") or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return records

    async def _emit_calendar_event_added(
        self,
        *,
        ctx: AdapterContext,
        calendar_id: str,
        event: dict[str, Any],
    ) -> None:
        """Map a Google event onto ``CalendarEventAddedV1`` per [D7] +
        canonical 11a's "MAP DON'T REGISTER"."""
        await self._append(
            ctx=ctx,
            event_type="calendar.event_added",
            payload={
                "source": "google_calendar",
                "external_event_id": str(event.get("id") or ""),
                "calendar_id": calendar_id,
                "summary": str(event.get("summary") or ""),
                "start": _extract_when(event.get("start")),
                "end": _extract_when(event.get("end")),
                "location": event.get("location"),
                "attendees": list(event.get("attendees") or []),
                "body": event.get("description"),
            },
        )

    async def _emit_calendar_snapshot(
        self,
        *,
        ctx: AdapterContext,
        event_type: str,
        calendar_id: str,
        event: dict[str, Any],
    ) -> None:
        start = event.get("start") or {}
        end = event.get("end") or {}
        reminders = event.get("reminders") or {}
        alarms = [
            {
                "method": str(item.get("method") or "default"),
                "trigger_at": None,
                "relative_offset_seconds": -60 * int(item.get("minutes") or 0),
                "provider_alarm_id": None,
            }
            for item in reminders.get("overrides") or []
        ]
        organizer_raw = event.get("organizer")
        attachments = [
            {
                "title": item.get("title"),
                "url": str(item.get("fileUrl") or ""),
                "mime_type": item.get("mimeType"),
                "provider_file_id": item.get("fileId"),
            }
            for item in event.get("attachments") or []
            if item.get("fileUrl")
        ]
        await self._append(
            ctx=ctx,
            event_type=event_type,
            schema_version=2,
            payload={
                "member_id": str(self._config["member_id"]),
                "provider": "google_calendar",
                "provider_account_id": str(
                    self._config.get("provider_account_id")
                    or self._config.get("user_email")
                ),
                "calendar_external_id": calendar_id,
                "calendar_name": self._config.get("calendar_name"),
                "external_event_id": str(event.get("id") or ""),
                "ical_uid": event.get("iCalUID"),
                "provider_revision": event.get("etag"),
                "status": str(event.get("status") or "confirmed"),
                "event_type": str(event.get("eventType") or "default"),
                "summary": str(event.get("summary") or ""),
                "description": event.get("description"),
                "location": event.get("location"),
                "structured_location": None,
                "start": _extract_when(start),
                "end": _extract_when(end),
                "start_time_zone": start.get("timeZone"),
                "end_time_zone": end.get("timeZone"),
                "all_day": "date" in start,
                "availability": "free"
                if event.get("transparency") == "transparent"
                else "busy",
                "visibility": str(event.get("visibility") or "default"),
                "organizer": _participant(organizer_raw, organizer=True)
                if isinstance(organizer_raw, dict)
                else None,
                "attendees": [
                    _participant(item) for item in event.get("attendees") or []
                ],
                "alarms": alarms,
                "use_default_alarms": bool(reminders.get("useDefault")),
                "recurrence": {
                    "lines": list(event.get("recurrence") or []),
                    "recurring_event_id": event.get("recurringEventId"),
                    "original_start": _extract_when(event.get("originalStartTime"))
                    or None,
                    "this_and_future": False,
                },
                "sequence": int(event.get("sequence") or 0),
                "conference": event.get("conferenceData"),
                "attachments": attachments,
                "color_id": event.get("colorId"),
                "source_url": (event.get("source") or {}).get("url"),
                "html_link": event.get("htmlLink"),
                "linked_item_kind": (event.get("extendedProperties") or {})
                .get("private", {})
                .get("adminme_linked_kind"),
                "linked_item_id": (event.get("extendedProperties") or {})
                .get("private", {})
                .get("adminme_linked_id"),
                "completion_policy": (event.get("extendedProperties") or {})
                .get("private", {})
                .get("adminme_completion_policy", "manual"),
                "created_at": event.get("created"),
                "updated_at": str(
                    event.get("updated") or ctx.now_factory().isoformat()
                ),
            },
        )

    async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult:
        """[UT-27] gate already checked ``operation.kind``. Phase B routes
        ``events.insert`` through :func:`observation_outbound` per
        [§6.20]; Phase A direct-calls when seams are unset."""
        if operation.kind not in self.write_capabilities:
            raise WriteCapabilityError(
                f"unsupported calendar operation {operation.kind!r}"
            )
        service = self._service
        if service is None:
            return WriteResult(applied=False, external_id=None, suppressed=False)

        payload = operation.payload
        calendar_id = str(
            payload.get("calendar_id")
            or payload.get("calendar_external_id")
            or self._config.get("calendar_id")
            or ""
        )
        event_id = str(
            operation.target_external_id or payload.get("external_event_id") or ""
        )
        expected_revision = payload.get("expected_provider_revision") or payload.get(
            "provider_revision"
        )

        async def _do_write() -> dict[str, Any]:
            events = service.events()
            if operation.kind == "create_event":
                if self._config.get("member_id"):
                    request = events.insert(
                        calendarId=calendar_id,
                        body=_event_body(payload),
                        conferenceDataVersion=1,
                        supportsAttachments=True,
                        sendUpdates=str(payload.get("send_updates") or "none"),
                    )
                else:
                    request = events.insert(
                        calendarId=calendar_id, body=_event_body(payload)
                    )
            elif operation.kind == "update_event":
                request = events.patch(
                    calendarId=calendar_id,
                    eventId=event_id,
                    body=_event_body(payload),
                    conferenceDataVersion=1,
                    supportsAttachments=True,
                    sendUpdates=str(payload.get("send_updates") or "none"),
                )
            elif operation.kind == "delete_event":
                request = events.delete(
                    calendarId=calendar_id,
                    eventId=event_id,
                    sendUpdates=str(payload.get("send_updates") or "none"),
                )
            elif operation.kind == "move_event":
                request = events.move(
                    calendarId=calendar_id,
                    eventId=event_id,
                    destination=str(payload["destination_calendar_id"]),
                    sendUpdates=str(payload.get("send_updates") or "none"),
                )
            else:
                current = events.get(calendarId=calendar_id, eventId=event_id).execute()
                attendees = list(current.get("attendees") or [])
                for attendee in attendees:
                    if attendee.get("self") or attendee.get(
                        "email"
                    ) == self._config.get("user_email"):
                        attendee["responseStatus"] = str(payload["response_status"])
                        break
                else:
                    attendees.append(
                        {
                            "email": str(self._config.get("user_email") or ""),
                            "self": True,
                            "responseStatus": str(payload["response_status"]),
                        }
                    )
                request = events.patch(
                    calendarId=calendar_id,
                    eventId=event_id,
                    body={"attendees": attendees},
                    sendUpdates=str(payload.get("send_updates") or "none"),
                )
            if expected_revision and hasattr(request, "headers"):
                request.headers["If-Match"] = str(expected_revision)
            response = request.execute()
            return response if isinstance(response, dict) else {}

        session = self._observation_session
        manager = self._observation_manager
        event_log = self._observation_event_log

        response: dict[str, Any] = {}
        try:
            if session is not None and manager is not None and event_log is not None:
                # [§6.20] Phase B: observation seam wraps events.insert.
                outbound_result = await observation_outbound(
                    session,
                    f"calendar.{operation.kind}",
                    {
                        "calendar_id": calendar_id,
                        "event_id": event_id or None,
                        "summary": payload.get("summary", ""),
                    },
                    _do_write,
                    manager=manager,
                    event_sink=event_log,
                    target_channel="calendaring-google-calendar",
                    target_identifier=calendar_id,
                )
                if outbound_result.suppressed:
                    # F-3 (HARD): suppressed → unapplied; caller does NOT
                    # emit externally-visible side effects.
                    return WriteResult(applied=False, external_id=None, suppressed=True)
                response = outbound_result.result or {}
            else:
                response = await _do_write()
        except BaseException as exc:  # noqa: BLE001 — classify & emit
            ctx = self._ctx
            severity, error_kind, retry_after_s = self._classify_calendar_error(exc)
            if ctx is not None:
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=retry_after_s,
                )
            raise

        external_id = response.get("id") or event_id or None
        return WriteResult(
            applied=True,
            external_id=str(external_id) if external_id else None,
            suppressed=False,
        )

    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        """Phase A no-op satisfying the Cat-B ABC. Inbound flow runs
        through :meth:`_poll_once`; future push-notification ingress
        (out of scope per parent draft) will dispatch through this hook."""
        return None

    async def _emit_authenticated(self, *, ctx: AdapterContext, op_ref: str) -> None:
        """``adapter.authenticated@v1``; carries op-ref, NEVER the secret."""
        await self._append(
            ctx=ctx,
            event_type="adapter.authenticated",
            payload={
                "adapter_name": self.name,
                "adapter_family": self.family,
                "token_storage_ref": op_ref,
                "authenticated_at": ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scopes": list(self.SCOPES),
            },
        )

    async def _emit_error(
        self,
        *,
        ctx: AdapterContext,
        severity: str,
        error_kind: str,
        error_message: str,
        retry_after_s: int | None,
    ) -> None:
        """``adapter.error@v1`` — drives the supervisor's backoff."""
        payload: dict[str, Any] = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "severity": severity,
            "error_kind": error_kind,
            "error_message": error_message[:2000],
            "retry_after_s": retry_after_s,
            "occurred_at": ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._status.last_error = dict(payload)
        await self._append(ctx=ctx, event_type="adapter.error", payload=payload)

    async def _append(
        self,
        *,
        ctx: AdapterContext,
        event_type: str,
        payload: dict[str, Any],
        causation_id: str | None = None,
        schema_version: int = 1,
    ) -> None:
        """[§7.3, D17] append to the event log; never write projections."""
        await write_adapter_event(
            ctx,
            adapter_name=self.name,
            sensitivity_floor=self.sensitivity_floor,
            config=self._config,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
            schema_version=schema_version,
        )

    @staticmethod
    def _classify_calendar_error(
        exc: BaseException,
    ) -> tuple[str, str, int | None]:
        """``HttpError`` duck-typed via ``.resp.status`` so the SDK is
        not imported at module load — same pattern as Gmail."""
        status: int | None = None
        retry_after: int | None = None
        resp = getattr(exc, "resp", None)
        if resp is not None:
            raw_status: Any = getattr(resp, "status", None)
            if raw_status is None and hasattr(resp, "get"):
                raw_status = resp.get("status")
            try:
                status = int(raw_status) if raw_status is not None else None
            except (TypeError, ValueError):
                status = None
            retry_header: Any = (
                resp.get("retry-after") or resp.get("Retry-After")
                if hasattr(resp, "get")
                else None
            )
            try:
                retry_after = int(retry_header) if retry_header is not None else None
            except (TypeError, ValueError):
                retry_after = None
        if status == 429:
            return ("transient", "rate_limited", retry_after)
        if status in (401, 403):
            return ("fatal", "auth_failed", retry_after)
        if status is not None and 500 <= status < 600:
            return ("transient", "network", retry_after)
        if status is not None and 400 <= status < 500:
            return ("transient", "api_changed", retry_after)
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return ("transient", "network", retry_after)
        return ("transient", "unknown", retry_after)


__all__ = [
    "CredentialsResolver",
    "GOOGLE_CALENDAR_SCOPES",
    "GoogleCalendarAdapter",
    "ServiceFactory",
]
