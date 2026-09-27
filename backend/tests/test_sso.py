"""GL-3-11: Entra OIDC sign-in, verified against an in-process fake IdP.

The fake IdP signs ID tokens with an RSA key generated here and serves
discovery, JWKS and the token endpoint through httpx.MockTransport, so no
network is used. Group claims are GUIDs, as Entra emits them.
"""

from __future__ import annotations

import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from groundline_api.auth import oidc
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.user import User
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

ISSUER = "https://fake-idp.test/tenant-1/v2.0"
CLIENT_ID = "gl-test-client"
ADMIN_GROUP = "a1a1a1a1-0000-4000-8000-000000000001"
EDITOR_GROUP = "e2e2e2e2-0000-4000-8000-000000000002"
ANNOTATOR_GROUP = "a3a3a3a3-0000-4000-8000-000000000003"
UNMAPPED_GROUP = "99999999-0000-4000-8000-000000000009"


class FakeIdP:
    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
        self.sign_key = self.key
        self.next_claims: dict = {}
        self.token_form: dict = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": "https://fake-idp.test/authorize",
                "token_endpoint": "https://fake-idp.test/token",
                "jwks_uri": "https://fake-idp.test/jwks",
            })
        if path == "/jwks":
            return httpx.Response(200, json=KeySet([self.key]).as_dict(private=False))
        if path == "/token":
            self.token_form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            token = jwt.encode({"alg": "RS256", "kid": "k1"}, self.next_claims, self.sign_key)
            return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture()
def idp(monkeypatch):
    fake = FakeIdP()
    monkeypatch.setattr(settings, "auth_sso_enabled", True)
    monkeypatch.setattr(settings, "oidc_discovery_url", "https://fake-idp.test/.well-known/openid-configuration")
    monkeypatch.setattr(settings, "entra_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "entra_client_secret", "test-secret")
    monkeypatch.setattr(settings, "oidc_redirect_uri", "http://testserver/v1/auth/sso/callback")
    monkeypatch.setattr(settings, "role_mapping", json.dumps({
        ADMIN_GROUP: "admin", EDITOR_GROUP: "editor", ANNOTATOR_GROUP: "annotator", "default": "viewer",
    }))
    monkeypatch.setattr(oidc, "_http", lambda: httpx.Client(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture()
def identity():
    """A fresh Entra identity per test; any user it creates is removed after."""
    tag = uuid.uuid4().hex[:8]
    ident = {"oid": str(uuid.uuid4()), "email": f"sso-test-{tag}@example.com"}
    yield ident
    db = SessionLocal()
    try:
        for user in db.query(User).filter(
            (User.entra_oid == ident["oid"]) | (User.email == ident["email"])
        ):
            db.delete(user)
        db.commit()
    finally:
        db.close()


def _claims(identity: dict, nonce: str, groups: list[str]) -> dict:
    now = int(time.time())
    return {
        "iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300, "nonce": nonce,
        "oid": identity["oid"], "tid": "tenant-1", "email": identity["email"],
        "preferred_username": identity["email"], "name": "SSO Test", "groups": groups,
    }


def sign_in(client, idp, identity, groups=(ADMIN_GROUP,), tweak=None):
    """Run the flow: /sso/login -> (fake IdP) -> /sso/callback. Returns the callback response."""
    login = client.get("/v1/auth/sso/login", follow_redirects=False)
    assert login.status_code == 302, login.text
    query = parse_qs(urlparse(login.headers["location"]).query)
    claims = _claims(identity, query["nonce"][0], list(groups))
    if tweak:
        tweak(claims)
    idp.next_claims = claims
    return client.get(
        f"/v1/auth/sso/callback?code=code-1&state={query['state'][0]}", follow_redirects=False
    ), query


def _user(identity) -> User | None:
    db = SessionLocal()
    try:
        return db.query(User).filter(
            (User.entra_oid == identity["oid"]) | (User.email == identity["email"])
        ).one_or_none()
    finally:
        db.close()


# --- happy paths -----------------------------------------------------------


def test_valid_login_creates_session_and_jit_user(client, idp, identity):
    resp, _ = sign_in(client, idp, identity, groups=[ADMIN_GROUP])
    assert resp.status_code == 302 and resp.headers["location"] == "/"
    me = client.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == identity["email"] and me.json()["role"] == "admin"
    assert _user(identity).entra_oid == identity["oid"]


def test_authorize_redirect_uses_pkce_and_token_call_sends_verifier(client, idp, identity):
    resp, query = sign_in(client, idp, identity)
    assert resp.status_code == 302
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == [CLIENT_ID]
    assert create_s256_code_challenge(idp.token_form["code_verifier"]) == query["code_challenge"][0]


def test_multi_group_user_gets_highest_role(client, idp, identity):
    sign_in(client, idp, identity, groups=[ANNOTATOR_GROUP, EDITOR_GROUP, UNMAPPED_GROUP])
    assert client.get("/v1/auth/me").json()["role"] == "editor"


def test_unmapped_user_gets_viewer(client, idp, identity):
    sign_in(client, idp, identity, groups=[UNMAPPED_GROUP])
    assert client.get("/v1/auth/me").json()["role"] == "viewer"


def test_existing_local_user_is_linked_by_email_not_duplicated(client, idp, identity):
    db = SessionLocal()
    try:
        local = User(email=identity["email"], display_name="Local", role="viewer")
        db.add(local)
        db.commit()
        local_id = local.id
    finally:
        db.close()
    sign_in(client, idp, identity, groups=[EDITOR_GROUP])
    linked = _user(identity)  # one_or_none(): would raise on a duplicate
    assert linked.id == local_id and linked.entra_oid == identity["oid"]
    assert linked.role.value == "editor"


def test_role_resyncs_when_groups_change(client, idp, identity):
    sign_in(client, idp, identity, groups=[EDITOR_GROUP])
    assert client.get("/v1/auth/me").json()["role"] == "editor"
    sign_in(client, idp, identity, groups=[ANNOTATOR_GROUP])
    assert client.get("/v1/auth/me").json()["role"] == "annotator"


def test_preferred_username_used_when_email_missing(client, idp, identity):
    sign_in(client, idp, identity, tweak=lambda c: c.pop("email"))
    assert client.get("/v1/auth/me").json()["email"] == identity["email"]


def test_groups_overage_falls_back_to_default_role(client, idp, identity, caplog):
    def overage(c):
        c.pop("groups")
        c["_claim_names"] = {"groups": "src1"}

    sign_in(client, idp, identity, tweak=overage)
    assert client.get("/v1/auth/me").json()["role"] == "viewer"
    assert "groups overage" in caplog.text


# --- rejections: 401, no session, no user ----------------------------------


def _expired(c):
    c["exp"] = int(time.time()) - 60


@pytest.mark.parametrize(
    "tweak",
    [
        pytest.param(lambda c: c.update(iss="https://evil.test/v2.0"), id="wrong-iss"),
        pytest.param(lambda c: c.update(aud="someone-else"), id="wrong-aud"),
        pytest.param(_expired, id="expired"),
        pytest.param(lambda c: c.update(nonce="not-the-nonce"), id="nonce-mismatch"),
        pytest.param(lambda c: (c.pop("email"), c.pop("preferred_username")), id="no-email-or-username"),
        pytest.param(lambda c: c.pop("oid"), id="no-oid"),
    ],
)
def test_invalid_id_token_is_rejected(client, idp, identity, tweak):
    resp, _ = sign_in(client, idp, identity, tweak=tweak)
    assert resp.status_code == 401
    assert "groundline_session" not in resp.cookies
    assert client.get("/v1/auth/me").status_code == 401
    assert _user(identity) is None


def test_bad_signature_is_rejected(client, idp, identity):
    idp.sign_key = RSAKey.generate_key(2048, parameters={"kid": "k1"})  # not in the JWKS
    resp, _ = sign_in(client, idp, identity)
    assert resp.status_code == 401
    assert _user(identity) is None


def test_state_mismatch_is_rejected(client, idp, identity):
    client.get("/v1/auth/sso/login", follow_redirects=False)
    resp = client.get("/v1/auth/sso/callback?code=c&state=forged", follow_redirects=False)
    assert resp.status_code == 401
    assert _user(identity) is None


def test_missing_state_or_flow_cookie_is_rejected(client, idp):
    client.get("/v1/auth/sso/login", follow_redirects=False)
    assert client.get("/v1/auth/sso/callback?code=c", follow_redirects=False).status_code == 401
    client.cookies.clear()
    resp = client.get("/v1/auth/sso/callback?code=c&state=anything", follow_redirects=False)
    assert resp.status_code == 401


def test_inactive_user_is_rejected(client, idp, identity):
    db = SessionLocal()
    try:
        db.add(User(email=identity["email"], display_name="Gone", role="viewer", active=False))
        db.commit()
    finally:
        db.close()
    resp, _ = sign_in(client, idp, identity)
    assert resp.status_code == 401
    assert _user(identity).entra_oid is None  # not linked either


# --- flag + coexistence ----------------------------------------------------


def test_sso_disabled_hides_routes_and_reports_config(client):
    assert settings.auth_sso_enabled is False
    assert client.get("/v1/auth/sso/login", follow_redirects=False).status_code == 404
    assert client.get("/v1/auth/sso/callback?code=c&state=s", follow_redirects=False).status_code == 404
    assert client.get("/v1/auth/config").json() == {"dev_login": True, "sso": False}


def test_config_reports_sso_when_enabled(client, idp):
    assert client.get("/v1/auth/config").json() == {"dev_login": True, "sso": True}


def test_pat_and_dev_login_still_work_with_sso_enabled(client, idp, as_role):
    as_role("editor")  # dev login still works
    raw = client.post("/v1/auth/tokens", json={"name": "sso-coexist"}).json()["token"]
    client.cookies.clear()
    resp = client.get("/v1/datasets", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200
