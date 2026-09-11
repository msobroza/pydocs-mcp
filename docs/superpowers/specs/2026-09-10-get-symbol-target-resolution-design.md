# get_symbol target-resolution fallbacks — design

- **Status:** final design (FINALIZE phase), ready to implement test-first.
- **Date:** 2026-09-10.
- **Branch:** `feat/get-symbol-resolution`, cut from origin/main `5461d8e`.
- **Problem source:** owner-assigned 2026-09-10. The ask-your-docs agent was working on a `src/`-layout project (example_needle). It called `get_symbol` with a failing target, got `isError`, and needed a whole extra turn to retry with the right name.
- **Evidence:** every `path:line` below is against this worktree at `5461d8e` unless marked otherwise. Live CLI output comes from the PyPI 0.6.1 CLI over `~/pydocs-openrouter/index` (the bundle `example_needle_5383c5f58b.db`).

## Owner decisions

1. **OD-1 — R7 trace attribution of resolved fallbacks.**
   - *Implementation:* not blocked.
   - *Merge:* blocked until the owner picks one option.
   - *Default this spec implements:* (a).
   - **(a) Resolver behaviour (default).** A resolved fallback is treated like contract §6 migration row 5 (`docs/tool-contracts.md:487`). It logs `target_fallback_resolved` on its own logger, is not captured in traces, and is ablated per rule through the YAML flags.
   - **(b) Machinery.** Traces capture the event as a machinery annotation. Pick (b) if a rewritten target must never count as model-earned evidence (ADR 0011:24-25, :221-225). It needs three owner-gated changes:
     - an ADR 0010 schema note, because `fired_rules` is defined as `suggestion_fired` records only (docs/adr/0010-trace-event-schema-and-blob-store.md:168, :172-181);
     - a `benchmarks/src/pydocs_eval/trajectory/merge.py:320-327` change, because the presence cross-check raises `SuggestionCrossCheckError` whenever `fired_rules` is non-empty and `meta.suggestion` is null, which `get_symbol` always is;
     - `TraceRecorder` attaching its handler to the new logger too (python/pydocs_mcp/observability/trace_recorder.py:49-68, :184-186).
   - *Why the event cannot reuse `suggestion_fired`:* under (a) and (b) alike, that would trip the merge.py:320-327 cross-check on every resolved `get_symbol` call.

2. **OD-2 — root-cause follow-up (non-blocking, a separate PR if approved).**
   - `AstMemberExtractor` builds member module ids with `os.path.relpath(filepath, root)` (python/pydocs_mcp/extraction/strategies/members/ast_extractor.py:166-169). That skips the chunker's package-root rule (ast_python.py:483-538).
   - Verified in the bundle: `module_members` has 128 modules starting with `src.` (e.g. `src.needle.scoring.strategies`). `chunks` has none apart from 5 `#`-suffixed egg-info rows.
   - Search advertises those `src.` names: `qname = f"{module}.{name}"` (application/multi_project_search.py:231-233) and the `[[next:lookup:{module}.{name}]]` pointer (application/formatting.py:307-308).
   - Fixing the extractor changes stored ids and needs a reindex. Whether `pipeline_hash` forces that reindex is unverified.
   - This spec makes those pointers resolve regardless, through Rule 1.

No other item needs the owner. `docs/tool-contracts.md` needs no change (§3).

---

## 1. Root cause of each failing shape

| Shape (live 0.6.1 CLI, rc=1 unless noted) | Mechanism | Raise site |
|---|---|---|
| `src.needle.scoring.strategies.MaxSimScorer` → `no module matching '…' found under 'src'` | `LookupTarget.parse` treats `parts[0]` (`src`) as the package. It probes `src`, then re-probes the full parts under `__project__` (lookup_service.py:151-172). `.py` module ids are rooted at the parent of the topmost `__init__.py` directory (ast_python.py:483-538), so no stored id starts with `src.`. | lookup_service.py:398 |
| `MaxSimScorer` → `package 'MaxSimScorer' not indexed` | A single segment becomes a package (:154-160). `_package_overview` tries the package doc, then an exact `__project__` module id, then raises (:415-430). Nothing looks up symbol names. | lookup_service.py:430 |
| `get_context` with either shape (`context scoring` → `no symbol matching 'scoring' found for context closure`) | `_resolve_context_target` rejects `module is None or not symbol_path` (:670-672). | lookup_service.py:672 |
| `depth="source"` with either shape | Exact `chunks.qualified_name` filter; `parse` is never called (symbol_source.py:96-106). | symbol_source.py:102-106 |
| Re-export `needle.scoring.MaxSimScorer` → `'…' not found in needle.scoring` | The module resolves; the symbol is not in its tree. | lookup_service.py:463 |

**Upstream trigger:** see OD-2. The search → `get_symbol` pointer carries `src.`-prefixed member names, and the model copies them.

## 2. Resolution algorithm

### 2.1 Principles

- **P1.** A fallback runs only after the existing exact path raises `NotFoundError`. `ServiceUnavailableError` is a sibling class, not a subclass (mcp_errors.py:24, :33), so it is never caught. Every call that resolves today keeps its path, including the rule that an indexed dependency wins over `__project__` (lookup_service.py:161-172).
- **P2.** At most one retry, and it is pinned to `__project__`. The retry never re-parses a rewritten string, because `parse` probes a dependency named `parts[0]` first (:161-162). This repo can index itself as a dependency: benchmarks/pyproject.toml:70/:111/:128 require `pydocs-mcp`, deps.py:190-209 collects every nested manifest, and a local bundle of this repo (`~/.pydocs-mcp/sub-pr-3-storage-repositories_d2b5bdc656.db`) has `packages` rows for both `__project__` and `pydocs_mcp`.
- **P3.** Never guess. A rewrite happens only on an exact source-root equivalence (Rule 1) or a provable uniqueness (Rule 2) over a complete scan.
- **P4.** Every name the server emits (a rewrite or a candidate) passes `is_symbol_target` (mcp_inputs.py:65-75), and for `get_context` it is never a module-only name.

### 2.2 New module `python/pydocs_mcp/application/target_resolution.py`

About 230 lines; every function stays within 4-20 lines.

```python
FallbackRule = Literal["source_root_strip", "unique_bare_name"]
ResolutionEntry = Literal["lookup", "context", "source"]
_SYMBOL_NAME_SCAN_LIMIT = 50_000   # safety bound (not a quality knob): a truncated scan never resolves and emits no candidates
_IMPORTS_PSEUDO_LEAF = "__imports__"   # chunker pseudo-node (verified: 112 such chunk rows in the bundle)
_RESOLVABLE_SOURCE_SUFFIX = ".py"  # v1: Python code symbols only (see §10 R3)

@dataclass(frozen=True, slots=True)
class TargetRewrite:
    rule: FallbackRule
    canonical: str                   # e.g. "needle.scoring.strategies.MaxSimScorer"
    module: str                      # e.g. "needle.scoring.strategies"
    symbol_path: tuple[str, ...]     # () for a module-only rewrite

@dataclass(frozen=True, slots=True)
class TargetResolution:
    rewrite: TargetRewrite | None = None
    candidates: tuple[str, ...] = ()   # ranked, capped at max_candidates
    candidate_total: int = 0           # before the cap → "(+N more)"
    exact_leaf_count: int = 0          # bare targets: eligible exact-leaf matches (drives workspace uniqueness)
    ambiguous: bool = False
    scan_truncated: bool = False

class TargetResolver(Protocol):     # lives in application/protocols.py
    async def resolve(self, target: str, /, *, entry: ResolutionEntry) -> TargetResolution: ...

@dataclass(frozen=True, slots=True)
class ProjectTargetResolver:
    uow_factory: Callable[[], UnitOfWork]
    rules: TargetResolutionConfig
    async def resolve(self, target, /, *, entry) -> TargetResolution:
        """One read UoW: Rule 1 (dotted) or Rule 2 (bare), then Rule 3 candidates.
        >>> await resolver.resolve("src.pkg.mod.Cls", entry="lookup")  # rewrite rule=source_root_strip"""

@dataclass(frozen=True, slots=True)
class NullTargetResolver:          # Null-object: every flag off, or direct/test construction
    async def resolve(self, target, /, *, entry) -> TargetResolution:
        return TargetResolution()

async def with_target_fallback(
    target: str, *, entry: ResolutionEntry, resolver: TargetResolver,
    run_exact: Callable[[], Awaitable[_T]],
    run_rewrite: Callable[[TargetRewrite], Awaitable[_T]],
) -> _T:
    """run_exact(); on NotFoundError → resolver.resolve(target, entry=entry).
    rewrite → run_rewrite(rewrite); success → log target_fallback_resolved, return.
    retry NotFoundError → raise NotFoundError(render_miss_message(str(original), <candidates for the canonical, minus the canonical>)) from original.
    no rewrite → raise NotFoundError(render_miss_message(str(original), resolution)) from original.
    A message with no candidates is re-raised as the ORIGINAL exception object (byte- and type-identical)."""
```

`TargetNotFoundError` is dropped. Candidates travel on `TargetResolution` and never through an exception, so no `mcp_errors.py` change is needed.

### 2.3 Storage: one projection read (Protocol addition)

```python
# pydocs_mcp/models.py
@dataclass(frozen=True, slots=True)
class ChunkSymbolName:
    qualified_name: str
    module: str
    source_path: str | None

# storage/protocols.py — ChunkStore
async def list_symbol_names(self, package: str, *, limit: int) -> tuple[ChunkSymbolName, ...]:
    """Distinct (qualified_name, module, source_path) of one package, ORDER BY
    qualified_name — a text-free projection for miss-path target resolution."""
```

- **SQLite** (`storage/sqlite/chunk_repository.py`, modelled on `list_id_hash_pairs` :150-164): `SELECT DISTINCT qualified_name, module, source_path FROM chunks WHERE package = ? AND qualified_name IS NOT NULL AND qualified_name != '' ORDER BY qualified_name LIMIT ?`.
  - The `package` filter uses `ix_chunks_package` (db.py:171).
  - The ordered projection closes the no-`ORDER BY`, `SELECT *` truncation problem in `list_rows` (storage/sqlite/table_crud.py:42-60).
- **Fake:** `InMemoryChunkStore` (tests/_fakes.py:234-270) gets the same method over `by_package[package]` metadata, sorted and deduplicated.
- **Scope of the change:** the only two `ChunkStore` implementers are `SqliteChunkRepository` and `InMemoryChunkStore` (grep over python/ and tests/). The runtime conformance checks (tests/storage/test_protocol_conformance.py:100, tests/storage/test_branch_repositories.py:74) keep passing once both have the method.
- **Why a new method rather than the existing `list` with `{"like": …}`:**
  - `FieldLike` always emits `%x%` (filter_adapter.py:67-74), so it cannot express "leaf equals".
  - `list_rows` has no `ORDER BY`, so a capped scan is order-dependent.
  - `SELECT *` pulls the chunk text for every row.

### 2.4 Rules inside `ProjectTargetResolver.resolve`

**Shared eligibility.** `is_resolvable_symbol_name(row, entry)` is true only when all of these hold:

- `is_symbol_target(row.qualified_name)`;
- `row.source_path` ends with `.py`;
- the leaf is not `__imports__`;
- when `entry == "context"`, `row.qualified_name != row.module` (no module-only names, because get_context rejects them at lookup_service.py:670-672).

This single predicate drives Rules 2 and 3.

The `.py` requirement closes the extension-leaf hole. In the verified bundle, bare `md` has exactly one symbol-valid exact-leaf match, `AGENTS.md`, which today renders the AGENTS.md outline (live CLI: `symbol AGENTS.md` rc=0, `symbol md` rc=1). `.continue.*` and `.github.*` rows fail `_TARGET_RE`. Chunk `origin` cannot be used as the filter: markdown chunks carry `origin='python_def'` in this bundle (verified: `AGENTS.md|…|python_def`).

**Rule 1 — source-root strip.** Dotted targets only; gated by `rules.source_root_strip`. All of these must hold:

1. `len(parts) >= 2`.
2. `await uow.packages.get(parts[0]) is None` (storage/protocols.py:35). An indexed dependency of that name shadows the rule.
3. Walk the prefixes of `parts[1:]` longest-first, calling `uow.chunks.list(filter={"package": "__project__", "module": prefix}, limit=1)`. `chunks.module` is indexed (db.py:172), and the fake supports `module` equality (tests/_fakes.py:206).
4. The first hit must satisfy:

```python
def is_stripped_source_root(segment: str, source_path: str | None, module: str) -> bool:
    """True iff `segment` is exactly the one directory the .py chunker dropped from `module`.
    >>> is_stripped_source_root("src", "src/needle/scoring/strategies.py", "needle.scoring.strategies")
    True
    >>> is_stripped_source_root("src", "src/needle/scoring/__init__.py", "needle.scoring")
    True"""
```

   The predicate normalises `\` to `/`, requires a `.py` suffix, drops a trailing `__init__`, and returns `stem_parts == (segment, *module.split("."))`.
5. The rewrite is `TargetRewrite("source_root_strip", ".".join(parts[1:]), module=hit, symbol_path=parts[1 + len(hit.split(".")):])`.
6. For `entry == "context"`, a module-only rewrite (empty `symbol_path`) is dropped.

Properties of Rule 1:

- Nothing is configured and there is no `("src",)` list. It covers `src/`, `lib/`, and this repo's `python/` layout.
- It strips exactly one segment.
- `tests.foo` never becomes `foo` unless `foo`'s chunk really has `source_path == "tests/foo.py"`.
- It works under `NullTreeService`, because it reads chunks, not trees.

**Rule 2 — unique bare name.** Single-segment targets only; gated by `rules.unique_bare_name`.

- `rows = await uow.chunks.list_symbol_names("__project__", limit=_SYMBOL_NAME_SCAN_LIMIT + 1)`, and `truncated = len(rows) > _SYMBOL_NAME_SCAN_LIMIT`.
- `exact = sorted({r for r in eligible(rows) if leaf_of(r.qualified_name) == target})`. The comparison is a case-sensitive Python `==`, so `score` does not match `Score`.
- Rewrite only when `len(exact) == 1 and not truncated`. Then `canonical = exact[0].qualified_name`, `module = exact[0].module`, and `symbol_path` is the remainder after the module (empty when `qualified_name == module`).
- `exact_leaf_count = len(exact)`, and `ambiguous = len(exact) >= 2`.
- Methods and `.py` module names count. This is conservative: more names error as ambiguous, and no name resolves to the wrong node.
- Only `__project__` is searched. Bare names never resolve to dependency symbols, because the surface is frozen and there is no scope parameter.

**Rule 3 — miss candidates.** Gated by `rules.miss_candidates`; runs whenever no rewrite exists.

- **Pool:** `list_symbol_names(parts[0])` when `packages.get(parts[0])` is not None, otherwise `"__project__"`. The rows are filtered by `is_resolvable_symbol_name`. A truncated pool yields no candidates, so the message is unchanged.
- **Tier 1:** eligible names whose leaf equals the target leaf case-sensitively. Rank by the count of trailing segments shared with the target (descending), then by name.
- **Tier 2** (only when tier 1 is empty): compute `difflib.SequenceMatcher(None, target_leaf.casefold(), name_leaf.casefold()).ratio()` over the deduplicated leaves and keep ratios of at least `rules.candidate_similarity_cutoff`. Rank by ratio (descending), then by name.
  - The whole projection is ranked, with no prefix probe. That fixes first-four-character typos (`MxaSimScorer` scores 0.917) and case-only mismatches (`maxsimscorer` scores 1.0 after casefolding).
  - Measured with python3 difflib.
- The result is `candidates = ranked[: rules.max_candidates]` and `candidate_total = len(ranked)`.

### 2.5 Hooks — the application layer only; raise sites are unchanged

**`LookupService`** (lookup_service.py; about +40 lines on a file that is already 742 lines, see §10 R5):

- A new field: `target_resolver: TargetResolver = dataclasses_field(default_factory=NullTargetResolver)`. It follows the `cross_navigator` precedent at :358, and its comment says the Null default is for direct/test construction only.
- Lookup split: the current `lookup_with_items` body (:367-409) is split with no edits. Parsing goes into `_lookup_exact(payload)`, and the branch dispatch goes into `_dispatch_parsed(payload, parsed)`.
- Public methods:
  - `lookup_with_items(payload)` → `with_target_fallback(payload.target, entry="lookup", run_exact=lambda: self._lookup_exact(payload), run_rewrite=lambda rw: self.lookup_rewritten(payload, rw))`.
  - `lookup_exact(payload)`: the exact-only path, no fallback. Multi-project pass 1 uses it.
  - `lookup_rewritten(payload, rw)` → `_dispatch_parsed(payload.model_copy(update={"target": rw.canonical}), _pinned_target(rw))`, where `_pinned_target` builds `LookupTarget(package=PROJECT_PACKAGE_NAME, module=rw.module, consumed=len(rw.module.split(".")), symbol_path=rw.symbol_path)`.
  - Because the retry carries `rw.canonical` as its target, the existing `_symbol_lookup` full-string match (:461) finds the right node.
- Context split: `_resolve_context_target` (:666-679) splits the same way, into parse and `_context_target_from_parsed(target, parsed)`. `context_nodes(target)` gets the fallback (`entry="context"`), with siblings `context_nodes_exact(target)` and `context_nodes_rewritten(target, rw)`. On a fallback, the card heading and `display_target` show the canonical name. This affects only calls that fail today.

**`SymbolSourceService.source_with_items(target, *, package: str | None = None)`**: when `package` is set, the filter becomes `{"qualified_name": target, "package": package}` (symbol_source.py:100). There is no other change.

**`ToolRouter._resolve_source`** (tool_router.py:113-128):

- The explicit-project and single-service branches call `with_target_fallback(target, entry="source", resolver=svc.lookup.target_resolver, run_exact=lambda: svc.symbol_source.source_with_items(target), run_rewrite=lambda rw: svc.symbol_source.source_with_items(rw.canonical, package=PROJECT_PACKAGE_NAME))`.
- The multi-project branch passes both callables to `_resolve_by_recency`.

**Multi-project `_resolve_by_recency`** (multi_project_search.py:454-478). Its three call sites are :428, :449 and tool_router.py:125.

- New signature: `(run_exact: Callable[[ProjectServices], Awaitable[_T]], run_rewrite: Callable[[ProjectServices, TargetRewrite], Awaitable[_T]], *, target: str, entry: ResolutionEntry)`.
- **Pass 1:** the loop at :472-477, byte for byte, over `run_exact` (`lookup_exact`, `context_nodes_exact`, raw source). The first exact hit in any project wins.
- **Pass 2:** `resolutions = [(svc, await svc.lookup.target_resolver.resolve(target, entry=entry)) for svc in ordered]`, then the pure `decide_workspace_rewrite(resolutions)` decides:
  - **Resolve** only if exactly one project yields a rewrite, **and**:
    - for a bare target, the summed `exact_leaf_count` across all projects is 1;
    - no project scan was truncated.
  - On resolve, run `run_rewrite(svc, rw)`; on success, log with `"project": svc.project.name`.
  - A unique-in-A plus ambiguous-in-B case does **not** resolve.
  - **Otherwise** raise today's `'{target}' not found in any loaded project. [[next:search:{leaf}]]`, suffixed (per §3) with the merged candidates. Projects are merged in recency order; each entry reads `name (project p)`, and the list is deduplicated and capped. A workspace total of 2 or more uses the ambiguity sentence.

**Unchanged:** `get_overview`, `search_codebase`, `grep`/`glob`/`read_file`, `_defining_span`, and every raise site's message construction.

## 3. Contract verdict and exact strings

**Verdict: `docs/tool-contracts.md` needs no text change. No diff.**

- §1 (tool-contracts.md:21-44) freezes tool names, parameter schemas, the envelope, and vocabularies. Resolution semantics and error text are not frozen.
- The §3 grammar (:178-187) is syntactic, and every new target shape already passes `_TARGET_RE` (mcp_inputs.py:54-56).
- No contract sentence becomes false. §3 says project code "is addressed by its bare project-qualified name" (:181-184), and that stays true. The fallbacks only add addresses that used to error.
- Row 5 (:487), "Previously-erroring targets now resolve; no working call changes behavior", is the governing precedent. It is a closed 0.5.x → 0.6.0 migration record, so it needs no new row.
- **Why this is not the substitution ADR 0007 rejected** (docs/adr/0007-deterministic-routing-suggestions.md:113-119): that ADR rejected serving *another tool's* results in place of a contracted exact-string result (grep → semantic search). Here the same tool returns the same node the canonical name addresses. Rule 1 proves the equivalence exactly, and Rule 2 proves uniqueness. An exact target always wins (P1).
- Errors carry no envelope or meta (server.py:610-629 re-raises `MCPToolError`), so candidates can only appear in the error text.
- `meta.suggestion` is not touched (§2.3 covers three tools), and the `[suggestion:` prefix is not used.
- **Optional, not required:** the owner may later add a one-sentence §3 clarification through the ADR 0007 process. It is listed in §10, not in the owner decisions.

**The renderer:** `render_miss_message(message, res)`.

- If there are no candidates, or `miss_candidates` is off, it returns `message` unchanged.
- Otherwise it returns `message + joiner + sentence`:
  - `joiner = " "` when the message ends with `.` or `]]`, else `". "`;
  - `more = f" (+{total - shown} more)"` when `total > shown`, else `""`;
  - the sentence is `f"Ambiguous name '{leaf}' matches {total} indexed symbols: {', '.join(shown)}{more}."` when `res.ambiguous`,
  - or `f"Closest indexed names: {', '.join(shown)}{more}."` otherwise;
  - the multi-project ambiguity variant says `indexed symbols across projects:`, and each entry is `name (project p)`.

**Exact strings.** Every line below is computed from the verified bundle rows.

| Call | Today | After |
|---|---|---|
| `symbol src.needle.scoring.strategies.MaxSimScorer` | error :398 | resolves; body identical to `needle.scoring.strategies.MaxSimScorer` |
| `symbol MaxSimScorer` | `package 'MaxSimScorer' not indexed` | resolves to `needle.scoring.strategies.MaxSimScorer` |
| `symbol main` | `package 'main' not indexed` | `package 'main' not indexed. Ambiguous name 'main' matches 10 indexed symbols: examples.custom_retriever.main, examples.evaluate.main, examples.pipeline_demo.main, examples.quickstart.main, needle.cli.main (+5 more).` |
| `symbol score` | `package 'score' not indexed` | `package 'score' not indexed. Ambiguous name 'score' matches 3 indexed symbols: needle.scoring.strategies.CosineScorer.score, needle.scoring.strategies.MaxSimScorer.score, needle.scoring.strategies.ScoringStrategy.score.` (`needle_core.domain.ranking.Score` excluded by case) |
| `symbol needle.scoring.MaxSimScorer` (re-export) | `'needle.scoring.MaxSimScorer' not found in needle.scoring` | `'needle.scoring.MaxSimScorer' not found in needle.scoring. Closest indexed names: needle.scoring.strategies.MaxSimScorer.` (never rewritten) |
| `symbol maxsimscorer` (case only) | `package 'maxsimscorer' not indexed` | `package 'maxsimscorer' not indexed. Closest indexed names: needle.scoring.strategies.MaxSimScorer.` (never resolved) |
| `symbol MaxSimScorr --depth source` | `'MaxSimScorr' has no indexed source. [[next:search:MaxSimScorr]]` | `'MaxSimScorr' has no indexed source. [[next:search:MaxSimScorr]] Closest indexed names: needle.scoring.strategies.MaxSimScorer.` |
| `symbol md` / `symbol toml` | `package 'md' not indexed` | **unchanged** (no `.py` match) |
| `context scoring` | `no symbol matching 'scoring' found for context closure` | **unchanged** (the only exact match, `needle.scoring`, is module-only) |

Leaf-ratio notes: `score` vs `scoring` is 0.667, below the 0.75 cutoff, and `MaxSimScorr` vs `MaxSimScorer` is 0.957.

## 4. Structured log event

- **Logger:** `pydocs_mcp.application.target_resolution`.
- **Level:** INFO.
- **Emission:** exactly once, after the rewritten run succeeds. It follows the `suggestions.py:19, :36` pattern (`log.info(json.dumps({...}))`).
- **Payload, key order fixed:**

```json
{"event": "target_fallback_resolved", "entry": "lookup", "rule": "source_root_strip", "target": "src.needle.scoring.strategies.MaxSimScorer", "resolved": "needle.scoring.strategies.MaxSimScorer"}
```

- `entry` is one of `lookup|context|source`, and `rule` is one of `source_root_strip|unique_bare_name`.
- Multi-project pass 2 appends `"project": "<name>"`.
- A miss (with or without candidates), a retry miss, or an exact hit emits nothing.
- It deliberately does not use the `suggestion_fired` event or the suggestions logger (see OD-1).

## 5. Effects on other tools and the CLI

- **`get_symbol`:** the summary, tree and source depths agree. A resolved body is byte-identical to calling the canonical target directly.
- **`get_references`:** all five directions go through `lookup_with_items`, so they inherit the fallback. `meta.resolution` comes from the resolved node, as it does today.
- **`get_context`:** each batched target resolves independently. The heading shows the canonical name, and module-only rewrites and candidates are excluded.
- **CLI:** `symbol`, `refs`, `context`, the canonical subcommands and the deprecated `lookup` all route through `ToolRouter`, `LookupService` or `MultiProjectLookup`. A resolved call exits 0. A miss prints `Error: {exc}` (with candidates) and exits 1.
- **`search_codebase`:** its `src.`-prefixed member pointers now resolve through Rule 1; its output is unchanged.

## 6. Configuration (YAML only; no MCP parameter)

- **Model:** `TargetResolutionConfig(BaseModel)` in `retrieval/config/models.py`, next to `SymbolSourceConfig` (:384-395). It has `extra="forbid"` and follows the per-rule-flag shape of `SuggestionsConfig` (:420-434):

```python
_DEFAULT_TARGET_MAX_CANDIDATES = 5
_DEFAULT_TARGET_SIMILARITY_CUTOFF = 0.75

class TargetResolutionConfig(BaseModel):
    """get_symbol / get_context / get_references target fallbacks — one ablation flag per rule."""
    model_config = ConfigDict(extra="forbid")
    source_root_strip: bool = True
    unique_bare_name: bool = True
    miss_candidates: bool = True
    max_candidates: int = Field(_DEFAULT_TARGET_MAX_CANDIDATES, ge=1, le=20)
    candidate_similarity_cutoff: float = Field(_DEFAULT_TARGET_SIMILARITY_CUTOFF, ge=0.0, le=1.0)
```

- **`AppConfig`:** `target_resolution: TargetResolutionConfig = Field(default_factory=TargetResolutionConfig)` in app_config.py, next to `symbol_source` (:117).
- **`defaults/default_config.yaml`**, a new block after `symbol_source:` (:127-128):

```yaml
# Target-resolution fallbacks for get_symbol / get_context / get_references.
# Each runs only after the exact target misses; exact targets never change.
# One flag per rule so each can be ablated independently.
target_resolution:
  source_root_strip: true           # hypothesis: agents copy src.-prefixed names; stripping the one dropped source-root dir saves a retry turn
  unique_bare_name: true            # hypothesis: agents ask for a bare class/function name; resolving a provably unique one saves a search turn
  miss_candidates: true             # hypothesis: listing the closest indexed names in a miss lets the agent fix the target in one retry
  max_candidates: 5
  candidate_similarity_cutoff: 0.75
```

- **Wiring:** `build_sqlite_lookup_service` (factories.py:133-180) follows the `rg`/`impact_cfg` pattern at :160-162:
  - `tr_cfg = config.target_resolution if config is not None else TargetResolutionConfig()`;
  - `target_resolver=ProjectTargetResolver(uow_factory, tr_cfg)` when any of the three flags is on, else `NullTargetResolver()`.
  - The no-config path therefore wires the real resolver with the model defaults. No literal is re-encoded.
- **Not configurable:** `_SYMBOL_NAME_SCAN_LIMIT` stays a module constant. It is a memory and latency safety bound, not a quality knob, like the hardcoded caps in the ADR 0007 Appendix Class B.

## 7. Acceptance criteria

- **AC1 — Rule 1 on every surface.** On a src-layout index, `src.<pkg>.<mod>.<Cls>` returns the same text, items and meta as `<pkg>.<mod>.<Cls>` at summary and tree depth, at depth=source, in `get_references` (callers), and in `get_context`, whose heading shows the canonical name.
- **AC2 — module-only strip.** `src.<pkg>.<mod>` resolves to the module outline in `get_symbol`. `get_context` keeps today's message.
- **AC3 — Rule 1 guards.** The strip does not fire when:
  - `parts[0]` is an indexed package;
  - `is_stripped_source_root` fails (an unstripped `.py`, a `.md` module, a mismatched `source_path`, or two dropped segments).
- **AC4 — Rule 2 on every surface.** A bare name with exactly one eligible exact case-sensitive match, from a complete scan, resolves on all four surfaces. For `get_context`, the match must also be a non-module name.
- **AC5 — ambiguous bare name.** A bare name with 2 or more eligible matches raises `NotFoundError` with today's message, then the `Ambiguous name` sentence, sorted names and `(+N more)`. It never resolves. Case-variant names are excluded.
- **AC6 — non-code leaves.** Bare `md` and `toml` (a single `AGENTS.md` / `pyproject.toml` present) keep today's message byte for byte. So does a leaf of `__imports__`.
- **AC7 — unresolved misses.** Misses at :398, :430, :463, :672 and symbol_source.py:102-106 keep today's message as an exact byte prefix, plus at most `max_candidates` deterministic candidates that pass `is_symbol_target`. With no candidates, the message is `==` today's and it is the same exception object.
- **AC8 — re-exports.** The re-export shape (:463) is never rewritten; it only gains candidates.
- **AC9 — no regression on working targets.** Every target that resolves on origin/main returns byte-identical text, items and meta, with the resolver wired or Null. Dependency-over-`__project__` precedence is unchanged.
- **AC10 — pinned retry.** A rewrite always renders the `__project__` node, even when an indexed dependency is named like the project's top-level package.
- **AC11 — retry miss.** When a rewrite's retry misses, the error is the original message plus the candidates for the canonical, minus the canonical itself. The server never returns a wrong body.
- **AC12 — per-rule flags.**
  - With a flag off, only that rule stops: `source_root_strip` → shape-1 targets error as today; `unique_bare_name` → bare names error, but still carry candidates when `miss_candidates` is on; `miss_candidates` → no sentences are appended.
  - With all three off, the factory wires `NullTargetResolver` and every message is byte-identical to origin/main.
- **AC13 — multi-project.**
  - An exact hit in an older project beats a rewrite in a newer one.
  - A rewrite in two or more projects raises the ambiguity error.
  - Unique-in-A plus ambiguous-in-B raises the ambiguity error.
  - An all-miss keeps `[[next:search:` and appends merged, project-tagged candidates.
- **AC14 — logging.** Exactly one `target_fallback_resolved` JSON line per resolved fallback, with the exact keys in §4. None on a miss or an exact hit. Nothing on the suggestions logger.
- **AC15 — truncated scan.** A truncated scan (more than `_SYMBOL_NAME_SCAN_LIMIT` rows) never resolves and emits no candidates.
- **AC16 — frozen surface.** No change to MCP schemas, the envelope, the registration golden, TOOL_DOCS or `docs/tool-contracts.md`.
- **AC17 — CI gates.** The full gate set is green: ruff check and format, mypy, complexipy ≤ 15, vulture 80, coverage ≥ 90, `uv lock --check`, and the benchmarks tests.

## 8. Tests (test-first; AC mapping in brackets)

**Fakes (tests/_fakes.py):**

- `InMemoryChunkStore.list_symbol_names`, sorted and deduplicated.
- A named `FakeTargetResolver(resolution_by_target=…)` for the router and multi-project tests.
- `tests/application/_router_fakes.py`: `FakeLookup` (:65) and `FakeSymbolSource` (:116) gain `lookup_exact` / `lookup_rewritten`, a `package=` kwarg and `target_resolver`.
- `tests/application/test_error_empty_contract.py::_RaisingLookup` (:35) gets the same additions.

**New `tests/application/test_target_resolution.py`** (pure helpers plus `make_fake_uow_factory(chunks=…, packages=…)`):

- the `is_stripped_source_root` table: `src/…py`, `src/…/__init__.py`, no root, a `.md` module, backslashes, a mismatch, two segments [AC3];
- `is_resolvable_symbol_name`: `#` names, dot-leading names, `.md`/`.toml` rows, `__imports__`, module-only rows for `entry="context"` [AC6, AC2];
- exact-leaf case sensitivity (`score` vs `Score`) [AC5];
- Rule 3 ranking: shared trailing segments, a `MxaSimScorer` typo in the first four characters, a `maxsimscorer` case-only mismatch, the cap and `(+N more)` [AC7];
- `render_miss_message` joiners for `.`-, `]]`- and bare-ending messages, and the no-candidate identity [AC7];
- resolver cases: strip accepted, strip shadowed by a dependency, bare unique, bare ambiguous, truncated scan [AC1-5, AC15];
- `NullTargetResolver` [AC12];
- `with_target_fallback`:
  - the exact hit makes no resolver call;
  - a resolved fallback logs one JSON line (caplog plus `json.loads`, exact keys);
  - a retry miss raises the original message plus canonical-minus-self candidates;
  - a miss with no candidates re-raises the same exception object [AC11, AC14];
- `decide_workspace_rewrite`: single rewrite, two rewrites, unique-in-A plus ambiguous-in-B, truncated [AC13].

**New `tests/test_src_layout_resolution.py`.** It runs the real pipeline, following the `probe_db` recipe (tests/test_reference_probe_regressions.py:107-150), over:

- `src/srcpkg/__init__.py`, which re-exports `MaxSimScorer`;
- `src/srcpkg/scoring.py`, with `MaxSimScorer.score`, `CosineScorer.score` and a top-level `Score`;
- `src/srcpkg/cli.py` with `main`;
- `scripts/run.py` with `main`;
- `AGENTS.md` and `pyproject.toml`.

Services come from `build_sqlite_lookup_service`, `build_sqlite_symbol_source_service` and `ToolRouter`. Cases:

- all shapes at every depth, plus refs and context [AC1, AC2, AC4];
- ambiguous `main` and `score`, with `Score` excluded [AC5];
- bare `md` and `toml` unchanged [AC6];
- the typo and the re-export [AC7, AC8];
- every exact target, with the resolver wired and with it Null [AC9];
- a second fixture where an indexed dependency package is named `srcpkg`: the rewrite renders `__project__` [AC10];
- flags individually off, and all off [AC12].

**Other test files:**

- `tests/application/test_multi_project_search.py`: the four AC13 cases with two real or fake projects [AC13].
- `tests/retrieval/test_config.py`: the defaults, `extra=forbid`, the `target_resolution` block present in `default_config.yaml` with the same values, the factory wiring Null when every flag is off, and the real resolver when there is no config [AC12].
- `tests/storage/test_protocol_conformance.py` and the SQLite repository tests: `list_symbol_names` ordering, `DISTINCT`, the NULL/empty exclusion, and `limit` [AC15].
- `tests/test_cli.py`: `symbol MaxSimScorer`-shape exits 0; ambiguous `main` exits 1 with `Ambiguous name` [AC4, AC5].

**Existing tests that must stay green unedited** [AC9, AC16]:

- tests/test_reference_probe_regressions.py:248-259;
- tests/application/test_lookup_target.py;
- tests/application/test_lookup_service.py (Null default; `lookup_with_items` call sites :569-995);
- tests/application/test_error_empty_contract.py;
- tests/test_structured_envelope.py, tests/test_mcp_registration_snapshot.py, tests/test_mcp_surface_freeze.py.

## 9. Docs and CHANGELOG

- **CHANGELOG.md:** there is no `[Unreleased]` section (:1-8). Insert `## [Unreleased]` above `## [0.6.1] — 2026-09-10`:
  - `### Fixed` — "`get_symbol` / `get_context` / `get_references` now resolve targets prefixed with the source root, and unique bare project targets. `src.pkg.mod.Cls` resolves when the file lives under `src/`, and a bare `Cls` resolves when exactly one project code symbol has that name. Misses that remain list the closest indexed names in the error text. Targets that already resolved are unchanged, and an exact match in any loaded project still wins."
  - `### Added` — "`target_resolution.*` YAML block (`source_root_strip`, `unique_bare_name`, `miss_candidates`, `max_candidates`, `candidate_similarity_cutoff`), all rules on by default."
- **Unchanged:** docs/tool-contracts.md, descriptions.md, TOOL_DOCS, the README, and the registration goldens.

## 10. Risks and non-blocking follow-ups

- **R1 — miss-path cost.**
  - A Rule 1 miss costs up to `len(parts) - 1` indexed chunk probes.
  - Rules 2 and 3 cost one text-free `DISTINCT … ORDER BY` projection per miss, on `ix_chunks_package`.
  - Here that is 1,343 chunks. Latency at around 100k chunks is **unverified**. The 50k cap bounds memory, and a truncated scan degrades to today's message.
- **R2 — methods count toward bare names.** Ambiguity rises, so fewer names resolve. It is honest and never wrong.
- **R3 — v1 is `.py`-only.** Multilang (ADR 0021 T3) code symbols never resolve or appear as candidates, and non-code files never do either. How multilang module ids are shaped is **unverified**. Widening this is a follow-up.
- **R4 — dependency candidate pool.** The `qualified_name` shape of dependency chunks is **unverified**: no current local bundle has dependency chunks. The worst case is an empty pool, which leaves the message unchanged.
- **R5 — file size.** lookup_service.py is already 742 lines, over the 500-line guideline, and grows by about 40. All new logic lives in target_resolution.py. Splitting lookup_service.py is a separate pre-existing chore.
- **R6 — multi-branch.** The lookup path applies no branch filter today, and the resolver inherits that.
- **F1 — optional §3 contract clarification.** If the owner wants the behaviour documented, one sentence could follow tool-contracts.md:184, applied only through the ADR 0007 process:
  > "Where no exact match exists, a target prefixed with one stripped source-root directory, or a bare name matching exactly one project code symbol, resolves to that symbol. The candidate text in `NotFoundError` messages is not frozen, and exact targets always take precedence."
- **F2** — resolve re-exports through the IMPORTS edge (the :463 site).
- **F3** — strip more than one leading segment.
- **F4** — mention short names in the `get_symbol` description (this needs the goldens regenerated).

## Critique resolution

| # | Critic / severity | Finding | Verified? | Resolution |
|---|---|---|---|---|
| C1 | contract / major | One flag for three behaviours breaks ADR 0007 R7 per-rule flaggability | Yes: ADR 0007:21-24, :141-149; `SuggestionsConfig` per-rule precedent at models.py:420-434 | **Applied.** Three flags, each with a documented default and a YAML hypothesis (§6). The R7 "marked as harness-initiated" part is OD-1. Candidate text in errors is a Class R rendering convention (ADR 0007:236-240), not a new routing rule, so no ADR addendum is required. |
| C2 | contract / major | The log event cannot be seen by TraceRecorder, so calling traces "unaffected" was wrong | Yes: trace_recorder.py:49-68, :184-186; also found that merge.py:320-327 would **fail** a trace merge if the event reused `suggestion_fired` | **Applied.** Reworded (§4) and escalated as OD-1 with options (a) and (b) and their exact change sites. |
| C3 | contract / major | "No contract change" must address §3 and ADR 0007's rejected substitution | Yes: tool-contracts.md:181-187, :487; ADR 0007:113-119 | **Applied.** §3 argues the difference explicitly. The optional clarification is F1, which the owner may pick up. Verdict stays "no text change required". |
| C4 | contract / minor | The factory default re-encodes the flag default; the field and YAML defaults disagree | Yes: factories.py:160-162 shows the pattern; the design had `on or config is None` | **Applied.** `TargetResolutionConfig()` fallback; the Null field default is documented as test-only; a factory test pins the no-config wiring. |
| C5 | contract / minor | Candidates can fail `is_symbol_target` | Yes: mcp_inputs.py:65-75; bundle has `src.example_needle.egg-info.SOURCES.txt#L1-80` rows | **Applied.** One eligibility predicate for every pool (§2.4). |
| C6 | contract / minor | A truncated scan without `ORDER BY` is not deterministic | Yes: table_crud.py:55-60 | **Applied.** An ordered projection, and truncated scans emit no candidates (AC15). |
| C7 | contract / minor | lookup_service.py size; the generic `fallback` bool name | Yes: 742 lines | **Applied.** The wrapper lives in target_resolution.py; the bool parameter is replaced by distinctly named `lookup_exact` / `lookup_rewritten` / `context_nodes_exact` / `context_nodes_rewritten`; size noted as R5. |
| C8 | contract / minor | Candidate cap and cutoff are A/B-testable, so they belong in YAML | Yes: CLAUDE.md litmus test | **Applied.** `max_candidates` and `candidate_similarity_cutoff` are in YAML. The scan limit stays a safety constant, with a stated reason. |
| K1 | correctness / major | Bare `md` would resolve to AGENTS.md | Yes: bundle SQL shows `AGENTS.md` as the only symbol-valid `md` leaf; live CLI shows `symbol AGENTS.md` rc=0 and `symbol md` rc=1 | **Applied.** `.py` source requirement plus the `__imports__` exclusion; AC6 plus tests. |
| K2 | correctness / major | The retry re-parses a rewritten string, so a same-named dependency wins | Yes: lookup_service.py:161-172; benchmarks/pyproject.toml:70/:111/:128; old local bundle has a `pydocs_mcp` package row. Dependency tree module ids still unverified. | **Applied.** P2: a pinned `LookupTarget`, a pinned context target, and `source_with_items(…, package="__project__")`; AC10 plus a fixture. |
| K3 | correctness / major | Multi-project pass 2 resolves a name that is ambiguous across the workspace | Yes: follows from the prior design text (no code yet) | **Applied.** The workspace-wide `exact_leaf_count` must be exactly 1, and no scan truncated; AC13 plus a test. |
| K4 | correctness / minor | `LIMIT` with no `ORDER BY` and an unindexed `%x%` scan with `SELECT *` | Yes: table_crud.py:55-60, filter_adapter.py:67-74, db.py:171-172 | **Applied.** A `ChunkStore.list_symbol_names` projection (§2.3); latency at scale noted as unverified (R1). |
| K5 | correctness / minor | get_context would be told to retry with a module-only canonical | Yes: bundle shows bare `scoring` matches only `needle.scoring`; lookup_service.py:670-672; live `context scoring` rc=1 | **Applied.** An `entry`-aware resolver drops module-only rewrites and candidates for context; the retry miss excludes the canonical itself (AC11). |
| K6 | correctness / minor | The 4-character prefix probe misses early typos; case-only is borderline at 0.75 | Yes: difflib gives `MxaSimScorer` 0.917 and `maxsimscorer`/`MaxSimScorer` exactly 0.75 | **Applied.** Casefolded ratio over the full leaf projection, with no prefix probe; tests for both cases. |

None of the findings was rejected.
