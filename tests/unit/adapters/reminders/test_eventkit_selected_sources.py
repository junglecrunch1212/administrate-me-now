"""Selected Reminders containers bound native reads as well as bridge emits."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[4]
PACK = ROOT / "packs/adapters/reminders/apple-eventkit"


def _module(filename: str) -> Any:
    name = "selected_reminders_" + filename.removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, PACK / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _ForbiddenContent:
    calendarIdentifier = "forbidden-list"
    list_identifier = "forbidden-list"

    def __getattr__(self, name: str) -> Any:
        pytest.fail(f"unselected content accessed: {name}")


def test_native_selected_lists_resolve_only_selected_ids(monkeypatch: Any) -> None:
    shim = _module("_eventkit_shim.py")
    selected = SimpleNamespace(
        calendarIdentifier="selected-list", title="Personal", CGColor=None
    )
    resolved: list[str] = []

    class Store:
        def calendarsForEntityType_(self, _kind: Any) -> None:
            pytest.fail("selected acquisition must not enumerate all calendars")

        def calendarWithIdentifier_(self, identifier: str) -> Any:
            resolved.append(identifier)
            assert identifier == "selected-list"
            return selected

        def defaultCalendarForNewReminders(self) -> Any:
            return _ForbiddenContent()

    monkeypatch.setitem(
        sys.modules, "EventKit", SimpleNamespace(EKEntityTypeReminder=1)
    )
    transport = shim.PyObjCEventStore.__new__(shim.PyObjCEventStore)
    transport._store = Store()
    result = transport.fetch_lists(list_identifiers=("selected-list",))
    assert [(row.calendarIdentifier, row.title) for row in result] == [
        ("selected-list", "Personal")
    ]
    assert resolved == ["selected-list"]
    assert transport.fetch_lists(list_identifiers=()) == []
    assert resolved == ["selected-list"]


def test_native_predicate_and_conversion_are_confined_to_selected_list() -> None:
    shim = _module("_eventkit_shim.py")
    selected_calendar = SimpleNamespace(calendarIdentifier="selected-list")
    selected_reminder = SimpleNamespace(calendar=selected_calendar)
    forbidden_reminder = SimpleNamespace(calendar=_ForbiddenContent())
    converted: list[Any] = []

    class Store:
        def calendarWithIdentifier_(self, identifier: str) -> Any:
            assert identifier == "selected-list"
            return selected_calendar

        def predicateForRemindersInCalendars_(self, calendars: list[Any]) -> str:
            assert calendars == [selected_calendar]
            return "selected-predicate"

        def fetchRemindersMatchingPredicate_completion_(
            self, predicate: str, callback: Any
        ) -> None:
            assert predicate == "selected-predicate"
            callback([forbidden_reminder, selected_reminder])

    transport = shim.PyObjCEventStore.__new__(shim.PyObjCEventStore)
    transport._store = Store()
    transport._record = lambda value: converted.append(value) or "selected-record"
    assert transport.fetch_reminders(list_identifier="selected-list") == [
        "selected-record"
    ]
    assert converted == [selected_reminder]


def _adapter(*, allowed: Any, role: str = "adult") -> Any:
    module = _module("adapter.py")
    return module.AppleEventKitAdapter(
        config={
            "member_id": "member-test",
            "target_list_identifier": "selected-list",
            "allowed_container_ids": allowed,
            "emit_schema_version": 2,
        },
        member_role_resolver=lambda _member: role,
    )


@pytest.mark.parametrize("invalid", [None, "selected-list", [None], [""], [" "]])
def test_malformed_container_ceiling_cannot_fall_back_to_all_lists(
    invalid: Any,
) -> None:
    with pytest.raises(ValueError, match="allowed_container_ids"):
        _adapter(allowed=invalid)


def test_selected_fetch_preserves_list_and_item_never_sync_rules() -> None:
    adapter = _adapter(allowed=["selected-list", "work-list"])
    selected = SimpleNamespace(
        list_identifier="selected-list", title="Allowed item", notes=None
    )
    tagged = SimpleNamespace(
        list_identifier="selected-list", title="#private item", notes=None
    )
    fetched: list[str] = []

    class Store:
        def list_name_for_identifier(self, identifier: str) -> str:
            assert identifier in {"selected-list", "work-list"}
            return "Personal" if identifier == "selected-list" else "Work"

        def fetch_reminders(self, *, list_identifier: str) -> list[Any]:
            fetched.append(list_identifier)
            assert list_identifier == "selected-list"
            return [_ForbiddenContent(), tagged, selected]

    adapter._ek_store = Store()
    assert adapter._fetch_reminders_filtered() == [selected]
    assert fetched == ["selected-list"]


@pytest.mark.parametrize("role", ["child", "ambient"])
def test_excluded_members_do_not_fetch_reminder_content(role: str) -> None:
    adapter = _adapter(allowed=["selected-list"], role=role)
    adapter._ek_store = _ForbiddenContent()
    assert adapter._fetch_reminders_filtered() == []


async def test_empty_ceiling_and_old_unselected_cache_emit_no_content() -> None:
    adapter = _adapter(allowed=[])
    adapter._initial_sync_complete = True
    adapter._known_lists = {"forbidden-list": {"list_name": "Private list"}}
    adapter._known_inbound_ids = {"forbidden-item"}
    adapter._inbound_snapshots = {
        "forbidden-item": {"list_external_id": "forbidden-list"}
    }
    emitted: list[Any] = []

    class Store:
        def fetch_lists(self, *, list_identifiers: tuple[str, ...]) -> list[Any]:
            assert list_identifiers == ()
            # A bad transport result still cannot disclose metadata to ingest.
            return [_ForbiddenContent()]

        def fetch_reminders(self, **_kwargs: Any) -> None:
            pytest.fail("an empty ceiling must never request reminder content")

    async def capture(**kwargs: Any) -> None:
        emitted.append(kwargs)

    adapter._ek_store = Store()
    adapter._append = capture
    ctx = SimpleNamespace(now_factory=lambda: datetime.now(timezone.utc))
    assert await adapter._inbound_poll_once(ctx=ctx) == 0
    assert emitted == []
    assert adapter._known_lists == {}
    assert adapter._known_inbound_ids == set()
