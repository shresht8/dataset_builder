"""`groundline datasets ...` subcommands (§7)."""

from __future__ import annotations

import typer

app = typer.Typer(help="Inspect datasets")


@app.command("list")
def list_() -> None:
    """List datasets available to the caller."""
    raise NotImplementedError


@app.command()
def diff(dataset: str, from_version: str, to_version: str) -> None:
    """Report rows added, removed, and modified between two versions."""
    raise NotImplementedError
