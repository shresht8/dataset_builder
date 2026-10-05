"""Microsoft Entra ID OIDC Authorization Code flow with PKCE (§5, GL-3-11).

Requests openid, profile, email; the groups claim comes from the app
registration. Discovery, token exchange and JWKS come from the discovery
document verbatim (hosts are never rewritten). ID tokens are validated with
joserfc (Authlib's JOSE library): signature via JWKS, iss, aud, exp, nonce.
External SMEs come through as Entra B2B guests on the same flow.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from groundline_api.config import settings

ENTRA_DISCOVERY = "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"


class OIDCError(Exception):
    """Sign-in failed; the message is safe to show as a 401 detail."""


def _http() -> httpx.Client:
    # One seam for tests: they swap this for a client on an in-process fake IdP.
    return httpx.Client(timeout=10)


def discovery_url() -> str:
    return settings.oidc_discovery_url or ENTRA_DISCOVERY.format(tenant=settings.entra_tenant_id)


def _get_json(url: str) -> dict:
    try:
        with _http() as http:
            resp = http.get(url)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OIDCError(f"identity provider unreachable: {exc}") from exc


def discovery() -> dict:
    return _get_json(discovery_url())


def build_authorization_url(state: str, nonce: str, code_verifier: str) -> str:
    params = {
        "client_id": settings.entra_client_id,
        "response_type": "code",
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": " ".join(s.strip() for s in settings.oidc_scopes.split(",") if s.strip()),
        "state": state,
        "nonce": nonce,
        "code_challenge": create_s256_code_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    return f"{discovery()['authorization_endpoint']}?{urlencode(params)}"


def exchange_code(code: str, code_verifier: str) -> tuple[dict, str]:
    """Redeem the code; returns (discovery document, raw ID token)."""
    disc = discovery()
    try:
        with _http() as http:
            resp = http.post(
                disc["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.oidc_redirect_uri,
                    "client_id": settings.entra_client_id,
                    "client_secret": settings.entra_client_secret,
                    "code_verifier": code_verifier,
                },
            )
            resp.raise_for_status()
            id_token = resp.json()["id_token"]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise OIDCError(f"code exchange failed: {exc}") from exc
    return disc, id_token


def validate_id_token(disc: dict, id_token: str, nonce: str) -> dict:
    """Verify signature (JWKS), iss, aud, exp and nonce; return the claims."""
    key_set = KeySet.import_key_set(_get_json(disc["jwks_uri"]))
    try:
        token = jwt.decode(id_token, key_set, algorithms=["RS256"])
        JWTClaimsRegistry(
            iss={"essential": True, "value": disc["issuer"]},
            aud={"essential": True, "value": settings.entra_client_id},
            exp={"essential": True},
            nonce={"essential": True, "value": nonce},
        ).validate(token.claims)
    except JoseError as exc:
        raise OIDCError(f"invalid ID token: {exc}") from exc
    return token.claims
