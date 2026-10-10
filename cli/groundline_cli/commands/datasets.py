"""`groundline datasets ...` subcommands (§7)."""

from __future__ import annotations

import re
from pathlib import Path

import typer

from groundline_cli.client import ApiClient, ApiError
from groundline_cli.commands._run import api, fail
from groundline_cli.config import ConfigError, load_config
from groundline_cli.schemafile import SchemaFileError, load_schema_file

app = typer.Typer(help="List, create and diff datasets")

_VERSION_PATTERN = re.compile(r"^v(\d+)$")


def _parse_version_arg(value: str) -> int:
    match = _VERSION_PATTERN.match(value)
    if not match:
        raise ApiError(f"invalid version '{value}': expected vN (e.g. v3)")
    return int(match.group(1))


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]
    typer.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    for row in rows:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths)))


@app.command("list")
def list_() -> None:
    """List datasets available to the caller."""
    try:
        config = load_config()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    with ApiClient(config) as client:
        try:
            datasets = client.list_datasets()
            rows = []
            for dataset in datasets:
                versions = client.get_versions(dataset["id"])
                latest = str(versions[0]["version"]) if versions else "-"
                rows.append(
                    [
                        dataset["name"],
                        dataset["id"],
                        dataset["feature_id"] or "-",
                        latest,
                    ]
                )
        except ApiError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    if not rows:
        typer.echo("no datasets")
        return
    _print_table(["NAME", "ID", "FEATURE", "LATEST"], rows)


def _render_diff(result: dict) -> None:
    added, removed, modified = result["added"], result["removed"], result["modified"]
    schema_changes = result["schema_changes"]
    has_schema_changes = any(schema_changes[k] for k in ("added", "removed", "changed"))

    if not added and not removed and not modified and not has_schema_changes:
        typer.echo("no differences")
        return

    if added:
        typer.echo(f"added ({len(added)}):")
        for row in added:
            typer.echo(f"  + {row['id']} [{row['status']}] {row['data']}")
    if removed:
        typer.echo(f"removed ({len(removed)}):")
        for row in removed:
            typer.echo(f"  - {row['id']} [{row['status']}] {row['data']}")
    if modified:
        typer.echo(f"modified ({len(modified)}):")
        for row in modified:
            status = row["status"]
            suffix = f" status: {status['old']} -> {status['new']}" if status else ""
            typer.echo(f"  ~ {row['id']}{suffix}")
            for change in row["changes"]:
                typer.echo(f"      {change['field']}: {change['old']!r} -> {change['new']!r}")
    if has_schema_changes:
        typer.echo("schema changes:")
        if schema_changes["added"]:
            typer.echo(f"  added: {', '.join(schema_changes['added'])}")
        if schema_changes["removed"]:
            typer.echo(f"  removed: {', '.join(schema_changes['removed'])}")
        if schema_changes["changed"]:
            typer.echo(f"  changed: {', '.join(schema_changes['changed'])}")


@app.command("create")
def create(
    name: str,
    schema: str = typer.Option(..., "--schema", help="schema file (see `schema infer`)"),
    description: str = typer.Option(None, "--description"),
) -> None:
    """Create a dataset and set its schema from a schema file."""
    try:
        columns = load_schema_file(Path(schema))
    except SchemaFileError as exc:
        fail(str(exc))
    with api() as client:
        dataset = client.create_dataset(name, description)
        try:
            client.put_schema(dataset["id"], columns)
        except ApiError as exc:
            fail(
                f"dataset '{name}' was created without a schema: {exc}\n"
                f"fix {schema}, then run: groundline schema push {name} {schema}"
            )
    typer.echo(f"created {name} with {len(columns)} columns")


@app.command()
def diff(dataset: str, from_version: str, to_version: str) -> None:
    """Report rows added, removed, and modified between two versions."""
    try:
        config = load_config()
        from_v = _parse_version_arg(from_version)
        to_v = _parse_version_arg(to_version)
    except (ConfigError, ApiError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    with ApiClient(config) as client:
        try:
            dataset_id = client.resolve_dataset_id(dataset)
            result = client.get_diff(dataset_id, from_v, to_v)
        except ApiError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    _render_diff(result)
