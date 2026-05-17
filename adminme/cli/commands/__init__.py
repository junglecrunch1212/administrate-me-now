"""Subcommand modules wired into the top-level ``adminme`` CLI app.

Each module defines a ``typer.Typer`` subgroup that
:func:`adminme.cli.main` registers via ``app.add_typer``. Per the
prompt-11-γ split (`docs/09-split-memo-11-framework.md` §4), this
package starts with the ``adapters`` group (list / validate / scaffold);
future prompts add ``events``, ``projections``, etc. as siblings.
"""
