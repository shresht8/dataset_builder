"""`groundline datasets ...` subcommands (§7)."""

from __future__ import annotations

import re
from pathlib import Path

import typer

from groundline_cli.client import ApiClient, ApiError
from groundline_cli.commands._run import api, fail
from groundline_cli.config import ConfigError, load_config
from groundline_cli.paths import locate, split_path
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
def list_(
    collection: str = typer.Option(None, "--collection", help="only datasets in this collection"),
) -> None:
    """List datasets available to the caller, by path."""
    try:
        config = load_config()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    with ApiClient(config) as client:
        try:
            collection_id = client.find_collection(collection)["id"] if collection else None
            datasets = client.list_datasets(collection_id)
            rows = []
            for dataset in datasets:
                versions = client.get_versions(dataset["id"])
                latest = str(versions[0]["version"]) if versions else "-"
                rows.append(
                    [
                        dataset.get("path") or dataset["name"],
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
    _print_table(["PATH", "ID", "FEATURE", "LATEST"], rows)


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


def store_outcome(store_schema: dict | None) -> str | None:
    """'added to store as X' / 'using store schema X' for a collection dataset (D7)."""
    if not store_schema:
        return None
    if store_schema.get("created"):
        return f"added to store as {store_schema['name']}"
    return f"using store schema {store_schema['name']}"


@app.command("create")
def create(
    name: str = typer.Argument(..., help="name, or collection/folder/.../name"),
    schema: str = typer.Option(None, "--schema", help="schema file (see `schema infer`)"),
    from_store: str = typer.Option(
        None, "--from-store", help="use this store schema (collection datasets only)"
    ),
    description: str = typer.Option(None, "--description"),
) -> None:
    """Create a dataset with a schema file, or (in a collection) a store schema."""
    if (schema is None) == (from_store is None):
        fail("give --schema FILE, or --from-store ENTRY (not both)")
    try:
        segments = split_path(name)
    except ApiError as exc:
        fail(str(exc))
    columns = None
    if schema is not None:
        try:
            columns = load_schema_file(Path(schema))
        except SchemaFileError as exc:
            fail(str(exc))
    if len(segments) > 1:
        with api() as client:
            collection, folder_id = locate(client, segments[:-1])
            store_schema_id = (
                client.find_store_entry(collection["id"], from_store)["id"] if from_store else None
            )
            dataset = client.create_dataset(
                segments[-1],
                description,
                collection_id=collection["id"],
                folder_id=folder_id,
                store_schema_id=store_schema_id,
                columns=columns,
            )
            count = len(client.get_schema(dataset["id"])["columns"])
        typer.echo(f"created {dataset.get('path', name)} with {count} columns")
        typer.echo(store_outcome(dataset.get("store_schema")))
        return
    if from_store:
        fail("--from-store needs a collection path (collection/.../name)")
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


@app.command("move")
def move(
    path: str = typer.Argument(..., help="the dataset's current path"),
    to: str = typer.Option(None, "--to", help="target: COLLECTION or COLLECTION/FOLDER/..."),
    unfiled: bool = typer.Option(False, "--unfiled", help="take it out of its collection"),
) -> None:
    """Move a dataset into, between or out of collections. Its data is kept."""
    if (to is None) == (not unfiled):
        fail("give --to COLLECTION[/FOLDER...], or --unfiled (not both)")
    with api() as client:
        dataset = client.resolve(path)
        if unfiled:
            collection_id, folder_id = None, None
        else:
            try:
                collection, folder_id = locate(client, split_path(to))
            except ApiError as exc:
                fail(str(exc))
            collection_id = collection["id"]
        moved = client.move_dataset(dataset["id"], collection_id, folder_id)
    typer.echo(f"moved {path} -> {moved.get('path')}")
    if moved.get("store_schema") and moved.get("collection_id") != dataset.get("collection_id"):
        typer.echo(store_outcome(moved["store_schema"]))


@app.command("delete")
def delete(
    name: str,
    yes: bool = typer.Option(False, "--yes", help="don't ask for confirmation"),
) -> None:
    """Delete a dataset that has no versions (its name becomes free again)."""
    if not yes:
        typed = typer.prompt(f"Type the dataset name to delete it ({name})")
        if typed != name:
            fail("name didn't match; nothing deleted")
    with api() as client:
        client.delete_dataset(client.resolve_dataset_id(name))
    typer.echo(f"deleted {name}")


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
