"""The panel's one redactor: the bearer plus every configured key variable (PROPOSAL §6).

Everything the activity panel stores or renders — tool arguments, results, reasoning,
failures — crosses ``turn_redactor`` once, over accumulated text.
"""

from __future__ import annotations

from pydocs_mcp.harness.ask_your_docs.activity_redaction import (
    secret_env_names,
    turn_redactor,
)

from ._connection_fakes import FakeBearer

_KEY = "sk-env-key-0123456789-wxyz"


def test_the_bearer_and_every_named_key_value_are_masked() -> None:
    environ = {"LLM_KEY": _KEY, "OPENAI_API_KEY": "sk-openai-key-5678"}
    redact = turn_redactor(FakeBearer("tok-fixed-abcd"), ("LLM_KEY", "OPENAI_API_KEY"), environ)
    text = f"tok-fixed-abcd then {_KEY} then sk-openai-key-5678 and Bearer other-token"
    assert redact(text) == "…abcd then …wxyz then …5678 and Bearer …abcd"


def test_short_or_unset_values_are_not_treated_as_secrets() -> None:
    redact = turn_redactor(FakeBearer("tok-fixed-abcd"), ("SHORT", "UNSET"), {"SHORT": "abc"})
    assert redact("abc abc") == "abc abc"  # masking "abc" everywhere would garble the text


def test_the_names_come_from_the_connection_and_the_sdk_default() -> None:
    assert secret_env_names("LLM_KEY") == ("LLM_KEY", "OPENAI_API_KEY")
    assert secret_env_names(None) == ("OPENAI_API_KEY",)
    assert secret_env_names("OPENAI_API_KEY") == ("OPENAI_API_KEY",)
