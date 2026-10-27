# mypy: disable-error-code="import-untyped,import-not-found"
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
"""Exact source reads without scans or writes; consent is explicit setup only."""

from __future__ import annotations

import importlib.util
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adminme.adapters.cos_apple_reminders import (
    CoSRemindersLookup,
    CoSRemindersSelection,
)


def _shim() -> Any:
    name = "_adminme_cos_reminders_eventkit_shim"
    if name not in sys.modules:
        path = (
            Path(__file__).parents[2]
            / "packs"
            / "adapters"
            / "reminders"
            / "apple-eventkit"
            / "_eventkit_shim.py"
        )
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("EventKit conversion module is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@dataclass(frozen=True)
class SelectedSnapshot:
    source_id: str
    list_id: str
    list_name: str | None
    items: tuple[Any, ...]


class NativeRemindersReader:
    def __init__(self) -> None:
        from EventKit import EKEventStore  # type: ignore[import-not-found,import-untyped]

        self._event_store_class = EKEventStore
        self._require_permission()
        self._store = EKEventStore.alloc().init()
        self._record = _shim().PyObjCEventStore._record

    def _require_permission(self) -> None:
        from EventKit import EKAuthorizationStatusFullAccess, EKEntityTypeReminder  # type: ignore[import-not-found,import-untyped]

        if (
            self._event_store_class.authorizationStatusForEntityType_(
                EKEntityTypeReminder
            )
            != EKAuthorizationStatusFullAccess
        ):
            raise PermissionError("CoS runtime Reminders permission is not granted")

    def inspect_selection(self, lookup: CoSRemindersLookup) -> dict[str, str]:
        """Setup only: resolve one requested title/type, without reading reminders.

        EventKit has no title query. Its metadata catalog is filtered here and
        only one exact match may leave this method; runtime read() never uses it.
        A CalDAV match still needs private account/source ownership review.
        """
        from EventKit import EKEntityTypeReminder, EKSourceTypeCalDAV, EKSourceTypeLocal  # type: ignore[import-not-found,import-untyped]

        self._require_permission()
        expected = (
            EKSourceTypeLocal if lookup.source_kind == "local" else EKSourceTypeCalDAV
        )
        matches = []
        calendars = self._store.calendarsForEntityType_(EKEntityTypeReminder)
        if calendars is None:
            raise RuntimeError("Reminders metadata is unavailable")
        for calendar in calendars:
            if str(calendar.title()) != lookup.list_name:
                continue
            source = calendar.source()
            source_id = str(source.sourceIdentifier())
            if source.sourceType() != expected or (
                lookup.source_id and lookup.source_id != source_id
            ):
                continue
            if calendar.isSubscribed():
                raise PermissionError("The selected Reminders list is subscribed")
            matches.append(
                {
                    "source_id": source_id,
                    "source_kind": lookup.source_kind,
                    "source_title": str(source.title()),
                    "list_id": str(calendar.calendarIdentifier()),
                    "list_name": lookup.list_name,
                }
            )
        if len(matches) != 1:
            raise ValueError("Reminders metadata selection is missing or ambiguous")
        selected = matches[0]
        calendar = self._store.calendarWithIdentifier_(selected["list_id"])
        source = self._store.sourceWithIdentifier_(selected["source_id"])
        if (
            calendar is None
            or source is None
            or str(calendar.calendarIdentifier()) != selected["list_id"]
            or str(calendar.source().sourceIdentifier()) != selected["source_id"]
            or str(source.sourceIdentifier()) != selected["source_id"]
            or source.sourceType() != expected
            or str(calendar.title()) != lookup.list_name
            or calendar.isSubscribed()
            or not selected["source_id"]
            or not selected["list_id"]
        ):
            raise PermissionError("Reminders metadata selection changed during lookup")
        self._require_permission()
        return selected

    def _source(self, selection: CoSRemindersSelection) -> Any:
        from EventKit import EKSourceTypeCalDAV, EKSourceTypeLocal  # type: ignore[import-not-found,import-untyped]

        self._require_permission()
        source = self._store.sourceWithIdentifier_(selection.source_id)
        expected = (
            EKSourceTypeLocal
            if selection.source_kind == "local"
            else EKSourceTypeCalDAV
        )
        if (
            source is None
            or str(source.sourceIdentifier()) != selection.source_id
            or source.sourceType() != expected
        ):
            raise PermissionError(
                "The reviewed Reminders source is unavailable or changed"
            )
        # EventKit does not expose an authenticated iCloud account identity.
        # The private owner-reviewed source evidence supplies that binding;
        # neither source title nor CalDAV type is treated as an identity proof.
        return source

    def _calendar(self, selection: CoSRemindersSelection) -> Any:
        self._source(selection)
        calendar = self._store.calendarWithIdentifier_(selection.list_id)
        if calendar is not None and (
            str(calendar.calendarIdentifier()) != selection.list_id
            or str(calendar.source().sourceIdentifier()) != selection.source_id
            or calendar.isSubscribed()
        ):
            raise PermissionError("The reviewed Reminders list binding changed")
        return calendar

    def read(self, selection: CoSRemindersSelection) -> SelectedSnapshot:
        calendar = self._calendar(selection)
        if calendar is None:
            # Source and permission are still proved; only this exact list is absent.
            self._require_permission()
            return SelectedSnapshot(selection.source_id, selection.list_id, None, ())
        predicate = self._store.predicateForRemindersInCalendars_([calendar])
        complete = threading.Event()
        result: dict[str, Any] = {}

        def callback(reminders: Any) -> None:
            result["items"] = reminders
            complete.set()

        self._store.fetchRemindersMatchingPredicate_completion_(predicate, callback)
        if not complete.wait(30):
            raise TimeoutError("Selected Reminders list read timed out")
        # A nil callback is an error, not an authoritative empty snapshot.
        records = result.get("items")
        if records is None:
            raise RuntimeError("Selected Reminders snapshot is unavailable")
        current = self._calendar(selection)
        if current is None:
            raise RuntimeError("Selected Reminders list changed during read")
        for item in records:
            native_list = item.calendar()
            if (
                str(native_list.calendarIdentifier()) != selection.list_id
                or str(native_list.source().sourceIdentifier()) != selection.source_id
            ):
                raise PermissionError(
                    "Reminders returned content outside the selected list"
                )
        items = tuple(self._record(item) for item in records)
        self._require_permission()
        return SelectedSnapshot(
            selection.source_id, selection.list_id, str(current.title()), items
        )


def request_reminders_permission() -> None:
    """Explicit setup-only consent; polling never calls this function."""
    from EventKit import EKEventStore  # type: ignore[import-not-found,import-untyped]

    _shim()._ensure_access(EKEventStore.alloc().init())
