# Multilanguage Reference Analyzers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Extend the reference graph (CALLS / INHERITS / IMPORTS edges + alias tables) from Python-only capture to seven code extensions (`.rs .c .h .js .ts .tsx .java`) via per-language tree-sitter analyzers behind the frozen `LanguageAnalyzer` seam, exactly as ratified in the spec's twelve decisions D1–D12 and 36 acceptance criteria.

**Architecture:** `analyzers.py` becomes the package `extraction/strategies/analyzers/` (same import path); `_treesitter.py` holds shared plumbing (a `ReferenceQueryRole` StrEnum, the two D7 capability states, an `(ext, role)` compiled-query cache joined to the chunker's reset seam, and a top-level span bisect index whose qname assignment is a single helper shared with the chunker — joinability by construction). Five language modules register seven extensions; `ReferenceResolver` is byte-untouched; packaging promotes tree-sitter + five grammar wheels to required deps under the owner's footprint waiver; project-scope discovery defaults gain the code extensions; a loadable-grammar salt joins the package content hash; ADR 0022 carries the `docs/tool-contracts.md` §2.2/§4.1/§5.1 amendments; version 0.7.0.

**Tech Stack:** Python 3.11+, `tree-sitter>=0.25,<0.26` + official MIT grammar wheels (`tree-sitter-rust`, `-c`, `-javascript`, `-typescript`, `-java`), pydantic-settings (`AppConfig`), SQLite (`node_references` DDL unchanged), pytest.

## Global Constraints

- **The spec is authoritative:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/elated-joliot-4853d7/docs/superpowers/specs/2026-07-29-multilang-reference-analyzers-design.md` (commit 605027c, PR #225). Implement it exactly — no relitigating D1–D12, no scope additions. When this plan and the spec disagree, the spec wins.
- **Nine-tool MCP surface untouched:** no new tool, no new parameter, no envelope field. `docs/tool-contracts.md` edits are limited to the three §7.4 amendments carried by ADR 0022 and flagged for owner ratification in the PR description.
- **`ReferenceResolver` is byte-untouched** (`python/pydocs_mcp/extraction/strategies/reference_resolver.py`). The four D8 canonical multi-segment alias examples are pinned **expected-None** at resolution; per-language fixtures enumerate must-resolve vs expected-None edges exactly as AC-13..AC-17 state.
- **Git authorship:** commits authored solely by the repo's configured user; NEVER add `Co-Authored-By:` trailers; never pass `--author`; never sign (`-S`) unless asked. Every commit step below shows a plain `git commit` command. Explicit per-file `git add` — never `git add -A`.
- **Relock ONLY with `~/.local/bin/uv`** (the anaconda `uv` churns platform markers in `uv.lock`).
- **pip-audit requirement-mode SIGABRTs under the sandbox** — use `.venv/bin/pip-audit --strict --local` on the frozen venv instead of the CI's requirement-file mode.
- **Local complexipy runs rewrite `complexipy-snapshot.json` in place** — after running complexipy locally, restore the snapshot from HEAD (`git checkout -- complexipy-snapshot.json`) before staging; never sweep it in via `git add -A`.
- **Full CI gate set before any push** (run ALL of these):
  ```bash
  ruff format --check python/ tests/ benchmarks/
  ruff check python/ tests/ benchmarks/
  mypy python/pydocs_mcp
  complexipy python/pydocs_mcp --max-complexity-allowed 15
  vulture python/pydocs_mcp --min-confidence 80
  pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90
  uv lock --check
  ```
- **Code shape rules:** functions 4–20 lines; files <500 lines; max 2 indent levels (early returns / guard clauses); closed string vocabularies are `enum.StrEnum` with UPPER_SNAKE members, never bare `Literal` aliases; single-source defaults via `_DEFAULT_X` module constants; comments explain WHY; every new function gets a test; error messages carry the offending value and expected shape.
- **Repo/cwd for ALL tasks:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/elated-joliot-4853d7` (the worktree carrying spec commit 605027c). All paths below are relative to it. Run pytest from the repo root with the worktree venv (`.venv/bin/pytest` or an activated venv).
- **Environment bootstrap (do this FIRST, before Task 1):** at plan time the worktree has NO `.venv` of its own — only the main checkout (`/Users/msobroza/Projects/pyctx7-mcp/.venv`) has one, and that is an editable install of the MAIN tree, never usable for worktree work. Create a worktree-local venv and install the project plus the gate tooling into it:

  ```bash
  python3.12 -m venv .venv        # any CPython ≥3.11 works
  .venv/bin/pip install -e '.[multilang]' pytest pytest-asyncio pytest-cov \
      ruff mypy complexipy vulture pip-audit
  ```

  Every `.venv/bin/*` command in this plan (the pytest runs throughout, Task 8 Step 1, Task 10 Step 6, Task 14 Step 1) refers to THIS worktree venv.
- **Grammar availability during development:** the bootstrap above installs the `[multilang]` extra needed for Tasks 2–9; Task 8 additionally installs `tree-sitter-java` into the venv WITHOUT touching `pyproject.toml` (the single pyproject change + single relock happens in Task 10). Every grammar-dependent test file gates itself with `pytest.importorskip`, so a wheel-less venv skips rather than fails.

## Rollout order

Tasks are strictly ordered. Task 1 (pure package move) must land before anything touches the seam. Task 2 (shared plumbing + the chunker-side qname hoist) is the foundation for every language module. Task 3 (capabilities property) precedes the first tree-sitter analyzer because the analyzers are property-shaped from birth. Tasks 4–8 add one language each (Rust → C → JavaScript → TypeScript → Java); each removes its extension from the router test's "unavailable" parametrize the moment its analyzer registers. Task 9 adds the cross-language invariants (exact registry set, drift guard, joinability, degrade, router two-state) that only make sense once all seven extensions are live. Tasks 10–12 are packaging/defaults/migration (pyproject relock happens exactly once, in Task 10). Task 13 is the docs/contract/ADR/version wave; Task 14 is the full-gate verification run and AC cross-off.

---

## Task 1 — Package conversion: `analyzers.py` → `analyzers/__init__.py` (pure move, zero behavior change)

**Files:**
- Move: `python/pydocs_mcp/extraction/strategies/analyzers.py` → `python/pydocs_mcp/extraction/strategies/analyzers/__init__.py` (git mv, byte-identical content)
- Test (modify): `tests/extraction/test_analyzers.py` (one new AC-1 test appended; nothing else changes)

**Interfaces:**
- Consumes: the current 291-line `analyzers.py` (seam: `LanguageCapabilities`, `LanguageAnalyzer`, `analyzer_registry`, `register_analyzer`, `language_capabilities`, `PYTHON_CAPABILITIES`, `MARKDOWN_CAPABILITIES`, `PythonAstAnalyzer`, `MarkdownMentionsAnalyzer`, `__all__`).
- Produces (LOCKED): the package `pydocs_mcp.extraction.strategies.analyzers` exposing the identical surface at the identical dotted path. No shim module — a package with the same dotted path IS the compatibility layer (spec §4.1). `reference_capture.py` and `tool_router.py` require zero import edits (AC-1).

- [ ] **Step 1: Write the failing test (AC-1)** — append to `tests/extraction/test_analyzers.py`:

```python
def test_analyzers_import_path_is_a_package_with_the_full_seam_surface():
    """AC-1: the seam converted to a package preserving the dotted path —
    every name in the pre-conversion __all__ is importable unchanged, and
    the module object is a package (has __path__), ready to host the
    per-language modules of spec §4.1."""
    import pydocs_mcp.extraction.strategies.analyzers as analyzers_pkg

    assert hasattr(analyzers_pkg, "__path__"), "analyzers must be a package"
    for name in analyzers_pkg.__all__:
        assert getattr(analyzers_pkg, name, None) is not None, name
```

- [ ] **Step 2: Run, see it fail** — `pytest tests/extraction/test_analyzers.py::test_analyzers_import_path_is_a_package_with_the_full_seam_surface -q`. Expected: FAILED on the `__path__` assertion (it's still a flat module).
- [ ] **Step 3: The move** — byte-identical content, history-preserving:

```bash
mkdir python/pydocs_mcp/extraction/strategies/analyzers
git mv python/pydocs_mcp/extraction/strategies/analyzers.py \
       python/pydocs_mcp/extraction/strategies/analyzers/__init__.py
```

Do NOT edit the file content in this task — the golden 9-row edge-set pin (`test_golden_edge_set_identical_pre_and_post_registry_refactor`) must pass unmodified (AC-6: the refactor is invisible to Python capture).

- [ ] **Step 4: Run to green** — `pytest tests/extraction/test_analyzers.py tests/application/test_tool_router.py tests/extraction/test_reference_capture_stage.py -q`. Expected: all pass, including the golden edge-set pin and the new AC-1 test.
- [ ] **Step 5: Lint + format** — `ruff format python/ tests/ benchmarks/ && ruff check python/ tests/ benchmarks/`. Expected: `All checks passed!`.
- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/analyzers/__init__.py \
        tests/extraction/test_analyzers.py
git commit -m "refactor(extraction): convert analyzers.py to a package — dotted path and seam surface byte-identical (AC-1, AC-6)"
```

---

## Task 2 — Shared tree-sitter plumbing: qname hoist + `_treesitter.py`

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/chunkers/_shared.py` (new `_assign_top_level_qnames` next to `_identifier_slug`; add to `__all__`)
- Modify: `python/pydocs_mcp/extraction/strategies/chunkers/multilang_treesitter.py` (`_build_symbol_tree` / `_symbol_nodes` refactor to call the hoisted helper; new `_register_cache_reset` hook wired into `_reset_multilang_caches`)
- Create: `python/pydocs_mcp/extraction/strategies/analyzers/_treesitter.py`
- Test (create): `tests/extraction/test_analyzers_treesitter.py`
- Test (modify): `tests/extraction/test_multilang_treesitter.py` — the qname refactor is behavior-preserving, but TWO tests call `mlt._symbol_nodes` directly with the PRE-refactor call shape (raw 4-tuple `_Symbol`s + `module=`/`rel=` kwargs) and must move to the hoisted contract in Step 4b: `test_symbol_nodes_dedup_colled_names` (line ~137) and `test_symbol_nodes_keep_camelcase_and_snake_case_verbatim` (line ~181). Everything else — the `_build_symbol_tree` direct-call tests (unchanged signature), the `_identifier_slug` pins, the span tests — passes unmodified.

**Interfaces:**
- Consumes: `_identifier_slug(name: str, seen: dict[str, int]) -> str`, `_module_from_doc_path(path: str, root: Path) -> str` (`chunkers/_shared.py`); `_load_language(ext) -> Any | None`, `_compiled_query(ext, language) -> Any`, `_symbol_from_match(captures, kinds) -> _Symbol | None`, `_in_range_symbols(symbols, n_lines) -> list[_Symbol]`, `_reset_multilang_caches()` (`chunkers/multilang_treesitter.py`); `LANGUAGE_SPECS` (`chunkers/multilang_queries.py`); `NodeReference` (`storage/node_reference.py`); `ReferenceKind`; `ReferenceCollector` + `_MAX_TO_NAME_CHARS` (`strategies/references.py`).
- Produces (LOCKED names):
  - `_shared._assign_top_level_qnames(symbols: list[tuple[Any, str, int, int]], module: str) -> list[tuple[str, Any, str, int, int]]` — owns the start-line sort AND the slug-dedup (spec §4.4: dedup suffixes depend on iteration order).
  - `multilang_treesitter._register_cache_reset(reset: Callable[[], None]) -> None`.
  - `multilang_treesitter.loadable_grammar_fingerprint` is NOT created here (Task 12).
  - `_treesitter.ReferenceQueryRole` (StrEnum: `CALLS = "calls"`, `INHERITS = "inherits"`, `IMPORTS = "imports"`)
  - `_treesitter.TREESITTER_ACTIVE_CAPABILITIES` / `TREESITTER_DEGRADED_CAPABILITIES` (module-level `LanguageCapabilities` dicts — stable objects, so identity pins hold)
  - `_treesitter.capabilities_for(ext: str) -> LanguageCapabilities`
  - `_treesitter.canonical_target(raw: str | None) -> str | None`
  - `_treesitter.node_text(node: Any) -> str`
  - `_treesitter.add_reference(collector, *, from_package: str, from_node_id: str, to_name: str | None, kind: ReferenceKind) -> None`
  - `_treesitter.record_aliases(collector, module: str, aliases: dict[str, str]) -> None`
  - `_treesitter.open_capture_session(source: str, *, path: str, root: Path) -> CaptureSession | None`
  - `_treesitter.CaptureSession` with `.module: str`, `.matches(role, query_source) -> list[dict[str, Any]]`, `.enclosing_qname(node) -> str`
  - `_treesitter._TopLevelSymbolIndex` with `.enclosing(line: int) -> str`
  - `_treesitter._REFERENCE_QUERY_CACHE: dict[tuple[str, ReferenceQueryRole], Any]` (cleared through the chunker's reset seam)

- [ ] **Step 1: Write the failing tests** — create `tests/extraction/test_analyzers_treesitter.py`:

```python
"""Shared analyzer plumbing pins — the qname hoist (AC-23), the
ReferenceQueryRole vocabulary, the two D7 capability states, the bisect
attribution index, target canonicalization, and the cache-reset seam."""

from __future__ import annotations

import inspect
import sys
from enum import StrEnum
from pathlib import Path

import pytest

from pydocs_mcp.extraction.model import NodeKind
from pydocs_mcp.extraction.strategies.analyzers import _treesitter as ts_shared
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
    ReferenceQueryRole,
    _TopLevelSymbolIndex,
    canonical_target,
    capabilities_for,
    record_aliases,
)
from pydocs_mcp.extraction.strategies.chunkers import multilang_treesitter as mlt
from pydocs_mcp.extraction.strategies.chunkers._shared import _assign_top_level_qnames
from pydocs_mcp.extraction.strategies.references import ReferenceCollector


@pytest.fixture(autouse=True)
def _clean_caches():
    mlt._reset_multilang_caches()
    yield
    mlt._reset_multilang_caches()


# ── the qname hoist (spec §4.4, AC-23) ─────────────────────────────────────


def test_assign_top_level_qnames_owns_the_sort_and_the_dedup():
    """Unsorted input still yields start-line-ordered, order-stable dedup
    suffixes — the reason the sort lives INSIDE the shared helper."""
    symbols = [
        (NodeKind.CLASS, "Node", 5, 7),
        (NodeKind.CLASS, "Node", 1, 2),
    ]
    assigned = _assign_top_level_qnames(symbols, "m")
    assert [(q, s) for q, _k, _n, s, _e in assigned] == [
        ("m.Node", 1),
        ("m.Node_2", 5),
    ]


def test_ac23_span_qname_assignment_is_a_single_shared_function():
    """AC-23 structural check: after the hoist, neither the chunker's
    _symbol_nodes nor the analyzer index builder owns a private copy of
    the slug rule."""
    assert "_identifier_slug" not in inspect.getsource(mlt._symbol_nodes)
    assert "_assign_top_level_qnames" in inspect.getsource(mlt._build_symbol_tree)
    assert "_assign_top_level_qnames" in inspect.getsource(ts_shared._symbol_index)


# ── ReferenceQueryRole (closed vocabulary) ─────────────────────────────────


def test_reference_query_role_is_a_closed_strenum():
    assert issubclass(ReferenceQueryRole, StrEnum)
    assert [m.value for m in ReferenceQueryRole] == ["calls", "inherits", "imports"]


# ── the two capability states (spec §7.2) ──────────────────────────────────


def test_capability_state_constants_pin_spec_7_2():
    assert TREESITTER_ACTIVE_CAPABILITIES == {
        "outline": "available",
        "definitions": "available",
        "references": "syntactic",
    }
    assert TREESITTER_DEGRADED_CAPABILITIES == {
        "outline": "available",
        "definitions": "unavailable",
        "references": "unavailable",
    }


def test_capabilities_for_active_state_when_grammar_loads():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_rust")
    assert capabilities_for(".rs") is TREESITTER_ACTIVE_CAPABILITIES


def test_capabilities_for_degraded_state_when_grammar_blocked(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    mlt._reset_multilang_caches()
    assert capabilities_for(".rs") is TREESITTER_DEGRADED_CAPABILITIES


# ── bisect attribution index ───────────────────────────────────────────────


def test_symbol_index_bisects_lines_to_enclosing_top_level_span():
    assigned = [
        ("m.A", NodeKind.CLASS, "A", 2, 4),
        ("m.b", NodeKind.FUNCTION, "b", 6, 8),
    ]
    index = _TopLevelSymbolIndex("m", assigned)
    assert index.enclosing(1) == "m"          # preamble → module
    assert index.enclosing(2) == "m.A"        # span start
    assert index.enclosing(4) == "m.A"        # span end (inclusive)
    assert index.enclosing(5) == "m"          # gap between spans → module
    assert index.enclosing(8) == "m.b"
    assert index.enclosing(9) == "m"          # past EOF-side span → module


def test_symbol_index_with_no_spans_always_returns_module():
    index = _TopLevelSymbolIndex("m", [])
    assert index.enclosing(1) == "m"
    assert index.enclosing(400) == "m"


# ── canonical_target (mirror of canonical_dotted's None policy) ────────────


def test_canonical_target_normalizes_separators_and_drops_junk():
    assert canonical_target("a::b::f") == "a.b.f"
    assert canonical_target("include/graph.h") == "include.graph.h"
    assert canonical_target("x.f") == "x.f"
    assert canonical_target("foo().bar") is None    # computed callee → dropped
    assert canonical_target("") is None
    assert canonical_target(None) is None


def test_canonical_target_caps_length_like_the_python_emitters():
    from pydocs_mcp.extraction.strategies.references import _MAX_TO_NAME_CHARS

    capped = canonical_target("x" * (_MAX_TO_NAME_CHARS + 50))
    assert capped is not None
    assert len(capped) == _MAX_TO_NAME_CHARS
    assert capped.endswith("…")


# ── alias recording (the AC-19 empty-table pin depends on this) ────────────


def test_record_aliases_skips_empty_input_and_merges_per_module():
    collector = ReferenceCollector()
    record_aliases(collector, "m", {})
    assert collector.aliases == {}          # no empty dict created
    record_aliases(collector, "m", {"A": "x.A"})
    record_aliases(collector, "m", {"B": "y.B"})
    assert collector.aliases == {"m": {"A": "x.A", "B": "y.B"}}


# ── cache-reset seam (spec §4.3) ───────────────────────────────────────────


def test_reference_query_cache_clears_via_the_shared_reset_seam():
    ts_shared._REFERENCE_QUERY_CACHE[(".rs", ReferenceQueryRole.CALLS)] = object()
    mlt._reset_multilang_caches()
    assert ts_shared._REFERENCE_QUERY_CACHE == {}


# ── session construction (grammar-gated) ───────────────────────────────────


def test_open_capture_session_builds_module_id_and_skips_empty_queries():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_rust")
    session = ts_shared.open_capture_session("fn top() {}\n", path="pkg/x.rs", root=Path())
    assert session is not None
    assert session.module == "pkg.x.rs"
    # Empty query (C inherits) → no matches, tree_sitter untouched (D11).
    assert session.matches(ReferenceQueryRole.INHERITS, "") == []


def test_open_capture_session_returns_none_when_grammar_blocked(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    mlt._reset_multilang_caches()
    assert ts_shared.open_capture_session("fn f() {}", path="pkg/x.rs", root=Path()) is None
```

- [ ] **Step 2: Run, see it fail** — `pytest tests/extraction/test_analyzers_treesitter.py -q`. Expected: collection error (`ModuleNotFoundError`/`ImportError` — `_treesitter` and `_assign_top_level_qnames` don't exist yet).
- [ ] **Step 3: Hoist the qname assignment into `chunkers/_shared.py`** — add after `_identifier_slug` (keep `Any` for the kind slot so `_shared.py` needs no `NodeKind` import):

```python
def _assign_top_level_qnames(
    symbols: list[tuple[Any, str, int, int]],
    module: str,
) -> list[tuple[str, Any, str, int, int]]:
    """Start-line-sorted qname assignment for top-level code symbols.

    THE single source of the span→qname rule (multilang spec §4.4): both the
    chunker's ``_symbol_nodes`` and the analyzers' attribution index call this
    — joinability of reference edges with the persisted document tree is a
    property of the code structure, not a convention. The sort lives HERE, not
    in callers, because ``_identifier_slug``'s ``_N`` dedup suffixes depend on
    iteration order: a caller feeding unsorted symbols would silently drift
    the qnames between the two sides.
    """
    ordered = sorted(symbols, key=lambda s: s[2])
    seen: dict[str, int] = {}
    return [
        (f"{module}.{_identifier_slug(name, seen)}", kind, name, start, end)
        for kind, name, start, end in ordered
    ]
```

Add `from typing import Any` to `_shared.py`'s imports if not present, and `"_assign_top_level_qnames"` to its `__all__`.

- [ ] **Step 4: Refactor `multilang_treesitter.py` to call the hoisted helper.** Replace `_build_symbol_tree` and `_symbol_nodes` (current lines ~221–262) with:

```python
def _build_symbol_tree(
    path: str,
    content: str,
    root: Path,
    symbols: list[_Symbol],
) -> DocumentNode | None:
    module = _module_from_doc_path(path, root)
    rel = _relpath(path, root)
    lines = content.splitlines()
    valid = _in_range_symbols(symbols, len(lines))
    if not valid:
        return None  # no top-level items — caller falls back to windows
    # Shared span→qname assignment (multilang spec §4.4): the analyzers build
    # their attribution index from the SAME call, so edges join this tree by
    # construction.
    assigned = _assign_top_level_qnames(valid, module)
    preamble = _slice_lines(lines, 1, assigned[0][3] - 1)
    children = _symbol_nodes(assigned, lines, rel, module)
    return _module_node(module, rel, content, direct_text=preamble, children=children)


def _symbol_nodes(
    assigned: list[tuple[str, NodeKind, str, int, int]],
    lines: list[str],
    rel: str,
    module: str,
) -> tuple[DocumentNode, ...]:
    nodes: list[DocumentNode] = []
    for qname, kind, name, start, end in assigned:
        text = _slice_lines(lines, start, end)
        nodes.append(_symbol_node(qname, name, kind, rel, start, end, text, module))
    return tuple(nodes)
```

Update the import block: add `_assign_top_level_qnames` to the existing `from pydocs_mcp.extraction.strategies.chunkers._shared import (...)` list (`_identifier_slug` stays imported only if still referenced elsewhere in the file — after this refactor it is not; remove it from the import if `ruff` flags it unused).

- [ ] **Step 4b: Move the two direct-call `_symbol_nodes` tests to the hoisted contract.** In `tests/extraction/test_multilang_treesitter.py`, `test_symbol_nodes_dedup_colled_names` (~line 137) and `test_symbol_nodes_keep_camelcase_and_snake_case_verbatim` (~line 181) call `mlt._symbol_nodes` with raw 4-tuples plus `module=`/`rel=` kwargs — under the refactored signature they raise `ValueError` at the 5-tuple unpack. Rewrite ONLY their call sites to feed the shared helper's output, preserving every behavior assertion byte-for-byte (the dedup pin `["m.rs.f", "m.rs.f_2"]` and the verbatim-case pin `["app.js.topLevelInference", "app.js.JsEngine", "app.js.safe_truncate"]` are the point of the tests — they now double as proof the hoist is behavior-preserving). Add `_assign_top_level_qnames` to the file's `_shared` import. First test after the rewrite:

```python
def test_symbol_nodes_dedup_colled_names() -> None:
    lines = ["fn f(){}", "fn f(){}"]
    nodes = mlt._symbol_nodes(
        _assign_top_level_qnames(
            [(NodeKind.FUNCTION, "f", 1, 1), (NodeKind.FUNCTION, "f", 2, 2)], "m.rs"
        ),
        lines,
        rel="x.rs",
        module="m.rs",
    )
    qnames = [n.qualified_name for n in nodes]
    # verification finding #2: dedup suffix is identifier-SAFE (``_2``, not
    # ``-2``) so the disambiguated id stays a valid dotted identifier and
    # remains addressable via get_symbol / get_references.
    assert qnames == ["m.rs.f", "m.rs.f_2"]
```

and analogously wrap the second test's three-tuple list in `_assign_top_level_qnames([...], "app.js")`. Scope: `_symbol_nodes` only — `test_build_symbol_tree_orders_children_and_sets_preamble` and `test_build_symbol_tree_returns_none_when_no_in_range_symbols` call `_build_symbol_tree`, whose signature does not change, and genuinely pass unmodified.

- [ ] **Step 5: Add the cache-reset hook to `multilang_treesitter.py`.** Add `from collections.abc import Callable` to imports; add next to the module caches and replace `_reset_multilang_caches`:

```python
# Sibling tree-sitter caches (the analyzers' compiled reference-query cache)
# register their clear function here so the ONE test seam resets everything.
# A callback list — not a chunkers→analyzers import, which would invert the
# layering (analyzers depend on chunkers, never the reverse).
_EXTRA_CACHE_RESETS: list[Callable[[], None]] = []


def _register_cache_reset(reset: Callable[[], None]) -> None:
    """Join a sibling cache to `_reset_multilang_caches` (analyzers seam)."""
    _EXTRA_CACHE_RESETS.append(reset)


def _reset_multilang_caches() -> None:
    """Clear the module-scope caches. Test-only seam so the absence path (extra
    blocked via ``sys.modules``) and the present path both run in one process."""
    _LANG_CACHE.clear()
    _QUERY_CACHE.clear()
    _UNAVAILABLE_EXTS.clear()
    _LOGGED_FALLBACK_EXTS.clear()
    for reset in _EXTRA_CACHE_RESETS:
        reset()
```

- [ ] **Step 6: Create `python/pydocs_mcp/extraction/strategies/analyzers/_treesitter.py`** — full content:

```python
"""Shared tree-sitter plumbing for the per-language reference analyzers.

One grammar cache, one degrade path (spec §4.3): grammar availability and
``Language`` objects come from the CHUNKER's caches
(``chunkers/multilang_treesitter.py``), so the declared capability state and
the actual indexing behavior can never disagree within a process. This module
adds only what reference capture needs on top:

- the two capability states of spec §7.2 (D7);
- a compiled-query cache keyed ``(ext, ReferenceQueryRole)`` — the
  calls/inherits/imports query sources differ from the chunker's top-level
  query, so they get their own cache, registered with the chunker's reset
  seam so the degrade tests can flip states in one process;
- the top-level span bisect index that makes attribution joinable by
  construction (spec §4.4): the analyzer runs the CHUNKER's own top-level
  query and assigns qnames with the SAME shared helper the chunker uses;
- the probe-rule query executor (D11): ``QueryCursor.matches()`` only,
  ``Tree`` + cursor bound to live locals across iteration, 1-indexed spans,
  the out-of-range span guard, and empty query → no matches (C inherits).

Language modules never import ``tree_sitter`` directly — every touch of the
library goes through this module or the chunker's loaders.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _assign_top_level_qnames,
    _module_from_doc_path,
)
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import LANGUAGE_SPECS
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _compiled_query,
    _in_range_symbols,
    _load_language,
    _register_cache_reset,
    _symbol_from_match,
)
from pydocs_mcp.extraction.strategies.references import _MAX_TO_NAME_CHARS
from pydocs_mcp.storage.node_reference import NodeReference

if TYPE_CHECKING:
    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector


class ReferenceQueryRole(StrEnum):
    """Closed vocabulary keying the compiled-query cache and parameterizing
    the shared executor (spec §4.3). Values match the capture-kind strings
    the stage's ``allowed`` frozenset carries."""

    CALLS = "calls"
    INHERITS = "inherits"
    IMPORTS = "imports"


# The two per-deployment capability states (spec §7.2, D7). Module-level
# constants — ``capabilities_for`` returns THESE objects, so identity pins
# (``language_capabilities(ext) is analyzer.capabilities``) stay valid.
TREESITTER_ACTIVE_CAPABILITIES: LanguageCapabilities = {
    "outline": "available",
    "definitions": "available",
    "references": "syntactic",
}
TREESITTER_DEGRADED_CAPABILITIES: LanguageCapabilities = {
    "outline": "available",  # text-window fallback still persists a module tree
    "definitions": "unavailable",  # no symbol nodes in the fallback shape
    "references": "unavailable",  # the analyzer no-ops (D11)
}


def capabilities_for(ext: str) -> LanguageCapabilities:
    """Per-deployment capability state for a tree-sitter extension (D7).

    Routes through the CHUNKER's memoized ``_load_language`` verdict — O(1)
    after first touch, and structurally incapable of disagreeing with what
    indexing actually did (spec §7.2 invariant).
    """
    active = _load_language(ext) is not None
    return TREESITTER_ACTIVE_CAPABILITIES if active else TREESITTER_DEGRADED_CAPABILITIES


# Compiled reference queries, keyed (ext, role) — the analyzer-side sibling of
# the chunker's per-ext top-level query cache. Registered with the shared
# reset seam below so degrade tests can flip grammar states in one process.
_REFERENCE_QUERY_CACHE: dict[tuple[str, ReferenceQueryRole], Any] = {}
_register_cache_reset(_REFERENCE_QUERY_CACHE.clear)

# Identifier-chain targets only — the string mirror of ``canonical_dotted``'s
# None policy (references.py): computed callees and punctuation never become
# ``to_name`` rows. ``$`` joins ``\\w`` for JavaScript identifiers.
_DOTTED_TARGET_RE = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")


def canonical_target(raw: str | None) -> str | None:
    """``::`` / ``/`` → ``.``, then keep only clean dotted identifier chains.

    Mirrors ``canonical_dotted``'s drop-don't-guess policy and its
    ``_MAX_TO_NAME_CHARS`` cap + trailing-ellipsis convention (spec §5.1).
    """
    if raw is None:
        return None
    text = raw.replace("::", ".").replace("/", ".").strip()
    if _DOTTED_TARGET_RE.fullmatch(text) is None:
        return None
    if len(text) > _MAX_TO_NAME_CHARS:
        return text[: _MAX_TO_NAME_CHARS - 1] + "…"
    return text


def node_text(node: Any) -> str:
    """Decoded source text of a tree-sitter node (replace-on-error, like the
    chunker's ``_capture_name``)."""
    return str(node.text.decode("utf-8", "replace"))


def add_reference(
    collector: ReferenceCollector,
    *,
    from_package: str,
    from_node_id: str,
    to_name: str | None,
    kind: ReferenceKind,
) -> None:
    """Emit one unresolved candidate; ``to_name=None`` (dropped target) is a
    no-op. ``to_node_id`` stays None — the resolver flips it later (AC-21)."""
    if to_name is None:
        return
    collector.add(
        NodeReference(
            from_package=from_package,
            from_node_id=from_node_id,
            to_name=to_name,
            to_node_id=None,
            kind=kind,
        )
    )


def record_aliases(collector: ReferenceCollector, module: str, aliases: dict[str, str]) -> None:
    """Merge alias entries under the suffix-preserving module qname.

    Empty input is a no-op — no empty dict may be created, because the C
    analyzer pins an EMPTY alias table (includes are not renaming imports,
    AC-19)."""
    if not aliases:
        return
    collector.aliases.setdefault(module, {}).update(aliases)


class _TopLevelSymbolIndex:
    """Bisect index: 1-indexed line → enclosing top-level symbol qname.

    Root-anchored chunker queries cannot produce overlapping top-level spans
    (spec §4.4), so bisect on start line + an end-line check suffices. Lines
    outside every span (imports, preamble, top-level statements) attribute
    to the module qname.
    """

    def __init__(self, module: str, assigned: list[tuple[str, Any, str, int, int]]) -> None:
        ordered = sorted(assigned, key=lambda item: item[3])
        self._module = module
        self._starts = [start for (_q, _k, _n, start, _e) in ordered]
        self._spans = [(start, end, qname) for (qname, _k, _n, start, end) in ordered]

    def enclosing(self, line: int) -> str:
        i = bisect_right(self._starts, line) - 1
        if i < 0:
            return self._module
        start, end, qname = self._spans[i]
        return qname if start <= line <= end else self._module


class CaptureSession:
    """One parsed file: module id, live Tree, span index, query runner.

    A plain class, not a frozen dataclass: it must hold the tree-sitter
    ``Tree`` in a live attribute across every query iteration — a GC'd
    temporary segfaults (probe rule, evidence-treesitter §3 / D11).
    """

    def __init__(
        self,
        ext: str,
        language: Any,
        tree: Any,
        module: str,
        index: _TopLevelSymbolIndex,
    ) -> None:
        self.ext = ext
        self.module = module
        self._language = language
        self._tree = tree
        self._index = index

    def matches(self, role: ReferenceQueryRole, query_source: str) -> list[dict[str, Any]]:
        """Probe-rule query runner: ``matches()`` only (never ``captures()``);
        an empty query source (C inherits) is "no matches" without touching
        tree-sitter (D11)."""
        if not query_source.strip():
            return []
        import tree_sitter as ts

        query = _reference_query(self.ext, role, query_source, self._language)
        cursor = ts.QueryCursor(query)  # live local across iteration (probe rule)
        return [captures for _pattern, captures in cursor.matches(self._tree.root_node)]

    def enclosing_qname(self, node: Any) -> str:
        """Attribution (spec §4.4): the captured node's 1-indexed start line,
        bisected against the chunker's own top-level spans."""
        return self._index.enclosing(node.start_point[0] + 1)


def open_capture_session(source: str, *, path: str, root: Path) -> CaptureSession | None:
    """Parse ``source`` and build the attribution index for its extension.

    Returns None when the grammar is unavailable — the analyzer no-ops and
    the CHUNKER's one-per-extension ``multilang_fallback`` log stays the
    single operator signal (D11: no second log here).
    """
    ext = Path(path).suffix.lower()
    language = _load_language(ext)
    if language is None:
        return None
    import tree_sitter as ts

    module = _module_from_doc_path(path, root)
    tree = ts.Parser(language).parse(source.encode("utf-8"))  # bound live below
    index = _symbol_index(ext, language, tree, source, module)
    return CaptureSession(ext, language, tree, module, index)


def _symbol_index(
    ext: str, language: Any, tree: Any, source: str, module: str
) -> _TopLevelSymbolIndex:
    """Run the CHUNKER's own top-level query over the analyzer's parse and
    assign qnames with the shared helper — joinability by construction."""
    symbols = _top_level_symbols(ext, language, tree)
    valid = _in_range_symbols(symbols, len(source.splitlines()))
    return _TopLevelSymbolIndex(module, _assign_top_level_qnames(valid, module))


def _top_level_symbols(ext: str, language: Any, tree: Any) -> list[tuple[Any, str, int, int]]:
    """The chunker's ``_extract_symbols`` over an existing tree (one parse per
    file on the analyzer side; the compiled top-level query is the chunker's
    own cached object)."""
    import tree_sitter as ts

    kinds = LANGUAGE_SPECS[ext][3]
    cursor = ts.QueryCursor(_compiled_query(ext, language))  # live local
    symbols: list[tuple[Any, str, int, int]] = []
    for _pattern, captures in cursor.matches(tree.root_node):
        symbol = _symbol_from_match(captures, kinds)
        if symbol is not None:
            symbols.append(symbol)
    return symbols


def _reference_query(ext: str, role: ReferenceQueryRole, query_source: str, language: Any) -> Any:
    key = (ext, role)
    cached = _REFERENCE_QUERY_CACHE.get(key)
    if cached is not None:
        return cached
    import tree_sitter as ts

    query = ts.Query(language, query_source)
    _REFERENCE_QUERY_CACHE[key] = query
    return query


__all__ = (
    "TREESITTER_ACTIVE_CAPABILITIES",
    "TREESITTER_DEGRADED_CAPABILITIES",
    "CaptureSession",
    "ReferenceQueryRole",
    "add_reference",
    "canonical_target",
    "capabilities_for",
    "node_text",
    "open_capture_session",
    "record_aliases",
)
```

- [ ] **Step 7: Run to green** — `pytest tests/extraction/test_analyzers_treesitter.py tests/extraction/test_multilang_treesitter.py tests/extraction/test_analyzers.py -q`. Expected: all pass — with the two Step 4b call-site rewrites in place, the multilang chunker suite's untouched behavior pins prove the qname refactor is behavior-preserving; the golden Python pin proves the seam untouched.
- [ ] **Step 8: Lint + type + format** — `ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp`. Expected: clean (run `ruff format` first if formatting differs).
- [ ] **Step 9: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/analyzers/_treesitter.py \
        python/pydocs_mcp/extraction/strategies/chunkers/_shared.py \
        python/pydocs_mcp/extraction/strategies/chunkers/multilang_treesitter.py \
        tests/extraction/test_analyzers_treesitter.py \
        tests/extraction/test_multilang_treesitter.py
git commit -m "feat(extraction): shared tree-sitter analyzer plumbing — qname hoist, span bisect index, (ext, role) query cache, two-state capabilities (AC-23)"
```

---

## Task 3 — `LanguageAnalyzer.capabilities` becomes a read-only property (D7)

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/analyzers/__init__.py` (the Protocol block only; `PythonAstAnalyzer` / `MarkdownMentionsAnalyzer` keep their plain `ClassVar` class attributes — a class attribute satisfies a read-only property Protocol structurally, both for `runtime_checkable` isinstance and for mypy)
- Test (modify): `tests/application/test_tool_router.py` (new property-mechanism test; existing tests untouched)
- Test (existing, must stay green): `tests/extraction/test_analyzers.py` — `test_registered_analyzers_satisfy_protocol`, `test_language_capabilities_lookup` (including the `language_capabilities(".py") is analyzer_registry[".py"].capabilities` identity pin and, at this task, still the `language_capabilities(".rs") is None` pin — no `.rs` analyzer exists yet; Task 4 rewrites it per AC-8)

**Interfaces:**
- Produces (LOCKED): the Protocol member changes from `capabilities: ClassVar[LanguageCapabilities]` to a `@property` returning `LanguageCapabilities` (spec §7.1 verbatim shape). `language_capabilities(ext)` signature unchanged; its return value becomes deployment-dependent for tree-sitter extensions once they register.

- [ ] **Step 1: Write the failing test** — append to `tests/application/test_tool_router.py` (below the existing `_resolution_for` helper):

```python
class _TwoStateAnalyzer:
    """Property-shaped fake proving the router is data-driven (spec §7.3):
    `_resolution_for_ext` re-reads `capabilities` per call, so a
    deployment-state flip changes `meta.resolution` with ZERO router code."""

    def __init__(self) -> None:
        self.active = True

    @property
    def capabilities(self) -> dict[str, str]:
        if self.active:
            return {"outline": "available", "definitions": "available", "references": "syntactic"}
        return {"outline": "available", "definitions": "unavailable", "references": "unavailable"}

    def capture(self, source, *, path, root, from_package, allowed, collector) -> None:
        return None


def test_references_resolution_follows_property_backed_capabilities(monkeypatch):
    from pydocs_mcp.extraction.strategies.analyzers import (
        LanguageAnalyzer,
        analyzer_registry,
    )

    fake = _TwoStateAnalyzer()
    assert isinstance(fake, LanguageAnalyzer)  # property-shaped Protocol (D7)
    monkeypatch.setitem(analyzer_registry, ".zz", fake)
    assert _resolution_for(".zz") == "syntactic"
    fake.active = False
    assert _resolution_for(".zz") == "unavailable"
```

- [ ] **Step 2: Run, see the state of the world** — `pytest tests/application/test_tool_router.py::test_references_resolution_follows_property_backed_capabilities -q`. Expected: PASSES already at runtime (runtime_checkable checks attribute presence) — the failing side of this task is **mypy**: `mypy python/pydocs_mcp` currently types the Protocol member as a ClassVar, so step 3's change is what makes the property shape the *declared* contract. Run `mypy python/pydocs_mcp` before and after to confirm both are clean.
- [ ] **Step 3: Change the Protocol** — in `analyzers/__init__.py`, replace the `LanguageAnalyzer` Protocol block (keep `runtime_checkable`; the `capture` signature is unchanged):

```python
@runtime_checkable
class LanguageAnalyzer(Protocol):
    """Reference-capture backend for one file extension.

    ``capture`` parses ``source`` and emits unresolved
    :class:`~pydocs_mcp.storage.node_reference.NodeReference` candidates
    (plus alias / attribute-type tables) into ``collector``. Per-file
    error containment is the CALLER's job (``ReferenceCaptureStage``
    logs and continues) — analyzers may raise freely.

    ``capabilities`` is a READ-ONLY property (multilang-analyzers spec D7):
    tree-sitter analyzers report per-deployment truth (grammar loads →
    ``references: syntactic``; degraded → ``unavailable``), which a
    ``ClassVar`` cannot express. Plain class attributes
    (``PythonAstAnalyzer``, ``MarkdownMentionsAnalyzer``) still satisfy the
    property Protocol structurally — for ``runtime_checkable`` isinstance
    (attribute presence) and for mypy alike.
    """

    @property
    def capabilities(self) -> LanguageCapabilities: ...

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None: ...
```

`ClassVar` stays imported (the two concrete analyzers still annotate with it).

- [ ] **Step 4: Run to green** — `pytest tests/extraction/test_analyzers.py tests/application/test_tool_router.py -q && mypy python/pydocs_mcp`. Expected: all pass, mypy clean (AC-3's property-shaped isinstance holds; the `.py` identity pin holds — class attributes are stable objects).
- [ ] **Step 5: Lint + format** — `ruff format python/ tests/ && ruff check python/ tests/`. Expected: clean.
- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/analyzers/__init__.py \
        tests/application/test_tool_router.py
git commit -m "feat(extraction): LanguageAnalyzer.capabilities becomes a read-only property — router stays data-driven (D7, spec 7.1)"
```

---

## Task 4 — Rust analyzer (`rust.py`, `.rs`) + AC-13 fixture

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/analyzers/rust.py`
- Modify: `python/pydocs_mcp/extraction/strategies/analyzers/__init__.py` (registration-at-import block appended at the very end)
- Test (create): `tests/extraction/_analyzer_fixtures.py` (shared fixture plumbing for Tasks 4–9), `tests/extraction/test_analyzer_rust.py`
- Test (modify): `tests/extraction/test_analyzers.py` — rewrite the `language_capabilities(".rs") is None` pin (AC-8); `tests/application/test_tool_router.py` — remove `".rs"` from the `test_references_resolution_non_python_target_is_unavailable` parametrize (the registered analyzer now declares per-deployment state; the final repartition lands in Task 9)

**Interfaces:**
- Consumes: everything `_treesitter` produces (Task 2), `register_analyzer`, `LanguageCapabilities` (TYPE_CHECKING) from the package, `ReferenceKind`, `ReferenceCollector` (TYPE_CHECKING).
- Produces (LOCKED names): `RustAnalyzer` (registered `.rs`), `normalize_rust_use(declaration_text: str) -> tuple[dict[str, str], list[str]]`, query constants `_RUST_CALLS_QUERY` / `_RUST_INHERITS_QUERY` / `_RUST_IMPORTS_QUERY`.

**NOTE (grammar-node contingency, applies to Tasks 4–8):** the S-expression queries below are written against the pinned grammar wheels (`tree-sitter-rust>=0.24,<0.25` etc.). If a red test in Step 2's loop fails with a tree-sitter `QueryError` naming a node or field, the pattern name drifted from the installed grammar — correct the node/field name against the wheel's `node-types.json` (`python -c "import tree_sitter_rust, pathlib; print(pathlib.Path(tree_sitter_rust.__file__).parent)"`) and keep the capture names (`@callee`, `@parent`, `@import`) and emitted shapes EXACTLY as specified; the fixture assertions are the contract, the query text is implementation detail.

- [ ] **Step 1: Create the shared fixture helper** — `tests/extraction/_analyzer_fixtures.py`:

```python
"""Shared fixture plumbing for the per-language analyzer test files.

Runs the CHUNKER (persisted-tree side) and the ANALYZER (edge side) over the
same in-memory fixture files, then resolves through the real
``ReferenceResolver`` — the per-language ACs (AC-13..AC-17) assert against
this end-to-end path with the resolver byte-untouched (D8)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.extraction.strategies.analyzers import analyzer_registry
from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker
from pydocs_mcp.extraction.strategies.reference_resolver import ReferenceResolver
from pydocs_mcp.extraction.strategies.references import ReferenceCollector
from pydocs_mcp.storage.node_reference import NodeReference

ALL_KINDS = frozenset({"calls", "imports", "inherits"})


def tree_qnames(node: DocumentNode) -> set[str]:
    names = {node.qualified_name}
    for child in node.children:
        names |= tree_qnames(child)
    return names


def capture_fixture(
    files: dict[str, str],
    allowed: frozenset[str] = ALL_KINDS,
) -> tuple[frozenset[str], ReferenceCollector]:
    """Chunk + capture every fixture file under package 'pkg'; returns the
    persisted-qname universe and the filled collector. Fixture paths MUST
    start with 'pkg/' so qnames carry the from_package prefix Rule C scopes
    on (``q.startswith("pkg.")``)."""
    collector = ReferenceCollector()
    universe: set[str] = set()
    for relpath, source in files.items():
        tree = MultilangChunker().build_tree(
            path=relpath, content=source, package="pkg", root=Path()
        )
        universe |= tree_qnames(tree)
        analyzer = analyzer_registry[Path(relpath).suffix.lower()]
        analyzer.capture(
            source,
            path=relpath,
            root=Path(),
            from_package="pkg",
            allowed=allowed,
            collector=collector,
        )
    return frozenset(universe), collector


def resolve_fixture(
    universe: frozenset[str], collector: ReferenceCollector
) -> list[NodeReference]:
    resolver = ReferenceResolver(qname_universe=universe, aliases=collector.aliases)
    return resolver.resolve(collector.refs)


def edge_map(refs: list[NodeReference]) -> dict[tuple[str, str, str], str | None]:
    """(from_node_id, to_name, kind) → to_node_id. Duplicate PKs collapse —
    fine here; the fixtures pin unique edges."""
    return {(r.from_node_id, r.to_name, r.kind.value): r.to_node_id for r in refs}
```

- [ ] **Step 2: Write the failing tests** — `tests/extraction/test_analyzer_rust.py`:

```python
"""RustAnalyzer pins — registration, two-state capabilities (AC-7), the D8
normalizer examples (AC-18), and the AC-13 two-file end-to-end fixture."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")

from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
    language_capabilities,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.rust import normalize_rust_use
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from tests.extraction._analyzer_fixtures import (
    capture_fixture,
    edge_map,
    resolve_fixture,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def test_rust_analyzer_is_registered_and_satisfies_the_protocol():
    assert isinstance(analyzer_registry[".rs"], LanguageAnalyzer)


def test_ac7_capabilities_active_state():
    caps = analyzer_registry[".rs"].capabilities
    assert caps == {
        "outline": "available",
        "definitions": "available",
        "references": "syntactic",
    }
    assert caps is TREESITTER_ACTIVE_CAPABILITIES
    # AC-8: the registry lookup surfaces the same deployment-dependent dict.
    assert language_capabilities(".rs") is caps


def test_ac7_capabilities_degraded_state(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    caps = analyzer_registry[".rs"].capabilities
    assert caps == {
        "outline": "available",
        "definitions": "unavailable",
        "references": "unavailable",
    }
    assert caps is TREESITTER_DEGRADED_CAPABILITIES


def test_ac18_normalizer_d8_canonical_use_rename():
    # D8 canonical example, byte-pinned: `use crate::a::B as C` → alias C → a.B.
    assert normalize_rust_use("use crate::a::B as C;") == ({"C": "a.B"}, ["a.B"])


def test_normalizer_plain_wildcard_list_and_super_shapes():
    assert normalize_rust_use("use a::b::D;") == ({"D": "a.b.D"}, ["a.b.D"])
    assert normalize_rust_use("use a::*;") == ({}, ["a"])
    assert normalize_rust_use("pub use super::x::Y;") == ({"Y": "x.Y"}, ["x.Y"])
    aliases, targets = normalize_rust_use("use a::{B, c::D};")
    assert aliases == {"B": "a.B", "D": "a.c.D"}
    assert sorted(targets) == ["a.B", "a.c.D"]


# AC-13 fixture (spec §10): the impl span is the SOLE `Node` in file one —
# the struct lives in file two, per the §4.4 dedup constraint.
_LIB_RS = (
    "use crate::B as C;\n"
    "trait Show {}\n"
    "trait Fancy: Show {}\n"
    "impl Node { fn go(&self) { helper(); C::new(); } }\n"
)
_A_RS = "pub struct B;\npub struct Node;\npub fn helper() {}\n"


def test_ac13_rust_two_file_fixture_resolution_floor():
    universe, collector = capture_fixture({"pkg/lib.rs": _LIB_RS, "pkg/a.rs": _A_RS})
    assert collector.aliases == {"pkg.lib.rs": {"C": "B"}}
    edges = edge_map(resolve_fixture(universe, collector))
    # Must-resolve shapes (§5.7): single-segment cross-file + same-file.
    assert edges[("pkg.lib.rs.Node", "helper", "calls")] == "pkg.a.rs.helper"
    assert edges[("pkg.lib.rs", "B", "imports")] == "pkg.a.rs.B"
    assert edges[("pkg.lib.rs.Fancy", "Show", "inherits")] == "pkg.lib.rs.Show"
    # Expected-None: Rule A rewrites C.new → B.new; the interleaved extension
    # segment (pkg.a.rs.B vs B.new) keeps multi-segment targets unresolvable.
    assert edges[("pkg.lib.rs.Node", "C.new", "calls")] is None
```

- [ ] **Step 3: Run, see it fail** — `pytest tests/extraction/test_analyzer_rust.py -q`. Expected: collection error (`ModuleNotFoundError: ...analyzers.rust`).
- [ ] **Step 4: Create `python/pydocs_mcp/extraction/strategies/analyzers/rust.py`** — full content:

```python
"""RustAnalyzer — CALLS / INHERITS / IMPORTS capture for ``.rs`` (spec §5.2).

Queries are unanchored (references occur at any depth); attribution comes
from the shared top-level span index, so a call inside ``impl Node { … }``
attributes to the impl span's qname (``….Node`` — or ``….Node_2`` when the
file also defines ``struct Node``; the dedup-verbatim rule of spec §4.4).
Macro invocations are deliberately not captured (not calls in the graph's
sense). The import normalizer is purely syntactic (D8): ``crate::`` /
``self::`` / repeated ``super::`` prefixes are stripped, never resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import register_analyzer
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    CaptureSession,
    ReferenceQueryRole,
    add_reference,
    canonical_target,
    capabilities_for,
    node_text,
    open_capture_session,
    record_aliases,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

_RUST_CALLS_QUERY = """
(call_expression function: (identifier) @callee)
(call_expression function: (scoped_identifier) @callee)
(call_expression function: (field_expression) @callee)
"""

_RUST_INHERITS_QUERY = """
(impl_item trait: (type_identifier) @parent)
(impl_item trait: (scoped_type_identifier) @parent)
(trait_item bounds: (trait_bounds (type_identifier) @parent))
(trait_item bounds: (trait_bounds (scoped_type_identifier) @parent))
"""

_RUST_IMPORTS_QUERY = """
(use_declaration) @import
"""


@register_analyzer(".rs")
@dataclass(frozen=True, slots=True)
class RustAnalyzer:
    """Tree-sitter syntactic reference backend for Rust."""

    @property
    def capabilities(self) -> LanguageCapabilities:
        return capabilities_for(".rs")

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        session = open_capture_session(source, path=path, root=root)
        if session is None:
            return  # degraded — chunker's multilang_fallback log is the signal (D11)
        _capture_imports(session, from_package, collector)
        if "calls" in allowed:
            _capture_calls(session, from_package, collector)
        if "inherits" in allowed:
            _capture_inherits(session, from_package, collector)


def _capture_calls(session: CaptureSession, from_package: str, collector: ReferenceCollector) -> None:
    for captures in session.matches(ReferenceQueryRole.CALLS, _RUST_CALLS_QUERY):
        nodes = captures.get("callee")
        if not nodes:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=canonical_target(node_text(nodes[0])),
            kind=ReferenceKind.CALLS,
        )


def _capture_inherits(session: CaptureSession, from_package: str, collector: ReferenceCollector) -> None:
    for captures in session.matches(ReferenceQueryRole.INHERITS, _RUST_INHERITS_QUERY):
        nodes = captures.get("parent")
        if not nodes:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=canonical_target(node_text(nodes[0])),
            kind=ReferenceKind.INHERITS,
        )


def _capture_imports(session: CaptureSession, from_package: str, collector: ReferenceCollector) -> None:
    for captures in session.matches(ReferenceQueryRole.IMPORTS, _RUST_IMPORTS_QUERY):
        nodes = captures.get("import")
        if not nodes:
            continue
        aliases, targets = normalize_rust_use(node_text(nodes[0]))
        record_aliases(collector, session.module, aliases)
        for target in targets:
            add_reference(
                collector,
                from_package=from_package,
                from_node_id=session.enclosing_qname(nodes[0]),
                to_name=canonical_target(target),
                kind=ReferenceKind.IMPORTS,
            )


def normalize_rust_use(declaration_text: str) -> tuple[dict[str, str], list[str]]:
    """``use …;`` text → (alias entries, IMPORTS targets). Spec §5.2 / D8.

    Purely syntactic: ``crate::`` / ``self::`` and repeated ``super::``
    prefixes are stripped greedily, ``::`` maps to ``.``, and no filesystem
    resolution happens. Example: ``use crate::a::B as C;`` →
    ``({"C": "a.B"}, ["a.B"])``.
    """
    text = declaration_text.strip().rstrip(";").strip()
    text = text.removeprefix("pub").strip()
    text = text.removeprefix("use").strip()
    if not text:
        return {}, []
    return _use_tree(text, prefix="")


def _use_tree(text: str, prefix: str) -> tuple[dict[str, str], list[str]]:
    brace = text.find("{")
    if brace >= 0 and text.endswith("}"):
        head = text[:brace].rstrip().rstrip(":")
        return _use_list(text[brace + 1 : -1], _join_path(prefix, head))
    if " as " in text:
        path, _, alias = text.partition(" as ")
        target = _dotted_use_path(prefix, path.strip())
        return ({alias.strip(): target}, [target]) if target else ({}, [])
    if text.endswith("*"):
        target = _dotted_use_path(prefix, text[:-1].rstrip().rstrip(":"))
        return ({}, [target]) if target else ({}, [])
    target = _dotted_use_path(prefix, text)
    if not target:
        return {}, []
    return {target.rsplit(".", 1)[-1]: target}, [target]


def _use_list(inner: str, prefix: str) -> tuple[dict[str, str], list[str]]:
    aliases: dict[str, str] = {}
    targets: list[str] = []
    for item in _split_top_level_commas(inner):
        item_aliases, item_targets = _use_tree(item, prefix)
        aliases.update(item_aliases)
        targets.extend(item_targets)
    return aliases, targets


def _split_top_level_commas(text: str) -> list[str]:
    """Brace-depth-aware comma split — `use a::{B, c::{D, E}}` keeps nested
    groups intact for the recursive `_use_tree` pass."""
    items: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        items.append(tail)
    return [item for item in items if item]


def _join_path(prefix: str, head: str) -> str:
    head = head.strip()
    if not head:
        return prefix
    return f"{prefix}::{head}" if prefix else head


def _dotted_use_path(prefix: str, path: str) -> str | None:
    full = _join_path(prefix, path)
    for marker in ("crate::", "self::"):
        full = full.removeprefix(marker)
    while full.startswith("super::"):
        full = full.removeprefix("super::")
    return canonical_target(full)


__all__ = ("RustAnalyzer", "normalize_rust_use")
```

- [ ] **Step 5: Wire registration-at-import** — append at the VERY END of `analyzers/__init__.py` (after `__all__`):

```python
# Registration-at-import (spec §4.2): importing a language module fires its
# @register_analyzer decorator — the extraction.pipeline.stages precedent for
# populating a registry by import side effect. These imports MUST stay the
# LAST statements in this file: the language modules import seam names
# (register_analyzer, LanguageCapabilities) back from this partially
# initialized package, which only works after every name above exists.
# Safe with grammars absent: language modules import only _treesitter helpers
# at module scope; tree_sitter itself stays function-local (D5 lazy-import
# discipline — what keeps the sdist/ABI-mismatch degrade path working).
from pydocs_mcp.extraction.strategies.analyzers import (  # noqa: E402,F401
    rust,
)
```

(Tasks 5–8 each add their module name to this one import statement.)

- [ ] **Step 6: Update the two broken pins.**
  - In `tests/extraction/test_analyzers.py`, `test_language_capabilities_lookup`: replace the line `assert language_capabilities(".rs") is None` with:

```python
    # .rs now carries a registered tree-sitter analyzer whose declaration is
    # deployment-dependent (multilang-analyzers spec D7 / AC-8) — per-state
    # pins live in tests/extraction/test_analyzer_rust.py. Unregistered
    # extensions still return None:
    assert language_capabilities(".toml") is None
```

  - In `tests/application/test_tool_router.py`, `test_references_resolution_non_python_target_is_unavailable`: remove `".rs"` from the parametrize list (leaving `[".toml", ".js", ".ts", ".c", ".yaml", ".json"]` for now — Tasks 5–7 remove their extensions; Task 9 finishes the repartition).

- [ ] **Step 7: Run to green** — `pytest tests/extraction/test_analyzer_rust.py tests/extraction/test_analyzers.py tests/extraction/test_analyzers_treesitter.py tests/application/test_tool_router.py -q`. Expected: all pass. (A `QueryError` here means a grammar node-name drift — see the Task-4 NOTE.)
- [ ] **Step 8: Lint + type + format** — `ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp`. Expected: clean.
- [ ] **Step 9: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/analyzers/rust.py \
        python/pydocs_mcp/extraction/strategies/analyzers/__init__.py \
        tests/extraction/_analyzer_fixtures.py \
        tests/extraction/test_analyzer_rust.py \
        tests/extraction/test_analyzers.py \
        tests/application/test_tool_router.py
git commit -m "feat(extraction): RustAnalyzer — CALLS/INHERITS/IMPORTS + use-normalizer, two-state capabilities (AC-7, AC-8, AC-13, AC-18)"
```

---

## Task 5 — C analyzer (`c_lang.py`, `.c` + `.h`) + AC-15 fixture

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/analyzers/c_lang.py`
- Modify: `python/pydocs_mcp/extraction/strategies/analyzers/__init__.py` (add `c_lang` to the registration import)
- Test (create): `tests/extraction/test_analyzer_c.py`
- Test (modify): `tests/application/test_tool_router.py` (remove `".c"` from the unavailable parametrize)

**Interfaces:**
- Consumes: `_treesitter` helpers (Task 2), `register_analyzer`.
- Produces (LOCKED names): `CAnalyzer` (registered `.c` AND `.h` — two stateless instances of one class), `normalize_c_include(path_text: str) -> str | None`, `_C_CALLS_QUERY` / `_C_INHERITS_QUERY` (empty string — C has no inheritance, spec §5.3) / `_C_IMPORTS_QUERY`.

- [ ] **Step 1: Write the failing tests** — `tests/extraction/test_analyzer_c.py`:

```python
"""CAnalyzer pins — dual-extension registration, two-state capabilities
(AC-7, per MODULE — .c/.h share one wheel and one accessor, spec §4.2), the
D8 include example (AC-18), the empty-alias-table pin (AC-19 groundwork),
and the AC-15 prototype + #include fixture."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_c")

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.c_lang import normalize_c_include
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from tests.extraction._analyzer_fixtures import (
    capture_fixture,
    edge_map,
    resolve_fixture,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def test_c_analyzer_registered_for_both_extensions():
    assert isinstance(analyzer_registry[".c"], LanguageAnalyzer)
    assert isinstance(analyzer_registry[".h"], LanguageAnalyzer)
    assert type(analyzer_registry[".c"]) is type(analyzer_registry[".h"])


def test_ac7_capabilities_both_states_per_module(monkeypatch):
    # Per MODULE, primary extension .c hardcoded (spec §4.2): .c and .h ship
    # in ONE grammar wheel with one accessor, so per-extension skew is
    # impossible — both registry entries report the same state.
    assert analyzer_registry[".c"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    assert analyzer_registry[".h"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert analyzer_registry[".c"].capabilities is TREESITTER_DEGRADED_CAPABILITIES
    assert analyzer_registry[".h"].capabilities is TREESITTER_DEGRADED_CAPABILITIES


def test_ac18_normalizer_d8_canonical_include():
    # D8 canonical example: `#include "graph.h"` → module-level IMPORTS edge;
    # the kept `.h` segment is what makes suffix matching land on the
    # suffix-preserving module qname (spec §5.3).
    assert normalize_c_include('"graph.h"') == "graph.h"
    assert normalize_c_include('"include/graph.h"') == "include.graph.h"
    assert normalize_c_include("<stdio.h>") == "stdio.h"
    assert normalize_c_include('""') is None


# AC-15 fixture: graph.h declares the prototype; main.c includes it and calls
# the prototyped function from inside a function_definition span.
_GRAPH_H = "void tick(void);\n"
_MAIN_C = '#include "graph.h"\nvoid run(void) { tick(); }\n'


def test_ac15_c_prototype_and_include_fixture():
    universe, collector = capture_fixture({"pkg/graph.h": _GRAPH_H, "pkg/main.c": _MAIN_C})
    # Empty alias table: a C include is not a renaming import (AC-19 pin).
    assert collector.aliases == {}
    assert not any(r.kind is ReferenceKind.INHERITS for r in collector.refs)
    edges = edge_map(resolve_fixture(universe, collector))
    # C is the ONE language whose IMPORTS reliably resolve (§5.7): the
    # include target keeps `.h`, matching the module qname's kept suffix.
    assert edges[("pkg.main.c", "graph.h", "imports")] == "pkg.graph.h"
    # The call attributes to the calling function_definition's span and
    # resolves to the PROTOTYPE's qname in the defining header.
    assert edges[("pkg.main.c.run", "tick", "calls")] == "pkg.graph.h.tick"
```

- [ ] **Step 2: Run, see it fail** — `pytest tests/extraction/test_analyzer_c.py -q`. Expected: collection error (module missing).
- [ ] **Step 3: Create `python/pydocs_mcp/extraction/strategies/analyzers/c_lang.py`** — full content:

```python
"""CAnalyzer — CALLS / IMPORTS capture for ``.c`` + ``.h`` (spec §5.3).

C has no inheritance: the inherits query is the empty string and the shared
executor skips it without touching tree-sitter (D11). Struct embedding is
deliberately NOT modeled as inheritance. Includes are not renaming imports,
so the alias table stays EMPTY for C modules (AC-19 pins this).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import register_analyzer
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    CaptureSession,
    ReferenceQueryRole,
    add_reference,
    canonical_target,
    capabilities_for,
    node_text,
    open_capture_session,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

_C_CALLS_QUERY = """
(call_expression function: (identifier) @callee)
"""

# C has no inheritance (spec §5.3) — empty query; the shared executor treats
# it as "no matches" without compiling anything.
_C_INHERITS_QUERY = ""

_C_IMPORTS_QUERY = """
(preproc_include path: (string_literal) @path)
(preproc_include path: (system_lib_string) @path)
"""


@register_analyzer(".h")
@register_analyzer(".c")
@dataclass(frozen=True, slots=True)
class CAnalyzer:
    """Tree-sitter syntactic reference backend for C sources and headers."""

    @property
    def capabilities(self) -> LanguageCapabilities:
        # Primary-extension hardcode (spec §4.2): .c/.h share one wheel and
        # one accessor, so the pair cannot skew — AC-7 pins per MODULE.
        return capabilities_for(".c")

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        session = open_capture_session(source, path=path, root=root)
        if session is None:
            return  # degraded — chunker's multilang_fallback log is the signal (D11)
        _capture_includes(session, from_package, collector)
        if "calls" in allowed:
            _capture_calls(session, from_package, collector)
        # No inherits pass: _C_INHERITS_QUERY is empty by design.


def _capture_calls(session: CaptureSession, from_package: str, collector: ReferenceCollector) -> None:
    for captures in session.matches(ReferenceQueryRole.CALLS, _C_CALLS_QUERY):
        nodes = captures.get("callee")
        if not nodes:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=canonical_target(node_text(nodes[0])),
            kind=ReferenceKind.CALLS,
        )


def _capture_includes(session: CaptureSession, from_package: str, collector: ReferenceCollector) -> None:
    for captures in session.matches(ReferenceQueryRole.IMPORTS, _C_IMPORTS_QUERY):
        nodes = captures.get("path")
        if not nodes:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=normalize_c_include(node_text(nodes[0])),
            kind=ReferenceKind.IMPORTS,
        )


def normalize_c_include(path_text: str) -> str | None:
    """``"graph.h"`` / ``<stdio.h>`` → dotted include target, extension kept.

    The kept ``.h`` segment is what makes suffix matching land on the
    suffix-preserving module qname (``src.include.graph.h``, spec §5.3).
    System includes normalize the same way and resolve to None (Rule E) —
    verbatim intent, like Python's unresolved imports.
    """
    return canonical_target(path_text.strip().strip('"<>'))


__all__ = ("CAnalyzer", "normalize_c_include")
```

- [ ] **Step 4: Wire registration** — in `analyzers/__init__.py`, extend the trailing import to:

```python
from pydocs_mcp.extraction.strategies.analyzers import (  # noqa: E402,F401
    c_lang,
    rust,
)
```

- [ ] **Step 5: Router parametrize** — remove `".c"` from `test_references_resolution_non_python_target_is_unavailable` (leaving `[".toml", ".js", ".ts", ".yaml", ".json"]`).
- [ ] **Step 6: Run to green** — `pytest tests/extraction/test_analyzer_c.py tests/extraction/test_analyzers.py tests/application/test_tool_router.py -q`. Expected: all pass.
- [ ] **Step 7: Lint + type + format** — `ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp`. Expected: clean.
- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/analyzers/c_lang.py \
        python/pydocs_mcp/extraction/strategies/analyzers/__init__.py \
        tests/extraction/test_analyzer_c.py \
        tests/application/test_tool_router.py
git commit -m "feat(extraction): CAnalyzer for .c/.h — calls + includes, empty inherits by design, empty alias table (AC-15, AC-18)"
```

---

## Task 6 — JavaScript analyzer (`javascript.py`, `.js`) + AC-16 fixture

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/analyzers/javascript.py`
- Modify: `python/pydocs_mcp/extraction/strategies/analyzers/__init__.py` (add `javascript` to the registration import)
- Test (create): `tests/extraction/test_analyzer_javascript.py`
- Test (modify): `tests/application/test_tool_router.py` (remove `".js"` from the unavailable parametrize)

**Interfaces:**
- Produces (LOCKED names): `JavaScriptAnalyzer` (registered `.js`), `normalize_js_import(stmt_text: str) -> tuple[dict[str, str], list[str]]`, `normalize_js_module_source(source: str) -> str`, `_JS_CALLS_QUERY` / `_JS_INHERITS_QUERY` / `_JS_IMPORTS_QUERY`. `normalize_js_import` and `normalize_js_module_source` are ALSO consumed by Task 7's `typescript.py`.

- [ ] **Step 1: Write the failing tests** — `tests/extraction/test_analyzer_javascript.py`:

```python
"""JavaScriptAnalyzer pins — registration, two-state capabilities (AC-7),
the D8 named-import example (AC-18), and the AC-16 require + class fixture."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_javascript")

from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.javascript import (
    normalize_js_import,
    normalize_js_module_source,
)
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from tests.extraction._analyzer_fixtures import (
    capture_fixture,
    edge_map,
    resolve_fixture,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def test_js_analyzer_is_registered_and_satisfies_the_protocol():
    assert isinstance(analyzer_registry[".js"], LanguageAnalyzer)


def test_ac7_capabilities_both_states(monkeypatch):
    assert analyzer_registry[".js"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert analyzer_registry[".js"].capabilities is TREESITTER_DEGRADED_CAPABILITIES


def test_ac18_normalizer_d8_canonical_named_import():
    # D8 canonical example: `import {X as Y} from './a/b'` → alias Y → a.b.X.
    assert normalize_js_import("import {X as Y} from './a/b'") == ({"Y": "a.b.X"}, ["a.b"])


def test_normalizer_default_namespace_and_source_shapes():
    assert normalize_js_import("import Z from './m'") == ({"Z": "m"}, ["m"])
    assert normalize_js_import("import * as N from './m'") == ({"N": "m"}, ["m"])
    assert normalize_js_import("import Z, {A} from './m'") == (
        {"Z": "m", "A": "m.A"},
        ["m"],
    )
    # Backtracking guard (red-green in the task that OWNS the regex; Task 7
    # re-pins the same shape through normalize_ts_import): a type-only named
    # import must NOT yield a spurious `type` default alias.
    assert normalize_js_import("import type { T } from './t'") == ({"T": "t.T"}, ["t"])
    assert normalize_js_import("import type Z from './m'") == ({"Z": "m"}, ["m"])
    assert normalize_js_module_source("./a/b") == "a.b"
    assert normalize_js_module_source("../x/y.js") == "x.y"


# AC-16 fixture: require + class heritage, one file. `const P = require(…)`
# is itself a top-level span, so the require's rows attribute to it — the
# pinned facts are the alias table, the IMPORTS target, and the two INHERITS
# resolutions.
_M_JS = (
    "const P = require('./a/b');\n"
    "class A {}\n"
    "class D extends A {}\n"
    "class E extends P.Base {}\n"
)


def test_ac16_js_require_and_class_fixture():
    universe, collector = capture_fixture({"pkg/m.js": _M_JS})
    assert collector.aliases == {"pkg.m.js": {"P": "a.b"}}
    edges = edge_map(resolve_fixture(universe, collector))
    # Expected-None: the normalizer strips source extensions while persisted
    # module qnames keep them (`a.b` vs `a.b.js`, §5.7) — JS IMPORTS rows
    # structurally never resolve in v1.
    imports = [
        (key, resolved)
        for key, resolved in edges.items()
        if key[2] == "imports" and key[1] == "a.b"
    ]
    assert imports and all(resolved is None for _key, resolved in imports)
    # Same-file single-segment heritage resolves.
    assert edges[("pkg.m.js.D", "A", "inherits")] == "pkg.m.js.A"
    # Rule-A-rewritten multi-segment heritage (P.Base → a.b.Base) → None.
    assert edges[("pkg.m.js.E", "P.Base", "inherits")] is None
```

- [ ] **Step 2: Run, see it fail** — `pytest tests/extraction/test_analyzer_javascript.py -q`. Expected: collection error.
- [ ] **Step 3: Create `python/pydocs_mcp/extraction/strategies/analyzers/javascript.py`** — full content:

```python
"""JavaScriptAnalyzer — CALLS / INHERITS / IMPORTS for ``.js`` (spec §5.4).

``require(...)`` calls are consumed by the imports pass and filtered out of
CALLS. Module-source normalization is purely syntactic (D8): strip leading
``./`` / ``../`` segments and a trailing extension, ``/`` → ``.`` — no
filesystem resolution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import register_analyzer
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    CaptureSession,
    ReferenceQueryRole,
    add_reference,
    canonical_target,
    capabilities_for,
    node_text,
    open_capture_session,
    record_aliases,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

_JS_CALLS_QUERY = """
(call_expression function: (identifier) @callee)
(call_expression function: (member_expression) @callee)
"""

_JS_INHERITS_QUERY = """
(class_heritage (identifier) @parent)
(class_heritage (member_expression) @parent)
"""

# File-scope only: ESM imports are top-level by grammar; CommonJS require is
# captured only at program-level lexical declarations (spec §5.4).
_JS_IMPORTS_QUERY = """
(program (import_statement) @import)
(program (lexical_declaration (variable_declarator
    name: (identifier) @binding
    value: (call_expression
        function: (identifier) @callee
        arguments: (arguments (string) @source)))))
"""

# Source specifier inside `from '…'` — the ESM anchor the text normalizer
# keys on. Named/default/namespace clauses are parsed from the same text.
_SOURCE_RE = re.compile(r"""from\s+['"]([^'"]+)['"]""")
_NAMED_RE = re.compile(r"\{([^}]*)\}")
_NAMESPACE_RE = re.compile(r"\*\s+as\s+([A-Za-z_$][\w$]*)")
# The (?!type\b) lookahead blocks a backtracking trap: without it, on
# `import type { T } from './t'` the optional `type\s+` group is skipped when
# the capture fails at `{`, and the capture then matches the KEYWORD `type`
# itself — a spurious default alias. With the lookahead, type-only named /
# namespace imports yield no default binding while `import type Z from './m'`
# and `import Z from './m'` still capture Z (spec §5.5: `import type`
# treated identically to a value import).
_DEFAULT_RE = re.compile(r"^import\s+(?:type\s+)?(?!type\b)([A-Za-z_$][\w$]*)")


@register_analyzer(".js")
@dataclass(frozen=True, slots=True)
class JavaScriptAnalyzer:
    """Tree-sitter syntactic reference backend for JavaScript."""

    @property
    def capabilities(self) -> LanguageCapabilities:
        return capabilities_for(".js")

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        session = open_capture_session(source, path=path, root=root)
        if session is None:
            return  # degraded — chunker's multilang_fallback log is the signal (D11)
        _capture_imports(session, _JS_IMPORTS_QUERY, from_package, collector)
        if "calls" in allowed:
            _capture_calls(session, _JS_CALLS_QUERY, from_package, collector)
        if "inherits" in allowed:
            _capture_inherits(session, _JS_INHERITS_QUERY, from_package, collector)


def _capture_calls(
    session: CaptureSession, query: str, from_package: str, collector: ReferenceCollector
) -> None:
    for captures in session.matches(ReferenceQueryRole.CALLS, query):
        nodes = captures.get("callee")
        if not nodes:
            continue
        text = node_text(nodes[0])
        if text == "require":
            continue  # consumed by the imports pass (spec §5.4)
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=canonical_target(text),
            kind=ReferenceKind.CALLS,
        )


def _capture_inherits(
    session: CaptureSession, query: str, from_package: str, collector: ReferenceCollector
) -> None:
    for captures in session.matches(ReferenceQueryRole.INHERITS, query):
        nodes = captures.get("parent")
        if not nodes:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=canonical_target(node_text(nodes[0])),
            kind=ReferenceKind.INHERITS,
        )


def _capture_imports(
    session: CaptureSession, query: str, from_package: str, collector: ReferenceCollector
) -> None:
    for captures in session.matches(ReferenceQueryRole.IMPORTS, query):
        stmt = captures.get("import")
        if stmt:
            _emit_statement_import(session, stmt[0], from_package, collector)
            continue
        _emit_require(session, captures, from_package, collector)


def _emit_statement_import(
    session: CaptureSession, node: Any, from_package: str, collector: ReferenceCollector
) -> None:
    aliases, targets = normalize_js_import(node_text(node))
    record_aliases(collector, session.module, aliases)
    for target in targets:
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(node),
            to_name=canonical_target(target),
            kind=ReferenceKind.IMPORTS,
        )


def _emit_require(
    session: CaptureSession,
    captures: dict[str, Any],
    from_package: str,
    collector: ReferenceCollector,
) -> None:
    callee, binding, source = (captures.get(k) for k in ("callee", "binding", "source"))
    if not (callee and binding and source) or node_text(callee[0]) != "require":
        return
    module = normalize_js_module_source(node_text(source[0]).strip("'\""))
    if not module:
        return
    record_aliases(collector, session.module, {node_text(binding[0]): module})
    add_reference(
        collector,
        from_package=from_package,
        from_node_id=session.enclosing_qname(binding[0]),
        to_name=canonical_target(module),
        kind=ReferenceKind.IMPORTS,
    )


def normalize_js_module_source(source: str) -> str:
    """``'./a/b'`` → ``a.b``; ``'../x/y.js'`` → ``x.y`` (spec §5.4).

    Purely syntactic: leading relative segments dropped, one trailing
    extension stripped, ``/`` → ``.`` — never resolved against the tree.
    """
    path = source
    while path.startswith(("./", "../")):
        path = path[2:] if path.startswith("./") else path[3:]
    stem, dot, ext = path.rpartition(".")
    if dot and "/" not in ext:
        path = stem
    return path.replace("/", ".")


def normalize_js_import(stmt_text: str) -> tuple[dict[str, str], list[str]]:
    """ESM import/export statement text → (alias entries, IMPORTS targets).

    D8 canonical example: ``import {X as Y} from './a/b'`` →
    ``({"Y": "a.b.X"}, ["a.b"])``. Re-exports (``export { X } from './a'``)
    and ``import type`` are handled by the same shapes (spec §5.5 reuses
    this via ``normalize_ts_import``). No ``from`` source → no rows.
    """
    match = _SOURCE_RE.search(stmt_text)
    if match is None:
        return {}, []
    module = normalize_js_module_source(match.group(1))
    if not module:
        return {}, []
    aliases: dict[str, str] = {}
    for name, alias in _named_clauses(stmt_text):
        aliases[alias] = f"{module}.{name}"
    namespace = _NAMESPACE_RE.search(stmt_text)
    if namespace is not None:
        aliases[namespace.group(1)] = module
    default = _DEFAULT_RE.match(stmt_text)
    if default is not None:
        aliases[default.group(1)] = module
    return aliases, [module]


def _named_clauses(stmt_text: str) -> list[tuple[str, str]]:
    """``{A as B, C, type D}`` → [("A", "B"), ("C", "C"), ("D", "D")]."""
    match = _NAMED_RE.search(stmt_text)
    if match is None:
        return []
    clauses: list[tuple[str, str]] = []
    for raw in match.group(1).split(","):
        words = raw.strip().split()
        if words and words[0] == "type" and len(words) > 1:
            words = words[1:]  # TS inline `type X` — treated identically (§5.5)
        if len(words) == 3 and words[1] == "as":
            clauses.append((words[0], words[2]))
        elif len(words) == 1:
            clauses.append((words[0], words[0]))
    return clauses


__all__ = ("JavaScriptAnalyzer", "normalize_js_import", "normalize_js_module_source")
```

- [ ] **Step 4: Wire registration** — extend the trailing import in `analyzers/__init__.py` to include `javascript` (alphabetical: `c_lang, javascript, rust`).
- [ ] **Step 5: Router parametrize** — remove `".js"` (leaving `[".toml", ".ts", ".yaml", ".json"]`).
- [ ] **Step 6: Run to green** — `pytest tests/extraction/test_analyzer_javascript.py tests/extraction/test_analyzers.py tests/application/test_tool_router.py -q`. Expected: all pass.
- [ ] **Step 7: Lint + type + format** — `ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp`. Expected: clean.
- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/analyzers/javascript.py \
        python/pydocs_mcp/extraction/strategies/analyzers/__init__.py \
        tests/extraction/test_analyzer_javascript.py \
        tests/application/test_tool_router.py
git commit -m "feat(extraction): JavaScriptAnalyzer — ESM + require imports, class heritage, require-filtered calls (AC-16, AC-18)"
```

---

## Task 7 — TypeScript analyzer (`typescript.py`, `.ts` + `.tsx`) + AC-14 fixture

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/analyzers/typescript.py`
- Modify: `python/pydocs_mcp/extraction/strategies/analyzers/__init__.py` (add `typescript` to the registration import)
- Test (create): `tests/extraction/test_analyzer_typescript.py`
- Test (modify): `tests/application/test_tool_router.py` (remove `".ts"` from the unavailable parametrize — `.tsx` was never in it)

**Interfaces:**
- Consumes: Task 6's `normalize_js_import` / `normalize_js_module_source` (spec §5.5: the TS normalizer is the JS normalizer plus `import type` / re-export shapes, which the JS text parser already handles — `normalize_ts_import` delegates and exists as the language module's named surface per spec §4.1).
- Produces (LOCKED names): `TypeScriptAnalyzer` (registered `.ts` AND `.tsx` — two stateless instances; capture derives the real extension from `path`, so `.tsx` files parse with the tsx dialect), `normalize_ts_import(stmt_text: str) -> tuple[dict[str, str], list[str]]`, `_TS_CALLS_QUERY` / `_TS_INHERITS_QUERY` / `_TS_IMPORTS_QUERY`.

- [ ] **Step 1: Write the failing tests** — `tests/extraction/test_analyzer_typescript.py`:

```python
"""TypeScriptAnalyzer pins — dual-dialect registration (.ts/.tsx), per-MODULE
two-state capabilities (AC-7, primary extension .ts hardcoded — one wheel,
two accessors, spec §4.2), re-export + type-import normalizer shapes, and
the AC-14 re-export + extends/implements fixture."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_typescript")

from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.typescript import normalize_ts_import
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from tests.extraction._analyzer_fixtures import (
    capture_fixture,
    edge_map,
    resolve_fixture,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def test_ts_analyzer_registered_for_both_dialects():
    assert isinstance(analyzer_registry[".ts"], LanguageAnalyzer)
    assert isinstance(analyzer_registry[".tsx"], LanguageAnalyzer)
    assert type(analyzer_registry[".ts"]) is type(analyzer_registry[".tsx"])


def test_ac7_capabilities_both_states_per_module(monkeypatch):
    assert analyzer_registry[".ts"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    assert analyzer_registry[".tsx"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert analyzer_registry[".ts"].capabilities is TREESITTER_DEGRADED_CAPABILITIES
    assert analyzer_registry[".tsx"].capabilities is TREESITTER_DEGRADED_CAPABILITIES


def test_normalizer_reexport_and_type_import_shapes():
    # Spec §5.5: re-export → IMPORTS row targeting the source + alias X → a.X;
    # `import type` treated identically to a value import.
    assert normalize_ts_import("export { X } from './a'") == ({"X": "a.X"}, ["a"])
    assert normalize_ts_import("import type { T } from './t'") == ({"T": "t.T"}, ["t"])
    assert normalize_ts_import("export * from './a'") == ({}, ["a"])
    assert normalize_ts_import("export class A {}") == ({}, [])  # no source → no rows


# AC-14 fixture, one file. Classes are deliberately UN-exported: the chunker's
# top-level query anchors on (program (class_declaration)), and an export
# wrapper would remove the spans the analyzer joins against.
_T_TS = (
    "export { X } from './a';\n"
    "interface I {}\n"
    "class A {}\n"
    "class B extends A implements I {}\n"
)


def test_ac14_ts_reexport_and_heritage_fixture():
    universe, collector = capture_fixture({"pkg/t.ts": _T_TS})
    assert collector.aliases == {"pkg.t.ts": {"X": "a.X"}}
    edges = edge_map(resolve_fixture(universe, collector))
    # Expected-None: extension-stripped `a` never matches `a.ts` (§5.7).
    assert edges[("pkg.t.ts", "a", "imports")] is None
    # Both heritage edges resolve same-file.
    assert edges[("pkg.t.ts.B", "A", "inherits")] == "pkg.t.ts.A"
    assert edges[("pkg.t.ts.B", "I", "inherits")] == "pkg.t.ts.I"


def test_tsx_files_capture_with_the_tsx_dialect():
    # A JSX-bearing file parses only under the tsx accessor — proves capture
    # derives the dialect from the path, not the module's primary extension.
    src = "class W {}\nclass V extends W {}\nconst view = () => <div/>;\n"
    universe, collector = capture_fixture({"pkg/v.tsx": src})
    edges = edge_map(resolve_fixture(universe, collector))
    assert edges[("pkg.v.tsx.V", "W", "inherits")] == "pkg.v.tsx.W"
```

- [ ] **Step 2: Run, see it fail** — `pytest tests/extraction/test_analyzer_typescript.py -q`. Expected: collection error.
- [ ] **Step 3: Create `python/pydocs_mcp/extraction/strategies/analyzers/typescript.py`** — full content:

```python
"""TypeScriptAnalyzer — CALLS / INHERITS / IMPORTS for ``.ts`` + ``.tsx``
(spec §5.5).

The TS grammar names classes with ``type_identifier`` where JS uses
``identifier`` (the "impossible pattern" note in ``multilang_queries.py``),
and adds interfaces / implements / re-exports — hence its own queries, not
JS + extras. Import normalization REUSES the JS text normalizer: the TS
additions (``import type``, re-exports) are shapes it already parses.
Dialect note: capture derives the extension from ``path`` (the session
loads ``language_tsx`` for ``.tsx``), while ``capabilities`` hardcodes the
primary extension ``.ts`` — one wheel, two accessors; the per-accessor skew
window is accepted and AC-7 pins per MODULE (spec §4.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.strategies.analyzers import register_analyzer
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    capabilities_for,
    open_capture_session,
)
from pydocs_mcp.extraction.strategies.analyzers.javascript import (
    _capture_calls,
    _capture_imports,
    _capture_inherits,
    normalize_js_import,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

_TS_CALLS_QUERY = """
(call_expression function: (identifier) @callee)
(call_expression function: (member_expression) @callee)
"""

_TS_INHERITS_QUERY = """
(extends_clause [(identifier) (member_expression)] @parent)
(implements_clause (type_identifier) @parent)
(implements_clause (generic_type (type_identifier) @parent))
(extends_type_clause (type_identifier) @parent)
"""

_TS_IMPORTS_QUERY = """
(program (import_statement) @import)
(program (export_statement) @import)
"""


@register_analyzer(".tsx")
@register_analyzer(".ts")
@dataclass(frozen=True, slots=True)
class TypeScriptAnalyzer:
    """Tree-sitter syntactic reference backend for TypeScript and TSX."""

    @property
    def capabilities(self) -> LanguageCapabilities:
        return capabilities_for(".ts")

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        session = open_capture_session(source, path=path, root=root)
        if session is None:
            return  # degraded — chunker's multilang_fallback log is the signal (D11)
        _capture_imports(session, _TS_IMPORTS_QUERY, from_package, collector)
        if "calls" in allowed:
            _capture_calls(session, _TS_CALLS_QUERY, from_package, collector)
        if "inherits" in allowed:
            _capture_inherits(session, _TS_INHERITS_QUERY, from_package, collector)


def normalize_ts_import(stmt_text: str) -> tuple[dict[str, str], list[str]]:
    """TS import/export/re-export text → (alias entries, IMPORTS targets).

    Delegates to the JS normalizer (spec §5.5): ``import type { T }`` and
    ``export { X } from './a'`` are shapes its text parser already handles;
    a statement without a ``from`` source (plain ``export class A {}``)
    yields no rows.
    """
    return normalize_js_import(stmt_text)


__all__ = ("TypeScriptAnalyzer", "normalize_ts_import")
```

**NOTE:** `typescript.py` imports `_capture_calls` / `_capture_inherits` / `_capture_imports` from `javascript.py` — they were written extension-agnostic in Task 6 (query passed as a parameter) precisely so TS reuses them without a copy. If ruff flags the private-name cross-import, keep it: it mirrors the established `_shared.py` private-helper convention, and the two modules are siblings inside one package. The ESM import statement pass calls `normalize_js_import`, which is behaviorally identical to `normalize_ts_import` (delegation) — the analyzer path and the named TS surface cannot drift.

- [ ] **Step 4: Wire registration** — extend the trailing import in `analyzers/__init__.py` to `c_lang, javascript, rust, typescript`.
- [ ] **Step 5: Router parametrize** — remove `".ts"` (leaving `[".toml", ".yaml", ".json"]`; Task 9 then widens the list to the FULL text/config set for AC-9).
- [ ] **Step 6: Run to green** — `pytest tests/extraction/test_analyzer_typescript.py tests/extraction/test_analyzers.py tests/application/test_tool_router.py -q`. Expected: all pass. (QueryError on `extends_type_clause` / `implements_clause` → grammar node-name drift; see the Task-4 NOTE.)
- [ ] **Step 7: Lint + type + format** — `ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp`. Expected: clean.
- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/analyzers/typescript.py \
        python/pydocs_mcp/extraction/strategies/analyzers/__init__.py \
        tests/extraction/test_analyzer_typescript.py \
        tests/application/test_tool_router.py
git commit -m "feat(extraction): TypeScriptAnalyzer for .ts/.tsx — type_identifier heritage, implements, re-exports (AC-14)"
```

---

## Task 8 — Java end-to-end: ceiling, chunker spec, analyzer (`java.py`, `.java`) + AC-17 fixture

**Files:**
- Modify: `python/pydocs_mcp/extraction/config.py` (`_CODE_EXTENSIONS` gains `".java"` — line ~37)
- Modify: `python/pydocs_mcp/extraction/strategies/chunkers/multilang_queries.py` (Java query + kinds + `LANGUAGE_SPECS` entry)
- Modify: `python/pydocs_mcp/extraction/strategies/chunkers/multilang_treesitter.py` (add `@_register_chunker(".java")` to the decorator stack)
- Create: `python/pydocs_mcp/extraction/strategies/analyzers/java.py`
- Modify: `python/pydocs_mcp/extraction/strategies/analyzers/__init__.py` (add `java` to the registration import)
- Test (create): `tests/extraction/test_analyzer_java.py`
- Test (modify): `tests/extraction/test_config.py` (the `test_allowed_extensions_is_frozenset` set pin gains `".java"`), `tests/extraction/test_multilang_treesitter.py` (the module-level `_CODE_EXTENSIONS` tuple at line 36 gains `".java"`; add the AC-30 Java chunker test)

**Interfaces:**
- Produces (LOCKED names): `LANGUAGE_SPECS[".java"] = ("tree_sitter_java", "language", _JAVA_QUERY, _JAVA_KINDS)` (top-level classes / interfaces / enums / records → `NodeKind.CLASS`; Java has no top-level functions, so NO FUNCTION mapping — AC-30); `JavaAnalyzer` (registered `.java`); `normalize_java_import(declaration_text: str) -> tuple[dict[str, str], list[str]]`; `_JAVA_CALLS_QUERY` / `_JAVA_INHERITS_QUERY` / `_JAVA_IMPORTS_QUERY`. `MULTILANG_EXTENSIONS` picks `.java` up automatically (`tuple(LANGUAGE_SPECS)`).
- **pyproject.toml is NOT touched here** — the grammar dep lands in Task 10's single promotion + relock. This task installs the wheel into the dev venv only.

- [ ] **Step 1: Install the grammar wheel into the dev venv** (test-only prerequisite; the packaging promotion is Task 10):

```bash
.venv/bin/pip install 'tree-sitter-java>=0.23,<0.24'
```

`uv lock --check` stays green — `pyproject.toml` is untouched in this task.

- [ ] **Step 2: Write the failing tests** — `tests/extraction/test_analyzer_java.py`:

```python
"""Java end-to-end pins — the new chunker spec (AC-30), analyzer
registration, two-state capabilities (AC-7), the D8 import example (AC-18),
and the AC-17 import + implements fixture."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_java")

from pydocs_mcp.extraction.config import ALLOWED_EXTENSIONS
from pydocs_mcp.extraction.model import NodeKind
from pydocs_mcp.extraction.serialization import chunker_registry
from pydocs_mcp.extraction.strategies.analyzers import (
    LanguageAnalyzer,
    analyzer_registry,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_ACTIVE_CAPABILITIES,
    TREESITTER_DEGRADED_CAPABILITIES,
)
from pydocs_mcp.extraction.strategies.analyzers.java import normalize_java_import
from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import (
    LANGUAGE_SPECS,
    MULTILANG_EXTENSIONS,
)
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
)
from tests.extraction._analyzer_fixtures import (
    capture_fixture,
    edge_map,
    resolve_fixture,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


# ── AC-30: Java joins the chunker stack end-to-end ─────────────────────────


def test_java_joins_ceiling_specs_and_chunker_registry():
    assert ".java" in ALLOWED_EXTENSIONS
    assert ".java" in MULTILANG_EXTENSIONS
    assert ".java" in chunker_registry
    grammar_module, accessor, _query, kinds = LANGUAGE_SPECS[".java"]
    assert (grammar_module, accessor) == ("tree_sitter_java", "language")
    # Java has no top-level functions — CLASS-only kind mapping (spec §5.6).
    assert set(kinds.values()) == {NodeKind.CLASS}


def test_ac30_java_fixture_builds_symbol_tree_with_1indexed_spans():
    src = (
        "import com.acme.G;\n"
        "class A {\n"
        "}\n"
        "interface B {}\n"
        "enum C { X }\n"
        "record R(int x) {}\n"
    )
    node = MultilangChunker().build_tree(
        path="pkg/Main.java", content=src, package="pkg", root=Path()
    )
    by_title = {child.title: child for child in node.children}
    assert set(by_title) == {"A", "B", "C", "R"}
    assert all(child.kind is NodeKind.CLASS for child in by_title.values())
    assert (by_title["A"].start_line, by_title["A"].end_line) == (2, 3)
    assert (by_title["R"].start_line, by_title["R"].end_line) == (6, 6)


# ── analyzer ───────────────────────────────────────────────────────────────


def test_java_analyzer_is_registered_and_satisfies_the_protocol():
    assert isinstance(analyzer_registry[".java"], LanguageAnalyzer)


def test_ac7_capabilities_both_states(monkeypatch):
    assert analyzer_registry[".java"].capabilities is TREESITTER_ACTIVE_CAPABILITIES
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert analyzer_registry[".java"].capabilities is TREESITTER_DEGRADED_CAPABILITIES


def test_ac18_normalizer_d8_canonical_import():
    # D8 canonical example: `import com.acme.G;` → alias G → com.acme.G.
    assert normalize_java_import("import com.acme.G;") == (
        {"G": "com.acme.G"},
        ["com.acme.G"],
    )


def test_normalizer_static_and_wildcard_shapes():
    assert normalize_java_import("import static com.acme.G.f;") == (
        {"f": "com.acme.G.f"},
        ["com.acme.G.f"],
    )
    assert normalize_java_import("import com.acme.*;") == ({}, ["com.acme"])


# AC-17 fixture, one file: LocalType has NO shadowing import.
_S_JAVA = (
    "import com.acme.G;\n"
    "interface I {}\n"
    "class LocalType {}\n"
    "class S implements I { void run() { new LocalType(); new G(); } }\n"
)


def test_ac17_java_import_and_implements_fixture():
    universe, collector = capture_fixture({"pkg/S.java": _S_JAVA})
    assert collector.aliases == {"pkg.S.java": {"G": "com.acme.G"}}
    edges = edge_map(resolve_fixture(universe, collector))
    # Expected-None (§5.7): extension-interleaved qnames — the IMPORTS row
    # and even the Rule-A-rewritten `new G()` constructor call miss.
    assert edges[("pkg.S.java", "com.acme.G", "imports")] is None
    assert edges[("pkg.S.java.S", "G", "calls")] is None
    # Must-resolve: same-file implements + unshadowed single-segment ctor.
    assert edges[("pkg.S.java.S", "I", "inherits")] == "pkg.S.java.I"
    assert edges[("pkg.S.java.S", "LocalType", "calls")] == "pkg.S.java.LocalType"
```

- [ ] **Step 3: Run, see it fail** — `pytest tests/extraction/test_analyzer_java.py -q`. Expected: first failures at the AC-30 tests (`.java` not in `ALLOWED_EXTENSIONS` / `LANGUAGE_SPECS`), then collection error on `analyzers.java`.
- [ ] **Step 4: Widen the ceiling** — `python/pydocs_mcp/extraction/config.py` line ~37:

```python
_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {".js", ".ts", ".tsx", ".c", ".h", ".rs", ".java"}
)
```

(The §4.1 contract-ceiling amendment this encodes is flagged for owner ratification in Task 13's ADR 0022 + PR description — the owner opened the gate 2026-07-28/29, spec §7.4 item 2.)

- [ ] **Step 5: Add the Java chunker spec** — in `multilang_queries.py`, insert before the `LanguageSpec` type alias:

```python
# --- Java (.java) — classes/interfaces/enums/records only; Java has no ------
# top-level functions, so there is deliberately NO FUNCTION mapping (spec
# §5.6 / ADR 0022). Names are plain ``identifier`` in this grammar.
_JAVA_QUERY = """
(program (class_declaration name:(identifier) @name) @item)
(program (interface_declaration name:(identifier) @name) @item)
(program (enum_declaration name:(identifier) @name) @item)
(program (record_declaration name:(identifier) @name) @item)
"""
_JAVA_KINDS: Mapping[str, NodeKind] = {
    "class_declaration": NodeKind.CLASS,
    "interface_declaration": NodeKind.CLASS,
    "enum_declaration": NodeKind.CLASS,
    "record_declaration": NodeKind.CLASS,
}
```

and add to `LANGUAGE_SPECS`:

```python
    ".java": ("tree_sitter_java", "language", _JAVA_QUERY, _JAVA_KINDS),
```

- [ ] **Step 6: Register the chunker** — in `multilang_treesitter.py`, add `@_register_chunker(".java")` to the top of the decorator stack on `MultilangChunker` (alongside the existing six).
- [ ] **Step 7: Create `python/pydocs_mcp/extraction/strategies/analyzers/java.py`** — full content:

```python
"""JavaAnalyzer — CALLS / INHERITS / IMPORTS for ``.java`` (spec §5.6).

Method invocations capture receiver + name as a dotted target
(``svc.run()`` → ``svc.run``; ``Files.read(p)`` → ``Files.read``); bare
calls capture the identifier alone. ``new G(...)`` object creation is a
CALLS edge to the constructed type (constructor call). ``package …;``
declarations are not captured — not an import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import register_analyzer
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    CaptureSession,
    ReferenceQueryRole,
    add_reference,
    canonical_target,
    capabilities_for,
    node_text,
    open_capture_session,
    record_aliases,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

_JAVA_CALLS_QUERY = """
(method_invocation object: (identifier) @recv name: (identifier) @meth)
(method_invocation object: (field_access) @recv name: (identifier) @meth)
(method_invocation !object name: (identifier) @meth)
(object_creation_expression type: (type_identifier) @ctor)
(object_creation_expression type: (scoped_type_identifier) @ctor)
"""

_JAVA_INHERITS_QUERY = """
(class_declaration (superclass (type_identifier) @parent))
(class_declaration (super_interfaces (type_list (type_identifier) @parent)))
(interface_declaration (extends_interfaces (type_list (type_identifier) @parent)))
"""

_JAVA_IMPORTS_QUERY = """
(import_declaration) @import
"""


@register_analyzer(".java")
@dataclass(frozen=True, slots=True)
class JavaAnalyzer:
    """Tree-sitter syntactic reference backend for Java."""

    @property
    def capabilities(self) -> LanguageCapabilities:
        return capabilities_for(".java")

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        session = open_capture_session(source, path=path, root=root)
        if session is None:
            return  # degraded — chunker's multilang_fallback log is the signal (D11)
        _capture_imports(session, from_package, collector)
        if "calls" in allowed:
            _capture_calls(session, from_package, collector)
        if "inherits" in allowed:
            _capture_inherits(session, from_package, collector)


def _capture_calls(session: CaptureSession, from_package: str, collector: ReferenceCollector) -> None:
    for captures in session.matches(ReferenceQueryRole.CALLS, _JAVA_CALLS_QUERY):
        target, anchor = _call_target(captures)
        if anchor is None:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(anchor),
            to_name=canonical_target(target),
            kind=ReferenceKind.CALLS,
        )


def _call_target(captures: dict[str, Any]) -> tuple[str, Any | None]:
    """One match → (raw dotted target, anchor node) or ("", None) to skip."""
    ctor = captures.get("ctor")
    if ctor:
        return node_text(ctor[0]), ctor[0]
    meth = captures.get("meth")
    if not meth:
        return "", None
    recv = captures.get("recv")
    if recv:
        return f"{node_text(recv[0])}.{node_text(meth[0])}", meth[0]
    return node_text(meth[0]), meth[0]


def _capture_inherits(session: CaptureSession, from_package: str, collector: ReferenceCollector) -> None:
    for captures in session.matches(ReferenceQueryRole.INHERITS, _JAVA_INHERITS_QUERY):
        nodes = captures.get("parent")
        if not nodes:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=canonical_target(node_text(nodes[0])),
            kind=ReferenceKind.INHERITS,
        )


def _capture_imports(session: CaptureSession, from_package: str, collector: ReferenceCollector) -> None:
    for captures in session.matches(ReferenceQueryRole.IMPORTS, _JAVA_IMPORTS_QUERY):
        nodes = captures.get("import")
        if not nodes:
            continue
        aliases, targets = normalize_java_import(node_text(nodes[0]))
        record_aliases(collector, session.module, aliases)
        for target in targets:
            add_reference(
                collector,
                from_package=from_package,
                from_node_id=session.enclosing_qname(nodes[0]),
                to_name=canonical_target(target),
                kind=ReferenceKind.IMPORTS,
            )


def normalize_java_import(declaration_text: str) -> tuple[dict[str, str], list[str]]:
    """``import [static] com.acme.G;`` → (alias entries, IMPORTS targets).

    D8 canonical example: ``import com.acme.G;`` →
    ``({"G": "com.acme.G"}, ["com.acme.G"])``. Static imports alias the
    member; wildcards emit an IMPORTS row only (spec §5.6).
    """
    text = declaration_text.strip().rstrip(";").strip()
    text = text.removeprefix("import").strip()
    text = text.removeprefix("static").strip()
    if text.endswith(".*"):
        target = canonical_target(text[:-2])
        return ({}, [target]) if target else ({}, [])
    target = canonical_target(text)
    if target is None:
        return {}, []
    return {target.rsplit(".", 1)[-1]: target}, [target]


__all__ = ("JavaAnalyzer", "normalize_java_import")
```

- [ ] **Step 8: Wire registration** — final trailing import in `analyzers/__init__.py`:

```python
from pydocs_mcp.extraction.strategies.analyzers import (  # noqa: E402,F401
    c_lang,
    java,
    javascript,
    rust,
    typescript,
)
```

- [ ] **Step 9: Update the two existing pins** — `tests/extraction/test_config.py::test_allowed_extensions_is_frozenset`: add `".java"` to the pinned frozenset literal. `tests/extraction/test_multilang_treesitter.py` line 36: `_CODE_EXTENSIONS = (".js", ".ts", ".tsx", ".c", ".h", ".rs", ".java")` (its `test_registered_for_every_code_extension` then covers the `.java` registration automatically).
- [ ] **Step 10: Run to green** — `pytest tests/extraction/test_analyzer_java.py tests/extraction/test_config.py tests/extraction/test_multilang_treesitter.py tests/application/test_tool_router.py -q`. Expected: all pass (`.java` was never in the router's unavailable parametrize — nothing to remove).
- [ ] **Step 11: Lint + type + format** — `ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp`. Expected: clean.
- [ ] **Step 12: Commit**

```bash
git add python/pydocs_mcp/extraction/config.py \
        python/pydocs_mcp/extraction/strategies/chunkers/multilang_queries.py \
        python/pydocs_mcp/extraction/strategies/chunkers/multilang_treesitter.py \
        python/pydocs_mcp/extraction/strategies/analyzers/java.py \
        python/pydocs_mcp/extraction/strategies/analyzers/__init__.py \
        tests/extraction/test_analyzer_java.py \
        tests/extraction/test_config.py \
        tests/extraction/test_multilang_treesitter.py
git commit -m "feat(extraction): Java end-to-end — allowlist ceiling, LANGUAGE_SPECS entry, chunker registration, JavaAnalyzer (AC-17, AC-18, AC-30)"
```

---

## Task 9 — Cross-language invariants: registry pins, drift guard, joinability, kind gating, degrade, router two-state

**Files:**
- Test (create): `tests/extraction/test_analyzers_multilang_integration.py`
- Test (modify): `tests/extraction/test_analyzers.py` (AC-2 exact-set pin; AC-4 duplicate-registration extended to `.rs`), `tests/application/test_tool_router.py` (AC-10/11 real-language two-state tests; AC-9 final parametrize widened to the FULL text/config set `[".toml", ".yaml", ".yml", ".cfg", ".ini", ".rst", ".txt", ".json"]` — spec AC-9 says "every text/config extension", not a sample)
- No production files change in this task — every test must pass against Tasks 1–8's code. A red test here is a defect in an earlier task; fix it THERE (smallest change), not by weakening the pin.

**Interfaces:** consumes only public/registered surfaces plus the sanctioned test seams (`_reset_multilang_caches`, `sys.modules` blocking, `stages_mod._CAPTURE_CONFIG` monkeypatch, `rust_mod.open_capture_session` fault injection).

- [ ] **Step 1: AC-2 + AC-4 in `tests/extraction/test_analyzers.py`.** Append:

```python
def test_ac2_registry_contains_exactly_the_nine_extensions():
    assert set(analyzer_registry) == {
        ".py", ".md", ".rs", ".c", ".h", ".js", ".ts", ".tsx", ".java",
    }
```

and extend `test_duplicate_registration_raises_at_import_time` with a tree-sitter case appended inside the same test (AC-4):

```python
    # AC-4, tree-sitter extension: same wiring-bug guarantee for .rs.
    original_rs = analyzer_registry[".rs"]
    with pytest.raises(ValueError, match=r"\.rs"):

        @register_analyzer(".rs")
        class ShadowRustAnalyzer:
            capabilities = PYTHON_CAPABILITIES

            def capture(self, source, *, path, root, from_package, allowed, collector):
                pass

    assert analyzer_registry[".rs"] is original_rs
```

- [ ] **Step 2: Router two-state (AC-10, AC-11) + final AC-9 state in `tests/application/test_tool_router.py`.** Verify Tasks 4–7 left the unavailable parametrize at `[".toml", ".yaml", ".json"]`, then WIDEN it to the full text/config set — spec AC-9 pins "`.toml` (and every text/config extension)", so the final parametrize is `[".toml", ".yaml", ".yml", ".cfg", ".ini", ".rst", ".txt", ".json"]`. Then append:

```python
_GRAMMAR_MODULES = {
    ".rs": "tree_sitter_rust",
    ".c": "tree_sitter_c",
    ".h": "tree_sitter_c",
    ".js": "tree_sitter_javascript",
    ".ts": "tree_sitter_typescript",
    ".tsx": "tree_sitter_typescript",
    ".java": "tree_sitter_java",
}


@pytest.fixture()
def _fresh_grammar_caches():
    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        _reset_multilang_caches,
    )

    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


@pytest.mark.parametrize("ext", sorted(_GRAMMAR_MODULES))
def test_ac10_references_resolution_treesitter_target_is_syntactic(ext, _fresh_grammar_caches):
    pytest.importorskip("tree_sitter")
    pytest.importorskip(_GRAMMAR_MODULES[ext])
    assert _resolution_for(ext) == "syntactic"


def test_ac11_references_resolution_degrades_to_unavailable(monkeypatch, _fresh_grammar_caches):
    import sys

    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        _reset_multilang_caches,
    )

    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    # §7.2 invariant end-to-end: a structurally empty graph never claims
    # "syntactic".
    assert _resolution_for(".rs") == "unavailable"
```

(AC-12 — `get_symbol` meta excludes the resolution field — is the existing channel-stripping test `test_references_resolution_channel_key_stripped_from_meta` plus `get_symbol`'s stripping test; confirm both still pass unmodified in Step 4's run.)

- [ ] **Step 3: The integration file** — `tests/extraction/test_analyzers_multilang_integration.py`:

```python
"""Cross-language invariants (spec §10): chunker/analyzer drift guard
(AC-5), kind gating parity (AC-19), file-scope attribution (AC-20),
unresolved-emission contract (AC-21), the joinability invariant + dedup
lockstep (AC-22), and the degrade seams (AC-24, AC-25, AC-26)."""

from __future__ import annotations

import logging
import sys
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
    # Alias tables survive for every aliasing language (D2)…
    aliases = new_state.refs.reference_aliases
    assert aliases["pkg.u.rs"] == {"C": "a.B"}
    assert aliases["pkg.m.js"] == {"Y": "a.b.X"}
    assert aliases["pkg.t.ts"] == {"X": "a.X"}
    assert aliases["pkg.S.java"] == {"G": "com.acme.G"}
    # …and C pins an EMPTY table (includes are not renaming imports, §5.3).
    assert "pkg.m.c" not in aliases


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
def test_ac20_file_scope_imports_attribute_to_the_module_qname(
    relpath, source, expected_target
):
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


@pytest.mark.parametrize("relpath", sorted(_JOINABILITY_FIXTURES))
def test_ac22_every_from_node_id_joins_the_persisted_tree(relpath):
    universe, collector = capture_fixture({relpath: _JOINABILITY_FIXTURES[relpath]})
    assert collector.refs, relpath
    for ref in collector.refs:
        assert ref.from_node_id in universe, (relpath, ref.from_node_id)


def test_ac22_dedup_case_keeps_analyzer_and_chunker_in_lockstep():
    # struct Node + impl Node → chunker slugs Node / Node_2 (shared helper);
    # the analyzer's attribution lands on the SAME deduped qname (§4.4).
    universe, collector = capture_fixture({"pkg/j.rs": _JOINABILITY_FIXTURES["pkg/j.rs"]})
    assert {"pkg.j.rs.Node", "pkg.j.rs.Node_2"} <= universe
    call = next(
        r for r in collector.refs
        if r.to_name == "helper" and r.kind is ReferenceKind.CALLS
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


def test_ac25_blocking_one_grammar_leaves_the_others_functional(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter_rust", None)
    _reset_multilang_caches()
    collector = ReferenceCollector()
    analyzer_registry[".rs"].capture(
        "fn f() { g(); }", path="pkg/x.rs", root=Path(),
        from_package="pkg", allowed=ALL_KINDS, collector=collector,
    )
    assert collector.refs == []  # .rs degraded
    analyzer_registry[".c"].capture(
        '#include "g.h"\n', path="pkg/m.c", root=Path(),
        from_package="pkg", allowed=ALL_KINDS, collector=collector,
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
```

- [ ] **Step 4: Run the invariants, then the whole suite** — `pytest tests/extraction/test_analyzers_multilang_integration.py tests/extraction/test_analyzers.py tests/application/test_tool_router.py -q`, then `pytest tests/ --ignore=tests/test_parity.py -q`. Expected: all pass. Any red here is fixed in the owning earlier task's code, never by weakening a pin.
- [ ] **Step 5: Lint + format** — `ruff format python/ tests/ && ruff check python/ tests/`. Expected: clean.
- [ ] **Step 6: Commit**

```bash
git add tests/extraction/test_analyzers_multilang_integration.py \
        tests/extraction/test_analyzers.py \
        tests/application/test_tool_router.py
git commit -m "test(extraction): cross-language invariants — registry set, drift guard, joinability, kind gating, degrade seams, router two-state (AC-2, AC-4, AC-5, AC-9..12, AC-19..22, AC-24..26)"
```

---

## Task 10 — Packaging promotion: required deps, `multilang = []` alias, mypy trim, single relock, audit

**Files:**
- Modify: `pyproject.toml` (`[project] dependencies` gains the six tree-sitter pins with their WHY comments moved along; `multilang = []` deprecated alias on the `watch = []` precedent at lines 75–82; the shared `[[tool.mypy.overrides]]` block at lines 316–335 loses ONLY the six `tree_sitter*` entries + their ADR 0021 comment)
- Modify: `python/pydocs_mcp/extraction/strategies/chunkers/multilang_treesitter.py` (`_INSTALL_HINT` reword per spec §6.2 + two extra-gating docstrings that become false under the promotion: the module docstring's "The tree-sitter dependency is optional: when ``[multilang]`` is installed…" paragraph at lines ~6–11 and `_load_language`'s "…or ``None`` when the ``[multilang]`` extra … is unavailable" at line ~126)
- Modify: `python/pydocs_mcp/extraction/strategies/chunkers/__init__.py` (the `.multilang_treesitter` bullet at line ~14 still says "behind the ``[multilang]`` extra")
- Modify: `uv.lock` (relock — the ONLY relock in this plan)
- Test (modify): `tests/test_pyproject_extras.py` (three new pins), `tests/extraction/test_multilang_treesitter.py` (fallback-log hint pin at lines ~263–270 updated)

**Interfaces:**
- Produces (LOCKED pin shapes, spec §6.1): `tree-sitter>=0.25,<0.26`, `tree-sitter-rust>=0.24,<0.25`, `tree-sitter-c>=0.24,<0.25`, `tree-sitter-javascript>=0.25,<0.26`, `tree-sitter-typescript>=0.23,<0.24`, `tree-sitter-java>=0.23,<0.24` — all in `[project] dependencies`; `multilang = []`.
- New `_INSTALL_HINT` (LOCKED text, spec §6.2 shape): `"reinstall pydocs-mcp from wheels (grammar unavailable or ABI-mismatched)"`. The log event name `multilang_fallback` and its one-per-extension discipline are unchanged.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_pyproject_extras.py` (reuse the module's existing `PYPROJECT` path constant and tomllib loading idiom):

```python
_TREE_SITTER_REQUIRED_PINS = {
    "tree-sitter>=0.25,<0.26",
    "tree-sitter-rust>=0.24,<0.25",
    "tree-sitter-c>=0.24,<0.25",
    "tree-sitter-javascript>=0.25,<0.26",
    "tree-sitter-typescript>=0.23,<0.24",
    "tree-sitter-java>=0.23,<0.24",
}


def test_tree_sitter_stack_is_required_not_optional():
    """Multilang-analyzers spec §6.1 (owner footprint waiver 2026-07-28/29):
    the core + five grammar wheels are required runtime deps with these
    exact pin shapes — a default install gets a working reference graph."""
    data = tomllib.loads(PYPROJECT.read_text())
    deps = set(data["project"]["dependencies"])
    assert _TREE_SITTER_REQUIRED_PINS <= deps


def test_multilang_extra_is_empty_backcompat_alias():
    """The [watch] precedent: `pip install pydocs-mcp[multilang]` stays a
    valid no-op; removal horizon next major version (spec §6.2)."""
    data = tomllib.loads(PYPROJECT.read_text())
    extras = data["project"]["optional-dependencies"]
    assert "multilang" in extras
    assert extras["multilang"] == []


def test_no_multilang_extra_install_hint_left():
    """The extra no longer installs anything — no shipped code may still
    tell operators to install it (mirrors test_no_watch_install_hint_left)."""
    pkg_root = PYPROJECT.parent / "python" / "pydocs_mcp"
    offenders = [
        str(path)
        for pattern in ("*.py", "*.yaml")
        for path in pkg_root.rglob(pattern)
        if "pydocs-mcp[multilang]" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
```

(If `tomllib` is not already imported at the top of the file, add `import tomllib`.)

- [ ] **Step 2: Run, see them fail** — `pytest tests/test_pyproject_extras.py -q`. Expected: the three new tests FAIL (deps still in the extra; hint still present).
- [ ] **Step 3: Edit `pyproject.toml`.**
  - In `[project] dependencies` (lines 49–65), append after the watchdog entry, moving the WHY comments from the extra:

```toml
    # Multilang reference analyzers + structural chunking (ADR 0021/0022).
    # Promoted from the [multilang] extra 2026-07 under an explicit owner
    # waiver of the <1% footprint clause (~6-10 MB on the ~90 MB baseline);
    # the other promotion criteria hold: zero transitive deps, prebuilt
    # wheels on every supported platform, first-class YAML surface
    # (discovery.*.include_extensions). tree-sitter 0.26.0 is EXCLUDED —
    # probe-verified use-after-free in QueryCursor.matches(); re-probe
    # before lifting the ceiling. Grammar wheels are official MIT abi3
    # builds; the tree-sitter core wheel is per-CPython (known cost).
    "tree-sitter>=0.25,<0.26",
    "tree-sitter-rust>=0.24,<0.25",
    "tree-sitter-c>=0.24,<0.25",
    "tree-sitter-javascript>=0.25,<0.26",
    "tree-sitter-typescript>=0.23,<0.24",
    "tree-sitter-java>=0.23,<0.24",
```

  - Replace the whole `multilang = [ … ]` extra (lines 114–133, including its old comment block) with:

```toml
# DEPRECATED alias: the tree-sitter core + grammar wheels moved into the
# required runtime deps (owner footprint waiver, 2026-07). Kept empty so
# `pip install pydocs-mcp[multilang]` in existing docs/scripts stays a
# valid no-op. Removal horizon: next major version.
multilang = []
```

  - In the shared `[[tool.mypy.overrides]]` block (lines 316–335): delete ONLY the six entries `"tree_sitter"`, `"tree_sitter.*"`, `"tree_sitter_javascript"`, `"tree_sitter_typescript"`, `"tree_sitter_c"`, `"tree_sitter_rust"` and the three-line ADR 0021 comment above them. `fast_plaid.*` / `fastembed.*` / `turbovec.*` / `yaml.*` / `numpy` / `numpy.*` and the block's `follow_imports = "skip"` / `ignore_missing_imports` settings remain untouched (AC-28). Do NOT add `tree_sitter_java` anywhere — the typecheck job now installs all grammars.

- [ ] **Step 4: Reword `_INSTALL_HINT`** in `multilang_treesitter.py` (line ~64):

```python
# The one actionable hint an operator sees when structural symbols are
# missing. The multilang extra is an empty no-op alias since the wheels
# became required deps (spec §6.2) — the only remaining degrade causes are a
# wheel-less sdist install or a grammar/core ABI mismatch, both fixed by
# reinstalling from wheels.
_INSTALL_HINT = "reinstall pydocs-mcp from wheels (grammar unavailable or ABI-mismatched)"
```

(Spell the deprecated extra WITHOUT brackets in comments here — Task 13 Step 9's audit greps `\[multilang\]` across `python/pydocs_mcp/` and must come back empty.)

Update the pinned log expectation in `tests/extraction/test_multilang_treesitter.py` (lines ~263–270): the `"hint"` field of the expected `multilang_fallback` JSON record becomes `"reinstall pydocs-mcp from wheels (grammar unavailable or ABI-mismatched)"`.

**In the same commit, fix the three shipped docstrings the promotion makes false** (spec AC-35 enumerates other sites; these three carry the same stale extra-gating claim and are otherwise unswept):

- `multilang_treesitter.py` module docstring (lines ~6–11): replace the sentence run `The tree-sitter dependency is optional: when ``[multilang]`` is installed the chunker emits STRUCTURAL symbols … when the extra is ABSENT it degrades INTERNALLY to the same fixed-line text windows T2 uses, so the file still indexes as searchable text — plus one structured ``multilang_fallback`` log carrying the install hint.` with: `The tree-sitter core and grammar wheels ship in the required runtime deps (ADR 0022; formerly the multilang extra). When a grammar loads, the chunker emits STRUCTURAL symbols (functions / classes / structs / …) with real 1-indexed spans; when it does not (wheel-less sdist install, grammar/core ABI mismatch) the chunker degrades INTERNALLY to the same fixed-line text windows T2 uses, so the file still indexes as searchable text — plus one structured ``multilang_fallback`` log carrying a reinstall-from-wheels hint.`
- `_load_language`'s docstring (line ~126): replace `Return a compiled tree-sitter ``Language`` for ``ext``, or ``None`` when the ``[multilang]`` extra (or the grammar's ABI) is unavailable.` with `Return a compiled tree-sitter ``Language`` for ``ext``, or ``None`` when the grammar wheel is absent (wheel-less sdist install) or ABI-rejected — the wheels ship in the required deps since ADR 0022.`
- `chunkers/__init__.py` (line ~14): replace the bullet tail `(ADR 0021 T3: the code set ``.js .ts .tsx .c .h .rs`` behind the ``[multilang]`` extra, with an internal text-window fallback when the extra is absent)` with `(ADR 0021 T3 / ADR 0022: the code set ``.js .ts .tsx .c .h .rs .java`` — grammar wheels ship in the required deps, with an internal text-window fallback when a wheel is absent or ABI-rejected)`.

- [ ] **Step 5: Relock (the ONLY relock in this plan)** — with the correct uv binary:

```bash
~/.local/bin/uv lock
~/.local/bin/uv lock --check
```

Expected: lock regenerates; check passes. Then verify AC-27's lock shape:

```bash
grep -n -A4 'name = "tree-sitter-java"' uv.lock
```

Expected: a `[[package]]` stanza with abi3 wheels spanning macOS x86_64/arm64 + manylinux + musllinux + Windows (the same platform spread as the existing four grammar stanzas). Also confirm the `multilang` extra resolution list is now empty and the six requirements appear WITHOUT an `extra == 'multilang'` marker.

- [ ] **Step 6: Refresh the venv + audit** —

```bash
.venv/bin/pip install -e .
.venv/bin/pip-audit --strict --local
```

Expected: install succeeds (grammar wheels now required); audit reports no known vulnerabilities. (Do NOT use pip-audit's requirement-file mode — it SIGABRTs under the sandbox.)

- [ ] **Step 7: Run to green** — `pytest tests/test_pyproject_extras.py tests/extraction/test_multilang_treesitter.py -q && mypy python/pydocs_mcp`. Expected: all pass; mypy green WITHOUT the tree_sitter overrides (AC-28) and without `unused section` warnings from `warn_unused_configs`.
- [ ] **Step 8: Lint + format** — `ruff format python/ tests/ && ruff check python/ tests/`. Expected: clean.
- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock \
        python/pydocs_mcp/extraction/strategies/chunkers/multilang_treesitter.py \
        python/pydocs_mcp/extraction/strategies/chunkers/__init__.py \
        tests/test_pyproject_extras.py \
        tests/extraction/test_multilang_treesitter.py
git commit -m "build: promote tree-sitter core + five grammar wheels to required deps (owner footprint waiver); [multilang] deprecated empty alias; mypy carve-out lifted (AC-27, AC-28)"
```

---

## Task 11 — Defaults split: per-scope `include_extensions` constants + partial-overlay pin

**Files:**
- Modify: `python/pydocs_mcp/extraction/config.py` (`_DEFAULT_PROJECT_INCLUDE_EXTENSIONS` / `_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS` module constants; `DiscoveryScopeConfig.include_extensions` default becomes the dependency constant; `DiscoveryConfig` per-scope `default_factory` wiring)
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (discovery section restates both lists — the sanctioned YAML duplication that is ALSO the partial-overlay backstop)
- Test (modify): `tests/extraction/test_config.py` (defaults pins become constants-equality per AC-29; new split + partial-overlay tests), `tests/retrieval/test_pipeline_hash_extension_scope.py` (AC-31 widening pin)

**Interfaces:**
- Produces (LOCKED names): `_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS: tuple[str, ...]` = the current 11-entry text/config default; `_DEFAULT_PROJECT_INCLUDE_EXTENSIONS: tuple[str, ...]` = the same 11 PLUS `.js .ts .tsx .c .h .rs .java`. The `_enforce_allowlist` validator is UNCHANGED (YAML still narrows within the ceiling only).

- [ ] **Step 1: Write the failing tests.**
  - In `tests/extraction/test_config.py`: delete the module-level `_EXPECTED_DEFAULT_EXTENSIONS` list; add `_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS, _DEFAULT_PROJECT_INCLUDE_EXTENSIONS` to the existing `from pydocs_mcp.extraction.config import (...)`; in `test_extraction_config_defaults_load` replace the two `include_extensions` assertions with:

```python
    # ADR 0022 / spec D6: per-scope defaults — constants-equality, not
    # literal repeats (AC-29). Project gains the seven code extensions;
    # dependency keeps text/config (census: dependency code skews vendored).
    assert cfg.discovery.project.include_extensions == list(
        _DEFAULT_PROJECT_INCLUDE_EXTENSIONS
    )
    assert cfg.discovery.dependency.include_extensions == list(
        _DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS
    )
```

and append two new tests:

```python
def test_per_scope_default_constants_split():
    """The split itself, pinned once as set algebra so neither constant can
    silently absorb the other's entries."""
    assert set(_DEFAULT_PROJECT_INCLUDE_EXTENSIONS) - set(
        _DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS
    ) == {".js", ".ts", ".tsx", ".c", ".h", ".rs", ".java"}
    assert set(_DEFAULT_PROJECT_INCLUDE_EXTENSIONS) <= ALLOWED_EXTENSIONS
    assert ".java" not in _DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS


def test_ac29_partial_project_overlay_keeps_the_widened_default(tmp_path):
    """A user overlay that sets another project-scope field but omits
    include_extensions MUST keep the widened default — the AppConfig layer
    merge over default_config.yaml is the guaranteed backstop (spec §6.3
    pins the behavior, not the mechanism)."""
    from pydocs_mcp.retrieval.config import AppConfig

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "extraction:\n"
        "  discovery:\n"
        "    project:\n"
        "      max_file_size_bytes: 4096\n"
    )
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.extraction.discovery.project.max_file_size_bytes == 4096
    assert ".rs" in cfg.extraction.discovery.project.include_extensions
    assert ".java" in cfg.extraction.discovery.project.include_extensions
    assert ".rs" not in cfg.extraction.discovery.dependency.include_extensions
```

  - In `tests/retrieval/test_pipeline_hash_extension_scope.py`, append (AC-31 — reuses the file's `_config_with_extensions` helper):

```python
def test_ac31_project_scope_default_widening_changed_the_hash(tmp_path: Path) -> None:
    # The 0.7.0 default (code extensions in project scope) is a DIFFERENT
    # corpus identity than the former text/config-only default — the
    # unconditional fold guarantees the one-time re-embed of spec §8.1.
    former_default = _config_with_extensions(
        tmp_path,
        "former.yaml",
        [".py", ".md", ".ipynb", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".rst", ".txt", ".json"],
    )
    assert AppConfig.load().ingestion_pipeline_hash != former_default.ingestion_pipeline_hash
```

- [ ] **Step 2: Run, see them fail** — `pytest tests/extraction/test_config.py tests/retrieval/test_pipeline_hash_extension_scope.py -q`. Expected: import error on the new constants, then assertion failures.
- [ ] **Step 3: Edit `python/pydocs_mcp/extraction/config.py`.**
  - Add the constants immediately below the `ALLOWED_EXTENSIONS` block:

```python
# ADR 0022 (multilang reference analyzers, spec D6): per-scope include
# defaults. The ADR 0021 census showed second-language code skews heavily
# vendored in DEPENDENCIES (e.g. 127 of matplotlib's 222 C/C++ files under
# extern/) while a user's own project code is exactly what they ask about —
# so code extensions are default-ON for project scope only. Single source of
# truth for both lists; defaults/default_config.yaml restates them (the
# sanctioned YAML duplication, which is also the partial-overlay backstop).
_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".md",
    ".ipynb",
    ".toml",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".rst",
    ".txt",
    ".json",
)
_DEFAULT_PROJECT_INCLUDE_EXTENSIONS: tuple[str, ...] = (
    _DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS + (".js", ".ts", ".tsx", ".c", ".h", ".rs", ".java")
)
```

  - Replace `DiscoveryScopeConfig.include_extensions`'s inline `default_factory=lambda: [".py", …]` (and its ADR 0021 T1 comment block) with:

```python
    # A BARE scope equals the dependency default; the effective per-scope
    # defaults live on DiscoveryConfig's factories below (spec D6). Kept as
    # a real default so directly-constructed scopes in tests stay valid.
    include_extensions: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS)
    )
```

  - Replace `DiscoveryConfig`'s two fields with:

```python
    # Per-scope defaults (ADR 0022 / spec D6). NOTE: a default_factory fires
    # only when the whole scope key is absent from the input dict; a PARTIAL
    # overlay (e.g. only max_file_size_bytes) is backstopped by
    # defaults/default_config.yaml restating the lists through the AppConfig
    # layer merge — AC-29 pins that behavior.
    project: DiscoveryScopeConfig = Field(
        default_factory=lambda: DiscoveryScopeConfig(
            include_extensions=list(_DEFAULT_PROJECT_INCLUDE_EXTENSIONS)
        )
    )
    dependency: DiscoveryScopeConfig = Field(
        default_factory=lambda: DiscoveryScopeConfig(
            include_extensions=list(_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS)
        )
    )
```

- [ ] **Step 4: Update `python/pydocs_mcp/defaults/default_config.yaml`** — replace the discovery section (lines 46–65) with:

```yaml
  discovery:
    project:
      # ADR 0022: project scope defaults to text/config PLUS the code
      # extensions — a user's own second-language code is exactly what they
      # ask questions about. Narrow this list to opt out (allowlist
      # semantics unchanged). Restates _DEFAULT_PROJECT_INCLUDE_EXTENSIONS.
      include_extensions: [".py", ".md", ".ipynb", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".rst", ".txt", ".json", ".js", ".ts", ".tsx", ".c", ".h", ".rs", ".java"]
      max_file_size_bytes: 1000000
      # Additive over the built-in floor; bare names match at any depth,
      # entries containing "/" anchor at the project root.
      # e.g. exclude_dirs: ["docs/generated", "fixtures"]
      exclude_dirs: []
    dependency:
      # ADR 0022: dependency scope keeps the text/config default — the
      # census showed second-language dependency code skews heavily
      # vendored. Add code extensions here to opt dependencies in.
      # Restates _DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS.
      include_extensions: [".py", ".md", ".ipynb", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".rst", ".txt", ".json"]
      max_file_size_bytes: 1000000
      # Additive over the built-in floor; applies to every dependency walk
      # (a dependency's own pyproject.toml is never consulted).
      # e.g. exclude_dirs: ["tests", "examples"]
      exclude_dirs: []
```

- [ ] **Step 5: Run to green, then the whole suite** — `pytest tests/extraction/test_config.py tests/retrieval/test_pipeline_hash_extension_scope.py -q`, then `pytest tests/ --ignore=tests/test_parity.py -q`. Expected: green. **Watch for collateral pins:** any test that asserted the OLD shared default through `DiscoveryConfig().project` (e.g. in `tests/extraction/test_discovery.py` or end-to-end suites) now sees the widened list — update those assertions to the new constants (`list(_DEFAULT_PROJECT_INCLUDE_EXTENSIONS)`), never by narrowing the shipped default back.
- [ ] **Step 6: Lint + type + format** — `ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp`. Expected: clean.
- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/extraction/config.py \
        python/pydocs_mcp/defaults/default_config.yaml \
        tests/extraction/test_config.py \
        tests/retrieval/test_pipeline_hash_extension_scope.py
git commit -m "feat(config): per-scope discovery defaults — project gains the seven code extensions, dependency stays text/config (D6, AC-29, AC-31)"
```

---

## Task 12 — Loadable-grammar fingerprint salt in the package content hash (D9, spec §8.2)

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/chunkers/multilang_treesitter.py` (new `loadable_grammar_fingerprint()`)
- Modify: `python/pydocs_mcp/extraction/pipeline/stages/content_hash.py` (unconditional grammar fold via a shared `_fold` helper)
- Test (create): `tests/extraction/test_content_hash_grammar_salt.py`
- Test (modify): `tests/extraction/test_stages.py` (the THREE equality pins that asserted "no effective excludes → byte-identical to raw hash_files" now compose the grammar fold — `test_content_hash_floor_only_is_byte_identical_to_unfolded`, `test_content_hash_empty_sentinel_is_unfolded`, AND `test_content_hash_floor_duplicate_entries_hash_like_floor_only` (~line 519, same final assertion shape); their docstrings' "skips as cached on the first post-upgrade run" claim is rewritten — §8.2 deliberately breaks that skip once, subsumed by the §8.1 re-embed)

**Interfaces:**
- Produces (LOCKED): `loadable_grammar_fingerprint() -> str` = `",".join(sorted(ext for ext in MULTILANG_EXTENSIONS if _load_language(ext) is not None))`; the fold formula `md5(f"{base}\x00grammars:{fingerprint}".encode(), usedforsecurity=False).hexdigest()[:16]`, applied UNCONDITIONALLY after the (still conditional) exclusion fold. `hash_files`, the Rust side, and the embedder-mismatch guard (`indexing_service.py:683–701`) are untouched (D7-of-the-toml-spec precedent: no Rust change).

- [ ] **Step 1: Write the failing tests** — `tests/extraction/test_content_hash_grammar_salt.py`:

```python
"""The loadable-grammar salt (spec §8.2, D9): the package content hash must
flip on BOTH grammar-availability transitions, fold unconditionally (empty
fingerprint distinguishable from not-folded), stay stable within one state,
and thereby rescue deployments whose graph was indexed empty (AC-33)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import (
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _reset_multilang_caches,
    loadable_grammar_fingerprint,
)

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")


@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


def _state(tmp_path: Path, f: Path) -> IngestionState:
    return IngestionState(
        files=FileBundle(target=tmp_path, target_kind=TargetKind.PROJECT, paths=(str(f),)),
    )


def _raw_hash_files(paths: list[str]) -> str:
    from pydocs_mcp._fast import hash_files

    result = hash_files(paths)
    return result if isinstance(result, str) else result.hex()


def test_fingerprint_lists_loadable_extensions_sorted():
    fingerprint = loadable_grammar_fingerprint()
    assert ".rs" in fingerprint.split(",")
    assert fingerprint == ",".join(sorted(fingerprint.split(",")))


def test_fingerprint_empty_when_grammars_blocked(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert loadable_grammar_fingerprint() == ""


@pytest.mark.asyncio
async def test_ac32_hash_differs_across_grammar_states_and_is_stable(
    tmp_path: Path, monkeypatch
) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    stage = ContentHashStage()

    with_grammars = (await stage.run(_state(tmp_path, f))).files.content_hash
    with_grammars_again = (await stage.run(_state(tmp_path, f))).files.content_hash
    assert with_grammars == with_grammars_again  # stable within one state

    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    blocked = (await stage.run(_state(tmp_path, f))).files.content_hash
    blocked_again = (await stage.run(_state(tmp_path, f))).files.content_hash
    assert blocked == blocked_again
    assert blocked != with_grammars  # flips on the transition (both ways)
    # Unconditional fold: the EMPTY fingerprint is still folded — the
    # blocked-state hash never equals the raw pre-0.7.0 framing.
    assert blocked != _raw_hash_files([str(f)])


@pytest.mark.asyncio
async def test_ac33_reextraction_rescue_skip_predicate_falls_through(
    tmp_path: Path, monkeypatch
) -> None:
    """A package indexed under blocked grammars stored hash H1; after the
    grammars become available, the recomputed hash H2 != H1, so
    `existing.content_hash == pkg.content_hash` (project_indexer.py:84-88,
    122-126) is False and the package re-extracts — references appear
    without any file touch."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    stage = ContentHashStage()

    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    stored_while_blocked = (await stage.run(_state(tmp_path, f))).files.content_hash

    monkeypatch.delitem(sys.modules, "tree_sitter", raising=False)
    _reset_multilang_caches()
    recomputed_with_grammars = (await stage.run(_state(tmp_path, f))).files.content_hash

    assert recomputed_with_grammars != stored_while_blocked
```

- [ ] **Step 2: Run, see them fail** — `pytest tests/extraction/test_content_hash_grammar_salt.py -q`. Expected: ImportError on `loadable_grammar_fingerprint`.
- [ ] **Step 3: Add the fingerprint helper** to `multilang_treesitter.py` (below `_load_language`):

```python
def loadable_grammar_fingerprint() -> str:
    """Sorted CSV of extensions whose grammar imports (analyzers spec §8.2).

    Probes only, no parses: ``_load_language`` memoizes success AND failure,
    so after first touch this costs one dict lookup per extension. The value
    reflects grammar availability at index time — exactly the thing that
    determines whether reference capture ran, which is why it salts the
    package content hash (D9's stranded-empty-graph rescue).
    """
    return ",".join(sorted(ext for ext in MULTILANG_EXTENSIONS if _load_language(ext) is not None))
```

Add `"loadable_grammar_fingerprint"` to the module's `__all__`.

- [ ] **Step 4: Fold it in `content_hash.py`** — replace the `_hash` method and add the module-level helper (keep the class's `run`, `from_dict`, `to_dict` untouched):

```python
    def _hash(self, paths: list[str], fingerprint: str | None) -> str:
        # Deferred so _fast's native/fallback choice is resolved lazily.
        from pydocs_mcp._fast import hash_files

        result = hash_files(paths)
        # hash_files may return str (fallback) or bytes (some native builds).
        # Normalize so downstream consumers see a stable str regardless.
        base = result if isinstance(result, str) else result.hex()
        if fingerprint is not None:
            # Conditional exclusion fold — unchanged semantics (spec §9.2 of
            # the exclude-dirs design: no user excludes → no fold, keeping
            # pre-upgrade stored hashes valid THROUGH that feature).
            base = _fold(base, fingerprint)
        # Loadable-grammar salt (analyzers spec §8.2, D9): UNCONDITIONAL —
        # unlike the exclusion fold, an empty fingerprint must be
        # distinguishable from "not folded", and the hash must flip on BOTH
        # transitions (grammars appear AND disappear). One-time full
        # re-extract on upgrade, subsumed by the §8.1 scope-fold re-embed.
        return _fold(base, f"grammars:{_grammar_fingerprint()}")


def _grammar_fingerprint() -> str:
    # Deferred: a stage module must not pull the chunker stack at import time.
    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        loadable_grammar_fingerprint,
    )

    return loadable_grammar_fingerprint()


def _fold(base: str, salt: str) -> str:
    """Digest-of-digest md5[:16] fold — the exclusion-fold shape (spec §8.2):
    hash_files' input framing is owned by the Rust/fallback parity pair and
    cannot grow a parameter, so salts wrap the base digest instead of
    entering it. md5 matches the fallback's non-cryptographic cache posture;
    [:16] matches the base digest width."""
    folded = hashlib.md5(f"{base}\x00{salt}".encode(), usedforsecurity=False)
    return folded.hexdigest()[:16]
```

(The old inline exclusion-fold code inside `_hash` is subsumed by `_fold`; the WHY comments about digest-of-digest move onto `_fold`.)

- [ ] **Step 5: Update the THREE broken pins in `tests/extraction/test_stages.py`.** Add one helper next to `_raw_hash_files`:

```python
def _grammar_folded(base: str) -> str:
    """0.7.0 framing: the UNCONDITIONAL loadable-grammar salt (analyzers spec
    §8.2) wraps the exclusion-fold output — the floor-only / sentinel cases
    now equal the salted base, not the raw hash_files framing."""
    import hashlib

    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        loadable_grammar_fingerprint,
    )

    salted = f"{base}\x00grammars:{loadable_grammar_fingerprint()}"
    return hashlib.md5(salted.encode(), usedforsecurity=False).hexdigest()[:16]
```

Then in all THREE raw-hash equality pins — `test_content_hash_floor_only_is_byte_identical_to_unfolded`, `test_content_hash_empty_sentinel_is_unfolded`, and `test_content_hash_floor_duplicate_entries_hash_like_floor_only` (~line 519; its final assertion is the same `out.files.content_hash == _raw_hash_files([str(f)])` shape) — change the final assertions to `assert out.files.content_hash == _grammar_folded(_raw_hash_files([str(f)]))`, and rewrite the three docstrings: the exclusion fold still folds NOTHING for floor-only / sentinel / floor-duplicate sets (that invariant stands, and the equality proves it — the only wrapper is the grammar salt), but the pre-0.7.0 "skips as cached on the first post-upgrade run" claim is superseded: the grammar salt deliberately re-extracts every package once on upgrade (spec §8.2), subsumed by the §8.1 re-embed. `test_content_hash_misses_when_exclude_added_paths_unchanged` passes unmodified (inequality survives a common outer fold).

- [ ] **Step 6: Run to green** — `pytest tests/extraction/test_content_hash_grammar_salt.py tests/extraction/test_stages.py tests/extraction/test_end_to_end.py tests/extraction/test_end_to_end_excludes.py -q`. Expected: all pass (end-to-end suites compare hashes within one process/state, so the salt is transparent to them; any test pinning a literal hash value must be updated to compute through `_grammar_folded`).
- [ ] **Step 7: Lint + type + format** — `ruff format python/ tests/ && ruff check python/ tests/ && mypy python/pydocs_mcp`. Expected: clean.
- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/chunkers/multilang_treesitter.py \
        python/pydocs_mcp/extraction/pipeline/stages/content_hash.py \
        tests/extraction/test_content_hash_grammar_salt.py \
        tests/extraction/test_stages.py
git commit -m "feat(indexing): unconditional loadable-grammar salt in the package content hash — re-extraction rescue for grammar transitions (D9, AC-32, AC-33)"
```

---

## Task 13 — ADR 0022, contract amendments, prose sweeps, version 0.7.0

**Files:**
- Create: `docs/adr/0022-multilang-reference-analyzers.md` (verify 0022 is still the next free number: `ls docs/adr/ | sort | tail -3` — if a 0022 landed meanwhile, take the next free number and update every reference in this task)
- Modify: `docs/tool-contracts.md` (§2.2 bullet rewrite at lines 110–118; §4.1 item 4 at lines 356–363; §5.1 rows inserted between lines 424 and 426)
- Modify: `python/pydocs_mcp/application/tool_router.py` (`_UNAVAILABLE_RESOLUTION` comment + `_resolution_for_ext` docstring), `python/pydocs_mcp/extraction/pipeline/stages/reference_capture.py` (module docstring cost note), `python/pydocs_mcp/extraction/strategies/references.py` (module docstring "Python-only capture today" paragraph)
- Modify: `CLAUDE.md` (three bullets + required-deps bullet), `README.md` ("Beyond Python" section + the live-reindex parenthetical), `CHANGELOG.md` (new 0.7.0 section), `pyproject.toml` (version)
- Test: no new tests — AC-34/AC-35 are auditable by grep; Step 9 runs the audits. The full suite must stay green (doc-conformance guards).

**Interfaces:** none (documentation + prose). The §2.2/§4.1/§5.1 edits land IN this PR and are **flagged explicitly in the PR description for owner ratification** (ADR 0007 precedent; spec §7.4 item 2 — unlike ADR 0021, this PR DOES apply the contract edits).

- [ ] **Step 1: Write `docs/adr/0022-multilang-reference-analyzers.md`** — full content:

```markdown
# ADR 0022 — Multilang reference analyzers: per-language tree-sitter capture, availability-aware capabilities, dependency promotion, and per-scope defaults

**Status:** Accepted — contract-line amendments (§2.2, §4.1, §5.1) applied in the implementation PR and flagged for owner ratification (ADR 0007 precedent; both prior ADR 0021 amendments followed this path and were ratified same-cycle) ·
**Date:** 2026-07-29 · **Phase:** feature (post-Phase-4, pre-paid-arc)

- **Decision area:** extending the reference graph (CALLS / INHERITS / IMPORTS + alias tables) from Python-only capture to `.rs .c .h .js .ts .tsx .java`; the declared capability matrix; packaging; discovery defaults; index-coherence migration. Owner: twelve decisions D1–D12 fixed interactively 2026-07-28/29 (design doc `docs/superpowers/specs/2026-07-29-multilang-reference-analyzers-design.md`), including two explicit gates: the <1% footprint-clause waiver for the tree-sitter promotion, and the `.java` ceiling widening.
- **Siblings:** ADR 0004 (the `LanguageAnalyzer` seam this ADR finally exercises; "references: unavailable is a legal launch state" — this is the follow-through), ADR 0021 (the chunker tiers, probe rules, and R9 individual-MIT-wheels-only decision this ADR completes), ADR 0007 (the owner-ratification amendment precedent used for the contract lines), ADR 0003 (the frozen nine-tool surface everything here lands behind).

## Context

ADR 0021 shipped multilanguage *indexing*: `MultilangChunker` persists top-level symbol trees for six code extensions, degrading internally to text windows when a grammar is absent. The reference graph stayed Python-only: `analyzer_registry` held exactly `.py` and `.md`, `ReferenceCaptureStage` silently skipped every other extension, and `get_references` for a non-Python target reported `meta.resolution = "unavailable"`. The graph is a differentiating retrieval signal (graph expansion ships in the default docs pipeline), and the asymmetry — multilanguage chunkers, discovery scope, and eval strata, but a Python-only graph — required the capability matrix to keep apologizing.

## Evidence

- **The seam was built for this.** ADR 0004 froze `LanguageAnalyzer` + `analyzer_registry` so adding a language is additive registration; `node_references` DDL is language-neutral TEXT (no schema change).
- **Trees already exist to join against.** The chunker's `LANGUAGE_SPECS` root-anchored queries produce top-level spans with real 1-indexed line numbers; attributing edges by bisecting those SAME spans, with the qname assignment hoisted into one shared helper (`chunkers/_shared.py`), makes joinability structural rather than aspirational (the markdown analyzer's WORKAROUND comment documents the failure mode this prevents).
- **The resolver is shape-compatible unchanged.** Rules B/C/D operate on plain dotted strings; suffix-preserving module qnames (`src.lib.rs`) are reachable by strict-suffix matching from single-segment dotted targets. Multi-segment targets miss on the interleaved extension segment (`a.B` vs `a.rs.B`) and return deterministic None — a recall cost, not wrong edges. File-scope (module-attributed) refs never alias-rewrite (`_module_part_of` strips the module qname's last segment) — also deterministic None, pinned expected-None in the test suite.
- **Probe rules carry over wholesale** (ADR 0021 / evidence-treesitter): `QueryCursor.matches()` never `captures()`; Tree + cursor bound to live locals; 1-indexed spans; the `0x3FFFFFFE` sentinel span guard; `tree-sitter>=0.25,<0.26` (0.26.0 use-after-free, probe-verified 5/5).
- **Census (ADR 0021):** second-language code skews vendored in dependencies (127 of matplotlib's 222 C/C++ files under `extern/`), while project code is what users ask about — the basis for the per-scope defaults split.

## Options considered

- **Single data-driven analyzer table** — REJECTED (owner, D4): the grammars genuinely differ (TS `type_identifier` vs JS `identifier` class names; C has includes and no inheritance; Java has no top-level functions); a table pushes differences into escape hatches. Per-language modules keep each language greppable and independently editable.
- **Keep `[multilang]` as the gate for the graph** — REJECTED (owner, D5): a default-ON reference graph that silently stays empty unless an extra is installed violates capability honesty from the other direction. Promotion under an explicit footprint waiver removes the cliff; the text-window degrade path stays for sdist/ABI-mismatch.
- **Filesystem-level import resolution** — REJECTED (D8): the same syntactic (not semantic) line Python draws; a future semantic backend flips only the declared value (ADR 0004 invariance).
- **Collapsing impl blocks onto the struct's qname** — REJECTED for v1 (§4.4): attribution follows the chunker's deduped span qnames verbatim (`Node`, `Node_2`); collapsing would break the joinability-by-construction invariant.
- **`tree-sitter-language-pack`** — remains REJECTED (ADR 0021 R9): individual official MIT wheels only.

## Decision

1. **Seven extensions, five analyzer modules** behind the existing seam: `rust.py` (`.rs`), `c_lang.py` (`.c .h`), `javascript.py` (`.js`), `typescript.py` (`.ts .tsx`), `java.py` (`.java`); `analyzers.py` becomes a package preserving the import path. Full capture-depth parity with Python where the language expresses the concept: CALLS + INHERITS + IMPORTS, alias tables always captured, IMPORTS rows filtered downstream (the existing stage contract).
2. **Top-level attribution, joinable by construction:** `from_node_id` is the enclosing top-level symbol qname (else the module qname), computed by bisecting the chunker's own spans with the qname assignment in ONE shared helper. Nested members stay unpersisted.
3. **Availability-aware capabilities:** `LanguageAnalyzer.capabilities` becomes a read-only property. Tree-sitter analyzers declare `{outline: available, definitions: available, references: syntactic}` when the grammar loads and `{outline: available, definitions: unavailable, references: unavailable}` degraded. `meta.resolution` never claims `"syntactic"` for a deployment whose graph is structurally empty.
4. **Packaging promotion:** `tree-sitter>=0.25,<0.26` + the five grammar wheels (`-rust`, `-c`, `-javascript`, `-typescript`, `-java`) move into `[project] dependencies`; `multilang = []` becomes a deprecated empty alias (the `watch = []` precedent). **Owner waiver recorded:** the <1% footprint clause of the promotion exception is explicitly waived for THIS promotion (~6–10 MB on ~90 MB); the other three criteria hold. The waiver does not soften the clause for future candidates.
5. **Per-scope defaults:** `discovery.project.include_extensions` gains the seven code extensions; `discovery.dependency` keeps text/config; `ALLOWED_EXTENSIONS` gains `.java`; `LANGUAGE_SPECS` gains a Java entry (classes/interfaces/enums/records → CLASS; no top-level functions).
6. **Migration:** the existing unconditional extension-scope → `ingestion_pipeline_hash` fold yields the one-time full re-embed; NEW: an unconditional loadable-grammar fingerprint salt in the package content hash rescues deployments whose graph was indexed empty (grammars appearing later re-extract unchanged packages).
7. **OWNER ESCALATION — the contract lines.** Three `docs/tool-contracts.md` amendments land in the implementation PR, flagged in the PR description for ratification: (a) §2.2's `"unavailable"` sentence rewritten for the two-state declaration (the "honest value for non-Python targets" parenthetical deleted — it inverts under this ADR); (b) §4.1's ceiling gains `.java` and the default sentence records the project/dependency split; (c) §5.1 gains the two-state capability rows for the six non-Python languages. The three-value `meta.resolution` enum, the frozen §5.1 vocabulary, and the nine-tool surface are UNCHANGED — no new tool, no new parameter, no envelope field.
8. **Version 0.7.0.**

## Consequences

Benefits: the reference graph covers every indexed code language with honest per-deployment declarations; adding a language remains additive registration; the resolver, storage schema, and MCP surface are untouched; the chunker/analyzer extension sets are pinned equal by a drift-guard test.

Costs and risks (accepted, recorded in the design doc §11): cross-language suffix-collision recall losses (deterministic None); multi-segment alias targets never resolve across the interleaved extension segment — JS/TS/Java IMPORTS rows structurally never resolve in v1 (only C's `.h`-keeping includes do); file-scope aliased refs never alias-rewrite; impl-block dedup granularity (`Node_2`); ~6–10 MB default-install growth (owner-waived); the per-CPython tree-sitter core wheel and the `<0.26` ceiling are now load-bearing in the default install; two parses per code file (bounded; caches shared); `.h` parsed as C.

## Action items

Product (`python/pydocs_mcp/`):

1. Package conversion + `_treesitter.py` shared plumbing + five language modules (implementation plan `docs/superpowers/plans/2026-07-29-multilang-reference-analyzers.md`, Tasks 1–8).
2. Capabilities property + router two-state behavior (Tasks 3, 9).
3. Packaging promotion + relock; per-scope defaults; grammar salt (Tasks 10–12).
4. Contract §2.2/§4.1/§5.1 edits + prose sweeps + version bump (Task 13).

Owner checkpoints:

5. Ratify the §2.2/§4.1/§5.1 amendments from the PR description (gate opened 2026-07-28/29; this ADR records the ratification wording once given).
6. Release-notes review: one-time full re-embed + re-extract on first index after 0.7.0; project-scope code files indexed by default (narrow via YAML to opt out); `[multilang]` now an empty no-op alias.
```

- [ ] **Step 2: Apply the §2.2 amendment** — in `docs/tool-contracts.md`, replace the bullet at lines 110–118 with:

```markdown
- `meta.resolution: str` — one of `"syntactic" | "semantic" | "unavailable"`, the
  declared capability level of the reference graph that produced the answer (§5.1).
  Python and the tree-sitter-backed languages ship declaring `"syntactic"` for
  analyzed targets; `"unavailable"` is declared when the target's language carries
  no registered reference analyzer, OR when a registered tree-sitter analyzer's
  grammar is unavailable in the deployment (§5.1 two-state declaration; ADR 0022 —
  amendment flagged for owner ratification, ADR 0007 precedent). If a semantic
  resolution backend is enabled by deployment configuration in a future release,
  only this declared value flips — names, parameters, and the rest of the envelope
  are invariant under that swap (ADR 0004).
```

- [ ] **Step 3: Apply the §4.1 amendment** — replace item 4 (lines 356–363) with:

```markdown
4. An **extension allowlist** enforced against the `ALLOWED_EXTENSIONS` ceiling
   (still an allowlist — extensions outside the ceiling are rejected at config
   load); the ceiling's code-extension list is `.js .ts .tsx .c .h .rs .java`
   (ADR 0022 adds `.java`; amendment flagged for owner ratification). The
   PROJECT-scope default set is `['.py', '.md', '.ipynb']` plus the text/config
   group (`.toml .yaml .yml .cfg .ini .rst .txt .json`) plus the code
   extensions (default-ON for project code, ADR 0022 — supersedes the former
   "ceiling-admitted but opt-in via YAML" wording for project scope); the
   DEPENDENCY scope keeps the text/config default, with code extensions
   opt-in via YAML. Plus `max_file_size_bytes = 1_000_000`
   (`DiscoveryScopeConfig`, `extraction/config.py`).
```

- [ ] **Step 4: Apply the §5.1 amendment** — insert between the current line 424 (end of the "flag surfaces in three places" paragraph) and line 426 (`### 5.2 …`):

```markdown
**Tree-sitter languages (Rust `.rs`, C `.c .h`, JavaScript `.js`, TypeScript
`.ts`, TSX `.tsx`, Java `.java`) declare availability-aware two-state
matrices (ADR 0022; the vocabulary above is unchanged — rows added, not
values):**

| State | outline | definitions | references |
|---|---|---|---|
| grammar loads | `available` | `available` | `syntactic` |
| degraded (grammar absent / ABI-rejected) | `available` | `unavailable` | `unavailable` |

Degraded `outline` stays `available` because the text-window fallback still
persists a module tree with spans; degraded `definitions` is `unavailable`
because no symbol nodes exist; degraded `references` is `unavailable` because
the analyzer emits nothing. Invariant: `meta.resolution` never claims
`"syntactic"` for a deployment whose reference graph is structurally empty
for that language. Dual-extension modules (`.c`/`.h`, `.ts`/`.tsx`) declare
per MODULE — each pair ships in one grammar wheel.
```

- [ ] **Step 5: Code prose sweeps (AC-35).**
  - `python/pydocs_mcp/application/tool_router.py` — replace the `_UNAVAILABLE_RESOLUTION` comment block (lines 69–73) and the `_resolution_for_ext` docstring with:

```python
# get_references meta.resolution value for a target whose extension carries no
# registered analyzer OR whose registered tree-sitter analyzer is degraded
# (grammar absent / ABI-rejected). The §5.1 LanguageCapabilities vocabulary
# admits it; ADR 0022's two-state declaration emits it so the router never
# overstates a structurally empty graph.
_UNAVAILABLE_RESOLUTION = "unavailable"


def _resolution_for_ext(ext: str | None) -> str:
    """Declared reference-resolution level for a target with extension ``ext``.

    Routes through the analyzer registry (ADR 0021 Decision 6 / ADR 0022):
    ``.py``/``.md`` and the seven tree-sitter code extensions carry registered
    analyzers — their ``references`` flag is deployment-dependent for the
    tree-sitter set ("syntactic" when the grammar loads, "unavailable"
    degraded). Text/config extensions and targets with no resolvable
    extension are unregistered → ``language_capabilities`` returns None →
    "unavailable".
    """
    caps = language_capabilities(ext) if ext else None
    return caps["references"] if caps is not None else _UNAVAILABLE_RESOLUTION
```

  - `python/pydocs_mcp/extraction/pipeline/stages/reference_capture.py` — in the module docstring, replace the sentence ending `…the cost is one extra ``ast.parse`` per file — bounded and only over ``.py`` files.` with: `…the cost is one extra parse per file — bounded: CPython ``ast`` for ``.py``, tree-sitter for the seven code extensions (grammar/query objects cached and shared with the chunker, ADR 0022).` Also update the parenthetical in the first paragraph — replace `(ADR 0004 seam — ``.py`` runs the CPython-ast emitters, ``.md`` the regex MENTIONS capture; unknown extensions are skipped, mirroring ``ChunkingStage``'s chunker_registry policy)` with `(ADR 0004 seam — ``.py`` runs the CPython-ast emitters, ``.md`` the regex MENTIONS capture, and the seven tree-sitter code extensions run their per-language analyzers (ADR 0022); unknown extensions are skipped, mirroring ``ChunkingStage``'s chunker_registry policy)`.
  - `python/pydocs_mcp/extraction/strategies/references.py` — replace the docstring paragraph `Python-only capture today. Markdown / notebook chunkers do NOT emit references (per spec Decision 7). MENTIONS edges land via the markdown chunker's separate capture path.` with:

```
The emitters here are the PYTHON (CPython-ast) capture path; the seven
tree-sitter code extensions capture through their per-language analyzers
(``extraction/strategies/analyzers/``, ADR 0022) into the same
``ReferenceCollector``. Notebook chunkers do NOT emit references; MENTIONS
edges land via the markdown analyzer's separate capture path.
```

- [ ] **Step 6: CLAUDE.md sweeps.** Three bullets (grep-anchor before editing — the lines are long single-line bullets):
  - The **Multilanguage indexing bullet** (line ~23, starts `- **Multilanguage indexing (ADR 0021)** —`): rewrite the tail from "code extensions … stay **ceiling-only opt-in**" onward so it reads (keeping T1/T2 text before it intact): code extensions (`.js .ts .tsx .c .h .rs .java`) are **default-ON for the project scope** and opt-in via YAML for the dependency scope (ADR 0022); **T3** is the availability-aware `MultilangChunker` registered once per code extension — tree-sitter + the official MIT grammar wheels now ship in the required deps (owner footprint waiver), with the internal T2 text-window degrade retained for sdist/ABI-mismatch plus one structured `multilang_fallback` JSON log carrying a reinstall-from-wheels hint; the **capability matrix** is availability-aware (ADR 0022): any registered chunker's tree feeds `search_codebase`, `get_symbol`, `get_context`, and `parent_rollup` for every language; the reference graph (CALLS/INHERITS/IMPORTS + alias tables) now covers `.py` plus the seven code extensions via per-language analyzers under `extraction/strategies/analyzers/`, declaring `"syntactic"` when the grammar loads and `"unavailable"` degraded (`meta.resolution` never overstates a structurally empty graph); `module_members` stays **Python-only**; the effective extension scope still folds unconditionally into `ingestion_pipeline_hash`, and the package content hash carries a loadable-grammar salt so grammar transitions re-extract.
  - The **required-deps bullet** (line ~148, "Required runtime deps"): append the six tree-sitter pins to the dependency list: `tree-sitter>=0.25,<0.26` + `tree-sitter-rust`/`-c`/`-javascript`/`-typescript`/`-java` grammar wheels (~6–10MB, promoted 2026-07 under an owner footprint waiver).
  - The **Optional extras bullet** (line ~150): move `[multilang]` out of the active-extras list into the deprecated-alias sentence next to `[watch]` (`[watch]` and `[multilang]` are deprecated empty aliases); delete the old `[multilang]`-adds-tree-sitter sentence; replace the mypy clause with: `tree_sitter` remains imported function-locally (the degrade path for sdist installs), and the grammar deps are installed in the typecheck job (the `tree_sitter*` mypy overrides are gone); amend the promotion-exception sentence to record: watchdog (2026-07) is the precedent; the tree-sitter stack (2026-07) was promoted under an explicit owner waiver of the <1% footprint clause, scoped to that promotion only — the clause is not softened for future candidates.
  - The **extension-registry bullet** (line ~157): the code set becomes `(.js .ts .tsx .c .h .rs .java)`.
- [ ] **Step 7: README + CHANGELOG + version.**
  - `README.md` "Beyond Python — multilanguage indexing" section (lines ~198–241): rewrite so "Indexed by default" covers Python, notebooks, text/config formats, AND — for the project's own code — JavaScript, TypeScript/TSX, C, Rust, and Java; "Code languages, opt-in" becomes a "Dependencies stay text/config by default" paragraph (name the extensions in `discovery.dependency.include_extensions` to opt dependencies in; narrow `discovery.project.include_extensions` to opt project code out); delete the `pip install 'pydocs-mcp[multilang]'` block (grammars ship in the default install; the extra is a deprecated no-op) while keeping the degrade sentence ("without usable grammar wheels — e.g. an sdist install — code files still index as searchable text; a one-line log tells you why symbols are missing"); rewrite "What works per language (today)": full-text search, symbol outlines, and surrounding-context expansion work for every indexed language; the call/import/reference graph now also covers the code languages (declared `syntactic`, precision-biased, name/alias-matched — not scope-resolved; when a grammar is unavailable the tool reports resolution as unavailable rather than pretending); per-symbol member listings remain Python-only; vendored trees and binary assets are never indexed. Update the live-reindex parenthetical at lines ~166–169 to "(Python, docs, config, and — for project scope — code files indexed by default; see Beyond Python)". Run the README-jargon audit from CLAUDE.md before committing (no PR numbers/task IDs).
  - `CHANGELOG.md`: insert above the `## [0.6.0] — Unreleased` section:

```markdown
## [0.7.0] — Unreleased

Headline: the reference graph goes multilanguage. Per-language tree-sitter
analyzers capture CALLS / INHERITS / IMPORTS edges (plus import-alias tables)
for Rust, C, JavaScript, TypeScript/TSX, and Java behind the existing
`get_references` surface, attributed to the same top-level symbols the
multilanguage chunker persists. Capability declarations are availability-aware:
`meta.resolution` reports `syntactic` only when the language's grammar actually
loads. No new tools, parameters, or envelope fields.

### Added
- Per-language reference analyzers for `.rs`, `.c`/`.h`, `.js`, `.ts`/`.tsx`,
  and `.java` (`extraction/strategies/analyzers/`), joinable by construction
  with the persisted document trees.
- Java end-to-end: extension ceiling, structural chunker spec
  (classes/interfaces/enums/records), grammar wheel, analyzer.
- A loadable-grammar fingerprint salt in the package-level content hash:
  deployments indexed while grammars were unavailable re-extract automatically
  once grammars appear (no file touch needed).

### Changed
- **One-time full re-embed + re-extract on the first index after upgrading**
  (extension-scope and grammar-salt hash folds) — expected duration scales
  with corpus size like a `--force` reindex.
- Project-scope discovery now indexes code files (`.js .ts .tsx .c .h .rs
  .java`) by default; dependency scope keeps the text/config default. Narrow
  `discovery.project.include_extensions` in YAML to opt out (allowlist
  semantics unchanged).
- `tree-sitter` and the five official MIT grammar wheels are required runtime
  dependencies; `[multilang]` is now an empty no-op alias — remove it from
  install scripts at leisure. Wheel-less installs still index code as
  searchable text and honestly report reference resolution as unavailable.
```

  - `pyproject.toml` line 7: `version = "0.7.0"`. **NOTE (spec §1.3 item 3):** the worktree carries 0.5.1 while docs reference a 0.6.0 rename; 0.7.0 is the intended landing number assuming 0.6.0 lands first. If at merge time main still carries <0.6.0, raise the numbering with the owner in the PR — the bump is "next minor after whatever main carries", 0.7.0 preferred. Do not tag or publish anything (merges and publishes are separate consents).
- [ ] **Step 8: Run to green** — full suite: `pytest tests/ --ignore=tests/test_parity.py -q`. Expected: green (doc-conformance and pyproject pins included).
- [ ] **Step 9: Audits** —

```bash
# README jargon audit (must print nothing):
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
# Stale-prose audit (must print nothing in python/):
grep -rn "Python-only capture today\|only over \`\`.py\`\` files\|pydocs-mcp\[multilang\]" python/pydocs_mcp/
# Stale extra-gating audit (must print nothing): no shipped code/docstring may
# still describe [multilang] gating — the wheels are required deps. Task 10
# Step 4 swept the three known docstrings and spells the deprecated alias
# WITHOUT brackets in comments, so a bare bracket-form hit is a regression.
grep -rn "\[multilang\]" python/pydocs_mcp/
```

- [ ] **Step 10: Lint + format** — `ruff format python/ tests/ benchmarks/ && ruff check python/ tests/ benchmarks/`. Expected: clean.
- [ ] **Step 11: Commit**

```bash
git add docs/adr/0022-multilang-reference-analyzers.md \
        docs/tool-contracts.md \
        python/pydocs_mcp/application/tool_router.py \
        python/pydocs_mcp/extraction/pipeline/stages/reference_capture.py \
        python/pydocs_mcp/extraction/strategies/references.py \
        CLAUDE.md README.md CHANGELOG.md pyproject.toml
git commit -m "docs: ADR 0022 + tool-contracts §2.2/§4.1/§5.1 amendments (flagged for owner ratification) + capability-matrix prose sweeps; version 0.7.0 (AC-34, AC-35)"
```

**NOTE:** the version-line change in `pyproject.toml` does not alter dependency resolution, but run `~/.local/bin/uv lock && ~/.local/bin/uv lock --check` after the bump — `uv.lock` records the project's own version, so the lockfile gate needs the refresh. If the lock changed, amend it into this commit: `git add uv.lock && git commit --amend --no-edit`.

---

## Task 14 — Full-gate verification run + AC cross-off + PR notes

**Files:** none (verification only; fix-forward commits allowed for gate failures, each scoped to the owning task's files).

- [ ] **Step 1: The full CI gate set** (all must pass):

```bash
ruff format --check python/ tests/ benchmarks/
ruff check python/ tests/ benchmarks/
mypy python/pydocs_mcp
complexipy python/pydocs_mcp --max-complexity-allowed 15
vulture python/pydocs_mcp --min-confidence 80
pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90
uv lock --check
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
.venv/bin/pip-audit --strict --local
```

After the local complexipy run: `git checkout -- complexipy-snapshot.json` (the local run rewrites it in place; never stage it).

- [ ] **Step 2: Cross off the AC coverage table below** — every AC-1..AC-36 row must map to a passing test or an audited artifact. AC-36 is this step itself.
- [ ] **Step 3: PR description must include** (when the branch is pushed and a PR opened — pushing/PR-opening only on the owner's word):
  - The **owner-ratification flag** for the `docs/tool-contracts.md` §2.2 / §4.1 / §5.1 amendments (ADR 0007 precedent; ADR 0022 carries them) — quote the three amended passages.
  - The **owner footprint-waiver record** for the dependency promotion (D5; waiver scoped to this promotion).
  - The four release-notes items of spec §8.3 (re-embed; re-extract subsumed; project-scope code default-ON + how to narrow; `[multilang]` empty alias).
  - No `Co-Authored-By` trailers anywhere in the branch: verify with `git log --format='%(trailers)' <base>..HEAD | grep -i co-authored` (must print nothing).

---

## AC coverage table

| AC | What it pins | Task | Where |
|---|---|---|---|
| AC-1 | Package import path + seam surface unchanged | 1 | `test_analyzers_import_path_is_a_package_with_the_full_seam_surface` |
| AC-2 | Registry == exactly the nine extensions | 9 | `test_ac2_registry_contains_exactly_the_nine_extensions` |
| AC-3 | Every analyzer isinstance-passes the property-shaped Protocol | 3, 4–8 | `test_registered_analyzers_satisfy_protocol` + per-language registration tests |
| AC-4 | Duplicate registration raises, original survives (incl. `.rs`) | 9 | extended `test_duplicate_registration_raises_at_import_time` |
| AC-5 | Analyzer/chunker extension parity drift guard | 9 | `test_ac5_treesitter_analyzer_extensions_match_language_specs_exactly` |
| AC-6 | `PYTHON_CAPABILITIES` + golden 9-row edge set unmodified | 1, 14 | existing pins pass throughout |
| AC-7 | Two-state capability pins per language module | 4–8 | `test_ac7_capabilities_*` in each per-language file |
| AC-8 | `language_capabilities(".rs")` active-dict (old `is None` pin replaced) | 4 | rewritten `test_language_capabilities_lookup` + rust test |
| AC-9 | Text/config extensions still `None`/unavailable | 4–7, 9 | final full text/config parametrize (all eight extensions) + `.toml` lookup pin |
| AC-10 | Router `syntactic` with grammar present | 9 | `test_ac10_references_resolution_treesitter_target_is_syntactic` |
| AC-11 | Router `unavailable` degraded | 9 | `test_ac11_references_resolution_degrades_to_unavailable` |
| AC-12 | `get_symbol` meta excludes resolution (channel stripping) | 9 | existing stripping tests re-verified |
| AC-13 | Rust two-file fixture (must-resolve + expected-None) | 4 | `test_ac13_rust_two_file_fixture_resolution_floor` |
| AC-14 | TS re-export + extends/implements fixture | 7 | `test_ac14_ts_reexport_and_heritage_fixture` |
| AC-15 | C prototype + include fixture | 5 | `test_ac15_c_prototype_and_include_fixture` |
| AC-16 | JS require + class fixture | 6 | `test_ac16_js_require_and_class_fixture` |
| AC-17 | Java import + implements fixture | 8 | `test_ac17_java_import_and_implements_fixture` |
| AC-18 | Four D8 canonical normalizer pins | 4, 5, 6, 8 | `test_ac18_normalizer_d8_*` per language |
| AC-19 | Calls-only gating: aliases survive, IMPORTS/INHERITS filtered, C table empty | 9 (+5) | `test_ac19_calls_only_keeps_aliases_and_drops_imports_inherits` |
| AC-20 | File-scope module attribution + aliased-call expected-None | 9 | `test_ac20_*` pair |
| AC-21 | `to_node_id is None` at capture; no `class_attribute_types` | 9 | `test_ac21_capture_emits_unresolved_and_no_class_attribute_types` |
| AC-22 | Joinability invariant + dedup lockstep | 9 | `test_ac22_*` pair |
| AC-23 | Single shared span→qname function (structural) | 2 | `test_ac23_span_qname_assignment_is_a_single_shared_function` |
| AC-24 | Degrade: zero rows, text windows, ONE fallback log, no second log | 9 | `test_ac24_blocked_grammar_analyzer_noops_chunker_logs_once` |
| AC-25 | Per-extension grammar independence | 9 | `test_ac25_blocking_one_grammar_leaves_the_others_functional` |
| AC-26 | Per-file stage containment on the tree-sitter path | 9 | `test_ac26_per_file_containment_on_the_treesitter_path` |
| AC-27 | Six pins required; `multilang = []`; lock resolves Java abi3 wheel | 10 | `test_tree_sitter_stack_is_required_not_optional` + lock grep |
| AC-28 | mypy tree_sitter overrides removed, other families intact, mypy green | 10 | pyproject edit + `mypy python/pydocs_mcp` |
| AC-29 | `.java` in ceiling; split default constants; partial-overlay keeps widened default | 8, 11 | `test_per_scope_default_constants_split` + `test_ac29_partial_project_overlay_keeps_the_widened_default` |
| AC-30 | Java LANGUAGE_SPECS (CLASS-only) + chunker registration + spans | 8 | `test_ac30_java_fixture_builds_symbol_tree_with_1indexed_spans` |
| AC-31 | Pipeline hash changes under the default widening | 11 | `test_ac31_project_scope_default_widening_changed_the_hash` |
| AC-32 | Grammar salt: differs across states, stable within, unconditional | 12 | `test_ac32_hash_differs_across_grammar_states_and_is_stable` |
| AC-33 | Re-extraction rescue (skip predicate falls through) | 12 | `test_ac33_reextraction_rescue_skip_predicate_falls_through` |
| AC-34 | ADR 0022 + contract edits applied + PR ratification flag | 13, 14 | ADR file + §2.2/§4.1/§5.1 diffs + PR description |
| AC-35 | Stale-prose sweep (router docstring, stage note, references docstring, contract sentence, CLAUDE.md matrix, `_INSTALL_HINT`) | 10, 13 | grep audits in Task 13 Step 9 |
| AC-36 | Full CI gate set green; code-shape rules hold | 14 | the gate run |

## Deviations and encoded tensions (do not resolve silently)

1. **AC-26 vs tree-sitter error tolerance:** tree-sitter never raises on syntactically broken input (it produces ERROR nodes), so a literally "broken .rs file" cannot trigger the stage's `except Exception` containment. The AC-26 test injects a deterministic fault at the analyzer's session seam instead, exercising the exact containment contract the AC names. If the owner prefers a naturally-raising input (e.g. undecodable bytes), swap the injection for that in the same test.
2. **Version number:** the worktree is at 0.5.1 while the CHANGELOG carries `## [0.6.0] — Unreleased`. Task 13 bumps to 0.7.0 per D10 and records the spec §1.3 caveat; if this ships before the 0.6.0 rename lands on main, the owner decides the final number in the PR ("next minor after whatever main carries", 0.7.0 preferred).
3. **Task 8's venv-only Java wheel install:** the spec puts the `tree-sitter-java` dependency in the packaging move (D5); this plan installs it into the dev venv at Task 8 (so AC-17/AC-30 tests can run) and lands the pyproject change + single relock at Task 10 — one relock instead of two, `uv lock --check` green throughout.
4. **Tree-sitter query node names:** the spec fixes capture semantics, not S-expression text. The queries in Tasks 4–8 are best-effort against the pinned grammar wheels; a `QueryError` in a red-green loop is corrected against the wheel's `node-types.json` without changing any pinned assertion (Task 4 NOTE governs all five languages).
5. **`normalize_rust_use` vs the spec's layout label `normalize_rust_import`:** spec §4.1's package-layout tree labels the Rust normalizer `normalize_rust_import`; this plan deliberately LOCKS `normalize_rust_use` instead — the function normalizes `use` declarations, mirroring `normalize_c_include`'s construct-named shape — while every other normalizer name (`normalize_c_include`, `normalize_js_import`, `normalize_ts_import`, `normalize_java_import`) matches the spec verbatim. Recorded here so the spec-wins rule is explicitly overridden for this ONE identifier: do not "correct" it back mid-implementation (Task 4's module code, `__all__`, and test imports all use `normalize_rust_use`; the D8 capture semantics and AC-18 pins are unchanged either way).
