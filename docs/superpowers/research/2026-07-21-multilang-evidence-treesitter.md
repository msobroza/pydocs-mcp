# T3 tree-sitter dependency decision — hands-on evidence

Research subagent evidence record for the MULTILANGUAGE INDEXING feature, Tier 3
(tree-sitter grammar registration behind an optional extra). All claims below are
either backed by executed-command output (quoted), a fetched PyPI JSON field, or a
`file:line` citation. Anything not verified hands-on is labelled **UNVERIFIED**.

- **Date:** 2026-07-21
- **Scratch venv:** `/private/tmp/ts-probe` (Python 3.11.4, `arm64 Darwin`) — cloned base interpreter from the repo `.venv`.
- **Repo worktree:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/multilang-indexing` @ `aaed02e`.
- **Probe scripts:** `/private/tmp/probe.py`, `probe2–6.py`, `matchtest.py`, `final.py`. **No repo files modified.**

---

## HEADLINE FINDING — pin `tree-sitter>=0.25,<0.26` (avoid 0.26.0)

`tree-sitter` **0.26.0** has a **memory-safety regression** in the `QueryCursor.matches()`
path. The exact same script that runs cleanly on 0.25.x either returns **garbage node
spans** or **crashes the interpreter** (SIGBUS/SIGSEGV) on 0.26.0. **0.25.0 and 0.25.2 are
clean.** This is the single most important input to the pin decision.

Reproduced deterministically (5/5 runs each), same grammar wheels, same input file:

```
=== 0.26.0 x5 (correct output printed, THEN crash at teardown) ===
tree-sitter 0.26.0 | api=QueryCursor.matches | matches=24 | GARBAGE=0 | first3=[('safe_truncate',43,48),...]
  run1 EXIT=138          # 138 = 128+SIGBUS(10)
  run2 EXIT=138
  run3 EXIT=139          # 139 = 128+SIGSEGV(11)
  run4 EXIT=138
  run5 EXIT=138
=== 0.25.2 x5 (clean) ===
tree-sitter 0.25.2 | api=QueryCursor.matches | matches=24 | GARBAGE=0 | first3=[('safe_truncate',43,48),...]
  run1 EXIT=0 ... run5 EXIT=0
```

A second access pattern on 0.26.0 (binding cursor+tree to locals, reading `.start_point`
inside the loop) does not crash but returns **garbage row values** for matches after ~the
5th — e.g. `extract_module_doc` reported as `row 1073742078` (`0x3FFFFFFE`, a tree-sitter
invalid-node sentinel) in a 598-line file (`probe5.py` output). Either way, 0.26.0's
`matches()` is unsafe. The crash happens *after* correct results print, i.e. at
GC/teardown → classic use-after-free. Under pytest this would surface as a segfaulting
worker (nonzero exit), failing the strict CI gate.

> **Recommendation:** `tree-sitter>=0.25,<0.26`. Newest safe release is **0.25.2**.
> Revisit the ceiling only after a 0.26.x that fixes this (watch the py-tree-sitter
> changelog / issue tracker; UNVERIFIED whether a fix exists post-0.26.0 — 0.26.0 was
> the latest on PyPI at probe time).

---

## 1. The binding: `tree-sitter` (py-tree-sitter)

| Field | Value | Evidence |
|---|---|---|
| Latest PyPI version | **0.26.0** | `pip index versions tree-sitter` → "Available versions: 0.26.0, 0.25.2, 0.25.1, 0.25.0, 0.24.0, 0.23.2 …" |
| Recommended pin | **0.25.2** (`>=0.25,<0.26`) | Headline finding above |
| License | **MIT** | `metadata('tree-sitter')` → `Classifier: License :: OSI Approved :: MIT License` (the free-text `License`/`License-Expression` fields are blank; the classifier is authoritative) |
| Typing | **`py.typed` + `__init__.pyi` present** | `ls site-packages/tree_sitter` → `__init__.pyi`, `py.typed` |
| macOS arm64 wheel | **yes** | Downloaded `tree_sitter-0.26.0-cp311-cp311-macosx_11_0_arm64.whl` (140 KB) |
| linux x86_64 wheel | **yes** | `pip download --platform manylinux2014_x86_64` → `Saved tree_sitter-0.25.2-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl` |
| Wheel ABI | **per-Python** (`cp311-cp311`, NOT abi3) | filename above → one core wheel per CPython minor |
| Installed size | 340 KB | `du -sh site-packages/tree_sitter` |

**API shape (0.25/0.26), verified in `probe.py`/`final.py`:**

- Build a language: `Language(tree_sitter_rust.language())` → `<class 'tree_sitter.Language'>`, `Language.abi_version == 15`.
- Parser: `Parser(lang)` (language passed **positionally** to the constructor), then `parser.parse(src_bytes) -> Tree`.
- Query: `Query(lang, query_src)` then `QueryCursor(query).matches(root)` / `.captures(root)`. Both `Query` and `QueryCursor` are top-level (`"QueryCursor" in dir(tree_sitter) == True`).

**Node spans give BOTH byte offsets AND row/col — exactly what the product chunk model
needs.** From `probe.py`:

```
type: line_comment
start_byte: 0 end_byte: 13
start_point: <Point row=0, column=0> end_point: <Point row=0, column=13>
```

`node.start_point.row` / `.column` are **0-indexed**; the product's chunk model uses
**1-indexed line spans**, so the mapping is `start_line = node.start_point.row + 1`,
`end_line = node.end_point.row + 1`. Verified end-to-end on `src/lib.rs` (`final.py`,
tree-sitter 0.25.2):

```
function_item  safe_truncate          L43-48
function_item  walk_py_files_impl     L109-154
function_item  hash_files             L177-201
struct_item    ParsedMember           L215-224
... 25 items, all spans monotonic & in-range (file is 598 lines)
```

`.start_point`/`.end_point` are `Point` objects with `.row`/`.column` attributes (also
tuple-indexable `[0]`/`[1]`), so both access styles work.

---

## 2. Grammar distribution — route (a) individual wheels vs (b) aggregate pack

### Route (a): individual official grammar wheels — RECOMMENDED for the MIT/Apache constraint

All four in-scope grammars are **MIT**, ship **`py.typed` + `.pyi`**, and are **abi3**
(one wheel per platform, works across CPython 3.10+ — decouples grammar wheels from the
per-Python core wheel):

| Grammar | Version | License | Requires-Python | Wheels verified | Size |
|---|---|---|---|---|---|
| `tree-sitter-python` | 0.25.0 | MIT | (none) | macos arm64 `cp310-abi3`; linux `cp310-abi3-manylinux…x86_64` | 512 KB |
| `tree-sitter-rust` | 0.24.2 | MIT | (none) | macos arm64 `cp39-abi3`; linux `cp39-abi3-manylinux…x86_64` | 1.2 MB |
| `tree-sitter-javascript` | 0.25.0 | MIT | (none) | macos arm64 `cp310-abi3`; linux `cp310-abi3-manylinux…x86_64` | 500 KB |
| `tree-sitter-c` | 0.24.2 | MIT | >=3.10 | macos arm64/x86 `cp310-abi3`; manylinux x86_64 **+ aarch64**; musllinux x86_64 + aarch64 | (not installed) |

Evidence: `pip show` License field (MIT for python/rust/js), PyPI JSON `info.license` for
`tree-sitter-c` (`MIT`), `pip download --platform manylinux2014_x86_64` saved a linux
wheel for each, `du -sh` for sizes. Grammars declare only a *soft optional* core
constraint via a `core` extra: `tree_sitter_python` → `['tree-sitter~=0.24; extra=="core"']`,
`tree_sitter_rust` → `~=0.22`, `tree_sitter_javascript` → `~=0.24`. **They bundle their own
C parser and do not hard-pin core** — the core version is our choice (subject to the ABI
constraint in §4).

**Maintenance:** the individual grammars live under the `tree-sitter/` GitHub org
(`Home-page: https://github.com/tree-sitter/tree-sitter-python`, etc.) — the canonical,
actively-maintained source.

### Route (b): aggregate packs — one alive, one dead

| Pack | Latest | Last upload | License | Verdict |
|---|---|---|---|---|
| `tree-sitter-languages` | 1.10.2 | **2024-02-04** | Apache 2.0 (`info.license`) | **ABANDONED** — >2 yr stale; old core ABI |
| `tree-sitter-language-pack` | **1.13.2** | **2026-07-20** (day before probe) | **MIT** (`license_expression`) | **ALIVE** — 62 releases, 306 grammars, `requires_python>=3.10` |

Evidence: PyPI JSON for both (fetched in `probe`). `tree-sitter-languages` summary:
"Binary Python wheels for all tree sitter languages"; `tree-sitter-language-pack` summary:
"Pre-compiled tree-sitter grammars for 306 programming languages".

**R9 MIT/Apache licensing caveat (important):** `tree-sitter-language-pack`'s *own package*
is MIT, but it **vendors 306 third-party grammars, each carrying its own license**. The
PyPI metadata exposes **no per-grammar license classifiers** (`license classifiers: []`),
so the bundled-grammar licenses are **not auditable from package metadata** and some are
**UNVERIFIED** (community grammars are not uniformly MIT/Apache). For a hard MIT/Apache-only
constraint, **route (a) is the auditable choice**: each official grammar wheel is a
separate dependency with its own verifiable MIT license, and we add exactly the languages
we ship (per-language cost = one MIT wheel). The aggregate pack trades that auditability
for one-line breadth.

> **Recommendation:** ship **individual official grammar wheels** under the `[multilang]`
> extra (start with python/rust/javascript/c; add per language on demand). Do **not** adopt
> `tree-sitter-language-pack` unless/until its bundled-grammar licenses are individually
> audited against R9, and never adopt the dead `tree-sitter-languages`.

---

## 3. Hands-on probe — parse real Rust + JS, extract items, time it, prove purity

**Rust (`src/lib.rs` → `/private/tmp/lib.rs`, 598 lines / 23.4 KB) on tree-sitter 0.25.2**
— query `(function_item name:(identifier) @name) @item` + `(struct_item …)`, extracted 25
top-level items with correct 1-indexed spans (full list in `final.py` output; sample in §1).

**JavaScript (`/private/tmp/sample.js`, 9 lines):**
```
lexical_declaration    x        L1-1
function_declaration   greet    L2-4
class_declaration      Widget   L5-8
lexical_declaration    helper   L9-9
```

**Timing (parse + query, tree-sitter 0.25.2):**
```
TIMING parse+query 23.4KB x200: 235 files/sec, 5505 KB/s     (final.py, includes Query compile per call)
parsed 23.4KB rust file 200x in 0.660s = 303 files/sec, 7093 KB/s   (probe.py, parse-only)
```
23.4 KB is a *large* single file; typical repo files are far smaller and parse
proportionally faster. At ~5.5 MB/s a several-thousand-file repo parses in low single-digit
seconds — **not a bottleneck for index build**. (Query objects can be compiled once and
reused across files to beat the 235/s figure, which recompiles per call.)

**Purity — no env / no filesystem / no network (`final.py`):** parsing operates on an
in-memory `bytes` buffer. Ran a parse with a poisoned environment:
```
PURITY: parsed in-memory buffer w/ HOME=/nonexistent PATH='' -> (source_file (function_item name: (identifier) par…
```
The parser produced a correct tree with `HOME=/nonexistent` and `PATH=''`. tree-sitter is a
pure C parser over the byte buffer we hand it — it reads no env vars, opens no files, makes
no network calls. **This satisfies ADR 0014's "the PROJECT index is a pure function of the
repo" finding** (`docs/adr/0014-…:52`) — T3 does not threaten purity: identical file bytes
→ identical parse tree, independent of machine/env.

### Query-API gotcha to encode in the T3 chunker (subtle, bit me during the probe)

Two query APIs, two different correct-usage rules — get this wrong and you silently emit
wrong line spans:

- **`QueryCursor.matches(root)`** returns `[(pattern_index, {capname: [Node,…]}), …]`.
  Captures **within one match are correctly paired** (the `@name` belongs to the same
  `function_item` as the `@item`). **But you MUST keep the `QueryCursor` (and `Tree`) bound
  to a live local** across the whole iteration. A temporary — `QueryCursor(q).matches(root)`
  inline — gets GC'd mid-iteration and **segfaults** (`probe4.py` → EXIT 139) or yields
  garbage; binding it (`cur = QueryCursor(q); ms = cur.matches(root)`) is clean
  (`probe5.py`/`final.py`, 0 garbage). *(On 0.25.x. On 0.26.0 even the bound form is unsafe
  — see headline.)*
- **`QueryCursor.captures(root)`** returns `{capname: [Node,…]}` where **each capture
  name's node list is in independent document order**. You **cannot** `zip(d["item"],
  d["name"])` — they misalign (verified in `probe6.py`: `parse_py_file_finds_class…`
  paired with the span of `scan_matching_paren`). Individual spans are correct, but
  name↔item pairing is lost. Use `matches()` when you need paired captures.

> T3 chunker MUST use `matches()` (paired), bind the cursor to a local, and pin
> `tree-sitter<0.26`.

---

## 4. API-stability / ABI history and pin strategy

Verified hands-on:

- **Grammar ABI 15 requires core ≥ 0.25.** Installing `tree-sitter==0.24.0` (and `0.23.2`)
  against the current grammar wheels raises at parse time:
  `ValueError: Incompatible Language version 15. Must be between 13 and 14`
  (`matchtest.py` output). So the *floor* is **0.25** — we cannot pin lower while using
  current grammar wheels.
- **0.25 introduced `QueryCursor` + `Query(Language, str)`.** `api=QueryCursor.matches`
  resolves on 0.25.0/0.25.2/0.26.0 (`matchtest.py`).
- **0.26.0 is the ceiling to avoid** (headline memory-safety regression).

Constructor/API churn direction across the 0.21→0.25 line (0.24→0.26 boundaries verified
above; the 0.21/0.22 details are from the py-tree-sitter changelog and **UNVERIFIED**
hands-on here):
- 0.21: `Language(lib_path, name)` loading a compiled `.so`; `parser.set_language(lang)`.
- 0.22: grammars expose `.language()` returning a PyCapsule; `Language(capsule)`;
  `Parser(lang)` accepted — this is the shape the current grammar wheels target.
- 0.23–0.24: `Query` via `Language.query(str)`; `Node.sexp()`.
- 0.25: `Query(Language, str)` constructor + `QueryCursor` object; `matches()`/`captures()`
  move onto `QueryCursor`.

> **Pin strategy:** `tree-sitter>=0.25,<0.26`, and pin each grammar to a known-good version
> (`tree-sitter-python==0.25.0`, `tree-sitter-rust==0.24.2`, `tree-sitter-javascript==0.25.0`,
> `tree-sitter-c==0.24.2`) or a tight compatible range. Because grammars are abi3 they don't
> churn with the core wheel; because core is per-Python (`cp311`), the wheel-availability
> risk is on the core dep — mitigated by the manylinux/macos wheels confirmed in §1. A
> parity-style test that parses a fixture and asserts `matches()` returns non-garbage,
> in-range spans + exit-0 would catch a future bad core release at CI time (mirrors the
> existing embedder-provider parity test discipline).

---

## 5. Typing / mypy (strict gate)

**All packages ship `py.typed` + `__init__.pyi`** (`ls` of each site-packages dir):
`tree_sitter`, `tree_sitter_python`, `tree_sitter_rust`, `tree_sitter_javascript` each have
both files; `tree-sitter-c` declares `requires_python>=3.10` and ships wheels with the same
layout. So the strict mypy gate has first-party stubs — **no `types-*` stub packages
needed**. Precedent for the config side: the repo already lists optional-extra native deps
under `[[tool.mypy.overrides]] module = ["fast_plaid.*", "fastembed.*", …]`
(`pyproject.toml:294`); if any tree-sitter stub proves incomplete under `--strict`, the
same `ignore_missing_imports`/override seam is the sanctioned escape hatch — but the
`py.typed` markers suggest it won't be necessary for the core surface.

---

## 6. The fallback question — what registered T3 extensions do when `[multilang]` is absent

### How extension→chunker dispatch actually works (verified in-repo)

- Discovery yields files whose extension is in `scope.include_extensions`
  (`extraction/strategies/discovery/project.py:86`, `dependency.py:122`). **T1 widening
  `include_extensions` is what makes `.rs`/`.js` files reach the pipeline at all.**
- `ChunkingStage` dispatches per file: `ext = Path(path).suffix.lower();
  chunker_cls = chunker_registry.get(ext); if chunker_cls is None: return None`
  — **"unknown extension — skip silently (policy, not error)"**
  (`extraction/pipeline/stages/chunking.py:55-58`).
  → **Consequence:** if T1 widens extensions but NO chunker is registered for `.rs`, the
  file is discovered and then **silently produces zero chunks** — indexed-into-nothing.
  T2/T3 exist precisely to fill this gap.
- Registration is `@_register_chunker(ext)`, and **duplicate registration for the same
  extension RAISES `ValueError("chunker for '.rs' already registered")`**
  (`extraction/serialization.py:64-66`). The registry is **NOT last-write-wins** — so
  **T2 and T3 cannot both statically register `.rs`.** This is the key constraint shaping
  the answer.

### In-repo graceful-degradation precedents

- **Silent no-op (advisory capability):** `NullVectorStore` — "vectors are advisory;
  missing them shouldn't break indexing" (CLAUDE.md §Null Object). Used when dense
  embeddings aren't indexed.
- **Loud raise (user-requested query):** `NullTreeService`/`NullReferenceService` raise
  `ServiceUnavailableError` with a YAML-anchored actionable message
  (`application/null_services.py:87,110,113,116`) — because an empty result to a
  user's `get_references` call would *mislead*.
- **Lazy-import-or-raise (optional native extra):** `fast_plaid_uow.py:87-94` lazily imports
  the `[late-interaction]` extra and `raise ImportError(_INSTALL_HINT)` when absent. The
  `[late-interaction]` extra itself is declared at `pyproject.toml:84`
  (`pylate`, `fast-plaid`, `transformers`) and wired in `storage/search_backend.py`
  (fast-plaid child store only built when `late_interaction.enabled`,
  `search_backend.py:210,257`).

### Recommendation — a single availability-aware chunker per extension, degrading to text (not raising)

Indexing is a **background batch build**, not a user-issued query. Aborting the whole index
because an *optional enhancement* (structural parsing) is unavailable would be the wrong
precedent (`fast_plaid`'s raise is right for a query path, wrong for batch indexing). The
matching precedent is **`NullVectorStore` — degrade silently but keep indexing**. Combined
with the **duplicate-registration-raises** constraint, the clean design is:

> **Register ONE `MultilangChunker` for each T3 extension (`.rs`, `.js`, …). It lazily
> imports `tree_sitter` + the grammar (fast_plaid-style lazy import). If the `[multilang]`
> extra is present → structural symbol extraction via `matches()`. If absent → it falls
> back internally to the **T2 language-agnostic text chunker**, so the file is still
> indexed as searchable text (BM25/dense), just without structural symbols.**

Why this shape and not the alternatives:

- **One registry entry per ext** sidesteps the `ValueError`-on-duplicate constraint — T2
  and T3 never both claim `.rs`. The fallback is *inside* the chunker, not a second
  registration.
- **Degrade, don't skip:** returning `None` (today's unknown-extension behavior) would
  leave widened files un-indexed — worse than text chunks. T2 text output is strictly
  better than nothing for retrieval.
- **Degrade, don't raise:** matches the advisory-capability precedent; a batch index build
  must not abort over an optional enhancement.
- **Emit one structured JSON log per index build** when falling back
  (`{"event":"multilang_fallback","reason":"tree_sitter_unavailable","extensions":[".rs",".js"],"hint":"pip install 'pydocs-mcp[multilang]'"}`),
  reusing `fast_plaid`'s `_INSTALL_HINT` message-quality bar. This preserves the operator's
  ability to understand why symbols are missing without breaking the run — the sanctioned
  "degrade gracefully at a boundary + structured logs" convention.
- **Lazy import inside the chunker** keeps `import pydocs_mcp` free of tree-sitter (same
  discipline as the langgraph/streamlit lazy imports), so the default install stays lean and
  the strict-mypy typecheck job (which won't have the extra installed) is unaffected — mirror
  the `[[tool.mypy.overrides]]` seam at `pyproject.toml:294` if needed.

**Open design point for the plan:** whether T2's text chunker is a standalone chunker
registered for a broad set of "plain text/config" extensions AND reused as T3's internal
fallback (recommended — single source of the text-chunking logic), vs. duplicated. The
duplicate-registration constraint means T2 and T3 must partition extensions at the registry
level; the `MultilangChunker`-with-internal-fallback design makes T3's extensions owned by
one entry that *delegates* to the shared T2 text logic when tree-sitter is absent.

---

## Appendix — commands & artifacts

- Install: `pip install tree-sitter tree-sitter-python tree-sitter-rust tree-sitter-javascript`
  → `tree-sitter-0.26.0 tree-sitter-javascript-0.25.0 tree-sitter-python-0.25.0 tree-sitter-rust-0.24.2`.
- Version matrix probe: `/private/tmp/matchtest.py` (garbage/crash detector across core versions).
- Clean reference run: `/private/tmp/final.py` on `tree-sitter==0.25.2` (EXIT=0).
- Inputs: `/private/tmp/lib.rs` (copy of repo `src/lib.rs`, 598 lines), `/private/tmp/sample.js`.
- No repo files were modified; nothing committed.
