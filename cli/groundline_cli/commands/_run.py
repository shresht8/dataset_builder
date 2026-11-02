"""Shared plumbing for commands: an API client, and one-line failures (exit 1)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import NoReturn

import typer

from groundline_cli.client import ApiClient, ApiError
from groundline_cli.config import ConfigError, load_config


def fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


@contextmanager
def api() -> Iterator[ApiClient]:
    """A configured client; config and API errors print and exit 1."""
    try:
        config = load_config()
    except ConfigError as exc:
        fail(str(exc))
    with ApiClient(config) as client:
        try:
            yield client
        except ApiError as exc:
            fail(str(exc))
