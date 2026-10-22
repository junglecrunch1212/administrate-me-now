# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
"""EventKit transport boundary for Apple Reminders.

Tests inject protocol-shaped fakes.  On macOS the default factory uses PyObjC
and exposes every reminder field EventKit actually makes public, including
lists, date components, recurrence, completion, URL, time alarms, and
geofenced enter/leave alarms.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from adminme.lib.eventkit_recurrence import build_eventkit_rule, eventkit_rule_to_rrule


@dataclass
class ReminderRecord:
    calendarItemIdentifier: str
    list_identifier: str
    title: str
    notes: str | None
    priority: int
    due_at: datetime | str | None = None
    due_components: dict[str, Any] | None = None
    start_components: dict[str, Any] | None = None
    completed: bool = False
    completion_date: datetime | None = None
    last_modified_at: datetime | None = None
    url: str | None = None
    alarms: list[dict[str, Any]] = field(default_factory=list)
    recurrence_rules: list[str] = field(default_factory=list)
    _native: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ReminderListRecord:
    calendarIdentifier: str
    title: str
    color: str | None = None
    is_default: bool = False


@runtime_checkable
class EKReminderLike(Protocol):
    calendarItemIdentifier: str
    list_identifier: str
    title: str
    notes: str | None
    priority: int


@runtime_checkable
class EKEventStoreLike(Protocol):
    def build_reminder(
        self,
        *,
        list_identifier: str,
        title: str,
        notes: str | None,
        due_at: datetime | str | None,
        priority: int,
    ) -> EKReminderLike: ...

    def update_reminder(
        self, reminder: EKReminderLike, *, payload: dict[str, Any]
    ) -> EKReminderLike: ...
    def reminder_with_identifier(self, external_id: str) -> EKReminderLike | None: ...
    def save(self, reminder: EKReminderLike, *, commit: bool) -> None: ...
    def remove(self, reminder: EKReminderLike, *, commit: bool) -> None: ...
    def fetch_reminders(
        self, *, list_identifier: str | None = None
    ) -> list[EKReminderLike]: ...
    def fetch_lists(self) -> list[ReminderListRecord]: ...
    def list_name_for_identifier(self, list_identifier: str) -> str: ...
    def create_list(self, *, name: str, color: str | None = None) -> str: ...
    def update_list(
        self, list_identifier: str, *, name: str | None, color: str | None
    ) -> None: ...
    def delete_list(self, list_identifier: str) -> None: ...


class NoopEKEventStore:
    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(
            f"NoopEKEventStore.{name} called without pyobjc-framework-EventKit "
            "and an OS permission grant"
        )

    def build_reminder(self, **_kwargs: Any) -> EKReminderLike:
        return self.__getattr__("build_reminder")()

    def save(self, reminder: EKReminderLike, *, commit: bool) -> None:
        self.__getattr__("save")()

    def remove(self, reminder: EKReminderLike, *, commit: bool) -> None:
        self.__getattr__("remove")()

    def fetch_reminders(
        self, *, list_identifier: str | None = None
    ) -> list[EKReminderLike]:
        return self.__getattr__("fetch_reminders")()

    def list_name_for_identifier(self, list_identifier: str) -> str:
        return self.__getattr__("list_name_for_identifier")()


def _call(value: Any, name: str, default: Any = None) -> Any:
    member = getattr(value, name, None)
    return member() if callable(member) else (default if member is None else member)


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    timestamp = _call(value, "timeIntervalSince1970")
    return (
        datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        if timestamp is not None
        else None
    )


def _nsdate(value: datetime | str) -> Any:
    from Foundation import NSDate

    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, str)
        else value
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return NSDate.dateWithTimeIntervalSince1970_(parsed.timestamp())


def _components_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    result: dict[str, Any] = {}
    for name in ("year", "month", "day", "hour", "minute", "second"):
        raw = _call(value, name)
        if raw is not None and int(raw) != 9223372036854775807:
            result[name] = int(raw)
    zone = _call(_call(value, "timeZone"), "name")
    result["time_zone"] = str(zone) if zone else None
    result["all_day"] = not any(name in result for name in ("hour", "minute", "second"))
    result["floating"] = zone is None
    return result


def _native_components(
    spec: dict[str, Any] | None, due_at: datetime | str | None
) -> Any:
    from Foundation import NSDateComponents, NSTimeZone

    if spec is None and due_at is None:
        return None
    if spec is None:
        parsed = (
            datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            if isinstance(due_at, str)
            else due_at
        )
        assert parsed is not None
        spec = {
            "year": parsed.year,
            "month": parsed.month,
            "day": parsed.day,
            "hour": parsed.hour,
            "minute": parsed.minute,
            "second": parsed.second,
            "time_zone": str(parsed.tzinfo) if parsed.tzinfo else None,
        }
    components = NSDateComponents.alloc().init()
    for name in ("year", "month", "day", "hour", "minute", "second"):
        if spec.get(name) is not None:
            getattr(components, f"set{name.title()}_")(int(spec[name]))
    if spec.get("time_zone"):
        components.setTimeZone_(NSTimeZone.timeZoneWithName_(str(spec["time_zone"])))
    return components


def _ensure_access(store: Any) -> None:
    """Request the modern full-access grant and fail closed on denial."""

    try:
        from EventKit import EKEntityTypeReminder
    except ImportError:  # pragma: no cover - guarded by constructor import
        return
    complete = threading.Event()
    result: dict[str, Any] = {"granted": False, "error": None}

    def callback(granted: Any, error: Any) -> None:
        result.update(granted=bool(granted), error=error)
        complete.set()

    modern = getattr(store, "requestFullAccessToRemindersWithCompletion_", None)
    if callable(modern):
        modern(callback)
    else:  # macOS 13 and earlier
        store.requestAccessToEntityType_completion_(EKEntityTypeReminder, callback)
    if not complete.wait(60):
        raise TimeoutError("EventKit Reminders permission request timed out")
    if not result["granted"]:
        raise PermissionError(
            f"full Apple Reminders access was not granted: {result['error']}"
        )


def _cgcolor_hex(value: Any) -> str | None:
    if value is None:
        return None
    try:
        from Quartz import CGColorGetComponents, CGColorGetNumberOfComponents

        # PyObjC returns an objc.varlist over a C array, not a sized Python
        # sequence. Iterating it can read past the allocation and crash the
        # entire bridge. Keep the color alive and read only its declared size.
        count = CGColorGetNumberOfComponents(value)
        if count not in (2, 4):
            return None
        native_components = CGColorGetComponents(value)
        if native_components is None:
            return None
        components = [native_components[index] for index in range(count)]
    except Exception:  # pragma: no cover - Quartz/PyObjC only
        return None
    if len(components) == 2:
        components = [components[0], components[0], components[0], components[1]]
    if len(components) < 3:
        return None
    return "#" + "".join(
        f"{round(max(0, min(1, float(c))) * 255):02X}" for c in components[:3]
    )


def _set_calendar_color(calendar: Any, value: str | None) -> None:
    if not value:
        return
    raw = value.lstrip("#")
    if len(raw) not in {6, 8}:
        raise ValueError("calendar color must be #RRGGBB or #RRGGBBAA")
    channels = [
        int(raw[index : index + 2], 16) / 255 for index in range(0, len(raw), 2)
    ]
    if len(channels) == 3:
        channels.append(1.0)
    from Quartz import CGColorCreate, CGColorSpaceCreateDeviceRGB

    calendar.setCGColor_(CGColorCreate(CGColorSpaceCreateDeviceRGB(), channels))


class PyObjCEventStore:
    def __init__(self) -> None:
        try:
            from EventKit import EKEventStore
        except ImportError as exc:  # pragma: no cover - macOS-only
            raise RuntimeError(
                "pyobjc-framework-EventKit is required on the member bridge"
            ) from exc
        self._store = EKEventStore.alloc().init()
        _ensure_access(self._store)

    def _calendar(self, identifier: str) -> Any:
        calendar = (
            self._store.defaultCalendarForNewReminders()
            if identifier == "default"
            else self._store.calendarWithIdentifier_(identifier)
        )
        if calendar is None:
            raise LookupError(f"EventKit reminder list not found: {identifier}")
        return calendar

    def _record(self, reminder: Any) -> ReminderRecord:
        calendar = _call(reminder, "calendar")
        due_components = _components_dict(_call(reminder, "dueDateComponents"))
        due_at: str | None = None
        if due_components and all(
            due_components.get(key) for key in ("year", "month", "day")
        ):
            due_at = (
                f"{due_components['year']:04d}-{due_components['month']:02d}-{due_components['day']:02d}"
                f"T{int(due_components.get('hour') or 0):02d}:{int(due_components.get('minute') or 0):02d}:00"
            )
        alarms: list[dict[str, Any]] = []
        for alarm in _call(reminder, "alarms", []) or []:
            location = _call(alarm, "structuredLocation")
            geo = _call(location, "geoLocation") if location is not None else None
            if location is not None and geo is not None:
                coordinate = _call(geo, "coordinate")
                alarms.append(
                    {
                        "kind": "location",
                        "absolute_at": None,
                        "relative_offset_seconds": None,
                        "location": {
                            "title": str(_call(location, "title") or "Location"),
                            "latitude": float(getattr(coordinate, "latitude", 0.0)),
                            "longitude": float(getattr(coordinate, "longitude", 0.0)),
                            "radius_meters": float(_call(location, "radius", 100.0)),
                        },
                        "proximity": {1: "enter", 2: "leave"}.get(
                            int(_call(alarm, "proximity", 0)), "none"
                        ),
                    }
                )
            elif _call(alarm, "absoluteDate") is not None:
                alarms.append(
                    {
                        "kind": "absolute",
                        "absolute_at": _datetime(_call(alarm, "absoluteDate")),
                        "relative_offset_seconds": None,
                        "location": None,
                        "proximity": "none",
                    }
                )
            else:
                alarms.append(
                    {
                        "kind": "relative",
                        "absolute_at": None,
                        "relative_offset_seconds": int(
                            _call(alarm, "relativeOffset", 0)
                        ),
                        "location": None,
                        "proximity": "none",
                    }
                )
        url = _call(reminder, "URL")
        return ReminderRecord(
            calendarItemIdentifier=str(_call(reminder, "calendarItemIdentifier") or ""),
            list_identifier=str(_call(calendar, "calendarIdentifier") or ""),
            title=str(_call(reminder, "title") or ""),
            notes=_call(reminder, "notes"),
            priority=int(_call(reminder, "priority", 0)),
            due_at=due_at,
            due_components=due_components,
            start_components=_components_dict(_call(reminder, "startDateComponents")),
            completed=bool(_call(reminder, "isCompleted", False)),
            completion_date=_datetime(_call(reminder, "completionDate")),
            last_modified_at=_datetime(_call(reminder, "lastModifiedDate")),
            url=str(_call(url, "absoluteString") or url) if url is not None else None,
            alarms=alarms,
            recurrence_rules=[
                eventkit_rule_to_rrule(rule)
                for rule in (_call(reminder, "recurrenceRules", []) or [])
            ],
            _native=reminder,
        )

    def build_reminder(
        self,
        *,
        list_identifier: str,
        title: str,
        notes: str | None,
        due_at: datetime | str | None,
        priority: int,
    ) -> ReminderRecord:
        from EventKit import EKReminder

        native = EKReminder.reminderWithEventStore_(self._store)
        record = ReminderRecord(
            "", list_identifier, title, notes, priority, due_at=due_at, _native=native
        )
        return self.update_reminder(
            record,
            payload={
                "target_list_id": list_identifier,
                "title": title,
                "notes": notes,
                "due_at": due_at,
                "priority": priority,
            },
        )

    def reminder_with_identifier(self, external_id: str) -> ReminderRecord | None:
        native = self._store.calendarItemWithIdentifier_(external_id)
        return self._record(native) if native is not None else None

    def update_reminder(
        self, reminder: EKReminderLike, *, payload: dict[str, Any]
    ) -> ReminderRecord:
        from Foundation import NSURL

        record = (
            reminder if isinstance(reminder, ReminderRecord) else self._record(reminder)
        )
        native = record._native
        list_id = payload.get("target_list_id") or payload.get("list_external_id")
        if list_id:
            native.setCalendar_(self._calendar(str(list_id)))
            record.list_identifier = str(list_id)
        for key, setter in (
            ("title", "setTitle_"),
            ("notes", "setNotes_"),
            ("priority", "setPriority_"),
        ):
            if key in payload:
                getattr(native, setter)(payload[key])
                setattr(record, key, payload[key])
        if "url" in payload:
            native.setURL_(
                NSURL.URLWithString_(str(payload["url"])) if payload["url"] else None
            )
        if "due" in payload or "due_at" in payload:
            native.setDueDateComponents_(
                _native_components(payload.get("due"), payload.get("due_at"))
            )
        if "start" in payload:
            native.setStartDateComponents_(
                _native_components(payload.get("start"), None)
            )
        if "completed" in payload:
            native.setCompleted_(bool(payload["completed"]))
        if "completion_at" in payload and payload["completion_at"]:
            native.setCompletionDate_(_nsdate(payload["completion_at"]))
        if "alarms" in payload:
            native.setAlarms_(
                [self._alarm(spec) for spec in payload.get("alarms") or []]
            )
        if "recurrence_rules" in payload:
            import EventKit

            native.setRecurrenceRules_(
                [
                    build_eventkit_rule(EventKit, rule)
                    for rule in payload.get("recurrence_rules") or []
                ]
            )
        return record

    @staticmethod
    def _alarm(spec: dict[str, Any]) -> Any:
        from EventKit import EKAlarm, EKStructuredLocation

        if spec["kind"] == "absolute":
            return EKAlarm.alarmWithAbsoluteDate_(_nsdate(spec["absolute_at"]))
        if spec["kind"] == "relative":
            return EKAlarm.alarmWithRelativeOffset_(
                float(spec["relative_offset_seconds"])
            )
        from CoreLocation import CLLocation

        location_spec = spec["location"]
        location = EKStructuredLocation.locationWithTitle_(location_spec["title"])
        location.setGeoLocation_(
            CLLocation.alloc().initWithLatitude_longitude_(
                float(location_spec["latitude"]), float(location_spec["longitude"])
            )
        )
        location.setRadius_(float(location_spec.get("radius_meters") or 100.0))
        alarm = EKAlarm.alarmWithStructuredLocation_(location)
        proximity = str(spec.get("proximity") or "")
        alarm.setProximity_({"enter": 1, "leave": 2}.get(proximity, 0))
        return alarm

    def save(self, reminder: EKReminderLike, *, commit: bool) -> None:
        record = (
            reminder if isinstance(reminder, ReminderRecord) else self._record(reminder)
        )
        ok, error = self._store.saveReminder_commit_error_(record._native, commit, None)
        if not ok:
            raise RuntimeError(f"EventKit reminder save failed: {error}")
        record.calendarItemIdentifier = str(
            _call(record._native, "calendarItemIdentifier") or ""
        )

    def remove(self, reminder: EKReminderLike, *, commit: bool) -> None:
        record = (
            reminder if isinstance(reminder, ReminderRecord) else self._record(reminder)
        )
        ok, error = self._store.removeReminder_commit_error_(
            record._native, commit, None
        )
        if not ok:
            raise RuntimeError(f"EventKit reminder delete failed: {error}")

    def fetch_reminders(
        self, *, list_identifier: str | None = None
    ) -> list[EKReminderLike]:
        calendars = [self._calendar(list_identifier)] if list_identifier else None
        predicate = self._store.predicateForRemindersInCalendars_(calendars)
        completed = threading.Event()
        result: list[Any] = []

        def callback(reminders: Any) -> None:
            result.extend(reminders or [])
            completed.set()

        self._store.fetchRemindersMatchingPredicate_completion_(predicate, callback)
        if not completed.wait(30):
            raise TimeoutError("EventKit reminder fetch timed out")
        return [self._record(item) for item in result]

    def fetch_lists(self) -> list[ReminderListRecord]:
        from EventKit import EKEntityTypeReminder

        default = self._store.defaultCalendarForNewReminders()
        default_id = str(_call(default, "calendarIdentifier") or "")
        return [
            ReminderListRecord(
                calendarIdentifier=str(_call(calendar, "calendarIdentifier") or ""),
                title=str(_call(calendar, "title") or ""),
                color=_cgcolor_hex(_call(calendar, "CGColor")),
                is_default=str(_call(calendar, "calendarIdentifier") or "")
                == default_id,
            )
            for calendar in (
                self._store.calendarsForEntityType_(EKEntityTypeReminder) or []
            )
        ]

    def list_name_for_identifier(self, list_identifier: str) -> str:
        return str(_call(self._calendar(list_identifier), "title") or list_identifier)

    def create_list(self, *, name: str, color: str | None = None) -> str:
        from EventKit import EKCalendar, EKEntityTypeReminder

        calendar = EKCalendar.calendarForEntityType_eventStore_(
            EKEntityTypeReminder, self._store
        )
        calendar.setTitle_(name)
        calendar.setSource_(
            _call(self._store.defaultCalendarForNewReminders(), "source")
        )
        _set_calendar_color(calendar, color)
        ok, error = self._store.saveCalendar_commit_error_(calendar, True, None)
        if not ok:
            raise RuntimeError(f"EventKit list create failed: {error}")
        return str(_call(calendar, "calendarIdentifier") or "")

    def update_list(
        self, list_identifier: str, *, name: str | None, color: str | None
    ) -> None:
        calendar = self._calendar(list_identifier)
        if name is not None:
            calendar.setTitle_(name)
        _set_calendar_color(calendar, color)
        ok, error = self._store.saveCalendar_commit_error_(calendar, True, None)
        if not ok:
            raise RuntimeError(f"EventKit list update failed: {error}")

    def delete_list(self, list_identifier: str) -> None:
        ok, error = self._store.removeCalendar_commit_error_(
            self._calendar(list_identifier), True, None
        )
        if not ok:
            raise RuntimeError(f"EventKit list delete failed: {error}")


def default_event_store() -> EKEventStoreLike:
    return PyObjCEventStore()


__all__ = [
    "EKEventStoreLike",
    "EKReminderLike",
    "NoopEKEventStore",
    "PyObjCEventStore",
    "ReminderListRecord",
    "ReminderRecord",
    "default_event_store",
]
