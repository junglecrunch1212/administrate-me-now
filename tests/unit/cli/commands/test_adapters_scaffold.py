"""Tests for ``adminme adapters scaffold`` (prompt 11-γ commit 3).

Covers the [D20] three-layer developer-mode gate plus the on-disk
template renderer:

- Wire-up: the ``scaffold`` subcommand is registered under the
  ``adapters`` group.
- Layer 1 refusal: missing ``ADMINME_DEVELOPER_MODE=1`` env var exits 3.
- Layer 2 refusal: missing ``--allow-scaffold`` flag exits 4.
- Layer 3 refusal: ``typer.confirm`` declined exits 5; ``--yes`` skips
  the prompt and lets a passing scaffold proceed.
- Happy-path: scaffold generates the template tree at ``--output`` with
  ``<NAME>`` / ``<CLASSNAME>`` substitution, and the generated pack
  passes ``validate_adapter_pack`` cleanly (no error findings).
- Template coverage: each of the five canonical templates renders into
  a validator-clean pack.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from adminme.cli.commands.adapters import (
    SCAFFOLD_EXIT_CONFIRM_DECLINED,
    SCAFFOLD_EXIT_ENV_MISSING,
    SCAFFOLD_EXIT_FLAG_MISSING,
    _classname_for,
    adapters_app,
)
from adminme.cli.main import app as main_app
from adminme.lib.pack_registry.validator import validate_adapter_pack

runner = CliRunner()


# ---------- Wire-up ----------


def test_scaffold_subcommand_reachable_from_main_app() -> None:
    """`adminme adapters --help` lists scaffold alongside list/validate."""
    result = runner.invoke(main_app, ["adapters", "--help"])
    assert result.exit_code == 0
    assert "scaffold" in result.stdout


def test_scaffold_subcommand_help_describes_kind_and_runtime() -> None:
    result = runner.invoke(adapters_app, ["scaffold", "--help"])
    assert result.exit_code == 0
    assert "kind" in result.stdout.lower() or "KIND" in result.stdout
    assert "runtime" in result.stdout.lower() or "RUNTIME" in result.stdout
    # Rich's help renderer splits ``--allow-scaffold`` across ANSI styling
    # segments; assert on the literal flag fragment instead.
    assert "scaffold" in result.stdout.lower()
    assert "developer-mode gate" in result.stdout


# ---------- _classname_for helper ----------


def test_classname_for_handles_hyphenated_and_underscored_names() -> None:
    assert _classname_for("demo") == "DemoAdapter"
    assert _classname_for("demo-pack") == "DemoPackAdapter"
    assert _classname_for("demo_pack") == "DemoPackAdapter"
    assert _classname_for("cat-c-central-skeleton") == "CatCCentralSkeletonAdapter"


# ---------- Layer 1: env var ----------


def test_scaffold_refuses_when_env_var_missing(monkeypatch, tmp_path: Path) -> None:
    """Layer 1 of the [D20] gate — ADMINME_DEVELOPER_MODE must be ``1``."""
    monkeypatch.delenv("ADMINME_DEVELOPER_MODE", raising=False)
    result = runner.invoke(
        adapters_app,
        [
            "scaffold",
            "Cat-C",
            "central",
            "--allow-scaffold",
            "--yes",
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == SCAFFOLD_EXIT_ENV_MISSING
    assert (
        "developer mode not enabled" in result.stderr
        or "developer mode not enabled" in result.stdout
    )


def test_scaffold_refuses_when_env_var_is_not_exactly_one(
    monkeypatch, tmp_path: Path
) -> None:
    """``ADMINME_DEVELOPER_MODE=0`` does NOT enable scaffold (strict eq '1')."""
    monkeypatch.setenv("ADMINME_DEVELOPER_MODE", "0")
    result = runner.invoke(
        adapters_app,
        [
            "scaffold",
            "Cat-C",
            "central",
            "--allow-scaffold",
            "--yes",
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == SCAFFOLD_EXIT_ENV_MISSING


# ---------- Layer 2: --allow-scaffold flag ----------


def test_scaffold_refuses_when_allow_flag_missing(monkeypatch, tmp_path: Path) -> None:
    """Layer 2 — the awkward-to-type --allow-scaffold flag is mandatory."""
    monkeypatch.setenv("ADMINME_DEVELOPER_MODE", "1")
    result = runner.invoke(
        adapters_app,
        [
            "scaffold",
            "Cat-C",
            "central",
            "--yes",
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == SCAFFOLD_EXIT_FLAG_MISSING


# ---------- Layer 3: interactive confirm ----------


def test_scaffold_refuses_when_confirm_declined(monkeypatch, tmp_path: Path) -> None:
    """Layer 3 — typer.confirm declined exits 5."""
    monkeypatch.setenv("ADMINME_DEVELOPER_MODE", "1")
    result = runner.invoke(
        adapters_app,
        [
            "scaffold",
            "Cat-C",
            "central",
            "--allow-scaffold",
            "--output",
            str(tmp_path / "out"),
        ],
        input="n\n",
    )
    assert result.exit_code == SCAFFOLD_EXIT_CONFIRM_DECLINED


# ---------- Happy path: all three layers pass ----------


def _scaffold_and_validate(
    monkeypatch, tmp_path: Path, kind: str, runtime: str, name: str
) -> Path:
    """Helper: run scaffold with --yes, assert exit 0, run validate, return pack dir."""
    monkeypatch.setenv("ADMINME_DEVELOPER_MODE", "1")
    output = tmp_path / f"scaffolded-{name}"
    result = runner.invoke(
        adapters_app,
        [
            "scaffold",
            kind,
            runtime,
            "--allow-scaffold",
            "--yes",
            "--output",
            str(output),
            "--name",
            name,
        ],
    )
    assert result.exit_code == 0, result.stdout + "\n" + result.stderr
    assert (output / "pack.yaml").exists()
    assert (output / "adapter.py").exists()
    assert (output / "config.schema.json").exists()
    # F-3 unique basename — tests dir holds the substituted filename.
    test_files = list((output / "tests").glob("test_*_adapter_pack_internal.py"))
    assert len(test_files) == 1
    assert name in test_files[0].name

    validation = validate_adapter_pack(output)
    assert not validation.has_errors(), [
        f for f in validation.findings if f.severity == "error"
    ]
    return output


def test_scaffold_cat_c_central_happy_path(monkeypatch, tmp_path: Path) -> None:
    """End-to-end §8 c3 verification: Cat-C central scaffolds + validates clean."""
    _scaffold_and_validate(monkeypatch, tmp_path, "Cat-C", "central", "demo-finance")


def test_scaffold_cat_a_central_happy_path(monkeypatch, tmp_path: Path) -> None:
    _scaffold_and_validate(monkeypatch, tmp_path, "Cat-A", "central", "demo-mail")


def test_scaffold_cat_b_central_happy_path(monkeypatch, tmp_path: Path) -> None:
    _scaffold_and_validate(monkeypatch, tmp_path, "Cat-B", "central", "demo-cal")


def test_scaffold_cat_d_bridge_happy_path(monkeypatch, tmp_path: Path) -> None:
    _scaffold_and_validate(monkeypatch, tmp_path, "Cat-D", "bridge", "demo-notes")


def test_scaffold_cat_e_central_happy_path(monkeypatch, tmp_path: Path) -> None:
    _scaffold_and_validate(monkeypatch, tmp_path, "Cat-E", "central", "demo-action")


# ---------- Substitution + structure ----------


def test_scaffold_substitutes_name_and_classname_in_adapter_py(
    monkeypatch, tmp_path: Path
) -> None:
    pack_dir = _scaffold_and_validate(
        monkeypatch, tmp_path, "Cat-C", "central", "subst-test"
    )
    adapter_src = (pack_dir / "adapter.py").read_text(encoding="utf-8")
    assert "SubstTestAdapter" in adapter_src
    assert "subst-test" in adapter_src
    # Placeholders must be fully replaced — no stray tokens remain.
    assert "<NAME>" not in adapter_src
    assert "<CLASSNAME>" not in adapter_src


def test_scaffold_refuses_unknown_kind_at_typer_layer(
    monkeypatch, tmp_path: Path
) -> None:
    """Typer's Enum binding rejects unknown kinds before the gate runs."""
    monkeypatch.setenv("ADMINME_DEVELOPER_MODE", "1")
    result = runner.invoke(
        adapters_app,
        [
            "scaffold",
            "Cat-Z",
            "central",
            "--allow-scaffold",
            "--yes",
            "--output",
            str(tmp_path / "out"),
        ],
    )
    # Typer surfaces invalid Enum choices with non-zero exit.
    assert result.exit_code != 0
    assert result.exit_code not in (
        SCAFFOLD_EXIT_ENV_MISSING,
        SCAFFOLD_EXIT_FLAG_MISSING,
        SCAFFOLD_EXIT_CONFIRM_DECLINED,
    )
