"""Dataset paths (Phase 4, C1).

A dataset is addressed by its path: `collection/[folder/…]/dataset`, or the
bare name for an unfiled dataset. Every segment matches the dataset name
pattern, which forbids `/`, `@` and `.`, so a path never needs escaping.
"""

from __future__ import annotations

import re
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.models.collection import Collection, CollectionSchema, Folder
from groundline_api.models.dataset import Dataset
from groundline_api.schemas.dataset import DATASET_NAME_PATTERN

NAME_RE = re.compile(DATASET_NAME_PATTERN)
UNFILED = "unfiled datasets"


class Paths:
    """Builds paths, caching collections and their folders for one request."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._collections: dict[uuid.UUID, Collection] = {}
        self._folders: dict[uuid.UUID, dict[uuid.UUID, Folder]] = {}
        self._entries: dict[uuid.UUID, str] = {}

    def store_schema_name(self, entry_id: uuid.UUID) -> str:
        if entry_id not in self._entries:
            self._entries[entry_id] = self._db.get(CollectionSchema, entry_id).name
        return self._entries[entry_id]

    def collection_name(self, collection_id: uuid.UUID) -> str:
        if collection_id not in self._collections:
            self._collections[collection_id] = self._db.get(Collection, collection_id)
        return self._collections[collection_id].name

    def _folders_of(self, collection_id: uuid.UUID) -> dict[uuid.UUID, Folder]:
        if collection_id not in self._folders:
            self._folders[collection_id] = {
                f.id: f
                for f in self._db.scalars(select(Folder).where(Folder.collection_id == collection_id))
            }
        return self._folders[collection_id]

    def folder_segments(self, collection_id: uuid.UUID, folder_id: uuid.UUID | None) -> list[str]:
        folders = self._folders_of(collection_id)
        segments: list[str] = []
        while folder_id is not None:
            folder = folders[folder_id]
            segments.append(folder.name)
            folder_id = folder.parent_id
        return segments[::-1]

    def parent_path(self, collection_id: uuid.UUID | None, folder_id: uuid.UUID | None) -> str:
        """`collection/folder/…`, or 'unfiled datasets'."""
        if collection_id is None:
            return UNFILED
        return "/".join(
            [self.collection_name(collection_id), *self.folder_segments(collection_id, folder_id)]
        )

    def dataset_path(self, dataset: Dataset) -> str:
        if dataset.collection_id is None:
            return dataset.name
        return f"{self.parent_path(dataset.collection_id, dataset.folder_id)}/{dataset.name}"


def dataset_path(db: Session, dataset: Dataset) -> str:
    return Paths(db).dataset_path(dataset)


def split(path: str) -> list[str]:
    """Split a path into segments; 422 naming the first invalid one."""
    segments = path.split("/")
    for segment in segments:
        if not NAME_RE.match(segment):
            raise HTTPException(status_code=422, detail=f"invalid path segment '{segment}'")
    return segments


def find_folder(
    db: Session, collection: Collection, segments: list[str]
) -> uuid.UUID | None:
    """The folder at `segments` under the collection root (None = root); 404 if missing."""
    parent_id: uuid.UUID | None = None
    walked = [collection.name]
    for name in segments:
        folder = db.scalar(
            select(Folder).where(
                Folder.collection_id == collection.id,
                Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id,
                Folder.name == name,
            )
        )
        if folder is None:
            raise HTTPException(
                status_code=404, detail=f"no folder '{name}' in {'/'.join(walked)}"
            )
        parent_id = folder.id
        walked.append(name)
    return parent_id


def resolve_path(db: Session, path: str) -> Dataset:
    """The dataset at `path`; 422 on a bad segment, 404 naming the first that didn't resolve."""
    segments = split(path)
    if len(segments) == 1:
        dataset = db.scalar(
            select(Dataset).where(Dataset.collection_id.is_(None), Dataset.name == segments[0])
        )
        if dataset is None:
            raise HTTPException(status_code=404, detail=f"no dataset '{segments[0]}' in {UNFILED}")
        return dataset

    collection = db.scalar(select(Collection).where(Collection.name == segments[0]))
    if collection is None:
        raise HTTPException(status_code=404, detail=f"no collection '{segments[0]}'")
    folder_id = find_folder(db, collection, segments[1:-1])
    dataset = db.scalar(
        select(Dataset).where(
            Dataset.collection_id == collection.id,
            Dataset.folder_id.is_(None) if folder_id is None else Dataset.folder_id == folder_id,
            Dataset.name == segments[-1],
        )
    )
    if dataset is None:
        raise HTTPException(
            status_code=404,
            detail=f"no dataset '{segments[-1]}' in {'/'.join(segments[:-1])}",
        )
    return dataset


def dataset_name_taken(
    db: Session,
    name: str,
    collection_id: uuid.UUID | None,
    folder_id: uuid.UUID | None,
    exclude: uuid.UUID | None = None,
) -> bool:
    query = select(Dataset.id).where(Dataset.name == name)
    if collection_id is None:
        query = query.where(Dataset.collection_id.is_(None))
    else:
        query = query.where(
            Dataset.collection_id == collection_id,
            Dataset.folder_id.is_(None) if folder_id is None else Dataset.folder_id == folder_id,
        )
    if exclude is not None:
        query = query.where(Dataset.id != exclude)
    return db.scalar(query) is not None
