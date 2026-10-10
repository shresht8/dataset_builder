"""Authenticated HTTP client for the Groundline API (§8).

Sends the PAT as a bearer token and wraps the endpoints the CLI needs: list
datasets, resolve a name to an id, fetch a version's schema/manifest/sidecar
in a given format, and fetch a diff. Every non-2xx response is turned into an
`ApiError` with a clear, one-line message; callers print it and exit non-zero.
"""

from __future__ import annotations

from typing import Self

import httpx

from groundline_cli.config import Config


class ApiError(Exception):
    """A user-facing CLI error: print the message and exit non-zero."""


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return response.text


class ApiClient:
    def __init__(self, config: Config) -> None:
        self._base_url = config.api_url.rstrip("/") + "/v1"
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise ApiError(f"could not connect to {self._base_url}: {exc}") from exc
        if response.status_code == 401:
            raise ApiError(f"authentication failed: {_detail(response)}")
        if response.status_code == 404:
            raise ApiError(f"not found: {_detail(response)}")
        if response.status_code >= 400:
            raise ApiError(f"API error {response.status_code}: {_detail(response)}")
        return response

    def list_datasets(self) -> list[dict]:
        return self._request("GET", "/datasets").json()

    def resolve_dataset_id(self, name: str) -> str:
        for dataset in self.list_datasets():
            if dataset["name"] == name:
                return dataset["id"]
        raise ApiError(f"unknown dataset: '{name}'")

    def get_schema(self, dataset_id: str) -> dict:
        return self._request("GET", f"/datasets/{dataset_id}/schema").json()

    def get_versions(self, dataset_id: str) -> list[dict]:
        return self._request("GET", f"/datasets/{dataset_id}/versions").json()

    def get_version_bytes(self, dataset_id: str, version: int, fmt: str) -> bytes:
        response = self._request(
            "GET",
            f"/datasets/{dataset_id}/versions/{version}",
            params={"format": fmt},
        )
        return response.content

    def get_manifest(self, dataset_id: str, version: int) -> dict:
        return self._request(
            "GET", f"/datasets/{dataset_id}/versions/{version}/manifest"
        ).json()

    def get_jsonschema(self, dataset_id: str, version: int, shape: str = "flat") -> dict:
        params = {"shape": shape} if shape != "flat" else None
        return self._request(
            "GET", f"/datasets/{dataset_id}/versions/{version}/jsonschema", params=params
        ).json()

    def get_diff(self, dataset_id: str, from_version: int, to_version: int) -> dict:
        return self._request(
            "GET",
            f"/datasets/{dataset_id}/versions/diff",
            params={"from": from_version, "to": to_version},
        ).json()
