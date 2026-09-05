# ADR 0021 — Multilanguage indexing: census-scoped tiers, availability-aware grammar chunkers, and index-scope identity

**Status:** Accepted — contract-line amendments owner-ratified 2026-07-21
(applied to `docs/tool-contracts.md` §2.2 and §4.1 in the same PR) ·
**Date:** 2026-07-21 · **Phase:** feature (post-Phase-4, pre-paid-arc)

- **Decision area:** the owner-approved multilanguage tiers T1 (extension
  scope), T2 (text/config chunker), T3 (tree-sitter grammars behind an
  optional extra), plus the capability-honesty matrix, the two
  index-identity fixes, and the eval hooks. Owner: "do all 1-3" (2026-07-21).
- **Siblings:** ADR 0004 (the LanguageAnalyzer seam, `chunker_registry` /
  `analyzer_registry`, `LanguageCapabilities` — the frozen seam every tier
  lands on), ADR 0003 (file tools walk exactly the indexer's discovery
  scope — why T1 widens both at once), ADR 0014 (index purity), ADR
  0016/0018 (campaigns declaring multi-language out of scope — source of
  the landing deadline), ADR 0007 (the owner-ratification precedent).

## Context

The MCP surface is frozen at nine task-shaped tools, but the corpus behind
it is Python-only: `ALLOWED_EXTENSIONS = {".py", ".md", ".ipynb"}` is a
code-enforced ceiling (`extraction/config.py:29`) whose `_enforce_allowlist`
validator (`config.py:148-158`) makes YAML `include_extensions`
**narrow-only — YAML alone cannot widen the set** (verified). The owner
approved three multilanguage tiers, motivated by "some python projects are
multilanguage". This ADR records what the census made of that directive, the
tier designs, the two correctness fixes any future multilang-on/off ablation
depends on, and the two frozen-document lines requiring owner ratification —
all behind the frozen surface: no new tool, param, or envelope field.

## Evidence

**The census verdict — the decision spine.** Measured over all 1,887 pinned
SWE-bench-Live instances + 12 cloned dev repos + this repo
(evidence-language-census §1-4): the gold-patch EDIT surface is **72.0%
Python, 13.3% docs, 8.8% config, 2.0% web, 0.2% native code** (0.4% of
instances). **44.0% of instances touch ≥1 non-`.py` file** — but that mass
is `.rst`/`.md`/`.yaml`/`.toml`/`.json`/`.cfg` text (`.rst` alone: 15.8% of
instances), exactly the T1+T2 set. Second-language *source* is a read-side
minority, concentrated in vendored trees (127 of matplotlib's 222 C/C++
files under `extern/`) and UI sub-projects. **Rust appears zero times in
the dev pool**; the only `.rs` in the study is this repo's own `src/lib.rs`.
Binary assets are 23%+ of READ bytes, zero retrieval value. T1+T2 are the
measured value; T3 is built per the owner's directive, scoped by evidence.

**The seam already exists (ADR 0004).** `chunker_registry` is keyed by
extension (`extraction/serialization.py:36`); duplicate registration for one
extension **raises `ValueError` at import** (`serialization.py:64-66`) — not
last-write-wins. Unknown extensions are skipped silently
(`stages/chunking.py:55-58`), so T1-without-T2 would discover files that
yield zero chunks. `get_symbol`/`get_context` resolve off DocumentNode
trees, not `module_members` (`application/lookup_service.py:437, 572, 645`)
— any chunker's tree feeds them; `ReferenceCaptureStage` skips unregistered
extensions silently (`stages/reference_capture.py:108-110`). One honesty
bug: `meta.resolution` is hardcoded to `PYTHON_CAPABILITIES["references"]`
(`application/tool_router.py:52, 168`) — non-Python targets overstate.

**tree-sitter 0.26.0 is memory-unsafe (probe-verified).** The identical
script that runs clean on 0.25.2 (5/5 EXIT=0) crashes on 0.26.0 (5/5 —
SIGBUS/SIGSEGV at teardown, a use-after-free in the `QueryCursor.matches()`
path) or returns garbage spans (row `1073742078` = `0x3FFFFFFE`, the
invalid-node sentinel, in a 598-line file). ABI-15 grammar wheels set the
floor at 0.25. Probe rules (evidence-treesitter §3): use `matches()` —
`captures()` returns per-name lists in independent document order, so
pairing silently misaligns; bind `QueryCursor` + `Tree` to live locals (an
inline temporary is GC'd mid-iteration → segfault); spans map as
`start_point.row+1 .. end_point.row+1` (verified on 25 `src/lib.rs` items);
reuse compiled `Query` objects (~235 files/s even recompiling per call —
not a build bottleneck). Purity: parsing an in-memory buffer under
`HOME=/nonexistent` and empty `PATH` produced a correct tree — no env,
files, or network — ADR 0014's project-index purity survives T3.
Licensing: the four official grammar wheels (`tree-sitter-javascript`,
`-typescript`, `-c`, `-rust`) are individually MIT, abi3, typed; the
aggregate `tree-sitter-language-pack` vendors 306 grammars whose licenses
are **not auditable from metadata** — R9 violation.

**The two index-identity gaps (verified).** (1) `ingestion_pipeline_hash`
folds only embedder + backend identity + `ingestion.yaml` bytes
(`retrieval/config/app_config.py:404-419`) — `include_extensions` is
absent, so multilang-on/off indexes share one pipeline identity. (2)
**CRITICAL:** campaign index-cache slots key on path only (checkout slug =
`<repo_slug>@<commit>`, `campaign/index_cache.py:54-58`; db name = md5 of
the absolute path, `db.py:171-181`) and `index_checkout` short-circuits on
`db.exists()` (`index_cache.py:138-156`) before any product invalidation
runs — a multilang campaign pointed at an existing `--cache-root` would
silently REUSE Python-only indexes, undetectable by the aggregator's R5
guard because the trace-header `artifact_hash` is the descriptions surface,
corpus-independent (`description_source.py:450-469`).

**Eval hooks are additive (verified).** `campaign_report` already accepts an
optional `stratum_of: Mapping[str, str]` (`campaign/aggregator.py:246-265`);
the CLI never passes one (`campaign/__main__.py:47`). Every rollout's
`facts.json` already requires `gold_files` (`compute_metrics_cli.py:59`).
The frozen pre-registration's `registration_hash` digests only the fixed
slots — **no strata field** (`optimize/prereg/config.py:175-186`) — so a
reporting stratum cannot change it. `CellConfig.suggestion_overlay` is
defined and lockfile-hashed (`campaign/cells.py:44-69`), **no consumer**.

**Blast radius (verified).** The descriptions-seed Phase-4 hash domain is
disjoint (tool-description text only) — unaffected while
`defaults/descriptions.md` / `application/tool_docs.py` stay untouched.
Registering chunkers alone changes no hash: an unused feature costs zero
reindex. Four tests + one YAML pin the default extension set
(`tests/retrieval/test_config.py:281-284,312-315`,
`tests/test_config_serve_watch.py:14`, `tests/test_watcher.py:126`,
`defaults/default_config.yaml:48,55`). No `NodeKind` exhaustiveness test
exists — a new member is additive-safe.

## Options considered

- **T1 via YAML only.** Impossible, not merely rejected: `_enforce_allowlist`
  (`extraction/config.py:148-158`) rejects any extension outside the
  code-level frozenset; widening is a code change by design.
- **Widen the DEFAULT to code extensions (`.js .ts .rs …`).** Buried by the
  census: second-language source is 0.2% of gold edits (~1 in 250
  instances) and read-side skews vendored — default-on would index library
  noise. Text/config is 22% of gold files. Hence the default/ceiling split.
- **Aggregate grammar pack (`tree-sitter-language-pack`).** REJECTED on R9:
  306 vendored grammars, licenses unauditable from metadata
  (`tree-sitter-languages` is additionally abandoned since 2024-02).
  Individual official MIT wheels are the auditable route.
- **tree-sitter 0.26.x.** Rejected on the probe-verified memory-safety
  regression; under pytest a segfaulting worker fails CI.
- **Eager registration of both T2 and T3 chunkers per code extension.**
  Impossible: duplicate registration raises `ValueError`
  (`serialization.py:64-66`); the fallback must live *inside* one chunker.
- **Raise (fast_plaid-style) when `[multilang]` is absent.** Wrong
  precedent: indexing is a background batch build, not a user query;
  `NullVectorStore`'s degrade-with-a-log is the match, and skipping is
  strictly worse than text chunks.
- **Cython grammar.** Deferred: 0.6% of files, one dev-pool instance, no
  stable grammar; T2 covers `.pyx` acceptably.

## Decision

1. **T1 — default vs ceiling, split by the census.** Lift
   `ALLOWED_EXTENSIONS` (`extraction/config.py:29`) to the full T1+T2+T3
   set, keeping the validator (still an allowlist, larger). The DEFAULT
   `include_extensions` widens only to the text/config set — `.toml .yaml
   .yml .cfg .ini .rst .txt .json` joining `.py .md .ipynb`. Code
   extensions (`.js .ts .tsx .c .h .rs`) stay **ceiling-only opt-in** via
   YAML; binary/asset extensions are never allowed. The four extension-pin
   tests + `default_config.yaml:48,55` are re-pinned deliberately.
2. **OWNER ESCALATION — the contract line.** Frozen `docs/tool-contracts.md`
   §4.1 line 352 documents the allowlist as "(narrow-only)" with default
   `['.py', '.md', '.ipynb']`; T1 falsifies BOTH — the word AND the default
   list, which widens to the text/config set. Not a tool/param/envelope
   change — the nine-tool freeze is intact — but the sentence is a
   documented invariant in a frozen document. **Implementation does not
   touch the contract**; the amendment (both the "(narrow-only)" word and
   the stale default list) is flagged in the PR for owner ratification
   (ADR 0007 precedent).
3. **T2 — `TextSectionChunker`.** One language-agnostic chunker registered
   for the text/config set: heading-aware for `.rst`/`.txt` (fixed-line
   windows as fallback), key-path aware where cheap for
   `.toml/.yaml/.cfg/.ini` (top-level tables/keys as section titles),
   **JSON capped** per file (the fixture-flooding finding — `.json` is
   17.9% of READ files but 2.2% of edits), real 1-indexed line spans. One
   new language-neutral `NodeKind.TEXT_SECTION` + matching `ChunkOrigin` +
   `_KIND_TO_ORIGIN` / `STRUCTURAL_ONLY_KINDS` entries (additive-safe). The
   vendored-dir exclusion floor grows the obvious entries (`node_modules`,
   `extern`, `third_party`, minified assets) — a hard prerequisite.
4. **T3 — grammars behind `[multilang]`.** Optional extra:
   `tree-sitter>=0.25,<0.26` (0.26.0 excluded on the memory-safety probe) +
   individual official MIT abi3 grammar wheels — `tree-sitter-javascript`,
   `tree-sitter-typescript` (verify wheel at implementation),
   `tree-sitter-c`, `tree-sitter-rust`, the last the **explicit dogfood
   choice against the census** (Rust = 0 in the dev pool; labeled as such).
   Python stays on the stdlib-`ast` chunker. Implementation follows the
   probe rules: `matches()` not `captures()`; cursor + tree bound to live
   locals; `row+1` spans; compiled `Query` reused. Static byte-parse, never
   imports — ADR 0014 purity holds.
5. **Availability-aware `MultilangChunker`, internal T2 fallback.** ONE
   registration per T3 extension. It lazily imports tree-sitter + the
   grammar; with `[multilang]` present it emits structural symbols; absent,
   it **falls back internally to the T2 text chunker** (shared logic), so
   the file still indexes as searchable text. Silent degrade + one
   structured `multilang_fallback` JSON log per build with the
   `pip install 'pydocs-mcp[multilang]'` hint — the NullVectorStore
   precedent; also forced by the duplicate-registration `ValueError`.
6. **Capability honesty (v1 matrix).** Any registered chunker's tree feeds
   `search_codebase`, `get_symbol` (outline/spans), `get_context`, and
   `parent_rollup` for every language — verified tree-driven.
   `module_members` and the reference graph stay Python-only in v1;
   references degrade honestly to empty/unavailable. **Fix the honesty
   bug:** route `meta.resolution` (`tool_router.py:52, 168`) through
   `language_capabilities(ext)` for the target's extension.
   `language_capabilities(ext)` returns `None` for every unregistered
   extension — that is ALL T2 text/config and T3 code targets, since only
   `.py` and `.md` carry analyzers (`analyzers.py:125, 239`). Exact emitted
   values: `.py` → `"syntactic"`, `.md` → `"syntactic"`, and `None` (every
   other target) → `"unavailable"` — the `references` value from the §5.1
   `LanguageCapabilities` vocabulary (`analyzers.py:45`), which contract
   §5.1 (line 411) already declares surfaces in `meta.resolution`. The
   wrinkle: frozen §2.2 line 110 enumerates `meta.resolution` as only
   `"syntactic" | "semantic"`, omitting the `"unavailable"` its own §5.1
   flag admits. So the honest value uses §5.1 vocabulary but steps outside
   §2.2's narrower two-value enumeration — §2.2:110 therefore joins the
   item-8 owner-ratification flag (Decision 2); the fix does not self-edit
   the contract.
7. **Index identity — the two correctness fixes.** (a) Fold the *effective
   extension scope* (the sorted effective extension set) into
   `ingestion_pipeline_hash` **unconditionally** — like the
   backend-identity fold (`app_config.py:410`), NOT gated on YAML bytes the
   way the `embed_chunks_multi_vector` late-interaction fold is
   (`app_config.py:417`). Gating it would keep the default-install hash
   stable and thereby defeat the multilang-on/off identity separation this
   fix exists to create; the scope digest must always mix in. Then
   multilang-on/off produce distinct chunk-cache identities and flipping
   scope re-embeds by design. (b) Campaign index-cache canonical slugs gain
   pipeline/scope
   identity so the `db.exists()` short-circuit can never reuse a
   Python-only index for a multilang cell (the verified-CRITICAL path).
8. **Landing deadline.** T1–T3 plus fix 7 merged **before the paid arc's
   first `prebuild-index` / `write_lockfile`** (R5/R7): after either freeze
   event, a multilang change is a new-campaign-ID event, never in-place.
9. **Eval hooks (additive; the frozen prereg untouched).** A
   `gold_touches_non_python` stratum computed from each trajectory's
   `facts.json` `gold_files` (already required), via generic
   `--stratum-map` plumbing on the `aggregate` subcommand — strata are
   reporting dimensions; the registration hash has no strata field
   (verified). Build the overlay-name→serve-config resolver (a stub even
   for `suggestion_overlay`), serving suggestions too, so a future
   multilang on/off cell is expressible; **no new campaign axis now**.

## Consequences

Benefits:

- The default widening serves the measured need (~22% of gold files)
  without indexing vendored second-language noise; code languages become a
  YAML edit, not a release.
- Zero cost when unused: registering chunkers changes no hash; a deployment
  pinning the pre-widening extension set sees byte-identical indexes. Stock
  deployments, whose default itself widens, take the deliberate one-time
  re-embed on upgrade (see Costs) — the scope fold (7a) is unconditional.
- Degrade paths are declared, not silent: internal T2 fallback + structured
  log; honest `meta.resolution`; empty references per ADR 0004.
- The two identity fixes make a future multilang ablation valid by
  construction; nine-tool freeze, descriptions seed, and ADR 0014 purity
  all survive untouched (verified disjoint domains).

Costs and risks:

- Two frozen-contract lines must be amended (§4.1:352 allowlist wording +
  default list; §2.2:110 `meta.resolution` enum) — an owner action; until
  both are ratified this ADR's status carries the pending flag.
- Widening the default re-extracts affected packages on first reindex, and
  the pipeline-hash fold (7a) forces a one-time re-embed — deliberate.
- `tree-sitter` core wheels are per-CPython, not abi3 — wheel-availability
  risk on new Python minors (grammars are abi3, decoupled); the <0.26
  ceiling needs revisiting when upstream fixes the use-after-free — the
  fixture parity test guards bad releases.
- `ChunkingConfig` tunables remain outside `pipeline_hash` (pre-existing
  latent staleness) — T2's config sub-model inherits it; noted, not fixed.
- Rust grammar support is dogfood-vanity by the census's own numbers;
  labeling it here prevents citing it later as eval-driven.

## Action items

Product (`python/pydocs_mcp/`):

1. T1: widen `ALLOWED_EXTENSIONS` (`extraction/config.py:29`) and the
   default `include_extensions` (`config.py:141`,
   `defaults/default_config.yaml:48,55`); re-pin, with rationale,
   `tests/retrieval/test_config.py:281-284,312-315`,
   `tests/test_config_serve_watch.py:14`, `tests/test_watcher.py:126`; add
   an accepted-extension test mirroring the rejection test.
2. T2: `TextSectionChunker` under `extraction/strategies/chunkers/` +
   side-effect registration; `NodeKind.TEXT_SECTION` + `ChunkOrigin` +
   `_KIND_TO_ORIGIN` / `STRUCTURAL_ONLY_KINDS` entries (`document_node.py`,
   `tree_flatten.py:30-39`); JSON cap; grow the vendored-dir floor.
3. T3: `[multilang]` extra in `pyproject.toml` (`tree-sitter>=0.25,<0.26` +
   the four pinned grammar wheels; verify the typescript wheel);
   `MultilangChunker` with lazy import, internal T2 fallback, structured
   log; fixture parity test: `matches()` yields in-range spans at exit 0.
4. Honesty fix: route `meta.resolution` through `language_capabilities(ext)`
   (`application/tool_router.py:52, 168`), mapping `None` (every
   unregistered extension — all T2/T3 targets) → `"unavailable"` and `.md`
   → `"syntactic"`; test with a non-Python target asserting `"unavailable"`.
   Do NOT self-edit §2.2:110 — its enum widening rides item 8.
5. Identity fix (a): fold the effective extension scope into
   `ingestion_pipeline_hash` **unconditionally**
   (`retrieval/config/app_config.py:404-419` — mix the sorted effective set
   in like the backend fold at :410, do NOT gate it like the
   `embed_chunks_multi_vector` fold at :417) + a test distinguishing scopes.
   Do NOT edit `defaults/descriptions.md`,
   `application/tool_docs.py`, or `docs/tool-contracts.md` (see item 8).

Eval (`benchmarks/src/pydocs_eval/`):

6. Identity fix (b): add pipeline/scope identity to the canonical
   index-cache slug (`campaign/index_cache.py:54-58, 138-156`) so
   `db.exists()` cannot short-circuit across scopes; reuse-path regression
   test.
7. `--stratum-map` plumbing on the `aggregate` subcommand
   (`campaign/__main__.py:99-104`) + a `gold_touches_non_python` map
   builder over run `facts.json` `gold_files`, threaded into
   `campaign_report(stratum_of=…)`; overlay-name→serve-config resolver
   consuming `CellConfig.suggestion_overlay` (`campaign/cells.py:44-69`),
   threading `--config` through `render_mcp_config` (`_command.py:122-149`).

Owner checkpoints:

8. **Ratify two frozen-document lines, both flagged in the PR, neither
   self-edited (ADR 0007 precedent):** (i) `docs/tool-contracts.md`
   §4.1:352 — both the "(narrow-only)" word AND the documented default list
   `['.py', '.md', '.ipynb']`, rewording to the widened, still-allowlisted
   ceiling and the new text/config default set (Decision 1); (ii) §2.2:110
   — the `meta.resolution` enum `"syntactic" | "semantic"`, extended to
   admit the `"unavailable"` its own §5.1 flag already carries (Decision 6).
   This ADR's status drops the pending qualifier once both land.
9. Confirm landing order: merge before the paid arc's first
   `prebuild-index` / `write_lockfile` (Decision 8); anything later is a
   new campaign ID.
