"""
Protocol-shaped stand-in for EventKit's ``EKEventStore`` (EKEntityTypeEvent).

Production wires ``pyobjc-framework-EventKit``; this shim describes the
narrow EventKit surface the Apple Calendar adapter relies on so tests
can substitute a fake without the pyobjc dependency. Mirrors
``packs/adapters/reminders/apple-eventkit/_eventkit_shim.py`` — the
analogous shim for the ``EKReminder`` (EKEntityTypeReminder) surface.

If pyobjc is unavailable in the Phase A test environment, the production
wiring lands in the bootstrap-wizard (prompt 16). Until then the
``NoopEKEventStore`` default raises on every call so any accidental
production path without a real factory wired fails loudly instead of
silently no-op-ing.

The shim covers three operations the c2 + c3 + c4 adapter relies on:

- ``fetch_events`` — inbound poll surface. Mirrors EventKit's
  ``predicateForEventsWithStartDate_endDate_calendars_`` +
  ``eventsMatchingPredicate_`` per
  ``docs/reference/apple-eventkit/ekeventstore.md``. Bridged as a
  synchronous list-return so the adapter's polling loop does not need
  to handle the Objective-C completion-handler callback shape directly.
- ``build_event`` — outbound construction. Wraps
  ``EKEvent.eventWithEventStore_`` plus the
  ``title`` / ``startDate`` / ``endDate`` / ``location`` / ``notes`` /
  ``attendees`` setters per ``docs/reference/apple-eventkit/ekevent.md``.
- ``save_event`` — outbound persist. Wraps
  ``EKEventStore.saveEvent_span_error_(event, EKSpanThisEvent, error)``
  per ``docs/reference/apple-eventkit/ekeventstore.md``.
  ``eventIdentifier`` is stamped on the event during save and becomes
  the round-trip dedupe key emitted on
  ``calendar.event_added@v1.external_event_id``.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from adminme.lib.eventkit_recurrence import build_eventkit_rule


@runtime_checkable
class EKEventLike(Protocol):
    """Narrow ``EKEvent`` surface — production: pyobjc's ``EKEvent``;
    tests: a dataclass-shaped fake with the same accessor names.
    ``eventIdentifier`` is the stable dedupe key recorded on
    ``calendar.event_added@v1.external_event_id``."""

    def eventIdentifier(self) -> str: ...
    def title(self) -> str | None: ...
    def startDate(self) -> Any: ...
    def endDate(self) -> Any: ...
    def location(self) -> str | None: ...
    def notes(self) -> str | None: ...
    def attendees(self) -> list[Any] | None: ...


@runtime_checkable
class EKEventStoreLike(Protocol):
    """Narrow ``EKEventStore`` surface for EKEntityTypeEvent. Tests
    substitute dataclass-shaped fakes; the supervisor wires the real
    PyObjC binding at startup (prompt 16 bootstrap wizard)."""

    def fetch_events(
        self,
        *,
        calendar_identifier: str,
        start: datetime,
        end: datetime,
    ) -> list[EKEventLike]: ...

    def build_event(
        self,
        *,
        calendar_identifier: str,
        summary: str,
        start: Any,
        end: Any,
        location: str | None,
        notes: str | None,
        attendees: list[Any] | None,
    ) -> EKEventLike: ...

    def save_event(self, event: EKEventLike, *, commit: bool) -> None: ...

    def event_with_identifier(self, external_id: str) -> EKEventLike | None: ...

    def remove_event(self, event: EKEventLike, *, span: str, commit: bool) -> None: ...

    def update_event(
        self, event: EKEventLike, *, payload: dict[str, Any]
    ) -> EKEventLike: ...


class NoopEKEventStore:
    """PM-27 default for the adapter's ``ek_event_store_factory``. Returns
    sentinels and raises loudly on every operation — the failure mode if
    an operator forgets to wire the real factory is a noisy RuntimeError,
    not a silently dropped write. The supervisor wires a real factory at
    startup (prompt 16 bootstrap-wizard); tests inject strict fakes."""

    def fetch_events(
        self,
        *,
        calendar_identifier: str,
        start: datetime,
        end: datetime,
    ) -> list[EKEventLike]:
        raise RuntimeError(
            "NoopEKEventStore.fetch_events called without a real factory "
            "(EventKit access requires pyobjc-framework-EventKit + an OS "
            "permission grant — see prompt 16 bootstrap wizard)"
        )

    def build_event(
        self,
        *,
        calendar_identifier: str,
        summary: str,
        start: Any,
        end: Any,
        location: str | None,
        notes: str | None,
        attendees: list[Any] | None,
    ) -> EKEventLike:
        raise RuntimeError("NoopEKEventStore.build_event called without a real factory")

    def save_event(self, event: EKEventLike, *, commit: bool) -> None:
        raise RuntimeError("NoopEKEventStore.save_event called without a real factory")

    def event_with_identifier(self, external_id: str) -> EKEventLike | None:
        raise RuntimeError("NoopEKEventStore.event_with_identifier called without a real factory")

    def remove_event(self, event: EKEventLike, *, span: str, commit: bool) -> None:
        raise RuntimeError("NoopEKEventStore.remove_event called without a real factory")

    def update_event(self, event: EKEventLike, *, payload: dict[str, Any]) -> EKEventLike:
        raise RuntimeError("NoopEKEventStore.update_event called without a real factory")


def _nsdate(value: Any) -> Any:
    from Foundation import NSDate

    if hasattr(value, "timeIntervalSince1970"):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError(f"unsupported EventKit date value: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return NSDate.dateWithTimeIntervalSince1970_(parsed.timestamp())


class PyObjCEventStore:
    """Production EventKit transport for a signed-in macOS bridge account."""

    def __init__(self) -> None:
        try:
            from EventKit import EKEventStore
        except ImportError as exc:  # pragma: no cover - macOS-only
            raise RuntimeError(
                "pyobjc-framework-EventKit is required on the member bridge"
            ) from exc
        self._store = EKEventStore.alloc().init()
        self._ensure_access()

    def _ensure_access(self) -> None:
        from EventKit import EKEntityTypeEvent

        completed = threading.Event()
        result: dict[str, Any] = {"granted": False, "error": None}

        def callback(granted: Any, error: Any) -> None:
            result.update(granted=bool(granted), error=error)
            completed.set()

        modern = getattr(self._store, "requestFullAccessToEventsWithCompletion_", None)
        if callable(modern):
            modern(callback)
        else:  # macOS 13 and earlier
            self._store.requestAccessToEntityType_completion_(EKEntityTypeEvent, callback)
        if not completed.wait(60):
            raise TimeoutError("EventKit Calendar permission request timed out")
        if not result["granted"]:
            raise PermissionError(f"full Apple Calendar access was not granted: {result['error']}")

    def _calendar(self, identifier: str) -> Any:
        calendar = (
            self._store.defaultCalendarForNewEvents()
            if identifier == "default"
            else self._store.calendarWithIdentifier_(identifier)
        )
        if calendar is None:
            raise LookupError(f"EventKit calendar not found: {identifier}")
        return calendar

    def fetch_events(
        self, *, calendar_identifier: str, start: datetime, end: datetime
    ) -> list[EKEventLike]:
        predicate = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            _nsdate(start), _nsdate(end), [self._calendar(calendar_identifier)]
        )
        return list(self._store.eventsMatchingPredicate_(predicate) or [])

    def build_event(
        self,
        *,
        calendar_identifier: str,
        summary: str,
        start: Any,
        end: Any,
        location: str | None,
        notes: str | None,
        attendees: list[Any] | None,
    ) -> EKEventLike:
        from EventKit import EKEvent

        if attendees:
            raise ValueError(
                "EventKit does not expose attendee mutation; use the Google adapter "
                "for Google-backed calendars"
            )
        event = EKEvent.eventWithEventStore_(self._store)
        event.setCalendar_(self._calendar(calendar_identifier))
        event.setTitle_(summary)
        event.setStartDate_(_nsdate(start))
        event.setEndDate_(_nsdate(end))
        if location is not None:
            event.setLocation_(location)
        if notes is not None:
            event.setNotes_(notes)
        return event

    def save_event(self, event: EKEventLike, *, commit: bool) -> None:
        from EventKit import EKSpanThisEvent

        ok, error = self._store.saveEvent_span_commit_error_(
            event, EKSpanThisEvent, commit, None
        )
        if not ok:
            raise RuntimeError(f"EventKit save failed: {error}")

    def event_with_identifier(self, external_id: str) -> EKEventLike | None:
        return self._store.eventWithIdentifier_(external_id)

    def remove_event(self, event: EKEventLike, *, span: str, commit: bool) -> None:
        from EventKit import EKSpanFutureEvents, EKSpanThisEvent

        native_span = EKSpanFutureEvents if span == "future" else EKSpanThisEvent
        ok, error = self._store.removeEvent_span_commit_error_(
            event, native_span, commit, None
        )
        if not ok:
            raise RuntimeError(f"EventKit delete failed: {error}")

    def update_event(self, event: EKEventLike, *, payload: dict[str, Any]) -> EKEventLike:
        setters = {
            "summary": "setTitle_",
            "location": "setLocation_",
            "description": "setNotes_",
            "notes": "setNotes_",
            "all_day": "setAllDay_",
        }
        for key, setter in setters.items():
            if key in payload:
                getattr(event, setter)(payload[key])
        if "start" in payload:
            event.setStartDate_(_nsdate(payload["start"]))
        if "end" in payload:
            event.setEndDate_(_nsdate(payload["end"]))
        calendar_identifier = payload.get("calendar_identifier") or payload.get("calendar_id")
        if calendar_identifier:
            event.setCalendar_(self._calendar(str(calendar_identifier)))
        if "url" in payload or "source_url" in payload:
            from Foundation import NSURL

            raw_url = payload.get("url") or payload.get("source_url")
            event.setURL_(NSURL.URLWithString_(str(raw_url)) if raw_url else None)
        if "time_zone" in payload or "start_time_zone" in payload:
            from Foundation import NSTimeZone

            name = payload.get("time_zone") or payload.get("start_time_zone")
            event.setTimeZone_(NSTimeZone.timeZoneWithName_(str(name)) if name else None)
        if "alarms" in payload:
            from EventKit import EKAlarm

            alarms = []
            for spec in payload.get("alarms") or []:
                if spec.get("trigger_at"):
                    alarm = EKAlarm.alarmWithAbsoluteDate_(_nsdate(spec["trigger_at"]))
                else:
                    alarm = EKAlarm.alarmWithRelativeOffset_(
                        float(spec.get("relative_offset_seconds") or 0)
                    )
                alarms.append(alarm)
            event.setAlarms_(alarms)
        recurrence_lines = payload.get("recurrence_rules")
        if recurrence_lines is None and isinstance(payload.get("recurrence"), dict):
            recurrence_lines = payload["recurrence"].get("lines")
        if recurrence_lines is not None:
            import EventKit

            event.setRecurrenceRules_([
                build_eventkit_rule(EventKit, line) for line in recurrence_lines or []
            ])
        return event


def default_event_store() -> EKEventStoreLike:
    return PyObjCEventStore()


__all__ = [
    "EKEventLike",
    "EKEventStoreLike",
    "NoopEKEventStore",
    "PyObjCEventStore",
    "default_event_store",
]
