"""`groundline folders create | delete` (Phase 4, GL-118)."""

from __future__ import annotations

import typer

from groundline_cli.client import ApiError
from groundline_cli.commands._run import api, fail
from groundline_cli.paths import locate, split_path

app = typer.Typer(help="Create and delete folders in a collection")


def _segments(path: str) -> list[str]:
    try:
        segments = split_path(path)
    except ApiError as exc:
        fail(str(exc))
    if len(segments) < 2:
        fail(f"expected COLLECTION/FOLDER[/...], got '{path}'")
    return segments


@app.command("create")
def create(path: str = typer.Argument(..., help="COLLECTION/FOLDER/...; the parent must exist")) -> None:
    """Create a folder."""
    segments = _segments(path)
    with api() as client:
        collection, parent_id = locate(client, segments[:-1])
        client.create_folder(collection["id"], segments[-1], parent_id)
    typer.echo(f"created folder {path}")


@app.command("delete")
def delete(path: str = typer.Argument(..., help="COLLECTION/FOLDER/...")) -> None:
    """Delete an empty folder (no subfolders, no datasets)."""
    segments = _segments(path)
    with api() as client:
        _, folder_id = locate(client, segments)
        client.delete_folder(folder_id)
    typer.echo(f"deleted folder {path}")
