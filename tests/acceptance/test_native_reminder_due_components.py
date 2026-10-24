"""macOS-only Foundation proof; no EventStore, permission prompt or provider IO.

The focused platform lane selects this file only on hosted macOS. The guard
keeps broad cross-platform collection safe; a skip is never native evidence.
"""

from __future__ import annotations

from datetime import datetime
import importlib.util
import os
from pathlib import Path
import sys

import pytest

from adminme.products.openclaw.apple_reminders import due_components


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("CI") != "true",
    reason="native Foundation boundary runs only on isolated hosted macOS",
)
def test_exact_member_due_instant_reaches_native_calendar_components() -> None:
    from Foundation import NSCalendar, NSCalendarIdentifierGregorian

    path = (
        Path(__file__).resolve().parents[2]
        / "packs/adapters/reminders/apple-eventkit/_eventkit_shim.py"
    )
    name = "_native_reminder_due_components_proof"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    shim = importlib.util.module_from_spec(spec)
    sys.modules[name] = shim
    spec.loader.exec_module(shim)
    requested = datetime.fromisoformat("2026-10-24T09:30:00-04:00")
    payload = due_components(requested)
    native = shim._native_components(payload, None)
    assert native.year() == 2026 and native.month() == 10 and native.day() == 24
    assert native.hour() == 13 and native.minute() == 30 and native.second() == 0
    assert native.timeZone() is not None
    calendar = NSCalendar.alloc().initWithCalendarIdentifier_(
        NSCalendarIdentifierGregorian
    )
    instant = calendar.dateFromComponents_(native)
    assert instant is not None
    assert instant.timeIntervalSince1970() == requested.timestamp()
    round_trip = shim._components_dict(native)
    assert round_trip["all_day"] is False and round_trip["floating"] is False
    assert {
        key: round_trip[key]
        for key in ("year", "month", "day", "hour", "minute", "second")
    } == {
        key: payload[key]
        for key in ("year", "month", "day", "hour", "minute", "second")
    }
