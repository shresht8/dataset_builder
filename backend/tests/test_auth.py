"""GL-1-9: dev login, sessions, and RBAC enforcement per the §5 capability table."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.main import create_app
from groundline_api.models.dataset import Dataset
from groundline_api.models.user import User


def test_login_me_logout_flow(client, role_users, as_role):
    # Not authenticated yet.
    assert client.get("/v1/auth/me").status_code == 401

    user = as_role("annotator")
    assert user["email"] == role_users["annotator"]
    assert user["role"] == "annotator"

    resp = client.get("/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "annotator"

    assert client.post("/v1/auth/logout").status_code == 204
    assert client.get("/v1/auth/me").status_code == 401


def test_login_unknown_email_401(client):
    resp = client.post(
        "/v1/auth/login", json={"email": "nobody-here@example.com"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "unknown or inactive user"


def test_login_inactive_user_401(client):
    email = f"gl19-inactive-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        db.add(User(email=email, display_name=email, active=False))
        db.commit()
        resp = client.post("/v1/auth/login", json={"email": email})
        assert resp.status_code == 401
    finally:
        for user in db.query(User).filter(User.email == email):
            db.delete(user)
        db.commit()
        db.close()


def test_tampered_session_cookie_401(client, as_role):
    as_role("viewer")
    cookie = client.cookies.get("groundline_session")
    client.cookies.set("groundline_session", cookie[:-4] + "0000")
    assert client.get("/v1/auth/me").status_code == 401


@pytest.fixture()
def rbac_dataset(client, as_role):
    """Editor-created dataset for the capability matrix."""
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl19-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201
    ds_id = resp.json()["id"]
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)
            db.commit()
    finally:
        db.close()


COLUMNS = {"columns": [{"key": "input", "label": "Input", "type": "text"}]}


def test_rbac_capability_matrix(client, as_role, rbac_dataset):
    ds = rbac_dataset

    # Viewer: read-only — can list/read, cannot create datasets/rows or PUT schema.
    as_role("viewer")
    assert client.get("/v1/datasets").status_code == 200
    assert client.get(f"/v1/datasets/{ds}/rows").status_code == 200
    assert client.get(f"/v1/datasets/{ds}/schema").status_code == 200
    assert client.post("/v1/datasets", json={"name": "x"}).status_code == 403
    assert (
        client.post(f"/v1/datasets/{ds}/rows", json={"data": {}}).status_code == 403
    )
    assert client.put(f"/v1/datasets/{ds}/schema", json=COLUMNS).status_code == 403

    # Annotator: row edits allowed; no schema changes, no dataset creation.
    as_role("annotator")
    assert (
        client.post(f"/v1/datasets/{ds}/rows", json={"data": {"input": "hi"}})
        .status_code
        == 201
    )
    assert client.put(f"/v1/datasets/{ds}/schema", json=COLUMNS).status_code == 403
    assert client.post("/v1/datasets", json={"name": "x"}).status_code == 403

    # Editor: schema PUT allowed.
    as_role("editor")
    assert client.put(f"/v1/datasets/{ds}/schema", json=COLUMNS).status_code == 200

    # Unauthenticated: everything (except health/login) is 401.
    client.cookies.clear()
    assert client.get("/v1/datasets").status_code == 401
    assert client.get("/v1/health").status_code == 200


def test_refuses_to_boot_with_dev_login_outside_dev(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "auth_dev_login", True)
    with pytest.raises(RuntimeError, match="AUTH_DEV_LOGIN"):
        create_app()


def test_login_403_when_dev_login_disabled(client, role_users, monkeypatch):
    monkeypatch.setattr(settings, "auth_dev_login", False)
    resp = client.post("/v1/auth/login", json={"email": role_users["viewer"]})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "dev login is disabled"
