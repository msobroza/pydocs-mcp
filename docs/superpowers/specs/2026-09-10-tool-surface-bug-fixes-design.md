# Tool-surface bug batch — implementation design

**Status:** design final, ready to implement (critique round applied — see §11).
**Branch:** `fix/tool-surface-bugs`, based on `origin/main` `5461d8e`.
**Scope:** the five owner bugs reproduced on 2026-09-10 against pydocs-mcp 0.6.1 (the
tool-surface files are byte-identical to `origin/main`), plus two cheap extras.
**Line references:** every `path:line` below points into this tree at `5461d8e` and was
re-read while writing this document.
**Bundle facts:** facts marked *(verified)* were queried read-only against
`~/pydocs-openrouter/index/*.db`, an index of `example_needle`.

## Owner decisions

**None required.** Every fix below fits inside the frozen nine-tool contract as written.
No `docs/tool-contracts.md` text changes, and there is no new tool, parameter or envelope
field.

Two *optional* contract-amendment proposals are drafted in §9 (P1: filling source from
disk for `get_symbol(depth="source")`; P2: a clarifying sentence for §3.7). Neither ships
in this PR, and nothing in this PR depends on either one.

## Constraints (apply to every section)

- The nine-tool surface is frozen (`docs/tool-contracts.md` §1, `:14-44`). Behaviour,
  error text, rendering and tool descriptions may change.
- Contract-text changes go through the ADR 0007 process
  (`docs/adr/0007-deterministic-routing-suggestions.md:135-139`, `:204-207`), which means
  owner ratification.
- Tunables live in YAML only (`AppConfig`), and each default has a single source.
- New code goes in `application/`. Functions are 4–20 lines. Logs are structured JSON.
  Error messages carry the offending value and the expected shape.
- Contract §2's "byte-identical to the 0.5.x output" sentence (`:57-60`, §6 row 2) is
  scoped to the 0.5.x → 0.6.0 boundary, not a permanent rendering freeze. §1 (`:24-44`)
  freezes names, schemas, the envelope, `items[]` field sets and vocabularies — not text
  rendering. The rendering fixes below therefore change no contract sentence.

---

## 0. Verified facts

| # | Fact | Evidence |
|---|---|---|
| F1 | A module-only target is routed to `_module_lookup` whatever the `show` value is. The single-segment project fallback does the same. | `lookup_service.py:400-402`, `:427-429`, `:432-441` |
| F2 | The reference-graph dispatch (`_REF_GETTERS`, `impact`, the inherits guard) only runs inside `_symbol_lookup`. | `lookup_service.py:443-530` |
| F3 | On MCP, outline rows fail `ReferencesEnvelope` validation and come back as `ServiceUnavailableError`. The CLI does no validation, so it prints the outline and exits 0. | `server.py:606-642`; `tool_router.py:184-205` |
| F4 | A module root's `node_id` and `qualified_name` are both the module id, which is also the key used in `node_references.from_node_id`. | `extraction/model/document_node.py:103-114`; bundle |
| F5 *(verified)* | Of the 465 resolved IMPORTS edges, 2 target a module id and 282 target a direct child of a module. None target a module-prefixed id that is not a node. The largest module has 28 direct children; the median is 4. Of the 15 GOVERNS edges, 12 target module ids. | scratch `toolbugs/imp_cov.py` |
| F6 | `depth="source"` renders the chunk for the exact `qualified_name`. For a CLASS that chunk runs from the class line to the line before the first method. For a MODULE it is the docstring. Methods and functions are the full `def` slice. The item span still covers the whole node, and `truncated` is only set by the line cap. | `symbol_source.py:96-125`; `ast_python.py:146-157`, `:339-341`, `:415-445` |
| F7 *(verified)* | Tree nodes store raw source lines: for all 85 classes and all 113 modules, each indexed line matches the file on disk at the same line number. Stitching indexed text still leaves gaps (decorators, comments, blank lines, class attributes, module statements that are not defs): 308 lines across the classes and 2,452 across the modules. | scratch `toolbugs/guard_check.py` |
| F8 | grep's `glob` is matched against the full root-relative path, anchored `^…$`, and `*` never crosses `/`. `path=` only filters by prefix. | `file_tools.py:117-140`, `:199-213` |
| F9 | The glob tool matches relative to `path=`, and tests pin that behaviour. | `file_tools.py:410-422`, `:565-571`; `tests/application/test_file_tools.py:472-476`, `:501-505` |
| F10 | `_POINTER_WITH_EOL_RE` (`resolve_pointers` suppression) and `strip_pointers` both remove `token + "\n?"`. The overview renderers put the token inline at the end of the line, so removing it also removes the bullet's line break. The workspace card does the same inline. | `formatting.py:96-100`, `:204-219`, `:969-1008`, `:1128` |
| F11 | Three tests pin the merged-line output: `test_next_pointers.py:226`, `test_pointer_target_validity.py:81-85`, and the `get_overview` golden at `test_structured_envelope.py:40` (`"- \`pydocs-mcp\` (script) - \`harness-ask-your-docs\` (script) \n"`). | tests |
| F12 | Every token emitted today is either on its own line at column 0 or inline after a space. No renderer or test uses an indented own-line token. | `git grep pointer_token(` over `python/`; `git grep` over tests |
| F13 | Module-map pointers render `get_context(targets=[module])`, and `_resolve_context_target` rejects module targets. | `formatting.py:121`, `:969-977`; `lookup_service.py:670-672` |
| F14 | A script entry point keeps only the script *name*; the `module:attr` value is dropped. Dependency-profile pointers are not checked against the indexed packages. Tree nodes exist only for `FunctionDef`, `ClassDef` and import blocks, so something like `app = typer.Typer()` is not a node. | `overview_service.py:290-310`, `:395-406`; `ast_python.py:192-205`, `:415-433` |
| F15 | The inherits error uses the internal `show` vocabulary: `show='inherits' only applies to CLASS nodes, got …`. | `lookup_service.py:506-509` |
| F16 | The `lookup` help text advertises `__project__.<module>.<symbol>`, which does not resolve. The contract addresses project code by its bare dotted name. | `__main__.py:545`, `:553`; `tool-contracts.md:178-187` |
| F17 | CLI and MCP share one composition root (`_build_cli_services` → `server.build_routers` → `storage/factories.py`), so a fix in a service or factory reaches both surfaces. | `__main__.py:940-966`; `factories.py:133-212` |
| F18 | `ReferenceService.callers` opens its own UoW for each call (`reference_service.py:162-167`). `impact` runs one recursive walk plus a `node_scores` read per call and ranks by `(hop, -pagerank, -in_degree, qname)` (`:288-325`). | code |
| F19 | No committed JSON/lock/YAML/TOML file contains an `artifact_hash`, so no seed-anchoring or campaign lockfile in the repo is invalidated by a description edit. | `git grep -l artifact_hash -- '*.json' '*.lock' '*.yaml' '*.toml'` returns nothing |
| F20 | The symbol-resolve branch is at HEAD `8d2f3dc7` (4 commits) with uncommitted edits. Its hunks are listed in §8. | `git diff origin/main` in that worktree |

---

## 1. Bug 1: `get_references` on a module target

### Root cause

`LookupService.lookup_with_items` step 3 (`lookup_service.py:400-402`) sends every target
that has no symbol path to `_module_lookup` (`:432-441`). That method returns the PageIndex
JSON plus the §3.3 outline rows for every `show` value, and the `_package_overview`
project fallback (`:427-429`) does the same. On MCP, those rows are then validated against
`ReferencesEnvelope`, fail, and `server.py:629` re-raises the failure as
`ServiceUnavailableError("get_references failed: …")`. The CLI's `refs` prints the JSON and
exits 0.

A second hole: a single-segment *dependency-package* target returns `format_package_doc`
with empty `items` for every direction (`:424-426`).

### Chosen fix: a module target answers its import graph

The graph already holds real module-level edges. Every IMPORTS edge starts at a module id,
and 12 of the 15 GOVERNS edges target module ids (F5). The module root works as the
reference node (F4).

| direction | Meaning for a module | Mechanism |
|---|---|---|
| `callers` | **Importers.** All edges into the module id (`import M`, GOVERNS), plus IMPORTS edges into its direct class/function children (`from M import X`). Deduplicated on `(from_node_id, to_node_id, kind)`, module-id rows first, then children in source order. CALLS edges into children are excluded; `get_references` on the member answers those. | `ref_svc.callers(package, id)` per seed |
| `callees` | What the module imports: edges out of the module id. | `_symbol_lookup(package, module, module, …)` (the root resolves by qualified name, F4) |
| `governed_by` | GOVERNS edges into the module id. | same |
| `impact` | Blast radius: transitive callers of the module **and** its direct class/function children, **excluding the module's own internals** (see the merge rule below). | `cross_navigator.impact` per seed |
| `inherits` | `InvalidArgumentError`: a module is not a class. | new wording shared with E1 |
| `context` (only reachable through the deprecated `lookup --show context`) | `InvalidArgumentError`. `get_context` stays symbol-only, which the symbol-resolve branch's K5 relies on. | new |

The asymmetry is intentional and documented in the description clause (§6): `callers`
answers "who imports this module", while `impact` answers "what breaks if I change it",
which includes external callers of its members.

**Seed set and cap.** `seeds = (module id,) + direct children of kind class/function`, in
source order. `import_block` and `code_example` children are skipped. The set is capped at
`reference_graph.impact.max_module_seeds`. This is a new pydantic field on `ImpactConfig`
(`retrieval/config/models.py:190-202`) with `ge=1, le=256`, and its single-source default
is `_DEFAULT_MAX_MODULE_SEEDS = 32`, declared next to `_DEFAULT_IMPACT_MAX_DEPTH`
(`:186-187`). The YAML key goes in `default_config.yaml` after `:109`. A cap of 32 covers
every module in the repro bundle (max 29 seeds). Dependency modules can have hundreds of
children, which is why the cap exists. When the cap applies:

- Record a `TruncationEntry(description=f"{n} of {total} module members not searched —
  raise reference_graph.impact.max_module_seeds", recovery="")`. This sets
  `meta.truncated=true`, which is correct: a limit cut the output.
- Log one JSON line: `{"event": "module_target_seed_cap", "module": …, "seeds": cap, "members": total}`.

**Impact merge (exact).** `internal = {module id} ∪ every qualified name in the module tree`.
- Call `cross_navigator.impact` once per seed with `limit_seed = limit + len(internal)`.
- Merge by the identity the cross navigator uses (qualified name plus the cross-repo
  project qualifier when present), keeping the row from the seed with the minimum hop.
  Ties go to the first seed.
- Drop every row whose qualified name is in `internal` (this covers the seeds), and any
  row starting with `module + "."`.
- Re-sort with the service key `(hop, -pagerank, -in_degree, qname)`, then slice to
  `limit`.

Why this is exact: take any external row X in the global top-`limit`, and let s be the
seed where X has its minimum hop. Every row ranked above X in s's list also ranks above X
globally, because a minimum-hop key can only improve. So at most `limit-1` external rows
and at most `len(internal)` internal rows sit above X in s's list, and X falls inside s's
top-`limit_seed`. This assumes `pagerank`/`in_degree` are per-node, not per-walk; AC1.3
pins it with a brute-force fixture.

**Single-segment dependency package.**
- `show` in `{"default", "tree"}`: the package doc, as today.
- Graph directions: route to the package's same-named top module when it exists
  (`_longest_indexed_module(package, [package])`).
- Otherwise raise `InvalidArgumentError(f"direction {show!r} needs a module or symbol
  target; {package!r} is an indexed package with no top-level module — name a module,
  e.g. '{package}.<module>'")`.

**Alternatives rejected.**
- *Reject every module target*: it would hide 12 of the 15 GOVERNS edges and all 719
  IMPORTS edges.
- *Exact-id callers only*: `callers(needle.retrieval.data)` would return one GOVERNS row,
  which reads as "nobody imports this".
- *A batched `ReferenceStore.find_callers_many`*: this would touch `storage/protocols.py`
  and `tests/_fakes.py`, which are in flight on the symbol-resolve branch. The cap already
  bounds the fan-out.

### Placement

- **New `python/pydocs_mcp/application/module_references.py`** (~110 lines). It holds pure
  helpers plus the per-seed fan-out, so `lookup_service.py` (742 lines) grows by only
  ~25 net lines.
  - `module_seed_ids(root: DocumentNode, cap: int) -> tuple[tuple[str, ...], int]`
    returns the seeds and the uncapped member count.
  - `module_internal_qnames(root: DocumentNode) -> frozenset[str]`
  - `async def module_importer_rows(ref_svc: ReferenceNavigator, package: str, seeds: Sequence[str]) -> tuple[NodeReference | CrossReferenceRow, ...]`
  - `async def module_impact_rows(navigator: CrossNavigator, package: str, seeds: Sequence[str], internal: frozenset[str], *, max_depth: int, limit: int) -> tuple[ImpactNode, ...]`
  - `merge_impact(per_seed: Sequence[tuple[ImpactNode, ...]], internal: frozenset[str], module: str, limit: int) -> tuple[ImpactNode, ...]`
  - `reject_module_show(target: str, show: str) -> None` raises for `inherits`/`context`.
  - `INHERITS_NEEDS_CLASS = "direction 'inherits' applies only to class targets; {target!r} is a {kind}. Directions that accept it: callers, callees, impact, governed_by."`
  - `CONTEXT_NEEDS_SYMBOL = "show 'context' needs a symbol target; {target!r} is a module — use get_symbol(target={target!r}, depth=\"tree\") for its outline."`
- **`lookup_service.py`:**
  - `:398` → `return await self._package_overview(parsed.package, payload.show, payload.limit)`
  - `:401-402` → `return await self._module_target(parsed.package, parsed.module, payload.show, payload.limit)`
  - `_package_overview` (`:411-430`) takes `show`/`limit`. Tree shows get the package doc;
    graph directions get the top module or `InvalidArgumentError`. The project fallback at
    `:428-429` calls `_module_target`.
  - New `_module_target`: tree shows → `_module_lookup` (bytes unchanged); otherwise
    `reject_module_show`; then `callers` → `_module_callers`, `impact` → `_module_impact`,
    and `callees`/`governed_by` → `_symbol_lookup(package, module, module, show, limit)`.
    Place it after `_symbol_lookup`.
  - Extract `:516-530` (cap, decision titles, `format_references`, items) into
    `_render_reference_rows(target, show, rows, limit, extras)`, shared by `_symbol_lookup`
    and `_module_callers`. Module impact reuses the existing `format_impact` render that
    follows `:480-490`.
  - New field `module_seed_cap: int = _DEFAULT_MAX_MODULE_SEEDS`, placed directly after
    `impact_max_depth` (`:346`). Add the constant to the import list at `:58`.
  - Rewrite the stale comments at `:436-440` and `:465-469`, and update the docstring at
    `:367-374`.
  - Log one JSON line per call:
    `{"event": "module_target_references", "module": …, "direction": …, "seeds": n, "rows": m}`.
- **`storage/factories.py`:** add `module_seed_cap=impact_cfg.max_module_seeds,` after
  `:173` (`impact_max_depth=…`).
- `meta.resolution` is unchanged: `TARGET_EXTENSION_EXTRA` comes from `root.source_path`,
  so `.py`/`.md` map to `"syntactic"` and everything else maps to `"unavailable"` with
  empty edges (ADR 0021 Decision 6).

### User-visible before → after

| Call | Before | After |
|---|---|---|
| MCP `get_references(target="needle.retrieval.data", direction="callers")` | `isError`: "get_references failed: 27 validation errors for ReferencesEnvelope …" | `# Callers of \`needle.retrieval.data\``, 23 IMPORTS rows from 13 modules + 1 GOVERNS row (capped by `limit`); valid §3.5 `items[]`; `meta.resolution="syntactic"` |
| `…direction="callees"` | same error | the module's 13 import rows |
| `…direction="governed_by"` | same error | GOVERNS rows into the module |
| `…direction="impact"` | same error | external transitive callers of the module and its members; module internals excluded |
| `…direction="inherits"` | same error | `InvalidArgumentError`: `direction 'inherits' applies only to class targets; 'needle.retrieval.data' is a module. Directions that accept it: callers, callees, impact, governed_by.` |
| CLI `refs needle.retrieval.data --direction callers` | PageIndex JSON, rc 0 | the same markdown as MCP (surface-rendered pointers), rc 0 |
| CLI `refs … --direction inherits` | PageIndex JSON, rc 0 | the message above, rc 1 |
| `get_symbol(target=<module>, depth="tree"\|"summary")` | outline | **byte-identical** |

### Contract

§3.5 (`tool-contracts.md:261-281`) only requires `target` to follow the dotted grammar
(`:178-187`), and module edge rows fit the `items[]` field set exactly. `callers` already
returns GOVERNS rows (`reference_service.py:163`, which has no kind filter). No text
change.

### Acceptance criteria

- **AC1.1:** For a `.py` module target, `get_references` with `callers`, `callees`,
  `impact` or `governed_by` returns an envelope that validates against `ReferencesEnvelope`
  and never contains PageIndex JSON.
- **AC1.2:** `callers` is the deduplicated union of edges into the module id and IMPORTS
  edges into the direct children, ordered module-id rows first. CALLS edges into children
  are absent.
- **AC1.3:** `impact` equals a brute-force global ranking over all seeds on a fixture where
  per-seed top-k differs from global top-k, and where a member-internal caller (such as
  `M.Loader.load` → `M.parse`) exists. No row in `internal` is ever listed.
- **AC1.4:** `inherits` and `context` on a module raise `InvalidArgumentError` naming the
  target and its kind, never `ServiceUnavailableError`.
- **AC1.5:** A single-segment dependency package with a graph direction resolves to its
  top module or raises `InvalidArgumentError`. Tree shows stay byte-identical.
- **AC1.6:** The CLI and MCP outputs satisfy
  `strip_pointers(cli_text) == strip_pointers(mcp_text)`, their `items[]` are equal, and
  the exit codes are 0 on success and 1 on `InvalidArgumentError`.
- **AC1.7:** A `.toml` module root returns empty edges with `meta.resolution="unavailable"`.
  A `.md` root reports `"syntactic"`.
- **AC1.8:** `show` in `{"default", "tree"}` on a module is byte-identical, so
  `test_module_lookup_with_tree_svc_returns_rendered_tree` (`test_lookup_service.py:261`)
  and `test_tree_branch_carries_target_extension` stay green.
- **AC1.9:** A module with more direct class/function children than `max_module_seeds`
  searches exactly `cap` seeds, records the truncation entry (`meta.truncated=true`), and
  logs `module_target_seed_cap` once. The cap is read only from `ImpactConfig`; a
  YAML/model parity test covers the new key.

### Tests

- **New `tests/application/test_module_references.py` (pure):**
  - `module_seed_ids` skips `import_block` and `code_example`, and honours the cap.
  - The importer merge: ordering, deduplication, imports-only filtering for children.
  - `merge_impact` against brute force, including exclusion of internal callers.
  - The messages from `reject_module_show`.
- **New `tests/application/test_lookup_module_targets.py`:** uses `make_fake_uow_factory`
  plus a local tree_svc/ref_svc double modelled on `test_lookup_service.py`'s
  `_tree_svc_for_module` (copied, not imported). Covers AC1.2, 1.4, 1.5, 1.7, 1.8, 1.9 and
  E1.
- **Wire test (new `tests/test_tool_surface_wire.py`):**
  - Setup: a real index over a tmp project, built by a new shared helper
    `tests/_index_fixture.py` that reuses the pipeline pattern from
    `tests/test_reference_probe_regressions.py:107-150` without editing that file. The
    project has `pkg/mod.py` (a class and a function), `pkg/user.py` (`from pkg.mod import
    Alpha`) and `import pkg.mod`.
  - For each direction, `server._to_call_tool_result(await router.get_references(...),
    ReferencesEnvelope)` must validate (AC1.1).
  - `inherits` must raise `InvalidArgumentError` (AC1.4).
  - The CLI's `main(["refs", "pkg.mod", "--direction", "callers", …])` under capsys must
    satisfy AC1.6.
- **Config:** extend the retrieval config tests with the `ImpactConfig.max_module_seeds`
  default, its bounds, and a YAML override.

---

## 2. Bug 2: `get_symbol(depth="source")` on a class or module

### Root cause

`SymbolSourceService.source_with_items` renders `chunks[0].text` for
`filter={"qualified_name": target}, limit=1` (`symbol_source.py:100`, `:109`). Chunks hold
only a node's *direct* text: a CLASS chunk stops before its first method
(`ast_python.py:418-419`, `:444`), and a MODULE chunk is the dedented docstring (`:146`,
`:157`). Meanwhile `_span_item` (`:26-47`) reports the whole node span, and truncation is
only recorded when the chunk exceeds `max_lines` (`:114-124`). So `BaseIndexStore` (span
22–86) returns 8 lines with `truncated=false`, and a 479-line module returns its docstring
inside a `python` fence with `truncated=false`.

Function and method chunks already contain the full `def` slice and are correct.

### Chosen fix: rebuild the node's span from indexed text, and mark gaps outside the fence

This applies to CLASS and MODULE targets whose `source_path` ends in `.py`.

1. **Rebuild from the index.** Inside the existing UoW, load the node from its module tree.
   Then walk it:
   - Place each node's own `text` at its `start_line`. This applies to CLASS, FUNCTION,
     METHOD and IMPORT_BLOCK nodes, restricted to the same `source_path`.
   - A CLASS's own text is clipped so it never reaches its first child's `start_line`.
   - Any other node's text is placed only when its line count is at most
     `end_line - start_line + 1`. Otherwise it is skipped, with a debug-level JSON log
     `symbol_source_span_skip`.
   - The MODULE node's own text (its dedented docstring) and every `code_example` node
     (stubbed spans, `ast_python.py:462-468`) contribute nothing.
   - Everything is clamped to `node.start_line..node.end_line`.

   F7 shows that indexed text is raw file lines at their true numbers, so every placed line
   is verbatim for the last index pass.
2. **Render verbatim runs and gaps.** Each maximal run of covered lines is its own
   ` ```python ` fence. Each maximal run of uncovered lines becomes one marker line
   **outside** any fence: `[lines 33-35 not in the index]`, or `[line 29 not in the index]`
   for a single line. When there is at least one gap, one closing note follows the last
   fence: `[{n} lines of this span are not in the index — read {path} lines {a}-{b} for
   the full text]`. This is body text, not a ledger entry, so `meta.truncated` still means
   only "cut by a limit/budget" (contract §2.1, `:79`). Because every line inside a fence
   is verbatim and every marker names its line range, a reader can recover any fenced
   line's file number from `items[0].start_line` and the markers.
3. **Cap.** `max_lines` bounds the *span window* `start_line .. start_line + max_lines - 1`,
   gap lines included. Past the window, the existing footer
   `[… {elided} more lines — read {path} directly]` and the existing ledger entry
   (`symbol_source.py:114-124`, unchanged text) apply, and `truncated=true`, which is the
   honest §2.1 meaning.

Function/method targets and every non-Python kind (markdown heading, text section, notebook
cells) stay on today's chunk path, **byte-identical**, including the ` ```python ` fence.
No disk read happens anywhere, so the §4.2 split (the index serves the index pass, the file
tools serve live disk) holds. The cap config (`symbol_source.max_lines`,
`default_config.yaml:127-128`) is untouched.

**Why this approach.**
- Live-disk gap filling would break §3.3 Backend (`:240`) and §4.2 (`:412`), and its gap
  lines cannot be verified. It is kept as owner proposal P1 (§9).
- Markers inside the fence are indistinguishable from real `#` comments and break "every
  fenced line is verbatim".
- Setting `truncated` for gaps would redefine a frozen meta field.
- Widening chunk text, or persisting source at index time, would be a re-embed or schema
  event (follow-up F-9).

### Placement

- **New `python/pydocs_mcp/application/symbol_source_span.py`** (~120 lines):
  - `SPAN_SOURCE_KINDS = frozenset({NodeKind.CLASS, NodeKind.MODULE})`
  - `indexed_lines_by_number(node: DocumentNode) -> dict[int, str]`
  - `span_runs(indexed: Mapping[int, str], start: int, end: int) -> list[tuple[bool, int, int]]`
  - `render_span(runs, indexed, path, target) -> tuple[str, int]` returns the body and the
    gap-line count.
  - `window_end(start: int, end: int, max_lines: int) -> int`
- **`symbol_source.py`:**
  - Import the new helpers after `:18`.
  - Insert one statement after `:101`, inside the existing `async with` (one UoW per call):
    `span_node = await _span_node(uow, target, chunks[0].metadata) if chunks else None`.
    `_span_node` returns the node only when its kind is in `SPAN_SOURCE_KINDS` and its path
    ends in `.py`. It reuses `_find_by_qualified_name` (`:50-58`).
  - Replace `:107-125` with a branch: `span_node` → `_render_span_source(...)`, otherwise
    `_render_chunk_source(...)`, which is today's code moved verbatim. Both return the
    same `(text, items, {})` shape.
  - Lines `:99-101` and `:102-106` stay unchanged. They separate this change from the
    symbol-resolve hunks (§8).
- **Not touched:** `storage/factories.py`, `server.py`, the YAML config.

### User-visible before → after (`needle.indexing.base.BaseIndexStore`, span 22–86)

- **Before:** one ` ```python ` fence with the class line and its docstring (8 lines),
  `items[0]` span 22–86, `truncated=false`.
- **After:** the header, then alternating verbatim fences (the class header, then each
  method body at its true lines) and markers for the uncovered runs (the repro bundle's
  are lines 29, 33-35, 40, 53, 58-60, 64, 68-71, 74-75, 78-80 and 84). Then the note
  `[20 lines of this span are not in the index — read src/needle/indexing/base.py lines
  22-86 for the full text]`. Same span, `truncated=false`.
- **Module `needle.retrieval.page_retrievers` (479 lines), before:** its docstring prose
  in a `python` fence, `truncated=false`.
- **Module, after:** the indexed defs, classes and import blocks within file lines 1–400,
  with markers for the uncovered runs (including the docstring lines), then
  `[… 79 more lines — read src/needle/retrieval/page_retrievers.py directly]`,
  `truncated=true`.

### Contract

§3.3 promises that `source` = "verbatim source text" (`:237`). With this change every
fenced line is verbatim, and anything the index lacks is stated explicitly instead of being
passed off as the whole span. §3.3 Backend ("document_trees (+ chunk text for
depth="source")", `:240`) stays true: the text now comes from document_trees node text plus
chunk text. §2.1 `truncated` stays limit-only. `items[]` is unchanged. **No text change.**

ADR 0011 (`docs/adr/0011-*.md:75-78`) records the caveat that "get_symbol depth=source on a
class returns only the class-header chunk text … rendered coverage ≠ span coverage". This
PR resolves that caveat. Commit 6 appends a dated one-paragraph addendum to ADR 0011
stating that class and module source now renders the whole span (verbatim indexed runs plus
explicit gap markers). The ADR 0011 evidence-fidelity classification (`:171`, hunk-level
node spans) is unaffected: spans are unchanged and every fenced line is verbatim.

### Acceptance criteria

- **AC2.1:** For a class target, every fenced line equals the file line at its computed
  position (`items[0].start_line` + offset, accounting for markers). The fenced lines plus
  the marker ranges exactly cover `start_line..min(end_line, window_end)`.
- **AC2.2:** Each gap run is exactly one marker line outside any fence, naming its range.
  The closing note states the gap-line count and the path. Gaps alone never set
  `meta.truncated` and never add a ledger entry.
- **AC2.3:** A module over the cap renders only its span window, the existing cap footer,
  the existing ledger entry, and `truncated=true`. The dedented docstring is never placed
  as code.
- **AC2.4:** `code_example` children and non-`.py` paths never contribute lines. Text of
  another node that is longer than its span is skipped.
- **AC2.5:** Output for function/method targets and for markdown heading, text section and
  notebook cell targets is byte-identical to today.
- **AC2.6:** CLI `symbol --depth source` and MCP `get_symbol` produce equal bodies after
  `strip_pointers`, and equal `truncated` flags.
- **AC2.7:** The service performs no filesystem read. The test uses a fake uow only, with
  no tmp files.

### Tests

- **New `tests/application/test_symbol_source_spans.py`**, using `make_fake_uow_factory`
  with in-memory trees:
  - a class with CLASS text at lines 1–3 and METHOD children at 5–6 and 8–9;
  - a module under `max_lines=5`;
  - a `code_example` stub at line 1;
  - an oversize child text;
  - a method target (byte-identity against a golden captured from today's code);
  - a markdown-heading target (byte-identity).

  It also asserts the fence-verbatim invariant of AC2.1.
- **Wire test (`tests/test_tool_surface_wire.py`):** index a tmp project containing a
  decorated second method and a comment between methods. Call `get_symbol(depth="source")`
  through the router on both surfaces. Assert every fenced line equals the tmp file line
  at its position, the markers cover exactly the decorator and comment lines, and
  `truncated=false`.

---

## 3. Bug 3: grep `glob="*.py"` only matches root files

### Root cause

`_filter_candidates` (`file_tools.py:199-213`) matches `_glob_to_regex(glob)` against the
full root-relative `c.rel`. The regex is anchored `^…$` and `*` becomes `[^/]*`
(`:117-140`). The comment at `:209-210` copies the glob tool's dialect on purpose. But the
contract example (§3.7, `tool-contracts.md:306`) and the shipped description
(`defaults/descriptions.md:79`) both use `glob="*.py"`, which today matches only root-level
files, even with `path=`. On example_needle it returns "No matches." plus the ADR 0007
`grep_zero_hit` hint; ripgrep returns 113 files.

### Chosen fix: `rg --glob` / gitignore anchoring, for grep's `glob` only

Add `_grep_glob_regex(glob: str) -> re.Pattern[str]` next to `_glob_to_regex`. It
normalizes the glob in this order:
1. A leading `./` becomes `/`.
2. A trailing `/` gets `**` appended, so it matches everything under that directory.
3. A leading `/` anchors at the selected root: `_glob_to_regex(glob[1:])`.
4. A glob with no `/` matches the file name at any depth: `_glob_to_regex("**/" + glob)`.
5. Anything else keeps today's behaviour: anchored at the *selected root*. rg also anchors
   at the invocation root, not the search path; `rg --glob 'needle/scoring/*.py' src`
   finds nothing.

Call it at `:211`, and rewrite the `:209-210` comment to: *grep's glob follows `rg --glob`
anchoring; the glob tool keeps root/`path`-anchored POSIX glob (§3.8).* `_glob_to_regex`,
`_scoped_match_key` and the glob tool stay byte-identical, which also keeps the
branch-diff plan's direct import of `_glob_to_regex` valid.

Prototype results (`toolbugs/proto_glob.py` against `git ls-files`;
`toolbugs/glob_edge.py`, verified), old → new:

| Glob | Old | New | Note |
|---|---|---|---|
| `*.py` | 0 | 113 | matches rg |
| `src/**/*.py` | 73 | 73 | |
| `/*.md` | 0 | 7 | |
| `scoring/*.py` | 0 | 0 | rg parity |
| `test_*.py` | 0 | 31 | |
| `./src/*.py` | ∅ | `src/core.py` | |
| `src/` | ∅ | every file under `src/` | |
| `**/*.md` | = `*.md` | = `*.md` | |

**Rejected alternatives.**
- *Relative to `path=`*: still root-only with no `path`, and it breaks `src/**/*.md` under
  `path=src`.
- *fnmatch*: `src/*.py` would over-match.
- *Text-only fix*: the default stays hostile, and the contract example is owner-gated.

### Text

- CLI `--glob` help (`__main__.py:389-393`) becomes: `Glob filter on candidate file paths
  (e.g. "*.py", "src/**/*.md"). A glob without "/" matches file names at any depth (like
  rg --glob); one with "/" matches the root-relative path; a leading "/" anchors at the
  root.`
- **`defaults/descriptions.md` grep section (mandatory, MCP-visible):** append one sentence:
  `A glob without "/" matches file names at any depth (rg --glob); "/x" anchors at the
  root.` The grep section sits at about 339 of its 500-token budget
  (`description_source.py:66`).
- The internal spec sentence at
  `docs/superpowers/specs/2026-09-04-branch-diff-task-layer-design.md:455-458` ("`test_*`
  alone matches only a root-level file") is updated to the new rule.
- The `grep_zero_hit` rule and its text are unchanged (ADR 0007).

### Before → after

`grep(pattern="class MaxSimScorer", glob="*.py")`:
- **Before:** `No matches.` plus `[suggestion: … search_codebase …]`.
- **After:** `src/needle/scoring/strategies.py`. The same result with `path="src"`.

### Contract

§3.7's two examples (`*.py`, `src/**/*.md`, `:306`) now do what they suggest. §3.8 and the
glob tool are untouched, and ADR 0003 (`docs/adr/0003-grep-glob-backend.md:219-220`)
supports "behave exactly as their mainstream-shaped descriptions promise". **No text
change.** An optional clarifying sentence is P2 (§9).

### Acceptance criteria

- **AC3.1:** `*.md` finds `src/notes.md`.
- **AC3.2:** `*.py` with `path="src"` finds every `.py` file under `src` at any depth.
- **AC3.3:** `/*.py` finds root files only, and `./*.py` equals `/*.py`.
- **AC3.4:** `src/*.py` excludes `src/a/b.py`; `src/**/*.py` recurses; `src/` equals
  `src/**`.
- **AC3.5:** `path="src", glob="a/*.py"` does not match `src/a/b.py` (rg parity,
  documented in the help text).
- **AC3.6:** `*.py` with `scope="deps"` reaches package subdirectories.
- **AC3.7:** Glob-tool results are unchanged.
- **AC3.8:** `**/*.md` and `*.md` select the same set.

### Tests (`tests/application/test_file_tools.py`, which the symbol-resolve branch does not touch)

- Invert `test_grep_glob_star_stays_at_root_level` (`:338-341`) into
  `test_grep_slash_free_glob_matches_basename_at_any_depth`, and keep `:344-353`.
- Add:
  - `test_grep_slash_free_glob_composes_with_path` (adds `src/a/b.py`)
  - `test_grep_leading_slash_glob_anchors_at_root`
  - `test_grep_dot_slash_glob_equals_leading_slash`
  - `test_grep_trailing_slash_glob_matches_directory`
  - `test_grep_slashed_glob_stays_root_anchored_under_path`
  - `test_grep_double_star_prefix_is_idempotent`
  - `test_grep_deps_scope_slash_free_glob_reaches_subdirs` (existing deps fake)
  - a parametrized `_grep_glob_regex` table test mirroring the rows above
  - `test_glob_tool_semantics_unchanged`, which re-asserts `:472-476` and `:501-505`
    alongside the change
- Re-baseline `tests/fixtures/goldens/tool_docs_phase0_baseline.json` (pinned by
  `test_description_loading.py:65-68`) and `mcp_registration_surface.json` for the grep
  description edit.

---

## 4. Bug 4: get_overview run-on lines

### Root cause

`_POINTER_WITH_EOL_RE = re.compile(_POINTER_RE.pattern + r"\n?")` (`formatting.py:100`)
drives the suppression pre-pass in `resolve_pointers` (`:211-213`), and `strip_pointers`
(`:217-219`) removes the same span. Both assume the token sits on its own line. The
overview renderers put tokens inline, just before the line break:
- module map, `:973-974`
- entry points, `:984`
- dependencies, `:1005`
- workspace card, `:1128`

So when a token is suppressed, the bullet's newline goes with it. Suppression happens when
`is_symbol_target` is false (`mcp_inputs.py:65-78`), for example a leading-dot `.toml` id
or a hyphenated `needle-demo`. With `output.next_pointers.enabled=false`, every inline
bullet merges.

### Chosen fix: one line-aware elision span, shared by both paths

```python
_POINTER_SPAN_RE = re.compile(r"[ \t]*" + _POINTER_RE.pattern + r"\n?")  # prefix adds no groups
_ANY_POINTER_SPAN_RE = re.compile(r"[ \t]*\[\[next:[^\]]*\]\]\n?")       # strip path

def _elided_pointer_span(match: re.Match[str]) -> str:
    """Own-line token: drop the whole line (today's bytes). Inline token: drop
    it with its leading blanks but KEEP the line break it sat before."""
    start = match.start()
    if start == 0 or match.string[start - 1] == "\n":
        return ""
    return "\n" if match.group(0).endswith("\n") else ""
```

- `resolve_pointers`:
  `_POINTER_SPAN_RE.sub(lambda m: _elided_pointer_span(m) if _is_invalid_symbol_pointer(m) else m.group(0), text)`.
  `_is_invalid_symbol_pointer` reads only named or positional groups of `_POINTER_RE`, and
  the `[ \t]*` prefix adds none. The implementer verifies group indices are unchanged.
- `strip_pointers`: `_ANY_POINTER_SPAN_RE.sub(_elided_pointer_span, text)`.
- `_POINTER_WITH_EOL_RE` is deleted, and its rationale comment moves to `_POINTER_SPAN_RE`.
- **Byte impact:** own-line column-0 tokens, which is every own-line token emitted today
  (F12), produce exactly today's bytes. For example,
  `test_overview_decisions_block.py:130` stays green. Inline tokens keep their line break.
  An indented own-line token, which nothing emits today, now drops its indentation too; the
  table test pins that.
- **Companion:** `_overview_module_block` leaves out ` — ` when `first_doc_line` is empty.
  Today it emits `` - `.coding-agent-playbook.local.toml` —  ``.

The alternative, putting every overview pointer on its own line, would change every
overview's bytes even when nothing is suppressed, and it would fix only the overview.

### Before → after (repro render, `overview_cli.txt:16`, `:19`)

Before:
```
- `.coding-agent-playbook.local.toml` —  - `.coding-agent-playbook.toml` — schema_version = 1 - …
## Entry points
- `needle-demo` (script) - `needle.pipeline` (root) → pydocs-mcp symbol needle.pipeline
```

After (bug 4 alone; bug 5 then re-targets the script pointers):
```
- `.coding-agent-playbook.local.toml`
- `.coding-agent-playbook.toml` — schema_version = 1

## Entry points
- `needle-demo` (script)
- `needle.pipeline` (root) → pydocs-mcp symbol needle.pipeline
```

The blank line before each `## ` heading comes back, because the last bullet keeps its
newline and the block join adds one more. The workspace card's strip path (`:1128`) stops
merging lines in the same way.

### Contract

Rendering only; no change.

### Acceptance criteria

- **AC4.1:** On both surfaces, an inline suppressed token leaves exactly one line per
  bullet, with no trailing blank.
- **AC4.2:** A column-0 own-line suppressed or stripped token removes its whole line, with
  bytes unchanged from today.
- **AC4.3:** `resolve_pointers` suppression bytes equal `strip_pointers` bytes in every
  shape: own-line, inline, two inline tokens, indented own-line, end of text with and
  without a newline.
- **AC4.4:** An overview rendered with pointers disabled, and a workspace card rendered
  with pointers disabled, have one line per bullet.
- **AC4.5:** An empty `first_doc_line` renders no dangling `—`.

### Tests

- Amend three tests to the one-bullet-per-line form:
  - `test_next_pointers.py:226` → `"before\nafter"`.
  - `test_pointer_target_validity.py:81-85` → `"before\nafter"`, renamed to
    `test_mid_line_suppression_keeps_line_break_and_matches_strip`.
  - The `get_overview` golden at `test_structured_envelope.py:40` →
    `"- \`pydocs-mcp\` (script)\n- \`harness-ask-your-docs\` (script)\n"`, followed by the
    blank line before `## Structure communities`. Update its comment at `:36-39`. The
    commit message states that contract §2's byte-identity sentence is scoped to the
    0.5.x → 0.6.0 boundary.
- New `tests/application/test_pointer_elision.py`:
  - a parametrized `(surface × resolve|strip × shape)` table;
  - an overview line-structure invariant over an `OverviewCard` containing `proj.core`,
    `.cfg-x.toml` (empty doc), `docs.0001-a.md`, a `demo-cli` script, and one indexed and
    one unindexed dependency. For `resolve_pointers(mcp)`, `resolve_pointers(cli)` and
    `strip_pointers`, assert that the number of `- ` lines equals the number of entries,
    that no line contains ``- ` `` twice, that every `## ` heading after the first is
    preceded by a blank line, and that no line ends in a space or `— `;
  - a workspace-card strip case.

---

## 5. Bug 5: dead get_overview pointers

### Root cause

- **Module map.** `_overview_module_block` (`formatting.py:969-977`) emits
  `lookup-show:<module>:context`, which renders as `get_context(targets=["<module>"])`
  (`:121`). `_resolve_context_target` rejects that (`lookup_service.py:671-672`), so all 9
  module-map pointers on the repro fail.
- **Dependency profile.** `_dependency_profile` (`overview_service.py:395-406`) never
  checks names against the indexed packages. All 10 dependency pointers fail with
  `package 'typing' not indexed` (`lookup_service.py:430`).
- **Script entry points.** These point at the script *name* (`overview_service.py:302`,
  `formatting.py:984`). `needle` resolves only by coincidence. `needle-demo` is suppressed,
  even though its callable `needle_demo.cli.main` resolves (`example_needle/pyproject.toml:70-72`).

### Chosen fix: every emitted pointer resolves by construction, and a test proves it

1. **Module map** emits `pointer_token('lookup-show', m.qualified_name, 'tree')`, which
   renders as `get_symbol(target="<m>", depth="tree")` or
   `pydocs-mcp symbol <m> --depth tree` (`_SHOW_TO_TOOL["tree"]`, `formatting.py:122`).
   This call was verified to resolve on both surfaces. Update the docstring at `:970-971`.
   `get_context` stays symbol-only, which is consistent with the symbol-resolve spec's K5
   and with bug 1.
2. **Dependency profile.** Add a new internal field
   `OverviewCard.indexed_packages: frozenset[str] = frozenset()`, defaulted so the fakes and
   goldens still build. `_assemble` fills it from the `packages` census it already receives
   (`overview_service.py:188-219`). The renderer emits `lookup:<pkg>` only when
   `pkg in card.indexed_packages`; otherwise it prints `- typing (N imports)`. An import
   name that differs from its distribution name (`yaml` vs `pyyaml`) gets no pointer. That
   is honest, and the docstring notes it.
3. **Script entry points.** Add `EntryPoint.target: str = ""`. `_entry_points` fills it
   through `_script_callable(value: str, trees: Mapping[str, DocumentNode]) -> str`:
   - Strip any `[extra]` suffix and surrounding whitespace, then split `mod:attr`.
   - Return `f"{mod}.{attr}"` only when `mod in trees` **and**
     `trees[mod].find_node_by_qualified_name(f"{mod}.{attr}")` is not None
     (`document_node.py:103`).
   - Otherwise return `""`. This covers a Typer/Click object or a re-exported name, which
     are not tree nodes (F14).

   The renderer targets `e.target` for scripts and `e.name` for `module`/`root` entries,
   and emits no token when the target is empty. The label stays the script name.

### Text

`descriptions.md:45`: `get_context(targets=["pydocs_mcp.retrieval.pipeline"])` is a
module target and fails today. It becomes
`get_context(targets=["pydocs_mcp.retrieval.pipeline.base.RetrieverPipeline"])`; the class
is defined at `retrieval/pipeline/base.py:62`. This is a required correctness fix, because
the description advertises a failing call.

An actionable get_context module-target error, one that names
`get_symbol(depth="tree")`, would edit the raise site at `lookup_service.py:672`, which
the symbol-resolve branch splits. That work is deferred as follow-up F-4.

### Before → after (MCP)

| Line | Before | After |
|---|---|---|
| Module map | `` - `needle.retrieval` — … → get_context(targets=["needle.retrieval"]) `` | `` - `needle.retrieval` — … → get_symbol(target="needle.retrieval", depth="tree") `` |
| Script | `` - `needle` (script) → get_symbol(target="needle") `` | `` - `needle` (script) → get_symbol(target="needle.cli.main") `` |
| Script | `` - `needle-demo` (script) `` (merged into the next line) | `` - `needle-demo` (script) → get_symbol(target="needle_demo.cli.main") `` |
| Dependency | `- typing (N imports) → get_symbol(target="typing")` | `- typing (N imports)` |
| CLI module map | `→ pydocs-mcp context needle.retrieval` | `→ pydocs-mcp symbol needle.retrieval --depth tree` |

### Contract

Pointers are rendering. The §3.1 `items[]` come from `ModuleEntry`
(`overview_service.py:45-51`) and are unchanged. `OverviewCard` and `EntryPoint` are
internal. **No text change.**

### Acceptance criteria

- **AC5.1:** Every module-map pointer renders `get_symbol(…, depth="tree")`.
- **AC5.2:** A dependency pointer appears if and only if the package is indexed.
- **AC5.3:** A script pointer targets its dotted callable only when that callable is a
  tree node. A non-node attribute (`app = object()`), a re-export, or an unindexed module
  gets no pointer.
- **AC5.4 (every pointer resolves):** On a real index, every pointer the overview emits on
  MCP and on CLI executes without `NotFoundError`, `ServiceUnavailableError`,
  `ValidationError` or a non-zero exit, and the MCP and CLI pointer counts are equal.
- **AC5.5:** The golden at `test_format_overview.py:45` expects
  `[[next:lookup-show:proj.core:tree]]`.

### Tests

- **Unit (`tests/application/test_format_overview.py`):** update `:45`; add cases for
  indexed vs unindexed dependency rendering, script-target rendering, and an empty target
  producing no token.
- **Unit (`tests/application/test_overview_service.py`):** `_script_callable` handles
  `mod:attr`, `mod:attr [extra]`, bare `mod`, an unindexed module, a non-node attribute and
  a re-export; `_assemble` fills `indexed_packages`.
- **End-to-end: new `tests/application/test_overview_pointers_resolve.py`.**
  - **Index setup.** Index a tmp project headless through the real CLI `index` path, using
    the `MockEmbedder` monkeypatch pattern (`tests/test_cli.py:42`) with
    `PYDOCS_CACHE_DIR` isolation. The project contains:
    - `demo/core.py` (a class and a function) and `demo/cli.py`, with `def main`,
      `app = object()` and `from demo.core import helper`;
    - `pyproject.toml [project.scripts]` with `demo = "demo.cli:main"`,
      `demo-tool = "demo.cli:main"`, `app-cli = "demo.cli:app"`,
      `reexp = "demo.cli:helper"` and `ghost = "notindexed.x:main"`;
    - `.demo-config.toml`;
    - `import typing` and `import requests`;
    - one `# DECISION:` marker, so the Decisions block renders its `get_why()` pointer
      (`decision_capture.enabled` defaults to true, `default_config.yaml:167-169`).
  - **Routers.** Build them with `server.build_routers(config, db_path=…, surface="mcp"|"cli")`
    and render `get_overview`.
  - **MCP check.** Parse each `→ tool(kwargs)` with `ast.parse(...).body[0].value.keywords`
    and `ast.literal_eval`. Validate the kwargs through the tool's input model, await the
    router method, and validate the result through `server._to_call_tool_result` with the
    matching envelope.
  - **CLI check.** `shlex.split` each `→ pydocs-mcp …`, append the db selector, and assert
    that `main(argv)` exits 0.
  - **Non-vacuity guard.** At least one pointer exists of each kind: module tree, script
    callable, root or `__main__`, and why. `ghost`, `app-cli`, `reexp`, `requests`,
    `typing` and `.demo-config.toml` all render with no pointer.
- **Static.** For every CLI template in `_SHOW_TO_TOOL` and `_POINTER_RENDERERS`, the real
  argparse parser accepts the rendered argv.

---

## 6. Extras and description text

**E1: CLI wording leaks into the MCP inherits error.** Replace `lookup_service.py:506-509`
with `INHERITS_NEEDS_CLASS` (§1). Example:
`direction 'inherits' applies only to class targets; 'needle.cli.main' is a function.
Directions that accept it: callers, callees, impact, governed_by.`
Update the docstring examples at `mcp_errors.py:20` and `formatting.py:594-604` to say
`direction="inherits"`. No test pins the old string.
- **AC-E1:** MCP and `refs --direction inherits` on a function raise
  `InvalidArgumentError` whose text contains the target, `function` and `inherits`, and
  contains neither `show=` nor `CLASS nodes`.

**E2: `lookup --help` advertises a form that does not resolve.**
- `__main__.py:545` becomes
  `pydocs-mcp lookup mypkg.my_module.MyClass  # YOUR class — project code uses its bare dotted name`.
- `:553` becomes
  `Dotted path (e.g. "fastapi.routing.APIRouter"). Project code uses its bare dotted name (e.g. "mypkg.mod.MyClass"). Empty = list all indexed packages.`

This matches contract §3 addressing (`tool-contracts.md:182-186`).
- **AC-E2:** `lookup --help` contains no `__project__.`. Add a new test in
  `tests/test_cli.py`, appended only.

**Description edits (`defaults/descriptions.md`, one isolated commit):**
1. *Required:* the get_context example switches to a class target (§5).
2. *Required:* the grep glob sentence (§3).
3. *Behaviour doc:* add a get_references clause: `A module target answers its import graph:
   callers = modules importing it or its members, callees = its imports, impact =
   transitive callers of it and its members (excluding its internals), governed_by =
   decisions on it; inherits needs a class.` Keep it within the per-tool 500-token budget
   (`description_source.py:66`); if the budget test fails, shorten the clause rather than
   drop it.

These edits change the descriptions-artifact hash that ADR 0006 (`:155`) logs and that
ADR 0018 uses as the seed candidate, whose domain ADR 0021 (`:99-101`) describes as
unaffected "while defaults/descriptions.md … stay untouched". No committed lockfile
carries an `artifact_hash` (F19). The PR description must still flag the change, so any
*local* seed-anchoring or campaign lockfiles made from the old seed are known to be stale.
The commit deliberately re-baselines `tool_docs_phase0_baseline.json` and
`mcp_registration_surface.json`.

---

## 7. Follow-ups (not in this PR)

- **F-1:** A module without a docstring has no qualified-name chunk, so `depth="source"`
  raises NotFound. Found by reading the code; it does not reproduce on the bundle.
- **F-2:** Decorator-inclusive method spans (a re-embed event).
- **F-3:** Nested classes as nodes (chunker scope).
- **F-4:** An actionable get_context module-target error naming `get_symbol(depth="tree")`,
  once the symbol-resolve branch lands (it edits `lookup_service.py:672`).
- **F-5:** Non-Python files take 11 of the 20 module-map slots.
- **F-6:** A `grep_zero_hit` variant for "glob/path matched no files" (ADR 0007 process).
- **F-7:** `{a,b}` / `[...]` glob syntax.
- **F-8:** A fence language per extension for non-Python `depth="source"` (`.ipynb` code
  cells must stay `python`).
- **F-9:** Persist `__project__` source at index time, so `depth="source"` has no gaps (a
  schema and reindex event; it would make P1 unnecessary).
- **F-10:** A batched `ReferenceStore.find_callers_many`, if seed fan-out shows up in
  latency (touches `storage/protocols.py` and `tests/_fakes.py`).

---

## 8. Keeping hunks disjoint from the symbol-resolve branch (HEAD `8d2f3dc7` + working tree)

`git merge-file` merges cleanly when one unchanged line separates the two sides' hunks,
and conflicts when they are adjacent (scratch check).

| File | This PR | Symbol-resolve | Result |
|---|---|---|---|
| `lookup_service.py` | import list `:58`; field after `:346`; `:398`, `:401-402`; `:411-430`; new methods after `_symbol_lookup`; `:506-509`; `:516-530` extraction; comments `:436-440`/`:465-469` | insert after `:52`; `:67-68`; field after `:358`; `:374-381` header replaced by `with_target_fallback` + `_dispatch_parsed` (body `:382-409` kept in place, unedited); `:620-631`; `:666-670` | disjoint: nearest pair `:381` vs `:398` (16 lines apart) and `:346` vs `:358` |
| `symbol_source.py` | imports after `:18`; insert after `:101`; `:107-125` | insert after `:85`; `:97`; after `:98`; `:100` | disjoint (`:101` and `:102-106` are unchanged separators) |
| `storage/factories.py` | one kwarg after `:173` | `:78`, `:81`, `:153`; after `:168`; after `:178`; after `:182` | disjoint |
| `retrieval/config/models.py` | `ImpactConfig` `:186-202` | after `:397` | disjoint |
| `defaults/default_config.yaml` | after `:109` | after `:129` | disjoint |
| `server.py`, `tool_router.py`, `multi_project_search.py`, `storage/protocols.py`, `application/protocols.py`, `tests/_fakes.py`, `models.py` | **not touched** | edited | disjoint |
| `formatting.py`, `overview_service.py`, `file_tools.py`, `__main__.py`, `mcp_errors.py`, `descriptions.md`, goldens, ADR 0011 | edited | untouched | disjoint |
| `CHANGELOG.md` | inserts `## [Unreleased]` / `### Fixed` | inserts `## [Unreleased]` | the second to land merges under the existing heading |

**Semantics:**
- Bug 5 keeps get_context rejecting modules (their K5).
- Bug 1 lives below `lookup_with_items` / `_dispatch_parsed`, so their fallback wraps every
  direction. It only catches `NotFoundError`, and the new `InvalidArgumentError`s pass
  through.
- Bug 2 reads the package from chunk metadata, which is compatible with their
  `package=` filter.

**Required pre-implementation step:** the symbol-resolve branch is in flight. Before commit
4 and commit 5, re-run a 3-way check for each shared file:
`git merge-file -p <this-branch-file> <merge-base-file> <symbol-resolve-working-file>`.
Update this table if any hunk became adjacent.

**Merge order:** either order works. If this PR lands first, the symbol-resolve implementer
only needs to know that `_dispatch_parsed` now calls
`_package_overview(package, show, limit)` and `_module_target(...)` at the two former
`:398` / `:401-402` sites.

---

## 9. Optional contract-amendment proposals (ADR 0007 process — NOT in this PR)

Neither proposal is required. If the owner wants either one, it lands in a follow-up PR
with these steps:
1. The contract text change, marked "owner ratification pending", as §2.4 is (`:163`).
2. An explicit flag in the PR description.
3. A dated "amendment owner-ratified YYYY-MM-DD" line once ratified.
4. The rationale recorded as an addendum to ADR 0004/0011 (P1) or ADR 0003 (P2).

**P1: live-disk gap fill for `get_symbol(depth="source")`.** This would replace the gap
markers with file lines when the project checkout is available. Proposed text:

> §3.3 **Backend:** "`document_trees` (+ chunk text; for `depth="source"`, project-code
> lines inside the node span that the index does not store may be filled from the live
> project file **only when every indexed line in the span equals the file line at the same
> number and the file is not newer than `index_metadata.indexed_at`**; filled lines are
> live-disk bytes and are **not** verified against the index pass — the index stores no
> per-file hashes (§4.2). Otherwise they render as explicit not-in-the-index markers.)"
>
> §4.2, append: "Exception: `get_symbol(depth="source")` may mix live-disk gap lines into
> an indexed span under the §3.3 conditions."

Implementation notes carried from the critique:
- Thread `indexed_at` together with `project_root`.
- Reuse the `file_tools` normpath containment rule instead of `Path.resolve()`
  (`_shared.py:125-127` has the symlink rationale).
- Use `__project__` only.
- Log `symbol_source_disk_newer` / `…_mismatch`.
- Keep `truncated` limit-only.

**P2: §3.7 clarification.**

> §3.7 `glob` row: "Glob filter on candidate file paths (e.g. `*.py`, `src/**/*.md`). **A
> glob without `/` matches file names at any depth; a glob with `/` matches the path
> relative to the selected root; a leading `/` anchors at the root** (ripgrep `--glob`
> semantics). The `glob` tool's `pattern` (§3.8) keeps root/`path`-anchored glob
> semantics."

---

## 10. Commit plan (6 test-first commits) and verification

Each commit writes failing tests first, then makes the smallest change to green, then
refactors. Every commit must pass `pytest -q`, `ruff check`, `ruff format --check` and
`mypy` on its own. Owner policy: no `Co-Authored-By` trailers, and the existing git
identity only.

1. **`fix(formatting): keep line breaks when eliding inline pointer tokens`** (bug 4 and
   the empty-doc em-dash).
   - Tests: `test_pointer_elision.py`, plus amended `test_next_pointers.py:226`,
     `test_pointer_target_validity.py:81-85` and the `test_structured_envelope.py:40`
     golden.
   - Code: `formatting.py:96-100`, `:204-219`, `:969-977`.
2. **`fix(grep): ripgrep --glob anchoring for grep's glob filter`** (bug 3, code and the
   CLI help only).
   - Tests: the `test_file_tools.py` inversions and additions.
   - Code: `file_tools.py` (`_grep_glob_regex` and `:209-211`), `__main__.py:389-393`, and
     the internal branch-diff spec sentence.
3. **`fix(overview): every emitted pointer resolves`** (bug 5; depends on 1).
   - Tests: `test_format_overview.py`, `test_overview_service.py`,
     `test_overview_pointers_resolve.py`, the static CLI-template test, and
     `tests/_index_fixture.py`. Re-check the `test_structured_envelope.py:40` golden.
   - Code: `formatting.py:969-1008`, and in `overview_service.py`: `EntryPoint.target`,
     `OverviewCard.indexed_packages`, `_script_callable`, `_assemble`.
4. **`fix(get_references): module targets answer the import graph; surface-neutral
   inherits error`** (bug 1 and E1).
   - Tests: `test_module_references.py`, `test_lookup_module_targets.py`, the
     `get_references` part of `tests/test_tool_surface_wire.py`, and the config test.
   - Code: new `application/module_references.py`; the `lookup_service.py` hunks from §8;
     `factories.py` after `:173`; `ImpactConfig` and its YAML key; docstrings at
     `mcp_errors.py:20` and `formatting.py:594-604`.
5. **`fix(get_symbol): depth="source" renders the node's whole span for classes and
   modules`** (bug 2).
   - Tests: `test_symbol_source_spans.py` and the source part of
     `tests/test_tool_surface_wire.py`.
   - Code: new `application/symbol_source_span.py`, and the `symbol_source.py` hunks from
     §8.
6. **`docs: tool descriptions, lookup help, ADR 0011 addendum, CHANGELOG`** (E2, §6
   descriptions, the ADR 0011 note).
   - Tests: the `lookup --help` test, plus re-baselined `tool_docs_phase0_baseline.json` and
     `mcp_registration_surface.json`.
   - Code/docs: `__main__.py:545,553`; `defaults/descriptions.md` (the get_context example,
     the grep sentence, the get_references clause); a dated ADR 0011 addendum; and
     `CHANGELOG.md` `## [Unreleased]` → `### Fixed`, with one bullet per bug and extra.
   - The PR description flags the change to the descriptions-artifact hash.

After each task review, run the owner's per-task simplify and clean-architecture pass as a
separate commit with behaviour unchanged byte for byte. Restore `complexipy-snapshot.json`
from HEAD before staging.

**Gate set** (CLAUDE.md §Tests & Lint). Run it where a venv can be built; this worktree
has none and the disk is nearly full.
- `ruff check` and `ruff format --check python/ tests/ benchmarks/`
- `mypy python/pydocs_mcp`
- `complexipy python/pydocs_mcp --max-complexity-allowed 15`
- `vulture python/pydocs_mcp --min-confidence 80`
- `pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90`
- `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`
- `uv lock --check`
- the pip-audit pair

Rust is untouched. For branches that predate `ad222d4`, run the suites under
`HOME=$(mktemp -d)`.

**Free live re-check after the build:** run `toolbugs/refs_driver.py`, `bug2_mcp.py` and
`ov_driver.py` against a locally built wheel on the repro bundle. Never run `search`/`why`
(paid).

**Risks:**
- `lookup_service.py` grows by about 25 lines net.
- Module fan-out is bounded by `max_module_seeds` (32 by default): at most 32 point reads
  or walks per call.
- Bug 2 adds one more tree point-read per class or module source call.
- Heavily decorated classes get many fence breaks; F-9 or P1 removes them.
- The description edits move the optimizer's seed hash, which the PR description flags.

---

## 11. Critique resolution

| # | Critic | Severity | Finding (short) | Verdict | Resolution |
|---|---|---|---|---|---|
| 1 | contract | blocker | Default-on disk fill falsifies §3.3 Backend and §4.2 | **Accepted** (verified `:240`, `:412`) | Disk fill removed from this PR. Bug 2 is index-only with no filesystem read (AC2.7). It moves to optional proposal P1. |
| 2 | contract | blocker | "Provably agree" is false because gap lines are never compared | **Accepted** | P1 now says outright that filled lines are unverified live-disk bytes. Nothing ships on that basis. |
| 3 | contract | blocker | `truncated=true` for not-indexed lines redefines frozen §2.1 | **Accepted** (verified `:79`) | Option (a): gaps are body text only, and `truncated` is set only by the cap (AC2.2/2.3). |
| 4 | contract | major | Markers inside the ```python fence look like comments | **Accepted** | Fences close and reopen around each gap. Markers sit outside, and AC2.1 checks that every fenced line is verbatim. |
| 5 | contract | major | ADR 0011 is never mentioned | **Accepted** (verified `:39-42`, `:73-78`) | The class caveat is resolved, and commit 6 appends a dated ADR 0011 addendum. The live-disk statement is untouched because there is no disk read. |
| 6 | contract | minor | O4 re-seeds the Phase-4 hash, and the example fix is bundled with optional text | **Accepted** | The example fix and grep sentence are required; the get_references clause is behaviour documentation. F19 shows no committed lockfile. The PR description flags the hash change. |
| 7 | contract | minor | The grep semantics are not MCP-visible | **Accepted** | The grep sentence in `descriptions.md` is mandatory. |
| 8 | contract | minor | "ADR 0007 amendment A/B" has no ADR home | **Accepted** | Renamed to "contract-amendment proposals (ADR 0007 process)", with the process steps and ADR homes listed in §9. |
| 9 | correctness | major | The `test_structured_envelope.py:40` golden is missing from the plan | **Accepted** (verified) | Re-baselined in commit 1 and re-checked in commit 3. The §2 byte-identity sentence is scoped to 0.5.x→0.6.0. |
| 10 | correctness | major | The disk staleness check misses same-line-count edits | **Accepted** (moot here) | Nothing ships a disk read. P1 adds the `indexed_at` mtime guard and honest wording. |
| 11 | correctness | major | Module fan-out is unbounded for dependency modules | **Accepted** (verified `reference_service.py:162-167`, `:306-311`) | New YAML cap `reference_graph.impact.max_module_seeds` (single-source default 32), a TruncationEntry and a JSON log (AC1.9). A batched read or single UoW was rejected: `LookupService` has no uow_factory, and a batched read touches files in flight (F-10). |
| 12 | correctness | major | Script pointers are only checked at module level | **Accepted** (verified `ast_python.py:192-205`) | `_script_callable` requires `find_node_by_qualified_name` to hit. The fixture adds `app-cli` and `reexp` cases (AC5.3). |
| 13 | correctness | minor | The §7 hunk table is stale | **Accepted** (re-derived at `8d2f3dc7`) | §8 is rewritten. Bug 2 no longer touches `factories.py`/`server.py`, and a merge-file re-check is mandatory before commits 4 and 5. |
| 14 | correctness | minor | "CLI text equals MCP text" is impossible (per-surface pointers, full-page footer) | **Accepted** (verified `formatting.py:563-582`) | AC1.6/AC2.6 compare `strip_pointers(...)` output plus `items[]`. |
| 15 | correctness | minor | Module impact lists the module's own internals | **Accepted** | Internals are dropped, and the per-seed limit is `limit + len(internal)`, which keeps the merge exact (proof in §1, AC1.3). |
| 16 | correctness | minor | Disk reader containment uses `resolve()` (symlinks) | **Moot** | There is no disk reader in this PR. The note moves into P1. |
| 17 | correctness | minor | Fence by extension regresses `.ipynb` | **Accepted** (moot) | The fence-language change is dropped, and non-span kinds are byte-identical (AC2.5). Moved to F-8 with the `.ipynb` caveat. |
| 18 | correctness | minor | `./x` and a trailing `/` match nothing | **Accepted** (verified `toolbugs/glob_edge.py`) | Normalization steps 1–2 in `_grep_glob_regex` (AC3.3/3.4). |

No finding was rejected.
