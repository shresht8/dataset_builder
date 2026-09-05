"""CLI entrypoint (§7).

    groundline datasets list
    groundline pull claims-eligibility@v3 --format yaml -o ./evals/
    groundline pull --lock
    groundline datasets diff claims-eligibility v3 v4
"""

from __future__ import annotations

import typer

from groundline_cli.commands import datasets as datasets_cmd

app = typer.Typer(help="Groundline dataset builder CLI")
app.add_typer(datasets_cmd.app, name="datasets")


@app.command()
def pull(
    target: str = typer.Argument(None, help="name@vN; omit with --lock"),
    lock: bool = typer.Option(False, "--lock", help="restore everything in groundline.lock"),
    format: str = typer.Option("jsonl", help="jsonl|json|yaml"),
    out: str = typer.Option(".", "-o", "--out", help="output directory"),
) -> None:
    """Pull a version (or everything pinned in the lock file) and verify hashes."""
    raise NotImplementedError


if __name__ == "__main__":
    app()
