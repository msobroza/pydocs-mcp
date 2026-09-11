"""Cross-language invariants (spec §10): kind gating parity (AC-19),
file-scope attribution (AC-20), unresolved-emission contract (AC-21), the
joinability invariant + dedup lockstep (AC-22), and the degrade seams
(AC-24, AC-25, AC-26). The chunker/analyzer drift guard (AC-5) lives in the
ungated tests/extraction/test_analyzers.py.

Gating is per test, never module-wide: only tests that parse source with a
grammar carry ``_requires_grammars``. The degrade tests block ``tree_sitter``
themselves, so they run — and must pass — where no grammar is installed."""

from __future__ import annotations

import importlib
import logging
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import (
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages import ReferenceCaptureStage
from pydocs_mcp.extraction.pipeline.stages import reference_capture as stages_mod
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import analyzer_registry
from pydocs_mcp.extraction.strategies.analyzers._treesitter import CaptureSession
from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import LANGUAGE_SPECS
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from pydocs_mcp.extraction.strategies.references import ReferenceCollector
from pydocs_mcp.retrieval.config import ReferenceCaptureConfig
from pydocs_mcp.storage.node_reference import NodeReference
from tests.extraction._analyzer_fixtures import (
    capture_fixture,
    capture_with_analyzer,
    edge_map,
    resolve_fixture,
)

_GRAMMAR_STACK = (
    "tree_sitter",
    "tree_sitter_rust",
    "tree_sitter_c",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_java",
)


def _grammar_stack_importable() -> bool:
    try:
        for module_name in _GRAMMAR_STACK:
            importlib.import_module(module_name)
    except ImportError:
        return False
    return True


# The grammar wheels are required deps, but a wheel-less install can still
# lack them, and a module-wide importorskip would also silence the
# grammar-free degrade tests: skip only the tests that need them.
_requires_grammars = pytest.mark.skipif(
    not _grammar_stack_importable(),
    reason="needs tree-sitter + all five grammar wheels (absent on a wheel-less install)",
)


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
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


def _set_capture_kinds(monkeypatch: pytest.MonkeyPatch, kinds: list[str]) -> None:
    """Pin the stage's capture config to exactly ``kinds`` for one test."""
    monkeypatch.setattr(
        stages_mod, "_CAPTURE_CONFIG", ReferenceCaptureConfig(enabled=True, kinds=kinds)
    )


# ── AC-19: kind gating parity through the real stage ───────────────────────

_GATING_FILES = (
    ("pkg/u.rs", "use crate::a::B as C;\nimpl S { fn f(&self) { helper(); } }\n"),
    ("pkg/m.c", '#include "graph.h"\nvoid run(void) { tick(); }\n'),
    ("pkg/m.js", "import {X as Y} from './a/b';\nclass A {}\nclass D extends A {}\n"),
    # An IMPORT, not a re-export: a re-export binds nothing locally and so
    # records no alias, which would leave TypeScript unable to demonstrate the
    # alias-survival claim below.
    ("pkg/t.ts", "import { X } from './a';\nclass A {}\nclass B extends A {}\n"),
    ("pkg/S.java", "import com.acme.G;\nclass S { void r() { new G(); } }\n"),
)
_GATING_PATHS = tuple(relpath for relpath, _source in _GATING_FILES)


def _owning_fixture_file(node_id: str, relpaths: Iterable[str]) -> str:
    """The fixture file whose suffix-preserving module qname (AC-20) is
    ``node_id`` or an ancestor of it. An unattributable id comes back raw,
    so it surfaces as an extra key in the pin instead of vanishing."""
    for relpath in relpaths:
        module = relpath.replace("/", ".")
        if node_id == module or node_id.startswith(f"{module}."):
            return relpath
    return node_id


def _edges_per_fixture_file(
    refs: Iterable[NodeReference], relpaths: tuple[str, ...]
) -> dict[str, int]:
    """Edge count per fixture file, explicit zeros included. A whole-list
    total stays green when one file duplicates an edge while another loses
    one; a per-file pin does not."""
    counts = Counter(_owning_fixture_file(ref.from_node_id, relpaths) for ref in refs)
    return {key: counts[key] for key in sorted({*relpaths, *counts})}


# Exhaustive per file under calls-only: rs helper(), c tick(), java new G();
# the js/ts fixtures carry no call. A duplicated edge is invisible to the
# kind-set checks, and a whole-list total misses one duplicated edge plus one
# lost edge. This pin catches both.
_CALLS_ONLY_EDGE_COUNTS = {
    "pkg/u.rs": 1,
    "pkg/m.c": 1,
    "pkg/m.js": 0,
    "pkg/t.ts": 0,
    "pkg/S.java": 1,
}


@_requires_grammars
@pytest.mark.asyncio
async def test_ac19_calls_only_keeps_aliases_and_drops_imports_inherits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_capture_kinds(monkeypatch, ["calls"])
    new_state = await ReferenceCaptureStage().run(_state(_GATING_FILES))
    kinds = {r.kind for r in new_state.refs.references}
    assert ReferenceKind.IMPORTS not in kinds
    assert ReferenceKind.INHERITS not in kinds
    per_file = _edges_per_fixture_file(new_state.refs.references, _GATING_PATHS)
    assert per_file == _CALLS_ONLY_EDGE_COUNTS
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
_C_KIND_SOURCE = '#include "graph.h"\nvoid run(void) { tick(); }\n'
_ESM_KIND_SOURCE = (
    "import {X as Y} from './a/b';\nclass A {}\nclass D extends A { m() { go(); } }\n"
)
_KIND_SOURCES = {
    ".rs": "use crate::a::B as C;\ntrait T: B {}\nimpl S { fn f(&self) { helper(); } }\n",
    ".c": _C_KIND_SOURCE,
    ".h": _C_KIND_SOURCE,
    ".js": _ESM_KIND_SOURCE,
    ".ts": _ESM_KIND_SOURCE,
    ".tsx": _ESM_KIND_SOURCE,
    ".java": "import com.acme.G;\nclass S extends G { void r() { go(); } }\n",
}
_NO_INHERITANCE_EXTS = frozenset({".c", ".h"})
# Includes are not renaming imports (§5.3): C modules keep an EMPTY alias table.
_NO_ALIAS_EXTS = frozenset({".c", ".h"})


@_requires_grammars
@pytest.mark.parametrize("ext", sorted(LANGUAGE_SPECS))
def test_kind_sources_emit_every_supported_kind_exactly_once(ext: str) -> None:
    _universe, collector = capture_fixture({f"pkg/k{ext}": _KIND_SOURCES[ext]})
    expected = {"imports": 1, "calls": 1}
    if ext not in _NO_INHERITANCE_EXTS:
        expected["inherits"] = 1
    assert Counter(r.kind.value for r in collector.refs) == expected


@_requires_grammars
@pytest.mark.parametrize("ext", sorted(LANGUAGE_SPECS))
def test_ac19_imports_only_drops_calls_and_inherits_per_language(ext: str) -> None:
    """The gating negative branch at the ANALYZER seam (the stage-level test
    above pins the calls-only direction): with only "imports" allowed, no
    language emits a CALLS or INHERITS row, while its IMPORTS rows and alias
    table are untouched — the table empty for C by design (§5.3)."""
    files = {f"pkg/k{ext}": _KIND_SOURCES[ext]}
    _universe, full = capture_fixture(files)
    _universe, narrowed = capture_fixture(files, allowed=frozenset({"imports"}))
    assert narrowed.refs == [r for r in full.refs if r.kind is ReferenceKind.IMPORTS]
    expected_modules = set() if ext in _NO_ALIAS_EXTS else {f"pkg.k{ext}"}
    assert set(narrowed.aliases) == expected_modules
    assert narrowed.aliases == full.aliases


# ── AC-20: file-scope attribution + the module-attributed alias miss ───────


@_requires_grammars
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
def test_ac20_file_scope_imports_attribute_to_the_module_qname(
    relpath: str, source: str, expected_target: str
) -> None:
    _universe, collector = capture_fixture({relpath: source})
    module = relpath.replace("/", ".")  # suffix-preserving module id
    rows = [r for r in collector.refs if r.kind is ReferenceKind.IMPORTS]
    assert [(r.from_node_id, r.to_name) for r in rows] == [(module, expected_target)]


@_requires_grammars
def test_ac20_file_scope_aliased_call_is_expected_none() -> None:
    # Module-attributed refs never alias-rewrite: _module_part_of strips the
    # module qname's last segment, mis-keying the alias lookup (§5.1, §11).
    src = "const P = require('./a/b');\nP.init();\nclass A {}\n"
    universe, collector = capture_fixture({"pkg/m.js": src})
    edges = edge_map(resolve_fixture(universe, collector))
    assert edges[("pkg.m.js", "P.init", "calls")] is None


# ── AC-21: unresolved emission + no attribute-type tables ──────────────────


@_requires_grammars
def test_ac21_capture_emits_unresolved_and_no_class_attribute_types() -> None:
    files = dict(_GATING_FILES)
    _universe, collector = capture_fixture(files)
    assert collector.refs, "fixtures must emit edges"
    # Two edges per fixture file, no duplicates: rs import + call, c include
    # + call, js import + extends, ts re-export + extends, java import + call.
    assert _edges_per_fixture_file(collector.refs, _GATING_PATHS) == {
        "pkg/u.rs": 2,
        "pkg/m.c": 2,
        "pkg/m.js": 2,
        "pkg/t.ts": 2,
        "pkg/S.java": 2,
    }
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


@_requires_grammars
@pytest.mark.parametrize("relpath", sorted(_JOINABILITY_FIXTURES))
def test_ac22_every_from_node_id_joins_the_persisted_tree(relpath: str) -> None:
    universe, collector = capture_fixture({relpath: _JOINABILITY_FIXTURES[relpath]})
    assert collector.refs, relpath
    assert len(collector.refs) == _JOINABILITY_EDGE_COUNTS[relpath], relpath
    for ref in collector.refs:
        assert ref.from_node_id in universe, (relpath, ref.from_node_id)


@_requires_grammars
def test_ac22_dedup_case_keeps_analyzer_and_chunker_in_lockstep() -> None:
    # struct Node + impl Node → chunker slugs Node / Node_2 (shared helper);
    # the analyzer's attribution lands on the SAME deduped qname (§4.4).
    universe, collector = capture_fixture({"pkg/j.rs": _JOINABILITY_FIXTURES["pkg/j.rs"]})
    assert {"pkg.j.rs.Node", "pkg.j.rs.Node_2"} <= universe
    helper_call_sources = [
        r.from_node_id
        for r in collector.refs
        if r.to_name == "helper" and r.kind is ReferenceKind.CALLS
    ]
    assert helper_call_sources == ["pkg.j.rs.Node_2"]


# ── AC-24 / AC-25: degrade seams ───────────────────────────────────────────


def test_ac24_blocked_grammar_analyzer_noops_chunker_logs_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    collector = ReferenceCollector()
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        capture_with_analyzer("pkg/x.rs", "fn f() {}", collector)
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
def test_ac24_every_treesitter_analyzer_degrades_to_a_silent_noop(
    ext: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AC-24 no-op contract holds for EVERY registered tree-sitter
    extension, not just the one the seam was written against: no rows, no
    aliases, no raise — a degraded deployment indexes, it does not crash.
    The sources emit every supported kind when the grammar loads (pinned by
    test_kind_sources_emit_every_supported_kind_exactly_once)."""
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    collector = ReferenceCollector()
    capture_with_analyzer(f"pkg/x{ext}", _KIND_SOURCES[ext], collector)
    assert collector.refs == []
    assert collector.aliases == {}


@_requires_grammars
def test_ac25_blocking_one_grammar_leaves_the_others_functional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "tree_sitter_rust", None)
    _reset_multilang_caches()
    collector = ReferenceCollector()
    capture_with_analyzer("pkg/x.rs", "fn f() { g(); }", collector)
    assert collector.refs == []  # .rs degraded
    capture_with_analyzer("pkg/m.c", '#include "g.h"\n', collector)
    assert [r.to_name for r in collector.refs] == ["g.h"]  # .c fully functional
    assert analyzer_registry[".rs"].capabilities["references"] == "unavailable"
    assert analyzer_registry[".c"].capabilities["references"] == "syntactic"


# ── AC-26: stage containment on the tree-sitter path ───────────────────────

_SessionOpener = Callable[..., CaptureSession | None]


def _open_failing_on_broken_rs(real_open: _SessionOpener) -> _SessionOpener:
    """``open_capture_session`` stand-in that raises for ``broken.rs`` only."""

    def _exploding_open(source: str, *, path: str, root: Path) -> CaptureSession | None:
        if path.endswith("broken.rs"):
            raise RuntimeError(f"injected parse fault for {path!r}")
        return real_open(source, path=path, root=root)

    return _exploding_open


@_requires_grammars
@pytest.mark.asyncio
async def test_ac26_per_file_containment_on_the_treesitter_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # tree-sitter parses broken syntax error-tolerantly (it never raises), so
    # a deterministic fault is injected at the session seam — standing in for
    # the encoding/ABI surprises D11's containment exists for.
    import pydocs_mcp.extraction.strategies.analyzers.rust as rust_mod

    exploding_open = _open_failing_on_broken_rs(rust_mod.open_capture_session)
    monkeypatch.setattr(rust_mod, "open_capture_session", exploding_open)
    _set_capture_kinds(monkeypatch, ["calls", "imports", "inherits"])
    files = (
        ("pkg/broken.rs", "fn broken( {{{\n"),
        ("pkg/ok.rs", "fn f() { g(); }\n"),
    )
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        new_state = await ReferenceCaptureStage().run(_state(files))
    # The stage's own containment warning (reference_capture.py), not merely
    # any record echoing the injected fault's text.
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("reference_capture failed on pkg/broken.rs:") for m in messages)
    assert any(r.to_name == "g" for r in new_state.refs.references)  # ok.rs captured
