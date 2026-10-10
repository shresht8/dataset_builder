"""`groundline rows pull | push` (GL-3.5-16): keep a dataset in sync with a file.

`rows pull` writes the dataset's rows to a file and records each row's rev and
base hash in a state file beside it. `rows push` sends the file to the sync API
with that state, so a row changed in Groundline since the pull is a conflict
only if you edited it too; rows you didn't touch are skipped.
"""

from __future__ import annotations

from pathlib import Path

import typer

from groundline_cli.client import ApiClient, ApiError
from groundline_cli.commands._run import api, fail
from groundline_cli.rowfile import (
    RowFileError,
    file_format,
    load_state,
    records_from_rows,
    render_records,
    save_state,
)

app = typer.Typer(help="Pull rows to a file and push edits back")

_SHOWN = 80


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _SHOWN else text[: _SHOWN - 3] + "..."


def _key_column(client: ApiClient, dataset_id: str, name: str) -> tuple[list[dict], str]:
    columns = client.get_schema(dataset_id)["columns"]
    key = next((c["key"] for c in columns if c.get("is_key")), None)
    if key is None:
        fail(f"dataset '{name}' has no key column; mark one in its schema (is_key: true)")
    return columns, key


def _local_edits(result: dict) -> int:
    return len(result["changes"]) + len(result["conflicts"]) + len(result["errors"])


@app.command("pull")
def pull(
    name: str,
    out: str = typer.Option(None, "-o", "--out", help="rows file (default: NAME.yaml)"),
    format: str = typer.Option(None, help="yaml|json|jsonl (default: from the file name)"),
    records_key: str = typer.Option(
        None, "--records-key", help="write the records under this key (yaml/json)"
    ),
    force: bool = typer.Option(False, "--force", help="overwrite unpushed local edits"),
) -> None:
    """Write a dataset's rows to a file you can edit and push back."""
    try:
        fmt = file_format(Path(out or f"{name}.yaml"), format)
    except RowFileError as exc:
        fail(str(exc))
    out = Path(out or f"{name}.{fmt}")
    with api() as client:
        dataset_id = client.resolve_dataset_id(name)
        columns, key = _key_column(client, dataset_id, name)
        if out.exists() and not force:
            state = load_state(out, name, dataset_id) or {}
            try:
                result = client.sync(
                    dataset_id, out.name, out.read_bytes(), records_key=records_key,
                    revs=state.get("revs"), bases=state.get("bases"), dry_run=True,
                )
            except ApiError as exc:
                fail(f"can't check {out} for local edits ({exc}); pass --force to overwrite it")
            if _local_edits(result):
                fail(
                    f"{_local_edits(result)} row(s) in {out} changed locally since your last "
                    "pull - push them first, or pass --force to discard them"
                )
        records, revs, bases = records_from_rows(client.list_rows(dataset_id), columns)
        try:
            out.write_text(render_records(out, fmt, records, records_key), encoding="utf-8")
        except RowFileError as exc:
            fail(str(exc))
        save_state(out, name, dataset_id, key, revs, bases)
    typer.echo(f"pulled {len(records)} rows of {name} -> {out}")


def _report(result: dict, name: str, file: Path) -> None:
    counts = (
        f"created {result['created']}, updated {result['updated']}, "
        f"unchanged {result['unchanged']}, newer in Groundline {result['server_newer']}, "
        f"not in file {result['not_in_file']}"
    )
    typer.echo(counts)
    for change in result["changes"]:
        mark = "+" if change["op"] == "create" else "~"
        typer.echo(f"  {mark} {change['key']}")
        if change["op"] == "update":
            for field, values in change["fields"].items():
                typer.echo(
                    f"      {field}: {_short(values['before'])} -> {_short(values['after'])}"
                )
    if result["conflicts"]:
        typer.echo(f"conflicts ({len(result['conflicts'])}) - changed in Groundline and in the file:")
        for conflict in result["conflicts"]:
            reason = conflict.get("reason") or (
                f"server rev {conflict['server_rev']}, yours {conflict['your_rev']}"
            )
            typer.echo(f"  ! {conflict['key']} ({reason})")
            for field, values in conflict["fields"].items():
                typer.echo(
                    f"      {field}: Groundline {_short(values['server'])} | "
                    f"file {_short(values['file'])}"
                )
    if result["errors"]:
        typer.echo(f"errors ({len(result['errors'])}):")
        for error in result["errors"]:
            where = " ".join(str(p) for p in (error.get("key"), error.get("column")) if p)
            typer.echo(f"  {where}: {error['reason']}")
    if result["ignored_paths"]:
        typer.echo(f"ignored paths: {', '.join(result['ignored_paths'])}")
    if result["server_newer_keys"]:
        typer.echo(
            f"{len(result['server_newer_keys'])} row(s) changed in Groundline since your last "
            f"pull ({', '.join(result['server_newer_keys'])}); run "
            f"`groundline rows pull {name} -o {file}` to get them"
        )


@app.command("push")
def push(
    name: str,
    file: Path,
    records_key: str = typer.Option(None, "--records-key", help="key holding the records"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show what would change, apply nothing"),
    force: bool = typer.Option(False, "--force", help="overwrite rows changed in Groundline"),
    ignore_unknown: bool = typer.Option(
        False, "--ignore-unknown", help="skip fields with no matching column"
    ),
) -> None:
    """Push a rows file: create new keys, update edited rows, report conflicts."""
    try:
        content = file.read_bytes()
    except OSError as exc:
        fail(f"can't read {file}: {exc}")
    with api() as client:
        dataset_id = client.resolve_dataset_id(name)
        _, key = _key_column(client, dataset_id, name)
        state = load_state(file, name, dataset_id) or {"revs": {}, "bases": {}}
        result = client.sync(
            dataset_id, file.name, content, records_key=records_key,
            revs=state["revs"], bases=state["bases"],
            force=force, dry_run=dry_run, ignore_unknown=ignore_unknown,
        )
    _report(result, name, file)
    if result["applied"]:
        save_state(
            file, name, dataset_id, key,
            {**state["revs"], **result["revs"]}, {**state["bases"], **result["bases"]},
        )
        typer.echo("pushed")
        return
    if dry_run and not result["conflicts"] and not result["errors"]:
        typer.echo("dry run: nothing applied")
        return
    if result["errors"]:
        fail("nothing applied: fix the errors above")
    fail(
        "nothing applied: rows pull to take Groundline's changes, "
        "or push with --force to overwrite them"
    )
