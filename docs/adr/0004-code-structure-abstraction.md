# ADR 0004 — Code-structure abstraction: LanguageAnalyzer seam + capability flags; syntactic Python references now, jedi designated later

- **Status:** Accepted
- **Date:** 2026-07-17

## Context

Phase 0 prepares pydocs-mcp to be the tool layer of a code-agent harness. The frozen
nine-tool surface (see `0002-tool-naming-and-parameter-contracts.md` for the freeze itself and
`0003-grep-glob-backend.md` for the three filesystem additions) includes
`get_references`, whose current description promises unhedged call-graph semantics —
"Who calls X", "usage sites", "ranked transitive blast radius"
(application/tool_docs.py:82-92) — with no qualifier that resolution is heuristic.
The Phase 0 spec's decision area D4 asks: what code-structure abstraction do we freeze so
that (i) the contract is honest about what the Python backend actually resolves today,
(ii) a better resolver can be swapped in later without changing tool names, parameters,
or output schemas, and (iii) a second language can be added additively?

This ADR renders decision D4 of the Phase 0 decision record. Evidence is from the
five-researcher pass at repo HEAD 261c933 (worktree
`.claude/worktrees/dreamy-joliot-f7830a`), including a live empirical probe against an
indexed 4-case tricky project.

## Evidence

### What parses code today

Three distinct parsing paths exist; none uses tree-sitter, jedi, libcst, or astroid
(grep over `python/`, `src/`, `pyproject.toml`: zero code imports; "pyright" appears only
in a docstring analogy at application/null_services.py:30).

1. **Member extraction — the misnamed regex path.** `AstMemberExtractor` does NOT use
   Python's `ast` module despite its name: it delegates to the Rust regex scanner
   `parse_py_file` (extraction/strategies/members/ast_extractor.py:159,170), which is
   "regex, not a full parser" — top-level defs/classes only, skips `_`-private names
   (src/lib.rs:203-207, 256-273). The pure-Python fallback is also regex
   (_fallback.py:115-116). `InspectMemberExtractor` live-imports dependencies only, never
   the project (inspect_extractor.py:42-47), falling back to the regex path on failure
   (inspect_extractor.py:71-78).
2. **Tree/chunk extraction — real CPython ast.** `AstPythonChunker` uses CPython's `ast`
   (extraction/strategies/chunkers/ast_python.py:21), keyed `.py` in `chunker_registry`
   (ast_python.py:73).
3. **Reference emitters — syntactic, emitter by emitter** (all in
   extraction/strategies/references.py, invoked by `ReferenceCaptureStage`, which calls
   `ast.parse` per `.py` file, extraction/pipeline/stages/reference_capture.py:120-122):
   - `capture_calls`: walks `ast.Call` nodes, emits the dotted spelling as written via
     `canonical_dotted` (references.py:106-137) — SYNTACTIC, name-as-written.
   - `capture_imports`: emits IMPORTS edges and builds a per-module alias table, but
     IGNORES `ast.ImportFrom.level`, so relative imports emit unqualified names
     (`from .mod import thing` → `to_name='mod.thing'`, references.py:170-184) —
     SYNTACTIC with import-statement awareness.
   - `capture_inherits`: per base expression (references.py:187-211) — SYNTACTIC.
   - `capture_mentions`: a backtick-dotted-name REGEX over markdown
     (references.py:42, 347-384) — fully fuzzy.
   - `capture_self_attribute_types`: infers `self.X` types from class-body annotations
     and `__init__` patterns (references.py:214-262) — a narrow slice of semantic
     inference.

   Resolution is a separate post-pass, `ReferenceResolver`
   (extraction/strategies/reference_resolver.py:34-233): alias rewrite (Rule A), exact
   qname match (Rule B), unique dotted-suffix match within `from_package` (Rule C,
   ambiguous → None under Rule D), self-type rewrite (Rule 0), resolving against a static
   qname universe from `document_trees` plus optional bundled stdlib qnames
   (application/indexing_service.py:487-506). There is NO scope model: no local-variable
   binding, no shadowing awareness, no re-export following. Class-ness of a qname segment
   is guessed by a PEP8 uppercase heuristic (reference_resolver.py:236-263). Net
   characterization: syntactic name-matching with two heuristic semantic patches
   (import aliases, self-attribute types).

### Graph schema and existing capability coverage

- `node_references`: columns from_package, from_node_id, to_name, to_node_id
  (NULL = unresolved), kind; PK (from_package, from_node_id, to_name, kind)
  (db.py:79-86, 129-131). `ReferenceKind` = calls|imports|inherits|mentions|similar|
  governs, stored as plain TEXT (extraction/reference_kind.py:23-29). Node identity is
  DOTTED QNAMES ONLY — edges carry no file path and no line numbers
  (storage/node_reference.py:16-31). File+line live one join away on tree nodes:
  `DocumentNode` has source_path, start_line, end_line
  (extraction/model/document_node.py:53-76).
- **Outline effectively already exists**: `document_trees` serialized via
  `DocumentNode.to_pageindex_json` (title, node_id, kind, source_path, spans, nesting;
  document_node.py:78-96) is what `get_symbol` renders (lookup_service.py:289-293).
- `module_members`: no line numbers, and (via the regex scanner) top-level symbols only,
  no methods (db.py:66-70; src/lib.rs:256-259).
- The DDL is language-neutral (all TEXT ids, kind as free text). Python-only assumptions
  live in code, not schema: dotted-qname identity (references.py:45-66), the PEP8
  uppercase heuristic (reference_resolver.py:236-263), Rule C's package-name==qname-prefix
  convention (reference_resolver.py:178-184), `walk_py_files` hardcoded to `.py`.
- The additive seam half-exists: `chunker_registry` is an extension-keyed dict populated
  by `@_register_chunker('.py'|'.md'|'.ipynb')` with import-time duplicate detection
  (extraction/serialization.py:36-70); `ChunkingStage` dispatches on extension
  (stages/chunking.py:55-58). The GAP: `ReferenceCaptureStage` hardcodes
  `path.endswith('.py')` / `.endswith('.md')` branches with inline logic
  (reference_capture.py:120,197) — reference emission is NOT registry-keyed.

### Empirical probe (4-case tricky project, indexed live)

Probe: an 11-module project (package `probepkg`) indexed with the worktree's own venv at
HEAD 261c933 via `pydocs-mcp index . --skip-deps --force` → "34 chunks, 15 symbols,
11 trees"; results read by driving `ReferenceService` directly against the probe DB
(reference_service.py:123-181). Probe sidecars were deleted afterwards.

**P0 finding first — project symbols are unreachable through target strings.**
`pydocs-mcp refs probepkg.mod.thing --direction callers` exits 1 with "no module matching
'probepkg.mod.thing' found under 'probepkg'"; the `__project__.`-prefixed form fails too.
Root cause: `LookupTarget.parse` takes parts[0] as the package, but project code is
stored under package `__project__` with prefixless module ids. The repo's own test
fixture admits it verbatim: "a __project__ symbol is unreachable through the target
string" (tests/test_cli.py:563-571). MCP `get_references` routes through the same parser
(tool_router.py:111-118), so the flagship references tool currently cannot answer
questions about the user's own project by symbol name.

**Case results:**

- **(a) Shadowing — FALSE POSITIVE plus a missing true edge.**
  `callers(probepkg.mod.thing)` returned `caller_shadow`'s CALLS edge (alias-table
  resolution) even though a local `def thing` shadows the import;
  `callers(probepkg.shadow.thing)` returned NOTHING — the true edge is absent.
- **(b) Re-export — FALSE NEGATIVE plus relative-import mangling.**
  `callees(use_reexport)` = CALLS `to_name='probepkg.thing'`, `to_node_id=None`;
  `callers(probepkg.mod.thing)` misses `use_reexport` entirely. Bonus gap: the package
  `__init__`'s relative import emitted IMPORTS `to_name='mod.thing'` UNRESOLVED, because
  `capture_imports` drops `ast.ImportFrom.level` (references.py:170-184).
- **(c) Annotated local — FALSE NEGATIVE but NO method conflation; self-attribute
  success.** `x: MyClass = MyClass(); x.run()` emitted CALLS `to_name='x.run'`,
  `to_node_id=None` (local annotations invisible); `callers(OtherClass.run)` = empty, so
  the two same-named `run` methods were NOT conflated (Rule D prefers silence over
  guessing). The self-attribute variant DID resolve: `Holder.go`'s `self.worker.run()` →
  `probepkg.classes.MyClass.run` (Rule 0 works). Constructor call `MyClass()` resolved to
  the class.
- **(d) Bare-name ambiguity — alias success, conservative silence, and a structurally
  dead rule.** With `from probepkg.dup_a import helper`, both call sites correctly
  resolved to `probepkg.dup_a.helper` (Rule A). A truly bare `helper()` (no import, two
  defs) stayed unresolved — correct conservatism. But a bare call to the UNIQUE
  `one_of_a_kind()` ALSO stayed unresolved: Rule C's candidate filter requires qnames
  prefixed by `from_package`, and `from_package='__project__'` never prefixes
  `probepkg.*` qnames (reference_resolver.py:178-184) — **Rule C is structurally dead
  for ALL project code.**

Summary: the graph is precision-biased (no method conflation; alias-aware imported-name
calls, inheritance, and self-attribute calls all resolve correctly); its misses are
recall failures, and every one is a scope/type-resolution failure, not a parsing failure.

### License audit (R6: MIT/Apache-2.0-compatible only)

Verified via PyPI JSON metadata (2026-07-17,
`curl https://pypi.org/pypi/<name>/json`) and upstream LICENSE fetches:

| Candidate | Version | License | R6 verdict |
|---|---|---|---|
| jedi | 0.20.0 | MIT (PyPI) | PASSES |
| tree-sitter (py bindings) | 0.26.0 | MIT (PyPI classifier) | PASSES |
| tree-sitter-python | 0.25.0 | MIT | PASSES |
| tree-sitter-language-pack | 1.12.5 | MIT; repo policy: permissive-only grammars, copyleft not accepted | PASSES — the licensing-safe multi-grammar bundle |
| tree-sitter-languages | 1.10.2 | Apache-2.0 wrapper; vendored grammar licenses not enumerated; dormant | CONDITIONAL — prefer the language-pack |
| LibCST | 1.8.6 | MIT (some files PSF-derived; permissive) | PASSES |
| pyright (PyPI wrapper) | 1.1.411 | MIT, but explicitly a wrapper requiring a Node.js runtime | license PASSES; **excluded operationally** |
| astroid | 4.1.2 | LGPL-2.1 (PyPI metadata empty; upstream LICENSE verified by fetch) | **FAILS R6 — excluded** |

Unverified items (kept labeled): per-grammar licenses inside the tree-sitter-languages
vendored bundle were not individually enumerated; the permissive-only claim for
tree-sitter-language-pack is the upstream repo's own policy statement, not a per-grammar
audit; the PyPI pyright wrapper's Node auto-install mechanism is prior knowledge, not
verified; jedi 0.20.0's maintenance status beyond its PyPI listing was not checked.

## Options considered

- **(a) LanguageAnalyzer Protocol + registry, tree-sitter uniform backend for all
  languages.** The seam is right (and half-built: chunker_registry is already
  extension-keyed with import-time conflict detection), but tree-sitter-everywhere is
  wrong for Phase 0: for Python it duplicates the CPython-ast chunker/emitters with
  strictly worse semantic fidelity, and it fixes ZERO of the four probe failures — all
  were resolution failures, not parsing failures. Non-Python implementations are out of
  scope now, so tree-sitter would ship as dead weight. Licensing is clean.
- **(b) Same seam, Python references backed by a semantic scope engine; tree-sitter
  reserved for outline/future languages.** The semantic-engine half is the only path
  that fixes the probe failures (shadowing, re-exports, annotated locals, receiver
  typing are all scope/type resolution — jedi's core competency, MIT). pyright's PyPI
  package is a Node.js wrapper — operationally disqualifying for a pip-installed local
  server. Weakness as stated: "tree-sitter for outline" is unnecessary for Python
  (document_trees already IS the outline, with spans), and whole-corpus index-time edge
  emission via a scope engine is expensive at dependency scale — the fast syntactic
  emitters should remain the index-time backbone.
- **(c) Hybrid per capability.** Best fit — the capability flags express it naturally.
  Outline: existing document_trees (no new dep). Definitions: existing qname universe +
  trees. References: the index-time syntactic graph as the always-available base
  (declared `references: syntactic`), a scope engine as the opt-in semantic upgrade
  (`references: semantic`), and `references: unavailable` for future outline-only
  languages. No graph-schema change needed (node_references is language-neutral TEXT).
- **(d) Extend what the repo already has.** Covers ~2.5 of 3 capabilities today
  (outline: done; definitions: done; references: real but syntactic). Cheap targeted
  fixes exist and are worth doing regardless of backend (ImportFrom.level; Rule C's
  `__project__` mismatch; the LookupTarget parser P0). But (d) alone cannot deliver
  `references: semantic` — shadowing + re-export chains + local-variable typing inside
  the name-matched resolver is re-implementing a scope engine with fewer tests — and (d)
  lacks the seam: reference capture is hardcoded `.py`/`.md` branches, not
  registry-keyed, so new languages are not additive today.

## Decision

Adopt option (c), structured as (d)+(b):

1. **Freeze the seam now.** A `LanguageAnalyzer` Protocol + extension-keyed registry,
   modeled on the existing `chunker_registry` pattern
   (extraction/serialization.py:36-70), covering reference capture — today a hardcoded
   `.py`/`.md` branch in `ReferenceCaptureStage` (reference_capture.py:120,197). Adding
   a language becomes additive registration; emission stays in the EXISTING
   `node_references` schema (its DDL is language-neutral TEXT; no schema change).
2. **Freeze the capability-flag vocabulary as contract output.** The vocabulary is
   exactly:

   `{outline, definitions, references} × {semantic | syntactic | unavailable}`

   Python declares: `outline: available` (document_trees IS the outline, with spans),
   `definitions: available`, `references: syntactic`. The flag surfaces in
   `get_references`' structured meta (`resolution: "syntactic"`) and in its re-hedged
   description (see `0002-tool-naming-and-parameter-contracts.md`, decision item 4).
3. **Python backend stays: CPython-ast emitters + ReferenceResolver as the index-time
   backbone, WITH three fixes** that are prerequisites for any references tool (all
   live-confirmed by the probe):
   - **P0:** `LookupTarget.parse` cannot address ANY project-source symbol — project
     code is stored under `__project__` but target strings treat parts[0] as the
     package; the repo's own fixture admits it (tests/test_cli.py:563-571). Fix: resolve
     bare project-qualified names under `__project__` (MCP and CLI both route through
     the same parser).
   - `capture_imports` ignores `ast.ImportFrom.level`, so every relative import emits an
     unresolvable name (references.py:170-184).
   - Resolver Rule C is structurally dead for project code
     (`from_package='__project__'` never prefixes project qnames,
     reference_resolver.py:178-184).
4. **jedi (MIT, license-verified) is the DESIGNATED semantic backend — not built in
   Phase 0.** The probe (shadowing false-positive, re-export false-negative,
   annotated-local false-negative, bare-name conservatism) shows exactly the failures a
   scope engine fixes — but jedi's own quality on those cases is UNVERIFIED (not
   installed, not tested); shipping it now would be an evidence-free decision. The
   frozen contract is invariant under the swap: when jedi lands (YAML opt-in), only the
   declared flag flips syntactic→semantic and edges improve; names, parameters, and
   output schemas are unchanged. The probe cases become jedi's acceptance tests.
5. **No tree-sitter in Phase 0.** It fixes none of the four probe failures (all are
   resolution failures; CPython ast already parses Python with full fidelity), and
   non-Python languages are out of scope. tree-sitter + tree-sitter-python +
   tree-sitter-language-pack are license-cleared (MIT / permissive-only, verified) as
   the pre-approved path when a second language lands. astroid is EXCLUDED (LGPL-2.1,
   fails R6). pyright-via-PyPI is excluded operationally (MIT but requires a Node.js
   runtime).

**The R5 argument, explicitly.** "Python fully supported" means every tool works on
Python code at its DECLARED capability level — the flags exist precisely so the contract
is honest about `references: syntactic`. The probe shows the syntactic graph is
precision-biased (no method conflation; alias-aware import calls, inheritance, and
self-attribute calls resolve correctly), and its misses are enumerated above rather than
hidden behind an unhedged description.

## Consequences

**Easier:**
- Adding a second language is additive registration behind the seam, with an honest
  per-language capability declaration (`references: unavailable` is a legal launch
  state) and no schema change.
- Swapping in jedi later is contract-invariant: a flag flip plus better edges, zero
  client migration.
- `get_references` becomes trustworthy for project code once the three fixes land — the
  P0 alone unblocks the harness's flagship structural tool.
- The probe project doubles as a ready-made acceptance suite for any semantic backend.

**Harder:**
- Two analyzer tiers (always-on syntactic, opt-in semantic) mean two code paths to test
  once jedi lands; the capability declaration must stay truthful per deployment.
- The hedged description trades perceived capability for honesty — agents reading
  `resolution: "syntactic"` must be taught what recall gaps to expect.
- The seam refactor touches `ReferenceCaptureStage`, a stage every index run exercises.

**We revisit:**
- jedi adoption, once its quality on the four probe cases is empirically verified
  (currently unverified — see Evidence) and its index-time cost on project-scale corpora
  is measured (estimated from its per-Script query model, not benchmarked).
- Per-call-site line numbers on edges (node_references carries none today; usage-site
  locations need a join to document_trees spans or a schema addition).
- tree-sitter-language-pack, when a second language actually lands.

## Action items

1. **The seam:** introduce the `LanguageAnalyzer` Protocol + extension-keyed analyzer
   registry over reference capture (lift the `.py` branch of `ReferenceCaptureStage._capture_all`
   behind it, mirroring chunker_registry), keeping the existing emitters as the Python
   registration.
2. **The flags:** per-language capability declarations using the frozen vocabulary
   `{outline, definitions, references} × {semantic|syntactic|unavailable}`; surface
   Python's `references: syntactic` in `get_references` structured meta
   (`meta.resolution`) and in the re-hedged TOOL_DOCS description (with
   `0002-tool-naming-and-parameter-contracts.md`).
3. **The three fixes:** (i) `LookupTarget.parse` `__project__` addressing so project
   symbols are reachable via target strings (P0; tests/test_cli.py:563-571 becomes a
   positive test); (ii) honor `ast.ImportFrom.level` in `capture_imports`
   (references.py:170-184); (iii) fix Rule C's project-code prefix mismatch
   (reference_resolver.py:178-184). Each ships with a regression test derived from the
   corresponding probe case.
