"""
Protocol-shaped stand-in for EventKit's ``EKEventStore``.

Production wires ``pyobjc-framework-EventKit``; this shim describes the
narrow EventKit surface our adapter relies on so tests can substitute a
fake without the pyobjc dependency. Per Assumption A8 in
``prompts/_active/11c-adapter-reminders-bidirectional.md``: if pyobjc is
unavailable in the Phase A test environment, the production wiring
lands in the bootstrap-wizard (prompt 16). Until then the
``NoopEKEventStore`` default raises on every call so any accidental
production path without a real factory wired fails loudly instead of
silently no-op-ing.

The shim covers four operations the c2 + c3 + c4 adapter relies on:

- ``build_reminder`` — constructs an ``EKReminder``-like object on the
  store (mirrors Apple's ``EKReminder.init(eventStore:)`` plus setters
  for ``title`` / ``notes`` / ``dueDateComponents`` / ``priority`` /
  ``calendar`` per ``docs/reference/apple-eventkit/create.md``).
- ``save`` — persists the reminder via ``EKEventStore.save(_:commit:)``
  per ``docs/reference/apple-eventkit/ekeventstore.md``.
  ``calendarItemIdentifier`` is stamped on the reminder during save and
  becomes the round-trip dedupe key on the emitted
  ``reminder.list_item.synced`` event.
- ``fetch_reminders`` — c3's inbound poll surface (mirrors
  ``EKEventStore.fetchReminders(matching:completion:)`` per
  ``docs/reference/apple-eventkit/fetch.md`` and ``ekeventstore.md``).
  Bridged here as a synchronous list-return so the adapter's polling
  loop does not need to handle the Objective-C completion-handler
  callback shape directly.
- ``list_name_for_identifier`` — convenience over
  ``EKEventStore.calendar(withIdentifier:)`` returning just the name —
  the never-sync predicate needs the human-readable list name, not the
  full ``EKCalendar`` object.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class EKReminderLike(Protocol):
    """The narrow subset of ``EKReminder`` the adapter touches.

    Production: pyobjc's ``EKReminder``. Tests: a dataclass-shaped fake
    with the same attribute names. ``calendarItemIdentifier`` is stamped
    by ``EKEventStore.save(_:commit:)`` and is the dedupe key recorded
    on the emitted ``reminder.list_item.synced`` event."""

    calendarItemIdentifier: str
    title: str
    notes: str | None
    priority: int


@runtime_checkable
class EKEventStoreLike(Protocol):
    """The narrow EKEventStore surface the adapter relies on. Covers
    outbound writes (c2: ``build_reminder`` + ``save``), inbound reads
    (c3: ``fetch_reminders`` + ``list_name_for_identifier``), and the
    delete path (future c-prompts: ``remove``)."""

    def build_reminder(
        self,
        *,
        list_identifier: str,
        title: str,
        notes: str | None,
        due_at: datetime | str | None,
        priority: int,
    ) -> EKReminderLike: ...

    def save(self, reminder: EKReminderLike, *, commit: bool) -> None: ...

    def remove(self, reminder: EKReminderLike, *, commit: bool) -> None: ...

    def fetch_reminders(
        self, *, list_identifier: str | None = None
    ) -> list[EKReminderLike]: ...

    def list_name_for_identifier(self, list_identifier: str) -> str: ...


class NoopEKEventStore:
    """PM-27 default for the adapter's ``ek_event_store_factory``.

    Returns sentinels and raises loudly on every operation — the failure
    mode if an operator forgets to wire the real factory is a noisy
    RuntimeError, not a silently dropped write. The supervisor wires a
    real factory at startup (prompt 16 bootstrap-wizard) and tests inject
    strict fakes."""

    def build_reminder(
        self,
        *,
        list_identifier: str,
        title: str,
        notes: str | None,
        due_at: datetime | str | None,
        priority: int,
    ) -> EKReminderLike:
        raise RuntimeError(
            "NoopEKEventStore.build_reminder called without a real factory "
            "(EventKit access requires pyobjc-framework-EventKit + an OS "
            "permission grant — see prompt 16 bootstrap wizard)"
        )

    def save(self, reminder: EKReminderLike, *, commit: bool) -> None:
        raise RuntimeError(
            "NoopEKEventStore.save called without a real factory"
        )

    def remove(self, reminder: EKReminderLike, *, commit: bool) -> None:
        raise RuntimeError(
            "NoopEKEventStore.remove called without a real factory"
        )

    def fetch_reminders(
        self, *, list_identifier: str | None = None
    ) -> list[EKReminderLike]:
        raise RuntimeError(
            "NoopEKEventStore.fetch_reminders called without a real factory"
        )

    def list_name_for_identifier(self, list_identifier: str) -> str:
        raise RuntimeError(
            "NoopEKEventStore.list_name_for_identifier called without a "
            "real factory"
        )


__all__ = [
    "EKEventStoreLike",
    "EKReminderLike",
    "NoopEKEventStore",
]
