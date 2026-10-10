"""`groundline schema infer | pull | push` (GL-3.5-16)."""

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


@app.command("push")
def push(
    name: str,
    file: Path,
    dry_run: bool = typer.Option(False, "--dry-run", help="show the changes, apply nothing"),
    yes: bool = typer.Option(False, "--yes", help="don't ask before archiving columns"),
) -> None:
    """Replace a dataset's schema with a schema file. Absent columns are archived."""
    try:
        new_columns = load_schema_file(file)
    except SchemaFileError as exc:
        fail(str(exc))
    with api() as client:
        dataset_id = client.resolve_dataset_id(name)
        current = {c["key"]: c for c in client.get_schema(dataset_id)["columns"]}
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
        if dry_run or not (added or changed or archived):
            return
        if archived and not yes:
            typer.confirm(
                f"Archive {len(archived)} column(s)? Their values stay in old rows and versions",
                abort=True,
            )
        client.put_schema(dataset_id, new_columns)
    typer.echo(f"schema of {name} updated")
