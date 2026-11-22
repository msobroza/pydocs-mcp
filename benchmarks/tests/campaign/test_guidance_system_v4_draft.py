"""Variant (a)'s guidance file: the candidate seed plus the ``system_v4`` draft.

Issue #463, part of #391. The file is the candidate seed's ten skill sections
followed by a ``SYSTEM_PROMPT`` section that holds the draft; the ask harness
routes that section to its existing prompt-override channel. These tests are
the sentence-map seam of the spec ("The ``system_v4`` draft and the sentence
map"): every ``system_v3`` rule has exactly one home — kept in the draft, moved
to the candidate seed, or (rule 16) absent until a winning PR.

Every moved and kept sentence is first checked against the LIVE ``system_v3``
text, so a quote that drifts from the product fails here instead of passing
vacuously.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest
from pydocs_mcp.application.description_source import CHARS_PER_TOKEN, parse_sections
from pydocs_mcp.harness.ask_your_docs.binding import _partition_guidance, _write_candidate_skill
from pydocs_mcp.harness.ask_your_docs.prompt_assembly import (
    _assemble_prompt,
    _resolved_skill_block,
)
from pydocs_mcp.harness.ask_your_docs.prompts import SYSTEM_PROMPT, rewrite_prompt
from pydocs_mcp.harness.core.prompt_surfaces import ACTIVE_SYSTEM_PROMPT_TEMPLATE
from pydocs_mcp.harness.core.prompts import core_prompt_names
from pydocs_mcp.harness.core.skill_artifact_loader import (
    BACKBONE_HEADER,
    SKILL_ARTIFACT_HEADERS,
    harness_task_head_section_header,
    task_head_section_header,
)

from pydocs_eval.optimize.artifacts._delimited import render_delimited
from pydocs_eval.optimize.artifacts.ask_prompt import (
    _ASK_SYSTEM_TOKEN_BUDGET,
    _REWRITE_KEY,
    _ROUTED_TOOL_NAMES,
    _SYSTEM_KEY,
    AskPromptArtifact,
)

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs" / "guidance_task"
CANDIDATE_PATH = _CONFIGS_DIR / "candidate_seed.md"
VARIANT_A_PATH = _CONFIGS_DIR / "variant_a_system_v4_draft.md"

_REPO_QA_TASK_HEAD = task_head_section_header("repo_qa")
_ASK_REPO_QA_HEAD = harness_task_head_section_header("ask_your_docs", "repo_qa")


def _collapsed(text: str) -> str:
    """Collapse hard wrapping so a sentence matches across lines."""
    return " ".join(text.split())


def _variant_a_sections() -> dict[str, str]:
    return parse_sections(
        VARIANT_A_PATH.read_text(encoding="utf-8"),
        allowed=(*SKILL_ARTIFACT_HEADERS, _SYSTEM_KEY),
    )


def _draft() -> str:
    return _variant_a_sections()[_SYSTEM_KEY]


def _candidate_sections() -> dict[str, str]:
    return parse_sections(
        CANDIDATE_PATH.read_text(encoding="utf-8"), allowed=SKILL_ARTIFACT_HEADERS
    )


def _v3() -> str:
    return _collapsed(SYSTEM_PROMPT)


def _assert_kept(sentence: str) -> None:
    """``sentence`` is quoted from system_v3 and is still in the draft."""
    assert sentence in _v3(), f"the quote drifted from system_v3: {sentence!r}"
    assert sentence in _collapsed(_draft()), sentence


# --- The file: the candidate's ten sections, then the draft ------------------------


def test_the_file_is_the_ten_skill_sections_then_the_system_prompt_section() -> None:
    assert tuple(_variant_a_sections()) == (*SKILL_ARTIFACT_HEADERS, _SYSTEM_KEY)


def test_the_ten_skill_sections_equal_the_candidate_seed_byte_for_byte() -> None:
    sections = _variant_a_sections()
    skill_sections = {key: sections[key] for key in SKILL_ARTIFACT_HEADERS}

    assert skill_sections == _candidate_sections()


def test_the_file_is_the_candidate_seed_bytes_then_the_draft_section() -> None:
    # Stronger than section equality: the seed has ONE text, so the variant
    # file is the seed file verbatim with the prompt section appended.
    candidate = CANDIDATE_PATH.read_text(encoding="utf-8")
    variant = VARIANT_A_PATH.read_text(encoding="utf-8")

    assert variant == candidate + f"=== {_SYSTEM_KEY} ===\n" + _draft() + "\n"


# --- Kept rules: each sentence is in system_v3 AND in the draft --------------------
# Rule numbers in names and keys are system_v3's; the draft renumbers 1-10.

_KEPT = {
    "preamble: answer only from tool results": (
        "You answer ONLY from the results of your tools — never from memory."
    ),
    "preamble: tool descriptions say when to call": (
        "Each tool's own description says what it does and when to call it; this "
        "prompt does not repeat them."
    ),
    "rule 1: users may not know the project name": (
        "Users often don't know the framework or project name. Infer it from the task "
        "and the indexed-projects list below."
    ),
    "rule 1: infer the project, search unscoped first": (
        "If unsure, search UNSCOPED first (all projects) and let the results "
        "identify the owner, then narrow."
    ),
    "rule 2: self-contained follow-up queries": (
        "Rewrite follow-up questions into self-contained queries"
    ),
    "rule 3: one clarifying question": "ask ONE short clarifying question instead of guessing.",
    "rule 4: concise, cited, fenced": (
        "Be concise. Cite the project and package.module for every claim. Put "
        "signatures and code in fenced code blocks."
    ),
    "rule 4: say so plainly": (
        "If the tools found nothing, say so plainly — do not invent an answer."
    ),
    "rule 6: pinned scope": ('A question may carry a "[pinned scope: ...]" note set by the app.'),
    "rule 6: empty search under a pin": (
        "If a search comes back empty, say the pinned scope may be too narrow and "
        "suggest widening it."
    ),
    "rule 11: exact strings to grep, concepts to search": (
        "An exact string, an error message or a config key goes to grep and read_file; "
        "a ranked or conceptual question goes to search_codebase."
    ),
}


@pytest.mark.parametrize("rule", sorted(_KEPT))
def test_each_kept_rule_sentence_is_in_the_draft(rule: str) -> None:
    _assert_kept(_KEPT[rule])


def test_v3_rule_4_keeps_say_so_plainly() -> None:
    _assert_kept("If the tools found nothing, say so plainly")


# --- The three protected rules, asserted by name -----------------------------------


def test_protected_v3_rule_8_take_the_offered_follow_up_instead_of_searching_again() -> None:
    sentence = "Call the follow-up a result offers instead of searching again for the same thing."

    _assert_kept(sentence)


def test_protected_v3_rule_10_one_call_with_many_targets() -> None:
    sentence = (
        "One call with many targets beats several calls of the same tool: get_context "
        "takes a list of targets."
    )

    _assert_kept(sentence)


def test_protected_v3_rule_15_context_and_uniqueness_check_in_one_turn() -> None:
    sentence = (
        "When the answer needs get_context on a symbol you located and a grep confirming "
        "its name has no second site, issue both in the same turn."
    )

    _assert_kept(sentence)


# --- Moved rules: absent from the draft, present in the ask head --------------------

# rule → (system_v3 sentences that must leave the draft, the ask head's wording)
_MOVED = {
    "rule 5: an Example only from what was shown": (
        (
            'end with a SHORT "Example" snippet',
            "assembled strictly from a signature or usage the results already showed",
            "Never invent parameters, defaults, or return shapes the tools did not show.",
        ),
        ("When a result showed a signature or usage, end with a short Example built from it",),
    ),
    "rule 7: follow-ups are offers, Together now, Then later": (
        (
            "They are offers, not orders: take only the ones the question needs.",
            'The calls on a "Together:" line are independent of each other',
            'The calls on a "Then:" line need the earlier results first',
        ),
        (
            "Follow-up calls on a result are offers: take only those the question needs.",
            'Issue the calls on a "Together:" line in one turn',
            'leave a "Then:" line for a later turn',
        ),
    ),
    "rule 12: one source view, the card only for a member list": (
        (
            'To see a symbol\'s code, make one get_symbol call with depth="source" or one '
            "windowed read_file call.",
            "Ask for the symbol card (get_symbol's default depth) only when the question "
            "needs its member list.",
        ),
        (
            'one get_symbol depth="source" call or one windowed read_file call',
            "ask for the symbol card only when the question needs its member list",
        ),
    ),
    "rule 13: windowed read_file": (
        (
            "Give read_file an offset and limit around the lines a result cited or "
            "reported as missing",
        ),
        (
            "Give read_file an offset and limit around the lines a result cited or "
            "reported as missing, and widen only to follow a reference you need.",
        ),
    ),
    "rule 14: get_overview after a miss or for structure": (
        ("Call get_overview only after a search misses",),
        (
            "Call get_overview only after a search misses or for a question about the "
            "project's structure, never beside the first search",
        ),
    ),
}


@pytest.mark.parametrize("rule", sorted(_MOVED))
def test_each_moved_rule_left_the_draft_and_lives_in_the_ask_repo_qa_head(rule: str) -> None:
    v3_sentences, head_wording = _MOVED[rule]
    draft = _collapsed(_draft())
    head = _collapsed(_candidate_sections()[_ASK_REPO_QA_HEAD])

    for sentence in v3_sentences:
        assert sentence in _v3(), f"{rule}: the quote drifted from system_v3"
        assert sentence not in draft, f"{rule}: still in the draft: {sentence!r}"
    for wording in head_wording:
        assert wording in head, f"{rule}: missing from the ask head: {wording!r}"


_RULE_9_V3_SENTENCES = (
    "Never read or fetch content a result already showed you.",
    'A hit whose follow-up calls offer no source call (no depth="source", no read_file) '
    "has already shown its whole span.",
    'When a view says lines are "not in the index", read exactly those lines',
)


def test_v3_rule_9_left_the_draft_with_neither_its_prohibition_nor_its_definition() -> None:
    draft = _collapsed(_draft())

    for sentence in _RULE_9_V3_SENTENCES:
        assert sentence in _v3(), f"the quote drifted from system_v3: {sentence!r}"
        assert sentence not in draft, sentence
    for phrase in ("already showed", "already shown", "not in the index", "whole span"):
        assert phrase not in draft, phrase


def test_v3_rule_9_prohibition_is_in_the_ask_head_and_its_definition_in_backbone_once() -> None:
    sections = _candidate_sections()
    head = _collapsed(sections[_ASK_REPO_QA_HEAD])
    backbone = _collapsed(sections[BACKBONE_HEADER])

    assert "Never read again what a result already showed" in head
    assert "Already shown: a hit whose follow-up calls offer no source call" in backbone
    assert "lines a view reports as not in the index are read exactly once" in backbone
    # Each stated once: the definition is not repeated in the head.
    assert "Already shown:" not in head
    assert "not in the index" not in head


# --- Rule 16: absent ---------------------------------------------------------------


def test_v3_rule_16_the_gated_branch_rule_is_absent() -> None:
    draft = _draft()

    assert "branch" not in draft.lower()
    assert "branch_selector_advertised" not in draft


# --- The routing sentence -------------------------------------------------------------

# The distinctive instruction words of every moved rule (5, 7, 9, 12, 13, 14):
# a routing sentence carrying any of them repeats a moved rule's instruction.
_MOVED_INSTRUCTION_MARKERS = (
    "depth",
    "source",
    "card",
    "member",
    "miss",
    "structure",
    "first search",
    "offset",
    "window",
    "Example",
    "Together",
    "Then:",
    "offers",
    "already",
)


def _sentences_naming(tool: str, text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.;])\s+", _collapsed(text)) if tool in s]


def test_one_routing_sentence_names_get_symbol_and_get_overview_and_points_to_the_guidance() -> (
    None
):
    draft = _draft()
    symbol_sentences = _sentences_naming("get_symbol", draft)
    overview_sentences = _sentences_naming("get_overview", draft)

    assert len(symbol_sentences) == 1
    assert symbol_sentences == overview_sentences
    assert "guidance" in symbol_sentences[0]


def test_the_routing_sentence_repeats_no_moved_rule_instruction() -> None:
    (routing,) = _sentences_naming("get_symbol", _draft())

    for marker in _MOVED_INSTRUCTION_MARKERS:
        assert marker.lower() not in routing.lower(), marker


# --- Shape: numbering, validator, plain text ------------------------------------------


def test_the_draft_rules_are_numbered_contiguously_from_one() -> None:
    numbered = re.findall(r"^\s*(\d+)\. ", _draft(), re.MULTILINE)

    assert numbered
    assert numbered == [str(n) for n in range(1, len(numbered) + 1)]


def test_the_draft_passes_the_ask_prompt_validator() -> None:
    document = render_delimited(
        {
            _SYSTEM_KEY: _draft(),
            _REWRITE_KEY: rewrite_prompt(history="{history}", question="{question}"),
        }
    )

    assert AskPromptArtifact().with_content(document).validate() == ()


def test_the_draft_names_all_six_routed_tools_and_fits_the_budget() -> None:
    draft = _draft()

    assert draft.strip()
    assert len(draft) // CHARS_PER_TOKEN < _ASK_SYSTEM_TOKEN_BUDGET
    for tool in _ROUTED_TOOL_NAMES:
        assert tool in draft, tool


def test_the_draft_is_smaller_than_system_v3() -> None:
    assert len(_draft()) < len(SYSTEM_PROMPT)


def test_the_draft_is_plain_text_with_no_template_variable() -> None:
    draft = _draft()

    for delimiter in ("{{", "}}", "{%", "%}", "{#", "#}"):
        assert delimiter not in draft, delimiter
    # A no-variable template of this text renders to itself.
    assert jinja2.Template(draft, keep_trailing_newline=True).render() == draft


# --- What the ask harness serves from this file ------------------------------------------


def test_the_served_prompt_opens_with_the_draft_and_ends_with_the_repo_qa_guidance(
    tmp_path: Path,
) -> None:
    """The routing sentence says the guidance is BELOW: hold that on the
    assembled prompt the ask harness builds from this very file."""
    overrides, skill_sections = _partition_guidance(_variant_a_sections())
    skill_path = _write_candidate_skill(skill_sections, tmp_path)
    skill_block = _resolved_skill_block(skill_path, "repo_qa")
    served = _assemble_prompt("text_react", {"demo": ["pkg_a"]}, overrides, None, skill_block)

    folded = (BACKBONE_HEADER, _REPO_QA_TASK_HEAD, _ASK_REPO_QA_HEAD)
    assert overrides.system_prompt == _draft()
    assert served.startswith(_draft() + "\nIndexed projects and packages:\n")
    assert served.endswith("\n".join(_candidate_sections()[key] for key in folded))


# --- The product is untouched -----------------------------------------------------------


def test_no_system_v4_product_template_and_the_active_template_is_v3() -> None:
    assert ACTIVE_SYSTEM_PROMPT_TEMPLATE == "system_v3"
    assert not any(name.startswith("system_v4") for name in core_prompt_names())
