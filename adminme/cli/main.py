"""Supported Typer entrypoint for adapter validation and canonical demos."""

import typer

from adminme.cli.commands.adapters import adapters_app
from adminme.cli.commands.demo import demo_app

app = typer.Typer(help="AdministrateMe operator CLI.", no_args_is_help=True)
app.add_typer(adapters_app, name="adapters")
app.add_typer(demo_app, name="demo")


if __name__ == "__main__":
    app()
