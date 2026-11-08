"""`groundline collections list | create | delete | pull | push` (Phase 4).

`pull` writes a collection as a directory in the bundle layout (C4) and
records each dataset's sync state, as `rows pull` does. `push` sends a
directory back:

- a collection that doesn't exist yet is created in one step through the
  bundle import API;
- otherwise missing store schemas, folders and datasets are created, and every
  existing dataset's rows go through the row sync API (dry run, conflicts,
  revs/bases). A store schema that exists with different columns is reported,
  never edited (schemas in use are frozen; use `store` / `schema apply`).
  Push never deletes anything.

Push runs in two passes: a dry run of every dataset's sync first, and if any
dataset has conflicts or errors nothing is applied. Collection-wide atomicity
isn't possible across API calls: if a row changes in Groundline between the
two passes, that dataset's sync is refused and the others still apply.
"""

from __future__ import annotations

from pathlib import Path

import typer
from groundline_schema import schema_hash
from groundline_schema.bundle import (
    Bundle,
    BundleError,
    read_dir,
    read_zip,
    write_dir,
    write_zip,
)

from groundline_cli.client import ApiClient, ApiError
from groundline_cli.commands._run import api, fail
from groundline_cli.commands.rows import _report
from groundline_cli.paths import dataset_paths, folder_paths
from groundline_cli.rowfile import dump_yaml, load_state, records_from_rows, save_state

app = typer.Typer(help="Manage collections; pull and push them as directories")

_PULL_CAP = 2 * 1024 * 1024 * 1024  # the server already capped what it wrote


@app.command("list")
def list_() -> None:
    """List collections."""
    with api() as client:
        collections = client.list_collections()
    if not collections:
        typer.echo("no collections")
        return
    rows = [[c["name"], str(c["dataset_count"]), c.get("description") or ""] for c in collections]
    headers = ["NAME", "DATASETS", "DESCRIPTION"]
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(3)]
    typer.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip())
    for row in rows:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())


@app.command("create")
def create(name: str, description: str = typer.Option(None, "--description")) -> None:
    """Create an empty collection."""
    with api() as client:
        client.create_collection(name, description)
    typer.echo(f"created collection {name}")


@app.command("delete")
def delete(
    name: str,
    yes: bool = typer.Option(False, "--yes", help="don't ask for confirmation"),
) -> None:
    """Delete a collection that holds no datasets (its folders and store go too)."""
    if not yes:
        typed = typer.prompt(f"Type the collection name to delete it ({name})")
        if typed != name:
            fail("name didn't match; nothing deleted")
    with api() as client:
        client.delete_collection(client.find_collection(name)["id"])
    typer.echo(f"deleted collection {name}")


# --- pull --------------------------------------------------------------------


def _unpushed_edits(client: ApiClient, root: Path, name: str) -> list[str]:
    """Datasets in an existing pulled directory whose files have edits not yet pushed."""
    try:
        local = read_dir(root)
    except BundleError:
        return []
    edited = []
    for ds in local.datasets:
        full = f"{name}/{ds.path}"
        rows_file = root / f"{ds.path}.yaml"
        try:
            dataset_id = client.resolve_dataset_id(full)
        except ApiError:
            continue
        state = load_state(rows_file, full, dataset_id)
        if state is None:
            continue
        result = client.sync(
            dataset_id, rows_file.name, ds.content,
            revs=state.get("revs"), bases=state.get("bases"), dry_run=True,
        )
        count = len(result["changes"]) + len(result["conflicts"]) + len(result["errors"])
        if count:
            edited.append(f"{ds.path} ({count} row(s))")
    return edited


@app.command("pull")
def pull(
    name: str,
    out: str = typer.Option(None, "-o", "--out", help="directory (default: NAME)"),
    source: str = typer.Option("draft", "--source", help="draft (live rows) or latest (versions)"),
    force: bool = typer.Option(False, "--force", help="overwrite unpushed local edits"),
) -> None:
    """Write a collection to a directory: collection.yaml, _store/, one file per dataset."""
    if source not in ("draft", "latest"):
        fail(f"invalid --source '{source}': expected draft or latest")
    root = Path(out or name)
    with api() as client:
        collection = client.find_collection(name)
        if not force:
            edited = _unpushed_edits(client, root, name)
            if edited:
                fail(
                    "rows changed locally since your last pull: " + ", ".join(edited)
                    + " - push them first, or pass --force to discard them"
                )
        try:
            bundle = read_zip(client.export_collection(collection["id"], source), _PULL_CAP)
        except BundleError as exc:
            fail(f"can't read the exported bundle: {exc}")

        states = []
        if source == "draft":
            # Rebuild each file from the rows we take the revs from, so the
            # file, its revs and its bases describe the same moment.
            ids = dataset_paths(client.get_collection(collection["id"]))
            for ds in bundle.datasets:
                dataset_id = ids[ds.path]["id"]
                columns = client.get_schema(dataset_id)["columns"]
                key = next((c["key"] for c in columns if c.get("is_key")), None)
                if key is None:
                    continue
                records, revs, bases = records_from_rows(client.list_rows(dataset_id), columns)
                ds.content = dump_yaml(records).encode("utf-8")
                states.append((ds.path, dataset_id, key, revs, bases))

    write_dir(bundle, root)
    for path, dataset_id, key, revs, bases in states:
        save_state(root / f"{path}.yaml", f"{name}/{path}", dataset_id, key, revs, bases)
    typer.echo(
        f"pulled {len(bundle.datasets)} dataset(s) of {name} ({source}) -> {root}"
    )
    for skipped in bundle.skipped:
        typer.echo(f"skipped {skipped['path']}: {skipped['reason']}")
    if source == "latest":
        typer.echo(
            "note: no sync state is written for --source latest; a push treats "
            "every row that differs from Groundline as an edit",
            err=True,
        )


# --- push --------------------------------------------------------------------


def _counts(result: dict) -> str:
    return (
        f"created {result['created']}, updated {result['updated']}, "
        f"unchanged {result['unchanged']}, newer in Groundline {result['server_newer']}"
    )


def _push_new(client: ApiClient, root: Path, bundle: Bundle, dry_run: bool) -> None:
    content = write_zip(bundle)
    report = client.import_collection(content, preview=True)
    typer.echo(f"collection {bundle.name} doesn't exist yet: it will be created")
    for ds in report["datasets"]:
        typer.echo(f"  + {ds['path']}: {ds['rows']} row(s), store schema {ds['store_schema']}")
        for error in ds["errors"]:
            typer.echo(f"      row {error['row']} {error['column']}: {error['reason']}")
    for error in report["errors"]:
        typer.echo(f"  ! {error}")
    for entry in report["store"]:
        if entry["error"]:
            typer.echo(f"  ! store schema {entry['name']}: {entry['error']}")
    if not report["ok"]:
        fail("nothing applied: fix the errors above")
    if dry_run:
        typer.echo("dry run: nothing applied")
        return
    created = client.import_collection(content)
    # Record sync state, so the next push only sends real edits.
    ids = dataset_paths(client.get_collection(created["id"]))
    for ds in bundle.datasets:
        dataset_id = ids[ds.path]["id"]
        columns = client.get_schema(dataset_id)["columns"]
        key = next((c["key"] for c in columns if c.get("is_key")), None)
        if key is not None:
            _, revs, bases = records_from_rows(client.list_rows(dataset_id), columns)
            save_state(root / f"{ds.path}.yaml", f"{bundle.name}/{ds.path}", dataset_id, key, revs, bases)
    typer.echo(f"created collection {bundle.name} with {len(bundle.datasets)} dataset(s)")


@app.command("push")
def push(
    directory: str = typer.Argument(..., help="a directory written by `collections pull`"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show what would change, apply nothing"),
    force: bool = typer.Option(False, "--force", help="overwrite rows changed in Groundline"),
    ignore_unknown: bool = typer.Option(
        False, "--ignore-unknown", help="skip fields with no matching column"
    ),
) -> None:
    """Push a collection directory: create what's missing, sync rows. Never deletes.

    Every dataset is dry-run first; any conflict or error anywhere and nothing
    is applied. Atomicity across datasets isn't possible: a row changed in
    Groundline between the dry run and the apply refuses that dataset only.
    """
    directory = Path(directory)
    try:
        bundle = read_dir(directory)
    except (BundleError, OSError) as exc:
        fail(str(exc))

    with api() as client:
        existing = next((c for c in client.list_collections() if c["name"] == bundle.name), None)
        if existing is None:
            _push_new(client, directory, bundle, dry_run)
            return

        collection_id = existing["id"]
        tree = client.get_collection(collection_id)
        on_server = dataset_paths(tree)
        folder_ids = {path: folder_id for folder_id, path in folder_paths(tree).items()}
        store = {e["name"]: e for e in client.list_store(collection_id)}

        problems: list[str] = []
        new_entries = [n for n in bundle.store if n not in store]
        for name in bundle.store:
            if name in store and schema_hash(bundle.store[name]) != store[name]["content_hash"]:
                typer.echo(
                    f"store schema {name} differs from Groundline's and was not changed "
                    "(store schemas in use are frozen; use `groundline store` and `schema apply`)"
                )
        new_folders = [f for f in bundle.folders if f not in folder_ids]
        new_datasets = [ds for ds in bundle.datasets if ds.path not in on_server]
        for ds in new_datasets:
            columns = bundle.store.get(ds.store_schema) or store.get(ds.store_schema, {}).get("columns")
            if columns is None:
                problems.append(f"{ds.path}: store schema '{ds.store_schema}' not found")
            elif not any(c.get("is_key") for c in columns):
                problems.append(f"{ds.path}: store schema '{ds.store_schema}' has no key column")

        # Pass 1: dry-run every existing dataset.
        planned = []
        blocked = []
        for ds in bundle.datasets:
            if ds.path not in on_server:
                continue
            dataset_id = on_server[ds.path]["id"]
            full = f"{bundle.name}/{ds.path}"
            rows_file = directory / f"{ds.path}.yaml"
            state = load_state(rows_file, full, dataset_id) or {"revs": {}, "bases": {}}
            try:
                result = client.sync(
                    dataset_id, rows_file.name, ds.content,
                    revs=state["revs"], bases=state["bases"],
                    force=force, dry_run=True, ignore_unknown=ignore_unknown,
                )
            except ApiError as exc:
                problems.append(f"{ds.path}: {exc}")
                continue
            planned.append((ds, dataset_id, full, rows_file, state))
            if result["conflicts"] or result["errors"]:
                blocked.append((ds.path, full, rows_file, result))
            else:
                typer.echo(f"{ds.path}: {_counts(result)}")

        for name in new_entries:
            typer.echo(f"+ store schema {name}")
        for folder in new_folders:
            typer.echo(f"+ folder {folder}")
        for ds in new_datasets:
            typer.echo(f"+ dataset {ds.path} (store schema {ds.store_schema})")

        if blocked or problems:
            for path, full, rows_file, result in blocked:
                typer.echo(f"== {path}")
                _report(result, full, rows_file)
            for problem in problems:
                typer.echo(f"! {problem}")
            fail("nothing applied: resolve the problems above (rows pull, or push with --force)")
        if dry_run:
            typer.echo("dry run: nothing applied")
            return

        # Pass 2: apply.
        for name in new_entries:
            store[name] = client.create_store_entry(collection_id, name, bundle.store[name])
        for folder in new_folders:
            parent, _, leaf = folder.rpartition("/")
            folder_ids[folder] = client.create_folder(collection_id, leaf, folder_ids.get(parent))["id"]
        for ds in new_datasets:
            created = client.create_dataset(
                ds.name, ds.description,
                collection_id=collection_id,
                folder_id=folder_ids.get(ds.folder) if ds.folder else None,
                store_schema_id=store[ds.store_schema]["id"],
            )
            planned.append(
                (ds, created["id"], f"{bundle.name}/{ds.path}", directory / f"{ds.path}.yaml",
                 {"revs": {}, "bases": {}})
            )

        refused = []
        for ds, dataset_id, full, rows_file, state in planned:
            result = client.sync(
                dataset_id, rows_file.name, ds.content,
                revs=state["revs"], bases=state["bases"],
                force=force, ignore_unknown=ignore_unknown,
            )
            if not result["applied"]:
                refused.append(ds.path)
                typer.echo(f"== {ds.path} (refused)")
                _report(result, full, rows_file)
                continue
            key = next(c["key"] for c in client.get_schema(dataset_id)["columns"] if c.get("is_key"))
            save_state(
                rows_file, full, dataset_id, key,
                {**state["revs"], **result["revs"]}, {**state["bases"], **result["bases"]},
            )
            typer.echo(f"{ds.path}: {_counts(result)}")
    if refused:
        fail("not applied for: " + ", ".join(refused) + " (changed in Groundline meanwhile)")
    typer.echo("pushed")
