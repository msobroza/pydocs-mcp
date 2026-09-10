# ADR 0022 — Multilang reference analyzers: per-language tree-sitter capture, availability-aware capabilities, dependency promotion, and per-scope defaults

**Status:** Accepted — contract-line amendments (§2.2, §3.5, §4.1, §5.1) applied in the implementation PR and flagged for owner ratification (ADR 0007 precedent; both prior ADR 0021 amendments followed this path and were ratified same-cycle) ·
**Date:** 2026-07-29 · **Phase:** feature (post-Phase-4, pre-paid-arc)

- **Decision area:** extending the reference graph (CALLS / INHERITS / IMPORTS + alias tables) from Python-only capture to `.rs .c .h .js .ts .tsx .java`; the declared capability matrix; packaging; discovery defaults; index-coherence migration. Owner: twelve decisions D1–D12 fixed interactively 2026-07-28/29 (design doc `docs/superpowers/specs/2026-07-29-multilang-reference-analyzers-design.md`), including two explicit gates: the <1% footprint-clause waiver for the tree-sitter promotion, and the `.java` ceiling widening.
- **Siblings:** ADR 0004 (the `LanguageAnalyzer` seam this ADR finally exercises; "references: unavailable is a legal launch state" — this is the follow-through), ADR 0021 (the chunker tiers, probe rules, and R9 individual-MIT-wheels-only decision this ADR completes), ADR 0007 (the owner-ratification amendment precedent used for the contract lines), ADR 0003 (the frozen nine-tool surface everything here lands behind).

## Context

ADR 0021 shipped multilanguage *indexing*: `MultilangChunker` persists top-level symbol trees for six code extensions, degrading internally to text windows when a grammar is absent. The reference graph stayed Python-only: `analyzer_registry` held exactly `.py` and `.md`, `ReferenceCaptureStage` silently skipped every other extension, and `get_references` for a non-Python target reported `meta.resolution = "unavailable"`. The graph is a differentiating retrieval signal (graph expansion ships in the default docs pipeline), and the asymmetry — multilanguage chunkers, discovery scope, and eval strata, but a Python-only graph — required the capability matrix to keep apologizing.

## Evidence

- **The seam was built for this.** ADR 0004 froze `LanguageAnalyzer` + `analyzer_registry` so adding a language is additive registration; `node_references` DDL is language-neutral TEXT (no schema change).
- **Trees already exist to join against.** The chunker's `LANGUAGE_SPECS` root-anchored queries (`chunkers/multilang_queries.py`) produce top-level spans with real 1-indexed line numbers. The analyzers attribute edges by bisecting those SAME spans: they run the chunker's own extraction loop (`_positioned_symbols_from_tree` in `chunkers/multilang_treesitter.py`, over its cached top-level query; the index bisects the item nodes' tree-sitter `(row, column)` points, so two items on one line never share attribution) and assign qnames with ONE shared helper (`_assign_top_level_qnames` in `chunkers/_shared.py`, which also owns the start-line sort). That makes joinability structural rather than aspirational (the markdown analyzer's WORKAROUND comment documents the failure mode this prevents).
- **The resolver is shape-compatible unchanged.** Rules B/C/D operate on plain dotted strings; suffix-preserving module qnames (`src.lib.rs`) are reachable by strict-suffix matching from single-segment dotted targets. Multi-segment targets miss on the interleaved extension segment (`a.B` vs `a.rs.B`) and return deterministic None — a recall cost, not wrong edges. File-scope (module-attributed) refs never alias-rewrite (`_module_part_of` in `extraction/strategies/reference_resolver.py` delegates to `split_symbol_qname`, whose no-class branch strips the module qname's last segment) — also deterministic None, pinned expected-None in the test suite.
- **Probe rules carry over wholesale** (ADR 0021 / evidence-treesitter): `QueryCursor.matches()` never `captures()`; Tree + cursor bound to live locals; 1-indexed spans; the `0x3FFFFFFE` sentinel span guard; `tree-sitter>=0.25,<0.26` (0.26.0 use-after-free, probe-verified 5/5).
- **Census (ADR 0021):** second-language code skews vendored in dependencies (127 of matplotlib's 222 C/C++ files under `extern/`), while project code is what users ask about — the basis for the per-scope defaults split.

## Options considered

- **Single data-driven analyzer table** — REJECTED (owner, D4): the grammars genuinely differ (TS `type_identifier` vs JS `identifier` class names; C has includes and no inheritance; Java has no top-level functions); a table pushes differences into escape hatches. Per-language modules keep each language greppable and independently editable.
- **Keep `[multilang]` as the gate for the graph** — REJECTED (owner, D5): a default-ON reference graph that silently stays empty unless an extra is installed violates capability honesty from the other direction. Promotion under an explicit footprint waiver removes the cliff; the text-window degrade path stays for sdist/ABI-mismatch.
- **Filesystem-level import resolution** — REJECTED (D8): the same syntactic (not semantic) line Python draws; a future semantic backend flips only the declared value (ADR 0004 invariance).
- **Collapsing impl blocks onto the struct's qname** — REJECTED for v1 (§4.4): attribution follows the chunker's deduped span qnames verbatim (`Node`, `Node_2`); collapsing would break the joinability-by-construction invariant.
- **`tree-sitter-language-pack`** — remains REJECTED (ADR 0021 R9): individual official MIT wheels only.

## Decision

1. **Seven extensions, five analyzer modules** behind the existing seam, under `extraction/strategies/analyzers/`: `rust.py` (`.rs`), `c_lang.py` (`.c .h`), `javascript.py` (`.js`), `typescript.py` (`.ts .tsx`), `java.py` (`.java`), with the shared tree-sitter plumbing in `_treesitter.py`. `analyzers.py` becomes the package `analyzers/__init__.py`, preserving the import path: the seam, `PythonAstAnalyzer`, and `MarkdownMentionsAnalyzer` stay there, and the language modules register by import side effect. Full capture-depth parity with Python where the language expresses the concept: CALLS + INHERITS + IMPORTS, alias tables always captured, IMPORTS rows filtered downstream (the existing stage contract).
2. **Top-level attribution, joinable by construction:** `from_node_id` is the enclosing top-level symbol qname (else the module qname), computed by bisecting the chunker's own spans with the qname assignment in ONE shared helper. Nested members stay unpersisted.
3. **Availability-aware capabilities:** `LanguageAnalyzer.capabilities` becomes a read-only property (plain class attributes still satisfy it structurally, so the Python and Markdown analyzers are unchanged). Tree-sitter analyzers answer through `capabilities_for(ext)` in `analyzers/_treesitter.py`, which reads the chunker's memoized grammar verdict: `TREESITTER_ACTIVE_CAPABILITIES` = `{outline: available, definitions: available, references: syntactic}` when the grammar loads, and `TREESITTER_DEGRADED_CAPABILITIES` = `{outline: available, definitions: unavailable, references: unavailable}` degraded. `meta.resolution` never claims `"syntactic"` for a deployment whose graph is structurally empty.
4. **Packaging promotion:** `tree-sitter>=0.25,<0.26` + the five grammar wheels (`-rust`, `-c`, `-javascript`, `-typescript`, `-java`) move into `[project] dependencies`; `multilang = []` becomes a deprecated empty alias (the `watch = []` precedent). **Owner waiver recorded:** the <1% footprint clause of the promotion exception is explicitly waived for THIS promotion (~6–10 MB on ~90 MB); the other three criteria hold. The waiver does not soften the clause for future candidates.
5. **Per-scope defaults:** `discovery.project.include_extensions` gains the seven code extensions (`_DEFAULT_PROJECT_INCLUDE_EXTENSIONS`); `discovery.dependency` keeps text/config (`_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS`), both in `extraction/config.py`; `ALLOWED_EXTENSIONS` gains `.java` (via `_CODE_EXTENSIONS`); `LANGUAGE_SPECS` gains a Java entry (classes/interfaces/enums/records → CLASS; no top-level functions).
6. **Migration:** the existing unconditional extension-scope → `ingestion_pipeline_hash` fold yields the one-time full re-embed. NEW: an unconditional loadable-grammar fingerprint salt in the package content hash — `loadable_grammar_fingerprint()` in `chunkers/multilang_treesitter.py` (the sorted list of code extensions whose grammar loads), folded by `ContentHashStage` (`extraction/pipeline/stages/content_hash.py`) after the conditional exclusion fold. It rescues deployments whose graph was indexed empty: when grammars appear later, unchanged packages re-extract.
7. **OWNER ESCALATION — the contract lines.** Three `docs/tool-contracts.md` amendments land in the implementation PR, flagged in the PR description for ratification: (a) §2.2's `"unavailable"` sentence rewritten for the two-state declaration (the "honest value for non-Python targets" parenthetical deleted — it inverts under this ADR); (b) §4.1's ceiling gains `.java` and the default sentence records the project/dependency split; (c) §5.1 gains the two-state capability rows for the six non-Python languages. The three-value `meta.resolution` enum, the frozen §5.1 vocabulary, and the nine-tool surface are UNCHANGED — no new tool, no new parameter, no envelope field.
8. **Release number:** not set here; the next release PR picks it (CHANGELOG entry under `## [Unreleased]`).

## Consequences

Benefits: the reference graph covers every indexed code language with honest per-deployment declarations; adding a language remains additive registration; the resolver, storage schema, and MCP surface are untouched; the chunker/analyzer extension sets are pinned equal by a drift-guard test (`tests/extraction/test_analyzers.py`).

Costs and risks (accepted, recorded in the design doc §11): cross-language suffix-collision recall losses (deterministic None); multi-segment alias targets never resolve across the interleaved extension segment — JS/TS/Java IMPORTS rows structurally never resolve in v1 (C's `.h`-keeping includes resolve reliably; Rust IMPORTS resolve only for single-segment targets); file-scope aliased refs never alias-rewrite; impl-block dedup granularity (`Node_2`); ~6–10 MB default-install growth (owner-waived); the per-CPython tree-sitter core wheel and the `<0.26` ceiling are now load-bearing in the default install; two parses per code file (bounded; caches shared); `.h` parsed as C.

Grammar-salt blast radius: `loadable_grammar_fingerprint()` runs on every package hash, including pure-Python projects and every dependency. `_import_language` catches only `ImportError` and `ValueError`, so any other grammar-import error (for example an `AttributeError` from a renamed accessor) is not memoized and re-raises on every hash. Nothing between `ContentHashStage` and the index pass catches it, so it would abort a default `index` run (or the serve start-up pass) at the project pass, before any dependency is indexed. Under `--skip-project` each dependency fails separately (caught per package and counted), and a `--watch` reindex logs the error and keeps serving the previous index. Before this change such an error broke only that extension's files. The upper-bounded grammar pins make this unlikely.

A grammar that loads but rejects its top-level symbol query (a `tree_sitter.QueryError`, e.g. after a grammar release renames a node type) counts as unloadable: `_import_language` compiles that query inside its probe, so capabilities, the chunker and the fingerprint all read one verdict, and the salt flips once the grammar is fixed.

Capabilities describe the SERVING process, not the index: `capabilities_for` reads the serving process's grammar verdict. A read-only bundle built before the upgrade, or while grammars were unloadable, must be rebuilt before its code targets report a populated graph. Follow-up: stamp the fingerprint in `index_metadata`, so a server can tell.

Reference rows per file are uncapped, as they are for Python; one minified bundle can emit thousands. Exclude vendored or minified trees (`extern/`, `vendor/`, `static/`) with `discovery.*.exclude_dirs`.

Prebuilt wheels cover the default install's supported platforms. musllinux is not one of them in full: `tree-sitter-typescript` (like `tree-sitter-java` and the core) ships no musllinux aarch64 wheel, and there the text-window degrade covers a grammar that cannot load.

A fourth contract line, §3.5's `get_references` Backend bullet, now names the tree-sitter analyzers; it is flagged for owner ratification like the §2.2 / §4.1 / §5.1 amendments.

P1 obligation (owner ruling 2026-09-10): the multi-branch `file_extractions` cache must also require a matching `loadable_grammar_fingerprint()` on a hit, and clean up rows from a previous `pipeline_hash` — recorded at the `split_cache_hits` task of `docs/superpowers/plans/2026-09-04-multi-branch-indexing-p1-multi-branch.md`.

v1 capture limits (accepted; each is "no edge", never a wrong edge):

- **TypeScript CommonJS `require` produces no rows at all.** The TypeScript imports query is ESM-only (import and export statements), and the CALLS pass skips `require`.
- **Java `@interface` (annotation type) declarations are deliberately omitted** from the top-level symbol query, so they get no symbol node.
- **JavaScript `require` is captured only as a program-level `const` or `let` binding of a single identifier** (`const x = require('./m')`). `var`, bare `require('./m')`, and destructured `const { a } = require('./m')` produce no rows. This is narrower than spec §5.4's "CommonJS `require("…")` at file scope" wording.
- **Exported JS/TS declarations (`export class B`, `export function f`) get no symbol node.** The chunker's root-anchored queries skip `export_statement`, so edges inside them attribute to the module qname, which is never alias-rewritten — coarser attribution, not a wrong edge. The fix changes chunk trees (a re-embed), so it is a follow-up.
- **Recall gaps:** call chains split across lines are dropped (`canonical_target` rejects internal whitespace); Rust turbofish calls (`f::<T>()`) are not captured; `pub(crate)` / `pub(super)` / `pub(in …)` `use` declarations yield no rows; scoped npm sources (`@scope/pkg`) drop their IMPORTS rows, and side-effect imports (`import './x'`) are not captured.

## Action items

Product (`python/pydocs_mcp/`):

1. Package conversion + `_treesitter.py` shared plumbing + five language modules (implementation plan `docs/superpowers/plans/2026-07-29-multilang-reference-analyzers.md`, Tasks 1–8).
2. Capabilities property + router two-state behavior (Tasks 3, 9).
3. Packaging promotion + relock; per-scope defaults; grammar salt (Tasks 10–12).
4. Contract §2.2/§4.1/§5.1 edits + prose sweeps (Task 13).

Owner checkpoints:

5. Ratify the §2.2/§3.5/§4.1/§5.1 amendments from the PR description (gate opened 2026-07-28/29; this ADR records the ratification wording once given).
6. Release-notes review: one-time full re-embed + re-extract (the project and every dependency package) on first index after upgrading; project-scope code files indexed by default (narrow via YAML to opt out); `[multilang]` now an empty no-op alias.
