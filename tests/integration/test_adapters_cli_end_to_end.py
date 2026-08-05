"""End-to-end integration tests for the prompt 11-γ ``adminme adapters`` CLI.

Closes prompt 11-γ's C4 deliverable: the three subcommands shipped in
c1+c2+c3 (``list`` / ``validate`` / ``scaffold``) are exercised together
end-to-end. The integration scenario is the framework's full developer
workflow: ``scaffold`` writes a pack from a per-category template tree,
then ``validate`` runs the 11-β six-check validator on the freshly
written pack, then ``list`` discovers the pack alongside on-main packs.
The contract is "scaffold produces packs the validator accepts" —
asserted by walking the CLI exit codes directly, not by stubbing.

Per [D20] (three-layer developer-mode gate): ``scaffold`` requires the
``ADMINME_DEVELOPER_MODE=1`` environment variable, the ``--allow-scaffold``
flag, and either interactive confirmation or ``--yes``. The integration
tests set the env var via ``monkeypatch`` and pass both flags so each
end-to-end run exercises the post-gate happy path without coupling to
the gate's refusal semantics (which the unit tests in
``tests/unit/cli/commands/test_adapters_scaffold.py`` cover).

Per ``docs/SYSTEM_INVARIANTS.md`` §7.3 / [D17]: the CLI does NOT write
projections — it is pure-functional over filesystem + validator. The
end-to-end tests confirm this contract holds by exercising the
scaffold-then-validate cycle without touching any projection table; the
generated pack lives entirely on ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from adminme.cli.commands.adapters import adapters_app
from adminme.cli.main import app as main_app

runner = CliRunner()


def _scaffold(tmp_path: Path, kind: str, runtime: str, name: str) -> Path:
    """Invoke ``adminme adapters scaffold <kind> <runtime>`` end-to-end.

    Returns the resolved output directory so the caller can pass it to
    the ``validate`` subcommand. Asserts exit 0 on the scaffold itself —
    a non-zero exit here indicates a gate or template-loader regression,
    not a validator finding.
    """
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
    assert result.exit_code == 0, (
        f"scaffold {kind}/{runtime} failed: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert (output / "pack.yaml").exists()
    assert (output / "adapter.py").exists()
    return output


def _validate(pack_dir: Path) -> None:
    """Invoke ``adminme adapters validate <pack-dir>`` and assert exit 0.

    Exit 0 means the validator found zero error-severity findings (the
    happy-path contract for a freshly-scaffolded pack per prompt 11-γ
    §7). Warnings and advisories do NOT fail the exit code per the
    CLI's documented severity ladder.
    """
    result = runner.invoke(adapters_app, ["validate", str(pack_dir)])
    assert result.exit_code == 0, (
        f"validate {pack_dir} exit={result.exit_code}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.fixture
def _developer_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the [D20] developer-mode env var for one test only."""
    monkeypatch.setenv("ADMINME_DEVELOPER_MODE", "1")


def test_cat_a_central_scaffold_then_validate_clean(
    _developer_mode: None, tmp_path: Path
) -> None:
    """Cat-A central: scaffold a messaging adapter → validate exits 0.

    The canonical worked example for the operator-facing seam: an
    author scaffolds the Gmail-shape pack, the validator confirms the
    skeleton passes all six checks (capability-runtime coherence,
    event-schema registration, projection subscription, base class
    conformance, write_capabilities, observation-mode wrapping),
    and the pack is ready for the supervisor's install flow.
    """
    pack_dir = _scaffold(tmp_path, "Cat-A", "central", "cat-a-e2e")
    _validate(pack_dir)


def test_cat_b_central_scaffold_then_validate_clean(
    _developer_mode: None, tmp_path: Path
) -> None:
    """Cat-B central: scaffold an external-state-mirror adapter → validate clean.

    Cat-B is the write-surface-bearing category — the template emits a
    ``write_capabilities`` block on the manifest plus matching
    ``_handle_<op>`` methods on the adapter class so the 11-β
    write-capabilities check passes on first run [UT-27].
    """
    pack_dir = _scaffold(tmp_path, "Cat-B", "central", "cat-b-e2e")
    _validate(pack_dir)


def test_cat_d_bridge_scaffold_then_validate_clean(
    _developer_mode: None, tmp_path: Path
) -> None:
    """Cat-D bridge: scaffold a personal-knowledge adapter → validate clean.

    Cat-D is the Tailscale-bridge-bound category per memo 04 §2.2 —
    the template emits ``runtime: bridge`` and
    ``owner_scope_overridability_at_install: false`` to satisfy the
    capability-runtime-coherence check. The on-main projections tree
    handles the family declaration (``advisory_pending_projection``
    or clean) without blocking install.
    """
    pack_dir = _scaffold(tmp_path, "Cat-D", "bridge", "cat-d-e2e")
    _validate(pack_dir)


def test_cat_e_central_scaffold_then_validate_clean(
    _developer_mode: None, tmp_path: Path
) -> None:
    """Cat-E central: scaffold an outbound-action adapter → validate clean.

    Cat-E is the outbound-action category — the template emits
    ``observation_mode_required: true`` on the manifest and routes
    ``execute_action`` through ``adminme.lib.observation.outbound`` per
    [§6.20]. The 11-β observation-mode-wrapping check verifies the
    routing by AST-walking the adapter source; the scaffold's
    template guarantees the check passes on first run.
    """
    pack_dir = _scaffold(tmp_path, "Cat-E", "central", "cat-e-e2e")
    _validate(pack_dir)


def test_list_subcommand_includes_scaffolded_pack(
    _developer_mode: None, tmp_path: Path
) -> None:
    """End-to-end discovery: ``adminme adapters list --packs-root`` finds
    a freshly-scaffolded pack alongside any on-main packs in the tree.

    The scaffold lands at ``<root>/<family>/<name>/`` (mirroring the
    on-main ``packs/adapters/<family>/<name>/`` layout); the list
    subcommand walks depth-2 and emits the pack's identifier in the
    rendered table. This pins the contract that the three subcommands
    compose into a coherent operator workflow: scaffold writes packs
    the validator accepts AND the list subcommand discovers.
    """
    family_root = tmp_path / "messaging"
    family_root.mkdir()
    _scaffold(family_root, "Cat-A", "central", "demo")

    result = runner.invoke(
        main_app, ["adapters", "list", "--packs-root", str(tmp_path)]
    )
    assert result.exit_code == 0, (
        f"list exit={result.exit_code}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # ``rich.table`` wraps cells when the terminal width forces it, so
    # assert against fields that fit each column's natural width: the
    # short family name (``messaging``), the kind (``Cat-A``), and the
    # runtime (``central``). Together they uniquely identify our
    # scaffolded pack — no on-main packs appear in ``tmp_path``.
    assert "messaging" in result.stdout
    assert "Cat-A" in result.stdout
    assert "central" in result.stdout
