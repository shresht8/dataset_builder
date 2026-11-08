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

        def with_resolve(request: httpx.Request) -> httpx.Response:
            # Phase 4: the CLI looks datasets up with GET /v1/datasets/resolve.
            # Handlers written before that mock only GET /v1/datasets, so
            # answer resolve from that list (a bare name = an unfiled dataset).
            response = handler(request)
            if request.url.path != "/v1/datasets/resolve" or response.status_code != 404:
                return response
            listing = handler(httpx.Request("GET", "http://api.test/v1/datasets"))
            path = request.url.params.get("path")
            if listing.status_code == 200:
                for dataset in listing.json():
                    if dataset.get("path", dataset["name"]) == path:
                        return httpx.Response(200, json={"path": dataset["name"], **dataset})
            return httpx.Response(404, json={"detail": f"no dataset '{path}' in unfiled datasets"})

        def patched_init(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(with_resolve)
            orig_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)

    return _install
