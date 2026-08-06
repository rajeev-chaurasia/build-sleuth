"""Command line entry point.

Subcommands are added phase by phase. Each maps to one pipeline stage so
every stage stays independently runnable and debuggable.
"""

import typer

from buildsleuth import __version__

app = typer.Typer(
    name="buildsleuth",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Triage failing CI runs: classify, localize, and propose verified fixes."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"buildsleuth {__version__}")
