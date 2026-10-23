"""The final EventKit write boundary rechecks the current native container."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from adminme.lib.adapters import WriteOperation
from tests.unit.adapters.calendaring.test_apple_calendar import AppleCalendarAdapter
from tests.unit.adapters.reminders.test_apple_eventkit_retrofit import (
    AppleEventKitAdapter,
)


@pytest.mark.parametrize("provider", ["calendar", "reminders"])
async def test_moved_native_target_is_denied_before_any_update_or_save(provider):
    marker = {
        "event_id": "permission-one",
        "revision": 1,
        "coordinates_sha256": "a" * 64,
    }
    if provider == "calendar":
        adapter = AppleCalendarAdapter(config={"calendar_identifier": "selected"})
        native = SimpleNamespace(
            calendar=lambda: SimpleNamespace(calendarIdentifier=lambda: "outside")
        )
        adapter._store = SimpleNamespace(event_with_identifier=lambda _target: native)
        operation = WriteOperation(
            "update_event",
            "native-target",
            {
                "calendar_id": "selected",
                "apple_service_permission": marker,
                "apple_service_allowed_container_ids": ["selected"],
                "summary": "Must not apply",
            },
        )
    else:
        adapter = AppleEventKitAdapter(config={"target_list_identifier": "selected"})
        adapter._ek_store = SimpleNamespace(
            reminder_with_identifier=lambda _target: SimpleNamespace(
                list_identifier="outside"
            )
        )
        operation = WriteOperation(
            "update_reminder",
            "native-target",
            {
                "list_external_id": "selected",
                "apple_service_permission": marker,
                "apple_service_allowed_container_ids": ["selected"],
                "title": "Must not apply",
            },
        )
    # These strict stores expose no mutation operation, so reaching any update
    # or save would fail instead of silently masking a boundary regression.
    with pytest.raises(ValueError, match="outside its approved source"):
        await adapter.apply_write(operation)


@pytest.mark.parametrize("provider", ["calendar", "reminders"])
async def test_create_cannot_use_destination_missing_from_current_grant(provider):
    if provider == "calendar":
        adapter = AppleCalendarAdapter(config={"calendar_identifier": "outside"})
        adapter._store = SimpleNamespace()
        operation = WriteOperation(
            "create_event",
            None,
            {
                "calendar_id": "outside",
                "apple_service_permission": {"revision": 1},
                "apple_service_allowed_container_ids": ["selected"],
            },
        )
    else:
        adapter = AppleEventKitAdapter(config={"target_list_identifier": "outside"})
        adapter._ek_store = SimpleNamespace()
        operation = WriteOperation(
            "create_reminder",
            None,
            {
                "list_external_id": "outside",
                "apple_service_permission": {"revision": 1},
                "apple_service_allowed_container_ids": ["selected"],
            },
        )
    with pytest.raises(ValueError, match="outside the current permission"):
        await adapter.apply_write(operation)
