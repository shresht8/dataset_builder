"""CLI configuration: API base URL and personal access token (§5, §7).

Precedence: the environment variables `GROUNDLINE_API_URL` / `GROUNDLINE_TOKEN`
win. Otherwise both are read from a config file at
``~/.config/groundline/config.toml``::

    api_url = "https://groundline.example.com"
    token = "gl_pat_..."

Either value may come from the env while the other comes from the file. If
neither source supplies both, `load_config` raises `ConfigError` with a
message naming what's missing and where to put it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import tomllib

CONFIG_PATH = Path.home() / ".config" / "groundline" / "config.toml"


class ConfigError(Exception):
    """Raised when the API URL or token can't be resolved."""


@dataclass(frozen=True)
class Config:
    api_url: str
    token: str


def _read_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_config() -> Config:
    file_data = _read_config_file(CONFIG_PATH)
    api_url = os.environ.get("GROUNDLINE_API_URL") or file_data.get("api_url")
    token = os.environ.get("GROUNDLINE_TOKEN") or file_data.get("token")

    missing = []
    if not api_url:
        missing.append("api_url (env GROUNDLINE_API_URL)")
    if not token:
        missing.append("token (env GROUNDLINE_TOKEN)")
    if missing:
        raise ConfigError(
            "missing configuration: "
            + ", ".join(missing)
            + f". Set the environment variable(s), or add them to {CONFIG_PATH}."
        )
    return Config(api_url=api_url, token=token)
