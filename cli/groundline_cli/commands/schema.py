"""`groundline schema infer | pull | push` (GL-3.5-16), `schema apply` (Phase 4)."""

from __future__ import annotations

from pathlib import Path

import typer
from groundline_schema.paths import PathError
from groundline_schema.records import RecordProblem, RecordsError, parse_records

from groundline_cli.commands._run import api, fail
from groundline_cli.schemafile import (
    SchemaFileError,
    infer_columns,
    load_schema_file,
    render_schema,
    wire_columns,
)

app = typer.Typer(help="Draft, download and upload dataset schemas")

_COMPARED = ("label", "type", "options", "required", "is_key", "json_schema")


def _write(text: str, out: Path | None) -> None:
    if out is None:
        typer.echo(text, nl=False)
    else:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}", err=True)


@app.command("infer")
def infer(
    file: str = typer.Argument(..., help="a .json, .jsonl or .yaml data file"),
    records_key: str = typer.Option(None, "--records-key", help="key holding the records"),
    key: str = typer.Option(None, "--key", help="field that identifies a record"),
    out: str = typer.Option(None, "-o", "--out", help="write here instead of stdout"),
) -> None:
    """Draft a schema from a data file. Review it before `datasets create`."""
    path = Path(file)
    try:
        parsed = parse_records(path.name, path.read_bytes(), records_key)
        records = [r for r in parsed.records if not isinstance(r, RecordProblem)]
        columns, warnings = infer_columns(records, key)
    except OSError as exc:
        fail(f"can't read {file}: {exc}")
    except (RecordsError, PathError, SchemaFileError) as exc:
        fail(str(exc))
    for warning in warnings:
        typer.echo(f"warning: {warning}", err=True)
    header = f"Draft inferred from {path.name} ({len(records)} records) - review types, options, required"
    _write(render_schema(columns, header), Path(out) if out else None)


@app.command("pull")
def pull(
    name: str,
    out: str = typer.Option(None, "-o", "--out", help="write here instead of stdout"),
) -> None:
    """Download a dataset's current schema as a schema file."""
    with api() as client:
        columns = client.get_schema(client.resolve_dataset_id(name))["columns"]
    _write(render_schema(wire_columns(columns)), Path(out) if out else None)


def _summarise(current_columns: list[dict], new_columns: list[dict]) -> tuple[bool, list[str]]:
    """Print added/changed/archived column keys. Returns (anything changes, archived keys)."""
    current = {c["key"]: c for c in current_columns}
    new = {c["key"]: c for c in new_columns}
    added = [k for k in new if k not in current]
    archived = [k for k in current if k not in new]
    changed = [
        k for k in new
        if k in current
        and any(new[k].get(f) != current[k].get(f) for f in _COMPARED)
    ]
    for label, keys in (("added", added), ("changed", changed), ("archived", archived)):
        if keys:
            typer.echo(f"{label}: {', '.join(keys)}")
    if not (added or changed or archived):
        typer.echo("no changes")
        return False, []
    return True, archived


def _check_dry_run(result: dict) -> None:
    """Exit non-zero with the server's message when the change would break stored rows (GL-134)."""
    if "ok" not in result:  # an older server ignores ?dry_run and applies the change
        fail("this server does not support dry runs: the change was applied")
    if not result["ok"]:
        fail(str(result.get("detail") or "the change would break stored rows"))


def _confirm_archive(archived: list[str], yes: bool) -> None:
    if archived and not yes:
        typer.confirm(
            f"Archive {len(archived)} column(s)? Their values stay in old rows and versions",
            abort=True,
        )


@app.command("push")
def push(
    name: str,
    file: Path,
    dry_run: bool = typer.Option(False, "--dry-run", help="show the changes, apply nothing"),
    yes: bool = typer.Option(False, "--yes", help="don't ask before archiving columns"),
) -> None:
    """Replace a dataset's schema with a schema file. Absent columns are archived.

    In a collection, the schema is matched against the collection's store: an
    identical entry is reused, anything else is added as a new entry (D7).
    """
    try:
        new_columns = load_schema_file(file)
    except SchemaFileError as exc:
        fail(str(exc))
    with api() as client:
        dataset_id = client.resolve_dataset_id(name)
        changes, archived = _summarise(client.get_schema(dataset_id)["columns"], new_columns)
        if not changes:
            return
        # Ask the server first: a change that would break stored rows is refused
        # before the archive prompt, not after it.
        _check_dry_run(client.put_schema(dataset_id, new_columns, dry_run=True))
        if dry_run:
            typer.echo("ok: no stored row would break")
            return
        _confirm_archive(archived, yes)
        result = client.put_schema(dataset_id, new_columns)
    store_schema = result.get("store_schema")
    if store_schema:
        verb = "added" if store_schema.get("created") else "reused"
        typer.echo(f"store: {verb} {store_schema['name']}")
    typer.echo(f"schema of {name} updated")


@app.command("apply")
def apply(
    name: str = typer.Argument(..., help="collection/.../dataset path"),
    entry: str = typer.Argument(..., help="store schema to switch to"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show the changes, apply nothing"),
    yes: bool = typer.Option(False, "--yes", help="don't ask before archiving columns"),
) -> None:
    """Switch a collection dataset to another schema from its collection's store."""
    with api() as client:
        dataset = client.resolve(name)
        if not dataset.get("collection_id"):
            fail(f"{name} is not in a collection; use `schema push` instead")
        target = client.find_store_entry(dataset["collection_id"], entry)
        current = client.get_schema(dataset["id"])["columns"]
        changes, archived = _summarise(current, target["columns"])
        if not changes and dataset.get("store_schema_id") == target["id"]:
            return
        _check_dry_run(client.apply_schema(dataset["id"], target["id"], dry_run=True))
        if dry_run:
            typer.echo("ok: no stored row would break")
            return
        _confirm_archive(archived, yes)
        client.apply_schema(dataset["id"], target["id"])
    typer.echo(f"{name} now uses store schema {entry}")
