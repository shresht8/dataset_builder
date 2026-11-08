"""Shared fixtures: TestClient, one user per role, and a login helper (GL-1-9)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from groundline_api.db import SessionLocal
from groundline_api.main import app
from groundline_api.models.user import Role, User


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def role_users():
    """One active user per role, created directly in the DB for the whole run."""
    tag = uuid.uuid4().hex[:8]
    emails = {role.value: f"gl-test-{role.value}-{tag}@example.com" for role in Role}
    db = SessionLocal()
    try:
        for role in Role:
            db.add(
                User(
                    email=emails[role.value],
                    display_name=f"Test {role.value}",
                    role=role,
                )
            )
        db.commit()
    finally:
        db.close()

    yield emails

    db = SessionLocal()
    try:
        for user in db.query(User).filter(User.email.in_(emails.values())):
            db.delete(user)
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def cleanup():
    """Phase 4: collect collection and dataset ids; delete them (datasets first) after the test."""
    from groundline_api.models.collection import Collection
    from groundline_api.models.dataset import Dataset
    from sqlalchemy import select

    ids: dict[str, list] = {"collections": [], "datasets": []}
    yield ids
    db = SessionLocal()
    try:
        for cid in ids["collections"]:
            for dataset in db.scalars(select(Dataset).where(Dataset.collection_id == uuid.UUID(str(cid)))):
                db.delete(dataset)
        for did in ids["datasets"]:
            dataset = db.get(Dataset, uuid.UUID(str(did)))
            if dataset is not None:
                db.delete(dataset)
        db.flush()
        for cid in ids["collections"]:
            collection = db.get(Collection, uuid.UUID(str(cid)))
            if collection is not None:
                db.delete(collection)
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def as_role(client, role_users):
    """Log the shared client in as the given role; returns the user payload."""

    def _login(role: str) -> dict:
        resp = client.post("/v1/auth/login", json={"email": role_users[role]})
        assert resp.status_code == 200, resp.text
        return resp.json()

    return _login
