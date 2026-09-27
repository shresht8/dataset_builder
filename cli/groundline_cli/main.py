"""CLI entrypoint (§7).

    groundline datasets list
    groundline pull claims-eligibility@v3 --format yaml -o ./evals/
    groundline pull --lock
    groundline datasets diff claims-eligibility v3 v4
"""

from __future__ import annotations

from pathlib import Path

import typer

from groundline_cli.client import ApiClient, ApiError
from groundline_cli.commands import datasets as datasets_cmd
from groundline_cli.config import ConfigError, load_config
from groundline_cli.pull import (
    HashMismatchError,
    check_drift,
    parse_target,
    pull_version,
)

app = typer.Typer(help="Groundline dataset builder CLI")
app.add_typer(datasets_cmd.app, name="datasets")

_FORMATS = ("jsonl", "json", "yaml")


@app.command()
def pull(
    target: str = typer.Argument(None, help="name@vN; omit with --lock"),
    lock: bool = typer.Option(False, "--lock", help="restore everything in groundline.lock"),
    format: str = typer.Option("jsonl", help="jsonl|json|yaml"),
    out: str = typer.Option(".", "-o", "--out", help="output directory"),
) -> None:
    """Pull a version (or everything pinned in the lock file) and verify hashes."""
    if lock:
        typer.echo("pull --lock is not yet implemented (GL-3-8)", err=True)
        raise typer.Exit(code=1)
    if target is None:
        typer.echo("pull requires name@vN, or --lock", err=True)
        raise typer.Exit(code=1)
    if format not in _FORMATS:
        typer.echo(f"invalid --format '{format}': expected one of {_FORMATS}", err=True)
        raise typer.Exit(code=1)

    try:
        config = load_config()
        name, version = parse_target(target)
    except (ConfigError, ApiError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    with ApiClient(config) as client:
        try:
            content_hash = pull_version(client, name, version, format, Path(out))
            missing = check_drift(client, name, version)
        except ApiError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        except HashMismatchError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    dest = Path(out) / name / f"v{version}"
    typer.echo(f"pulled {name}@v{version} -> {dest} ({content_hash})")
    if missing:
        typer.echo(
            "warning: current schema has required column(s) not present in this "
            "version: " + ", ".join(missing),
            err=True,
        )


if __name__ == "__main__":
    app()
