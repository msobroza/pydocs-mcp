"""Next-step pointer tokens + per-surface resolution (spec §D5)."""

from __future__ import annotations

import re

import pytest

from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_members_markdown_within_budget,
    pointer_token,
    resolve_pointers,
    strip_pointers,
)
from pydocs_mcp.application.mcp_inputs import SymbolInput
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
)


def _chunk(title: str, text: str, qualified_name: str = "") -> Chunk:
    # The models expose no ``Chunk.from_metadata`` — mirror the direct
    # constructor tests/application/test_formatting.py uses; qualified_name
    # travels in metadata exactly as the v7 column round-trips it
    # (see storage/sqlite/row_mappers.row_to_chunk).
    metadata: dict[str, object] = {ChunkFilterField.TITLE.value: title}
    if qualified_name:
        metadata["qualified_name"] = qualified_name
    return Chunk(text=text, metadata=metadata)


def test_pointer_token_shape() -> None:
    assert pointer_token("lookup", "pkg.mod.X") == "[[next:lookup:pkg.mod.X]]"


def test_code_backed_chunk_gets_lookup_token() -> None:
    out = format_chunks_markdown_within_budget(
        (_chunk("T", "body", qualified_name="pkg.mod.X"),),
        budget_tokens=500,
    )
    assert "[[next:lookup:pkg.mod.X]]" in out


def test_prose_chunk_gets_no_token() -> None:
    out = format_chunks_markdown_within_budget(
        (_chunk("README", "prose"),),
        budget_tokens=500,
    )
    assert "[[next:" not in out


def test_markdown_heading_chunk_pointer_targets_parent_doc() -> None:
    # Heading chunks persist ``pkg.FILE.md#slug`` qnames (heading_markdown
    # chunker); the ``#slug`` fragment fails SymbolInput's dotted-identifier
    # rule, so a fragment pointer would be a follow-up call the server itself
    # rejects. The emitted pointer must name the parent doc node instead.
    out = format_chunks_markdown_within_budget(
        (_chunk("Install", "body", qualified_name="pkg.README.md#install-steps"),),
        budget_tokens=500,
    )
    assert "[[next:lookup:pkg.README.md]]" in out
    assert "#install-steps" not in out


def test_every_emitted_lookup_pointer_passes_symbol_input_validation() -> None:
    # Response-contract pin: pointers are promised as ready-made calls, so
    # every lookup target a search page emits must survive SymbolInput.
    chunks = (
        _chunk("code", "body", qualified_name="pkg.mod.X"),
        _chunk("doc module", "body", qualified_name="pkg.README.md"),
        _chunk("doc heading", "body", qualified_name="pkg.CLAUDE.md#source-of-truth-spec-md"),
        _chunk("prose", "body"),
    )
    out = format_chunks_markdown_within_budget(chunks, budget_tokens=5000)
    targets = re.findall(r"\[\[next:lookup:([^\]]*)\]\]", out)
    assert len(targets) == 3, out
    for target in targets:
        SymbolInput(target=target)  # must not raise


def test_member_gets_lookup_token_from_module_dot_name() -> None:
    member = ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: "pkg",
            ModuleMemberFilterField.MODULE.value: "pkg.mod",
            ModuleMemberFilterField.NAME.value: "X",
            ModuleMemberFilterField.KIND.value: "class",
            "signature": "()",
            "docstring": "d",
        }
    )
    out = format_members_markdown_within_budget((member,), budget_tokens=500)
    assert "[[next:lookup:pkg.mod.X]]" in out


def test_resolve_mcp_syntax() -> None:
    text = "hit\n[[next:lookup:pkg.mod.X]]\n"
    assert resolve_pointers(text, "mcp") == 'hit\n→ get_symbol(target="pkg.mod.X")\n'


def test_resolve_cli_syntax() -> None:
    text = "hit\n[[next:lookup:pkg.mod.X]]\n"
    assert resolve_pointers(text, "cli") == "hit\n→ pydocs-mcp symbol pkg.mod.X\n"


def test_resolve_show_variants_map_to_new_tools() -> None:
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:callers]]", "mcp") == (
        '→ get_references(target="pkg.mod.X", direction="callers")'
    )
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:impact]]", "cli") == (
        "→ pydocs-mcp refs pkg.mod.X --direction impact"
    )
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:context]]", "mcp") == (
        '→ get_context(targets=["pkg.mod.X"])'
    )
    assert resolve_pointers("[[next:lookup-show:pkg.mod.X:tree]]", "mcp") == (
        '→ get_symbol(target="pkg.mod.X", depth="tree")'
    )


def test_search_action_token() -> None:
    assert pointer_token("search", "retry logic") == "[[next:search:retry logic]]"
    assert resolve_pointers("[[next:search:retry logic]]", "mcp") == (
        '→ search_codebase(query="retry logic")'
    )
    assert resolve_pointers("[[next:search:retry logic]]", "cli") == (
        '→ pydocs-mcp search "retry logic"'
    )


def test_overview_action_token() -> None:
    # get_overview scopes to a package, so the zero-hit-search recovery pointer
    # carries an empty target (spec §D1 empty contract).
    assert pointer_token("overview", "") == "[[next:overview:]]"
    assert resolve_pointers("[[next:overview:]]", "mcp") == "→ get_overview()"
    assert resolve_pointers("[[next:overview:]]", "cli") == "→ pydocs-mcp overview"


def test_overview_action_project_target() -> None:
    # Non-empty target = project selector — the workspace card's per-project
    # deepening pointer (multi-repo empty-selector rendering).
    assert pointer_token("overview", "backend") == "[[next:overview:backend]]"
    assert resolve_pointers("[[next:overview:backend]]", "mcp") == (
        '→ get_overview(project="backend")'
    )
    assert resolve_pointers("[[next:overview:backend]]", "cli") == (
        "→ pydocs-mcp overview --project backend"
    )


def test_strip_restores_pre_pointer_bytes() -> None:
    with_token = "## T\nbody\n[[next:lookup:pkg.mod.X]]\n"
    assert strip_pointers(with_token) == "## T\nbody\n"


def test_strip_removes_overview_token() -> None:
    with_token = "No matches found.\n[[next:overview:]]\n"
    assert strip_pointers(with_token) == "No matches found.\n"


def test_resolve_lookup_show_missing_show_word_does_not_raise() -> None:
    # Indexed chunk content (this repo's own tests/docs) can contain a
    # pointer-shaped literal with no show word — e.g. quoted inside prose
    # or example text, NOT emitted by pointer_token(). The regex's third
    # group is optional (``?``) so ``show`` is ``None`` here; resolving
    # must not KeyError(None) on ``_SHOW_TO_TOOL``. Pin: left verbatim.
    text = "see [[next:lookup-show:x]] for details"
    assert resolve_pointers(text, "mcp") == text
    assert resolve_pointers(text, "cli") == text


def test_resolve_lookup_show_unknown_show_word_does_not_raise() -> None:
    # An indexed chunk can also contain a show word that was never a valid
    # key in ``_SHOW_TO_TOOL`` (e.g. quoted in a test file as a negative
    # example). Resolving must not KeyError('frobnicate'). Pin: verbatim.
    text = "see [[next:lookup-show:x:frobnicate]] for details"
    assert resolve_pointers(text, "mcp") == text
    assert resolve_pointers(text, "cli") == text


@pytest.mark.xfail(
    reason=(
        "Known gap, not fixed here: resolve_pointers has no way to tell a "
        "renderer-emitted token apart from a pointer-SHAPED literal that "
        "arrives as indexed chunk content (e.g. this repo's own source, "
        "once indexed as __project__, quotes '[[next:overview:]]' verbatim "
        "in formatting.py's docstring and in this test file). Fixing this "
        "for real needs a design decision — e.g. escaping/sentinel-marking "
        "tokens at the point formatting.py embeds them into the body, or "
        "switching the token grammar to a marker that can't occur in plain "
        "text — which changes a contract shared across decision_service.py, "
        "envelope.py, multi_project_search.py, symbol_source.py, and "
        "tool_router.py. Pinned here as documented current behavior."
    ),
    strict=True,
)
def test_resolve_does_not_corrupt_valid_pointer_literal_shown_as_source() -> None:
    # A chunk that displays SOURCE CODE containing a valid pointer literal
    # (e.g. this test file's own body, or formatting.py's docstrings, once
    # indexed as __project__) must round-trip byte-for-byte through
    # resolve_pointers — it is content, not a renderer-emitted token.
    literal = 'strip_pointers("[[next:overview:]]")'
    assert resolve_pointers(literal, "mcp") == literal
    assert resolve_pointers(literal, "cli") == literal


def test_pointer_token_with_slash_target_round_trips() -> None:
    """Grammar guard for the WhyInput path relaxation (spec
    2026-07-11-cli-mcp-docs-audit Q1): '/' is not a pointer-token
    delimiter (only ':' and ']' are), so a slash-bearing target must
    survive emit → parse → strip untouched."""
    from pydocs_mcp.application.formatting import (
        _POINTER_RE,
        pointer_token,
        strip_pointers,
    )

    token = pointer_token("search", "src/pydocs_mcp/db.py")
    match = _POINTER_RE.search(token)
    assert match is not None
    assert match.group(2) == "src/pydocs_mcp/db.py"
    assert strip_pointers(f"before {token}\nafter") == "before after"
