"""Native Calendar date values stay queryable and retain exact revisions."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.lib.adapters import ProviderConflictError, WriteOperation


def _module() -> Any:
    source = (
        Path(__file__).resolve().parents[4]
        / "packs/adapters/calendaring/apple-calendar/adapter.py"
    )
    spec = importlib.util.spec_from_file_location(
        "calendar_native_dates_fixture", source
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeDate:
    """NSDate exposes epoch seconds; its description is not ISO 8601."""

    def __init__(self, seconds: Any) -> None:
        self.seconds = seconds

    def timeIntervalSince1970(self) -> Any:  # noqa: N802
        return self.seconds

    def __str__(self) -> str:
        return "2031-01-02 16:00:00 +0000"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (datetime(2031, 1, 2, 16), "2031-01-02T16:00:00Z"),
        (
            datetime(2031, 1, 2, 11, 0, 0, 123000, timezone(timedelta(hours=-5))),
            "2031-01-02T16:00:00.123000Z",
        ),
        ("2031-01-02T11:00:00.123-05:00", "2031-01-02T16:00:00.123000Z"),
        (NativeDate(0), "1970-01-01T00:00:00Z"),
        (NativeDate(-0.5), "1969-12-31T23:59:59.500000Z"),
        (
            NativeDate(
                datetime(2031, 1, 2, 16, 0, 0, 123000, timezone.utc).timestamp()
            ),
            "2031-01-02T16:00:00.123000Z",
        ),
    ],
)
def test_normalizes_supported_dates(value: Any, expected: str) -> None:
    assert _module()._to_iso(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        NativeDate(float("nan")),
        NativeDate(float("inf")),
        NativeDate(float("-inf")),
        NativeDate(1e100),
        NativeDate(-1e100),
        NativeDate(True),
        NativeDate("not-a-timestamp"),
        object(),
        "not-a-date",
    ],
)
def test_invalid_dates_never_fall_back_to_provider_description(value: Any) -> None:
    module = _module()
    with pytest.raises(ValueError, match="invalid EventKit date"):
        module._to_iso(value)
    with pytest.raises(ValueError, match="invalid EventKit date"):
        module._to_datetime(value)


# Linux's focused gate requires zero skips; the actual Foundation check is
# collected by the required macOS gate only, with no store/account access.
if sys.platform == "darwin":

    def test_actual_foundation_nsdate_is_normalized_without_opening_a_store() -> None:
        from Foundation import NSDate

        instant = datetime(2031, 1, 2, 16, 0, 0, 123000, timezone.utc)
        native = NSDate.dateWithTimeIntervalSince1970_(instant.timestamp())
        assert _module()._to_iso(native) == "2031-01-02T16:00:00.123000Z"


@pytest.mark.asyncio
async def test_subsecond_native_revision_change_refuses_edit_before_provider_call() -> (
    None
):
    module = _module()
    current = datetime(2031, 1, 2, 16, 0, 0, 900000, timezone.utc)
    event = SimpleNamespace(lastModifiedDate=lambda: NativeDate(current.timestamp()))
    adapter = module.AppleCalendarAdapter(
        config={
            "member_id": "member-fixture",
            "calendar_identifier": "calendar-fixture",
        }
    )
    # This boundary intentionally has no save/update methods. A revision conflict
    # must be detected before reaching either provider mutation.
    adapter._store = SimpleNamespace(event_with_identifier=lambda _: event)
    with pytest.raises(ProviderConflictError):
        await adapter._apply_write_impl(
            WriteOperation(
                "update_event",
                "event-fixture",
                {"expected_provider_revision": "2031-01-02T16:00:00.100000Z"},
            )
        )
