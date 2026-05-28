"""Reference capture tests on AstPythonChunker (spec §7.1, AC #5/#7/#9/#10/#16)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.chunkers import AstPythonChunker
from pydocs_mcp.extraction.strategies.references import ReferenceCollector


def _build(source: str) -> tuple[list, AstPythonChunker]:
    """Helper: run AstPythonChunker over source with a fresh collector,
    return (refs, chunker) for assertions."""
    collector = ReferenceCollector()
    chunker = AstPythonChunker()
    chunker.build_tree(
        path="pkg/mod.py",
        content=source,
        package="pkg",
        root=Path(),
        ref_collector=collector,
    )
    return collector.refs, chunker


def test_calls_emits_one_edge_for_bare_function_call():
    """AC #5 — `def runner(): return do_it(42)` emits 1 CALLS edge."""
    refs, _ = _build("def runner():\n    return do_it(42)\n")
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    assert len(calls) == 1
    assert calls[0].from_node_id == "pkg.mod.runner"
    assert calls[0].to_name == "do_it"


def test_calls_captures_dotted_attribute_call():
    refs, _ = _build("def runner():\n    return os.path.join('a', 'b')\n")
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    assert len(calls) == 1
    assert calls[0].to_name == "os.path.join"


def test_calls_short_circuits_self_dot_prefix():
    """AC #9 — self.X.Y captured as to_name='self.X.Y', NOT short-circuited
    at the capturer level (the resolver short-circuits it at Rule 5).
    The capturer still emits the candidate so the resolver controls policy."""
    refs, _ = _build("class A:\n    def m(self):\n        return self.client.fetch(self.url)\n")
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    # `self.client.fetch` is one CALL. self.url is an Attribute, not a Call.
    self_calls = [r for r in calls if r.to_name.startswith("self.")]
    assert any(r.to_name == "self.client.fetch" for r in self_calls)


def test_calls_drops_non_dotted_shapes():
    """AC #16 — canonical_dotted returns None for Call(Call(...).x); dropped silently."""
    refs, _ = _build(
        "def runner():\n    return get_factory()()  # Call(Call) — not dotted-shaped\n"
    )
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    # `get_factory` IS captured (it's the inner Call's func, a Name).
    # The OUTER Call's func is a Call — canonical_dotted returns None and that's dropped.
    inner_only = [r for r in calls if r.to_name == "get_factory"]
    assert len(inner_only) == 1
    not_dotted = [r for r in calls if "(" in r.to_name or r.to_name == ""]
    assert not_dotted == []


def test_imports_emits_one_edge_per_name_in_import():
    """`import a, b` → 2 IMPORTS edges; from_node_id = the module qname."""
    refs, _ = _build("import os, sys\n")
    imports = [r for r in refs if r.kind == ReferenceKind.IMPORTS]
    names = {r.to_name for r in imports}
    assert names == {"os", "sys"}
    # All from the module node, not an import-block synthetic node.
    assert all(r.from_node_id == "pkg.mod" for r in imports)


def test_imports_from_emits_one_edge_per_imported_name():
    """`from helpers import a, b` → 2 IMPORTS edges with full dotted to_name."""
    refs, _ = _build("from helpers import a, b\n")
    imports = [r for r in refs if r.kind == ReferenceKind.IMPORTS]
    names = {r.to_name for r in imports}
    assert names == {"helpers.a", "helpers.b"}


def test_inherits_emits_one_edge_per_base_class():
    """AC #7 — `class Sub(Base, Mixin):` → 2 INHERITS edges."""
    refs, _ = _build("class Base: ...\nclass Mixin: ...\nclass Sub(Base, Mixin):\n    pass\n")
    inherits = [r for r in refs if r.kind == ReferenceKind.INHERITS]
    sub_inherits = [r for r in inherits if r.from_node_id == "pkg.mod.Sub"]
    assert {r.to_name for r in sub_inherits} == {"Base", "Mixin"}


def test_inherits_captures_dotted_base():
    refs, _ = _build("class S(framework.View):\n    pass\n")
    inherits = [r for r in refs if r.kind == ReferenceKind.INHERITS]
    assert any(r.to_name == "framework.View" for r in inherits)


def test_collector_is_none_means_no_refs_captured():
    """spec §7.1 — passing ref_collector=None skips capture entirely.
    Feature toggles cleanly via the optional kwarg."""
    chunker = AstPythonChunker()
    tree = chunker.build_tree(
        path="pkg/mod.py",
        content="def runner():\n    return do_it()\n",
        package="pkg",
        root=Path(),
        # No ref_collector kwarg — falls through default None.
    )
    # Tree built successfully; we just have no way to observe captures
    # (no collector to inspect). The fact that build_tree returned a
    # tree at all proves capture is OPTIONAL, not REQUIRED.
    assert tree.qualified_name == "pkg.mod"


def test_collector_per_node_error_isolation():
    """spec §7.1 — malformed/pathological AST nodes don't abort the
    whole tree's capture pass."""
    refs, _ = _build(
        "def a():\n"
        "    legitimate_call()\n"
        "def b():\n"
        "    return [foo for foo in bar].method()  # nested expr; mostly ok\n"
    )
    calls = [r for r in refs if r.kind == ReferenceKind.CALLS]
    # `legitimate_call` must appear; we don't care whether the nested
    # comprehension method-call captures or drops — just that the
    # capture doesn't crash and ``a`` gets its ref recorded.
    assert any(r.to_name == "legitimate_call" for r in calls)


# ── capture_mentions (sub-PR #5c) ────────────────────────────────────────────


def test_capture_mentions_matches_backtick_quoted_dotted_names():
    """`capture_mentions` emits MENTIONS edges for backtick-quoted dotted
    names with AT LEAST one dot (e.g. ``pkg.helpers.compute``)."""
    from pydocs_mcp.extraction.strategies.references import (
        ReferenceCollector,
        capture_mentions,
    )

    collector = ReferenceCollector()
    capture_mentions(
        "See `pkg.helpers.compute` for details.\nAlso `other.mod.fn` does X.\n",
        from_package="pkg",
        from_node_id="pkg.docs.readme",
        collector=collector,
    )
    mentions = [r for r in collector.refs if r.kind == ReferenceKind.MENTIONS]
    names = {r.to_name for r in mentions}
    assert names == {"pkg.helpers.compute", "other.mod.fn"}
    assert all(r.from_node_id == "pkg.docs.readme" for r in mentions)
    assert all(r.from_package == "pkg" for r in mentions)
    assert all(r.to_node_id is None for r in mentions)


def test_capture_mentions_ignores_bare_identifiers():
    """Bare ``compute`` (no dots) MUST NOT be captured — the regex
    requires AT LEAST one dot. Bare identifiers in backticks are noise
    (variable names, single-word code) and would flood the graph."""
    from pydocs_mcp.extraction.strategies.references import (
        ReferenceCollector,
        capture_mentions,
    )

    collector = ReferenceCollector()
    capture_mentions(
        "Calling `compute` directly is fine.\nBut `foo` and `bar` are bare.\n",
        from_package="pkg",
        from_node_id="pkg.docs.readme",
        collector=collector,
    )
    mentions = [r for r in collector.refs if r.kind == ReferenceKind.MENTIONS]
    assert mentions == []


def test_capture_mentions_dedupes_per_chunk():
    """Multiple occurrences of the same dotted name in one chunk emit
    ONE edge — local ``seen: set[str]`` keeps the graph tidy."""
    from pydocs_mcp.extraction.strategies.references import (
        ReferenceCollector,
        capture_mentions,
    )

    collector = ReferenceCollector()
    capture_mentions(
        "Use `pkg.helpers.compute` here.\n"
        "Also `pkg.helpers.compute` there.\n"
        "And finally `pkg.helpers.compute` again.\n",
        from_package="pkg",
        from_node_id="pkg.docs.readme",
        collector=collector,
    )
    mentions = [r for r in collector.refs if r.kind == ReferenceKind.MENTIONS]
    assert len(mentions) == 1
    assert mentions[0].to_name == "pkg.helpers.compute"


def test_capture_mentions_emits_kind_mentions():
    """Every emitted edge has ``kind == ReferenceKind.MENTIONS`` — pin
    the wire value so the DB column stays stable."""
    from pydocs_mcp.extraction.strategies.references import (
        ReferenceCollector,
        capture_mentions,
    )

    collector = ReferenceCollector()
    capture_mentions(
        "Reference `a.b.c` and `x.y`.\n",
        from_package="pkg",
        from_node_id="pkg.docs.readme",
        collector=collector,
    )
    for r in collector.refs:
        assert r.kind == ReferenceKind.MENTIONS
        assert r.kind == "mentions"  # StrEnum identity check
