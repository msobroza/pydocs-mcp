"""The v2 system prompt, pinned by name after its retirement (spec 2026-07-26 track T4).

v2 was served from its activation until v3 replaced it (issue #379); it stays in
the core pool, renderable by name and serving nobody — the versioning rule never
edits or deletes a shipped ``_vN``. So these pins read ``render_core_prompt(
"system_v2")`` explicitly, never the ACTIVE name, and its bytes keep their own
golden (``tests/fixtures/goldens/ask_your_docs_system_v2.txt``) after the flip.
The served-prompt seam now lives in ``test_system_prompt_v3.py``.

What v2 changed over v1: the nine-tool restatement is gone (the model already
reads every tool description once, from the MCP tool schemas) and the
mutual-context rules take its place — which follow-ups are safe to issue in one
turn, and how to use a result without calling for it again.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydocs_mcp.harness.core.prompt_surfaces import ACTIVE_SYSTEM_PROMPT_TEMPLATE
from pydocs_mcp.harness.core.prompts import render_core_prompt

# The tools the mutual-context rules route work to; every OTHER tool reaches
# the model through its schema alone, which is the whole point of v2.
_ROUTED_TOOLS = ("search_codebase", "get_symbol", "get_context", "grep", "read_file")
_UNROUTED_TOOLS = ("get_overview", "get_references", "get_why", "glob")
_V2_GOLDEN = (
    Path(__file__).resolve().parents[2] / "fixtures" / "goldens" / "ask_your_docs_system_v2.txt"
)


def v2_render() -> str:
    """The v2 render by name — what the model was served while v2 was active."""
    return render_core_prompt("system_v2")


class TestRetiredV2KeepsItsBytes:
    def test_v2_renders_its_own_golden(self) -> None:
        assert render_core_prompt("system_v2") == _V2_GOLDEN.read_bytes().decode("utf-8")

    def test_v2_is_no_longer_the_active_template(self) -> None:
        assert ACTIVE_SYSTEM_PROMPT_TEMPLATE != "system_v2"


class TestMutualContextRules:
    """The rules added in v2 — each one is in the v2 render."""

    def test_together_group_calls_are_safe_in_one_turn(self) -> None:
        v2 = v2_render()
        assert '"Together:" line are' in v2
        assert "issue all of them in ONE turn" in v2
        assert '"Then:" line need the earlier results first' in v2

    def test_the_offered_follow_up_replaces_a_second_search(self) -> None:
        v2 = v2_render()
        assert "Call the follow-up a result offers instead of searching again" in v2

    def test_rendered_content_is_never_fetched_twice(self) -> None:
        assert "Never read or fetch content a result already showed you." in v2_render()

    def test_one_batch_call_beats_a_fan_out(self) -> None:
        v2 = v2_render()
        assert "One call with many targets beats several calls of the same tool" in v2
        assert "get_context takes a list of targets" in v2

    def test_exact_strings_route_to_grep_and_read_file(self) -> None:
        v2 = v2_render()
        assert "goes to grep and\n    read_file" in v2
        assert "ranked or conceptual question goes to search_codebase" in v2

    def test_the_card_comes_before_the_source(self) -> None:
        v2 = v2_render()
        assert "Look at the symbol card first" in v2
        assert 'ask for\n    depth="source" only when the card does not answer' in v2


class TestNoToolRestatement:
    """v2 drops the per-tool restatement — the schemas carry the descriptions."""

    def test_no_backticked_call_signature_survives(self) -> None:
        # v1 opened with nine ``- `tool(arg, arg)` — what it does`` lines.
        v2 = v2_render()
        assert re.search(r"`[a-z_]+\(", v2) is None

    def test_the_v1_signature_lines_are_gone(self) -> None:
        v2 = v2_render()
        for signature in (
            "search_codebase(query, kind, package, scope, limit, project)",
            "get_symbol(target, depth, project)",
            "get_references(target, direction, project)",
            "get_context(targets, project)",
            "get_overview(package, project)",
            "get_why(query, targets, project)",
            "grep(pattern, path, glob, output_mode, scope, project)",
            "glob(pattern, path, project)",
            "read_file(file_path, offset, limit, project)",
        ):
            assert signature not in v2

    def test_only_the_routed_tools_are_named(self) -> None:
        v2 = v2_render()
        assert all(tool in v2 for tool in _ROUTED_TOOLS)
        assert not any(tool in v2 for tool in _UNROUTED_TOOLS)


class TestKeptRules:
    """Rules 1-6 survive verbatim; rule 5 narrows where rule 12 supersedes it."""

    def test_the_six_original_rules_are_kept(self) -> None:
        v2 = v2_render()
        for kept in (
            "search UNSCOPED first",
            "Rewrite follow-up questions into self-contained queries",
            "ONE short clarifying question instead of guessing",
            "Cite the project and package.module for every claim",
            'end with a SHORT\n   "Example" snippet',
            '"[pinned scope: ...]" note set by the app',
        ):
            assert kept in v2

    def test_rule_five_no_longer_sends_the_model_to_the_source_depth(self) -> None:
        """Rule 12 supersedes it: the card carries the signature, so asking for
        the source to read one is the needless call this version removes."""
        v2 = v2_render()
        assert "(use\n   get_symbol when you need the exact signature)" in v2
        assert 'get_symbol with depth="source" when you need the exact signature' not in v2

    def test_the_rules_are_numbered_one_through_twelve(self) -> None:
        v2 = v2_render()
        numbered = re.findall(r"^\s*(\d+)\. ", v2, re.MULTILINE)
        assert numbered == [str(n) for n in range(1, 13)]
