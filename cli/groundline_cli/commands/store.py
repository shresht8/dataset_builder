"""`groundline store list | pull | push | duplicate | delete` (Phase 4, GL-118).

Each collection has a schema store: named schemas its datasets use. A store
schema in use by any dataset is frozen (the API answers 409 and names the
datasets): duplicate it, change the copy, then `schema apply` the copy.
"""

from __future__ import annotations

from pathlib import Path

import typer

from groundline_cli.commands._run import api, fail
from groundline_cli.schemafile import (
    SchemaFileError,
    load_schema_file,
    render_schema,
    wire_columns,
)

app = typer.Typer(help="Manage a collection's schema store")


@app.command("list")
def list_(collection: str) -> None:
    """List a collection's store schemas and the datasets using each."""
    with api() as client:
        entries = client.list_store(client.find_collection(collection)["id"])
    if not entries:
        typer.echo("no store schemas")
        return
    rows = [
        [e["name"], str(len(e["columns"])), ", ".join(u["path"] for u in e["used_by"]) or "-"]
        for e in entries
    ]
    headers = ["NAME", "COLUMNS", "USED BY"]
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(3)]
    typer.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    for row in rows:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths)))


@app.command("pull")
def pull(
    collection: str,
    entry: str,
    out: str = typer.Option(None, "-o", "--out", help="write here instead of stdout"),
) -> None:
    """Download a store schema as a schema file."""
    with api() as client:
        found = client.find_store_entry(client.find_collection(collection)["id"], entry)
    text = render_schema(wire_columns(found["columns"]))
    if out is None:
        typer.echo(text, nl=False)
    else:
        Path(out).write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}", err=True)


@app.command("push")
def push(collection: str, entry: str, file: Path) -> None:
    """Create a store schema from a file, or replace an unused one."""
    try:
        columns = load_schema_file(file)
    except SchemaFileError as exc:
        fail(str(exc))
    with api() as client:
        collection_id = client.find_collection(collection)["id"]
        existing = {e["name"]: e for e in client.list_store(collection_id)}
        if entry in existing:
            client.update_store_entry(collection_id, existing[entry]["id"], columns)
            typer.echo(f"updated store schema {entry}")
        else:
            client.create_store_entry(collection_id, entry, columns)
            typer.echo(f"created store schema {entry}")


@app.command("duplicate")
def duplicate(collection: str, entry: str, new: str) -> None:
    """Copy a store schema under a new name (to change it, then `schema apply`)."""
    with api() as client:
        collection_id = client.find_collection(collection)["id"]
        source = client.find_store_entry(collection_id, entry)
        client.duplicate_store_entry(collection_id, source["id"], new)
    typer.echo(f"duplicated store schema {entry} -> {new}")


@app.command("delete")
def delete(
    collection: str,
    entry: str,
    yes: bool = typer.Option(False, "--yes", help="don't ask for confirmation"),
) -> None:
    """Delete a store schema no dataset uses."""
    if not yes:
        typer.confirm(f"Delete store schema {entry} from {collection}?", abort=True)
    with api() as client:
        collection_id = client.find_collection(collection)["id"]
        client.delete_store_entry(collection_id, client.find_store_entry(collection_id, entry)["id"])
    typer.echo(f"deleted store schema {entry}")
