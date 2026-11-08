"""CLI entrypoint (§7).

    groundline datasets list [--collection C]
    groundline pull claims-eligibility@v3 --format yaml --shape nested -o ./evals/
    groundline pull claims-evals/eligibility/train@v3 -o ./evals/
    groundline pull --lock
    groundline datasets diff claims-eligibility v3 v4

    groundline schema infer questions.yaml --key id -o questions.schema.yaml
    groundline datasets create questions --schema questions.schema.yaml
    groundline rows push questions questions.yaml
    groundline rows pull questions -o questions.yaml --records-key questions
    groundline schema pull questions / schema push questions questions.schema.yaml

    groundline collections list | create | delete | pull | push      (Phase 4)
    groundline folders create claims-evals/eligibility
    groundline store list claims-evals
    groundline datasets move questions --to claims-evals/eligibility
"""

from __future__ import annotations

from pathlib import Path

import typer
from groundline_schema.render import FORMATS, SHAPES

from groundline_cli.client import ApiClient, ApiError
from groundline_cli.commands import collections as collections_cmd
from groundline_cli.commands import datasets as datasets_cmd
from groundline_cli.commands import folders as folders_cmd
from groundline_cli.commands import rows as rows_cmd
from groundline_cli.commands import schema as schema_cmd
from groundline_cli.commands import store as store_cmd
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
app.add_typer(schema_cmd.app, name="schema")
app.add_typer(rows_cmd.app, name="rows")
app.add_typer(collections_cmd.app, name="collections")
app.add_typer(folders_cmd.app, name="folders")
app.add_typer(store_cmd.app, name="store")


@app.command()
def pull(
    target: str = typer.Argument(
        None, help="name@vN or collection/.../name@vN; omit with --lock"
    ),
    lock: bool = typer.Option(False, "--lock", help="restore everything in groundline.lock"),
    format: str = typer.Option(
        None, help="jsonl|json|yaml (default jsonl; with --lock, the pinned format)"
    ),
    shape: str = typer.Option(
        None,
        help="flat|nested: nested re-nests dotted column keys "
        "(default flat; with --lock, the pinned shape)",
    ),
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
    if format is not None and format not in FORMATS:
        typer.echo(f"invalid --format '{format}': expected one of {FORMATS}", err=True)
        raise typer.Exit(code=1)
    if shape is not None and shape not in SHAPES:
        typer.echo(f"invalid --shape '{shape}': expected one of {SHAPES}", err=True)
        raise typer.Exit(code=1)

    try:
        config = load_config()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if lock:
        with ApiClient(config) as client:
            try:
                restored = restore_lock(
                    client,
                    Path(lock_file),
                    format,
                    Path(out),
                    shape,
                    warn=lambda message: typer.echo(message, err=True),
                )
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

    fmt = format or "jsonl"
    shape = shape or "flat"
    with ApiClient(config) as client:
        try:
            dataset_id = client.resolve_dataset_id(name)
            content_hash = pull_version(
                client, name, version, fmt, Path(out), shape, dataset_id=dataset_id
            )
            missing = check_drift(client, name, version, dataset_id=dataset_id)
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
    update_lock(Path(lock_file), name, version, content_hash, fmt, shape, dataset_id=dataset_id)


if __name__ == "__main__":
    app()
