"""GL-1-8: local user management + first-admin bootstrap (§5 interim)."""

from __future__ import annotations

import uuid

from groundline_api.bootstrap import ensure_admin
from groundline_api.db import SessionLocal
from groundline_api.models.user import User


def _delete_users_by_email(emails: list[str]) -> None:
    db = SessionLocal()
    try:
        for user in db.query(User).filter(User.email.in_(emails)):
            db.delete(user)
        db.commit()
    finally:
        db.close()


def test_create_list_patch_user(client, as_role):
    as_role("admin")
    email = f"gl18-{uuid.uuid4().hex[:8]}@example.com"
    try:
        resp = client.post(
            "/v1/users", json={"email": email, "role": "annotator"}
        )
        assert resp.status_code == 201, resp.text
        user = resp.json()
        assert user["email"] == email
        assert user["role"] == "annotator"
        assert user["display_name"] == email  # defaults to email
        assert user["active"] is True

        # Shows up in the list.
        resp = client.get("/v1/users")
        assert resp.status_code == 200
        assert email in [u["email"] for u in resp.json()]

        # Re-role + rename + deactivate.
        resp = client.patch(
            f"/v1/users/{user['id']}",
            json={"role": "editor", "display_name": "GL Eighteen", "active": False},
        )
        assert resp.status_code == 200, resp.text
        patched = resp.json()
        assert patched["role"] == "editor"
        assert patched["display_name"] == "GL Eighteen"
        assert patched["active"] is False
    finally:
        _delete_users_by_email([email])


def test_duplicate_email_409(client, as_role):
    as_role("admin")
    email = f"gl18-dup-{uuid.uuid4().hex[:8]}@example.com"
    try:
        assert client.post("/v1/users", json={"email": email}).status_code == 201
        resp = client.post("/v1/users", json={"email": email})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "email already exists"
    finally:
        _delete_users_by_email([email])


def test_invalid_role_422(client, as_role):
    as_role("admin")
    resp = client.post(
        "/v1/users",
        json={"email": f"gl18-{uuid.uuid4().hex[:8]}@example.com", "role": "superuser"},
    )
    assert resp.status_code == 422


def test_patch_missing_user_404(client, as_role):
    as_role("admin")
    resp = client.patch(f"/v1/users/{uuid.uuid4()}", json={"role": "viewer"})
    assert resp.status_code == 404


def test_users_endpoints_admin_only(client, as_role):
    for role in ("editor", "annotator", "viewer"):
        as_role(role)
        assert client.get("/v1/users").status_code == 403
        resp = client.post("/v1/users", json={"email": "x@example.com"})
        assert resp.status_code == 403
        assert client.patch(f"/v1/users/{uuid.uuid4()}", json={}).status_code == 403


def test_bootstrap_admin_idempotent():
    email = f"gl18-boot-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        first = ensure_admin(db, email)
        assert first.role.value == "admin"
        again = ensure_admin(db, email, display_name="Boot Admin")
        assert again.id == first.id  # promoted in place, not duplicated
        assert again.display_name == "Boot Admin"
        assert again.active is True
    finally:
        db.close()
        _delete_users_by_email([email])
