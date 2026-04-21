# Sub-PR #6 — MCP surface consolidation + Pydantic at boundary

**Status:** Approved via brainstorm 2026-04-20.
**Date:** 2026-04-20
**Depends on:** sub-PR #4 (merged — `PackageLookupService` / `SearchDocsService` / `SearchApiService` / `ModuleIntrospectionService`). Optionally consumes sub-PR #5 (`DocumentTreeService`) and sub-PR #5b (`ReferenceService`) when they land; no hard blocker.
**Follows-on:** sub-PR #6b (query-parser DSL), sub-PR #7 (error-tolerance primitives).

**⚠️ Sub-PR #4 Protocol amendment (post-merge, additive):** `PackageLookupService.find_module(package: str, module: str) → bool` added — returns `True` iff at least one indexed `Chunk` exists for that `(package, module)`. New method, ~10 LOC, backward-compatible. Used by `LookupService._longest_indexed_module` to resolve dotted-path targets without requiring sub-PR #5's `DocumentTreeService`. See §6.4.

> ## ⚠️ BREAKING CHANGE — read first
>
> **MCP tool surface reduced from 5 tools to 2.** Removed: `list_packages`, `get_package_doc`, `search_docs`, `search_api`, `inspect_module`. Added: `search`, `lookup`.
> **CLI subcommands renamed:** `pydocs-mcp query` / `pydocs-mcp api` → `pydocs-mcp search` / `pydocs-mcp lookup`.
> **`inspect_module`'s live-import feature is dropped** — no shim. Can be re-added later as a distinct `live_inspect` tool if real demand surfaces.
> MCP clients that hardcoded pre-#6 tool names break on upgrade and must update.

---

## 1. Goal

Consolidate the MCP surface from 5 (→ 10 after #5/#5b land) separate tools into **2 purpose-distinct tools** — `search` and `lookup` — with typed Pydantic inputs, structured errors, and LLM-optimized descriptions. Addresses three problems:

1. **Tool-selection errors** — LLMs confuse semantically-close tools (search_docs vs search_api; get_callers vs get_references_to). Each added tool degrades selection accuracy.
2. **Input validation** — current handlers accept raw strings with no format checks; malformed inputs leak to the pipeline or silently coerce.
3. **Error swallowing** — `try/except Exception: return "No matches found."` hides real failures behind success-with-empty-result strings, leaving operators blind to bugs.

## 2. Out of scope (deferred)

| Item | Why deferred |
|---|---|
| Query-parser mini-DSL (`"x package:y scope:project"`) | Orthogonal to the validation/consolidation work. Split into **sub-PR #6b** when demand appears. |
| Full Pydantic output (JSON-only responses) | Breaks LLM UX — markdown success payloads are deliberately preserved. Structured payloads only for the typed-error path. |
| Live-import replacement for `inspect_module` | Can be added as a new `live_inspect` tool after if users ask. Not this PR. |
| Error-tolerance primitives (`TryStage` etc.) | **Sub-PR #7**. |
| Output-format hybrid (JSON for structured tools) | Would fracture the "prose-tool UX" invariant. Not needed when `lookup` returns rendered markdown from the tree/refs services. |

## 3. Key decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | **Scope = validation + typed errors** (option B from brainstorm) | Fixes the two biggest gaps without breaking the markdown-response UX. |
| 2 | **Consolidated to 2 tools: `search` + `lookup`** (option Z5 from brainstorm) | Minimizes LLM tool-selection error. Clean "text-ish query → `search`, named target → `lookup`" mental model. |
| 3 | **`lookup` absorbs everything structural** — package overview, module tree, package arborescence, call graph, inheritance | One tool for "I know the target; show me something about it." Target string + `show` discriminator handles all modes. |
| 4 | **`inspect_module` dropped — no replacement** | Live-import is a distinct operation from indexed lookup; not worth conflating. Can be re-added later if wanted. |
| 5 | **Tool descriptions are LLM-first: concise, concrete examples, cross-references** | Tool selection is prompt engineering; descriptions ARE the prompt. Every word counts. |
| 6 | **Enforce format, not arbitrary length.** Protocol-safety caps set 2-3 orders of magnitude above expected use | Avoids false-positive rejections while still protecting against runaway clients. |
| 7 | **`kind="any"` uses a dedicated `unified_search.yaml` preset pipeline** with internal RRF fusion | Correctly leverages sub-PR #2's `ParallelRetrievalStage` + `ReciprocalRankFusionStage`. Handler never stitches pre-rendered outputs. |
| 8 | **Typed exception hierarchy: 3 classes** (`InvalidArgumentError`, `NotFoundError`, `ServiceUnavailableError`) under `MCPToolError` | Minimal taxonomy matching JSON-RPC error codes. Not over-engineered. |
| 9 | **`search` empty-result = success, `lookup` missing-target = error** | Semantic difference: search is probabilistic; lookup asserts a specific target. Mapping to protocol matches user intent. |
| 10 | **Soft dependencies on #5 and #5b services** (option B from brainstorm) | `LookupService` accepts `tree_svc: DocumentTreeService | None = None` and `ref_svc: ReferenceService | None = None`. When `None`, the corresponding `show` values raise `ServiceUnavailableError`. #6 ships whenever its own code is ready — features light up progressively. |

## 4. MCP surface — 2 tools

### 4.1 Tool descriptions (LLM-visible)

These descriptions are **production copy** — not documentation. Written for tool-selection quality under LLM inference.

```
search(query, kind="any", package="", scope="all", limit=10)

Full-text search over indexed docs and code (BM25 ranked).

Use when the user describes a topic or keyword, not a specific target.

Params:
  query:   search terms (space-separated)
  kind:    "docs" (prose/README) | "api" (functions/classes) | "any" (default)
  package: restrict to one package (e.g. "fastapi"); "" = all; "__project__" = your code
  scope:   "project" | "deps" | "all" (default)
  limit:   1–1000, default 10

Examples:
  search(query="batch inference", kind="docs")
  search(query="HTTPBasicAuth", kind="api")
  search(query="retry logic", package="requests")
  search(query="parser", scope="project")

For a specific known target (package, module, class, method), use lookup.
```

```
lookup(target="", show="default")

Navigate to a specific named package/module/symbol; show its info or references.

Use when the user names an exact target.

Params:
  target: dotted path
    ""                                      → list all indexed packages
    "fastapi"                               → package overview + deps
    "fastapi.routing"                       → module tree
    "fastapi.routing.APIRouter"             → class + children
    "fastapi.routing.APIRouter.include_router"  → method details
  show: "default" | "tree" (full subtree)
        | "callers" (who calls this)
        | "callees" (what this calls)
        | "inherits" (base classes)

Examples:
  lookup(target="")
  lookup(target="fastapi.routing.APIRouter")
  lookup(target="fastapi.routing.APIRouter.include_router", show="callers")
  lookup(target="requests.auth.HTTPBasicAuth", show="inherits")

For keyword/topic search, use search.
```

### 4.2 Mapping of pre-#6 tools to post-#6

| Pre-#6 | Post-#6 equivalent |
|---|---|
| `list_packages()` | `lookup(target="")` |
| `get_package_doc(package="fastapi")` | `lookup(target="fastapi")` |
| `search_docs(query="x")` | `search(query="x", kind="docs")` |
| `search_api(query="x")` | `search(query="x", kind="api")` |
| `inspect_module(...)` | **dropped** (feature removed) |
| `get_document_tree(pkg, mod)` (planned #5) | `lookup(target="pkg.mod")` |
| `get_package_tree(pkg)` (planned #5) | `lookup(target="pkg", show="tree")` |
| `get_callers(pkg, node_id)` (planned #5b) | `lookup(target="pkg.X.y", show="callers")` |
| `get_callees(pkg, node_id)` (planned #5b) | `lookup(target="pkg.X.y", show="callees")` |
| `get_references_to(name)` (planned #5b) | **Dropped as MCP tool in #6.** `ReferenceService.find_by_name` stays in the codebase (unused-from-MCP) until a future PR reinstates it; see §4.6. |

### 4.3 Pydantic input models

```python
# application/mcp_inputs.py
import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator

_PACKAGE_RE = re.compile(r"^(?:[a-zA-Z0-9][a-zA-Z0-9._-]*|__project__)$")
_TARGET_RE = re.compile(r"^(?:[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)?$")  # empty or dotted-identifier chain; rejects `foo..bar`, `foo.`, starting digit


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=30000)
    kind: Literal["docs", "api", "any"] = "any"
    package: str = ""
    scope: Literal["project", "deps", "all"] = "all"
    limit: int = Field(default=10, ge=1, le=1000)

    @field_validator("package")
    @classmethod
    def _check_package(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError("package must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ or be '__project__'")
        return v


class LookupInput(BaseModel):
    target: str = ""
    show: Literal["default", "tree", "callers", "callees", "inherits"] = "default"

    @field_validator("target")
    @classmethod
    def _check_target(cls, v: str) -> str:
        if v and not _TARGET_RE.match(v):
            raise ValueError("target must be a dotted identifier like 'pkg.mod.Class.method' or empty")
        return v
```

### 4.4 Principle codified

> **Enforce format, not arbitrary length — with protocol-safety caps set very permissively (2-3 orders of magnitude above expected use).** Limits guard against concrete failure modes (format-mismatch bugs, DoS from runaway clients). They don't substitute for downstream safeguards that already exist (`TokenBudgetFormatterStage` caps payloads; SQLite FTS5 caps query complexity).

### 4.5 Output contract

- **All success outputs stay markdown strings.** Preserves LLM-reading UX — no client-side JSON parsing needed to quote results.
- **Error responses use FastMCP's built-in error channel** (JSON-RPC error codes). Clients receive structured errors via the protocol, not embedded in the success string.
- **"No matches found."** style strings are **reserved for the empty-but-successful `search` case** only. `lookup` never returns such strings for missing targets — it raises `NotFoundError`.

### 4.6 `get_references_to` handling (design note)

`get_references_to(name)` in sub-PR #5b's design returned references targeting an arbitrary textual `name` (e.g., stdlib function calls with `to_node_id == NULL`). Under #6's consolidation:

- **Default:** dropped as an MCP tool. Indexed references flow through `lookup(target="pkg.X.y", show="callers")` or `show="callees"`.
- **If user needs "search refs by name":** add as a variant of `search` — e.g., `search(query="os.path.join", kind="refs")` — in a follow-on PR. Not in #6 initial scope.

## 5. Exception hierarchy + error policy

### 5.1 Exception classes

```python
# application/mcp_errors.py
class MCPToolError(Exception):
    """Base — every handler-raised error inherits from this. Default MCP error code -32000."""

class InvalidArgumentError(MCPToolError):
    """Semantic validation failure — input parsed but domain-invalid.
    (Pydantic ValidationError covers schema-level failures; this is for post-parse checks.)
    MCP error code -32602.
    """

class NotFoundError(MCPToolError):
    """Specified target doesn't exist in the index. MCP error code -32000.
    Raised by `lookup` for unknown packages/modules/symbols.
    NOT raised by `search` — empty search returns success with empty result.
    """

class ServiceUnavailableError(MCPToolError):
    """Backend raised an unexpected error (SQLite, pipeline, missing optional service).
    MCP error code -32000.
    """
```

### 5.2 Handler error policy (enforced on every `@mcp.tool()`)

```python
@mcp.tool()
async def search(payload: SearchInput) -> str:
    try:
        return await _do_search(payload)
    except MCPToolError:
        raise                                # typed errors pass through to FastMCP
    except Exception as e:
        log.exception("search failed unexpectedly")
        raise ServiceUnavailableError(f"search failed: {e}") from e
```

Rule: **NO blanket `try/except Exception: return "No matches found."` patterns remain in `server.py`.** Anywhere a handler catches `Exception`, it must re-raise as a typed `MCPToolError`. This is enforceable via a ruff/grep check in AC #15.

### 5.3 Error code mapping

| Exception | JSON-RPC code | Use |
|---|---|---|
| Pydantic `ValidationError` | `-32602` Invalid params | Schema-level input failures (wrong type, regex mismatch, out of range) |
| `InvalidArgumentError` | `-32602` Invalid params | Post-parse semantic failures |
| `NotFoundError` | `-32000` Server error | Target not indexed |
| `ServiceUnavailableError` | `-32000` Server error | Backend bug / missing optional service |

FastMCP (`mcp>=1.0`) maps uncaught exceptions to `-32000` by default and surfaces Pydantic `ValidationError` via its own path (code `-32602`). Per-subclass error-code discrimination for the `MCPToolError` hierarchy is **not guaranteed by FastMCP's current API** — implementation must verify during the planning / coding phase whether FastMCP exposes hooks for custom error codes, or if the plan needs to map each `MCPToolError` subclass to an MCP response shape manually (e.g., by catching in a middleware and re-raising with the desired code). Fallback: all `MCPToolError` subclasses surface as `-32000` with the exception's `str(exc)` in `data.message`, differentiating via message text. The acceptance criteria (#12–#17) name codes expected under either strategy; if FastMCP doesn't discriminate, the test suite asserts message content instead.

## 6. `LookupService` — target dispatch

### 6.1 Placement

New file: `python/pydocs_mcp/application/lookup_service.py` (~150 LOC). Owns the target-string parsing and dispatch to backing services.

### 6.2 Constructor (soft dependencies — decision #10)

```python
class LookupService:
    def __init__(
        self,
        package_lookup: PackageLookupService,          # required — from sub-PR #4
        tree_svc: "DocumentTreeService | None" = None, # optional — from sub-PR #5
        ref_svc: "ReferenceService | None" = None,     # optional — from sub-PR #5b
    ) -> None:
        self._package = package_lookup
        self._tree = tree_svc
        self._refs = ref_svc
```

**Degraded-mode policy (authoritative — §6.5 implements):**
- `tree_svc is None` → `show="tree"` raises `ServiceUnavailableError`.
- `ref_svc is None` → `show="callers"` / `show="callees"` raise `ServiceUnavailableError`.
- `show="inherits"` **does NOT require `ref_svc`**; it reads `DocumentNode.extra_metadata["inherits_from"]` via `tree_svc`. Only raises `ServiceUnavailableError` when BOTH `tree_svc` AND `ref_svc` are `None`. See §6.5.

### 6.3 Dispatch algorithm

```python
async def lookup(self, payload: LookupInput) -> str:
    target = payload.target
    show = payload.show

    # 1. Empty target → list packages
    if not target:
        return await self._list_packages()

    parts = target.split(".")
    package = parts[0]

    # 2. Package-only lookup
    if len(parts) == 1:
        return await self._package_lookup(package, show)

    # 3. Resolve longest module prefix
    module = await self._longest_indexed_module(package, parts)
    if module is None:
        raise NotFoundError(f"no module matching '{target}' found under '{package}'")

    # 4. Remaining segments = symbol path (can be empty → module-level lookup)
    symbol_path = parts[len(module.split(".")):]

    # 5. Module-only lookup
    if not symbol_path:
        return await self._module_lookup(package, module, show)

    # 6. Symbol lookup
    return await self._symbol_lookup(package, module, target, show)
```

### 6.4 `_longest_indexed_module` resolution

```python
async def _longest_indexed_module(self, package: str, parts: list[str]) -> str | None:
    # Walk from longest-prefix to shortest, checking module-existence.
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        # Prefer document_trees lookup when sub-PR #5 is wired (sees newly-extracted modules).
        if self._tree is not None:
            tree = await self._tree.get_tree(package, candidate)
            if tree is not None:
                return candidate
        # Fallback (works pre-#5 too): probe the chunks table via PackageLookupService.find_module.
        if await self._package.find_module(package, candidate):
            return candidate
    return None
```

Disambiguates `pkg.FooBar` — `FooBar` could be a module file OR a class inside `pkg.__init__.py`. We prefer the module interpretation when a tree row exists OR chunks reference that module path; else fall back to class-lookup.

**Sub-PR #4 Protocol amendment** (post-merge, additive): `PackageLookupService.find_module(package: str, module: str) → bool` returns `True` iff at least one indexed `Chunk` exists with that exact `(package, module)` metadata. Implementation: `SELECT 1 FROM chunks WHERE package = ? AND module = ? LIMIT 1`. New method, ~10 LOC. Backward-compatible — no caller of #4 breaks.

### 6.5 Degraded-mode behavior for `show="inherits"`

When `ref_svc is None`, `lookup(target="pkg.X", show="inherits")` reads the target class's **`DocumentNode.extra_metadata["inherits_from"]`** directly — the tree node carries the same `inherits_from` list that is mirrored into `Chunk.extra_metadata` (per sub-PR #5 §4.4). No `ChunkStore` dependency required; `LookupService` already has `tree_svc` wired for the target-resolution path. When `tree_svc` is ALSO `None`, `show="inherits"` raises `ServiceUnavailableError`.

**`show="inherits"` on non-class targets:** raises `InvalidArgumentError` — "inherits only applies to CLASS nodes, got {kind}". The tool description (§4.1) documents this constraint.

This is deliberate: inheritance metadata is small and useful standalone; we don't want users to lose it just because #5b isn't merged yet.

## 7. Wiring — `server.py` startup additions

```python
# server.py run() — constructed at startup, injected into handlers
from pydocs_mcp.application import (
    LookupService,        # NEW — sub-PR #6
    PackageLookupService, # from #4
    SearchDocsService,    # from #4
    SearchApiService,     # from #4
)

# Optional services — present only if their sub-PR has landed.
# Each import is guarded because #5 / #5b may not be merged yet at #6's ship time.
# Note: sub-PR #5 / #5b specs must explicitly export their service from `application/__init__.py`
# for this to resolve — added to their respective ACs as part of #6's amendments.
try:
    from pydocs_mcp.application import DocumentTreeService  # from #5
    tree_svc = DocumentTreeService(tree_store) if tree_store is not None else None
except ImportError:
    tree_svc = None

try:
    from pydocs_mcp.application import ReferenceService     # from #5b
    ref_svc = ReferenceService(reference_store) if reference_store is not None else None
except ImportError:
    ref_svc = None

lookup_svc = LookupService(
    package_lookup=package_lookup,
    tree_svc=tree_svc,
    ref_svc=ref_svc,
)

# Unified search pipeline (NEW — presets/unified_search.yaml)
unified_pipeline = build_unified_pipeline_from_config(config, context)
```

## 8. `unified_search.yaml` preset

New file: `python/pydocs_mcp/presets/unified_search.yaml`:

```yaml
name: unified_search
description: Merges BM25 chunk results and LIKE module-member results via RRF.
stages:
  - type: ParallelRetrievalStage
    retrievers:
      - type: Bm25ChunkRetriever
      - type: LikeMemberRetriever
  - type: ReciprocalRankFusionStage
  - type: TokenBudgetFormatterStage
```

Uses existing stages from sub-PR #2. Zero new stages, zero new retrievers. The `search(kind="any")` handler invokes this pipeline; `kind="docs"` uses the existing chunk pipeline; `kind="api"` uses the existing member pipeline.

## 9. `search` handler implementation

```python
@mcp.tool()
async def search(payload: SearchInput) -> str:
    try:
        return await _do_search(payload)
    except MCPToolError:
        raise
    except Exception as e:
        log.exception("search failed unexpectedly")
        raise ServiceUnavailableError(f"search failed: {e}") from e


async def _do_search(payload: SearchInput) -> str:
    query = _build_search_query(payload)   # translates Pydantic input → SearchQuery
    match payload.kind:
        case "docs":
            response = await search_docs_svc.search(query)
            return _render_search_response(response, empty_msg="No matches found.")
        case "api":
            response = await search_api_svc.search(query)
            return _render_search_response(response, empty_msg="No symbols found.")
        case "any":
            response = await unified_pipeline.run(query)
            return _render_search_response(response, empty_msg="No matches found.")
```

`_build_search_query` centralizes the `SearchQuery` construction that sub-PR #4 split across `_build_chunk_query` + `_build_member_query` (one unified helper now; `scope` semantics the same).

**Filter-field unification for `kind="any"`:** A single `SearchQuery.pre_filter` drives both `Bm25ChunkRetriever` and `LikeMemberRetriever`. This works because per sub-PR #1 §5 canonical data model, `ChunkFilterField.PACKAGE.value == ModuleMemberFilterField.PACKAGE.value == "package"` and `ChunkFilterField.SCOPE.value == ModuleMemberFilterField.SCOPE.value == "scope"` — the filter-key strings are deliberately shared across the two field enums. The only divergence (`ChunkFilterField.TITLE` vs `ModuleMemberFilterField.NAME`) doesn't apply to `kind="any"` queries, which are driven by `query` + `package` + `scope` only (no topic/name filter).

If sub-PR #1 §5's canonical enums ever drift apart on those shared keys, the `unified_search.yaml` preset breaks. **Invariant to preserve:** `ChunkFilterField.PACKAGE/SCOPE` values match their `ModuleMemberFilterField` counterparts. Added as an invariant check in acceptance (see AC #25 below).

## 10. File map

| File | Action |
|---|---|
| `python/pydocs_mcp/server.py` | Rewrite — 5 handlers → 2. Pydantic input models in signatures, typed-error handling. ~150 LOC. |
| `python/pydocs_mcp/application/mcp_inputs.py` | NEW — `SearchInput`, `LookupInput`. ~50 LOC. |
| `python/pydocs_mcp/application/mcp_errors.py` | NEW — exception hierarchy. ~30 LOC. |
| `python/pydocs_mcp/application/lookup_service.py` | NEW — `LookupService` + dispatch. ~150 LOC. |
| `python/pydocs_mcp/application/__init__.py` | Export `LookupService`, `SearchInput`, `LookupInput`, `MCPToolError` subclasses. |
| `python/pydocs_mcp/presets/unified_search.yaml` | NEW preset. ~15 LOC. |
| `python/pydocs_mcp/retrieval/config.py` | Add `build_unified_pipeline_from_config(config, context)` factory — mirrors existing `build_chunk_pipeline_from_config` / `build_member_pipeline_from_config` shape. ~20 LOC. |
| `python/pydocs_mcp/__main__.py` | CLI subcommands: remove `query`, `api`; add `search`, `lookup` with argparse flags `--kind`, `--package`, `--scope`, `--limit` (search) and `--show` (lookup). Preserve `index`, `serve`. |
| `tests/test_mcp_surface.py` | NEW — golden-fixture success paths + typed-error failure paths. ~300 LOC. |
| `tests/test_server.py` (if exists pre-#6) | **Delete or rewrite** — pre-#6 handler tests no longer applicable; replaced by `test_mcp_surface.py`. |

**Total delta:** ~850 LOC.

## 11. CLI amendments

Sub-PR #6 renames user-facing CLI subcommands:

| Old | New |
|---|---|
| `pydocs-mcp query "x"` | `pydocs-mcp search "x"` |
| `pydocs-mcp api predict -p vllm` | `pydocs-mcp search "predict" --kind=api --package=vllm` |
| — | `pydocs-mcp lookup fastapi.routing.APIRouter` |
| — | `pydocs-mcp lookup fastapi.routing.APIRouter --show=callers` |
| `pydocs-mcp index .` | unchanged |
| `pydocs-mcp serve .` | unchanged |

No backward-compat aliases for the renamed commands. Users see a clear "command not found" error; `--help` lists the new names.

## 12. Amendments to sub-PR #5 spec

Forward-pointer added to #5's top matter:

> **Note:** Tree retrieval is exposed to MCP clients via sub-PR #6's unified `lookup(target=..., show="tree")` tool — **not** as dedicated `get_document_tree` / `get_package_tree` MCP tools. The `DocumentTreeService` from this sub-PR remains and backs `LookupService`. The CLI exposes tree browsing via `pydocs-mcp lookup`, not `pydocs-mcp tree`.

Removals in #5:
- §13.1 `@mcp.tool() get_document_tree` / `get_package_tree` definitions.
- §13.2 dedicated `pydocs-mcp tree` CLI subcommand (superseded by `lookup`).
- AC #2 (MCP tool `get_document_tree` exists), AC #3 (`pydocs-mcp tree` CLI subcommand).

Additions to #5 ACs:
- New AC: `application/__init__.py` explicitly re-exports `DocumentTreeService` so `from pydocs_mcp.application import DocumentTreeService` resolves at #6's wiring time.

Untouched in #5:
- All extraction / chunker / `DocumentNode` / `document_trees` table work.
- `DocumentTreeService` in `application/`.

## 13. Amendments to sub-PR #5b spec

Forward-pointer added to #5b's top matter:

> **Note:** Reference-graph queries are exposed to MCP clients via sub-PR #6's unified `lookup(..., show="callers"|"callees"|"inherits")`. `ReferenceService` from this sub-PR remains and backs `LookupService`. No dedicated `get_callers` / `get_callees` / `get_references_to` MCP tools ship.

Removals in #5b:
- §8.2 `@mcp.tool() get_callers` / `get_callees` / `get_references_to` definitions.
- AC #14 MCP tool count constraint.

Additions to #5b ACs:
- New AC: `application/__init__.py` explicitly re-exports `ReferenceService` so `from pydocs_mcp.application import ReferenceService` resolves at #6's wiring time.

Untouched in #5b:
- `ReferenceKind`, `NodeReference`, `ReferenceStore`, `ReferenceCollector`, `ReferenceResolver`, `ReferenceService`.
- `node_references` table.

## 13b. Amendments to sub-PR #4 spec

Sub-PR #4 is already **merged to main** — amendments here are post-merge additive edits (sub-PR #4 spec file + codebase).

Forward-pointer added to #4's top matter:

> **Note (added by sub-PR #6):** `PackageLookupService` gains a new method `find_module(package: str, module: str) → bool` — returns `True` iff at least one indexed `Chunk` has that `(package, module)` metadata. Implementation: `SELECT 1 FROM chunks WHERE package = ? AND module = ? LIMIT 1`. Backward-compatible — no existing caller breaks. Used by sub-PR #6's `LookupService._longest_indexed_module` to resolve dotted-path targets when `tree_svc` is unavailable.

Additions to #4:
- In §5.1 (`PackageLookupService` class), add `find_module(package, module) -> bool` with the implementation note above.
- Add AC: `find_module("requests", "requests.auth")` returns `True` when chunks exist for that module; `False` otherwise.
- `application/__init__.py` re-exports unchanged (no new class — only a method added).

Untouched in #4:
- Existing services (`SearchDocsService`, `SearchApiService`, `ModuleIntrospectionService`).
- Existing Protocols + adapters.
- All other ACs.

**Implementation note:** this spec edit to #4 is part of #6's PR. Sub-PR #4's spec file gets a commit in the same #6 changeset, alongside the new `find_module` code in `application/package_lookup_service.py`.

## 14. Acceptance criteria

| # | Criterion |
|---|---|
| 1 | `server.py` exposes exactly **2** `@mcp.tool()` handlers: `search`, `lookup`. |
| 2 | `search(query="batch inference", kind="docs")` returns byte-identical output to pre-#6 `search_docs("batch inference")` (golden fixture). |
| 3 | `search(query="HTTPBasicAuth", kind="api")` returns byte-identical output to pre-#6 `search_api("HTTPBasicAuth")`. |
| 4 | `search(query="parser", kind="any")` executes the `unified_search.yaml` pipeline once and returns RRF-merged chunks + members. Total latency ≈ max(chunk_latency, member_latency), not sum. |
| 5 | `lookup(target="")` returns the packages list (byte-identical to pre-#6 `list_packages()`). |
| 6 | `lookup(target="fastapi")` returns the package doc (byte-identical to pre-#6 `get_package_doc("fastapi")`). |
| 7 | With `tree_svc` wired: `lookup(target="fastapi.routing")` returns the module tree as rendered text. |
| 8 | With `tree_svc=None` (pre-#5): `lookup(target="fastapi.routing", show="tree")` raises `ServiceUnavailableError`. |
| 9 | With `ref_svc` wired: `lookup(target="pkg.X.y", show="callers")` returns caller references as rendered text. |
| 10 | With `ref_svc=None` (pre-#5b): `lookup(..., show="callers")` raises `ServiceUnavailableError`. |
| 11 | With `ref_svc=None` but `tree_svc` wired: `lookup(target="pkg.X", show="inherits")` still works by reading `Chunk.extra_metadata["inherits_from"]` (degraded mode per §6.5). |
| 12 | `search(query="", ...)` raises Pydantic `ValidationError` → MCP error code `-32602`. |
| 13 | `search(query="x", package="Has Spaces")` raises `ValidationError` via package regex → `-32602`. |
| 14 | `lookup(target="has spaces!")` raises `ValidationError` via target regex → `-32602`. |
| 15 | `lookup(target="nonexistent.pkg")` raises `NotFoundError` → MCP error `-32000` with message naming the missing segment. |
| 16 | `lookup(target="fastapi.nonexistent.mod")` raises `NotFoundError` identifying which segment failed to resolve. |
| 17 | A simulated `sqlite3.DatabaseError` inside the pipeline propagates as `ServiceUnavailableError` → MCP error `-32000`. |
| 18 | `search(query="zzz_no_matches", kind="api")` is **success**, not error — returns `"No symbols found."`. Protocol-distinguishable from #12-#17. |
| 19 | Zero `try/except Exception: return "..."` patterns remain in `server.py` handlers. Enforced via ruff/grep in CI. |
| 20 | `LookupService` test coverage: each dispatch branch (empty target, package-only, module-only, symbol-only, each `show` variant, degraded-mode for missing services) has ≥1 test. |
| 21 | CLI: `pydocs-mcp search "x"` and `pydocs-mcp lookup "pkg.mod.X"` work and print the same rendered output as their MCP tool counterparts. |
| 22 | Sub-PR #5 spec carries a forward-pointer to #6 in its §1 top-matter; AC #2 and AC #3 are rewritten to say "Superseded by sub-PR #6"; §13.1 section body replaced with a forward-pointer (content moved to #6). |
| 23 | Sub-PR #5b spec carries a forward-pointer to #6 in its §1 top-matter; AC #14 is rewritten to say "Superseded by sub-PR #6"; §8.2 section body replaced with a forward-pointer (content moved to #6). |
| 24 | Tool descriptions in `server.py` use the production copy from §4.1 verbatim — no paraphrasing at implementation time. |
| 25 | Invariant check: `ChunkFilterField.PACKAGE.value == ModuleMemberFilterField.PACKAGE.value` and `ChunkFilterField.SCOPE.value == ModuleMemberFilterField.SCOPE.value`. Unit test asserts equality so the unified `SearchQuery` → dual-retriever dispatch stays sound. |
| 26 | `PackageLookupService.find_module(package, module)` returns True for an indexed `(pkg, mod)` and False otherwise. Added to sub-PR #4's service (post-merge, additive). ~10 LOC. Used by `LookupService._longest_indexed_module`. |
| 27 | Sub-PR #4 spec file (`2026-04-19-sub-pr-4-query-application-services-design.md`) is amended in this PR: §5.1 `PackageLookupService` gains `find_module` method definition; top-matter gets a "post-merge amendment" banner; corresponding AC added. Diff committed alongside the new `find_module` code. |
| 28 | Sub-PR #5 spec file is amended to add an AC requiring `application/__init__.py` to re-export `DocumentTreeService`. Sub-PR #5b spec file similarly amended for `ReferenceService`. Both are edits within this PR's changeset. |

## 15. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Breaking MCP tool surface breaks existing clients | **High — intentional** | Documented prominently (breaking-change banner at §1). PR body + CHANGELOG call out the rename. No compat shim — clients adapt once. |
| LLM tool selection fails more often with 2 tools than 5 | Low | Each of the 2 tools is obviously-distinct ("text search" vs "named target lookup"). Descriptions cross-reference for ambiguous cases. |
| `LookupService` dispatch ambiguous on `pkg.FooBar` (module vs class) | Low | §6.4 resolves via `_longest_indexed_module`: prefers module interpretation when a `document_trees` row exists; falls through to class-lookup otherwise. Deterministic. |
| `inspect_module` users lose functionality with no replacement | Medium | Acknowledged. Can be re-added as a new `live_inspect` tool later if demand surfaces. Not this PR. |
| CLI subcommand rename breaks user scripts | Medium | Documented in CHANGELOG + PR body. Users fix their scripts once. |
| `lookup` degraded mode (missing optional services) confuses users | Low | Error message in `ServiceUnavailableError` names the missing sub-PR ("reference graph not indexed; enable via sub-PR #5b"). Users know how to fix. |
| `unified_search.yaml` preset ships untested | Low | AC #4 covers it with golden-fixture + latency assertions. |
| Forward-pointers in #5/#5b go stale if they're amended after #6 merges | Low | Both specs have "drift notice" conventions (established in sub-PRs #1-#4). Reviewers check on each merge. |

## 16. Out of scope (reaffirmed)

- Query-parser DSL — **sub-PR #6b** if needed.
- Error-tolerance primitives — **sub-PR #7**.
- Live-import replacement for `inspect_module` — future PR.
- Output-format hybrid (JSON for structured tools) — deferred; prose-markdown is the deliberate UX choice.
- MCP tool count cap at exactly 2 forever — if a genuinely-distinct operation emerges (e.g., `re_index`), add it; don't cram it into `search` / `lookup`.

## 17. Follow-up sub-PRs

- **Sub-PR #6b** — Query-parser DSL. Lets clients write `"auth package:fastapi scope:project"` in the `query` string; parser extracts filters into `SearchInput.package` / `scope`. Purely additive.
- **Sub-PR #7** — Error-tolerance primitives (`TryStage`, `RetryStage`, `CircuitBreakerStage`, `TimedStage`, `CachingStage`). Independent.
- **Live-inspect** — new distinct tool `live_inspect(target)` if users miss `inspect_module`'s import-and-check capability.
- **Structured-output variant** — if a client integration actually needs JSON (e.g., a dashboard), add a `format="json"` param to specific tools. Speculative.

---

**Approval log:** brainstormed 2026-04-20; 4 major decision axes resolved (scope=B, surface=Z5, deps=B, kind=any-preset); design sections 1-3 approved inline; breaking changes explicit.
