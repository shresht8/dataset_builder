"""Authenticated HTTP client for the Groundline API (§8).

Sends the PAT as a bearer token and wraps the endpoints the CLI needs:
list datasets, fetch a version in a given format, fetch a diff.
"""

from __future__ import annotations

# TODO: class ApiClient: list_datasets, get_version, get_diff
