"""The activity panel's redactor: the bearer plus every configured key value (PROPOSAL §6).

Tool results, reasoning and failures can quote a credential the page knows about — the
bearer it presents, or the value of the environment variable a key comes from. The
panel's trace builder runs every accumulated string through ONE callable built here,
so a widening (another credential shape) reaches every panel string at once.

Secrets inside repository files that are not among these known values cannot be
detected; the README says so.

Example:
    redact = turn_redactor(bearer, secret_env_names(connection.api_key_env), os.environ)
    redact("401 for Bearer sk-...wxyz")  # -> "401 for Bearer …wxyz"
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerSource,
    last_four_of,
    redact_bearer,
)

# WHY a floor: masking a short value (a stray "abc" in a test shell) everywhere it occurs
# would garble ordinary text, and a real API key is far longer than this.
_MIN_SECRET_CHARS = 8
# The SDK's own fallback variable: with no ask_your_docs.llm block, ChatOpenAI reads it.
_SDK_KEY_ENV = "OPENAI_API_KEY"


def secret_env_names(api_key_env: str | None) -> tuple[str, ...]:
    """The variables whose values the panel masks: the configured one, then the SDK default."""
    names = (api_key_env, _SDK_KEY_ENV)
    return tuple(dict.fromkeys(name for name in names if name))


def turn_redactor(
    bearer: BearerSource, env_names: tuple[str, ...], environ: Mapping[str, str]
) -> Callable[[str], str]:
    """``text -> text`` with each known key value and the bearer shown as ``…last4``."""
    values = (environ.get(name) or "" for name in env_names)
    secrets = tuple(value for value in values if len(value) >= _MIN_SECRET_CHARS)

    def redact(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, f"…{last_four_of(secret)}")
        return redact_bearer(text, bearer)

    return redact


__all__ = ("secret_env_names", "turn_redactor")
