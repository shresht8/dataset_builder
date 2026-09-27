"""Shared test fixtures: fake config and a mockable httpx transport."""

from __future__ import annotations

import httpx
import pytest
from groundline_cli.config import Config


@pytest.fixture
def config() -> Config:
    return Config(api_url="http://api.test", token="tok_abc")


@pytest.fixture
def install_transport(monkeypatch):
    """install_transport(handler) makes every httpx.Client() route through handler."""

    def _install(handler) -> None:
        orig_init = httpx.Client.__init__

        def patched_init(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            orig_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)

    return _install
