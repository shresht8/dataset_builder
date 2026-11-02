"""Canonical temporal values (GL-3.5-7), shared so the API, sidecar and CLI agree.

`date` is a calendar date with no time and no timezone, stored as the string
`YYYY-MM-DD` (years 0001-9999; it must be a real calendar date). The `datetime`
helpers belong to the deferred GL-3.5-17 and will sit beside these.
"""

from __future__ import annotations

import re
from datetime import date

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_date(text: str) -> date:
    """Parse a canonical `YYYY-MM-DD` date. Raises ValueError otherwise."""
    if not isinstance(text, str) or not _DATE.fullmatch(text):
        raise ValueError(f"{text!r} is not a date in YYYY-MM-DD form")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a real calendar date") from None


def format_date(value: date) -> str:
    """The canonical form of a date: `YYYY-MM-DD`."""
    return value.isoformat()


def is_date(text: object) -> bool:
    try:
        parse_date(text)  # type: ignore[arg-type]
    except ValueError:
        return False
    return True
