"""
adminme CLI entrypoint — Typer app exposed as `adminme` console script.

Implemented in prompt 17 per ADMINISTRATEME_BUILD.md §PHASE PLAN.

Exposes verbs for instance management, pack lifecycle (list / search / info /
install / update / remove / publish), projection rebuilds (`projections
list|rebuild|lag`), skill replay (`skill replay <name> --since <ts>`),
observation mode (`observation on|off`), audit surfaces (`audit privileged-
access`), and bootstrap resumption.

Do not implement in this scaffolding prompt. Prompt 17 will fill in.

The `app` symbol below is referenced by the `[tool.poetry.scripts]` entry in
pyproject.toml (`adminme = "adminme.cli.main:app"`). Stubbed as a Typer app
so importing this module does not crash the entry point machinery.
"""

import typer

app = typer.Typer(
    help="AdministrateMe CLI — implemented in prompt 17.",
    no_args_is_help=True,
)
