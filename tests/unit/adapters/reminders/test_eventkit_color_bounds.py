"""Color metadata must never enumerate an unbounded native C array."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[4]
SHIM = ROOT / "packs/adapters/reminders/apple-eventkit/_eventkit_shim.py"


def _shim():
    name = "eventkit_color_bounds_under_test"
    spec = importlib.util.spec_from_file_location(name, SHIM)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("channels", "expected"),
    [((1.0, 0.5, 0.0, 1.0), "#FF8000"), ((0.5, 1.0), "#808080")],
)
def test_color_reads_exact_native_bounds(monkeypatch, channels, expected):
    reads = []

    class NativeArray:
        def __iter__(self):
            pytest.fail("a native varlist has no end; never iterate it")

        def __getitem__(self, index):
            assert 0 <= index < len(channels), "native array over-read"
            reads.append(index)
            return channels[index]

    color = object()
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        SimpleNamespace(
            CGColorGetNumberOfComponents=lambda value: len(channels),
            CGColorGetComponents=lambda value: NativeArray(),
        ),
    )
    assert _shim()._cgcolor_hex(color) == expected
    assert reads == list(range(len(channels)))


@pytest.mark.parametrize("count", [0, 1, 3, 5, 1000000])
def test_unsupported_color_has_no_native_array_access(monkeypatch, count):
    def unread(_):
        pytest.fail("unsupported color must not read native memory")

    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        SimpleNamespace(
            CGColorGetNumberOfComponents=lambda value: count,
            CGColorGetComponents=unread,
        ),
    )
    assert _shim()._cgcolor_hex(object()) is None


def test_absent_color_does_not_load_quartz(monkeypatch):
    monkeypatch.setitem(sys.modules, "Quartz", None)
    assert _shim()._cgcolor_hex(None) is None
