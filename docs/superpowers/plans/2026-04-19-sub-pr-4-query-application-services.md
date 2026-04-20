# Sub-PR #4 — Query Application Services + Thin MCP/CLI Handlers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract the remaining business logic from `server.py` and `__main__.py` into backend-agnostic Application Services on Protocol-typed dependencies, making MCP/CLI handlers thin wrappers (≤25 LOC each) and closing the refactor series.

**Architecture:** New `application/` services — 4 query-side (`PackageLookupService`, `SearchDocsService`, `SearchApiService`, `ModuleIntrospectionService`) and 1 write-side (`IndexProjectService`) — alongside sub-PR #3's `IndexingService`. All services are `@dataclass(frozen=True, slots=True)` with Protocol-typed deps. New supporting Protocols (`DependencyResolver`, `ChunkExtractor`, `MemberExtractor`) plus thin adapters over existing `deps.py`/`indexer.py` functions. Shared presentation helpers in `application/formatting.py` — single source of truth used by both `TokenBudgetFormatterStage` AND MCP-handler fallback path AND CLI stdout. Services do NOT implement `to_dict`/`from_dict` (not user-configurable wiring). MCP tool signatures byte-identical to `main`.

**Tech Stack:** Python 3.11+, stdlib `dataclasses`/`asyncio`/`typing.Protocol`, existing `storage/` Protocols (sub-PR #3), existing `retrieval/CodeRetrieverPipeline` (sub-PR #2). No new runtime deps (AC #18).

**Spec source of truth:** [`docs/superpowers/specs/2026-04-19-sub-pr-4-query-application-services-design.md`](../specs/2026-04-19-sub-pr-4-query-application-services-design.md). §5 is canonical; §8 is the files-touched breakdown; §11 is the 20 ACs.

**Work location:** Worktree `.claude/worktrees/sub-pr-4-query-application-services/` on branch `feature/sub-pr-4-query-application-services`, draft PR [#16](https://github.com/msobroza/pydocs-mcp/pull/16).

**Depends on:** sub-PR #3 merged as `6bbecc4` on `main`. `application/indexing_service.py` + `storage/` subpackage + `retrieval/CodeRetrieverPipeline` + YAML config all in place.

**Repo policy (critical):** No `Co-Authored-By:` trailers. All commits authored solely by `msobroza`. No git config changes, no `--author` overrides.

**Inherited invariants (MUST NOT regress):** AC #21 byte-parity, AC #26 no blanket swallow, AC #27-#35 from sub-PR #2 review + sub-PR #3 simplify fixes.

---

## File structure

### Files created
- `python/pydocs_mcp/application/protocols.py` — `DependencyResolver`, `ChunkExtractor`, `MemberExtractor` Protocols
- `python/pydocs_mcp/application/package_lookup_service.py` — `PackageLookupService` + `PackageDoc` value object
- `python/pydocs_mcp/application/search_docs_service.py` — `SearchDocsService`
- `python/pydocs_mcp/application/search_api_service.py` — `SearchApiService`
- `python/pydocs_mcp/application/module_introspection_service.py` — `ModuleIntrospectionService` (includes live-import logic extracted from server.py)
- `python/pydocs_mcp/application/index_project_service.py` — `IndexProjectService` + adapter classes + `IndexingStats`
- `python/pydocs_mcp/application/formatting.py` — 4 shared formatting helpers
- `tests/application/conftest.py` — in-memory Protocol fakes
- `tests/application/test_package_lookup_service.py`
- `tests/application/test_search_docs_service.py`
- `tests/application/test_search_api_service.py`
- `tests/application/test_module_introspection_service.py`
- `tests/application/test_index_project_service.py`
- `tests/application/test_server_handlers.py`
- `tests/application/test_cli_handlers.py`
- `tests/application/test_formatting.py`
- `tests/application/test_end_to_end.py`

### Files modified
- `python/pydocs_mcp/application/__init__.py` — re-exports all 6 services + PackageDoc + IndexingStats + Protocols
- `python/pydocs_mcp/models.py` — add `PackageDoc` value object + `IndexingStats` accumulator
- `python/pydocs_mcp/retrieval/stages.py` — `TokenBudgetFormatterStage` calls `application/formatting.py` helper (single source of truth)
- `python/pydocs_mcp/server.py` — rewrite all 5 handlers as thin wrappers; startup wiring (~25 LOC)
- `python/pydocs_mcp/__main__.py` — `query`/`api`/`index`/`serve` thin wrappers
- `python/pydocs_mcp/indexer.py` — slim down; bootstrap orchestration moves to `IndexProjectService`
- `CLAUDE.md` — architecture section updated

### Files deleted
None. `indexer.py` stays (extraction functions remain as adapter backends).

---

## Task 0 — Baseline verification

- [ ] **Step 0.1:** Activate venv + verify baseline.

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-4-query-application-services
source .venv/bin/activate
pytest -q
```
Expected: `468 passed` (sub-PR #3 baseline).

- [ ] **Step 0.2:** Rust toolchain:
```bash
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
```
Expected: exit 0.

- [ ] **Step 0.3:** No commit.

---

## BATCH 1 — Application layer foundation (Tasks 1–9)

All additive or internal-only. Existing tests stay green throughout.

## Task 1 — `application/protocols.py`

**Files:**
- Create: `python/pydocs_mcp/application/protocols.py`
- Create: `tests/application/test_protocols.py` (smoke test only)

Per spec §5.2:

```python
"""Application-layer Protocols — extraction + dependency resolution.

Sub-PR #4 ships thin adapters wrapping today's deps.py / indexer.py functions.
Sub-PR #5 replaces them with strategy-based implementations without touching
IndexProjectService or any other consumer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.models import Chunk, ModuleMember, Package


@runtime_checkable
class DependencyResolver(Protocol):
    async def resolve(self, project_dir: Path) -> tuple[str, ...]: ...


@runtime_checkable
class ChunkExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], Package]: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], Package]: ...


@runtime_checkable
class MemberExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]: ...
```

Test: smoke import + `assert issubclass(..., Protocol)` via runtime_checkable.

Commit: `feat(application): add DependencyResolver/ChunkExtractor/MemberExtractor protocols (spec §5.2)`

---

## Task 2 — `models.py` additions: `PackageDoc` + `IndexingStats`

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Modify: `tests/test_models.py`

Per spec §5.1 + §5.3:

```python
# Append to models.py (after existing dataclasses)

@dataclass(frozen=True, slots=True)
class PackageDoc:
    """Groups the three query results for get_package_doc one-shot retrieval."""
    kind: ClassVar[str] = "package_doc"
    package: Package
    chunks: tuple[Chunk, ...]
    members: tuple[ModuleMember, ...]


@dataclass(slots=True)
class IndexingStats:
    """Mutable accumulator for IndexProjectService.index_project()."""
    project_indexed: bool = False
    indexed: int = 0
    cached: int = 0
    failed: int = 0
```

Tests:
- `test_package_doc_frozen` — construction + immutability.
- `test_indexing_stats_mutable` — fields update, dataclass(slots=True) but NOT frozen.
- `test_indexing_stats_defaults` — all zeros / False.

Commit: `feat(models): add PackageDoc + IndexingStats (spec §5.1, §5.3)`

---

## Task 3 — `SearchDocsService`

**Files:**
- Create: `python/pydocs_mcp/application/search_docs_service.py`
- Create: `tests/application/test_search_docs_service.py`

Per spec §5.1:

```python
"""SearchDocsService — thin wrapper around chunk_pipeline.run (spec §5.1)."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import ChunkList, SearchQuery, SearchResponse
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline


@dataclass(frozen=True, slots=True)
class SearchDocsService:
    chunk_pipeline: CodeRetrieverPipeline

    async def search(self, query: SearchQuery) -> SearchResponse:
        state = await self.chunk_pipeline.run(query)
        return SearchResponse(
            result=state.result or ChunkList(items=()),
            query=state.query,
            duration_ms=state.duration_ms,
        )
```

Tests use a fake `CodeRetrieverPipeline`-compatible stub (class with `async def run(query)` returning a fake `PipelineState`). Cover:
- Happy path: pipeline returns `ChunkList(items=(c1, c2))` → service returns `SearchResponse(result=ChunkList(items=(c1, c2)), ...)`.
- Empty path: pipeline returns `state.result=None` → service returns `SearchResponse(result=ChunkList(items=()), ...)`.
- Propagation: pipeline raises → service propagates (no swallow).
- Duration threading: `state.duration_ms=12.5` → `response.duration_ms=12.5`.

Commit: `feat(application): add SearchDocsService (spec §5.1)`

---

## Task 4 — `SearchApiService`

**Files:**
- Create: `python/pydocs_mcp/application/search_api_service.py`
- Create: `tests/application/test_search_api_service.py`

Same shape as `SearchDocsService` but for `ModuleMemberList`:

```python
@dataclass(frozen=True, slots=True)
class SearchApiService:
    member_pipeline: CodeRetrieverPipeline

    async def search(self, query: SearchQuery) -> SearchResponse:
        state = await self.member_pipeline.run(query)
        return SearchResponse(
            result=state.result or ModuleMemberList(items=()),
            query=state.query,
            duration_ms=state.duration_ms,
        )
```

Tests mirror Task 3.

Commit: `feat(application): add SearchApiService (spec §5.1)`

---

## Task 5 — `PackageLookupService`

**Files:**
- Create: `python/pydocs_mcp/application/package_lookup_service.py`
- Create: `tests/application/test_package_lookup_service.py`

Per spec §5.1:

```python
"""PackageLookupService — list + get_package_doc via stores (spec §5.1)."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import ChunkFilterField, ModuleMemberFilterField, Package, PackageDoc
from pydocs_mcp.storage.protocols import ChunkStore, ModuleMemberStore, PackageStore


@dataclass(frozen=True, slots=True)
class PackageLookupService:
    package_store: PackageStore
    chunk_store: ChunkStore
    module_member_store: ModuleMemberStore

    async def list_packages(self) -> tuple[Package, ...]:
        return tuple(await self.package_store.list(limit=200))

    async def get_package_doc(self, package_name: str) -> PackageDoc | None:
        pkg = await self.package_store.get(package_name)
        if pkg is None:
            return None
        chunks = await self.chunk_store.list(
            filter={ChunkFilterField.PACKAGE.value: package_name},
            limit=10,
        )
        members = await self.module_member_store.list(
            filter={ModuleMemberFilterField.PACKAGE.value: package_name},
            limit=30,
        )
        return PackageDoc(package=pkg, chunks=tuple(chunks), members=tuple(members))
```

Tests with in-memory fakes cover:
- `test_list_packages_returns_tuple` — fake store returns list[Package], service returns tuple.
- `test_list_packages_respects_limit` — fake store asserts `limit=200` is passed.
- `test_get_package_doc_missing_returns_none`
- `test_get_package_doc_composes_all_three_stores` — verify limit=10 for chunks, limit=30 for members.
- `test_get_package_doc_passes_enum_filter_keys` — fake asserts filter dict contains `"package"` key (ChunkFilterField.PACKAGE.value).

Commit: `feat(application): add PackageLookupService + PackageDoc (spec §5.1)`

---

## Task 6 — `application/formatting.py` (single source of truth)

**Files:**
- Create: `python/pydocs_mcp/application/formatting.py`
- Create: `tests/application/test_formatting.py`

Per spec §5.4 + AC #6:

```python
"""Shared formatting helpers used by TokenBudgetFormatterStage AND handler fallback paths.

Single source of truth — stage + MCP handler fallback + CLI handler all call these.
"""
from __future__ import annotations

from pydocs_mcp.models import Chunk, ChunkFilterField, ModuleMember, ModuleMemberFilterField


_CHARS_PER_TOKEN = 4


def format_chunks_markdown_within_budget(
    chunks: tuple[Chunk, ...],
    budget_tokens: int,
) -> str:
    """Concatenate chunks as `## {title}\\n{text}\\n` within a char budget.

    Preserves byte-parity contract from sub-PR #2 AC #21 — single \\n between
    heading + body; trailing \\n preserved; NO .rstrip().
    """
    max_chars = budget_tokens * _CHARS_PER_TOKEN
    parts: list[str] = []
    total = 0
    for chunk in chunks:
        title = chunk.metadata.get(ChunkFilterField.TITLE.value, "") or ""
        text = chunk.text or ""
        piece = f"## {title}\n{text}\n"
        if total + len(piece) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                parts.append(piece[:remaining])
            break
        parts.append(piece)
        total += len(piece)
    return "".join(parts)


def format_members_markdown_within_budget(
    members: tuple[ModuleMember, ...],
    budget_tokens: int,
) -> str:
    """Member-list markdown formatter — mirrors old format_within_budget for members."""
    max_chars = budget_tokens * _CHARS_PER_TOKEN
    parts: list[str] = []
    total = 0
    for member in members:
        md = member.metadata
        pkg = md.get(ModuleMemberFilterField.PACKAGE.value, "")
        module = md.get(ModuleMemberFilterField.MODULE.value, "")
        name = md.get(ModuleMemberFilterField.NAME.value, "")
        kind = md.get(ModuleMemberFilterField.KIND.value, "")
        signature = md.get("signature", "") or ""
        docstring = md.get("docstring", "") or ""
        header = f"**[{pkg}] {module}.{name}{signature}** ({kind})"
        piece = f"{header}\n{docstring}\n"
        if total + len(piece) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                parts.append(piece[:remaining])
            break
        parts.append(piece)
        total += len(piece)
    return "".join(parts)


def format_chunks_cli_stdout(chunks: tuple[Chunk, ...]) -> str:
    """CLI stdout formatter — one chunk per block with separator lines.

    Mirrors pre-PR __main__.py query output format byte-identically.
    """
    # See pre-PR __main__.py query branch for exact format
    ...


def format_members_cli_stdout(members: tuple[ModuleMember, ...]) -> str:
    """CLI stdout formatter for api command."""
    ...
```

Tests cover:
- Byte-parity invariants — `## {title}\n{body}` single newline; trailing `\n` preserved; no rstrip.
- Budget enforcement — 100-char remaining gate; truncation stops early.
- CLI formats — spot-check against golden strings captured from `main`.

Commit: `feat(application): add formatting helpers — single source of truth for markdown/CLI rendering (spec §5.4, AC #6)`

---

## Task 7 — Update `TokenBudgetFormatterStage` to use formatting helper

**Files:**
- Modify: `python/pydocs_mcp/retrieval/stages.py`
- Modify: `tests/retrieval/test_stages.py` (if the rendering logic's direct tests touch `rstrip` / header spacing, adapt to the helper's contract)

Per spec AC #6: `TokenBudgetFormatterStage.run` calls `format_chunks_markdown_within_budget` — no duplicate rendering logic.

```python
from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_members_markdown_within_budget,
)


async def run(self, state: PipelineState) -> PipelineState:
    if state.result is None or not state.result.items:
        return state
    if isinstance(state.result, ChunkList):
        composite_text = format_chunks_markdown_within_budget(
            state.result.items, self.budget,
        )
    else:
        composite_text = format_members_markdown_within_budget(
            state.result.items, self.budget,
        )
    composite = Chunk(
        text=composite_text,
        metadata={
            ChunkFilterField.TITLE.value: COMPOSITE_TITLE_SENTINEL,
            ChunkFilterField.ORIGIN.value: ChunkOrigin.COMPOSITE_OUTPUT.value,
        },
    )
    return replace(state, result=ChunkList(items=(composite,)))
```

Verify `tests/retrieval/test_parity_golden.py` 3/3 still pass after refactor.

Commit: `refactor(retrieval): TokenBudgetFormatterStage uses application/formatting helper (AC #6)`

---

## Task 8 — `ModuleIntrospectionService`

**Files:**
- Create: `python/pydocs_mcp/application/module_introspection_service.py`
- Create: `tests/application/test_module_introspection_service.py`

Extract the live-import logic from `server.py::inspect_module` into a service that depends only on `PackageStore`. The service wraps CPU-bound sync work in `asyncio.to_thread`.

Per spec §5.1:

```python
"""ModuleIntrospectionService — live importlib + inspect (spec §5.1)."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import re
from dataclasses import dataclass

from pydocs_mcp.constants import LIVE_DOC_MAX, LIVE_SIGNATURE_MAX
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.storage.protocols import PackageStore

_SUBMODULE_RE = re.compile(r"^([A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*)?$")


def _validate_submodule(submodule: str) -> bool:
    return bool(_SUBMODULE_RE.match(submodule))


@dataclass(frozen=True, slots=True)
class ModuleIntrospectionService:
    package_store: PackageStore

    async def inspect(self, package: str, submodule: str = "") -> str:
        pkg_name = normalize_package_name(package)
        if await self.package_store.get(pkg_name) is None:
            return f"'{package}' is not indexed. Use list_packages() to see available packages."
        if submodule and not _validate_submodule(submodule):
            return f"Invalid submodule '{submodule}'. Use only letters, digits, underscores, and dots."
        target = pkg_name + (f".{submodule}" if submodule else "")
        return await asyncio.to_thread(self._inspect_target, target)

    def _inspect_target(self, target: str) -> str:
        try:
            mod = importlib.import_module(target)
        except ImportError:
            return f"Cannot import '{target}'."
        # ... same body as current server.py::inspect_module starting from `items = []`
        # Preserve output-string byte-parity with pre-PR.
```

Tests cover:
- `test_inspect_unindexed_package` — fake PackageStore returns None → "is not indexed" message.
- `test_inspect_invalid_submodule` — submodule with `';--` returns "Invalid submodule" message.
- `test_inspect_successful_root` — install-time existing stdlib module (`asyncio`) returns expected output.
- `test_inspect_successful_submodule` — `"asyncio", "events"` returns expected format.
- `test_inspect_importerror` — non-existent submodule → "Cannot import" string.

Commit: `feat(application): add ModuleIntrospectionService (spec §5.1)`

---

## Task 9 — `IndexProjectService` + extraction-adapter classes + `DependencyResolverAdapter`

**Files:**
- Create: `python/pydocs_mcp/application/index_project_service.py`
- Create: `tests/application/test_index_project_service.py`

Per spec §5.1 + §5.2:

```python
"""IndexProjectService — write-side bootstrap orchestrator (spec §5.1).

Wraps IndexingService + 3 extractor Protocols. Sub-PR #5 replaces the adapters
with strategy-based implementations without touching this service.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import ChunkExtractor, DependencyResolver, MemberExtractor
from pydocs_mcp.models import IndexingStats

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True)
class IndexProjectService:
    indexing_service: IndexingService
    dependency_resolver: DependencyResolver
    chunk_extractor: ChunkExtractor
    member_extractor: MemberExtractor

    async def index_project(
        self,
        project_dir: Path,
        *,
        force: bool = False,
        include_project_source: bool = True,
    ) -> IndexingStats:
        stats = IndexingStats()
        if force:
            await self.indexing_service.clear_all()
        if include_project_source:
            chunks, pkg = await self.chunk_extractor.extract_from_project(project_dir)
            members = await self.member_extractor.extract_from_project(project_dir)
            await self.indexing_service.reindex_package(pkg, chunks, members)
            stats.project_indexed = True
        for dep_name in await self.dependency_resolver.resolve(project_dir):
            await self._index_one_dependency(dep_name, stats)
        return stats

    async def _index_one_dependency(self, dep_name: str, stats: IndexingStats) -> None:
        try:
            chunks, pkg = await self.chunk_extractor.extract_from_dependency(dep_name)
            members = await self.member_extractor.extract_from_dependency(dep_name)
            await self.indexing_service.reindex_package(pkg, chunks, members)
            stats.indexed += 1
        except Exception as e:  # noqa: BLE001
            # Per spec §7 — IndexProjectService.index_one_dependency is the ONE
            # place inside services that catches broadly, because one bad dep
            # shouldn't abort the whole indexing pass. The catch logs and
            # increments the `failed` counter; it does NOT swallow silently.
            log.warning("  fail %s: %s", dep_name, e)
            stats.failed += 1
```

Adapter classes in the same file (or a separate `application/adapters.py` — pick whichever keeps files < 250 LOC):

```python
@dataclass(frozen=True, slots=True)
class DependencyResolverAdapter:
    """Thin wrapper over pydocs_mcp.deps.discover_declared_dependencies."""

    async def resolve(self, project_dir: Path) -> tuple[str, ...]:
        from pydocs_mcp.deps import discover_declared_dependencies
        import asyncio
        return await asyncio.to_thread(
            lambda: tuple(discover_declared_dependencies(str(project_dir)))
        )


@dataclass(frozen=True, slots=True)
class ChunkExtractorAdapter:
    """Thin wrapper over indexer.py chunk-extraction functions."""

    async def extract_from_project(self, project_dir: Path):
        from pydocs_mcp.indexer import extract_project_chunks  # exists today or add in Task 12
        return await extract_project_chunks(project_dir)

    async def extract_from_dependency(self, dep_name: str):
        from pydocs_mcp.indexer import extract_dependency_chunks
        return await extract_dependency_chunks(dep_name)


@dataclass(frozen=True, slots=True)
class MemberExtractorAdapter:
    """Thin wrapper over indexer.py member-extraction functions."""

    async def extract_from_project(self, project_dir: Path):
        from pydocs_mcp.indexer import extract_project_members
        return await extract_project_members(project_dir)

    async def extract_from_dependency(self, dep_name: str):
        from pydocs_mcp.indexer import extract_dependency_members
        return await extract_dependency_members(dep_name)
```

The adapter-target function names may not exist in `indexer.py` today — Task 12 splits existing `index_project_source` / `index_dependencies` into extraction + persist halves, exposing the `extract_*` functions.

Tests use in-memory `IndexingService` + Protocol fakes:
- `test_index_project_force_clears_first` — `force=True` calls `clear_all` before extraction.
- `test_index_project_skips_source_when_include_false`
- `test_index_project_resolves_and_indexes_each_dep`
- `test_index_one_dependency_increments_failed_on_exception` — extractor raises; `stats.failed += 1`; no re-raise.
- `test_index_one_dependency_success_increments_indexed`

Commit: `feat(application): add IndexProjectService + 3 extractor adapters + IndexingStats wiring (spec §5.1-§5.2)`

---

## BATCH 2 — Consumer migration (Tasks 10–13)

## Task 10 — `server.py` rewrite

**Files:**
- Modify: `python/pydocs_mcp/server.py`
- Create: `tests/application/test_server_handlers.py` (will run after Task 11 — include the file but tests can reference services that don't yet exist if that causes issues; if so, move test creation to Task 14)

Per spec §5.5 + §5.7 + AC #7, #8:

Startup wiring (~25 LOC):

```python
def run(db_path: Path) -> None:
    from pydocs_mcp.application.index_project_service import (
        IndexProjectService, ChunkExtractorAdapter, DependencyResolverAdapter, MemberExtractorAdapter,
    )
    from pydocs_mcp.application.indexing_service import IndexingService
    from pydocs_mcp.application.module_introspection_service import ModuleIntrospectionService
    from pydocs_mcp.application.package_lookup_service import PackageLookupService
    from pydocs_mcp.application.search_docs_service import SearchDocsService
    from pydocs_mcp.application.search_api_service import SearchApiService
    from pydocs_mcp.retrieval.wiring import build_retrieval_context
    from pydocs_mcp.retrieval.config import (
        AppConfig, build_chunk_pipeline_from_config, build_member_pipeline_from_config,
    )

    config = AppConfig.load()
    context = build_retrieval_context(db_path, config)
    # Storage handles come from the context's wiring
    ...

    # Services
    package_lookup = PackageLookupService(package_store=..., chunk_store=..., module_member_store=...)
    search_docs_svc = SearchDocsService(chunk_pipeline=...)
    search_api_svc = SearchApiService(member_pipeline=...)
    inspect_svc = ModuleIntrospectionService(package_store=...)
    # ...
```

Each MCP handler becomes ≤25 LOC with a single `try:...except Exception:` catch. See spec §5.5 for exact handler shape.

**Rendering helpers** (private to server.py):

```python
def _render_search_response_chunks(response: SearchResponse) -> str:
    # Dispatch on COMPOSITE_OUTPUT metadata; fall back to format_chunks_markdown_within_budget.
    ...


def _render_search_response_members(response: SearchResponse) -> str:
    # Same dispatch for ModuleMemberList — composite Chunk OR fallback formatter.
    ...


def _render_package_doc(doc: PackageDoc) -> str:
    # Rebuild the pre-PR `get_package_doc` return string from the typed doc.
    ...
```

Verify `pytest tests/test_server.py -v` still green. MCP tool surface byte-identical.

Commit: `refactor(server): all 5 handlers thin wrappers over application services (spec §5.5, AC #7, #8)`

---

## Task 11 — `__main__.py` rewrite

**Files:**
- Modify: `python/pydocs_mcp/__main__.py`

Per spec §5.6 + AC #9 + AC #16:

- `serve` subcommand: `server.run(db_path, config_path=args.config)` unchanged.
- `index` subcommand: build `IndexProjectService`, call `await service.index_project(project_dir, force=args.force, include_project_source=not args.skip_project)`. Print `IndexingStats` summary ("ok N, cached M, failed K").
- `query` / `api` subcommands: construct a CLI-flavored `SearchDocsService` / `SearchApiService` with a pipeline that **omits** `TokenBudgetFormatterStage` (so the service returns a raw `ChunkList` of N items). Render via `format_chunks_cli_stdout` / `format_members_cli_stdout`.

Each subcommand wraps in `try/except Exception:` at the `_cmd_*` level — prints `f"Error: {e}"` to stderr, returns non-zero exit code.

Commit: `refactor(cli): thin subcommand wrappers over application services (spec §5.6, AC #9, #16)`

---

## Task 12 — `indexer.py` slim-down

**Files:**
- Modify: `python/pydocs_mcp/indexer.py`

Move bootstrap orchestration logic (loop over deps, try/except per dep, stats accumulation) into `IndexProjectService._index_one_dependency` (Task 9) and keep ONLY the extraction functions:

- `extract_project_chunks(project_dir: Path) -> tuple[tuple[Chunk, ...], Package]` (async) — returns chunks + project Package.
- `extract_dependency_chunks(dep_name: str) -> tuple[tuple[Chunk, ...], Package]` (async) — returns chunks + dep Package.
- `extract_project_members(project_dir: Path) -> tuple[ModuleMember, ...]` (async).
- `extract_dependency_members(dep_name: str) -> tuple[ModuleMember, ...]` (async).

Delete `index_project_source` + `index_dependencies` (bootstrap orchestrators). Callers in `__main__.py` already migrated in Task 11. Tests that called them directly (if any) are updated in Task 14.

Commit: `refactor(indexer): slim down — extraction functions only; bootstrap orchestration moved to IndexProjectService (spec §8, AC #17)`

---

## Task 13 — `application/__init__.py` public API

**Files:**
- Modify: `python/pydocs_mcp/application/__init__.py`

Re-export the 6 services + 2 value objects + 3 Protocols + 3 adapters:

```python
"""Application services subpackage — use-case orchestrators on Protocol-only deps."""
from pydocs_mcp.application.index_project_service import (
    ChunkExtractorAdapter,
    DependencyResolverAdapter,
    IndexProjectService,
    MemberExtractorAdapter,
)
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.module_introspection_service import ModuleIntrospectionService
from pydocs_mcp.application.package_lookup_service import PackageLookupService
from pydocs_mcp.application.protocols import ChunkExtractor, DependencyResolver, MemberExtractor
from pydocs_mcp.application.search_api_service import SearchApiService
from pydocs_mcp.application.search_docs_service import SearchDocsService

__all__ = [
    "ChunkExtractor", "ChunkExtractorAdapter", "DependencyResolver", "DependencyResolverAdapter",
    "IndexProjectService", "IndexingService", "MemberExtractor", "MemberExtractorAdapter",
    "ModuleIntrospectionService", "PackageLookupService", "SearchApiService", "SearchDocsService",
]
```

Smoke: `python -c "from pydocs_mcp.application import IndexProjectService, SearchDocsService, PackageLookupService; print('ok')"`

Commit: `feat(application): public re-exports in __init__.py`

---

## BATCH 3 — Tests + final verification (Tasks 14–20)

## Task 14 — `tests/application/conftest.py` — in-memory Protocol fakes

**Files:**
- Create: `tests/application/__init__.py` (empty)
- Create: `tests/application/conftest.py`

Per spec §8 conftest: in-memory implementations of `PackageStore`, `ChunkStore`, `ModuleMemberStore`, `CodeRetrieverPipeline` (stub with `async def run`), `DependencyResolver`, `ChunkExtractor`, `MemberExtractor`. No SQLite, no importlib, no real pipelines.

Example:
```python
@dataclass
class InMemoryPackageStore:
    packages: dict[str, Package] = field(default_factory=dict)

    async def upsert(self, package): self.packages[package.name] = package
    async def get(self, name): return self.packages.get(name)
    async def list(self, filter=None, limit=None): return list(self.packages.values())[:limit]
    async def delete(self, filter): ...
    async def count(self, filter=None): return len(self.packages)
```

Commit: `test(application): add in-memory Protocol fakes for service tests (spec §8)`

---

## Tasks 15–18 — Service tests

Already specified per Task 3–9 (each service's test file). These commits may have landed in Tasks 3-9 if the subagent included them. Otherwise each remaining test file becomes its own commit here.

Expected final tests under `tests/application/`:
- `test_package_lookup_service.py` (~5 tests)
- `test_search_docs_service.py` (~4 tests)
- `test_search_api_service.py` (~4 tests)
- `test_module_introspection_service.py` (~5 tests)
- `test_index_project_service.py` (~6 tests)
- `test_formatting.py` (~8 tests covering byte-parity invariants from sub-PR #2 AC #21)
- `test_server_handlers.py` (~8 tests — fake services, one per MCP tool)
- `test_cli_handlers.py` (~6 tests — fake services, one per CLI subcommand)

Running total: ~46 new tests. Baseline 468 → expected ~514.

If not already committed inline with each service task, commit each test file:
`test(application): add <service>_service tests with Protocol fakes`

---

## Task 19 — End-to-end smoke + golden parity + zero-residue

**Files:**
- Create: `tests/application/test_end_to_end.py`

Per spec AC #14 + §11 #20:

- Wire a full real stack against a tmp-dir SQLite fixture repo: `SqliteUnitOfWork` + `SqlitePackageRepository` + `SqliteChunkRepository` + `SqliteModuleMemberRepository` + `SqliteVectorStore` + real `CodeRetrieverPipeline` from shipped preset + 5 services.
- Run `IndexProjectService.index_project(fixture_dir, force=True)` against a small fake project.
- Invoke each of the 5 MCP handlers via the real services.
- Assert outputs byte-identical to captured golden strings from `main`.

Zero-residue grep:
```bash
grep -RIn "\blegacy\b" python/ src/ tests/
grep -RIn "index_project_source\|index_dependencies" python/pydocs_mcp/ tests/   # removed from indexer.py
grep -RIn "except Exception" python/pydocs_mcp/application/ | grep -v "raise\|# noqa: BLE001\|stats\.failed"
```

All must be empty (the one legitimate blanket catch in `_index_one_dependency` is annotated with `# noqa: BLE001` per spec §7).

Final verification:
```bash
ruff check python/ tests/
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
pytest -q | tail -3
pytest tests/retrieval/test_parity_golden.py -v   # AC #27 inherited — must still pass
pytest tests/retrieval/test_stages.py::test_parallel_retrieval_stage_preserves_filtered_branches   # AC #28 inherited
```

Commit (if fixes needed): `refactor: clean up residue from Task 19 sweep`

---

## Task 20 — CLAUDE.md refresh + mark PR ready

**Files:**
- Modify: `CLAUDE.md`

Update architecture section:
- Add the 5 new services under `application/` in the data-flow diagram.
- Note that `server.py` + `__main__.py` are now thin handlers over services.
- Note `application/formatting.py` as the single source of truth for rendering.

Commit: `docs: refresh CLAUDE.md architecture — 5 new application services (AC §4)`

Push, flip draft to ready:
```bash
git push
gh pr ready 16
```

Post completion comment on PR #16 with AC coverage table.

---

## Self-review

**1. Spec coverage (20 ACs):**

| AC | Task |
|---|---|
| #1 application/ files | Tasks 1, 3–9, 13 |
| #2 frozen+slots Protocol-only | Tasks 3–5, 8, 9 |
| #3 DependencyResolver/ChunkExtractor/MemberExtractor protocols | Task 1 |
| #4 Concrete adapters | Task 9 |
| #5 PackageDoc + IndexingStats | Task 2 |
| #6 formatting.py + stage uses it | Tasks 6, 7 |
| #7 handlers ≤25 LOC with single try/except | Task 10 |
| #8 MCP surface byte-identical | Task 10 |
| #9 CLI subcommands byte-identical | Task 11 |
| #10 No concrete types in service constructors | Tasks 3–5, 8, 9 |
| #11 index_project order matches main | Task 9 |
| #12 Services propagate; no "legacy"; no blanket swallow | Tasks 9, 19 (grep) |
| #13 tests/application/ 10 files | Tasks 14, 15–18, 19 |
| #14 Golden-fixture MCP parity | Task 19 |
| #15 Composite-chunk dispatch test | Task 16 (server_handlers) |
| #16 CLI top-level exception handling | Task 11 |
| #17 No existing test deleted | invariant, Task 12 migration |
| #18 No new deps | Task 0 (verify) |
| #19 No Pydantic at MCP boundary | spec invariant |
| #20 Behavior parity end-to-end | Task 19 |

**2. Placeholder scan:** No TBD / implement later / add validation in any task. Some code bodies reference `...` for brevity (e.g., `_inspect_target` body in Task 8 references "same as pre-PR") — implementers should copy the pre-PR body verbatim.

**3. Type consistency:**
- `SearchResponse.result` always `PipelineResultItem` (sub-PR #1 canonical) — used in Tasks 3, 4, 10.
- `PackageDoc(package: Package, chunks: tuple[Chunk, ...], members: tuple[ModuleMember, ...])` — Tasks 2, 5, 10.
- Service constructor param types match Protocols (Task 1, Task 9).

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-19-sub-pr-4-query-application-services.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh Opus subagent per task, review between tasks, same flow as sub-PR #2 and #3.

**2. Inline Execution** — batch via `executing-plans`.
