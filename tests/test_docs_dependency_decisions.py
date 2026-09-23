"""The docs say where dependency decisions answer (issue #346).

Under ``decision_capture.include_deps`` a dependency's decisions persist under
its own package, and the decision surfaces return them only when a request asks
for them (``application/decision_corpus.py``). An agent learns which call
reaches them from the tool descriptions, and an integrator from the normative
contract, never from the code. A description that let ``get_why(query)`` read
as covering every package, or a contract that still left ``kind="decision"``
ignoring ``scope`` / ``package``, would send them to a call that cannot answer.

Ordinary searches keep the rule too (owner decision 2026-09-23). Each dependency
decision is also one of its dependency's docs chunks
(``embed_policy._DOC_ORIGINS``), and every ``kind="any"`` / ``"docs"`` search —
the default and ``scope="deps"`` included — leaves it out unless ``package=``
names that dependency (``SearchQuery.exclude_dependency_decisions``). Docs that
still carried the old "ordinary searches can return it" caveat would send an
agent looking for a mix the server no longer returns. That filter follows what
the loaded bundle holds (``ProjectServices.holds_dependency_decisions``), not
the config of the process answering the query, so the claim holds whichever
config serves the index — a read-only ``--workspace`` / ``--db`` load included —
and no doc may still tell the reader to repeat ``include_deps: true`` in the
serving config: that advice described a gate that no longer exists. The re-extraction cost
has the same shape as before: ``include_deps`` sits in the project's decision
digest, so toggling it re-extracts the project too, and only the project while
``enabled`` is false.
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


def _states_no_ordinary_search_caveat(text: str, where: str) -> None:
    """The narrowed claim this PR retired must not survive anywhere."""
    for caveat in ("can return it", "are not covered"):
        assert caveat not in text, f"{where} still says ordinary searches {caveat!r}"


# The retired serving-config caveat, as each doc spelled it: "the filter follows
# the config of the process that answers the query ... needs / set / give that
# config `include_deps: true` there too". Compared lowercased.
_SERVING_CONFIG_CAVEATS = ("process that answers", "answering process", "there too", "that config")


def _states_the_filter_follows_the_bundle(text: str, where: str) -> None:
    """The strict claim holds whatever serves the index (the content gate), and
    the retired advice to repeat ``include_deps: true`` at serve time is gone."""
    assert "whichever config serves the index" in text, (
        f"{where} does not say the filter holds whichever config serves the index"
    )
    lowered = text.lower()
    for caveat in _SERVING_CONFIG_CAVEATS:
        assert caveat not in lowered, f"{where} still carries the serving-config caveat {caveat!r}"


def _states_the_load_time_read(text: str, where: str) -> None:
    """The gate is read when a bundle loads, never per query, so a running server
    whose index gains its first dependency decision (a ``serve --watch`` reindex)
    filters it only after a restart. The user docs say so."""
    assert "when it loads it" in text, f"{where} does not say the server checks each index at load"
    assert "restart" in text, f"{where} does not say a running server needs a restart"


def test_contract_search_codebase_keeps_dependency_decisions_out_of_ordinary_searches() -> None:
    section = _contract_section("### 3.2 `search_codebase`")
    for claim in (
        '`kind="decision"` returns them only when a request asks for them',
        'a `kind="any"` or `"docs"` search — the default and `scope="deps"` included — '
        "returns it only when `package` names that dependency",
        "The filter follows what the loaded bundle holds",
        "a bundle that holds no dependency decision gets no filter",
    ):
        assert claim in section, f"tool-contracts §3.2 does not state {claim!r}"
    _states_no_ordinary_search_caveat(section, "tool-contracts §3.2")
    _states_the_filter_follows_the_bundle(section, "tool-contracts §3.2")


def test_changelog_keeps_dependency_decisions_out_of_ordinary_searches() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    entry = " ".join(changelog.split("- **`decision_capture.include_deps: true`", 1)[1].split())
    entry = entry.split("(#346)", 1)[0]
    assert 'returns it only when `package="<dependency>"` names that dependency' in entry
    _states_no_ordinary_search_caveat(entry, "the CHANGELOG #346 entry")
    _states_the_filter_follows_the_bundle(entry, "the CHANGELOG #346 entry")
    _states_the_load_time_read(entry, "the CHANGELOG #346 entry")


def test_claude_md_describes_the_bundle_content_gate() -> None:
    """The contributor map names the gate the code runs on, not the retired one."""
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    marker = "- **Architectural-decision layer**"
    assert marker in claude_md, "CLAUDE.md lost its decision-layer bullet"
    bullet = claude_md.split(marker, 1)[1].split("\n", 1)[0]
    assert "`ProjectServices.holds_dependency_decisions`" in bullet
    assert "`dependency_decisions_mined`" not in bullet
    lowered = bullet.lower()
    for caveat in _SERVING_CONFIG_CAVEATS:
        assert caveat not in lowered, f"CLAUDE.md still carries the caveat {caveat!r}"


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


def test_include_deps_comment_keeps_ordinary_searches_clean_and_states_the_toggle_cost() -> None:
    comment = _include_deps_comment()
    for claim in (
        'Ordinary kind="any"/"docs" searches',
        "leave them out too, unless package=<dep> names the dependency",
        "re-extracts the project and every dependency once",
        "only the project while `enabled` is false",
    ):
        assert claim in comment, f"default_config.yaml include_deps comment omits {claim!r}"
    _states_no_ordinary_search_caveat(comment, "default_config.yaml include_deps comment")
    _states_the_filter_follows_the_bundle(comment, "default_config.yaml comment")


def test_readme_keeps_ordinary_searches_clean_and_states_the_toggle_cost() -> None:
    paragraph = _readme_dependency_decisions_paragraph()
    for claim in (
        '(`kind="any"` or `"docs"`, the default and `scope="deps"` included) leave '
        "dependency decisions out too",
        'only when `package="<dependency>"` names that dependency',
        "re-indexes your project and each dependency once",
        "only your project while `decision_capture.enabled` is false",
    ):
        assert claim in paragraph, f"README dependency-decision paragraph omits {claim!r}"
    _states_no_ordinary_search_caveat(paragraph, "README dependency-decision paragraph")
    _states_the_filter_follows_the_bundle(paragraph, "README dependency paragraph")
    _states_the_load_time_read(paragraph, "README dependency paragraph")
