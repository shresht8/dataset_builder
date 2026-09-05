"""Roles and Entra group -> role mapping (§5).

Four roles: admin, editor, annotator, viewer. The annotator boundary (no schema
changes, no version cutting) is the important one.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    ANNOTATOR = "annotator"
    VIEWER = "viewer"


def role_for_groups(group_claims: list[str]) -> Role:
    """Map Entra security group names to a role, defaulting to viewer.

    TODO: load the configured mapping (see role_mapping in §5) and resolve the
    highest-privilege matching group.
    """
    raise NotImplementedError
