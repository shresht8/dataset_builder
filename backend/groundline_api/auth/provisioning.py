"""JIT provisioning on first login (§5).

A user who authenticates with a mapped group claim gets an account created on
first sign-in. SCIM is deferred until a customer needs deprovisioning.
"""

from __future__ import annotations

# TODO: provision_user(claims) -> User (create-or-update role from groups)
