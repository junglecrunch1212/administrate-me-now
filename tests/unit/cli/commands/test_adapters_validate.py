"""Tests for ``adminme adapters validate`` (prompt 11-γ commit 2).

Covers:

- Wire-up: the ``validate`` subcommand is registered under the
  ``adapters`` group and surfaces ``pack_dir`` in its help.
- Exit codes per memo 05 §3.4: ``0`` on no error findings, ``1`` on
  error findings, ``2`` on :class:`ManifestParseError`.
- Severity-color formatting: ``_format_finding`` emits rich-markup
  tags (``[red]`` / ``[yellow]`` / ``[blue]``) matched to the
  finding's severity, with ``field_path`` suffix when present.
- Rendering: the finding's check name and message survive into the
  CLI's stdout output (under CliRunner's non-tty buffer, rich strips
  ANSI but preserves the underlying text).
- On-main smoke: ``adminme adapters validate`` against the repo's
  Gmail pack exits ``0`` and surfaces the
  ``advisory_pending_retrofit``-flavored advisory the v1 manifest
  produces against the new ABCs.

We monkeypatch ``validate_adapter_pack`` for the unit cases so the CLI
layer is tested independently of the on-main registry / projection
tree; the on-main smoke test exercises the real validator end-to-end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from adminme.cli.commands import adapters as adapters_module
from adminme.cli.commands.adapters import (
    _SEVERITY_COLORS,
    _format_finding,
    adapters_app,
)
from adminme.cli.main import app as main_app
from adminme.lib.pack_registry import ManifestParseError
from adminme.lib.pack_registry.validator import (
    ValidationFinding,
    ValidationResult,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
ONMAIN_GMAIL_PACK = REPO_ROOT / "packs" / "adapters" / "messaging" / "gmail"

runner = CliRunner()


# ---------- Wire-up ----------


def test_validate_subcommand_help_describes_pack_dir_argument() -> None:
    """`adminme adapters validate --help` mentions the pack_dir positional."""
    result = runner.invoke(adapters_app, ["validate", "--help"])
    assert result.exit_code == 0
    assert "pack_dir" in result.stdout.lower() or "PACK_DIR" in result.stdout
    assert "adapter pack directory" in result.stdout


def test_validate_subcommand_is_reachable_from_main_app() -> None:
    """`adminme adapters --help` lists validate alongside list."""
    result = runner.invoke(main_app, ["adapters", "--help"])
    assert result.exit_code == 0
    assert "validate" in result.stdout
    assert "list" in result.stdout


# ---------- _format_finding ----------


def test_severity_colors_mapping_matches_memo_05_severity_ladder() -> None:
    """red = error, yellow = warning, blue = advisory per memo 05 §3.4."""
    assert _SEVERITY_COLORS == {
        "error": "red",
        "warning": "yellow",
        "advisory": "blue",
    }


def test_format_finding_emits_red_markup_for_error_severity() -> None:
    finding = ValidationFinding(
        severity="error",
        check="base_class_conformance",
        message="adapter must inherit ExternalStateMirrorAdapter",
        field_path="capabilities[0]",
    )
    rendered = _format_finding(finding)
    assert "[red]ERROR[/red]" in rendered
    assert "base_class_conformance" in rendered
    assert "capabilities[0]" in rendered
    assert "must inherit ExternalStateMirrorAdapter" in rendered


def test_format_finding_emits_yellow_for_warning_and_blue_for_advisory() -> None:
    warning = _format_finding(
        ValidationFinding(
            severity="warning",
            check="capability_runtime_coherence",
            message="runtime promoted",
        )
    )
    advisory = _format_finding(
        ValidationFinding(
            severity="advisory",
            check="projection_subscription_compatibility",
            message="projection pending",
        )
    )
    assert "[yellow]WARNING[/yellow]" in warning
    assert "[blue]ADVISORY[/blue]" in advisory


def test_format_finding_omits_field_path_suffix_when_none() -> None:
    """AST-walk findings reference adapter.py rather than a manifest field."""
    rendered = _format_finding(
        ValidationFinding(
            severity="error",
            check="observation_mode_wrapping",
            message="send() does not route through outbound",
            field_path=None,
        )
    )
    assert ":" in rendered
    # No trailing bracket suffix when field_path is None.
    assert rendered.split(":")[0].endswith("observation_mode_wrapping")


# ---------- Exit-code contract ----------


def _patch_validator(
    monkeypatch: pytest.MonkeyPatch, result: ValidationResult
) -> None:
    def _fake_validate(_pack_dir: Path, **_kwargs: Any) -> ValidationResult:
        return result

    monkeypatch.setattr(
        adapters_module, "validate_adapter_pack", _fake_validate
    )


def test_validate_exits_zero_for_empty_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _patch_validator(
        monkeypatch,
        ValidationResult(pack_dir=tmp_path, findings=()),
    )
    result = runner.invoke(adapters_app, ["validate", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert "no findings" in result.stdout


def test_validate_exits_one_when_error_finding_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    findings = (
        ValidationFinding(
            severity="error",
            check="base_class_conformance",
            message="wrong ABC",
            field_path="adapter.py",
        ),
    )
    _patch_validator(
        monkeypatch, ValidationResult(pack_dir=tmp_path, findings=findings)
    )
    result = runner.invoke(adapters_app, ["validate", str(tmp_path)])
    assert result.exit_code == 1, result.stdout
    # Render survives — check name appears in output even with ANSI
    # stripped by CliRunner's non-tty buffer.
    assert "base_class_conformance" in result.stdout
    assert "1 error(s)" in result.stdout


def test_validate_exits_zero_for_advisory_only_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Advisories alone do not fail the validate run per memo 05 §3.4."""
    findings = (
        ValidationFinding(
            severity="advisory",
            check="base_class_conformance",
            message="advisory_pending_retrofit",
        ),
    )
    _patch_validator(
        monkeypatch, ValidationResult(pack_dir=tmp_path, findings=findings)
    )
    result = runner.invoke(adapters_app, ["validate", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert "advisory_pending_retrofit" in result.stdout
    assert "1 advisory(ies)" in result.stdout


def test_validate_exits_zero_for_warning_only_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Warnings alone do not fail the validate run."""
    findings = (
        ValidationFinding(
            severity="warning",
            check="capability_runtime_coherence",
            message="dual deployment without bridge marker",
        ),
    )
    _patch_validator(
        monkeypatch, ValidationResult(pack_dir=tmp_path, findings=findings)
    )
    result = runner.invoke(adapters_app, ["validate", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert "1 warning(s)" in result.stdout


def test_validate_exits_two_on_manifest_parse_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Missing/malformed pack.yaml surfaces with the dedicated exit code."""

    def _raises(_pack_dir: Path, **_kwargs: Any) -> ValidationResult:
        raise ManifestParseError("pack.yaml not found at /nope/pack.yaml")

    monkeypatch.setattr(adapters_module, "validate_adapter_pack", _raises)

    result = runner.invoke(adapters_app, ["validate", str(tmp_path / "nope")])
    assert result.exit_code == 2, result.stdout
    assert "cannot parse manifest" in result.stderr or "cannot parse manifest" in result.stdout


def test_validate_exits_two_on_real_missing_pack_yaml(tmp_path: Path) -> None:
    """End-to-end: a directory without pack.yaml exits with code 2.

    Pinned against the real validator + parser surface (no monkeypatch)
    so the ``ManifestParseError`` branch is exercised against the
    actual parser.
    """
    empty_dir = tmp_path / "empty_pack"
    empty_dir.mkdir()

    result = runner.invoke(adapters_app, ["validate", str(empty_dir)])
    assert result.exit_code == 2, result.stdout


# ---------- On-main smoke ----------


def test_validate_against_on_main_gmail_pack_exits_zero_with_advisory() -> None:
    """Gmail is a legacy v1 pack pending Cohort C retrofit.

    Per prompt 11-γ §8 c2 verification, ``adminme adapters validate
    packs/adapters/messaging/gmail`` exits ``0`` and surfaces the
    advisory the base-class-conformance check produces against the v1
    manifest. Cohort C closes this advisory by retrofitting Gmail onto
    :class:`CommunicationAdapter`.
    """
    if not ONMAIN_GMAIL_PACK.is_dir():
        raise AssertionError(
            f"{ONMAIN_GMAIL_PACK} missing — repo layout drifted?"
        )

    result = runner.invoke(adapters_app, ["validate", str(ONMAIN_GMAIL_PACK)])
    assert result.exit_code == 0, result.stdout
    # The advisory's text survives the renderer; ANSI is stripped by
    # CliRunner's buffer so we look for the plain check name.
    assert "base_class_conformance" in result.stdout
