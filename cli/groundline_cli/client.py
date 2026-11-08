"""Authenticated HTTP client for the Groundline API (§8).

Sends the PAT as a bearer token and wraps the endpoints the CLI needs: list
and create datasets, resolve a path to a dataset, get/put a schema, list rows,
sync rows from a file, fetch a version's manifest/sidecar/rows, fetch a diff,
and (Phase 4) collections, folders, the schema store, moves and bundles. Every non-2xx response is turned into an
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

    def list_datasets(self, collection_id: str | None = None) -> list[dict]:
        params = {"collection_id": collection_id} if collection_id else None
        return self._request("GET", "/datasets", params=params).json()

    def resolve(self, path: str) -> dict:
        """The dataset at `path` (`collection/folder/…/name`, or a bare unfiled name)."""
        return self._request("GET", "/datasets/resolve", params={"path": path}).json()

    def resolve_dataset_id(self, path: str) -> str:
        return self.resolve(path)["id"]

    def get_dataset(self, dataset_id: str) -> dict:
        return self._request("GET", f"/datasets/{dataset_id}").json()

    def get_schema(self, dataset_id: str) -> dict:
        return self._request("GET", f"/datasets/{dataset_id}/schema").json()

    def create_dataset(
        self,
        name: str,
        description: str | None = None,
        *,
        collection_id: str | None = None,
        folder_id: str | None = None,
        store_schema_id: str | None = None,
        columns: list[dict] | None = None,
    ) -> dict:
        payload: dict = {"name": name}
        if description is not None:
            payload["description"] = description
        for field, value in (
            ("collection_id", collection_id),
            ("folder_id", folder_id),
            ("store_schema_id", store_schema_id),
            ("columns", columns),
        ):
            if value is not None:
                payload[field] = value
        return self._request("POST", "/datasets", json=payload).json()

    def move_dataset(self, dataset_id: str, collection_id: str | None, folder_id: str | None) -> dict:
        return self._request(
            "POST",
            f"/datasets/{dataset_id}/move",
            json={"collection_id": collection_id, "folder_id": folder_id},
        ).json()

    def apply_schema(self, dataset_id: str, store_schema_id: str) -> dict:
        return self._request(
            "POST", f"/datasets/{dataset_id}/schema/apply", json={"store_schema_id": store_schema_id}
        ).json()

    # --- Collections, folders, store (Phase 4) ---------------------------------

    def list_collections(self) -> list[dict]:
        return self._request("GET", "/collections").json()

    def find_collection(self, name: str) -> dict:
        for collection in self.list_collections():
            if collection["name"] == name:
                return collection
        raise ApiError(f"unknown collection: '{name}'")

    def get_collection(self, collection_id: str) -> dict:
        return self._request("GET", f"/collections/{collection_id}").json()

    def create_collection(self, name: str, description: str | None = None) -> dict:
        payload = {"name": name}
        if description is not None:
            payload["description"] = description
        return self._request("POST", "/collections", json=payload).json()

    def delete_collection(self, collection_id: str) -> None:
        self._request("DELETE", f"/collections/{collection_id}")

    def create_folder(self, collection_id: str, name: str, parent_id: str | None) -> dict:
        return self._request(
            "POST", f"/collections/{collection_id}/folders", json={"name": name, "parent_id": parent_id}
        ).json()

    def delete_folder(self, folder_id: str) -> None:
        self._request("DELETE", f"/folders/{folder_id}")

    def list_store(self, collection_id: str) -> list[dict]:
        return self._request("GET", f"/collections/{collection_id}/schemas").json()

    def find_store_entry(self, collection_id: str, name: str) -> dict:
        for entry in self.list_store(collection_id):
            if entry["name"] == name:
                return entry
        raise ApiError(f"unknown store schema: '{name}'")

    def create_store_entry(self, collection_id: str, name: str, columns: list[dict]) -> dict:
        return self._request(
            "POST", f"/collections/{collection_id}/schemas", json={"name": name, "columns": columns}
        ).json()

    def update_store_entry(self, collection_id: str, entry_id: str, columns: list[dict]) -> dict:
        return self._request(
            "PUT", f"/collections/{collection_id}/schemas/{entry_id}", json={"columns": columns}
        ).json()

    def duplicate_store_entry(self, collection_id: str, entry_id: str, name: str) -> dict:
        return self._request(
            "POST", f"/collections/{collection_id}/schemas/{entry_id}/duplicate", json={"name": name}
        ).json()

    def delete_store_entry(self, collection_id: str, entry_id: str) -> None:
        self._request("DELETE", f"/collections/{collection_id}/schemas/{entry_id}")

    def export_collection(self, collection_id: str, source: str = "draft") -> bytes:
        return self._request(
            "GET", f"/collections/{collection_id}/export", params={"source": source}
        ).content

    def import_collection(self, content: bytes, *, preview: bool = False) -> dict:
        path = "/collections/import/preview" if preview else "/collections/import"
        return self._request(
            "POST", path, files={"file": ("bundle.zip", content, "application/zip")}
        ).json()

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
