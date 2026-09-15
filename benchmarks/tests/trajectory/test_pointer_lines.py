"""The tolerant resolved-call-line parser: what counts as a rendered pointer."""

from __future__ import annotations

from pydocs_eval.trajectory.pointer_lines import (
    PointerCall,
    normalize_tool_name,
    parse_pointer_calls,
)


def test_parses_a_bare_arrow_line() -> None:
    (pointer,) = parse_pointer_calls('→ get_symbol(target="pkg.mod.Cls")')
    assert pointer == PointerCall(tool="get_symbol", arguments=(("target", "pkg.mod.Cls"),))


def test_tolerates_a_group_prefix_before_the_arrow() -> None:
    """A later change puts group labels before the arrow; the parser ignores them."""
    lines = (
        'together: → get_symbol(target="a.B")\nthen:   → get_symbol(target="a.B", depth="source")'
    )
    assert parse_pointer_calls(lines) == (
        PointerCall(tool="get_symbol", arguments=(("target", "a.B"),)),
        PointerCall(tool="get_symbol", arguments=(("depth", "source"), ("target", "a.B"))),
    )


def test_parses_several_pointers_on_one_line() -> None:
    line = 'together: → get_overview()  → search_codebase(query="batch inference")'
    assert parse_pointer_calls(line) == (
        PointerCall(tool="get_overview", arguments=()),
        PointerCall(tool="search_codebase", arguments=(("query", "batch inference"),)),
    )


def test_parses_a_list_argument() -> None:
    (pointer,) = parse_pointer_calls('→ get_context(targets=["a.B", "c.D"])')
    assert pointer.arguments == (("targets", ("a.B", "c.D")),)


def test_argument_order_does_not_change_identity() -> None:
    """Two renderings of one call compare (and hash) equal."""
    (first,) = parse_pointer_calls('→ get_references(target="a.B", direction="callers")')
    (second,) = parse_pointer_calls('→ get_references(direction="callers", target="a.B")')
    assert first == second
    assert len({first, second}) == 1


def test_prose_and_undecodable_lines_are_skipped() -> None:
    text = 'Call get_symbol on the class.\n→ get_symbol(target=<unquoted>)\n→ 9bad(x="y")'
    assert parse_pointer_calls(text) == ()


def test_matches_a_call_with_extra_arguments() -> None:
    """Arguments the pointer never named do not break the match."""
    (pointer,) = parse_pointer_calls('→ get_symbol(target="a.B")')
    assert pointer.matches("get_symbol", {"target": "a.B", "project": "widgetlib"})
    assert not pointer.matches("get_symbol", {"target": "other.C"})
    assert not pointer.matches("get_context", {"target": "a.B"})


def test_matches_a_prefixed_mcp_tool_name() -> None:
    (pointer,) = parse_pointer_calls('→ get_symbol(target="a.B")')
    assert pointer.matches("mcp__pydocs-mcp__get_symbol", {"target": "a.B"})


def test_matches_a_list_argument_recorded_as_a_list() -> None:
    (pointer,) = parse_pointer_calls('→ get_context(targets=["a.B", "c.D"])')
    assert pointer.matches("get_context", {"targets": ["a.B", "c.D"]})
    assert not pointer.matches("get_context", {"targets": ["a.B"]})


def test_normalize_tool_name_strips_only_the_mcp_prefix() -> None:
    assert normalize_tool_name("mcp__pydocs-mcp__get_symbol") == "get_symbol"
    assert normalize_tool_name("get_symbol") == "get_symbol"
    assert normalize_tool_name("Read") == "Read"
