"""The docs say where dependency decisions answer (issue #346).

Under ``decision_capture.include_deps`` a dependency's decisions persist under
its own package, and the decision surfaces return them only when a request asks
for them (``application/decision_corpus.py``). An agent learns which call
reaches them from the tool descriptions, and an integrator from the normative
contract, never from the code. A description that let ``get_why(query)`` read
as covering every package, or a contract that still left ``kind="decision"``
ignoring ``scope`` / ``package``, would send them to a call that cannot answer.

The "only when asked" rule stops at those surfaces. Each dependency decision is
also one of its dependency's docs chunks (``embed_policy._DOC_ORIGINS``), and no
``kind="any"`` / ``"docs"`` search filters decision chunks by package, so the
default search can return one. Docs that dropped that caveat would promise a
filter the code does not have. The re-extraction cost has the same shape:
``include_deps`` sits in the project's decision digest, so toggling it
re-extracts the project too, and only the project while ``enabled`` is false.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.tool_docs import TOOL_DOCS

ROOT = Path(__file__).resolve().parents[1]


def _contract_section(heading: str) -> str:
    """One ``###`` section of the contract, whitespace-collapsed so a re-wrap
    of the prose never reads as a dropped claim."""
    contract = (ROOT / "docs" / "tool-contracts.md").read_text(encoding="utf-8")
    assert heading in contract, f"docs/tool-contracts.md lost its {heading!r} section"
    section = contract.split(heading, 1)[1].split("\n### ", 1)[0]
    return " ".join(section.split())


def _include_deps_comment() -> str:
    """The comment block directly above ``include_deps:`` in the shipped defaults,
    ``#`` markers stripped so a claim wrapped across two lines still matches."""
    lines = (ROOT / "python/pydocs_mcp/defaults/default_config.yaml").read_text("utf-8")
    above = lines.split("  include_deps:", 1)[0].splitlines()
    comment: list[str] = []
    for line in reversed(above):
        if not line.strip().startswith("#"):
            break
        comment.insert(0, line.strip().removeprefix("#").strip())
    return " ".join(comment)


def _readme_dependency_decisions_paragraph() -> str:
    """The README paragraph on mining dependencies, whitespace-collapsed."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    opener = "Mining your dependencies is opt-in."
    assert opener in readme, "README.md lost its dependency-decision paragraph"
    paragraph = readme[readme.index(opener) :].split("\n\n", 1)[0]
    return " ".join(paragraph.split())


def test_search_codebase_description_scopes_decisions_to_the_project_unless_asked() -> None:
    doc = TOOL_DOCS["search_codebase"]
    assert 'kind="decision" searches the project\'s decisions unless scope="deps"' in doc
    assert "or package= asks for a dependency's" in doc


def test_get_why_description_keeps_query_on_the_project_and_targets_on_their_package() -> None:
    doc = TOOL_DOCS["get_why"]
    assert "over the project's decisions only" in doc
    assert "a dependency's symbol answers with that dependency's recorded decisions" in doc


def test_contract_search_codebase_states_the_decision_corpus_rule() -> None:
    section = _contract_section("### 3.2 `search_codebase`")
    for claim in (
        '**Decision corpus (`kind="decision"`):**',
        "`package` wins",
        '`scope="deps"` searches every dependency with mined decisions',
        "never the project",
        "`decision_capture.include_deps`",
    ):
        assert claim in section, f"tool-contracts §3.2 does not state {claim!r}"


def test_contract_search_codebase_says_ordinary_searches_can_return_dependency_decisions() -> None:
    section = _contract_section("### 3.2 `search_codebase`")
    for claim in (
        '`kind="decision"` returns them only when a request asks for them',
        'a `kind="any"` or `"docs"` search — the default included — can return it',
        "its `items[]` entry carries the dependency's `package`",
    ):
        assert claim in section, f"tool-contracts §3.2 does not state {claim!r}"


def test_contract_get_why_states_the_decision_corpus_rule() -> None:
    section = _contract_section("### 3.6 `get_why`")
    for claim in (
        "**Decision corpus:**",
        "project's decisions only",
        "in the package that mined",
        "`decision_capture.include_deps`",
    ):
        assert claim in section, f"tool-contracts §3.6 does not state {claim!r}"


def test_include_deps_comment_says_what_it_mines_and_where_it_answers() -> None:
    comment = _include_deps_comment()
    for claim in ("`inline_markers` only", 'scope="deps"', "get_why(targets=", "project-only"):
        assert claim in comment, f"default_config.yaml include_deps comment omits {claim!r}"


def test_include_deps_comment_states_the_ordinary_search_caveat_and_the_full_toggle_cost() -> None:
    comment = _include_deps_comment()
    for claim in (
        'kind="any"/"docs" searches can return it',
        "re-extracts the project and every dependency once",
        "only the project while `enabled` is false",
    ):
        assert claim in comment, f"default_config.yaml include_deps comment omits {claim!r}"


def test_readme_states_the_ordinary_search_caveat_and_the_full_toggle_cost() -> None:
    paragraph = _readme_dependency_decisions_paragraph()
    for claim in (
        '`kind="any"` or `"docs"`, the default included',
        "re-indexes your project and each dependency once",
        "only your project while `decision_capture.enabled` is false",
    ):
        assert claim in paragraph, f"README dependency-decision paragraph omits {claim!r}"
