# Multilanguage indexing — decision reconciliation (2026-07-21)

Reconciler's record for the owner-approved tiers 1–3, decided against the
four evidence files `2026-07-21-multilang-evidence-*.md`. Authoring brief for
ADR 0021. Owner directive: "do all 1-3" (2026-07-21), motivated by
"some python projects are multilanguage".

## The census verdict (drives everything)

Measured over all 1,887 pinned Live instances + 12 cloned dev repos +
this repo: the EDIT surface is 72.0% Python, 13.3% docs, 8.8% config,
2.0% web, **0.2% native code** (0.4% of instances). 44% of instances touch a
non-`.py` file — and that mass is `.rst/.md/.yaml/.toml/.json/.cfg` text,
exactly tiers 1–2. Second-language *source* is read-side minority and often
vendored. Rust appears zero times in the dev pool; the only `.rs` in the
study is our own `src/lib.rs`. Consequence: **T1+T2 are the value; T3 is
built per the owner's directive but scoped by evidence** — grammar set
JS/TS + C (+ Rust as the explicit dogfood choice, labeled as such), Cython
deferred.

## Decisions

**T1 — extension scope.**
- Lift the code-level `ALLOWED_EXTENSIONS` ceiling (extraction/config.py; it
  is narrow-only enforced — YAML alone cannot widen, verified) to the full
  T1+T2+T3 set; keep the validator (still an allowlist, larger).
- **Split default vs opt-in by the census**: the DEFAULT
  `include_extensions` widens to the text/config set
  (`.toml .yaml .yml .cfg .ini .rst .txt .json` + existing
  `.py .md .ipynb`) — these are 22% of gold files and the ordinary-UX win;
  code extensions (`.js .ts .tsx .c .h .rs`) stay **ceiling-only opt-in**
  via YAML. Re-pin the four extension-pinning tests deliberately.
- Binary/asset extensions are never allowed (png = 23% of read bytes, zero
  retrieval value).
- **Escalation to owner (not self-edited):** frozen `docs/tool-contracts.md`
  §4.1:352 documents the allowlist as "(narrow-only)" — T1 contradicts the
  documented invariant. Nine-tool freeze intact (no tool/param/envelope
  change); the one-line contract amendment is flagged in the PR for owner
  ratification, ADR 0007-style. Implementation does not touch the contract.

**T2 — the text/config chunker.**
- One language-agnostic `TextSectionChunker` registered for the text/config
  set: heading-aware for `.rst/.txt` (fallback fixed-line windows), key-path
  aware where cheap for `.toml/.yaml/.cfg/.ini` (top-level tables/keys as
  section titles), **JSON capped** (down-weight/limit chunks per file — the
  fixture-flooding finding), real 1-indexed line spans.
- NodeKind: add one language-neutral `text_section` kind (+
  `_KIND_TO_ORIGIN` and structural-set entries; additive-safe — no
  exhaustive-enum tests exist, verified).
- Vendored-directory exclusion floor grows the obvious entries
  (`node_modules`, `extern`, `third_party`, minified assets) — hard
  prerequisite finding.

**T3 — tree-sitter grammars.**
- Optional extra `[multilang]`: `tree-sitter>=0.25,<0.26` (**0.26.0 has a
  probe-verified memory-safety regression** — SIGBUS/SIGSEGV + garbage spans
  from `QueryCursor.matches()`; 0.25.2 clean; ABI-15 grammar wheels set the
  floor at 0.25) + individual grammar wheels (each MIT, abi3):
  `tree-sitter-javascript`, `tree-sitter-typescript` (verify wheel at
  implementation), `tree-sitter-c`, `tree-sitter-rust`. The aggregate
  language-pack is REJECTED (vendors 306 grammars, licenses unauditable —
  R9).
- **One availability-aware `MultilangChunker` per T3 extension** that
  lazily imports tree-sitter and **falls back internally to the T2 text
  chunker** when the extra is absent (silent degrade + one structured log —
  the NullVectorStore precedent; also required because the chunker registry
  raises on duplicate extension registration, verified).
- Implementation rules from the probes: use `matches()` not `captures()`
  (capture-order misalignment); keep `QueryCursor` + `Tree` alive in locals
  (segfault otherwise); spans = `start_point.row+1..end_point.row+1`
  (verified correct on 25 Rust items); reuse compiled `Query` objects
  (~235 files/s measured — not a build bottleneck).
- Purity preserved: tree-sitter is a pure byte parse (verified under
  `HOME=/nonexistent`, empty PATH) — ADR 0014's project-index purity
  survives; static-only, no imports ever.
- Python stays on the stdlib-`ast` chunker (no python grammar wheel).

**Capability honesty (v1 matrix).**
- Any chunker's tree feeds `search_codebase` chunks, `get_symbol`
  (outline/spans), `get_context`, parent rollup — verified tree-driven.
- `module_members` (signatures) and the reference graph stay Python-only in
  v1; `ReferenceCaptureStage` already skips unregistered extensions
  silently.
- **Fix the honesty bug found**: `get_references` `meta.resolution` is
  hardcoded to Python capabilities — route it through
  `language_capabilities(ext)` for the target extension so non-Python
  targets never overstate capability (uses existing resolution vocabulary;
  no contract change).

**Index identity (the two correctness fixes the ablation depends on).**
- Fold the *effective extension scope* into `pipeline_hash` so
  multilang-on/off produce distinct chunk-cache identities (today it folds
  only embedder+backend+ingestion.yaml — verified gap; flipping scope must
  re-embed by design).
- Campaign index-cache canonical slugs gain the pipeline/scope identity
  (today they key on path only and `index_checkout` short-circuits on
  `db.exists()` — a multilang campaign would silently REUSE Python-only
  indexes, verified CRITICAL).
- Landing deadline restated: merged before the paid arc's first
  prebuild-index / write_lockfile (R5/R7).

**Eval-side (additive, no prereg touch — verified).**
- `gold_touches_non_python` stratum: computed from each trajectory's
  `facts.json` `gold_files` (already required, workspace-relative) —
  generic `--stratum-map` plumbing at the campaign CLI (strata are
  reporting dimensions; the frozen registration hash has no strata field).
- Build the overlay-name→serve-config resolver (currently a stub even for
  `suggestion_overlay`) so a future multilang on/off cell is expressible;
  no new campaign axis now.

## Blast radius (verified)
Descriptions-seed hash: disjoint domain, unaffected. Four extension-pinning
tests + default_config.yaml re-pinned with rationale (default widening).
Tool-count / config-shape / NodeKind tests: additive-safe. Coverage 90% /
complexipy 15 / mypy: tree-sitter ships py.typed (mypy OK); heavy imports
function-local per the [late-interaction] precedent.

## ADR mapping
ADR 0021 — one ADR covering T1–T3, capability matrix, index identity, and
the eval hooks, with the census tables as its evidence spine and the
contract-line escalation recorded under owner checkpoints.
