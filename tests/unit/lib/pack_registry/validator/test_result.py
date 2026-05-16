"""Tests for :mod:`adminme.lib.pack_registry.validator.result`.

Covers the public surface of :class:`ValidationFinding` and
:class:`ValidationResult`: severity-bucket accessors, frozen-dataclass
canary, and the default ``field_path=None`` shape for findings that
reference an adapter source file rather than a manifest field path.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from adminme.lib.pack_registry.validator.result import (
    ValidationFinding,
    ValidationResult,
)


def test_validation_finding_is_frozen() -> None:
    finding = ValidationFinding(
        severity="error",
        check="capability_runtime_coherence",
        message="example",
        field_path="capabilities[0].runtime",
    )
    with pytest.raises(FrozenInstanceError):
        finding.severity = "warning"  # type: ignore[misc]


def test_validation_finding_default_field_path_is_none() -> None:
    """Findings from AST-walk checks reference a source file, not a manifest field."""
    finding = ValidationFinding(
        severity="error",
        check="base_class_conformance",
        message="adapter.py does not inherit from any ABC",
    )
    assert finding.field_path is None


def test_validation_result_severity_buckets_split_findings_correctly() -> None:
    err = ValidationFinding(severity="error", check="a", message="m1")
    warn = ValidationFinding(severity="warning", check="b", message="m2")
    advisory = ValidationFinding(
        severity="advisory", check="c", message="m3"
    )
    result = ValidationResult(
        pack_dir=Path("/tmp/some-pack"),
        findings=(err, warn, advisory),
    )
    assert result.errors() == [err]
    assert result.warnings() == [warn]
    assert result.advisories() == [advisory]


def test_validation_result_has_errors_true_when_any_error_finding() -> None:
    result = ValidationResult(
        pack_dir=Path("/tmp/p"),
        findings=(
            ValidationFinding(
                severity="advisory", check="a", message="m"
            ),
            ValidationFinding(severity="error", check="b", message="m"),
        ),
    )
    assert result.has_errors() is True


def test_validation_result_has_errors_false_when_only_warnings_and_advisories() -> None:
    result = ValidationResult(
        pack_dir=Path("/tmp/p"),
        findings=(
            ValidationFinding(
                severity="warning", check="a", message="m"
            ),
            ValidationFinding(
                severity="advisory", check="b", message="m"
            ),
        ),
    )
    assert result.has_errors() is False
    assert result.errors() == []
