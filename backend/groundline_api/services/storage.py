"""Object storage for version snapshots (§6).

Writes datasets/{name}/v{n}/{manifest.json,schema.json,rows.jsonl} to an
S3-compatible bucket and reads them back for export/pull.
"""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from groundline_api.config import settings


class SnapshotExistsError(RuntimeError):
    """An object already exists at a version's key (C2 immutability guard)."""


class ObjectNotFoundError(RuntimeError):
    """No object exists at the given key."""


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
