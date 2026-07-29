# Multilanguage reference analyzers — design

**Date:** 2026-07-29
**Status:** Ratified (owner-converged design; twelve decisions D1–D12 fixed in an interactive brainstorm on 2026-07-28/29 — encoded here, not re-litigated). Two explicit owner gates were opened in that session and are recorded in §3 and §6/§7: (a) the owner **waives the <1% footprint clause** of CLAUDE.md's dependency-promotion exception for the tree-sitter core + grammar wheels (~6–10 MB on a ~90 MB baseline), with the other three promotion criteria holding; (b) the owner **opens the `.java` allowlist widening** — a new `docs/tool-contracts.md` §4.1 ceiling amendment, to be applied via the ADR 0007 ratification precedent.
**Amends:** `docs/tool-contracts.md` §2.2 (a one-sentence wording amendment — the three-value `meta.resolution` enum is unchanged, but the "declared when the target's language carries no registered reference analyzer" sentence and its "honest value for non-Python targets" parenthetical become false under the two-state declaration and are rewritten, §7.4 item 3), §4.1 (`.java` joins the extension ceiling; the default `include_extensions` split changes), §5.1 (per-language capability rows) — all carried by one new ADR (ADR 0022, §7.4).
**Companions:** `docs/adr/0004-code-structure-abstraction.md` (the `LanguageAnalyzer` seam this design finally exercises), `docs/adr/0021-multilanguage-indexing.md` (the chunker tiers this design completes).

**Goal:** Extend the reference graph — CALLS, INHERITS, IMPORTS edges plus the alias tables the resolver consumes — from Python-only capture to seven additional code extensions (`.rs .c .h .js .ts .tsx` and, newly end-to-end, `.java`), by registering per-language tree-sitter analyzers behind the existing `LanguageAnalyzer` seam (`python/pydocs_mcp/extraction/strategies/analyzers.py`), attributing every edge to the enclosing **top-level** symbol using the exact spans the multilang chunker's `LANGUAGE_SPECS` queries already produce (joinability by construction), promoting the tree-sitter core and the five official MIT grammar wheels into the required runtime dependencies, flipping the project-scope discovery default to include code extensions, and making the declared capability matrix availability-aware so `get_references` `meta.resolution` never claims `"syntactic"` for a deployment whose graph is structurally empty. The frozen nine-tool surface is untouched: no new tool, no new parameter, no envelope change — everything lands behind the seam, in YAML defaults, and in packaging metadata.

## Abstract

ADR 0021 shipped multilanguage *indexing*: the `MultilangChunker` builds top-level symbol trees for `.rs .c .h .js .ts .tsx` via tree-sitter, degrading internally to text windows when a grammar is absent. But the reference graph stayed Python-only: `analyzer_registry` holds exactly two entries (`.py`, `.md`), so `ReferenceCaptureStage` silently skips every other extension (`extraction/pipeline/stages/reference_capture.py:108–110`) and `get_references` for a non-Python target honestly reports `meta.resolution = "unavailable"` via `language_capabilities(ext)` (`application/tool_router.py:76–86`).

This design fills that gap on the seam ADR 0004 froze for exactly this purpose. Five new per-language analyzer classes (Rust, C, JavaScript, TypeScript — covering `.ts` and `.tsx` — and Java) register at import time across seven extensions; each owns three tree-sitter query strings (calls / inherits / imports) plus an import normalizer that emits dotted alias targets. Attribution is top-level: `from_node_id` is the enclosing top-level symbol's qname, computed by bisecting the same root-anchored spans the chunker's `LANGUAGE_SPECS` queries produce, with the qname assignment hoisted into a single shared helper so analyzer output joins the persisted document tree by construction. The `ReferenceResolver` is unchanged: exact + strict-suffix matching bridges dotted alias targets to the suffix-preserving module qnames (`src.lib.rs.parse_config`) that `_module_from_doc_path` already mints. Java is added end-to-end (allowlist ceiling, chunker spec, grammar wheel, analyzer). Packaging promotes tree-sitter + grammars into required deps under an explicit owner waiver of the footprint clause; `[multilang]` becomes a deprecated empty alias on the `[watch]` precedent. `LanguageAnalyzer.capabilities` becomes a read-only property so tree-sitter analyzers can declare availability-aware capability states. One new ADR (0022) carries the contract amendments; version 0.7.0.

## 1. Motivation and context

### 1.1 Where the graph stops today

- **Capture**: `analyzer_registry` (`extraction/strategies/analyzers.py:86–92`) maps `.py` → `PythonAstAnalyzer` (CALLS + INHERITS + IMPORTS + alias tables + `self.X` attribute types) and `.md` → `MarkdownMentionsAnalyzer` (opt-in MENTIONS). `ReferenceCaptureStage._capture_all` looks up `analyzer_registry.get(Path(path).suffix.lower())` and skips unknown extensions silently — "policy, not error" (`reference_capture.py:108–110`).
- **Trees without edges**: the `MultilangChunker` (`extraction/strategies/chunkers/multilang_treesitter.py`) already persists top-level FUNCTION/CLASS symbol nodes with real 1-indexed spans for the six T3 extensions — but emits no reference edges of any kind. `search_codebase`, `get_symbol`, `get_context`, and `parent_rollup` work for these languages; `get_references` returns empty with `meta.resolution = "unavailable"`.
- **The seam is ready**: ADR 0004 froze `LanguageAnalyzer` + `analyzer_registry` precisely so "adding a language becomes additive registration; emission stays in the EXISTING `node_references` schema (its DDL is language-neutral TEXT; no schema change)". ADR 0004's consequence line: "`references: unavailable` is a legal launch state" — this design is the follow-through that upgrades those languages to `"syntactic"`.
- **Resolution is already shape-compatible**: the resolver's Rule B (exact) and Rules C/D (unique strict dotted-suffix within the from-package, tail-bucket indexed) operate on plain dotted strings (`extraction/strategies/reference_resolver.py:122–201`). The suffix-preserving module qnames non-Python files already carry (`src.lib.rs`, via `_module_from_doc_path`, `chunkers/_shared.py:95–112`) are reachable by suffix matching from dotted targets — no resolver change is required, only emitters that produce dotted candidates.

### 1.2 Why now

The reference graph is one of the platform's differentiating retrieval signals (graph expansion is in the shipped default docs pipeline, `chunk_search_graph.yaml`). Leaving it Python-only while the chunkers, the discovery scope, and the eval strata (ADR 0021 Decision 9, `gold_touches_non_python`) all went multilanguage creates an asymmetry the capability matrix has to keep apologizing for. The owner ratified closing it, including the packaging promotion that removes the biggest operational cliff (grammar wheels absent → graph silently empty).

### 1.3 Code-grounded corrections carried into this spec

Where prior docs drift from the code, this spec encodes what the code does and flags the correction:

1. **ADR 0021 "Decision 5" numbering.** D3 below cites "ADR 0021 Decision 5 (nested members NOT persisted)". In the ADR as written, Decision 5 is the availability-aware chunker-with-internal-fallback; the top-level-only rule (nested members intentionally left to text-window fallback) lives in the root-anchored query design and is cited *as* Decision 5 in `multilang_queries.py`'s docstring (lines 17–29). The substance is identical and stands; ADR 0022 will cite the query-design docstring, not the ADR decision number.
2. **`.java` exists nowhere today.** Not in `_CODE_EXTENSIONS` (`extraction/config.py:37`), not in `ALLOWED_EXTENSIONS`, not in `LANGUAGE_SPECS` (`multilang_queries.py:116–127`), no `tree-sitter-java` wheel in `pyproject.toml` or `uv.lock`. D1 adds it end-to-end; the §4.1 ceiling amendment is the owner-gated part (gate opened, §7.4).
3. **Worktree version is 0.5.1** (`pyproject.toml:7`), though project docs reference a "0.6.0 rename". D10's 0.7.0 bump assumes 0.6.0 lands first; if this design ships before the rename, the bump becomes "next minor after whatever main carries" with 0.7.0 as the intended landing number.
4. **Package-level hash algorithm.** CLAUDE.md describes it as "xxh3 of (path, mtime) pairs"; the pure-Python fallback (`_fallback.py:58–73`) is md5[:16] over `path:mtime_ns` lines. §8 describes the *fold mechanism* (which this design extends) without depending on the algorithm claim.
5. **Stale "Python-only" prose.** `_resolution_for_ext`'s docstring (`tool_router.py:80–83`), `reference_capture.py`'s "only over .py files" cost note (lines 20–22), `references.py`'s "Python-only capture today" docstring (lines 16–19), and CLAUDE.md's v1 capability matrix all assert the pre-design state; each is updated in the implementation PR (AC-35).
6. **ADR 0004's tree-sitter-language-pack "pre-approval"** (ADR 0004 line ~243) was superseded by ADR 0021's R9 rejection (per-grammar licenses unauditable). This design uses only individual official MIT grammar wheels and does not inherit the pre-approval wording.

## 2. Goals and non-goals

### 2.1 Goals

- CALLS + INHERITS + IMPORTS capture, plus always-on alias tables, for `.rs .c .h .js .ts .tsx .java` — full parity with `PythonAstAnalyzer`'s capture depth, to the extent each language expresses the concept (C has no inheritance; Java has no top-level functions).
- Top-level attribution joinable by construction with the persisted document trees.
- Availability-aware, honest capability declaration surfaced through the existing `meta.resolution` channel.
- Java end-to-end: allowlist, chunker spec, grammar dependency, analyzer.
- Grammar wheels as required runtime deps; the degrade path retained for sdist installs and grammar ABI mismatch.
- Project-scope discovery defaults widened to the code extensions.
- Index coherence across the upgrade: the existing scope→pipeline-hash re-embed plus a new package-level grammar-availability salt.

### 2.2 Non-goals

- **No new MCP tools, parameters, or envelope fields.** The nine-tool surface is frozen; everything here is behind it (D10).
- **No resolver changes.** `ReferenceResolver` is byte-untouched (D8); no filesystem-level import resolution in v1 — the same syntactic (not semantic) line Python draws.
- **No nested-member persistence.** Top-level-only attribution stands (D3); nested symbols still resolve to their enclosing top-level symbol or the module.
- **No `module_members` widening.** `module_members` stays Python-only; only the reference graph changes language coverage.
- **No new `NodeKind`, no `node_references` schema change** (language-neutral TEXT DDL, per ADR 0004).
- **No semantic backends** (jedi, LSIF, SCIP-shaped analysis) — the declared capability stays `"syntactic"`; a future semantic backend flips only the declared value (ADR 0004 invariance).
- **No `tree-sitter-language-pack`** — individual official MIT wheels only (ADR 0021 R9).

## 3. Decisions (owner-ratified 2026-07-28/29)

All twelve decisions below were fixed by the owner in the interactive brainstorm of 2026-07-28/29. They are encoded faithfully; the WHY notes ground each in the repository facts of §1 and the research record.

**D1 — Language set: `.rs .c .h .js .ts .tsx` + NEW `.java`, end-to-end.**
LanguageAnalyzers for every extension the multilang chunker already covers, plus Java added across the whole stack: `ALLOWED_EXTENSIONS` ceiling, a `LANGUAGE_SPECS` entry (chunker spec), a `tree-sitter-java` grammar dependency, and an analyzer. *Why:* the chunker set is the natural floor (trees already exist to join against); Java is the highest-volume enterprise language absent from the T3 set, and the LCA Java/Kotlin eval direction (owner-gated ADR 0021 widening, per project memory) needs at least Java indexable. Adding it end-to-end in one design avoids a second ceiling amendment later.

**D2 — Capture depth: CALLS + INHERITS + IMPORTS, alias tables always captured.**
Full parity with `PythonAstAnalyzer`: alias tables populate unconditionally (they are the resolver's source of truth); IMPORTS *rows* are filtered downstream by `ReferenceCaptureStage` when `"imports"` is not in the allowed kinds — the existing contract (`reference_capture.py:123–130`), unchanged. Calls/inherits queries run only when their kind is allowed, mirroring `PythonAstAnalyzer._capture_definitions` gating (`analyzers.py:163`). *Why:* the split between "aliases always" and "IMPORTS rows filtered" is already load-bearing for Python (`analyzers.py:129–133`); the new analyzers inherit it rather than inventing a second convention.

**D3 — Granularity: top-level attribution.**
`from_node_id` is the enclosing **top-level** symbol qname — e.g. a call inside `impl Node` in `pkg/src/lib.rs` attributes to `pkg.src.lib.rs.Node` — computed by bisecting the SAME top-level spans the chunker's `LANGUAGE_SPECS` queries produce. File-scope references (imports, top-level statements) attach to the module qname (`pkg.src.lib.rs`). Joinability by construction: the analyzer does not invent qnames; it reuses the chunker's span → qname assignment (§4.4). Nested members are NOT persisted (the ADR 0021 top-level-only rule, `multilang_queries.py:17–29`; see §1.3 item 1 for the decision-number nuance). *Why:* every emitted `from_node_id` must exist in the persisted document tree or the edge is dead weight — the markdown analyzer's WORKAROUND comment (`analyzers.py:261–266`) documents exactly this failure mode. Reusing the chunker's spans and slug rules makes the invariant structural instead of aspirational.

**D4 — Architecture: per-language analyzer classes; `analyzers.py` becomes a package.**
The owner chose per-language classes over a single data-driven analyzer. `analyzers.py` converts to a package preserving the import path `pydocs_mcp.extraction.strategies.analyzers`:

- `__init__.py` — keeps the seam: `LanguageCapabilities`, `LanguageAnalyzer` Protocol, `analyzer_registry`, `register_analyzer`, `language_capabilities`, `PYTHON_CAPABILITIES`, `MARKDOWN_CAPABILITIES`, `PythonAstAnalyzer`, `MarkdownMentionsAnalyzer`, the existing `__all__` — and imports the language modules so registration fires at import time.
- `_treesitter.py` — shared plumbing: tree-sitter capability constants (both states of D7), the enclosing-symbol bisect index, capture helpers; REUSES the chunker's `_load_language` grammar cache (one cache, one degrade path).
- `rust.py` (`.rs`), `c_lang.py` (`.c` + `.h`), `javascript.py` (`.js`), `typescript.py` (`.ts` + `.tsx`), `java.py` (`.java`).

Each language module owns its three tree-sitter query strings (calls / inherits / imports) plus its import normalizer. *Why per-language classes:* the grammars genuinely differ (TS classes use `type_identifier` where JS uses `identifier` — the "impossible pattern" note at `multilang_queries.py:88–91`; C has includes instead of imports and no inheritance; Java has no top-level functions). A data-driven table would push those differences into escape hatches; separate small modules keep each language greppable and independently editable, per the repo's one-responsibility-per-file rule. *Why a package:* six new modules in the existing ~290-line seam file would blow the file-size rule; the package keeps the public import path and `__all__` byte-compatible.

**D5 — Packaging: tree-sitter + five grammar wheels become required runtime deps; `[multilang]` becomes a deprecated empty alias.**
`tree-sitter>=0.25,<0.26` (the 0.26 use-after-free pin stays) plus official MIT abi3 grammar wheels `tree-sitter-rust`, `tree-sitter-c`, `tree-sitter-javascript`, `tree-sitter-typescript`, `tree-sitter-java` move into `[project] dependencies`. `[multilang]` becomes `multilang = []` — the `[watch]`/watchdog precedent (`pyproject.toml:78–82`), removal horizon next major. **The owner explicitly waives the <1% footprint clause** of CLAUDE.md's promotion exception (~6–10 MB on the ~90 MB baseline is ~7–11%); the other three criteria hold: zero transitive dependencies, prebuilt wheels for every supported platform (verified in `uv.lock` for the existing four — grammar stanzas at `:4844` c, `:4860` javascript, `:4876` rust, `:4892` typescript, with the per-CPython core at `:4808`; Java added under the same discipline), and first-class YAML surface (`discovery.*.include_extensions`). The internal text-window fallback path STAYS — sdist installs without wheels and grammar ABI mismatches still index code files as searchable text. *Why:* a default-ON reference graph (D6) that silently stays empty unless an extra is installed would violate the capability-honesty rule from the other direction; making the wheels required removes the cliff while the retained fallback keeps `pip install` from sdist working.

**D6 — Defaults: project scope gains the seven code extensions; dependency scope stays text/config; ceiling gains `.java`; `LANGUAGE_SPECS` gains Java.**
`discovery.project.include_extensions` gains `.js .ts .tsx .c .h .rs .java` (default-ON for project scope). `discovery.dependency` keeps the current text/config default (code extensions remain opt-in for dependencies). `ALLOWED_EXTENSIONS` gains `.java` (`_CODE_EXTENSIONS`, `extraction/config.py:37`). `LANGUAGE_SPECS` gains a Java entry: top-level classes / interfaces / enums / records → `NodeKind.CLASS`; Java has no top-level functions, so no FUNCTION mapping. *Why the split:* the ADR 0021 census showed second-language read-side code skews heavily vendored in *dependencies* (127 of matplotlib's 222 C/C++ files under `extern/`) while a user's own project code is exactly what they ask questions about; default-ON for project, opt-in for deps matches the evidence. This requires per-scope defaults in `DiscoveryConfig` (today both scopes share `DiscoveryScopeConfig`'s single `include_extensions` default — `extraction/config.py:196–211` — consumed by `DiscoveryConfig`'s fields at `:243–249`); §6.3 specifies the mechanics.

**D7 — Capabilities honesty: `capabilities` becomes a read-only property; tree-sitter analyzers are availability-aware.**
The `LanguageAnalyzer` Protocol's `capabilities: ClassVar[LanguageCapabilities]` becomes a read-only property; plain class attributes on the Python/Markdown analyzers still conform structurally. Tree-sitter analyzers report per-deployment:

- grammar loads → `{"outline": "available", "definitions": "available", "references": "syntactic"}`
- degraded (grammar absent / ABI-rejected) → `{"outline": "available", "definitions": "unavailable", "references": "unavailable"}`

`meta.resolution` must never claim `"syntactic"` for a deployment whose graph is structurally empty. *Why the degraded row reads this way:* the text-window fallback still persists a module tree (outline available), but no symbol nodes (definitions unavailable) and no analyzer output (references unavailable). *Why a property:* a `ClassVar` cannot express per-deployment truth; the property routes through the shared grammar cache (§4.3) so it is cheap and consistent with what the chunker actually did.

**D8 — Resolution: `ReferenceResolver` UNCHANGED; per-language normalizers emit dotted alias targets.**
Canonical normalizer examples (fixed by the owner, encoded exactly):

- `use crate::a::B as C` → alias `C → a.B`
- `import {X as Y} from './a/b'` → alias `Y → a.b.X`
- `#include "graph.h"` → module-level IMPORTS edge
- `import com.acme.G;` → alias `G → com.acme.G`

Exact + strict-suffix matching (resolver Rules B/C/D) bridges dotted targets to the suffix-preserving module qnames. NO filesystem-level import resolution in v1 — the same syntactic (not semantic) line Python draws. Cross-language suffix collisions resolve per the existing resolver rules (unique-match-or-None); accepted precision cost (§11). *Why:* the resolver's design already tolerates exactly this ambiguity for Python (Rule D returns deterministic None on >1 candidate); dotted-suffix bridging costs zero resolver code and inherits its performance fix (tail-bucket index, `reference_resolver.py:108–120`).

**D9 — Migration: the existing scope-fold re-embed, plus a NEW loadable-grammar salt in the package-level hash.**
The extension-scope → `ingestion_pipeline_hash` fold (unconditional, `app_config.py:428–435`) yields a one-time full re-embed on first index after upgrade — the existing ADR 0021 invariant; a release-notes item, not new machinery. NEW: salt the package-level content hash with a loadable-grammar fingerprint (sorted tuple of extensions whose grammar imports), so a deployment where grammars *become* available re-extracts unchanged packages instead of skipping on the package-level cache and stranding an empty graph. §8.2 specifies the fold. *Why:* reference rows are recomputed only when a package reindexes (`indexing_service.py:395` sweeps + rewrites per package); the package-level skip (`project_indexer.py:84–88, 122–126`) would otherwise pin the empty graph forever on unchanged files.

**D10 — Contract/ADR: ONE new ADR; version 0.7.0; frozen surface untouched.**
ADR 0022 covers, in one document: the §5.1 capability rows for the six non-Python languages (Rust, C, JavaScript, TypeScript, TSX, Java); the §2.2/§4.1 ratified extension-list amendment (`.java`); the defaults change (D6); and the dependency promotion with the owner's footprint waiver (D5). Version bump 0.7.0. No new tool, no new param, no envelope change. *Why one ADR:* the pieces are one decision (analyzers exist ⇒ capabilities flip ⇒ deps must be present ⇒ defaults can turn on) and splitting them would let a partial ratification produce an incoherent contract. §2.2's three-value enum already admits the new `"syntactic"` declarations, but its explanatory "no registered reference analyzer" sentence needs a one-line rewrite for the two-state declaration (§7.4 item 3); §4.1 and §5.1 carry the other line changes.

**D11 — Error handling: containment stays in the stage; grammar failure no-ops; the chunker's log is the single operator signal.**
Per-file containment stays in `ReferenceCaptureStage` (analyzers may raise freely — the existing caller contract, `analyzers.py:52–57`). Grammar-load failure → the analyzer no-ops (captures nothing); the chunker's existing one-per-extension structured `multilang_fallback` JSON log (`multilang_treesitter.py:291–306`) remains the single operator signal — analyzers add no second log. Probe-derived tree-sitter rules are inherited wholesale: `QueryCursor.matches()` not `captures()`; Tree + cursor bound to live locals; 1-indexed spans; the out-of-range span guard. *Why:* two logs for one root cause (missing grammar) is operator noise; the probe rules are segfault-and-corruption avoidance, not style (`multilang_treesitter.py:14–25`, ADR 0021 lines 58–75).

**D12 — Testing: per-language pins, two-state capability pins, joinability invariant, end-to-end fixtures, degrade seams, router tests, drift guard, mypy carve-out lift.**
Turned into numbered acceptance criteria in §10. Headline items: per-language pin tests mirroring `tests/extraction/test_analyzers.py`; TWO-state capability pins (available + degraded) per language; a JOINABILITY INVARIANT test (every emitted `from_node_id` exists in the persisted document tree for a fixture file per language); per-language end-to-end fixtures (Rust impl+use-rename, TS re-export+extends, C prototype+#include, JS require+class, Java import+implements) asserting expected CALLS/INHERITS/IMPORTS edges resolve; degrade tests via `sys.modules` blocking + the `_reset_multilang_caches` seam; `tool_router` `meta.resolution` tests in both states; a parity pin that the tree-sitter analyzers' extension set == `LANGUAGE_SPECS` keys (chunker/analyzer drift guard); and lifting the mypy multilang carve-out (deps now installed in the typecheck job).

## 4. Architecture

### 4.1 Package layout

```
python/pydocs_mcp/extraction/strategies/analyzers/
├── __init__.py      # the seam, unchanged surface: LanguageCapabilities, LanguageAnalyzer,
│                    # analyzer_registry, register_analyzer, language_capabilities,
│                    # PYTHON_CAPABILITIES, MARKDOWN_CAPABILITIES,
│                    # PythonAstAnalyzer, MarkdownMentionsAnalyzer, __all__ (superset of today's);
│                    # imports rust/c_lang/javascript/typescript/java so registration
│                    # fires at import time (same side-effect discipline as
│                    # extraction.pipeline.stages population of stage_registry)
├── _treesitter.py   # shared plumbing: TREESITTER_ACTIVE_CAPABILITIES /
│                    # TREESITTER_DEGRADED_CAPABILITIES constants (D7's two states),
│                    # TreeSitterAnalyzerBase (or equivalent shared helpers),
│                    # top-level span index + bisect attribution (§4.4),
│                    # query execution helpers honoring the probe rules (D11);
│                    # imports _load_language / _compiled_query from
│                    # chunkers.multilang_treesitter — ONE grammar cache, ONE degrade path
├── rust.py          # .rs   — _RUST_CALLS_QUERY / _RUST_INHERITS_QUERY / _RUST_IMPORTS_QUERY
│                    #         + normalize_rust_import
├── c_lang.py        # .c .h — _C_CALLS_QUERY / (no inherits — empty) / _C_INCLUDES_QUERY
│                    #         + normalize_c_include
├── javascript.py    # .js   — _JS_CALLS_QUERY / _JS_INHERITS_QUERY / _JS_IMPORTS_QUERY
│                    #         + normalize_js_import  (ESM + require())
├── typescript.py    # .ts .tsx — _TS_* queries (type_identifier heritage, interfaces,
│                    #         implements, re-exports) + normalize_ts_import
└── java.py          # .java — _JAVA_CALLS_QUERY / _JAVA_INHERITS_QUERY / _JAVA_IMPORTS_QUERY
                     #         + normalize_java_import
```

The module `python/pydocs_mcp/extraction/strategies/analyzers.py` is deleted in the same commit that creates the package; every existing import site (`reference_capture.py:101`, `tool_router.py:53`, tests) continues to work because the package `__init__.py` re-exports the identical surface. No shim module is needed — a package with the same dotted path IS the compatibility layer.

### 4.2 Registration at import time

Each language module ends with the same decorator pattern the seam already defines:

```python
@register_analyzer(".rs")
@dataclass(frozen=True, slots=True)
class RustAnalyzer:
    @property
    def capabilities(self) -> LanguageCapabilities:
        return _treesitter.capabilities_for(".rs")

    def capture(self, source, *, path, root, from_package, allowed, collector) -> None:
        ...
```

Rules carried over unchanged from the current seam:

- `register_analyzer` stores an **instance** (analyzers are stateless singletons) and raises `ValueError` on duplicate registration at import time (`analyzers.py:95–116`). `c_lang.py` and `typescript.py` register their single class under two extensions each — two instances, both stateless, no shared mutable state. **Dual-extension modules hardcode their primary extension (`.c`, `.ts`) in the `capabilities` property** (zero-arg instantiation stays intact): both extensions of each pair ship in one grammar wheel, so the per-accessor skew window (`.tsx`'s `language_tsx` accessor failing while `.ts`'s `language_typescript` loads; `.c`/`.h` share one accessor and cannot skew) is accepted, and AC-7 pins capability states per MODULE, not per extension.
- `analyzer_registry` keys are lowercase extensions with the leading dot.
- `__init__.py` imports the five language modules unconditionally. This is safe with grammars absent: language modules import only `_treesitter` helpers and the seam at module scope; `tree_sitter` and grammar modules stay function-local behind the chunker's `_load_language` (the `[late-interaction]` lazy-import discipline, preserved even though the deps are now required — it is what makes the degrade path work on sdist installs).

### 4.3 Shared tree-sitter plumbing and the one grammar cache

`_treesitter.py` REUSES the chunker's caches rather than duplicating them:

- **Grammar/Query loading**: `_load_language(ext)` and `_compiled_query(ext, language)` from `chunkers/multilang_treesitter.py` (module caches `_LANG_CACHE`, `_QUERY_CACHE`, `_UNAVAILABLE_EXTS`, `_LOGGED_FALLBACK_EXTS`, lines 75–78) are the single source of grammar availability. The analyzer side adds a parallel per-extension compiled-query cache for its own three query strings (calls/inherits/imports are different sources than the chunker's top-level query), keyed `(ext, ReferenceQueryRole)` — where `ReferenceQueryRole` is a `StrEnum` in `_treesitter.py` with members `CALLS`, `INHERITS`, `IMPORTS` (the closed-string-vocabulary rule: `StrEnum` with UPPER_SNAKE members, never bare literals; it keys the cache AND parameterizes the shared query executor) — but the `Language` object and the availability verdict come from the chunker's cache. One cache, one degrade path, one `multilang_fallback` log.
- **`capabilities_for(ext)`**: returns `TREESITTER_ACTIVE_CAPABILITIES` when `_load_language(ext)` is non-None, else `TREESITTER_DEGRADED_CAPABILITIES` (D7's two states, verbatim in §7.2). Because `_load_language` memoizes both success and failure, the property is O(1) after first touch.
- **Test seam**: `_reset_multilang_caches()` (`multilang_treesitter.py:309–315`) already clears the shared caches; `_treesitter.py` registers its own query cache with that reset (or exposes a `_reset_analyzer_caches()` that the existing seam calls through), so the D12 degrade tests can run absence and presence in one process.
- **Probe rules** (D11) are enforced in ONE place — the `_treesitter.py` query-execution helper: `QueryCursor.matches()` only; `Tree` and cursor bound to live locals across iteration; spans `start_point.row + 1 .. end_point.row + 1`; out-of-range span guard identical to `_in_range_symbols` (`multilang_treesitter.py:238–245`). Language modules never touch `tree_sitter` directly.

### 4.4 Top-level attribution: the bisect index, joinable by construction

The attribution pipeline per file:

1. Parse once. Run the **chunker's own top-level query** (`LANGUAGE_SPECS[ext][2]`) over the tree to get the same `(kind, name, start_line, end_line)` symbol list the chunker sees.
2. Assign qnames with the **same shared helper** the chunker uses. Today the pieces are split: the start-line sort lives in `_build_symbol_tree` (`valid.sort(key=…)`, `multilang_treesitter.py:232`) and the assignment itself lives inline in `_symbol_nodes` (`:248–262`): fresh `seen` dict, `qname = f"{module}.{_identifier_slug(name, seen)}"`. Both are hoisted into ONE shared function (landing in `chunkers/_shared.py`, next to `_identifier_slug`) called by BOTH `_symbol_nodes` and the analyzer's index builder — and the shared helper OWNS the start-line sort internally, because the dedup suffixes (`_identifier_slug`'s `_N`) depend on iteration order: a caller feeding unsorted symbols would silently drift the qnames (pinned by AC-22's dedup case). Joinability is then a property of the code structure — the analyzer cannot drift from the chunker because neither owns a private copy of the rule. `module` is `_module_from_doc_path(path, root)`, the suffix-preserving id (e.g. `pkg/src/lib.rs` → `pkg.src.lib.rs`).
3. Build a **sorted span index** over the in-range symbols and attribute each captured reference by bisecting its 1-indexed line: inside a top-level span → that symbol's qname (`pkg.src.lib.rs.Node`); outside every span (file scope: imports, top-level statements, the preamble) → the module qname (`pkg.src.lib.rs`). Root-anchored queries cannot produce overlapping top-level spans, so bisect on start line with an end-line check suffices.
4. A file the chunker degraded to text windows has no symbol spans; if the analyzer's parse ALSO fails (same grammar, same content — deterministic), it emits nothing (per-file containment). Module-attributed edges are always safe: the module node is persisted in both the symbol-tree and the text-fallback shapes.

Dedup consequence (constrains D3's example and the AC-13 fixture): attribution follows the chunker's **deduped** span qname verbatim. The chunker names `impl_item` spans after the type, so a Rust file containing both `struct Node` and `impl Node` carries TWO top-level spans named `Node`, slugged `Node` and `Node_2` by the `seen` dict — edges inside the impl block attribute to `{module}.Node_2`, not `{module}.Node`. There is NO collapsing of impl blocks onto the struct's qname in v1 (an accepted granularity cost, recorded in §11). Fixtures asserting attribution to `{module}.Node` must therefore make the impl span the sole `Node` in its file (AC-13 places the struct in the second fixture file).

Resolver compatibility note (verified against `split_symbol_qname`, `reference_resolver.py:253–302`): for a symbol-attributed `from_node_id` like `src.lib.rs.Node`, `parts[-2]` is the extension segment (`rs`, lowercase), so the uppercase-class heuristic does not fire and `_module_part_of` returns `src.lib.rs` — exactly the alias-table key the analyzer populates. The suffix-preserving module id accidentally makes the Python-shaped heuristic behave correctly for **symbol-attributed** refs. Module-attributed (file-scope) refs are the exception: `split_symbol_qname`'s no-class branch always strips the last segment, so `_module_part_of("pkg.src.lib.rs")` returns `pkg.src.lib` and the Rule A alias lookup mis-keys for EVERY file-scope ref — those refs never alias-rewrite and resolve to None (accepted v1 cost; §5.1 alias row, §11, AC-20).

### 4.5 Data flow — unchanged consumers

Nothing downstream of the collector changes:

- `ReferenceCaptureStage` iterates `state.files.file_contents`, looks up the registry, applies per-file containment, filters IMPORTS rows when not allowed, and bundles `(refs, aliases, class_attribute_types)` — byte-identical logic, now hitting seven more extensions.
- `IndexingService.reindex_package` sweeps and rewrites references per package, resolves against the cross-package qname universe from `uow.trees` — unchanged.
- `ReferenceResolver` — unchanged (D8).
- `get_references` / `ReferenceService` / the envelope — unchanged; only the *data* and the declared `meta.resolution` value change.
- `node_references` DDL — unchanged (language-neutral TEXT).

## 5. Per-language capture

### 5.1 Common contract (all five modules)

| Aspect | Rule |
|---|---|
| Top-level span source | The chunker's `LANGUAGE_SPECS[ext]` query, executed by the analyzer itself over its own parse; qnames via the shared assignment helper (§4.4). |
| `from_node_id` | Enclosing top-level symbol qname, else module qname (`_module_from_doc_path`). |
| `to_name` | Canonical dotted string, `::` and `/` normalized to `.`; non-name-rooted call targets (computed callees) dropped, mirroring `canonical_dotted`'s None policy; length-capped consistent with `_MAX_TO_NAME_CHARS`. |
| Alias tables | Always captured into `collector.aliases[module_qname]` (D2); keyed by the suffix-preserving module qname. Resolver Rule A finds them via `_module_part_of` for **symbol-attributed** refs (the lowercase extension segment sits at `parts[-2]`, so the module part is recovered intact — §4.4). **Module-attributed (file-scope) refs mis-key the lookup**: `split_symbol_qname`'s no-class branch always strips the last dotted segment, so `pkg.src.lib.rs` is looked up as `pkg.src.lib` and never hits the table — file-scope aliased refs never alias-rewrite and resolve to None (no wrong edges; accepted v1 cost, §11; expected-None pin in AC-20). |
| Kind gating | Imports query always runs (aliases); calls query only when `"calls"` allowed; inherits query only when `"inherits"` allowed. IMPORTS rows filtered downstream (existing stage behavior). |
| `class_attribute_types` | Not populated (Python-only Rule 0 input; receiver-typed rewriting is semantic territory, out of scope per D8). |
| Emission | `NodeReference(from_package, from_node_id, to_name, to_node_id=None, kind=…)` via `collector.add` — the exact shape `references.py` emits today. |

### 5.2 Rust (`rust.py`, `.rs`)

| Concern | Sketch |
|---|---|
| Top-level spans | `LANGUAGE_SPECS[".rs"]`: `function_item`, `struct_item`, `enum_item`, `trait_item`, `mod_item`, `impl_item` under `source_file` (impl blocks are CLASS-kind nodes named after the type — calls inside `impl Node { … }` attribute to `….Node`). |
| CALLS | `call_expression` with `function:` an `identifier`, `scoped_identifier`, or `field_expression`; scoped paths `a::b::f(...)` → to_name `a.b.f`; method calls `x.f(...)` → `x.f` (recorded verbatim; usually resolver-None, same as Python's non-self attribute calls). Macro invocations are not captured (not calls in the graph's sense). |
| INHERITS | `impl_item` with a `trait:` clause → INHERITS edge from the impl type's qname to the trait's dotted path (`impl Display for Node` → `….Node INHERITS Display`); `trait_item` supertrait bounds (`trait A: B`) → `….A INHERITS B`. |
| IMPORTS + normalizer | `use_declaration` — `use_as_clause`, `use_list`, `scoped_use_list`, `use_wildcard`. Normalizer strips the syntactic path prefixes `crate::`, `self::`, `super::` (repeated `super::` stripped greedily — no filesystem resolution, D8) and maps `::` → `.`. **Canonical example (D8): `use crate::a::B as C` → alias `C → a.B`** plus an IMPORTS row targeting `a.B`. Unaliased `use a::b::D;` → alias `D → a.b.D`. Wildcard `use a::*;` → IMPORTS row only, no alias. |
| Resolution bridge | Single-segment targets resolve: bare `B` suffix-matches the persisted qname `src.a.rs.B` (unique-match-or-None per Rules C/D), bare `helper` matches `…a.rs.helper`. Multi-segment dotted targets like `a.B` do NOT match `src.a.rs.B` — the extension segment interleaves — and resolve to None (§5.7, §11). |

### 5.3 C (`c_lang.py`, `.c` + `.h`)

| Concern | Sketch |
|---|---|
| Top-level spans | `LANGUAGE_SPECS[".c"]`: `function_definition`, prototype `declaration`, `struct_specifier`, `enum_specifier`, `type_definition` under `translation_unit`. |
| CALLS | `call_expression function: (identifier)` → to_name is the bare identifier (C has no dotted call syntax; single-segment targets resolve via exact or unique-suffix within the package). |
| INHERITS | None — C has no inheritance. The module ships an empty inherits query and the shared helper skips empty queries. Struct embedding is deliberately not modeled as inheritance. |
| IMPORTS + normalizer | `preproc_include`. **Canonical example (D8): `#include "graph.h"` → module-level IMPORTS edge** — `from_node_id` = the including file's module qname, to_name = the include path with `/` → `.` and the filename's dot kept (`graph.h` → `graph.h`; `include/graph.h` → `include.graph.h`). The kept `.h` segment is what makes suffix matching land on the suffix-preserving module qname `src.include.graph.h`. System includes (`<stdio.h>`) are emitted the same way and resolve to None (Rule E) — verbatim intent, like Python's unresolved imports. No alias-table entries (C include is not a renaming import). |

### 5.4 JavaScript (`javascript.py`, `.js`)

| Concern | Sketch |
|---|---|
| Top-level spans | `LANGUAGE_SPECS[".js"]`: `function_declaration`, `generator_function_declaration`, `class_declaration`, `lexical_declaration` under `program`. |
| CALLS | `call_expression` with `function:` an `identifier` or `member_expression`; member chains → dotted (`api.routes.mount(...)` → `api.routes.mount`). `require(...)` calls are consumed by the imports pass, not emitted as CALLS. |
| INHERITS | `class_declaration` / class expressions with `class_heritage` (`extends X` / `extends a.b.X`) → INHERITS from the class qname to the dotted heritage expression. |
| IMPORTS + normalizer | ESM `import_statement` and CommonJS `require("…")` at file scope. Module-source normalization is purely syntactic: strip leading `./` and `../` segments and a trailing extension if present, then `/` → `.` (`'./a/b'` → `a.b`). **Canonical example (D8): `import {X as Y} from './a/b'` → alias `Y → a.b.X`** plus an IMPORTS row targeting `a.b`. Default import `import Z from './m'` → alias `Z → m`; namespace `import * as N from './m'` → alias `N → m`; `const P = require('./a/b')` → alias `P → a.b` + IMPORTS row. |

### 5.5 TypeScript (`typescript.py`, `.ts` + `.tsx`)

| Concern | Sketch |
|---|---|
| Top-level spans | `LANGUAGE_SPECS[".ts"]` / `".tsx"`: the JS set plus `abstract_class_declaration`, `interface_declaration`, `type_alias_declaration`, `enum_declaration` — class/interface names are `type_identifier` in this grammar (the reason TS gets its own queries, `multilang_queries.py:88–91`). |
| CALLS | Same shapes as JS (the TS grammar shares expression node types for calls). |
| INHERITS | `extends` in class heritage AND `implements_clause` → INHERITS edges; `interface_declaration` `extends_type_clause` → INHERITS between interfaces. |
| IMPORTS + normalizer | JS normalizer plus: `import type { T } from './t'` treated identically (alias `T → t.T`); re-exports `export { X } from './a'` → IMPORTS row targeting `a` + alias `X → a.X` (the D12 "TS re-export" fixture); `export * from './a'` → IMPORTS row only. |

### 5.6 Java (`java.py`, `.java`)

| Concern | Sketch |
|---|---|
| Top-level spans | NEW `LANGUAGE_SPECS[".java"]` entry (D6): `class_declaration`, `interface_declaration`, `enum_declaration`, `record_declaration` under `program`, all → `NodeKind.CLASS`; Java has no top-level functions. Grammar module `tree_sitter_java`, accessor `language`. |
| CALLS | `method_invocation` — `object.method(...)` chains → dotted (`svc.run()` → `svc.run`; `Files.read(p)` → `Files.read`); bare `helper()` → `helper`. `object_creation_expression` (`new G(...)`) → CALLS edge to the constructed type's dotted name (constructor call). |
| INHERITS | `class_declaration` `superclass` (`extends B`) and `super_interfaces` (`implements I, J` — one edge per interface, the D12 fixture); `interface_declaration` `extends_interfaces`. |
| IMPORTS + normalizer | `import_declaration`. **Canonical example (D8): `import com.acme.G;` → alias `G → com.acme.G`** plus an IMPORTS row. Static import `import static com.acme.G.f;` → alias `f → com.acme.G.f`. Wildcard `import com.acme.*;` → IMPORTS row only. `package com.acme;` declarations are not captured (not an import). |

### 5.7 Resolution bridging (D8, resolver untouched)

The end-to-end path for `use crate::a::B as C; … C::new()` in `pkg/src/lib.rs`:

1. Analyzer emits alias `C → a.B` under module `pkg.src.lib.rs`, and a CALLS candidate `to_name="C.new"` attributed to the enclosing top-level symbol.
2. Resolver Rule A: leading segment `C` is in the alias map → rewrite to `a.B.new`.
3. Rule B misses (no exact `a.B.new` in the universe); Rules C/D suffix-scan within `from_package`: the persisted qname `pkg.src.a.rs.B` does not end with `.a.B.new`. Wherever a multi-segment dotted target and the suffix-preserving qname disagree by the interleaved extension segment (`a.B` vs `a.rs.B`), resolution returns None — the accepted v1 precision boundary, identical in kind to Python's unresolved cross-package heuristics. What DOES resolve: same-file targets and **single-segment names** (bare `helper` suffix-matches `…a.rs.helper`; bare `B` matches `…a.rs.B`). What structurally NEVER resolves in v1: **JS/TS/Java IMPORTS rows** — their normalizers strip source extensions while persisted module qnames keep them (`a.b` vs `a.b.js`; TS re-export target `a` vs `a.ts`; `com.acme.G` vs `…com.acme.G.java.G`); only C, whose include targets keep `.h`, has reliably resolving IMPORTS rows, and Rust IMPORTS resolve only for single-segment targets (§11). This is the honest reading of "syntactic": the graph is precision-biased, name/alias-matched, not scope-resolved (contract §5.1 wording, unchanged).
4. The D12 end-to-end fixtures pin BOTH directions of the bridge: each fixture's must-resolve assertions are limited to shapes Rules B/C/D can actually carry (single-segment targets, same-file inheritance, bare cross-file calls — enumerated per fixture in AC-13..17), and the four D8 canonical multi-segment examples are pinned **expected-None** at resolution time — verbatim-intent rows whose alias tables are byte-pinned by AC-18. That keeps the bridge's floor from regressing in either direction.

## 6. Packaging and defaults

### 6.1 Dependency promotion (owner footprint waiver)

`[project] dependencies` gains (pins continue the existing cap-below-next-minor discipline, `pyproject.toml:114–133`):

```
tree-sitter>=0.25,<0.26          # 0.26.0 use-after-free pin RETAINED (probe 5/5 repro)
tree-sitter-rust>=0.24,<0.25
tree-sitter-c>=0.24,<0.25
tree-sitter-javascript>=0.25,<0.26
tree-sitter-typescript>=0.23,<0.24   # one wheel, two accessors (.ts/.tsx)
tree-sitter-java>=0.23,<0.24         # NEW — official MIT abi3 wheel, same discipline
```

Owner-gate record: CLAUDE.md's promotion exception requires (<1% footprint, zero transitive deps, prebuilt wheels everywhere, first-class CLI/YAML surface). The footprint clause fails (~6–10 MB on ~90 MB, ~7–11%) and **the owner explicitly waives it (2026-07-28/29)**; the other three criteria hold — the grammar wheels have zero transitive dependencies, `uv.lock` shows macOS x86_64/arm64 + manylinux + musllinux + Windows wheels for all four existing grammars (grammar stanzas at `uv.lock:4844` for c, `:4860` javascript, `:4876` rust, `:4892` typescript; the per-CPython tree-sitter core stanza sits at `:4808`; Java verified at lock time under the same bar, AC-27), and `discovery.*.include_extensions` is the first-class YAML surface. The tree-sitter CORE wheel is per-CPython (not abi3) — a known cost carried over from ADR 0021, now in the default install (§11).

Lazy imports stay: `tree_sitter` and grammar modules remain function-local (`_import_language`, `multilang_treesitter.py:140–154`) so `import pydocs_mcp` never pays the import and the sdist/ABI-mismatch degrade path keeps working.

### 6.2 `[multilang]` becomes a deprecated empty alias

`multilang = []` with the same comment shape as `watch = []` (`pyproject.toml:78–82`): kept so `pip install 'pydocs-mcp[multilang]'` stays a valid no-op; removal horizon next major version. `_INSTALL_HINT` (`multilang_treesitter.py:64`) changes from the extra-install hint to a reinstall-with-wheels hint (`"reinstall pydocs-mcp from wheels (grammar unavailable or ABI-mismatched)"` shape) since the extra no longer installs anything — the log event name `multilang_fallback` and its one-per-extension discipline are unchanged.

### 6.3 Defaults split (D6)

- `_CODE_EXTENSIONS` (`extraction/config.py:37`) gains `.java` → the `ALLOWED_EXTENSIONS` ceiling admits it.
- `DiscoveryConfig` (`config.py:243–249`) moves from identical per-scope defaults to per-scope `default_factory` wiring — the exact mechanism, named once: `project: DiscoveryScopeConfig = Field(default_factory=lambda: DiscoveryScopeConfig(include_extensions=list(_DEFAULT_PROJECT_INCLUDE_EXTENSIONS)))` and the analogous `dependency` factory over `_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS`. `_DEFAULT_PROJECT_INCLUDE_EXTENSIONS` = the current text/config default PLUS `.js .ts .tsx .c .h .rs .java`; `_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS` = the current text/config default. `DiscoveryScopeConfig`'s own shared `include_extensions` default (`config.py:196–211`) stops being the effective source for either scope. Both extension lists are module-level constants (single-source-of-truth rule); `default_config.yaml` restates them for user-facing clarity (the sanctioned YAML duplication). **Partial-overlay behavior is pinned**: a user overlay that sets only, e.g., `discovery.project.max_file_size_bytes` MUST keep the widened `include_extensions` default — a parent-level factory alone would silently drop it when pydantic constructs the scope model from a partial dict, so the `AppConfig` layer merge (defaults layer under overlay, with `default_config.yaml` restating the lists) is the guaranteed backstop, and AC-29 pins the behavior, not the mechanism.
- The `_enforce_allowlist` validator (`config.py:218–228`) is unchanged — YAML still narrows within the ceiling only.
- `LANGUAGE_SPECS` gains the `.java` entry (§5.6) and `MultilangChunker` gains the `@_register_chunker(".java")` registration line; `MULTILANG_EXTENSIONS` picks it up automatically (`tuple(LANGUAGE_SPECS)`).

### 6.4 mypy carve-out lift (D12)

With the deps required, the typecheck job installs them: remove the six `tree_sitter*` module entries and their ADR 0021 comment (`pyproject.toml:324–332`) from the **shared** `[[tool.mypy.overrides]]` block (`:316–335`). The block itself STAYS — it also covers `fast_plaid.*`, `fastembed.*`, `turbovec.*`, `yaml.*`, `numpy`/`numpy.*`, and its `follow_imports = "skip"` / `ignore_missing_imports` settings for those families are untouched. The grammar wheels are typed (per the existing pyproject comment), so this is a strict-net-positive check gain. Function-local imports remain (they are a runtime degrade mechanism, not a typing dodge).

## 7. Capabilities and contract amendments

### 7.1 `capabilities` becomes a read-only property (D7)

The Protocol changes from `capabilities: ClassVar[LanguageCapabilities]` to:

```python
@runtime_checkable
class LanguageAnalyzer(Protocol):
    @property
    def capabilities(self) -> LanguageCapabilities: ...
    def capture(self, source: str, *, path: str, root: Path, from_package: str,
                allowed: frozenset[str], collector: ReferenceCollector) -> None: ...
```

`PythonAstAnalyzer` and `MarkdownMentionsAnalyzer` keep their plain class attributes — a class attribute satisfies a read-only property Protocol structurally (both for `runtime_checkable` isinstance, which checks attribute presence, and for mypy). `language_capabilities(ext)` is unchanged in signature but its return value is now deployment-dependent for tree-sitter extensions.

### 7.2 The two capability states (verbatim per D7)

| State | outline | definitions | references |
|---|---|---|---|
| grammar loads | `available` | `available` | `syntactic` |
| degraded (grammar absent / ABI-rejected) | `available` | `unavailable` | `unavailable` |

Rationale rows: degraded outline stays `available` because the text-window fallback still persists a module tree with spans; degraded definitions is `unavailable` because no symbol nodes exist; degraded references is `unavailable` because the analyzer no-ops. **Invariant: `meta.resolution` never claims `"syntactic"` for a deployment whose graph is structurally empty for that language.** The property routes through the chunker's `_load_language` verdict (§4.3), so the declared state and the actual indexing behavior cannot disagree within a process.

### 7.3 `meta.resolution` honesty in the router

`_resolution_for_ext` (`tool_router.py:76–86`) needs **no code change** — it already routes through `language_capabilities(ext)`. Behavior change is data-driven: `.rs .c .h .js .ts .tsx .java` targets now return `"syntactic"` (grammar present) or `"unavailable"` (degraded) instead of the unconditional `"unavailable"`; text/config extensions (no analyzer) still return `"unavailable"` via the `None` branch. Its docstring — which hardcodes ".py/.md carry analyzers; all T3 code targets report unavailable" — is rewritten (AC-35). `get_symbol`'s `TARGET_EXTENSION_EXTRA` stripping is untouched.

### 7.4 ADR 0022, contract amendments, version (D10)

ONE new ADR — `docs/adr/0022-multilang-reference-analyzers.md` (0022 is the next free number) — in the house ADR skeleton, covering all four ratified moves:

1. **§5.1 capability rows** for the six non-Python languages (Rust, C, JavaScript, TypeScript, TSX, Java): the availability-aware two-state declaration of §7.2, joining Python's row. §5.1's frozen *vocabulary* (`{outline, definitions, references} × {semantic | syntactic | unavailable}`) is unchanged — the amendment adds rows, not values.
2. **§4.1 extension amendment**: `.java` joins the ceiling's code-extension list, AND the default sentence changes — code extensions become default-ON for the *project* scope and stay opt-in for the *dependency* scope (superseding the "ceiling-admitted but opt-in via YAML" wording for project scope). This is the owner-gated widening; the gate was opened 2026-07-28/29. Sequencing, fixed per the ADR 0007 precedent: the §2.2/§4.1/§5.1 contract edits land IN the implementation PR, with the amendment flagged explicitly in the PR description for owner ratification (both prior ADR 0021 amendments followed exactly this path and were ratified same-cycle).
3. **§2.2 one-sentence wording amendment**: the three-value enum and the envelope are unchanged, but the current sentence — `"unavailable" is declared when the target's language carries no registered reference analyzer (ADR 0021 — the honest value for non-Python targets; amendment owner-ratified 2026-07-21)` (`docs/tool-contracts.md:110–115`) — becomes false under D7: registered tree-sitter analyzers in DEGRADED deployments also declare `"unavailable"` (the "when" clause is no longer the complete condition), and grammar-present non-Python targets declare `"syntactic"` (the "honest value for non-Python targets" parenthetical inverts). ADR 0022 carries the rewrite: `"unavailable" is declared when the target's language carries no registered reference analyzer, OR when a registered tree-sitter analyzer's grammar is unavailable in the deployment (§5.1 two-state declaration)` — with the "honest value for non-Python targets" parenthetical deleted. Flagged for ratification alongside the §4.1/§5.1 edits (item 2); swept by AC-35.
4. **The dependency promotion** (D5) with the owner's footprint-waiver record, and the defaults change (D6).

Version bump: **0.7.0** (see §1.3 item 3 for the 0.5.1-worktree caveat). The frozen nine-tool surface is untouched — no new tool, no new param, no envelope field; ADR 0022 states this explicitly as its non-goal, mirroring ADR 0021.

## 8. Migration and index coherence (D9)

### 8.1 Pipeline level — the existing scope-fold re-embed

`AppConfig.ingestion_pipeline_hash` folds `_effective_extension_scope()` unconditionally (`app_config.py:428–435`). D6's default widening changes the project-scope extension set, so the hash changes for every stock deployment → every chunk's `content_hash` (which embeds `pipeline_hash`, `models.py:200–249`) misses → **one-time full re-embed on first index after upgrade**. This is the existing ADR 0021 Decision 7a invariant working as designed ("Stock deployments take a deliberate one-time re-embed on upgrade"); no new machinery. Release-notes item, prominently.

### 8.2 Package level — the NEW loadable-grammar fingerprint salt

Problem: reference rows are swept + rewritten only when a package actually reindexes; the package-level skip (`existing.content_hash == pkg.content_hash`, `project_indexer.py:84–88, 122–126`) is keyed on file paths + mtimes (+ the exclusion fold). A deployment indexed while a grammar was unavailable (sdist install, later reinstalled from wheels; ABI mismatch, later fixed) has unchanged files → permanent skip → the graph stays empty forever ("stranded").

Fix: `ContentHashStage` folds a **loadable-grammar fingerprint** into the package content hash, alongside the existing exclusion fold (`content_hash.py:55–69`): `fingerprint = ",".join(sorted(ext for ext in MULTILANG_EXTENSIONS if _load_language(ext) is not None))`, folded digest-of-digest (`md5(f"{base}\x00grammars:{fingerprint}")[:16]`, `usedforsecurity=False`). Two deliberate properties:

- **Unconditional fold** (unlike the exclusion fold, which is skipped for the no-excludes sentinel): the fingerprint must flip the hash on BOTH transitions (grammars appear AND grammars disappear), and an empty fingerprint must be distinguishable from "not folded". Cost: every package hash changes once on upgrade to 0.7.0 → one-time full re-extract — which coincides with (and is subsumed by the user-visible cost of) the §8.1 re-embed, so it adds no separate migration event.
- **Probes only, no parses**: `_load_language` memoizes; the fold costs a dict lookup per extension after first touch. The fingerprint reflects grammar availability at index time, which is exactly the thing that determines whether reference capture ran.

Embedder-identity coherence is already covered by the existing guard (`IndexingService` clears `content_hash` on embedder mismatch, `indexing_service.py:683–701`) and is not touched.

### 8.3 Release-notes items

1. One-time full re-embed on first index after upgrading to 0.7.0 (scope fold, §8.1) — expected duration scales with corpus size exactly like a `--force` reindex.
2. One-time full re-extract (grammar salt, §8.2) — subsumed by item 1's run.
3. Project-scope code files (`.js .ts .tsx .c .h .rs .java`) are now indexed by default; deployments that must not index them narrow `discovery.project.include_extensions` in YAML (allowlist semantics unchanged).
4. `[multilang]` is now an empty no-op alias; remove it from install scripts at leisure.

## 9. Error handling (D11)

- **Per-file containment stays in the stage.** `ReferenceCaptureStage._capture_all`'s bare `except Exception` log-and-continue (`reference_capture.py:120–122`) is the single containment point; analyzers raise freely (grammar parse errors, query errors, encoding surprises). Contract unchanged from `analyzers.py:52–57`.
- **Grammar-load failure → analyzer no-ops.** `capture` checks `_load_language(ext)`; on None it returns without emitting (no exception, no log of its own). The chunker's existing one-per-extension structured `multilang_fallback` JSON log remains the SINGLE operator signal for the whole degrade (chunking + reference capture share the cause and the cache, so one log covers both).
- **Probe-derived tree-sitter rules inherited** (enforced solely inside `_treesitter.py`, §4.3): `QueryCursor.matches()` never `captures()`; `Tree` + cursor bound to live locals across iteration; spans 1-indexed as `start_point.row + 1`; the out-of-range span guard drops the `0x3FFFFFFE` sentinel garbage rows before the bisect index is built (a reference bisected against a garbage span would mis-attribute — the guard runs before attribution, same as `_in_range_symbols` runs before tree building).
- **Empty-query skip**: the shared executor treats an empty query string (C inherits) as "no matches" without touching tree-sitter.
- **Determinism note**: chunker and analyzer parse the same bytes with the same cached `Language` in the same process; a file that symbol-parses for one symbol-parses for the other. The only divergence window is a mid-process grammar state change, which the caches make impossible outside the test reset seam.

## 10. Testing and acceptance criteria (D12)

Every criterion is independently checkable; tests live under `tests/extraction/` mirroring `tests/extraction/test_analyzers.py` unless noted. "Fixture" means an in-test source string or tmp-path file, headless, F.I.R.S.T.

**Seam and registration**

- **AC-1** `pydocs_mcp.extraction.strategies.analyzers` imports as a package; every name in today's `__all__` (`analyzers.py:280–290`) is importable from the same path, and `reference_capture.py` + `tool_router.py` require no import edits.
- **AC-2** After `import pydocs_mcp.extraction.strategies.analyzers`, `analyzer_registry` contains exactly `{".py", ".md", ".rs", ".c", ".h", ".js", ".ts", ".tsx", ".java"}`.
- **AC-3** Every registered analyzer passes `isinstance(analyzer, LanguageAnalyzer)` (runtime_checkable, now property-shaped).
- **AC-4** Duplicate registration still raises `ValueError` naming the extension; the original registrant survives (existing test extended to a tree-sitter extension).
- **AC-5** Drift guard: the set of tree-sitter-analyzer extensions equals `set(LANGUAGE_SPECS)` exactly (chunker/analyzer parity pin) — adding a language to either side alone fails the suite.
- **AC-6** `PYTHON_CAPABILITIES` byte-for-byte pin unchanged; the Python golden 9-row edge-set pin (`test_golden_edge_set_identical_pre_and_post_registry_refactor`) passes unmodified — the refactor is invisible to Python capture.

**Capabilities and router**

- **AC-7** Per language (5 modules × both states): with the grammar loadable, `capabilities` == `{"outline": "available", "definitions": "available", "references": "syntactic"}`; with the grammar blocked (`sys.modules[grammar] = None` + `_reset_multilang_caches()`), `capabilities` == `{"outline": "available", "definitions": "unavailable", "references": "unavailable"}` — byte-for-byte pins of §7.2.
- **AC-8** `language_capabilities(".rs")` returns the active dict when the grammar loads — the old `is None` pin (`test_analyzers.py:136`) is replaced, with a comment citing this spec.
- **AC-9** `language_capabilities(".toml")` (and every text/config extension) still returns `None`.
- **AC-10** `tool_router` test, grammar-present state: `get_references` for a `.rs`-backed target carries `meta.resolution == "syntactic"`.
- **AC-11** `tool_router` test, degraded state (grammar blocked): the same target carries `meta.resolution == "unavailable"` — the §7.2 invariant enforced end-to-end.
- **AC-12** `get_symbol` meta still excludes the resolution field (channel-stripping behavior unchanged).

**Capture correctness (per-language pins)**

Per-fixture resolution assertions below name exactly WHICH edge must resolve to WHICH persisted qname, using only shapes the resolver can bridge (§5.7: single-segment targets, same-file inheritance, bare cross-file calls); the D8 canonical multi-segment examples are pinned **expected-None** at resolution (their alias tables are byte-pinned by AC-18).

- **AC-13** Rust two-file fixture (impl + use-rename). File one (`lib.rs`): `use crate::B as C;`, `trait Show {}`, `trait Fancy: Show {}`, and `impl Node { fn go(&self) { helper(); C::new(); } }` — the impl span is the SOLE `Node` in file one (the struct lives in file two, per the §4.4 dedup constraint). File two (`a.rs`): `pub struct B;`, `pub struct Node;`, `pub fn helper() {}`. Assertions: alias `{module_one: {"C": "B"}}`; the `helper()` call attributes `from_node_id == f"{module_one}.Node"` and **resolves** to `{module_two}.helper` (single-segment suffix); the IMPORTS row targeting `B` **resolves** to `{module_two}.B`; the `Fancy → Show` INHERITS edge **resolves** same-file to `{module_one}.Show`; the aliased call `C::new` (Rule-A-rewritten to `B.new`) is pinned **expected-None** (multi-segment interleaving, §5.7).
- **AC-14** TypeScript fixture (re-export + extends), one file: `export { X } from './a'`, `interface I {}`, `class A {}`, `class B extends A implements I {}`. Assertions: the re-export yields an IMPORTS row targeting `a` pinned **expected-None** (extension-stripped `a` never matches `a.ts`, §5.7) plus alias `X → a.X`; the two INHERITS edges from `{module}.B` **resolve** same-file to `{module}.A` and `{module}.I`.
- **AC-15** C fixture (prototype + #include): `#include "graph.h"` yields a module-level IMPORTS edge whose target **resolves** to the `graph.h` module qname in the fixture corpus (C keeps the `.h` segment — the one language whose IMPORTS reliably resolve, §5.7); a call to a prototyped function yields a CALLS edge attributed to the calling `function_definition`'s qname that **resolves** to the defining module's function qname; zero INHERITS rows.
- **AC-16** JavaScript fixture (require + class), one file: `const P = require('./a/b')`, `class A {}`, `class D extends A {}`, `class E extends P.Base {}`. Assertions: alias `P → a.b` + an IMPORTS row targeting `a.b` pinned **expected-None** (extension-stripped, §5.7); the `D extends A` INHERITS edge **resolves** same-file to `{module}.A`; the `E extends P.Base` INHERITS edge (Rule-A-rewritten to `a.b.Base`) is pinned **expected-None**.
- **AC-17** Java fixture (import + implements), one file: `import com.acme.G;`, `interface I {}`, `class LocalType {}`, `class S implements I { void run() { new LocalType(); new G(); } }` — `LocalType` has NO shadowing import. Assertions: alias `G → com.acme.G` + an IMPORTS row targeting `com.acme.G`, BOTH pinned **expected-None** at resolution (extension-interleaved qnames, §5.7 — the Rule-A rewrite makes even `new G()` unresolvable, also pinned expected-None); the `S implements I` INHERITS edge **resolves** same-file to `{module}.I`; the `new LocalType()` CALLS edge **resolves** to `{module}.LocalType` (single-segment, unshadowed).
- **AC-18** Normalizer unit pins: the four D8 canonical examples reproduce EXACTLY — `use crate::a::B as C` → `C → a.B`; `import {X as Y} from './a/b'` → `Y → a.b.X`; `#include "graph.h"` → module-level IMPORTS edge; `import com.acme.G;` → `G → com.acme.G`.
- **AC-19** Kind gating parity: with `allowed = {"calls"}` only, alias tables are still populated for every language that emits aliases (all except C — §5.3; the C pin asserts an EMPTY alias table instead, includes not being renaming imports) and no IMPORTS/INHERITS rows survive the stage (the stage-level IMPORTS filter + analyzer-level inherits gating both verified).
- **AC-20** File-scope attribution: a top-level call/import outside any symbol span attributes to the module qname for each language. Additionally, a file-scope ALIASED call (e.g. top-level `P.init()` after `const P = require('./a/b')`) is pinned **expected-None** at resolution — module-attributed refs never alias-rewrite because `_module_part_of` strips the last segment of the module qname (§5.1, §11).
- **AC-21** Every emitted `to_node_id` is `None` at capture time (resolver-flips-later contract preserved), and `class_attribute_types` stays empty for non-Python analyzers.

**Joinability invariant**

- **AC-22** Per language: index a fixture file end-to-end (chunker + analyzer in one pipeline run); assert every emitted `from_node_id` exists as a `qualified_name` in the persisted document tree — including a dedup case (two same-named top-level symbols) proving the shared slug assignment (§4.4) keeps analyzer and chunker in lockstep.
- **AC-23** The span→qname assignment is a single shared function called by both `_symbol_nodes` and the analyzer index builder (structural check: `_symbol_nodes` contains no inline `_identifier_slug` call after the hoist).

**Degrade behavior**

- **AC-24** With a grammar blocked via `sys.modules` + `_reset_multilang_caches()`: the analyzer emits zero rows for that extension, files still index (text windows), exactly ONE `multilang_fallback` log line fires for the extension, and analyzers emit no additional log.
- **AC-25** Per-extension independence: blocking `tree_sitter_rust` alone leaves `.c` capture fully functional (the `_UNAVAILABLE_EXTS` granularity, extended to analyzers).
- **AC-26** A syntactically broken `.rs` file in a multi-file batch is contained per-file (warning logged, remaining files captured) — the stage containment contract exercised on a tree-sitter path.

**Packaging, defaults, migration**

- **AC-27** `pyproject.toml`: the six tree-sitter requirements of §6.1 are in `[project] dependencies` with those exact pin shapes; `multilang = []` with a deprecation comment; `uv lock --check` passes and the lock resolves `tree-sitter-java` to an official MIT abi3 wheel with the same platform spread as the existing grammars.
- **AC-28** The six `tree_sitter*` entries (and their ADR 0021 comment) are removed from the shared `[[tool.mypy.overrides]]` block — the block's other module families (`fast_plaid.*`, `fastembed.*`, `turbovec.*`, `yaml.*`, `numpy`/`numpy.*`) remain (§6.4) — and `mypy python/pydocs_mcp` passes with the grammar deps installed.
- **AC-29** `ALLOWED_EXTENSIONS` contains `.java`; `DiscoveryConfig`'s `project`/`dependency` fields carry the split defaults (`_DEFAULT_PROJECT_INCLUDE_EXTENSIONS` includes the seven code extensions, `_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS` does not) — pinned as constants-equality tests, not literal repeats; and a project-scope overlay that sets another field but omits `include_extensions` keeps the widened default (§6.3 partial-overlay pin).
- **AC-30** `LANGUAGE_SPECS[".java"]` exists mapping class/interface/enum/record node types to `NodeKind.CLASS` with no FUNCTION mapping; `MultilangChunker` registration covers `.java`; a Java fixture file produces a symbol tree with correct 1-indexed spans.
- **AC-31** `ingestion_pipeline_hash` changes when the project-scope default widening lands (pin: hash under old explicit scope != hash under new default scope), reusing the existing `_effective_extension_scope` fold tests.
- **AC-32** Grammar fingerprint salt: package content hash differs between grammar-present and grammar-blocked states for the same file set (via the reset seam), and is stable across two runs in the same state; the fold is unconditional (empty fingerprint still folded).
- **AC-33** Re-extraction rescue: a package indexed under blocked grammars (empty graph) is NOT skipped on the next index after grammars become available — references appear without any file touch.

**Documentation and contract**

- **AC-34** ADR 0022 exists in the house skeleton covering the four items of §7.4; the implementation PR description flags the §2.2/§4.1/§5.1 contract edits for owner ratification (ADR 0007 precedent); the §2.2 sentence rewrite of §7.4 item 3 is applied in `docs/tool-contracts.md` (no "no-op amendment check" is recorded — the old sentence is false under D7).
- **AC-35** Stale-prose sweep: `_resolution_for_ext` docstring, `reference_capture.py` ".py-only" cost note, `references.py` "Python-only capture today" docstring, `docs/tool-contracts.md:110–115`'s `"unavailable"` sentence + "honest value for non-Python targets" parenthetical (the §7.4 item 3 rewrite), and CLAUDE.md's v1 capability matrix (reference graph no longer Python-only; `module_members` still is) are all updated in the same PR; `_INSTALL_HINT` reworded per §6.2.
- **AC-36** Full CI gate set green (`ruff format --check`, `ruff check`, `mypy`, `complexipy ≤15`, `vulture`, `pytest` with ≥90% coverage, `uv lock --check`, `pip-audit`) — every new module obeys the file-size and function-size rules (each language module is naturally small: three query strings + one normalizer + one class).

## 11. Risks and accepted costs

- **Cross-language suffix-collision precision (accepted, D8).** A shared symbol tail can suffix-match qnames from different languages within one package: target `Config` (or `config.Config`) matches both the Python qname `src.config.Config` (Python module qnames are suffix-free) and the TS qname `src.config.ts.Config` — both tails are `Config`, both are strict-suffix candidates. Resolver Rule D's unique-match-or-None makes collisions yield deterministic None rather than wrong edges — a recall cost, not a precision cost, by construction; genuinely wrong unique matches remain possible across languages and are the accepted v1 cost the owner ratified.
- **Interleaved-extension suffix misses (accepted, §5.7).** Alias targets like `a.B` do not suffix-match `a.rs.B` — deep cross-file resolution has a lower hit rate in non-Python languages than in Python, where module qnames are suffix-free. In particular, JS/TS/Java IMPORTS rows structurally NEVER resolve in v1: their normalizers strip source extensions while persisted module qnames keep them (`a.b` vs `a.b.js`, `a` vs `a.ts`, `com.acme.G` vs `…G.java.G`); only C, whose include targets keep `.h`, has reliably resolving IMPORTS rows, and Rust IMPORTS resolve only for single-segment targets. The declared capability (`syntactic`, precision-biased) already hedges this; the D12 fixtures pin the floor in both directions (must-resolve shapes + expected-None pins).
- **Rule A alias lookup misses for ALL module-attributed (file-scope) refs (accepted).** `_module_part_of` routes through `split_symbol_qname`, whose no-class branch ALWAYS strips the last dotted segment; for a file-scope ref whose `from_node_id` IS the module qname (`pkg.src.lib.rs`) it returns `pkg.src.lib`, which never equals the alias-table key `pkg.src.lib.rs`. Consequence: file-scope aliased references (e.g. top-level `P.init()` after `const P = require('./a/b')`) never alias-rewrite → resolve to None — a recall cost, no wrong edges (pinned expected-None, AC-20). Symbol-attributed refs are unaffected: the lowercase extension segment sits at `parts[-2]`, so the class heuristic does not fire and the module part is recovered intact (§4.4). The capitalized-file-name sub-case (`pkg.Utils.ts` firing the class heuristic) is subsumed by this broader miss. Accepted; recorded here so a future change (analyzer-side dual-keying — itself collision-prone for same-stem files like `lib.rs`/`lib.ts` in one directory — or a module-aware resolver lookup) can cite it.
- **Impl-block dedup granularity (accepted, §4.4).** A Rust file with `struct Node` + `impl Node` + `impl Display for Node` carries three top-level spans named `Node`, slugged `Node` / `Node_2` / `Node_3`; edges attribute to the deduped span qnames verbatim, with no collapsing of impl blocks onto the struct's qname in v1 — callers querying the struct's qname will not see edges attributed to sibling `Node_N` spans.
- **Dependency-scope opt-in asymmetry (deliberate, D6).** Project code gets graphs by default; dependency code does not unless YAML opts in. Users comparing `get_references` across scopes will see the asymmetry; the census evidence (vendored-heavy dependency code) justifies it, and `meta.resolution` stays honest either way.
- **One-time full re-embed + re-extract on upgrade (deliberate, D9).** The cost of hash-honesty; subsumed into one migration run; release-notes item.
- **Default-install footprint grows ~6–10 MB (owner-waived).** Recorded per D5; the waiver is scoped to THIS promotion and does not soften the <1% clause for future candidates.
- **tree-sitter core wheel is per-CPython, and the `<0.26` ceiling needs revisiting** — both carried over from ADR 0021, now load-bearing in the default install: a new CPython minor needs a core wheel before pydocs-mcp wheels can ship for it; the 0.26 pin must be re-probed when upstream fixes the use-after-free. The retained text-fallback keeps absent/ABI-mismatched states functional (searchable text, honest `unavailable`).
- **Two parses per code file** (chunker + analyzer) — bounded, mirrors the Python "one extra ast.parse per file" cost note; the grammar/Query caches are shared and tree-sitter parses at ~235 files/s even uncached. If profiling ever shows this matters, a parse-once handoff between stages is a pure optimization behind the seam (not designed here; no interface reserves it).
- **`.h` files parsed as C.** C++ headers with `.h` extensions will partially mis-parse; the C grammar degrades to fewer matches, per-file containment covers pathological cases, and C++ as a language is out of scope (not in the extension set the owner fixed).

## 12. References

- `python/pydocs_mcp/extraction/strategies/analyzers.py` — the seam: Protocol + registry (`:48–116`), capability constants (`:73–83`), `PythonAstAnalyzer` (`:125–236`), markdown joinability WORKAROUND (`:261–266`), `__all__` (`:280–290`).
- `python/pydocs_mcp/extraction/pipeline/stages/reference_capture.py` — stage lookup/containment/IMPORTS filter (`:94–130`), config singleton (`:54–66`).
- `python/pydocs_mcp/extraction/strategies/references.py` — `ReferenceCollector` (`:69–103`), capture functions and `NodeReference` shape (`:106–293`), `canonical_dotted` (`:45–66`).
- `python/pydocs_mcp/extraction/strategies/reference_resolver.py` — rules (`:122–201`), tail index (`:108–120`), `split_symbol_qname` (`:253–302`), synthetic-suffix preference (`:32`, `:150–158`).
- `python/pydocs_mcp/extraction/strategies/chunkers/multilang_treesitter.py` — caches (`:75–78`), `_load_language`/`_import_language` (`:124–154`), probe rules (docstring `:14–25`), qname assignment (`:248–262`), span guard (`:238–245`), fallback log (`:291–306`), reset seam (`:309–315`).
- `python/pydocs_mcp/extraction/strategies/chunkers/multilang_queries.py` — `LANGUAGE_SPECS` (`:116–127`), root-anchored top-level query design (`:17–29`), TS `type_identifier` note (`:88–91`).
- `python/pydocs_mcp/extraction/strategies/chunkers/_shared.py` — `_module_from_doc_path` suffix-preserving ids (`:95–112`), `_identifier_slug` addressability rationale (`:247–278`).
- `python/pydocs_mcp/extraction/config.py` — allowlist composition (`:34–44`), `DiscoveryScopeConfig.include_extensions` default (`:196–211`), `DiscoveryConfig` per-scope fields (`:243–249`), excluded-dirs floor (`:47–87`).
- `python/pydocs_mcp/extraction/pipeline/stages/content_hash.py` — exclusion-fingerprint fold precedent (`:55–69`).
- `python/pydocs_mcp/application/project_indexer.py` — package-level skip (`:84–88`, `:122–126`).
- `python/pydocs_mcp/application/indexing_service.py` — per-package reference sweep (`:395`), embedder-mismatch hash clear (`:683–701`).
- `python/pydocs_mcp/retrieval/config/app_config.py` — `ingestion_pipeline_hash` folds (`:369–444`), `_effective_extension_scope` (`:353–367`).
- `python/pydocs_mcp/application/tool_router.py` — `_resolution_for_ext` (`:76–86`), `TARGET_EXTENSION_EXTRA` channel (`:170–206`).
- `tests/extraction/test_analyzers.py` — golden edge-set pin (`:67–106`), capability pins (`:123–136`), duplicate-registration pin (`:139–150`).
- `pyproject.toml` — `[multilang]` extra + pin rationale (`:114–133`), `watch = []` alias precedent (`:78–82`), mypy overrides (`:324–332`).
- `docs/tool-contracts.md` — freeze statement §1, `meta.resolution` §2.2 (`:102–118`), discovery scope §4.1 (`:336–380`), capability flags §5.1 (`:407–424`), sanctioned parameter categories §5.2–5.3.
- `docs/adr/0004-…` — the LanguageAnalyzer seam decision, additive-registration consequence, jedi-as-designated-semantic-backend invariance.
- `docs/adr/0007-…` — the owner-ratification amendment precedent.
- `docs/adr/0021-multilanguage-indexing.md` — census, probe rules, tiers, Decisions 1–9, costs; the wheels/licensing decisions this design extends.
- `docs/superpowers/research/2026-07-21-multilang-evidence-treesitter.md` — the 0.26.0 use-after-free probe record behind the core pin.
