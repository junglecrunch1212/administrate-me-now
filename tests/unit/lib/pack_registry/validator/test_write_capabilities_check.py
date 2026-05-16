"""Tests for :mod:`~.write_capabilities_check`.

Each test constructs a ``tmp_path``-rooted stub pack directory with an
``adapter.py`` containing a synthetic adapter class. The check
AST-walks the file content; stubs do NOT need to be importable Python
modules.
"""

from __future__ import annotations

from pathlib import Path

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.write_capabilities_check import (
    check_write_capabilities,
)


def _write_adapter(pack_dir: Path, body: str) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "adapter.py").write_text(body, encoding="utf-8")


def test_handle_op_methods_satisfy_write_capabilities(tmp_path: Path) -> None:
    """Each declared op has a matching ``_handle_<op>`` method → no findings."""
    _write_adapter(
        tmp_path,
        "class FakeMirror:\n"
        "    async def _handle_create_list(self, operation):\n"
        "        pass\n"
        "    async def _handle_delete_item(self, operation):\n"
        "        pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["reminder.synced"],
                }
            ],
            "write_capabilities": ["create_list", "delete_item"],
        }
    )
    assert check_write_capabilities(tmp_path, manifest) == []


def test_apply_write_impl_dispatch_shape_satisfies_all_ops(tmp_path: Path) -> None:
    """A single ``_apply_write_impl`` dispatching on ``operation.kind`` covers all ops."""
    _write_adapter(
        tmp_path,
        "class FakeMirror:\n"
        "    async def _apply_write_impl(self, operation):\n"
        "        if operation.kind == 'create_list':\n"
        "            return await self._create(operation)\n"
        "        return None\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["reminder.synced"],
                }
            ],
            "write_capabilities": ["create_list", "delete_item", "rename_list"],
        }
    )
    assert check_write_capabilities(tmp_path, manifest) == []


def test_missing_both_shapes_yields_error_per_operation(tmp_path: Path) -> None:
    """Neither ``_handle_<op>`` nor a dispatching ``_apply_write_impl`` → one error per op."""
    _write_adapter(
        tmp_path,
        "class FakeMirror:\n"
        "    async def start(self, *, ctx):\n"
        "        pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["reminder.synced"],
                }
            ],
            "write_capabilities": ["create_list", "delete_item"],
        }
    )
    findings = check_write_capabilities(tmp_path, manifest)
    assert len(findings) == 2
    field_paths = sorted(f.field_path for f in findings)
    assert field_paths == ["write_capabilities[0]", "write_capabilities[1]"]
    for finding in findings:
        assert finding.severity == "error"
        assert finding.check == "write_capabilities"


def test_apply_write_impl_without_kind_dispatch_does_not_satisfy(
    tmp_path: Path,
) -> None:
    """``_apply_write_impl`` present but no ``operation.kind`` reference → error per op."""
    _write_adapter(
        tmp_path,
        "class FakeMirror:\n"
        "    async def _apply_write_impl(self, operation):\n"
        "        return None\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["reminder.synced"],
                }
            ],
            "write_capabilities": ["create_list"],
        }
    )
    findings = check_write_capabilities(tmp_path, manifest)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].field_path == "write_capabilities[0]"


def test_partial_handle_coverage_flags_only_missing_ops(tmp_path: Path) -> None:
    """``_handle_create_list`` covers ``create_list`` only; ``delete_item`` still missing."""
    _write_adapter(
        tmp_path,
        "class FakeMirror:\n"
        "    async def _handle_create_list(self, operation):\n"
        "        pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["reminder.synced"],
                }
            ],
            "write_capabilities": ["create_list", "delete_item"],
        }
    )
    findings = check_write_capabilities(tmp_path, manifest)
    assert len(findings) == 1
    assert findings[0].field_path == "write_capabilities[1]"
    assert "delete_item" in findings[0].message


def test_cat_a_with_write_capabilities_yields_error(tmp_path: Path) -> None:
    """Cat-A capability + non-empty write_capabilities → wrong-shape manifest."""
    _write_adapter(
        tmp_path,
        "class FakeMessaging:\n"
        "    async def _handle_create_list(self, operation):\n"
        "        pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ],
            "write_capabilities": ["create_list"],
        }
    )
    findings = check_write_capabilities(tmp_path, manifest)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].field_path == "write_capabilities"
    assert "Cat-A" in findings[0].message


def test_empty_write_capabilities_skips_check(tmp_path: Path) -> None:
    """No write_capabilities declared → nothing to check, no findings."""
    _write_adapter(tmp_path, "class FakeMirror:\n    pass\n")
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["reminder.synced"],
                }
            ],
            "write_capabilities": [],
        }
    )
    assert check_write_capabilities(tmp_path, manifest) == []
