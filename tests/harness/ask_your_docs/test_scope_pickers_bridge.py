"""``scope_pickers.pinned_question_scope`` — the sidebar pickers' dict as a QuestionScope.

The bridge keeps today's page working on the QuestionScope-typed ``ask()``: nothing
pinned is ``None`` (the interceptor's strict passthrough — the unpinned turn byte for
byte), anything pinned is a hard PIN whose note reads exactly as before.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from pydocs_mcp.harness.ask_your_docs.scope_pickers import pinned_question_scope
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
    scope_prefix,
)


def test_nothing_pinned_is_none() -> None:
    assert pinned_question_scope({"project": "", "package": "", "code": "all"}) is None
    assert pinned_question_scope({}) is None


def test_every_pin_lands_on_the_hard_pin() -> None:
    pin = pinned_question_scope({"project": "backend", "package": "requests", "code": "deps"})
    assert pin == QuestionScope(
        kind=ScopeKind.PIN,
        cells=(ScopeCell("backend", ""),),
        package="requests",
        code=ScopeCode.DEPS,
    )


@pytest.mark.parametrize(
    ("legacy_code", "expected"),
    [("project", ScopeCode.OWN), ("deps", ScopeCode.DEPS), ("all", ScopeCode.ALL)],
)
def test_the_code_words_map_to_the_enum(legacy_code: str, expected: ScopeCode) -> None:
    pin = pinned_question_scope({"project": "backend", "package": "", "code": legacy_code})
    assert pin is not None and pin.code is expected


def test_the_note_reads_exactly_as_before() -> None:
    """The "[pinned scope: ...]" note the model saw before the QuestionScope rewire."""
    pin = pinned_question_scope({"project": "backend", "package": "requests", "code": "project"})
    assert scope_prefix(pin) == "[pinned scope: project=backend, package=requests, own code only] "
    only_code = pinned_question_scope({"project": "", "package": "", "code": "deps"})
    assert scope_prefix(only_code) == "[pinned scope: dependencies only] "
