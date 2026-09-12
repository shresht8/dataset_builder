"""GL-1-10: personal access tokens — issue once, hash-only storage, revoke/expiry."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.models.user import PersonalAccessToken


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_pat_issue_authenticate_and_list_without_secret(client, as_role):
    as_role("annotator")
    resp = client.post("/v1/auth/tokens", json={"name": "laptop"})
    assert resp.status_code == 201, resp.text
    created = resp.json()
    raw = created["token"]
    assert raw.startswith("glpat_")

    # Only the hash is stored, never the raw secret.
    db = SessionLocal()
    try:
        record = db.get(PersonalAccessToken, uuid.UUID(created["id"]))
        assert record.token_hash != raw
        assert raw not in record.token_hash
        # Default TTL is 90 days (§11 Q5).
        days = (record.expires_at - datetime.now(timezone.utc)).days
        assert 89 <= days <= 90
    finally:
        db.close()

    # Listing never returns the secret.
    listed = client.get("/v1/auth/tokens").json()
    assert created["id"] in [t["id"] for t in listed]
    assert all("token" not in t and "token_hash" not in t for t in listed)

    # The PAT authenticates without a session and reflects the issuing user.
    client.cookies.clear()
    resp = client.get("/v1/auth/me", headers=_bearer(raw))
    assert resp.status_code == 200
    assert resp.json()["role"] == "annotator"


def test_pat_inherits_issuer_role(client, as_role):
    # Editor sets up a dataset; annotator issues a PAT.
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl110-{uuid.uuid4().hex[:8]}"})
    ds = resp.json()["id"]
    try:
        as_role("annotator")
        raw = client.post("/v1/auth/tokens", json={"name": "ci"}).json()["token"]
        client.cookies.clear()

        columns = {"columns": [{"key": "k", "label": "K", "type": "text"}]}
        # Annotator PAT: rows yes, schema PUT no.
        resp = client.post(
            f"/v1/datasets/{ds}/rows", json={"data": {}}, headers=_bearer(raw)
        )
        assert resp.status_code == 201
        resp = client.put(
            f"/v1/datasets/{ds}/schema", json=columns, headers=_bearer(raw)
        )
        assert resp.status_code == 403
    finally:
        db = SessionLocal()
        try:
            dataset = db.get(Dataset, uuid.UUID(ds))
            if dataset is not None:
                db.delete(dataset)
                db.commit()
        finally:
            db.close()


def test_revoked_pat_401(client, as_role):
    as_role("viewer")
    created = client.post("/v1/auth/tokens", json={"name": "to-revoke"}).json()
    assert client.delete(f"/v1/auth/tokens/{created['id']}").status_code == 204

    listed = {t["id"]: t for t in client.get("/v1/auth/tokens").json()}
    assert listed[created["id"]]["revoked_at"] is not None

    client.cookies.clear()
    resp = client.get("/v1/auth/me", headers=_bearer(created["token"]))
    assert resp.status_code == 401


def test_expired_pat_401(client, as_role):
    as_role("viewer")
    created = client.post(
        "/v1/auth/tokens", json={"name": "short-lived", "ttl_days": 1}
    ).json()

    # Force expiry in the DB.
    db = SessionLocal()
    try:
        record = db.get(PersonalAccessToken, uuid.UUID(created["id"]))
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    client.cookies.clear()
    resp = client.get("/v1/auth/me", headers=_bearer(created["token"]))
    assert resp.status_code == 401


def test_garbage_bearer_401(client):
    assert client.get("/v1/auth/me", headers=_bearer("glpat_nonsense")).status_code == 401
    assert client.get("/v1/auth/me", headers=_bearer("not-a-pat")).status_code == 401


def test_cannot_revoke_another_users_token(client, as_role):
    as_role("viewer")
    created = client.post("/v1/auth/tokens", json={"name": "mine"}).json()
    as_role("annotator")
    resp = client.delete(f"/v1/auth/tokens/{created['id']}")
    assert resp.status_code == 404


@pytest.fixture(autouse=True)
def _cleanup_tokens(role_users):
    """PATs issued by the shared role users are removed after each test."""
    yield
    db = SessionLocal()
    try:
        from groundline_api.models.user import User

        user_ids = [
            u.id for u in db.query(User).filter(User.email.in_(role_users.values()))
        ]
        db.query(PersonalAccessToken).filter(
            PersonalAccessToken.user_id.in_(user_ids)
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
