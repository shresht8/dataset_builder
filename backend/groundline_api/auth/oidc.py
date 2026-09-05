"""Microsoft Entra ID OIDC Authorization Code flow with PKCE (§5).

Requests openid, profile, email, and the groups claim. Backed by Authlib.
External SMEs come through as Entra B2B guests on the same flow.
"""

from __future__ import annotations

# TODO: build_authorization_url(pkce), exchange_code(code, verifier) -> claims
