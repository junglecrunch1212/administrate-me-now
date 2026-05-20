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

from datetime import datetime
from typing import Any, Protocol, runtime_checkable


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
        self, *, calendar_identifier: str, start: datetime, end: datetime,
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


class NoopEKEventStore:
    """PM-27 default for the adapter's ``ek_event_store_factory``. Returns
    sentinels and raises loudly on every operation — the failure mode if
    an operator forgets to wire the real factory is a noisy RuntimeError,
    not a silently dropped write. The supervisor wires a real factory at
    startup (prompt 16 bootstrap-wizard); tests inject strict fakes."""

    def fetch_events(
        self, *, calendar_identifier: str, start: datetime, end: datetime,
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
        raise RuntimeError(
            "NoopEKEventStore.build_event called without a real factory"
        )

    def save_event(self, event: EKEventLike, *, commit: bool) -> None:
        raise RuntimeError(
            "NoopEKEventStore.save_event called without a real factory"
        )


__all__ = [
    "EKEventLike",
    "EKEventStoreLike",
    "NoopEKEventStore",
]
