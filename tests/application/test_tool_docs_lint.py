"""§D13 docstring contract: six sections, size budgets, cross-references."""

from pydocs_mcp.application.tool_docs import (
    CHARS_PER_TOKEN as _CHARS_PER_TOKEN,
)
from pydocs_mcp.application.tool_docs import (
    PER_TOOL_TOKEN_BUDGET as _PER_TOOL_TOKEN_BUDGET,
)
from pydocs_mcp.application.tool_docs import (
    REQUIRED_MARKERS as _REQUIRED_MARKERS,
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
)


def test_all_six_tools_documented() -> None:
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
    assert total <= _TOTAL_TOKEN_BUDGET, f"surface total {total} tokens > 2400"


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

    assert (CHARS_PER_TOKEN, PER_TOOL_TOKEN_BUDGET, TOTAL_TOKEN_BUDGET) == (4, 500, 2400)
    assert len(REQUIRED_MARKERS) == 5
