"""What an OpenRouter response body holds, and how an error may quote one: no transport here.

The wire modules read bodies through these helpers, so reading a body never
imports the HTTP client. An error quotes at most the start of a body, with the
bearer masked.

Example:
    >>> usage_cost({"usage": {"cost": 0.00002}}), usage_cost({"usage": {"cost": True}})
    (2e-05, None)
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TypeGuard

# How much of a body an error quotes.
_ERROR_EXCERPT_CHARS = 300
_BEARER_HEADER = re.compile(r"Bearer\s+\S+")


def redact(text: str, bearer: str) -> str:
    """``text`` with the bearer and any ``Bearer <token>`` pattern masked.

    Example:
        >>> redact("sk-1 and sk-1", "sk-1")
        '… and …'
    """
    masked = text.replace(bearer, "…") if bearer else text
    return _BEARER_HEADER.sub("Bearer …", masked)


def redacted_excerpt(text: str, bearer: str) -> str:
    """The start of ``text`` an error may quote, with the bearer masked.

    Example:
        >>> redacted_excerpt("refused: sk-1", "sk-1")
        'refused: …'
    """
    return redact(text[:_ERROR_EXCERPT_CHARS], bearer)


def is_json_number(value: object) -> TypeGuard[int | float]:
    """Whether ``value`` is a JSON number; a JSON ``true`` is a bool, never 1.

    Example:
        >>> is_json_number(0.5), is_json_number(True)
        (True, False)
    """
    return isinstance(value, int | float) and not isinstance(value, bool)


def usage_cost(body: Mapping[str, object]) -> float | None:
    """What OpenRouter says an answer cost (``usage.cost``); ``None`` when it does not say."""
    usage = body.get("usage")
    cost = usage.get("cost") if isinstance(usage, Mapping) else None
    return float(cost) if is_json_number(cost) else None


__all__ = ("is_json_number", "redact", "redacted_excerpt", "usage_cost")
