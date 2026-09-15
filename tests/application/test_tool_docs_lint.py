"""The description-document lint: labels, redirects, budgets, cross-references.

Two families of check live here. The older one (§D13) pins the section
structure and the character-estimate budgets the loader itself enforces. The
newer one, at the bottom of the file, is the structural lint of issue #281:
the four labels in order, a not-when line that names somewhere else to go,
and runaway word ceilings.
"""

from pydocs_mcp.application.tool_docs import (
    CHARS_PER_TOKEN as _CHARS_PER_TOKEN,
)
from pydocs_mcp.application.tool_docs import (
    PER_TOOL_TOKEN_BUDGET as _PER_TOOL_TOKEN_BUDGET,
)
from pydocs_mcp.application.tool_docs import (
    PER_TOOL_WORD_CEILING as _PER_TOOL_WORD_CEILING,
)
from pydocs_mcp.application.tool_docs import (
    REQUIRED_MARKERS as _REQUIRED_MARKERS,
)
from pydocs_mcp.application.tool_docs import (
    SERVER_INSTRUCTIONS_WORD_CEILING as _SERVER_INSTRUCTIONS_WORD_CEILING,
)
from pydocs_mcp.application.tool_docs import (
    SERVER_INSTRUCTIONS,
    TOOL_DOCS,
)
from pydocs_mcp.application.tool_docs import (
    TOTAL_TOKEN_BUDGET as _TOTAL_TOKEN_BUDGET,
)

_TOOLS = (
    "get_overview",
    "search_codebase",
    "get_symbol",
    "get_context",
    "get_references",
    "get_why",
    "grep",
    "glob",
    "read_file",
)


def test_all_nine_tools_documented() -> None:
    assert set(TOOL_DOCS) == set(_TOOLS)


def test_each_doc_has_required_sections() -> None:
    for name, doc in TOOL_DOCS.items():
        for marker in _REQUIRED_MARKERS:
            assert marker in doc, f"{name} missing section {marker!r}"


def test_batching_guidance_where_targets_exist() -> None:
    for name in ("get_context", "get_why"):
        assert "ONE call" in TOOL_DOCS[name], f"{name} must carry batching guidance"


def test_size_budgets() -> None:
    total = 0
    for name, doc in TOOL_DOCS.items():
        tokens = len(doc) // _CHARS_PER_TOKEN
        assert tokens <= _PER_TOOL_TOKEN_BUDGET, f"{name}: {tokens} tokens > 500"
        total += tokens
    assert total <= _TOTAL_TOKEN_BUDGET, f"surface total {total} tokens > 3600"


def test_docs_reference_sibling_tools_not_old_surface() -> None:
    joined = "\n".join(TOOL_DOCS.values()) + SERVER_INSTRUCTIONS
    assert "lookup(" not in joined and 'show="' not in joined


def test_project_scoped_example_everywhere() -> None:
    for name, doc in TOOL_DOCS.items():
        assert 'project="' in doc, f"{name} missing a project= example"


def test_contract_constants_are_importable_and_pinned() -> None:
    from pydocs_mcp.application.tool_docs import (
        CHARS_PER_TOKEN,
        PER_TOOL_TOKEN_BUDGET,
        REQUIRED_MARKERS,
        TOTAL_TOKEN_BUDGET,
    )

    assert (CHARS_PER_TOKEN, PER_TOOL_TOKEN_BUDGET, TOTAL_TOKEN_BUDGET) == (4, 500, 3600)
    assert REQUIRED_MARKERS == ("When to use", "When NOT to use", "Arguments", "Examples")


def test_get_context_example_targets_a_symbol_not_a_module() -> None:
    """get_context rejects module targets, so a module example advertised a
    call that always failed (`_resolve_context_target` in lookup_service)."""
    doc = TOOL_DOCS["get_context"]
    assert 'get_context(targets=["pydocs_mcp.retrieval.pipeline"])' not in doc
    assert 'get_context(targets=["pydocs_mcp.retrieval.pipeline.base.RetrieverPipeline"])' in doc


def test_grep_doc_states_ripgrep_glob_anchoring() -> None:
    """grep's glob follows `rg --glob` anchoring; the MCP-visible description
    has to say so, because the shipped example `glob="*.py"` reads as
    root-anchored POSIX glob otherwise."""
    doc = TOOL_DOCS["grep"]
    assert "any depth" in doc
    assert "anchors at the root" in doc
    assert "trailing" in doc


def test_get_references_doc_states_module_target_behaviour() -> None:
    """A module target answers the import graph rather than the call graph;
    the asymmetry between callers and impact is not guessable from the name."""
    doc = TOOL_DOCS["get_references"]
    assert "module target" in doc
    assert "import graph" in doc


# ── structural lint (issue #281) ──────────────────────────────────────────
#
# The four labels carry the decision content of a tool section: when to call
# it, when to call something else, which arguments do not behave the way the
# name suggests, and what a call looks like. Order is fixed so a reader (and a
# diff) finds the same answer in the same place in all nine sections.
#
# There is deliberately NO token-reduction target here: sections may grow
# where decision content requires it. The word ceilings below are runaway
# guards — a future edit cannot quietly double every session's cost.


# Named views on the ordered label tuple, so the lint never re-spells a label
# the product document owns.
_, _WHEN_NOT_TO_USE, _ARGUMENTS, _ = _REQUIRED_MARKERS


def _label_positions(body: str) -> list[int]:
    return [body.index(label) for label in _REQUIRED_MARKERS]


def _not_when_line(body: str) -> str:
    """The text between the not-when label and the next label."""
    start = body.index(_WHEN_NOT_TO_USE)
    return body[start : body.index(_ARGUMENTS, start)]


def test_every_tool_section_carries_the_four_labels_in_order() -> None:
    for name, doc in TOOL_DOCS.items():
        for label in _REQUIRED_MARKERS:
            assert label in doc, f"{name} missing label {label!r}"
        positions = _label_positions(doc)
        assert positions == sorted(positions), (
            f"{name} labels out of order — expected {list(_REQUIRED_MARKERS)}, "
            f"got offsets {positions}"
        )


def test_not_when_line_names_an_alternative_tool() -> None:
    """A redirect is only actionable when it names where to go instead."""
    for name, doc in TOOL_DOCS.items():
        others = [other for other in _TOOLS if other != name]
        line = _not_when_line(doc)
        assert any(other in line for other in others), (
            f"{name}: 'When NOT to use' names no alternative tool — {line!r}"
        )


def test_tool_sections_stay_under_the_runaway_word_ceiling() -> None:
    for name, doc in TOOL_DOCS.items():
        words = len(doc.split())
        assert words <= _PER_TOOL_WORD_CEILING, (
            f"{name}: {words} words > {_PER_TOOL_WORD_CEILING} ceiling"
        )


def test_server_instructions_stay_under_the_runaway_word_ceiling() -> None:
    words = len(SERVER_INSTRUCTIONS.split())
    assert words <= _SERVER_INSTRUCTIONS_WORD_CEILING, (
        f"server instructions: {words} words > {_SERVER_INSTRUCTIONS_WORD_CEILING} ceiling"
    )


def test_shared_lines_live_only_in_the_server_instructions() -> None:
    """The workflow and the response contract are read once, not nine times."""
    for name, doc in TOOL_DOCS.items():
        assert "Workflow:" not in doc, f"{name} repeats the workflow line"
        assert "Response contract:" not in doc, f"{name} repeats the response contract"
    assert "Workflow:" in SERVER_INSTRUCTIONS
    assert "Response contract:" in SERVER_INSTRUCTIONS


def test_server_instructions_carry_the_mutual_context_rules() -> None:
    """Rules an agent needs to use one response to shape the next call."""
    text = SERVER_INSTRUCTIONS
    assert "[index:" in text and "stale" in text  # freshness line semantics
    assert '"Together:"' in text and '"Then:"' in text  # independent vs dependent
    assert "instead of searching again" in text  # follow the pointer
    assert "already showed you" in text  # never re-read
    assert "many targets" in text  # batch over fan-out
    assert "goes to grep and read_file" in text  # exact strings
    assert "goes to search_codebase" in text  # ranked questions
    assert "symbol card" in text  # card before source
