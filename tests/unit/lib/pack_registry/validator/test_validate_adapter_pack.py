"""Aggregator-level unit tests for :func:`validate_adapter_pack`.

The six per-check modules each carry their own unit-test file; this
module pins the **aggregator** contract specifically:

- Every check runs; the aggregator does NOT short-circuit.
- Findings from every check are preserved on the returned
  :class:`ValidationResult`.
- The aggregator forwards the injected ``registry`` and
  ``projections_dir`` parameters to the per-check modules that consume
  them, so callers can fully decouple from on-main side effects.
- ``has_errors()`` correctly summarizes mixed-severity findings.
- Missing/malformed ``pack.yaml`` surfaces as :class:`ManifestParseError`
  (a parser concern, not a validator finding — a malformed manifest
  cannot be validated).

Test fixtures construct stub pack directories under ``tmp_path`` with a
synthetic ``pack.yaml`` and ``adapter.py``; we never load an on-main
pack here (those scenarios live in the integration test).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from adminme.lib.pack_registry.parser import ManifestParseError
from adminme.lib.pack_registry.validator import (
    ValidationResult,
    validate_adapter_pack,
)


@dataclass
class _FakeRegistry:
    """Stand-in registry exposing ``known_types`` per the validator's structural type."""

    _names: list[str]

    def known_types(self) -> list[str]:
        return list(self._names)


def _write_pack(
    tmp_path: Path,
    *,
    pack_yaml: str,
    adapter_py: str,
) -> Path:
    pack_dir = tmp_path / "synthetic_pack"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text(pack_yaml, encoding="utf-8")
    (pack_dir / "adapter.py").write_text(adapter_py, encoding="utf-8")
    return pack_dir


def _write_projection(
    projections_dir: Path, name: str, *, subscribes_to: list[str]
) -> None:
    pkg = projections_dir / name
    pkg.mkdir(parents=True, exist_ok=True)
    rendered = "[" + ", ".join(repr(f) for f in subscribes_to) + "]"
    (pkg / "__init__.py").write_text(
        "from __future__ import annotations\n"
        "\n"
        f"class {name.title()}Projection(Projection):\n"
        f"    subscribes_to = {rendered}\n",
        encoding="utf-8",
    )


def test_aggregator_returns_validation_result_with_pack_dir(
    tmp_path: Path,
) -> None:
    """The aggregator returns a :class:`ValidationResult` carrying the input pack_dir."""
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    _write_projection(projections_dir, "tasks", subscribes_to=["task.created"])
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: example\n"
            "family: reminders\n"
            "capabilities:\n"
            "  - kind: Cat-B\n"
            "    runtime: central\n"
            "    event_families:\n"
            "      - task.created\n"
        ),
        adapter_py=(
            "from adminme.lib.adapters import ExternalStateMirrorAdapter\n"
            "\n"
            "class FakeMirror(ExternalStateMirrorAdapter):\n"
            "    pass\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["task.created"]),
        projections_dir=projections_dir,
    )

    assert isinstance(result, ValidationResult)
    assert result.pack_dir == pack_dir


def test_aggregator_runs_all_six_checks_without_short_circuit(
    tmp_path: Path,
) -> None:
    """A pack violating multiple rules surfaces findings from every offending check.

    The aggregator MUST collect findings from every check, not stop at
    the first error. We construct a pack that fails three checks
    simultaneously:

    - capability-runtime coherence: Cat-E declared without
      ``observation_mode_required=True``.
    - event-schema registration: an unknown family.
    - base-class conformance: adapter inherits the wrong ABC for Cat-E.

    All three findings must be present on the aggregated result.
    """
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: multi_violator\n"
            "family: external_action\n"
            "capabilities:\n"
            "  - kind: Cat-E\n"
            "    runtime: central\n"
            "    event_families:\n"
            "      - wholly.unknown\n"
        ),
        adapter_py=(
            "from adminme.lib.adapters import CommunicationAdapter\n"
            "\n"
            "class WrongClass(CommunicationAdapter):\n"
            "    pass\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["messaging.received"]),
        projections_dir=projections_dir,
    )

    checks_with_findings = {f.check for f in result.findings}
    assert "capability_runtime_coherence" in checks_with_findings
    assert "event_schema_registration" in checks_with_findings
    assert "base_class_conformance" in checks_with_findings
    assert result.has_errors()
    assert len(result.errors()) >= 3


def test_aggregator_preserves_mixed_severity_findings(
    tmp_path: Path,
) -> None:
    """A pack with both an error and an advisory surfaces both on the result."""
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    _write_projection(projections_dir, "tasks", subscribes_to=["task.created"])
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: mixed\n"
            "family: knowledge\n"
            "capabilities:\n"
            "  - kind: Cat-D\n"
            "    runtime: bridge\n"
            "    event_families:\n"
            "      - member_knowledge.note_added\n"
            "owner_scope_overridability_at_install: false\n"
        ),
        adapter_py=("class NoAbcAdapter:\n    pass\n"),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["member_knowledge.note_added"]),
        projections_dir=projections_dir,
    )

    severities = {f.severity for f in result.findings}
    assert "error" in severities
    assert "advisory" in severities
    assert result.has_errors() is True
    assert any(
        f.check == "projection_subscription_compatibility" and f.severity == "advisory"
        for f in result.findings
    )
    assert any(
        f.check == "base_class_conformance" and f.severity == "error"
        for f in result.findings
    )


def test_aggregator_clean_pack_yields_no_findings(tmp_path: Path) -> None:
    """A correctly-shaped pack produces zero findings across all six checks."""
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    _write_projection(projections_dir, "tasks", subscribes_to=["task.created"])
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: clean\n"
            "family: reminders\n"
            "capabilities:\n"
            "  - kind: Cat-B\n"
            "    runtime: central\n"
            "    event_families:\n"
            "      - task.created\n"
            "write_capabilities:\n"
            "  - toggle_task\n"
        ),
        adapter_py=(
            "from adminme.lib.adapters import ExternalStateMirrorAdapter\n"
            "\n"
            "class CleanMirror(ExternalStateMirrorAdapter):\n"
            "    async def _handle_toggle_task(self, operation):\n"
            "        return None\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["task.created"]),
        projections_dir=projections_dir,
    )

    assert result.findings == ()
    assert result.has_errors() is False


def test_aggregator_forwards_injected_registry(tmp_path: Path) -> None:
    """An injected fake registry is used by the event-schema-registration check.

    If the aggregator dropped the ``registry`` kwarg, the event-schema
    check would fall back to the on-main registry — and the pack here
    declares ``totally_fake.family`` which is registered on the fake but
    not on-main. Zero findings from the event-schema check confirms the
    injection threaded through.
    """
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    _write_projection(projections_dir, "fakes", subscribes_to=["totally_fake.family"])
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: injected_registry\n"
            "family: reminders\n"
            "capabilities:\n"
            "  - kind: Cat-B\n"
            "    runtime: central\n"
            "    event_families:\n"
            "      - totally_fake.family\n"
        ),
        adapter_py=(
            "from adminme.lib.adapters import ExternalStateMirrorAdapter\n"
            "\n"
            "class InjectedMirror(ExternalStateMirrorAdapter):\n"
            "    pass\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["totally_fake.family"]),
        projections_dir=projections_dir,
    )

    assert not any(f.check == "event_schema_registration" for f in result.findings)


def test_aggregator_forwards_injected_projections_dir(tmp_path: Path) -> None:
    """An injected ``projections_dir`` is used by the projection-subscription check.

    The pack declares a family only present in the injected stub
    projections tree. If the aggregator dropped the kwarg, the
    projection check would walk the on-main projections directory
    instead and the family would not match — yielding a finding. Zero
    projection-subscription findings confirms the injection threaded
    through.
    """
    projections_dir = tmp_path / "alternative_projections"
    projections_dir.mkdir()
    _write_projection(projections_dir, "exotic", subscribes_to=["exotic.event"])
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: injected_projections\n"
            "family: reminders\n"
            "capabilities:\n"
            "  - kind: Cat-B\n"
            "    runtime: central\n"
            "    event_families:\n"
            "      - exotic.event\n"
        ),
        adapter_py=(
            "from adminme.lib.adapters import ExternalStateMirrorAdapter\n"
            "\n"
            "class ExoticMirror(ExternalStateMirrorAdapter):\n"
            "    pass\n"
        ),
    )

    result = validate_adapter_pack(
        pack_dir,
        registry=_FakeRegistry(_names=["exotic.event"]),
        projections_dir=projections_dir,
    )

    assert not any(
        f.check == "projection_subscription_compatibility" for f in result.findings
    )


def test_aggregator_propagates_manifest_parse_error_on_missing_pack_yaml(
    tmp_path: Path,
) -> None:
    """A pack_dir without ``pack.yaml`` surfaces as :class:`ManifestParseError`.

    The aggregator does NOT translate parser errors into findings: a
    pack that cannot be parsed cannot be validated. The supervisor
    layer is responsible for distinguishing parser errors from
    validator findings; the aggregator preserves the distinction.
    """
    pack_dir = tmp_path / "empty_pack"
    pack_dir.mkdir()

    with pytest.raises(ManifestParseError):
        validate_adapter_pack(
            pack_dir,
            registry=_FakeRegistry(_names=[]),
            projections_dir=tmp_path / "projections",
        )


def test_aggregator_default_registry_path_is_used_when_not_injected(
    tmp_path: Path,
) -> None:
    """Omitting ``registry`` falls through to the default on-main registry.

    The event-schema check autoloads the on-main registry; a pack
    declaring an on-main-registered family (``messaging.received``)
    yields zero event-schema findings even with no registry passed in.
    """
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir()
    pack_dir = _write_pack(
        tmp_path,
        pack_yaml=(
            "pack: default_registry\n"
            "family: messaging\n"
            "capabilities:\n"
            "  - kind: Cat-A\n"
            "    runtime: central\n"
            "    event_families:\n"
            "      - messaging.received\n"
        ),
        adapter_py=(
            "from adminme.lib.observation import outbound\n"
            "from adminme.lib.adapters import CommunicationAdapter\n"
            "\n"
            "class DefaultRegistryAdapter(CommunicationAdapter):\n"
            "    async def send(self, message):\n"
            "        return await outbound(\n"
            "            session=None, action='m', payload={}, action_fn=None,\n"
            "            manager=None, event_log=None,\n"
            "        )\n"
        ),
    )

    result = validate_adapter_pack(pack_dir, projections_dir=projections_dir)

    assert not any(f.check == "event_schema_registration" for f in result.findings)
