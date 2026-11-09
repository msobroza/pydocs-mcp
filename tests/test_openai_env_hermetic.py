"""The suite never sees the shell's ``OPENAI_ADMIN_KEY``, ``OPENAI_CUSTOM_HEADERS`` or
``LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S``.

openai 2 reads the first two in every client it builds: the admin key can become the
bearer of a raw request whose own key is empty, and the custom headers are merged
into every request after authentication — an ``Authorization:`` line there
replaces the configured bearer. langchain-openai 1.2+ reads the third into every
``ChatOpenAI``'s stream limit, and an invalid value logs a WARNING. A developer whose
shell exports any of them would see results nobody else can reproduce, so
``tests/conftest.py`` removes all three from every test's environment without reading
them. ``OPENAI_API_KEY`` is left alone:
tests set it themselves, and the opt-in live integration test skips without it.

Membership checks only — no test here reads a value from the environment.
"""

from __future__ import annotations

import os

import pytest

_SHELL_ONLY_OPENAI_VARIABLES = (
    "OPENAI_ADMIN_KEY",
    "OPENAI_CUSTOM_HEADERS",
    "LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S",
)


@pytest.mark.parametrize("name", _SHELL_ONLY_OPENAI_VARIABLES)
def test_shell_openai_variable_is_absent_inside_a_test(name: str) -> None:
    # A bare bool, not ``name not in os.environ`` inline: pytest's assertion
    # rewriting would print the whole environment on failure.
    present = name in os.environ
    assert not present, f"{name} leaked from the calling shell into a test"


def test_a_test_can_still_set_a_removed_variable_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    # The autouse removal runs before the test body, so a test that needs one of
    # these variables sets a synthetic value through monkeypatch and sees it.
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Synthetic: 1")
    seen = os.environ.get("OPENAI_CUSTOM_HEADERS")
    assert seen == "X-Synthetic: 1"
