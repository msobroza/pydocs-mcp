"""The reference writer's prompt: ground truth in, one structured answer out, its hash kept.

``prompt_hash`` is what a stored reference names its prompt by, so the prompt
is pinned: a wording change is a deliberate edit to the golden, never a drift.
"""

from __future__ import annotations

import pytest

from pydocs_eval.judge.chat_wire import ChatCompletion, MessageRole
from pydocs_eval.judge.needle_citation import NeedleSite
from pydocs_eval.judge.reference_prompt import (
    REFERENCE_OUTPUT,
    reference_prompt,
    reference_text_of,
)
from pydocs_eval.judge.reference_sources import ReferenceShape, ReferenceSource, ShownCode

from ._judge_fakes import golden

_SOURCE = ReferenceSource(
    task_id="repoqa-qa/repo_qa/fixture@abc1234/fixture_repo/math_helpers.py::factorial",
    question="Which function in this repository implements the following? Compute n!.",
    shape=ReferenceShape.REPOQA,
    code=(
        ShownCode(
            site=NeedleSite("fixture_repo/math_helpers.py", "factorial"),
            start=3,
            end=6,
            first_line=2,
            lines=(
                "",
                "def factorial(n):",
                "    if n <= 1:",
                "        return 1",
                "    return n * factorial(n - 1)",
                "",
            ),
        ),
    ),
)
# The sha256 of the golden's canonical JSON: re-pin it only with the golden.
_PINNED_PROMPT_HASH = "e2999fda16f8c66a70ea586bc5560fa59395b58b31c90436309a99160c9be1d7"


def _messages(
    source: ReferenceSource = _SOURCE, rejected: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    request = reference_prompt(source, rejected=rejected).request
    return [{"role": m.role.value, "content": m.content} for m in request.messages]


def test_the_prompt_is_the_golden() -> None:
    request = reference_prompt(_SOURCE).request

    assert request.custom_id == _SOURCE.task_id
    assert _messages() == golden("reference_writer_prompt.json")["messages"]
    assert request.output == REFERENCE_OUTPUT


def test_the_prompt_hash_is_pinned() -> None:
    assert reference_prompt(_SOURCE).prompt_hash == _PINNED_PROMPT_HASH


def test_the_writer_is_shown_the_question_the_gold_and_the_numbered_lines() -> None:
    (system, user) = _messages()

    assert system["role"] == MessageRole.SYSTEM
    assert _SOURCE.question in user["content"]
    assert "`fixture_repo/math_helpers.py` — `factorial`, lines 3–6" in user["content"]
    assert "3 | def factorial(n):" in user["content"]
    assert "\n7 |\n" in user["content"], "the last context line is shown and numbered"


def test_the_answer_is_one_required_string() -> None:
    schema = REFERENCE_OUTPUT.schema

    assert schema["required"] == ["answer"]
    assert schema["properties"] == {"answer": {"type": "string"}}
    assert schema["additionalProperties"] is False


def test_a_regeneration_names_what_the_rejected_draft_got_wrong() -> None:
    rejected = ("names a file outside the gold: 'tests/test_math.py'",)

    again = reference_prompt(_SOURCE, rejected=rejected)

    (_, _, feedback) = _messages(rejected=rejected)
    assert feedback["role"] == MessageRole.USER
    assert rejected[0] in feedback["content"]
    assert again.prompt_hash != reference_prompt(_SOURCE).prompt_hash


def test_code_holding_a_fence_is_shown_inside_a_longer_one() -> None:
    fenced = ShownCode(NeedleSite("a.md", "Usage"), 1, 1, 1, ("```python",))
    source = ReferenceSource("t", "q", ReferenceShape.CHAT, (fenced,))

    (_, user) = _messages(source)

    assert "````\n1 | ```python\n````" in user["content"]


def test_every_gold_site_is_shown_in_order() -> None:
    first = ShownCode(NeedleSite("a.py", "run"), 1, 1, 1, ("def run(): ...",))
    second = ShownCode(NeedleSite("b.md", "Usage"), 4, 4, 4, ("## Usage",))
    source = ReferenceSource("t", "q", ReferenceShape.CHAT, (first, second))

    (_, user) = _messages(source)

    assert user["content"].index("`a.py` — `run`") < user["content"].index("`b.md` — `Usage`")


@pytest.mark.parametrize(
    ("content", "text"),
    [
        ({"answer": "  `a.py` — `run`  "}, "`a.py` — `run`"),
        ({"answer": ""}, None),
        ({"answer": 3}, None),
        ({"other": "x"}, None),
    ],
)
def test_the_answer_text_is_read_only_when_it_is_a_non_empty_string(
    content: dict[str, object], text: str | None
) -> None:
    completion = ChatCompletion("t", "anthropic/claude-opus-5.5", content)

    assert reference_text_of(completion) == text
