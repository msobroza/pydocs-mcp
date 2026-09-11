"""Strip secret-bearing inputs out of a config ``ValidationError`` before it is rendered.

Design 2026-09-05-ask-your-docs-llm-connection §E16 (and global constraints
G8 / H4): a credential never reaches YAML, argv, a log line, the UI or a cache
key. pydantic works against that by design — it appends ``input_value=...`` to
every line error, so a bad ``ask_your_docs.llm`` block echoes its raw mapping (a
``token_url`` query string, or a secret pasted at a key the schema does not
define) into the ValidationError that ``AppConfig.load`` raises at CLI /
MCP-server startup, straight to stderr.

``hide_input_in_errors`` cannot cover that: pydantic reads the flag off the
OUTERMOST validated model, so setting it on the auth sub-model protects direct
construction only and is ignored the moment ``AppConfig`` wraps it. This module
is the loader-side half of the pair — see :meth:`AppConfig.load`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import ValidationError
from pydantic_core import ErrorDetails, InitErrorDetails

# The one config subtree whose error INPUT can be a credential. WHY the whole
# block and not just ``…auth`` (widened 2026-09-09): the design forbids secrets
# in YAML, so an operator who pastes one anyway lands on a key the schema does
# not define — ``ask_your_docs.llm.api_key: sk-…`` reports ``extra_forbidden``
# at ``…llm.api_key``, a SIBLING of auth rather than a descendant, and a bare
# token pasted at ``ask_your_docs.llm`` reports ``model_type`` at the block
# itself. Both sit outside an auth-scoped prefix and echoed their input.
# The cost: this block's own fields (base_url, model, token_field,
# renew_on_status, vision.model, and every ``params`` key) lose pydantic's
# ``input_value=`` echo, so their validators name the offending value in the
# MESSAGE instead — messages always survive the rebuild below; only inputs are
# blanked. ``ask_your_docs_params_models`` echoes numbers only and names any other
# type, so a secret pasted under ``params`` stays out of both. Everything outside
# the block keeps its input_value — CLAUDE.md §Coding Rules: "error messages
# carry the offending value and the expected shape".
_SECRET_BEARING_LOCATION: tuple[str, ...] = ("ask_your_docs", "llm")
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


def _messages_only(error: ValidationError) -> ValidationError:
    """One ``value_error`` line carrying every message, and no input at all.

    The last resort when the faithful rebuild below cannot run: ``from_exception_data``
    only accepts pydantic's own error types, so a line error raised with a custom code
    comes back as a plain string it refuses. Falling back to the ORIGINAL error there
    would hand the caller the very inputs this module exists to blank — messages are
    written by our validators and carry no credential, so they are what survives.
    """
    messages = "; ".join(f"{list(detail['loc'])}: {detail['msg']}" for detail in error.errors())
    line: InitErrorDetails = {
        "type": "value_error",
        "loc": (),
        "input": _REDACTED_INPUT,
        "ctx": {"error": ValueError(messages)},
    }
    return ValidationError.from_exception_data(error.title, [line])


def redact_secret_inputs(error: ValidationError) -> ValidationError:
    """Return ``error`` with every secret-bearing line error's input replaced.

    Messages, error types, locations and non-secret inputs are preserved, so an
    unrelated config mistake reported in the same pass still names what it got.
    Returns the argument unchanged when nothing needs redacting — the common
    case, and it keeps the rebuild off every error that cannot leak.
    """
    details = error.errors()
    if not any(_is_secret_bearing(detail["loc"]) for detail in details):
        return error
    try:
        return ValidationError.from_exception_data(
            error.title,
            [_rebuilt_line_error(detail) for detail in details],
        )
    except Exception:  # broad on purpose: NO rebuild failure may put the raw input back in play
        return _messages_only(error)


@contextmanager
def redacting_secret_inputs() -> Iterator[None]:
    """Re-raise any escaping config ``ValidationError`` with its secrets blanked.

    The loader-side boundary. Wrap the ONE call every config layer (YAML
    overlay, env, init kwargs) funnels through — ``AppConfig.load``'s ``cls()``
    — and a credential cannot reach the startup error pydantic prints to
    stderr. ``from None`` keeps the un-redacted original off the traceback as
    ``__cause__`` / ``__context__``, so formatting the exception can't undo the
    redaction. Usage::

        with redacting_secret_inputs():
            instance = cls()
    """
    try:
        yield
    except ValidationError as error:
        raise redact_secret_inputs(error) from None


__all__ = ("redact_secret_inputs", "redacting_secret_inputs")
