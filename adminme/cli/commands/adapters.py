"""``adminme adapters`` Typer subcommand group.

Prompt 11-γ ships three subcommands here: ``list`` (discover installed
packs), ``validate`` (wrap the 11-β six-check validator), and
``scaffold`` (generate a validator-clean skeleton under the [D20]
three-layer developer-mode gate). Commit 1 of the four-commit cycle
introduces ``adapters_app`` and the ``list`` subcommand; ``validate``
and ``scaffold`` land in commits 2 and 3 respectively.

The ``list`` subcommand walks ``<packs-root>/<family>/<name>/`` at
depth 2 (not the entire ``packs/`` tree per the framework's
``packs/adapters/`` placement), parses each ``pack.yaml`` via
:func:`adminme.lib.pack_registry.parse_adapter_manifest`, and renders
the discovered rows as a ``rich.table.Table``. Malformed manifests
surface as an ``<unparseable>`` row with a ``<!>`` marker rather than
aborting the listing — operator visibility for the broken pack matters
more than a clean table.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from adminme.lib.pack_registry import (
    AdapterManifest,
    ManifestParseError,
    parse_adapter_manifest,
)

# The Typer subcommand group — registered into the top-level ``app`` in
# :mod:`adminme.cli.main` via ``app.add_typer(adapters_app, name="adapters")``.
adapters_app = typer.Typer(
    help="Inspect, validate, and scaffold adapter packs.",
    no_args_is_help=True,
)


@adapters_app.callback()
def _adapters_callback() -> None:
    """Group callback — forces Typer to treat ``adapters_app`` as a multi-
    command group from commit 1 (single command). Without this, Typer's
    single-command collapse rule would parse ``adapters list`` as if
    ``list`` were a positional argument to the root command. The
    callback is otherwise a no-op; commits 2 and 3 add ``validate`` and
    ``scaffold`` siblings to ``list``.
    """

# Default discovery root (cwd-relative). The operator typically invokes
# the CLI from the repo root where ``packs/adapters/`` lives; the
# ``--packs-root`` flag exists for testing and for non-repo installs.
_DEFAULT_PACKS_ROOT = Path("packs/adapters")

# Em-dash sentinel for empty cells in the rendered table. Used when a
# legacy v1 ``pack.yaml`` parses cleanly but declares no
# ``capabilities:`` block (so kinds / runtime are unknown).
_EMPTY_CELL = "—"


@dataclass(frozen=True)
class _PackRow:
    """One row in the ``adminme adapters list`` output table.

    Captured as a typed dataclass so the renderer and the discovery
    walker stay decoupled — tests for the walker assert structured rows
    without needing to parse rendered text.
    """

    name: str
    family: str
    version: str
    kinds: str
    runtime: str
    sensitivity_default: str
    unparseable: bool = False


def _row_from_pack(pack_dir: Path) -> _PackRow:
    """Build a :class:`_PackRow` for a single pack directory.

    Parses ``pack.yaml`` twice: once with :func:`parse_adapter_manifest`
    to extract the framework extension fields (capabilities, runtime,
    sensitivity_default), and once with a raw ``yaml.safe_load`` to
    extract the legacy top-level fields (``pack.id``, ``pack.version``,
    ``family``) that the parser intentionally ignores. Failure of either
    parse yields an ``unparseable=True`` row instead of raising — the
    list subcommand surfaces broken packs without aborting.
    """
    pack_yaml = pack_dir / "pack.yaml"

    # Raw read for legacy header fields (pack.id, pack.version, family).
    raw: dict[str, object]
    try:
        with pack_yaml.open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        if not isinstance(loaded, dict):
            return _PackRow(
                name=pack_dir.name,
                family=pack_dir.parent.name,
                version=_EMPTY_CELL,
                kinds=_EMPTY_CELL,
                runtime=_EMPTY_CELL,
                sensitivity_default=_EMPTY_CELL,
                unparseable=True,
            )
        raw = loaded
    except (OSError, yaml.YAMLError):
        return _PackRow(
            name=pack_dir.name,
            family=pack_dir.parent.name,
            version=_EMPTY_CELL,
            kinds=_EMPTY_CELL,
            runtime=_EMPTY_CELL,
            sensitivity_default=_EMPTY_CELL,
            unparseable=True,
        )

    # Parse extension fields. ManifestParseError ⇒ unparseable row.
    try:
        manifest = parse_adapter_manifest(pack_dir)
    except ManifestParseError:
        name, family, version = _extract_header(raw, pack_dir)
        return _PackRow(
            name=name,
            family=family,
            version=version,
            kinds=_EMPTY_CELL,
            runtime=_EMPTY_CELL,
            sensitivity_default=_EMPTY_CELL,
            unparseable=True,
        )

    name, family, version = _extract_header(raw, pack_dir)
    kinds = _join_kinds(manifest)
    runtime = _join_runtimes(manifest)
    return _PackRow(
        name=name,
        family=family,
        version=version,
        kinds=kinds,
        runtime=runtime,
        sensitivity_default=manifest.sensitivity_default,
    )


def _extract_header(raw: dict[str, object], pack_dir: Path) -> tuple[str, str, str]:
    """Pull (name, family, version) from the legacy ``pack:`` block.

    Both shapes seen on main are tolerated: top-level ``family:`` (Gmail
    pack) and nested ``pack.family:`` (Plaid, Reminders). Name is
    derived from ``pack.id`` with the ``adapter:`` prefix stripped so
    the table matches the operator's mental model of the pack's
    identifier (``messaging-gmail-api``, not ``adapter:messaging-gmail-api``).
    """
    pack_block = raw.get("pack")
    pack_dict: dict[str, object] = pack_block if isinstance(pack_block, dict) else {}

    pack_id = pack_dict.get("id")
    if isinstance(pack_id, str):
        name = pack_id.removeprefix("adapter:")
    else:
        name = pack_dir.name

    family = pack_dict.get("family")
    if not isinstance(family, str):
        top_family = raw.get("family")
        family = top_family if isinstance(top_family, str) else pack_dir.parent.name

    version = pack_dict.get("version")
    version_str = version if isinstance(version, str) else _EMPTY_CELL

    return name, family, version_str


def _join_kinds(manifest: AdapterManifest) -> str:
    """Format ``capabilities[*].kind`` as a slash-joined string.

    Empty capabilities (legacy v1 manifest) renders as the em-dash
    sentinel so the column never goes blank — operators can distinguish
    "framework-aware but no capabilities yet" (which would be a
    validator error post-Cohort C) from "legacy v1 pack".
    """
    if not manifest.capabilities:
        return _EMPTY_CELL
    return "/".join(cap.kind for cap in manifest.capabilities)


def _join_runtimes(manifest: AdapterManifest) -> str:
    """Format ``capabilities[*].runtime`` as a slash-joined string.

    Duplicates are collapsed (e.g. two ``central`` capabilities render
    as ``central``, not ``central/central``); a Cat-D + Cat-B multi-cap
    pack with different runtimes renders both (``bridge/central``).
    """
    if not manifest.capabilities:
        return _EMPTY_CELL
    seen: list[str] = []
    for cap in manifest.capabilities:
        if cap.runtime not in seen:
            seen.append(cap.runtime)
    return "/".join(seen)


def _discover_packs(packs_root: Path) -> list[_PackRow]:
    """Walk ``<packs_root>/<family>/<name>/`` and build a row per pack.

    Only directories at depth 2 containing a ``pack.yaml`` are
    considered. Missing or non-directory ``packs_root`` yields an empty
    list (the caller decides whether that's a fatal condition).
    """
    if not packs_root.is_dir():
        return []

    rows: list[_PackRow] = []
    for family_dir in sorted(packs_root.iterdir()):
        if not family_dir.is_dir():
            continue
        for pack_dir in sorted(family_dir.iterdir()):
            if not pack_dir.is_dir():
                continue
            if not (pack_dir / "pack.yaml").exists():
                continue
            rows.append(_row_from_pack(pack_dir))
    return rows


def _build_table(rows: list[_PackRow]) -> Table:
    """Render the discovered rows as a ``rich.table.Table``.

    The ``<!>`` marker in the first column flags unparseable rows; the
    rest of the row uses ``<unparseable>`` for any field the parser
    couldn't reach. Severity coloring lands in commits 2 (validate
    findings) and 3 (scaffold gate diagnostics); the list table itself
    is neutral on coloring.
    """
    table = Table(title="adminme adapters")
    table.add_column("name", overflow="fold")
    table.add_column("family")
    table.add_column("version")
    table.add_column("kinds")
    table.add_column("runtime")
    table.add_column("sensitivity_default")
    for row in rows:
        if row.unparseable:
            table.add_row(
                f"<!> {row.name}",
                row.family,
                "<unparseable>",
                "<unparseable>",
                "<unparseable>",
                "<unparseable>",
            )
        else:
            table.add_row(
                row.name,
                row.family,
                row.version,
                row.kinds,
                row.runtime,
                row.sensitivity_default,
            )
    return table


@adapters_app.command("list")
def list_command(
    packs_root: Path = typer.Option(
        _DEFAULT_PACKS_ROOT,
        "--packs-root",
        help="Directory containing adapter pack families (default: ./packs/adapters)",
    ),
) -> None:
    """List installed adapter packs as a table.

    Walks ``<packs-root>/<family>/<name>/`` and renders each pack's
    name, family, version, declared capability kinds, runtime placement,
    and pack-level sensitivity default. Malformed manifests surface as
    ``<unparseable>`` rows with a ``<!>`` marker; only filesystem-level
    failures (e.g. permission denied on iteration) exit non-zero.
    """
    console = Console()
    try:
        rows = _discover_packs(packs_root)
    except OSError as exc:
        typer.echo(
            f"adminme adapters list: filesystem error reading {packs_root}: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if not rows:
        typer.echo(f"No adapter packs found at {packs_root}.")
        return

    console.print(_build_table(rows))
