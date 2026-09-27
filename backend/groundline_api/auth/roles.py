"""Roles and Entra group -> role mapping (§5).

Four roles: admin, editor, annotator, viewer. The annotator boundary (no schema
changes, no version cutting) is the important one.
"""

from __future__ import annotations

import json
from enum import Enum

from groundline_api.config import settings
from groundline_api.models.user import Role as UserRole


class Role(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    ANNOTATOR = "annotator"
    VIEWER = "viewer"


_RANK = {UserRole.VIEWER: 0, UserRole.ANNOTATOR: 1, UserRole.EDITOR: 2, UserRole.ADMIN: 3}


def role_for_groups(group_claims: list[str]) -> UserRole:
    """Map Entra group object IDs to a role via ROLE_MAPPING.

    The highest-privilege matching group wins; with no match the mapping's
    `default` applies (viewer if the mapping has none).
    """
    mapping = json.loads(settings.role_mapping)
    matched = [UserRole(mapping[group]) for group in group_claims if group in mapping]
    if matched:
        return max(matched, key=_RANK.__getitem__)
    return UserRole(mapping.get("default", UserRole.VIEWER.value))
