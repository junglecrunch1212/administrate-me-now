"""Tests for :mod:`~.base_class_conformance`.

Each test constructs a ``tmp_path``-rooted stub pack directory containing
a synthetic ``adapter.py``. The check AST-walks the file content, so the
stubs do NOT need to be importable as Python modules — they only need to
parse cleanly. This keeps the test suite independent of the on-main
adapter packs.
"""

from __future__ import annotations

from pathlib import Path

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.base_class_conformance import (
    check_base_class_conformance,
)


def _write_adapter(pack_dir: Path, body: str) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "adapter.py").write_text(body, encoding="utf-8")


def test_happy_path_cat_a_with_communication_adapter(tmp_path: Path) -> None:
    _write_adapter(
        tmp_path,
        "from adminme.lib.adapters import CommunicationAdapter\n"
        "\n"
        "class FakeMessaging(CommunicationAdapter):\n"
        "    pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    assert check_base_class_conformance(tmp_path, manifest) == []


def test_happy_path_cat_b_with_external_state_mirror(tmp_path: Path) -> None:
    _write_adapter(
        tmp_path,
        "from adminme.lib.adapters import ExternalStateMirrorAdapter\n"
        "\n"
        "class FakeMirror(ExternalStateMirrorAdapter):\n"
        "    pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["reminder.list_item.synced"],
                }
            ]
        }
    )
    assert check_base_class_conformance(tmp_path, manifest) == []


def test_happy_path_cat_d_with_personal_knowledge(tmp_path: Path) -> None:
    _write_adapter(
        tmp_path,
        "from adminme.lib.adapters import PersonalKnowledgeAdapter\n"
        "\n"
        "class FakeKnowledge(PersonalKnowledgeAdapter):\n"
        "    pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": ["note.added"],
                }
            ],
            "owner_scope_overridability_at_install": False,
        }
    )
    assert check_base_class_conformance(tmp_path, manifest) == []


def test_cross_category_mismatch_yields_error(tmp_path: Path) -> None:
    """Cat-A manifest with ExternalStateMirrorAdapter inheritance → error."""
    _write_adapter(
        tmp_path,
        "from adminme.lib.adapters import ExternalStateMirrorAdapter\n"
        "\n"
        "class FakeMessaging(ExternalStateMirrorAdapter):\n"
        "    pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    findings = check_base_class_conformance(tmp_path, manifest)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "error"
    assert finding.check == "base_class_conformance"
    assert finding.field_path == "capabilities[0].kind"
    assert "Cat-A" in finding.message
    assert "ExternalStateMirrorAdapter" in finding.message


def test_no_abc_no_capabilities_yields_advisory_pending_retrofit(
    tmp_path: Path,
) -> None:
    """Existing v1 packs (Gmail / Plaid / Reminders shape): no ABC, no caps."""
    _write_adapter(
        tmp_path,
        "class LegacyAdapter:\n    pass\n",
    )
    manifest = AdapterManifest.model_validate({"capabilities": []})
    findings = check_base_class_conformance(tmp_path, manifest)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "advisory"
    assert finding.check == "base_class_conformance"
    assert "advisory_pending_retrofit" in finding.message


def test_no_abc_with_capabilities_yields_error(tmp_path: Path) -> None:
    """A manifest declaring capabilities without a corresponding ABC base is wrong-shape."""
    _write_adapter(
        tmp_path,
        "class NotRetrofittedYet:\n    pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    findings = check_base_class_conformance(tmp_path, manifest)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].field_path == "capabilities[0].kind"
    assert "CommunicationAdapter" in findings[0].message


def test_malformed_adapter_py_yields_error_finding_not_crash(
    tmp_path: Path,
) -> None:
    """Syntax error in adapter.py surfaces as a single error finding."""
    _write_adapter(
        tmp_path,
        "class Broken(:\n    pass\n",  # syntax error
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    findings = check_base_class_conformance(tmp_path, manifest)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "could not be parsed" in findings[0].message


def test_missing_adapter_py_yields_error_finding(tmp_path: Path) -> None:
    manifest = AdapterManifest.model_validate({"capabilities": []})
    findings = check_base_class_conformance(tmp_path, manifest)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "adapter.py not found" in findings[0].message


def test_dotted_abc_attribute_inheritance_is_recognized(tmp_path: Path) -> None:
    """Adapter inheriting via ``adapters.CommunicationAdapter`` (dotted form) is recognized."""
    _write_adapter(
        tmp_path,
        "from adminme.lib import adapters\n"
        "\n"
        "class FakeMessaging(adapters.CommunicationAdapter):\n"
        "    pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    assert check_base_class_conformance(tmp_path, manifest) == []


def test_multiple_abc_inheritance_yields_error(tmp_path: Path) -> None:
    """A class inheriting from two framework ABCs is wrong-shape."""
    _write_adapter(
        tmp_path,
        "from adminme.lib.adapters import (\n"
        "    CommunicationAdapter,\n"
        "    ExternalStateMirrorAdapter,\n"
        ")\n"
        "\n"
        "class HybridAdapter(CommunicationAdapter, ExternalStateMirrorAdapter):\n"
        "    pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    findings = check_base_class_conformance(tmp_path, manifest)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "multiple framework ABCs" in findings[0].message
