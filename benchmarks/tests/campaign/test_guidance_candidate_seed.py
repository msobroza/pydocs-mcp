"""The guidance-task candidate seed (issue #461, part of #391), read at its seams.

The candidate is an eval-side skill document an arm loads through an overlay's
``guidance_sections`` key. It is never packaged. These tests read it the way the
product reads guidance: through the product's own loader, beside the packaged
seed, and through the external harness's fold.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydocs_mcp.application import suggestions
from pydocs_mcp.application.description_source import CHARS_PER_TOKEN, parse_sections
from pydocs_mcp.harness.core.guidance_fold import (
    deliverable_section_keys,
    fold_guidance_sections,
)
from pydocs_mcp.harness.core.run_contract import NOT_CONFIRMED_LABEL
from pydocs_mcp.harness.core.skill_artifact_loader import (
    BACKBONE_HEADER,
    BACKBONE_TOKEN_BUDGET,
    PER_HARNESS_TASK_HEAD_TOKEN_BUDGET,
    PER_TASK_HEAD_TOKEN_BUDGET,
    SKILL_ARTIFACT_HEADERS,
    TASK_NAMES,
    load_packaged_skill,
    parse_skill_artifact,
)

from pydocs_eval.campaign.before_after_arm_settings import load_arm_overlay

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs" / "guidance_task"
CANDIDATE_PATH = _CONFIGS_DIR / "candidate_seed.md"


def _candidate_sections() -> dict[str, str]:
    return parse_sections(CANDIDATE_PATH.read_text(encoding="utf-8"), allowed=SKILL_ARTIFACT_HEADERS)


def _cap(header: str) -> int:
    if header == "BACKBONE":
        return BACKBONE_TOKEN_BUDGET
    if header.startswith("TASK_HEAD: "):
        return PER_TASK_HEAD_TOKEN_BUDGET
    return PER_HARNESS_TASK_HEAD_TOKEN_BUDGET


def test_candidate_loads_through_the_product_loader_with_all_ten_sections() -> None:
    parse_skill_artifact(CANDIDATE_PATH.read_text(encoding="utf-8"), origin=str(CANDIDATE_PATH))

    assert tuple(_candidate_sections()) == SKILL_ARTIFACT_HEADERS
    assert len(SKILL_ARTIFACT_HEADERS) == 10


def test_every_candidate_section_is_under_its_cap() -> None:
    for header, body in _candidate_sections().items():
        assert len(body) // CHARS_PER_TOKEN <= _cap(header), header


# --- The guard plan for the readers this experiment does not measure ----------

_UNTOUCHED_HEADERS = (
    "TASK_HEAD: vuln",
    "TASK_HEAD: bug_loc",
    "HARNESS_TASK_HEAD: external.repo_qa",
    "HARNESS_TASK_HEAD: external.vuln",
    "HARNESS_TASK_HEAD: external.bug_loc",
)
_ASK_HEADERS = tuple(f"HARNESS_TASK_HEAD: ask_your_docs.{task}" for task in TASK_NAMES)
# Quoted from the packaged seed, which hard-wraps; compared whitespace-collapsed.
_STOP_RULE = (
    "Stop searching the moment a result names the file and symbol that must contain the answer"
)
_WALKS_AND_READS_CLAUSE = "graph walks and file reads do not count"


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _packaged_sections() -> dict[str, str]:
    seed = load_packaged_skill()
    return {BACKBONE_HEADER: seed.backbone, **seed.task_heads, **seed.harness_task_heads}


def test_the_five_untouched_sections_equal_the_packaged_seed_byte_for_byte() -> None:
    candidate, packaged = _candidate_sections(), _packaged_sections()

    for header in _UNTOUCHED_HEADERS:
        assert candidate[header] == packaged[header], header


def test_the_five_rewritten_sections_differ_from_the_packaged_seed() -> None:
    candidate, packaged = _candidate_sections(), _packaged_sections()
    rewritten = set(SKILL_ARTIFACT_HEADERS) - set(_UNTOUCHED_HEADERS)

    assert rewritten == {BACKBONE_HEADER, "TASK_HEAD: repo_qa", *_ASK_HEADERS}
    for header in rewritten:
        assert candidate[header] != packaged[header], header


def test_backbone_is_no_longer_than_the_packaged_one() -> None:
    assert len(_candidate_sections()[BACKBONE_HEADER]) <= len(_packaged_sections()[BACKBONE_HEADER])


def test_backbone_keeps_the_stop_rule_and_the_walks_and_reads_clause_word_for_word() -> None:
    backbone = _collapsed(_candidate_sections()[BACKBONE_HEADER])

    assert _STOP_RULE in _collapsed(_packaged_sections()[BACKBONE_HEADER])
    assert _STOP_RULE in backbone
    assert _WALKS_AND_READS_CLAUSE in backbone


def test_backbone_defines_already_shown_and_keeps_the_re_orient_sentence() -> None:
    backbone = _collapsed(_candidate_sections()[BACKBONE_HEADER])

    assert "Already shown: a hit whose follow-up calls offer no source call has shown its whole span" in backbone
    assert "lines a view reports as not in the index are read exactly once" in backbone
    assert (
        "If three search queries surface no location, re-orient — widen scope or map the layout"
        in backbone
    )


def test_backbone_breadth_passages_are_conditional_and_a_ceiling() -> None:
    backbone = _collapsed(_candidate_sections()[BACKBONE_HEADER])

    assert "Decompose before retrieving" not in backbone
    assert "precision budget" not in backbone
    assert "When the question holds several behaviors, decompose it" in backbone
    assert "When it asks about one behavior, send one query" in backbone
    assert "at most about five documents in play — a ceiling, not a quota" in backbone


def test_backbone_takes_no_chat_policy_from_the_ask_heads() -> None:
    backbone = _collapsed(_candidate_sections()[BACKBONE_HEADER])

    for ask_only in ("get_overview", "Together:", "Then:", "member list", "Example", "offset"):
        assert ask_only not in backbone, ask_only
    assert NOT_CONFIRMED_LABEL not in backbone


# --- TASK_HEAD: repo_qa -----------------------------------------------------------

_SIGNATURE_TRIGGER = "the question asks for a signature that no result has shown"


def test_repo_qa_head_drops_the_unconditional_read_and_signature_clause() -> None:
    head = _collapsed(_candidate_sections()["TASK_HEAD: repo_qa"])

    assert "Confirm the candidate location by reading it" not in head
    assert "get_symbol where exact signatures matter" not in head
    assert "When the located hit has not already shown its span, read it" in head
    # The ledger records which form was written; either form is allowed.
    if "signature" in head:
        assert _SIGNATURE_TRIGGER in head


# --- The three ask_your_docs heads --------------------------------------------------

_MOVED_RULES = {
    "follow-ups are offers": (
        "Follow-up calls on a result are offers",
        'Issue the calls on a "Together:" line in one turn',
        'leave a "Then:" line for a later turn',
    ),
    "never read again what was shown": ("Never read again what a result already showed",),
    "one source view, the card only for a member list": (
        'one get_symbol depth="source" call or one windowed read_file call',
        "ask for the symbol card only when the question needs its member list",
    ),
    "windowed reads": ("Give read_file an offset and limit around the cited lines",),
    "get_overview after a miss or for structure": (
        "Call get_overview only after a search misses or for a question about the "
        "project's structure, never beside the first search",
    ),
    "an Example only from what was shown": (
        "When a result showed a signature or usage, end with a short Example built from it",
    ),
}
_EFFORT_SHAPE = (
    "the first turn locates, the second reads the cited spans not yet rendered in one batch, "
    "the third answers; any call past that names the claim it supports"
)


@pytest.mark.parametrize("header", _ASK_HEADERS)
def test_each_ask_head_carries_the_six_moved_rules(header: str) -> None:
    head = _collapsed(_candidate_sections()[header])

    for rule, phrases in _MOVED_RULES.items():
        for phrase in phrases:
            assert phrase in head, f"{header}: rule {rule!r} lost {phrase!r}"


@pytest.mark.parametrize("header", _ASK_HEADERS)
def test_each_ask_head_carries_the_effort_shape_and_the_label_from_the_shared_constant(
    header: str,
) -> None:
    head = _collapsed(_candidate_sections()[header])

    assert _EFFORT_SHAPE in head
    assert f'one line that starts with "{NOT_CONFIRMED_LABEL}"' in head
    # Never respelled: every mention of the label is the constant's exact spelling.
    assert head.lower().count(NOT_CONFIRMED_LABEL.lower().rstrip(":")) == head.count(
        NOT_CONFIRMED_LABEL
    )


@pytest.mark.parametrize("header", _ASK_HEADERS)
def test_no_ask_head_claims_the_catalog_or_skips_the_example_snippet(header: str) -> None:
    head = _collapsed(_candidate_sections()[header]).lower()

    assert "catalog is already in your prompt" not in head
    assert "snippet" not in head


# --- Consistency with tool output (ADR 0027, ADR 0007) ------------------------------


def _server_suggestion_texts() -> tuple[str, ...]:
    """Every fixed Suggestion text the server defines, read from its own constants."""
    texts = tuple(
        value
        for name, value in vars(suggestions).items()
        if name.endswith("_SUGGESTION") and isinstance(value, str)
    )
    assert texts, "no Suggestion constants found: the server module moved"
    return texts


def _suggestion_body(text: str) -> str:
    return text.removeprefix(suggestions.SUGGESTION_PREFIX).removesuffix("]").strip()


def test_no_section_restates_a_server_suggestion() -> None:
    document = _collapsed(CANDIDATE_PATH.read_text(encoding="utf-8"))

    assert suggestions.SUGGESTION_PREFIX not in document
    for text in _server_suggestion_texts():
        assert _suggestion_body(text) not in document, text


def test_no_section_tells_the_agent_to_repeat_a_missed_search_under_another_kind() -> None:
    for header, body in _candidate_sections().items():
        text = _collapsed(body).lower()
        for retry in ("kind=", "another kind", "retry"):
            assert retry not in text, f"{header}: {retry!r}"


# --- The external harness's fold --------------------------------------------------------


# The tiers of the external fold the candidate rewrites, per task. ``TASK_HEAD:
# repo_qa`` is harness-INVARIANT, so the decided repo_qa rewrite reaches the
# external harness's repo_qa fold too: for that one task the fold differs in
# BACKBONE and in the task head, never in the external head (a deviation from
# the ticket's "only inside BACKBONE", recorded in the ledger).
_REWRITTEN_EXTERNAL_TIERS = {
    "repo_qa": {BACKBONE_HEADER, "TASK_HEAD: repo_qa"},
    "vuln": {BACKBONE_HEADER},
    "bug_loc": {BACKBONE_HEADER},
}


@pytest.mark.parametrize("task", TASK_NAMES)
def test_external_fold_over_the_candidate_differs_only_in_the_rewritten_tiers(task: str) -> None:
    candidate, packaged = _candidate_sections(), _packaged_sections()
    rewritten = _REWRITTEN_EXTERNAL_TIERS[task]
    # The packaged fold with ONLY the rewritten tiers swapped for the candidate's.
    expected = {
        key: candidate[key] if key in rewritten else packaged[key]
        for key in deliverable_section_keys(harness_name="external", task_name=task)
    }

    fold_candidate = fold_guidance_sections(candidate, harness_name="external", task_name=task)

    assert fold_candidate == fold_guidance_sections(
        expected, harness_name="external", task_name=task
    )
    assert fold_candidate != fold_guidance_sections(
        packaged, harness_name="external", task_name=task
    )


# --- Plan time ----------------------------------------------------------------------------


def test_an_overlay_naming_the_candidate_is_accepted_by_the_overlay_loader(
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "candidate_overlay.yaml"
    overlay.write_text(f"guidance_sections: {CANDIDATE_PATH}\n", encoding="utf-8")

    loaded = load_arm_overlay(overlay, flag="--candidate-settings")

    assert loaded.product == {}
    assert loaded.guidance_sections_path == str(CANDIDATE_PATH)
    assert dict(loaded.guidance_sections) == _candidate_sections()
