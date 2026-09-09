"""Strip secret-bearing inputs out of a config ``ValidationError`` before it is rendered.

Design 2026-09-05-ask-your-docs-llm-connection §E16 (and global constraints
G8 / H4): a credential never reaches YAML, argv, a log line, the UI or a cache
key. pydantic works against that by design — it appends ``input_value=...`` to
every line error, so a bad ``ask_your_docs.llm.auth`` block echoes its raw
mapping (a ``token_url`` query string included) into the ValidationError that
``AppConfig.load`` raises at CLI / MCP-server startup, straight to stderr.

``hide_input_in_errors`` cannot cover that: pydantic reads the flag off the
OUTERMOST validated model, so setting it on the auth sub-model protects direct
construction only and is ignored the moment ``AppConfig`` wraps it. This module
is the loader-side half of the pair — see :meth:`AppConfig.load`.
"""

from __future__ import annotations

from pydantic import ValidationError
from pydantic_core import ErrorDetails, InitErrorDetails

# The one config subtree whose error INPUT can be a credential. The credential
# check itself lives in ``LlmAuthConfig``, so any line error at or under this
# location may carry the raw block; an error ABOVE it means auth already
# validated, hence its token_url is credential-free. Everything outside keeps
# its input_value — CLAUDE.md §Coding Rules: "error messages carry the
# offending value and the expected shape".
_SECRET_BEARING_LOCATION: tuple[str, ...] = ("ask_your_docs", "llm", "auth")
_REDACTED_INPUT = "<redacted>"


def _is_secret_bearing(loc: tuple[int | str, ...]) -> bool:
    """True when ``loc`` is the secret-bearing location or a descendant of it."""
    return tuple(loc[: len(_SECRET_BEARING_LOCATION)]) == _SECRET_BEARING_LOCATION


def _rebuilt_line_error(detail: ErrorDetails) -> InitErrorDetails:
    """Copy one line error, replacing a secret-bearing ``input`` with the placeholder."""
    loc = detail["loc"]
    line: InitErrorDetails = {
        "type": detail["type"],
        "loc": loc,
        "input": _REDACTED_INPUT if _is_secret_bearing(loc) else detail["input"],
    }
    context = detail.get("ctx")
    if context is not None:
        line["ctx"] = context
    return line


def redact_secret_inputs(error: ValidationError) -> ValidationError:
    """Return ``error`` with every secret-bearing line error's input replaced.

    Messages, error types, locations and non-secret inputs are preserved, so an
    unrelated config mistake reported in the same pass still names what it got.
    Returns the argument unchanged when nothing needs redacting — the common
    case, and it keeps the rebuild off every error that cannot leak.

    Usage (``AppConfig.load``)::

        try:
            instance = cls()
        except ValidationError as error:
            raise redact_secret_inputs(error) from None
    """
    details = error.errors()
    if not any(_is_secret_bearing(detail["loc"]) for detail in details):
        return error
    return ValidationError.from_exception_data(
        error.title,
        [_rebuilt_line_error(detail) for detail in details],
    )


__all__ = ("redact_secret_inputs",)
