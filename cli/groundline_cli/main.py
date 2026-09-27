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
from groundline_cli.lock import LockError, restore_lock, update_lock
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
    lock_file: str = typer.Option(
        "groundline.lock", "--lock-file", help="path to the lock file"
    ),
) -> None:
    """Pull a version (or everything pinned in the lock file) and verify hashes."""
    if lock and target is not None:
        typer.echo("pull: --lock cannot be combined with a target", err=True)
        raise typer.Exit(code=1)
    if not lock and target is None:
        typer.echo("pull requires name@vN, or --lock", err=True)
        raise typer.Exit(code=1)
    if format not in _FORMATS:
        typer.echo(f"invalid --format '{format}': expected one of {_FORMATS}", err=True)
        raise typer.Exit(code=1)

    try:
        config = load_config()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if lock:
        with ApiClient(config) as client:
            try:
                restored = restore_lock(client, Path(lock_file), format, Path(out))
            except (ApiError, HashMismatchError, LockError) as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(code=1) from exc
        for name, version, content_hash in restored:
            dest = Path(out) / name / f"v{version}"
            typer.echo(f"pulled {name}@v{version} -> {dest} ({content_hash})")
        return

    try:
        name, version = parse_target(target)
    except ApiError as exc:
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
    update_lock(Path(lock_file), name, version, content_hash)


if __name__ == "__main__":
    app()
