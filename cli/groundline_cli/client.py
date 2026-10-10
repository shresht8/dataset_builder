"""Authenticated HTTP client for the Groundline API (§8).

Sends the PAT as a bearer token and wraps the endpoints the CLI needs: list
and create datasets, resolve a name to an id, get/put a schema, list rows,
sync rows from a file, fetch a version's manifest/sidecar/rows, and fetch a
diff. Every non-2xx response is turned into an
`ApiError` with a clear, one-line message; callers print it and exit non-zero.
"""

from __future__ import annotations

import json
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
        detail = body["detail"]
        if isinstance(detail, list):  # request validation errors: one line each
            return "; ".join(
                ".".join(str(part) for part in error.get("loc", [])[1:]) + f": {error.get('msg')}"
                for error in detail
                if isinstance(error, dict)
            )
        return str(detail)
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

    def create_dataset(self, name: str, description: str | None = None) -> dict:
        payload = {"name": name}
        if description is not None:
            payload["description"] = description
        return self._request("POST", "/datasets", json=payload).json()

    def put_schema(self, dataset_id: str, columns: list[dict]) -> dict:
        return self._request(
            "PUT", f"/datasets/{dataset_id}/schema", json={"columns": columns}
        ).json()

    def list_rows(self, dataset_id: str) -> list[dict]:
        return self._request("GET", f"/datasets/{dataset_id}/rows").json()

    def delete_rows(self, dataset_id: str, keys: list[str]) -> dict:
        """Soft-delete rows by key value, all or nothing (GL-3.5-18)."""
        return self._request(
            "POST", f"/datasets/{dataset_id}/rows/delete", json={"keys": keys}
        ).json()

    def delete_dataset(self, dataset_id: str) -> None:
        self._request("DELETE", f"/datasets/{dataset_id}")

    def sync(
        self,
        dataset_id: str,
        filename: str,
        content: bytes,
        *,
        records_key: str | None = None,
        revs: dict | None = None,
        bases: dict | None = None,
        force: bool = False,
        dry_run: bool = False,
        ignore_unknown: bool = False,
    ) -> dict:
        """POST a file to the sync API (GL-3.5-15). 200 for applied, dry-run and refused."""
        form = {
            "force": str(force).lower(),
            "dry_run": str(dry_run).lower(),
            "ignore_unknown": str(ignore_unknown).lower(),
        }
        if records_key is not None:
            form["records_key"] = records_key
        if revs:
            form["revs"] = json.dumps(revs)
        if bases:
            form["bases"] = json.dumps(bases)
        return self._request(
            "POST",
            f"/datasets/{dataset_id}/sync",
            files={"file": (filename, content)},
            data=form,
        ).json()

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
