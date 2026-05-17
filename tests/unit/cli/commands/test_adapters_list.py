"""Tests for ``adminme adapters list`` (prompt 11-γ commit 1).

Covers:

- Wire-up: the ``adapters`` subcommand group is reachable from the
  top-level ``adminme`` Typer app.
- Discovery: depth-2 walk under ``<packs-root>/<family>/<name>/`` finds
  every pack and skips junk (loose files, missing pack.yaml).
- Rendering: a clean pack renders a populated row; a legacy v1 pack
  (no capabilities block) renders em-dashes in the kinds/runtime
  columns rather than blanks.
- Unparseable handling: a malformed pack.yaml surfaces as an
  ``<unparseable>`` row with a ``<!>`` marker; the listing does not
  abort.
- Exit codes: empty / missing packs-root exits 0 with a friendly
  message; the only nonzero path is filesystem-level errors.
- On-main smoke: invoked against the repo's own ``packs/adapters/``
  the three reference packs (Gmail, Plaid, Reminders) appear.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from adminme.cli.commands.adapters import (
    _build_table,
    _discover_packs,
    _row_from_pack,
    adapters_app,
)
from adminme.cli.main import app as main_app

REPO_ROOT = Path(__file__).resolve().parents[4]
ONMAIN_PACKS_ROOT = REPO_ROOT / "packs" / "adapters"

runner = CliRunner()


# ---------- Wire-up ----------


def test_adapters_subcommand_is_wired_into_main_app() -> None:
    """`adminme adapters --help` reaches the subgroup, not the root help."""
    result = runner.invoke(main_app, ["adapters", "--help"])
    assert result.exit_code == 0
    # Each subcommand body lands here over the four-commit cycle; for
    # commit 1 only ``list`` is registered, but the subgroup's help
    # must show it.
    assert "list" in result.stdout


def test_list_subcommand_help_describes_packs_root_option() -> None:
    result = runner.invoke(adapters_app, ["list", "--help"])
    assert result.exit_code == 0
    # The literal ``--packs-root`` may wrap across rich's rendered help
    # box; the underlying token survives without the leading dashes.
    assert "packs-root" in result.stdout
    assert "Directory containing adapter pack families" in result.stdout


# ---------- Discovery + rendering on synthetic packs ----------


def _write_pack(
    root: Path,
    family: str,
    name: str,
    pack_yaml_body: str,
) -> Path:
    pack_dir = root / family / name
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(pack_yaml_body, encoding="utf-8")
    return pack_dir


def test_discover_packs_returns_one_row_per_pack(tmp_path: Path) -> None:
    _write_pack(
        tmp_path,
        "messaging",
        "demo-mail",
        """\
pack:
  id: adapter:messaging-demo-mail
  name: Demo mail
  version: 1.2.3
  family: messaging
capabilities:
  - kind: Cat-A
    runtime: central
    event_families: [messaging.received]
sensitivity_default: sensitive
""",
    )
    _write_pack(
        tmp_path,
        "financial",
        "demo-bank",
        """\
pack:
  id: adapter:financial-demo-bank
  version: 0.1.0
  family: financial
capabilities:
  - kind: Cat-C
    runtime: central
sensitivity_default: privileged
""",
    )

    rows = _discover_packs(tmp_path)
    assert len(rows) == 2
    by_name = {r.name: r for r in rows}

    mail = by_name["messaging-demo-mail"]
    assert mail.family == "messaging"
    assert mail.version == "1.2.3"
    assert mail.kinds == "Cat-A"
    assert mail.runtime == "central"
    assert mail.sensitivity_default == "sensitive"
    assert mail.unparseable is False

    bank = by_name["financial-demo-bank"]
    assert bank.kinds == "Cat-C"
    assert bank.sensitivity_default == "privileged"


def test_discover_packs_handles_legacy_v1_manifest_with_empty_capabilities(
    tmp_path: Path,
) -> None:
    """Legacy packs (no ``capabilities:``) render em-dash, not blank."""
    _write_pack(
        tmp_path,
        "messaging",
        "legacy-mail",
        """\
pack:
  id: adapter:messaging-legacy-mail
  name: Legacy
  version: 1.0.0
family: messaging
""",
    )
    rows = _discover_packs(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "messaging-legacy-mail"
    assert row.family == "messaging"
    assert row.kinds == "—"
    assert row.runtime == "—"
    assert row.unparseable is False


def test_discover_packs_marks_malformed_yaml_unparseable(tmp_path: Path) -> None:
    pack_dir = tmp_path / "messaging" / "broken"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(": : :\n not valid yaml\n", encoding="utf-8")

    rows = _discover_packs(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row.unparseable is True
    # Without a parseable header we fall back to directory names.
    assert row.name == "broken"
    assert row.family == "messaging"


def test_discover_packs_marks_pydantic_failure_unparseable(tmp_path: Path) -> None:
    """A pack.yaml that parses as yaml but fails AdapterManifest validation.

    A bogus capability kind exercises the ``ManifestParseError`` branch
    distinct from the raw yaml-load failure exercised above. The header
    fields still surface (pack.id / version) because the raw load
    succeeded.
    """
    _write_pack(
        tmp_path,
        "messaging",
        "bad-cap",
        """\
pack:
  id: adapter:messaging-bad-cap
  version: 9.9.9
  family: messaging
capabilities:
  - kind: Cat-Z
    runtime: central
""",
    )
    rows = _discover_packs(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row.unparseable is True
    assert row.name == "messaging-bad-cap"
    assert row.version == "9.9.9"


def test_discover_packs_skips_loose_files_and_missing_pack_yaml(
    tmp_path: Path,
) -> None:
    # Loose file at the family level — must be ignored.
    (tmp_path / "README.md").write_text("not a family\n", encoding="utf-8")
    # Family dir with a non-pack sub-directory (no pack.yaml) — ignored.
    (tmp_path / "messaging" / "draft").mkdir(parents=True)
    # And one real pack so the walk finds something to return.
    _write_pack(
        tmp_path,
        "financial",
        "real",
        """\
pack:
  id: adapter:financial-real
  version: 1.0.0
  family: financial
""",
    )
    rows = _discover_packs(tmp_path)
    assert [r.name for r in rows] == ["financial-real"]


def test_discover_packs_returns_empty_when_root_missing(tmp_path: Path) -> None:
    rows = _discover_packs(tmp_path / "does-not-exist")
    assert rows == []


# ---------- CLI surface ----------


def test_list_command_renders_table_for_synthetic_packs(tmp_path: Path) -> None:
    _write_pack(
        tmp_path,
        "messaging",
        "demo",
        """\
pack:
  id: adapter:messaging-demo
  version: 0.1.0
  family: messaging
capabilities:
  - kind: Cat-A
    runtime: central
""",
    )
    result = runner.invoke(adapters_app, ["list", "--packs-root", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert "messaging-demo" in result.stdout
    assert "Cat-A" in result.stdout
    assert "central" in result.stdout


def test_list_command_surfaces_unparseable_marker(tmp_path: Path) -> None:
    pack_dir = tmp_path / "messaging" / "broken"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(":\n: : :\n", encoding="utf-8")

    result = runner.invoke(adapters_app, ["list", "--packs-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "<!>" in result.stdout
    # Rich's table renderer may truncate the literal ``<unparseable>``
    # mid-word when the column is narrow (default test terminal); the
    # ``<unparseab`` prefix survives both the truncated and full forms.
    assert "<unparseab" in result.stdout


def test_list_command_reports_no_packs_for_empty_root(tmp_path: Path) -> None:
    empty_root = tmp_path / "nope"
    result = runner.invoke(adapters_app, ["list", "--packs-root", str(empty_root)])
    assert result.exit_code == 0
    assert "No adapter packs found" in result.stdout


# ---------- On-main smoke ----------


def test_list_command_finds_three_reference_packs_on_main() -> None:
    """The repo's own ``packs/adapters/`` ships Gmail, Plaid, Reminders.

    Pinned per the prompt's §8 c1 verification block: ``adminme adapters
    list`` should show three rows on a clean main checkout.
    """
    if not ONMAIN_PACKS_ROOT.is_dir():
        # Repo layout drifted — let the failure here flag it loudly
        # rather than silently passing.
        raise AssertionError(f"{ONMAIN_PACKS_ROOT} missing — repo layout drifted?")
    rows = _discover_packs(ONMAIN_PACKS_ROOT)
    names = {r.name for r in rows}
    assert "messaging-gmail-api" in names
    assert "financial-plaid" in names
    assert "reminders-apple-eventkit" in names
    # All three are legacy v1 manifests on main (Cohort C retrofit
    # pending), so kinds/runtime render as em-dash.
    for row in rows:
        assert row.unparseable is False
        if row.name in {
            "messaging-gmail-api",
            "financial-plaid",
            "reminders-apple-eventkit",
        }:
            assert row.kinds == "—"
            assert row.runtime == "—"


# ---------- Table builder ----------


def test_build_table_handles_mixed_unparseable_and_clean_rows(tmp_path: Path) -> None:
    """The renderer accepts a heterogenous row list without raising.

    Smoke check on ``_build_table`` so regressions in the column shape
    surface in unit-test failure rather than at CLI invocation time.
    """
    pack_dir = tmp_path / "messaging" / "demo"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(
        """\
pack:
  id: adapter:messaging-demo
  version: 1.0.0
  family: messaging
capabilities:
  - kind: Cat-A
    runtime: central
""",
        encoding="utf-8",
    )
    clean = _row_from_pack(pack_dir)
    assert clean.unparseable is False

    broken_dir = tmp_path / "messaging" / "broken"
    broken_dir.mkdir(parents=True)
    (broken_dir / "pack.yaml").write_text("::: not yaml :::\n", encoding="utf-8")
    broken = _row_from_pack(broken_dir)
    assert broken.unparseable is True

    table = _build_table([clean, broken])
    assert table.row_count == 2
