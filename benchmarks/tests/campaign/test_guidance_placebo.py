"""The guidance-task placebo (issue #464, part of #391), read at its seams.

Variant (c) loads a placebo: a ten-section guidance document whose three
sections chat receives for ``repo_qa`` have the candidate seed's exact
character counts, and are about a task this agent never performs (laying out a
printed conference schedule). Its other seven sections are the candidate's,
byte for byte. The placebo is eval-side only and is never packaged.

Both files are read here, so a later change to the candidate fails these tests
on purpose: the placebo is then re-padded, never the candidate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydocs_mcp.application.description_source import (
    CHARS_PER_TOKEN,
    FROZEN_TOOL_NAMES,
    parse_sections,
)
from pydocs_mcp.harness.core.skill_artifact_loader import (
    BACKBONE_HEADER,
    BACKBONE_TOKEN_BUDGET,
    PER_HARNESS_TASK_HEAD_TOKEN_BUDGET,
    PER_TASK_HEAD_TOKEN_BUDGET,
    SKILL_ARTIFACT_HEADERS,
    TASK_HEAD_SECTION_HEADERS,
    harness_task_head_section_header,
    parse_skill_artifact,
    task_head_section_header,
)

import pydocs_mcp
from pydocs_eval.campaign.before_after_arm_settings import load_arm_overlay

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs" / "guidance_task"
CANDIDATE_PATH = _CONFIGS_DIR / "candidate_seed.md"
PLACEBO_PATH = _CONFIGS_DIR / "placebo_guidance.md"

# The three sections chat receives for repo_qa (the spec's construction rule).
_DELIVERED_FOR_REPO_QA = (
    BACKBONE_HEADER,
    task_head_section_header("repo_qa"),
    harness_task_head_section_header("ask_your_docs", "repo_qa"),
)
_NOT_DELIVERED = tuple(h for h in SKILL_ARTIFACT_HEADERS if h not in _DELIVERED_FOR_REPO_QA)


def _sections(path: Path) -> dict[str, str]:
    return parse_sections(path.read_text(encoding="utf-8"), allowed=SKILL_ARTIFACT_HEADERS)


def _cap(header: str) -> int:
    """The loader's cap for one section, read from the loader's own constants."""
    if header == BACKBONE_HEADER:
        return BACKBONE_TOKEN_BUDGET
    if header in TASK_HEAD_SECTION_HEADERS:
        return PER_TASK_HEAD_TOKEN_BUDGET
    return PER_HARNESS_TASK_HEAD_TOKEN_BUDGET


# --- The construction rule ------------------------------------------------------------


def test_placebo_loads_through_the_product_loader_with_all_ten_sections() -> None:
    parse_skill_artifact(PLACEBO_PATH.read_text(encoding="utf-8"), origin=str(PLACEBO_PATH))

    assert tuple(_sections(PLACEBO_PATH)) == SKILL_ARTIFACT_HEADERS
    assert len(SKILL_ARTIFACT_HEADERS) == 10


def test_every_placebo_section_is_under_its_cap() -> None:
    for header, body in _sections(PLACEBO_PATH).items():
        assert len(body) // CHARS_PER_TOKEN <= _cap(header), header


@pytest.mark.parametrize("header", _DELIVERED_FOR_REPO_QA)
def test_each_delivered_section_has_the_candidates_character_count(header: str) -> None:
    placebo, candidate = _sections(PLACEBO_PATH), _sections(CANDIDATE_PATH)

    assert len(placebo[header]) == len(candidate[header]), (
        f"{header}: placebo has {len(placebo[header])} characters, candidate has "
        f"{len(candidate[header])}; pad or trim the placebo, never the candidate"
    )
    assert placebo[header] != candidate[header], f"{header}: placebo repeats the candidate"


def test_the_seven_undelivered_sections_equal_the_candidates_byte_for_byte() -> None:
    placebo, candidate = _sections(PLACEBO_PATH), _sections(CANDIDATE_PATH)

    assert len(_NOT_DELIVERED) == 7
    for header in _NOT_DELIVERED:
        assert placebo[header] == candidate[header], f"{header} differs from the candidate"


# --- The content rule -----------------------------------------------------------------

# Word stems the delivered sections must not contain, matched case-insensitively
# at the start of a word. One line per stem says which part of the rule it covers.
_DENY_STEMS = {
    "search": "searching — the retrieval move the guidance under test tunes",
    "quer": "searching — a query is a search",
    "look": "searching — 'look up' / 'look for' is searching in other words",
    "find": "searching — finding is the outcome of a search",
    "retriev": "searching — retrieval is the backbone's subject",
    "grep": "searching — a literal-match search, also a tool name",
    "read": "reading — a confirming read is what the ask head rations",
    "open": "reading — opening a document is reading it",
    "stop": "stopping — the stop rule is the backbone's load-bearing sentence",
    "halt": "stopping — a synonym of stop",
    "cite": "citing — the answer's citation discipline",
    "citation": "citing — the noun form",
    "quot": "citing — quoting a source is citing it",
    "answer": "answering — the task's output",
    "repl": "answering — a reply is an answer",
    "respon": "answering — a response is an answer",
    "question": "answering — the question is what an answer answers",
    "file": "files — the unit the agent reads and names",
    "path": "paths — the file path convention",
    "director": "paths — directories are path components",
    "folder": "paths — a folder is a directory",
    "symbol": "symbols — the symbol convention",
    "function": "symbols — a function is a symbol",
    "class": "symbols — a class is a symbol",
    "method": "symbols — a method is a symbol",
    "identifier": "symbols — identifiers are symbol names",
    "code": "symbols and files — the corpus this agent works over",
    "repo": "paths — the repository the paths are relative to",
    "tool": "tools — the guidance under test routes between tools",
    "call": "tools — a tool call",
    "turn": "stopping — turns are the unit the effort shape budgets",
    "effort": "stopping — the ask head's effort shape is a stop budget",
    "budget": "stopping — a budget is a stop condition",
    "confirm": "reading — a confirming read; 'Not confirmed:' closes an answer",
    "verif": "reading — verifying is a confirming read",
    "check": "reading — checking is a confirming read",
    "guess": "answering — 'do not guess' is the unconfirmed-answer rule",
}

# Phrases that would carry one of the candidate's rule SHAPES over in other nouns
# (review finding: a placebo that maps clause for clause onto the candidate is not
# "length alone"). One line per phrase says which candidate rule it would mirror.
_DENY_PHRASES = {
    "at most": "the breadth caps (at most three queries, about five documents)",
    "the moment": "the stop trigger (stop the moment a result names ...)",
    "the third": "the first/second/third effort shape",
    "again": "never read again what a result already showed",
    "in one go": "batching independent calls in one turn",
    "together": "the 'Together:' batching line",
    "unconfirmed": "the trailing 'Not confirmed:' line",
}
# A quoted label such as "Together:" or "Not confirmed:" is the candidate's
# answer-shaping device; the placebo carries none.
_QUOTED_LABEL = re.compile(r"\"[^\"]*:\"")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z_]+", text.lower())


@pytest.mark.parametrize("header", _DELIVERED_FOR_REPO_QA)
def test_no_delivered_section_names_any_of_the_nine_tools(header: str) -> None:
    words = set(_words(_sections(PLACEBO_PATH)[header]))

    assert len(FROZEN_TOOL_NAMES) == 9
    assert not words & set(FROZEN_TOOL_NAMES)


@pytest.mark.parametrize("header", _DELIVERED_FOR_REPO_QA)
def test_no_delivered_section_contains_a_denied_stem(header: str) -> None:
    hits = sorted(
        {
            (w, _DENY_STEMS[s])
            for w in _words(_sections(PLACEBO_PATH)[header])
            for s in _DENY_STEMS
            if w.startswith(s)
        }
    )

    assert hits == [], f"{header} contains denied words (word, why denied): {hits}"


@pytest.mark.parametrize("header", _DELIVERED_FOR_REPO_QA)
def test_no_delivered_section_carries_a_rule_shape_of_the_candidate(header: str) -> None:
    body = " ".join(_sections(PLACEBO_PATH)[header].lower().split())
    hits = [(p, why) for p, why in _DENY_PHRASES.items() if re.search(rf"\b{p}\b", body)]

    assert hits == [], f"{header} mirrors candidate rules (phrase, rule): {hits}"
    assert _QUOTED_LABEL.findall(body) == [], header


def _sentences(text: str) -> list[str]:
    collapsed = " ".join(text.split())
    return [s for s in re.split(r"(?<=[.:;!?])\s+", collapsed) if s]


def test_the_delivered_sections_repeat_no_sentence_and_no_line() -> None:
    placebo = _sections(PLACEBO_PATH)
    sentences = [s for h in _DELIVERED_FOR_REPO_QA for s in _sentences(placebo[h])]
    lines = [ln.strip() for h in _DELIVERED_FOR_REPO_QA for ln in placebo[h].splitlines()]
    lines = [ln for ln in lines if ln]

    repeated = sorted({s for s in sentences if sentences.count(s) > 1})
    repeated_lines = sorted({ln for ln in lines if lines.count(ln) > 1})

    assert repeated == [], f"repeated sentences: {repeated}"
    assert repeated_lines == [], f"repeated lines: {repeated_lines}"


# Floors for "rules, not filler". The smallest delivered section of the candidate
# holds eight sentences; five leaves room for longer placebo sentences. A
# distinct-word ratio under 0.4 means the text recycles its own vocabulary.
_MIN_SENTENCES = 5
_MIN_DISTINCT_WORD_RATIO = 0.4


@pytest.mark.parametrize("header", _DELIVERED_FOR_REPO_QA)
def test_each_delivered_section_reads_as_rules_not_padding(header: str) -> None:
    body = _sections(PLACEBO_PATH)[header]
    words = _words(body)

    # Rules, not filler: several sentences, a varied vocabulary, no padding runs.
    assert len(_sentences(body)) >= _MIN_SENTENCES, header
    assert len(set(words)) / len(words) > _MIN_DISTINCT_WORD_RATIO, header
    assert not re.search(r"(.)\1{3,}", body)
    assert not re.search(r"\b(\w+) \1\b", body)


# --- Plan time and packaging ----------------------------------------------------------


def test_an_overlay_naming_the_placebo_is_accepted_by_the_overlay_loader(
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "placebo_overlay.yaml"
    overlay.write_text(f"guidance_sections: {PLACEBO_PATH}\n", encoding="utf-8")

    loaded = load_arm_overlay(overlay, flag="--candidate-settings")

    assert loaded.product == {}
    assert loaded.guidance_sections_path == str(PLACEBO_PATH)
    assert dict(loaded.guidance_sections) == _sections(PLACEBO_PATH)


def test_the_placebo_is_not_inside_the_product_package() -> None:
    package_root = Path(pydocs_mcp.__file__).resolve().parent

    assert not PLACEBO_PATH.resolve().is_relative_to(package_root)
    assert not list(package_root.rglob(PLACEBO_PATH.name))
