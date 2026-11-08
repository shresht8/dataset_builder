"""Object storage for version snapshots (§6).

Writes datasets/{dataset_id}/v{n}/{manifest.json,schema.json,rows.jsonl} to
an S3-compatible bucket and reads them back for export/pull. Versions cut
before Phase 4 live under datasets/{name}/v{n}/; every read takes the location
from the version's stored `snapshot_uri` (C2), so both layouts stay readable.
"""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from groundline_api.config import settings


class SnapshotExistsError(RuntimeError):
    """An object already exists at a version's key (C2 immutability guard)."""


class ObjectNotFoundError(RuntimeError):
    """No object exists at the given key."""


def parse_snapshot_uri(snapshot_uri: str) -> tuple[str, str]:
    """`s3://bucket/prefix/` -> (bucket, "prefix/")."""
    if not snapshot_uri.startswith("s3://"):
        raise ValueError(f"not an s3:// snapshot uri: {snapshot_uri!r}")
    bucket, _, prefix = snapshot_uri.removeprefix("s3://").partition("/")
    return bucket, prefix if prefix.endswith("/") else prefix + "/"


def get_snapshot_file(snapshot_uri: str, name: str) -> bytes:
    """Read one file (rows.jsonl, manifest.json, schema.json) of a stored version."""
    bucket, prefix = parse_snapshot_uri(snapshot_uri)
    return get_object(bucket, prefix + name)


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )


def put_snapshot(bucket: str, prefix: str, files: dict[str, bytes]) -> None:
    """Write `files` (key suffix -> bytes) under `prefix` in `bucket`.

    Each object is written with `If-None-Match: *`. The dataset row lock
    (C2) is what actually prevents two cuts from computing the same version
    number; this is a backstop so a losing writer can never overwrite an
    already-written object instead of failing loudly.
    """
    client = _client()
    for name, body in files.items():
        key = f"{prefix}{name}"
        try:
            client.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch="*")
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 412:
                raise SnapshotExistsError(key) from exc
            raise


def get_object(bucket: str, key: str) -> bytes:
    """Read one stored object's bytes (GL-3-3: reads pull from storage, not DB)."""
    client = _client()
    try:
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            raise ObjectNotFoundError(key) from exc
        raise
