"""Cross-language invariants (spec §10): chunker/analyzer drift guard
(AC-5), kind gating parity (AC-19), file-scope attribution (AC-20),
unresolved-emission contract (AC-21), the joinability invariant + dedup
lockstep (AC-22), and the degrade seams (AC-24, AC-25, AC-26)."""

from __future__ import annotations

import logging
import sys
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")
pytest.importorskip("tree_sitter_c")
pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")
pytest.importorskip("tree_sitter_java")

from pydocs_mcp.extraction.pipeline.ingestion import (
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages import ReferenceCaptureStage
from pydocs_mcp.extraction.pipeline.stages import reference_capture as stages_mod
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import analyzer_registry
from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import LANGUAGE_SPECS
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from pydocs_mcp.extraction.strategies.references import ReferenceCollector
from pydocs_mcp.retrieval.config import ReferenceCaptureConfig
from tests.extraction._analyzer_fixtures import (
    ALL_KINDS,
    capture_fixture,
    edge_map,
    resolve_fixture,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def _state(file_contents: tuple[tuple[str, str], ...]) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target=Path(),
            target_kind=TargetKind.PROJECT,
            package_name="pkg",
            root=Path(),
            file_contents=file_contents,
        ),
    )


# ── AC-5: chunker/analyzer extension parity ────────────────────────────────


def test_ac5_treesitter_analyzer_extensions_match_language_specs_exactly():
    """Adding a language to either side alone must fail the suite."""
    assert set(analyzer_registry) - {".py", ".md"} == set(LANGUAGE_SPECS)


# ── AC-19: kind gating parity through the real stage ───────────────────────

_GATING_FILES = (
    ("pkg/u.rs", "use crate::a::B as C;\nimpl S { fn f(&self) { helper(); } }\n"),
    ("pkg/m.c", '#include "graph.h"\nvoid run(void) { tick(); }\n'),
    ("pkg/m.js", "import {X as Y} from './a/b';\nclass A {}\nclass D extends A {}\n"),
    ("pkg/t.ts", "export { X } from './a';\nclass A {}\nclass B extends A {}\n"),
    ("pkg/S.java", "import com.acme.G;\nclass S { void r() { new G(); } }\n"),
)


@pytest.mark.asyncio
async def test_ac19_calls_only_keeps_aliases_and_drops_imports_inherits(monkeypatch):
    monkeypatch.setattr(
        stages_mod,
        "_CAPTURE_CONFIG",
        ReferenceCaptureConfig(enabled=True, kinds=["calls"]),
    )
    new_state = await ReferenceCaptureStage().run(_state(_GATING_FILES))
    kinds = {r.kind for r in new_state.refs.references}
    assert ReferenceKind.IMPORTS not in kinds
    assert ReferenceKind.INHERITS not in kinds
    # Exhaustive: rs helper(), c tick(), java new G() — a duplicated edge,
    # invisible to the kind-set checks above, fails here.
    assert len(new_state.refs.references) == 3
    # Alias tables survive for every aliasing language (D2)…
    aliases = new_state.refs.reference_aliases
    assert aliases["pkg.u.rs"] == {"C": "a.B"}
    assert aliases["pkg.m.js"] == {"Y": "a.b.X"}
    assert aliases["pkg.t.ts"] == {"X": "a.X"}
    assert aliases["pkg.S.java"] == {"G": "com.acme.G"}
    # …and C pins an EMPTY table (includes are not renaming imports, §5.3).
    assert "pkg.m.c" not in aliases


# One source per extension emitting EVERY kind its language supports — the
# per-language gating and degrade pins below would pass vacuously over a
# source that never emits the kind under test. C has no inheritance (§5.3).
_KIND_SOURCES = {
    ".rs": "use crate::a::B as C;\ntrait T: B {}\nimpl S { fn f(&self) { helper(); } }\n",
    ".c": '#include "graph.h"\nvoid run(void) { tick(); }\n',
    ".h": '#include "graph.h"\nvoid run(void) { tick(); }\n',
    ".js": "import {X as Y} from './a/b';\nclass A {}\nclass D extends A { m() { go(); } }\n",
    ".ts": "import {X as Y} from './a/b';\nclass A {}\nclass D extends A { m() { go(); } }\n",
    ".tsx": "import {X as Y} from './a/b';\nclass A {}\nclass D extends A { m() { go(); } }\n",
    ".java": "import com.acme.G;\nclass S extends G { void r() { go(); } }\n",
}
_NO_INHERITANCE_EXTS = frozenset({".c", ".h"})


@pytest.mark.parametrize("ext", sorted(LANGUAGE_SPECS))
def test_kind_sources_emit_every_supported_kind_exactly_once(ext):
    _universe, collector = capture_fixture({f"pkg/k{ext}": _KIND_SOURCES[ext]})
    expected = {"imports": 1, "calls": 1}
    if ext not in _NO_INHERITANCE_EXTS:
        expected["inherits"] = 1
    assert Counter(r.kind.value for r in collector.refs) == expected


@pytest.mark.parametrize("ext", sorted(LANGUAGE_SPECS))
def test_ac19_imports_only_drops_calls_and_inherits_per_language(ext):
    """The gating negative branch at the ANALYZER seam (the stage-level test
    above pins the calls-only direction): with only "imports" allowed, no
    language emits a CALLS or INHERITS row, while its IMPORTS rows and alias
    table are untouched — the table empty for C by design (§5.3)."""
    files = {f"pkg/k{ext}": _KIND_SOURCES[ext]}
    _universe, full = capture_fixture(files)
    _universe, narrowed = capture_fixture(files, allowed=frozenset({"imports"}))
    assert narrowed.refs == [r for r in full.refs if r.kind is ReferenceKind.IMPORTS]
    expected_modules = set() if ext in _NO_INHERITANCE_EXTS else {f"pkg.k{ext}"}
    assert set(narrowed.aliases) == expected_modules
    assert narrowed.aliases == full.aliases


# ── AC-20: file-scope attribution + the module-attributed alias miss ───────


@pytest.mark.parametrize(
    ("relpath", "source", "expected_target"),
    [
        ("pkg/u.rs", "use a::b::D;\nfn f() {}\n", "a.b.D"),
        ("pkg/m.c", '#include "graph.h"\nvoid f(void) {}\n', "graph.h"),
        # .js ESM row: AC-16's require row attributes to the `const P = …`
        # span, so module-qname attribution for a JS ESM import is otherwise
        # unexercised (AC-20 is worded per language).
        ("pkg/m.js", "import {X as Y} from './a/b';\nclass A {}\n", "a.b"),
        ("pkg/m.ts", "import {X as Y} from './a/b';\nclass A {}\n", "a.b"),
        ("pkg/M.java", "import com.acme.G;\nclass M {}\n", "com.acme.G"),
    ],
)
def test_ac20_file_scope_imports_attribute_to_the_module_qname(relpath, source, expected_target):
    _universe, collector = capture_fixture({relpath: source})
    module = relpath.replace("/", ".")  # suffix-preserving module id
    rows = [r for r in collector.refs if r.kind is ReferenceKind.IMPORTS]
    assert [(r.from_node_id, r.to_name) for r in rows] == [(module, expected_target)]


def test_ac20_file_scope_aliased_call_is_expected_none():
    # Module-attributed refs never alias-rewrite: _module_part_of strips the
    # module qname's last segment, mis-keying the alias lookup (§5.1, §11).
    src = "const P = require('./a/b');\nP.init();\nclass A {}\n"
    universe, collector = capture_fixture({"pkg/m.js": src})
    edges = edge_map(resolve_fixture(universe, collector))
    assert edges[("pkg.m.js", "P.init", "calls")] is None


# ── AC-21: unresolved emission + no attribute-type tables ──────────────────


def test_ac21_capture_emits_unresolved_and_no_class_attribute_types():
    files = dict(_GATING_FILES)
    _universe, collector = capture_fixture(files)
    assert collector.refs, "fixtures must emit edges"
    assert len(collector.refs) == 10  # two edges per fixture file, no duplicates
    assert all(r.to_node_id is None for r in collector.refs)
    assert collector.class_attribute_types == {}


# ── AC-22: joinability invariant + dedup lockstep ──────────────────────────

_JOINABILITY_FIXTURES = {
    "pkg/j.rs": "struct Node;\nimpl Node { fn go(&self) { helper(); } }\nfn helper() {}\n",
    "pkg/j.c": '#include "x.h"\nvoid a(void) { b(); }\nvoid b(void) {}\n',
    "pkg/j.js": "import {X as Y} from './a';\nclass A {}\nclass B extends A {}\n",
    "pkg/j.ts": "export { X } from './a';\ninterface I {}\nclass B implements I {}\n",
    "pkg/J.java": "import com.acme.G;\nclass A {}\nclass B extends A { void r() { new A(); } }\n",
}
# Exhaustive per-fixture edge counts: the join check below is per-row, so a
# duplicated edge would still "join" — only a count pin exposes it.
_JOINABILITY_EDGE_COUNTS = {
    "pkg/j.rs": 1,  # Node_2 → helper
    "pkg/j.c": 2,  # include x.h; a → b
    "pkg/j.js": 2,  # import a; B extends A
    "pkg/j.ts": 2,  # re-export a; B implements I
    "pkg/J.java": 3,  # import; B extends A; B → new A()
}


@pytest.mark.parametrize("relpath", sorted(_JOINABILITY_FIXTURES))
def test_ac22_every_from_node_id_joins_the_persisted_tree(relpath):
    universe, collector = capture_fixture({relpath: _JOINABILITY_FIXTURES[relpath]})
    assert collector.refs, relpath
    assert len(collector.refs) == _JOINABILITY_EDGE_COUNTS[relpath], relpath
    for ref in collector.refs:
        assert ref.from_node_id in universe, (relpath, ref.from_node_id)


def test_ac22_dedup_case_keeps_analyzer_and_chunker_in_lockstep():
    # struct Node + impl Node → chunker slugs Node / Node_2 (shared helper);
    # the analyzer's attribution lands on the SAME deduped qname (§4.4).
    universe, collector = capture_fixture({"pkg/j.rs": _JOINABILITY_FIXTURES["pkg/j.rs"]})
    assert {"pkg.j.rs.Node", "pkg.j.rs.Node_2"} <= universe
    call = next(
        r for r in collector.refs if r.to_name == "helper" and r.kind is ReferenceKind.CALLS
    )
    assert call.from_node_id == "pkg.j.rs.Node_2"


# ── AC-24 / AC-25: degrade seams ───────────────────────────────────────────


def test_ac24_blocked_grammar_analyzer_noops_chunker_logs_once(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    collector = ReferenceCollector()
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        analyzer_registry[".rs"].capture(
            "fn f() {}",
            path="pkg/x.rs",
            root=Path(),
            from_package="pkg",
            allowed=ALL_KINDS,
            collector=collector,
        )
    # The analyzer no-ops silently — no rows, no aliases, NO second log (D11).
    assert collector.refs == [] and collector.aliases == {}
    assert not [r for r in caplog.records if "multilang_fallback" in r.getMessage()]
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        node = MultilangChunker().build_tree(
            path="pkg/x.rs", content="fn f() {}", package="pkg", root=Path()
        )
        MultilangChunker().build_tree(
            path="pkg/y.rs", content="fn g() {}", package="pkg", root=Path()
        )
    fallback = [r for r in caplog.records if "multilang_fallback" in r.getMessage()]
    assert len(fallback) == 1  # exactly ONE operator signal per extension
    assert node.qualified_name == "pkg.x.rs"  # file still indexes (text windows)


@pytest.mark.parametrize("ext", sorted(LANGUAGE_SPECS))
def test_ac24_every_treesitter_analyzer_degrades_to_a_silent_noop(ext, monkeypatch):
    """The AC-24 no-op contract holds for EVERY registered tree-sitter
    extension, not just the one the seam was written against: no rows, no
    aliases, no raise — a degraded deployment indexes, it does not crash.
    The sources emit every supported kind when the grammar loads (pinned by
    test_kind_sources_emit_every_supported_kind_exactly_once)."""
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    collector = ReferenceCollector()
    analyzer_registry[ext].capture(
        _KIND_SOURCES[ext],
        path=f"pkg/x{ext}",
        root=Path(),
        from_package="pkg",
        allowed=ALL_KINDS,
        collector=collector,
    )
    assert collector.refs == []
    assert collector.aliases == {}


def test_ac25_blocking_one_grammar_leaves_the_others_functional(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter_rust", None)
    _reset_multilang_caches()
    collector = ReferenceCollector()
    analyzer_registry[".rs"].capture(
        "fn f() { g(); }",
        path="pkg/x.rs",
        root=Path(),
        from_package="pkg",
        allowed=ALL_KINDS,
        collector=collector,
    )
    assert collector.refs == []  # .rs degraded
    analyzer_registry[".c"].capture(
        '#include "g.h"\n',
        path="pkg/m.c",
        root=Path(),
        from_package="pkg",
        allowed=ALL_KINDS,
        collector=collector,
    )
    assert [r.to_name for r in collector.refs] == ["g.h"]  # .c fully functional
    assert analyzer_registry[".rs"].capabilities["references"] == "unavailable"
    assert analyzer_registry[".c"].capabilities["references"] == "syntactic"


# ── AC-26: stage containment on the tree-sitter path ───────────────────────


@pytest.mark.asyncio
async def test_ac26_per_file_containment_on_the_treesitter_path(monkeypatch, caplog):
    # tree-sitter parses broken syntax error-tolerantly (it never raises), so
    # a deterministic fault is injected at the session seam — standing in for
    # the encoding/ABI surprises D11's containment exists for.
    import pydocs_mcp.extraction.strategies.analyzers.rust as rust_mod

    real_open = rust_mod.open_capture_session

    def _exploding_open(source, *, path, root):
        if path.endswith("broken.rs"):
            raise RuntimeError(f"injected parse fault for {path!r}")
        return real_open(source, path=path, root=root)

    monkeypatch.setattr(rust_mod, "open_capture_session", _exploding_open)
    monkeypatch.setattr(
        stages_mod,
        "_CAPTURE_CONFIG",
        ReferenceCaptureConfig(enabled=True, kinds=["calls", "imports", "inherits"]),
    )
    files = (
        ("pkg/broken.rs", "fn broken( {{{\n"),
        ("pkg/ok.rs", "fn f() { g(); }\n"),
    )
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        new_state = await ReferenceCaptureStage().run(_state(files))
    assert any("broken.rs" in r.getMessage() for r in caplog.records)
    assert any(r.to_name == "g" for r in new_state.refs.references)  # ok.rs captured
