"""Text helpers: URL slugs and length-bounded display strings."""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]+")


def slugify(title: str) -> str:
    """Return a lowercase, hyphen-separated slug of ``title``.

    Non-alphanumeric runs collapse to a single hyphen; leading and trailing
    hyphens are stripped.
    """
    slug = _NON_ALNUM.sub("-", title).strip("-")
    return slug


def truncate(text: str, limit: int) -> str:
    """Return ``text`` cut to ``limit`` chars, appending an ellipsis if cut."""
    if limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit!r}")
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
