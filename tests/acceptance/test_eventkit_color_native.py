"""Real Quartz objects, isolated from EventKit stores, accounts and permissions."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("CI") != "true",
    reason="native memory boundary runs only on isolated hosted macOS",
)
def test_real_quartz_colors_survive_reminder_list_acquisition():
    root = Path(__file__).resolve().parents[2]
    program = r"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from Quartz import CGColorCreateGenericRGB, CGColorCreateGenericGray

spec = importlib.util.spec_from_file_location(
    "native_color_shim", Path("packs/adapters/reminders/apple-eventkit/_eventkit_shim.py")
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
# Exercise the real production list reader with synthetic calendars, real
# Quartz pointers and no EKEventStore allocation or permission request.
rgb = CGColorCreateGenericRGB(1.0, 0.5, 0.0, 1.0)
gray = CGColorCreateGenericGray(0.5, 1.0)
calendars = [
    SimpleNamespace(calendarIdentifier="rgb", title="Fixture RGB", CGColor=rgb),
    SimpleNamespace(calendarIdentifier="gray", title="Fixture gray", CGColor=gray),
]
reader = object.__new__(module.PyObjCEventStore)
reader._store = SimpleNamespace(
    defaultCalendarForNewReminders=lambda: calendars[0],
    calendarsForEntityType_=lambda _: calendars,
)
for _ in range(100):
    lists = reader.fetch_lists()
    assert [item.color for item in lists] == ["#FF8000", "#808080"]
    assert [item.is_default for item in lists] == [True, False]
print("native reminder-list color acquisition passed")
"""
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", program],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "native reminder-list color acquisition passed" in result.stdout
