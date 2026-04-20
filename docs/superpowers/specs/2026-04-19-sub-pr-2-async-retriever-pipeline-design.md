# Sub-PR #2 — Async retrievers, `CodeRetrieverPipeline`, routing, and config

**Status:** Approved (2026-04-19) — ready for implementation planning in a later session
**⚠️ Data-model drift notice (added 2026-04-19 after sub-PR #4 brainstorming):** the canonical data model is defined in **sub-PR #1 §5** (single source of truth). Implementers MUST consult that section for types. This spec's body contains references to shapes that no longer exist — listed below so you don't follow them by mistake.

**Known drift in this spec — do NOT implement these literal forms:**
- Protocol signatures using `tuple[SearchMatch, ...]` — should return `ChunkList` / `ModuleMemberList`.
- `PipelineState.matches` and `PipelineState.output` fields — canonical is `PipelineState.result: PipelineResultItem | None`.
- Stage descriptions talking about "appending matches" or "rebuilds ordered matches" — stages transform `state.result` (a `ChunkList` / `ModuleMemberList` / composite `ChunkList`), not a matches tuple.
- `TokenBudgetFormatterStage` setting `state.output` — canonical is: produces a 1-element `ChunkList` whose only `Chunk` has `metadata["origin"] == ChunkOrigin.COMPOSITE_OUTPUT.value`.
- Typed-field access like `chunk.package`, `chunk.title`, `result.package_filter` — canonical has these in `chunk.metadata[ChunkFilterField.*.value]`.
- Built-in predicate `_has_matches` computing `len(state.matches) > 0` — should inspect `state.result` for a non-empty list wrapper.
- AC #8 listing `PipelineState` fields `query`, `matches`, `output` — canonical is `query`, `result`, `duration_ms`.
- `state.output` in usage examples — read `state.result` then dispatch on type.

Follow this spec for **stage semantics + pipeline composition patterns + registry mechanics + YAML config**. Follow sub-PR #1 §5 for **types, field names, enums, `PipelineResultItem`**.
**Date:** 2026-04-19
**Depends on:** sub-PR #1 (approved) — domain models (`Chunk`, `ModuleMember`, `Package`, `SearchQuery`, `SearchMatch`, `SearchResult`, `Parameter`), enums (`ChunkOrigin`, `MemberKind`, `PackageOrigin`, `SearchScope`), SQLite schema, Rust `ModuleMember` class. Python 3.11+.
**Follows-on:** sub-PR #3 (storage repositories), sub-PR #4 (use-case layer), sub-PR #5 (indexer strategy split), sub-PR #6 (query parsing + Pydantic at MCP boundary), sub-PR #7 (error-tolerance primitives — `TryStage`, `RetryStage`, etc.).

**Scope changes vs. earlier plans:**
- Original sub-PR #4 (pipeline abstraction) is **merged into this PR** — pluggable retrievers and pluggable pipeline stages ship together.
- Pipeline routing primitives (`RouteStage`, `SubPipelineStage`) and **YAML-driven configuration** (via `pydantic-settings`) are **included in this PR** to preserve context. Sub-PR numbering for later PRs is unchanged.

---

## 1. Goal

Introduce async, pluggable retrieval and pipeline abstractions, plus a YAML-driven configuration layer that lets operators swap retrieval strategies (FTS5 / hybrid / reranker) or route between them based on predicates — **without changing any user-observable MCP behavior** when the config is absent or uses the default presets.

This PR ships:
- Retrieval and pipeline primitives needed for today's FTS5 / `LIKE` behavior.
- `RouteStage` + `SubPipelineStage` so future hybrid/reranker pipelines slot in without rewriting the retrieval layer.
- A `pydantic-settings`-backed `AppConfig` that reads an optional `pydocs-mcp.yaml` pointing at YAML pipeline preset files. Ships with two default presets that reproduce today's behavior byte-for-byte.

---

## 2. Decisions locked in during brainstorming

| Topic | Decision |
|---|---|
| Scope | Merge original sub-PR #4 (pipeline) + YAML config + routing into this PR. No renumbering for later PRs. |
| Retriever Protocol granularity | **Level 2** — a base `Retriever` Protocol + specialized `ChunkRetriever` / `ModuleMemberRetriever` sub-Protocols. |
| Pipeline pattern | **Uniform `PipelineStage`** (Chain of Responsibility / Pipes-and-Filters). All stages share one shape; compound stages (`ParallelRetrievalStage`, `ConditionalStage`, `RouteStage`, `SubPipelineStage`) are themselves `PipelineStage`s. |
| Connection lifecycle | **`ConnectionProvider` abstraction** with `PerCallConnectionProvider` default (opens/closes a SQLite connection per `acquire()`). |
| Formatter | **`ResultFormatter` Protocol** — strategy-injected into `TokenBudgetFormatterStage`. One concrete formatter per result type. |
| Hybrid-retrieval modelling | **Pipeline composition** is the default. `ParallelRetrievalStage` + `ReciprocalRankFusionStage` ship now. `PipelineChunkRetriever` (adapter) lets any sub-pipeline act as a `ChunkRetriever`. |
| Routing between pipelines | **`RouteStage`** (N-way predicate branching with default) + **`SubPipelineStage`** (pipeline-as-stage). `ConditionalStage` stays as the 1-way ergonomic form; `RouteStage` is its generalization. |
| Pipeline naming | `CodeRetrieverPipeline`; sibling primitives remain `PipelineStage`, `PipelineState`, `PipelineChunkRetriever`, `PipelineModuleMemberRetriever`, `SubPipelineStage`. |
| Predicate registry | **Registry pattern** + `@predicate("name")` decorator. `ConditionalStage` and `RouteStage` take `predicate_name: str`; no inline lambdas. |
| Serialization | Every component has `to_dict(self) -> dict` + `from_dict(data, context) -> Self`. Runtime deps injected via `BuildContext`. Predicates referenced by plain string name. Default-valued fields **omitted** from `to_dict` output. |
| Config file format | **YAML**. Top-level `pydocs-mcp.yaml` points at per-pipeline YAML presets. Preset YAML matches the dict form of `CodeRetrieverPipeline.to_dict()`. |
| Config library | **Explicit dep on `pydantic-settings`** (v2). `BaseSettings` subclass with `YamlConfigSettingsSource`. Explicit dep on `pyyaml` as the YAML parser (required by `YamlConfigSettingsSource`). |
| Config file location | Default search path: `./pydocs-mcp.yaml` → `~/.config/pydocs-mcp/config.yaml`. Override via CLI `--config PATH` flag or `PYDOCS_CONFIG_PATH` env var. **Absent config is valid** — falls through to built-in default presets; user-visible behavior identical to pre-PR. |
| Preset file location | Ships under `python/pydocs_mcp/presets/` (inside the installed package) for the built-in defaults. Users can reference their own paths in `pydocs-mcp.yaml`. |
| Error handling | Retrievers and stages **propagate** exceptions. Pipeline doesn't catch. MCP handlers catch broadly at the outermost boundary; logs + returns "No matches found." — same user-visible behavior as today. |
| Test policy | **No existing test is deleted.** Mechanical updates only. New test subtree under `tests/retrieval/` covers every new component including config loading and routing. |
| Long-lived MCP handlers | `list_packages` / `get_package_doc` / `inspect_module` migrate to `async with provider.acquire()`. All 5 handlers become `async`. |
| DB schema ownership | **Stays in `db.py`** this PR. PR #3 may relocate. |
| Read-only / read-write `ConnectionProvider` split | **No split.** Single protocol; read-only-ness is retriever discipline. |
| Factory functions | **No `factory.py`.** Default pipelines constructed via config-layer helpers (`build_chunk_pipeline_from_config`, `build_member_pipeline_from_config`) which are tiny (~20 LOC each) and live in `retrieval/config.py`. When no config file is present, the same helpers build the built-in default presets — behavior parity guaranteed. |
| Package layout | Flat `retrieval/` subpackage (8 files — adds `config.py`). No sub-subpackages. |
| Future `TryStage` extensibility | Preserved — stages propagate; pipeline doesn't catch. `TryStage` is a pure future addition. |

---

## 3. Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│                   pydocs-mcp.yaml (optional)                     │
│    points at presets/*.yaml; routes by predicate_name            │
└──────────────┬───────────────────────────────────────────────────┘
               │ (pydantic-settings loads at startup; absent OK)
               ▼
┌──────────────────────────────────────────────────────────────────┐
│   retrieval.config (AppConfig + build_*_pipeline_from_config)   │
│   builds one CodeRetrieverPipeline per handler, wrapping         │
│   RouteStage + SubPipelineStage if the config has routes        │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    server.py  (MCP async tools)                  │
│  - list_packages / get_package_doc / inspect_module              │
│      use `provider.acquire()` directly                           │
│  - search_docs / search_api                                      │
│      use `chunk_pipeline.run(query)` / `member_pipeline...`      │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│                retrieval.CodeRetrieverPipeline                   │
│                  - tuple[PipelineStage, ...]                     │
│                  - async run(query) -> PipelineState             │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    retrieval.stages                              │
│  Chunk/ModuleMemberRetrievalStage │ filters │ Parallel │ RRF    │
│  Conditional │ Route │ SubPipeline │ TokenBudgetFormatter        │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│                  retrieval.retrievers                            │
│  Bm25ChunkRetriever (FTS5) │ LikeMemberRetriever (LIKE)          │
│  PipelineChunkRetriever    │ PipelineModuleMemberRetriever       │
└──────────────┬───────────────────────────────────────────────────┘
               │ async with provider.acquire() as conn
               ▼
┌──────────────────────────────────────────────────────────────────┐
│            retrieval.PerCallConnectionProvider                   │
└──────────────┬───────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────┐
│                db.py  (SQLite schema + PRAGMA user_version)      │
└──────────────────────────────────────────────────────────────────┘
```

Perpendicular concerns:
- **`retrieval.serialization`** — `ComponentRegistry[C]` + `BuildContext` — enables `to_dict`/`from_dict` round-trips.
- **`retrieval.predicates`** — `PredicateRegistry` + `@predicate` + 4 built-ins — used by `ConditionalStage` and `RouteStage`.

---

## 4. Scope

### In scope

- New package `python/pydocs_mcp/retrieval/` with 8 files (§8).
- New `python/pydocs_mcp/presets/` directory shipping two default pipeline YAML files.
- Async `Retriever` base Protocol + `ChunkRetriever` / `ModuleMemberRetriever` sub-Protocols.
- `ConnectionProvider` Protocol + `PerCallConnectionProvider` default implementation.
- `PipelineStage` Protocol + `PipelineState` + `CodeRetrieverPipeline`.
- `ResultFormatter` Protocol + `ChunkMarkdownFormatter` + `ModuleMemberMarkdownFormatter`.
- `ComponentRegistry[C]` + `BuildContext` + `to_dict` / `from_dict` on every component.
- `PredicateRegistry` + `@predicate` + `default_predicate_registry` + 4 built-in predicates.
- Concrete retrievers: `Bm25ChunkRetriever`, `LikeMemberRetriever`, `PipelineChunkRetriever`, `PipelineModuleMemberRetriever`.
- Concrete stages: `ChunkRetrievalStage`, `ModuleMemberRetrievalStage`, `ParallelRetrievalStage`, `PackageFilterStage`, `ScopeFilterStage`, `TitleFilterStage`, `LimitStage`, `ReciprocalRankFusionStage`, `ConditionalStage`, `RouteStage`, `SubPipelineStage`, `TokenBudgetFormatterStage`. **12 stage classes total.**
- `AppConfig` (pydantic-settings) + YAML loading + pipeline-construction helpers in `retrieval/config.py`.
- CLI `--config PATH` flag (optional); `PYDOCS_CONFIG_PATH` env var support.
- `pyproject.toml` dependency additions: `pydantic-settings>=2.0` and `pyyaml>=6.0`.
- Delete `python/pydocs_mcp/search.py` (logic redistributed).
- `db.py`: add `build_connection_provider(cache_path: Path) -> ConnectionProvider`.
- `server.py` rewrite: all 5 MCP handlers async; pipelines built via `retrieval.config`; 3 long-lived-conn handlers migrate to `provider.acquire()`.
- `__main__.py`: `query` / `api` CLI subcommands build a throwaway minimal pipeline inline; top-level `--config` flag propagates to `serve`/`index` when provided.
- Tests: new subtree `tests/retrieval/` covering all new components including config loading.

### Out of scope (deferred to later sub-PRs)

- **PR #3:** Storage Repository layer. DDL may move out of `db.py`. Raw SQL in `server.py` handlers moves into repositories.
- **PR #4:** Use-case layer.
- **PR #5:** Indexer strategy split.
- **PR #6:** Query parsing component + Pydantic at MCP boundary.
- **PR #7:** Error-tolerance primitives (`TryStage`, `RetryStage`, etc.).
- Concrete dense / hybrid / LLM-rerank retrievers — shipped alongside their external-dep additions.
- Any user-visible behavior change when no `pydocs-mcp.yaml` is present. Built-in presets reproduce today's output byte-for-byte.

---

## 5. Domain components

> **Canonical data model:** this PR uses the data model defined in sub-PR #1 §5 (which is the canonical shape — `Chunk` with `text` + `metadata` dict, `ModuleMember` fully generic, `ChunkList` / `ModuleMemberList` list wrappers, `PipelineResultItem` TypeAlias, `SearchResponse.result` singular). Earlier drafts of this spec used `tuple[SearchMatch, ...]` — superseded.

### 5.1 Protocols (`retrieval/protocols.py`)

```python
from typing import Protocol, runtime_checkable

class Retriever(Protocol):
    """Any component that produces a ranked list of results."""
    name: str

class ChunkRetriever(Retriever, Protocol):
    """A Retriever that returns a ChunkList."""
    async def retrieve(self, query: SearchQuery) -> ChunkList: ...

class ModuleMemberRetriever(Retriever, Protocol):
    """A Retriever that returns a ModuleMemberList."""
    async def retrieve(self, query: SearchQuery) -> ModuleMemberList: ...

class PipelineStage(Protocol):
    name: str
    async def run(self, state: PipelineState) -> PipelineState: ...

class ConnectionProvider(Protocol):
    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[sqlite3.Connection]: ...

class ResultFormatter(Protocol):
    def format(self, result: Chunk | ModuleMember) -> str: ...
```

### 5.2 Pipeline primitives (`retrieval/pipeline.py`)

```python
@dataclass(frozen=True, slots=True)
class PipelineState:
    query: SearchQuery
    result: PipelineResultItem | None = None    # None until the first retrieval stage runs
    duration_ms: float = 0.0                     # accumulated from timing stages (optional)


@dataclass(frozen=True, slots=True)
class CodeRetrieverPipeline:
    name: str
    stages: tuple[PipelineStage, ...]

    async def run(self, query: SearchQuery) -> PipelineState:
        state = PipelineState(query=query)
        for stage in self.stages:
            state = await stage.run(state)
        return state

    def to_dict(self) -> dict:
        return {"name": self.name, "stages": [s.to_dict() for s in self.stages]}

    @classmethod
    def from_dict(cls, data, context: "BuildContext") -> "CodeRetrieverPipeline":
        return cls(
            name=data["name"],
            stages=tuple(context.stage_registry.build(s, context) for s in data["stages"]),
        )


@dataclass(frozen=True, slots=True)
class PerCallConnectionProvider:
    cache_path: Path

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[sqlite3.Connection]:
        connection = await asyncio.to_thread(self._open)
        try:
            yield connection
        finally:
            await asyncio.to_thread(connection.close)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
```

### 5.3 Registry + serialization (`retrieval/serialization.py`)

```python
C = TypeVar("C")

class ComponentRegistry(Generic[C]):
    def __init__(self) -> None:
        self._types: dict[str, type[C]] = {}

    def register(self, type_name: str):
        def decorator(cls: type[C]) -> type[C]:
            if type_name in self._types:
                raise ValueError(f"component {type_name!r} already registered")
            self._types[type_name] = cls
            return cls
        return decorator

    def build(self, data: Mapping, context: "BuildContext") -> C:
        type_name = data["type"]
        try:
            cls = self._types[type_name]
        except KeyError as e:
            raise KeyError(
                f"unknown component type {type_name!r}; "
                f"known: {sorted(self._types)}"
            ) from e
        return cls.from_dict(data, context)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._types))


stage_registry: ComponentRegistry = ComponentRegistry()
retriever_registry: ComponentRegistry = ComponentRegistry()
formatter_registry: ComponentRegistry = ComponentRegistry()


@dataclass(frozen=True, slots=True)
class BuildContext:
    connection_provider: ConnectionProvider
    predicate_registry: "PredicateRegistry" = field(default_factory=lambda: default_predicate_registry)
    stage_registry: ComponentRegistry = stage_registry
    retriever_registry: ComponentRegistry = retriever_registry
    formatter_registry: ComponentRegistry = formatter_registry
```

### 5.4 Predicate registry (`retrieval/predicates.py`)

```python
PipelinePredicate = Callable[[PipelineState], bool]

class PredicateRegistry:
    def __init__(self) -> None:
        self._predicates: dict[str, PipelinePredicate] = {}

    def register(self, name: str, predicate: PipelinePredicate) -> None:
        if name in self._predicates:
            raise ValueError(f"predicate {name!r} already registered")
        self._predicates[name] = predicate

    def get(self, name: str) -> PipelinePredicate:
        try:
            return self._predicates[name]
        except KeyError as e:
            raise KeyError(
                f"no predicate named {name!r}; "
                f"registered: {sorted(self._predicates)}"
            ) from e

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._predicates))


default_predicate_registry = PredicateRegistry()


def predicate(name: str, *, registry: PredicateRegistry = default_predicate_registry):
    def decorator(fn: PipelinePredicate) -> PipelinePredicate:
        registry.register(name, fn)
        return fn
    return decorator


# Built-ins (registered on import):

@predicate("has_matches")
def _has_matches(state): return len(state.matches) > 0

@predicate("query_has_multiple_terms")
def _query_has_multiple_terms(state): return len(state.query.terms.split()) >= 4

@predicate("scope_includes_dependencies")
def _scope_includes_dependencies(state): return state.query.scope != SearchScope.PROJECT_ONLY

@predicate("scope_includes_project")
def _scope_includes_project(state): return state.query.scope != SearchScope.DEPENDENCIES_ONLY
```

### 5.5 Concrete retrievers (`retrieval/retrievers.py`)

Four classes, all registered with `retriever_registry`:

- `Bm25ChunkRetriever(provider)` — FTS5 MATCH + BM25 over `chunks_fts`.
- `LikeMemberRetriever(provider)` — `LIKE` over `module_members.name` + `docstring`.
- `PipelineChunkRetriever(pipeline)` — adapter: sub-pipeline as `ChunkRetriever`.
- `PipelineModuleMemberRetriever(pipeline)` — same for `ModuleMember`.

Each has `to_dict`/`from_dict`. Runtime deps (`provider`, `pipeline`) come from `BuildContext`.

### 5.6 Concrete stages (`retrieval/stages.py`)

All `frozen=True, slots=True`, registered with `stage_registry`:

| Class | Role | Primary fields |
|---|---|---|
| `ChunkRetrievalStage` | Runs a `ChunkRetriever`; appends matches | `retriever: ChunkRetriever` |
| `ModuleMemberRetrievalStage` | Runs a `ModuleMemberRetriever`; appends matches | `retriever: ModuleMemberRetriever` |
| `ParallelRetrievalStage` | Runs inner stages via `asyncio.gather`; concatenates their new matches | `stages: tuple[PipelineStage, ...]` |
| `PackageFilterStage` | Keeps matches where `result.package == query.package_filter` | (none) |
| `ScopeFilterStage` | Keeps matches matching `query.scope` | (none) |
| `TitleFilterStage` | Keeps chunks whose `title` contains `query.title_filter` (case-insensitive). No-op for members. | (none) |
| `LimitStage` | Truncates to `max_results` | `max_results: int = 8` |
| `ReciprocalRankFusionStage` | RRF over `state.matches`, scored `1/(k+rank)`; rebuilds ordered matches | `k: int = 60` |
| `ConditionalStage` | Runs inner stage if `predicate_name` resolves true | `stage: PipelineStage`, `predicate_name: str`, `registry: PredicateRegistry = default_predicate_registry` |
| **`RouteStage`** | **First-match wins; falls through to `default`.** Generalizes `ConditionalStage`. | `routes: tuple[RouteCase, ...]`, `default: PipelineStage \| None = None`, `registry: PredicateRegistry = default_predicate_registry` |
| **`SubPipelineStage`** | **Runs a nested pipeline's stages on the incoming `state` (shares state, does not reset).** Enables pipelines-as-stages. | `pipeline: CodeRetrieverPipeline` |
| `TokenBudgetFormatterStage` | Renders each match via `formatter.format(result)`; accumulates within `budget * 4` bytes; sets `state.output` | `formatter: ResultFormatter`, `budget: int` |

**`RouteStage` support type:**

```python
@dataclass(frozen=True, slots=True)
class RouteCase:
    predicate_name: str
    stage: PipelineStage
```

**`RouteStage` implementation:**

```python
@dataclass(frozen=True, slots=True)
class RouteStage:
    routes: tuple[RouteCase, ...]
    default: PipelineStage | None = None
    registry: PredicateRegistry = field(default_factory=lambda: default_predicate_registry)
    name: str = "route"

    async def run(self, state: PipelineState) -> PipelineState:
        for case in self.routes:
            if self.registry.get(case.predicate_name)(state):
                return await case.stage.run(state)
        if self.default is not None:
            return await self.default.run(state)
        return state
```

**`SubPipelineStage` implementation:**

```python
@dataclass(frozen=True, slots=True)
class SubPipelineStage:
    pipeline: CodeRetrieverPipeline
    name: str = "sub_pipeline"

    async def run(self, state: PipelineState) -> PipelineState:
        for stage in self.pipeline.stages:
            state = await stage.run(state)
        return state
```

All stages implement `to_dict` / `from_dict`. Default-valued fields are omitted from `to_dict` output.

### 5.7 Concrete formatters (`retrieval/formatters.py`)

- `ChunkMarkdownFormatter` — `## {title}\n\n{text}`.
- `ModuleMemberMarkdownFormatter` — `**[{package}] {module}.{name}{signature}** ({kind})\n{docstring}`.

Both registered with `formatter_registry`. Both have `to_dict` / `from_dict`.

### 5.8 Config (`retrieval/config.py`)

#### `AppConfig` (pydantic-settings)

```python
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import YamlConfigSettingsSource

class PipelineRouteEntry(BaseModel):
    """
    One entry in a per-handler route list.
    - If `predicate` is set, run the pipeline at `pipeline_path` when the predicate matches.
    - If `default` is set (truthy), use the pipeline at `pipeline_path` as the fallback.
    Exactly one of `predicate` or `default` must be set.
    """
    predicate: str | None = None
    default: bool = False
    pipeline_path: Path


class HandlerConfig(BaseModel):
    """Ordered list of route entries for a single handler (chunk or member)."""
    routes: tuple[PipelineRouteEntry, ...]


class AppConfig(BaseSettings):
    cache_dir: Path = Path.home() / ".pydocs-mcp"
    log_level: str = "info"
    chunk: HandlerConfig | None = None    # None → use built-in default preset
    member: HandlerConfig | None = None   # None → use built-in default preset

    model_config = SettingsConfigDict(
        env_prefix="PYDOCS_",
        yaml_file=None,  # set dynamically via AppConfig.load()
    )

    @classmethod
    def load(cls, explicit_path: Path | None = None) -> "AppConfig":
        """
        Resolves config path with this precedence:
          1. explicit_path (from --config CLI flag)
          2. PYDOCS_CONFIG_PATH env var
          3. ./pydocs-mcp.yaml
          4. ~/.config/pydocs-mcp/config.yaml
          5. No file → AppConfig() with defaults (chunk=None, member=None)
        """
        ...
```

`chunk=None` and `member=None` mean "use the shipped default preset" — behavior parity with today.

#### Pipeline construction from config

```python
def build_chunk_pipeline_from_config(
    config: AppConfig, context: BuildContext
) -> CodeRetrieverPipeline:
    if config.chunk is None:
        return _load_preset_yaml(
            importlib.resources.files("pydocs_mcp.presets").joinpath("chunk_fts.yaml"),
            context,
        )
    return _build_handler_pipeline(
        handler_name="chunk",
        handler_config=config.chunk,
        context=context,
    )

def build_member_pipeline_from_config(
    config: AppConfig, context: BuildContext
) -> CodeRetrieverPipeline:
    if config.member is None:
        return _load_preset_yaml(
            importlib.resources.files("pydocs_mcp.presets").joinpath("member_like.yaml"),
            context,
        )
    return _build_handler_pipeline("member", config.member, context)


def _build_handler_pipeline(
    handler_name: str, handler_config: HandlerConfig, context: BuildContext
) -> CodeRetrieverPipeline:
    """Collapses a list of route entries into a single CodeRetrieverPipeline
    whose only stage is a RouteStage (or a direct SubPipelineStage for the
    single-default case)."""
    routes: list[RouteCase] = []
    default: PipelineStage | None = None
    for entry in handler_config.routes:
        sub_pipeline = _load_preset_yaml(entry.pipeline_path, context)
        stage = SubPipelineStage(pipeline=sub_pipeline)
        if entry.default:
            if default is not None:
                raise ValueError(f"{handler_name}: multiple default routes declared")
            default = stage
        elif entry.predicate:
            routes.append(RouteCase(predicate_name=entry.predicate, stage=stage))
        else:
            raise ValueError(
                f"{handler_name}: route entry must set either predicate or default"
            )
    if not routes and default is not None:
        # Shortcut: single default pipeline, no conditionals → just the sub-pipeline
        return CodeRetrieverPipeline(name=f"{handler_name}_from_config", stages=(default,))
    return CodeRetrieverPipeline(
        name=f"{handler_name}_from_config",
        stages=(RouteStage(routes=tuple(routes), default=default),),
    )


def _load_preset_yaml(path: Path, context: BuildContext) -> CodeRetrieverPipeline:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return CodeRetrieverPipeline.from_dict(data, context)
```

#### `pydocs-mcp.yaml` schema (reference)

```yaml
cache_dir: ~/.pydocs-mcp
log_level: info

chunk:
  routes:
    - predicate: query_has_multiple_terms
      pipeline_path: presets/chunk_hybrid.yaml
    - default: true
      pipeline_path: presets/chunk_fts.yaml

member:
  routes:
    - default: true
      pipeline_path: presets/member_like.yaml
```

Absent file → `AppConfig()` with `chunk=None` and `member=None` → both handlers load the shipped default preset → behavior identical to pre-PR.

---

## 6. Example pipelines and round-trips

### 6.1 Current FTS-only chunk pipeline (what `search_docs` uses today)

Python form:

```python
chunk_pipeline = CodeRetrieverPipeline(
    name="fts_chunk",
    stages=(
        ChunkRetrievalStage(retriever=Bm25ChunkRetriever(provider)),
        PackageFilterStage(),
        ScopeFilterStage(),
        TitleFilterStage(),
        LimitStage(max_results=8),
        TokenBudgetFormatterStage(
            formatter=ChunkMarkdownFormatter(),
            budget=CONTEXT_TOKEN_BUDGET,
        ),
    ),
)
```

Equivalent YAML (shipped as `python/pydocs_mcp/presets/chunk_fts.yaml`):

```yaml
name: fts_chunk
stages:
  - type: chunk_retrieval
    retriever: {type: bm25_chunk}
  - type: package_filter
  - type: scope_filter
  - type: title_filter
  - type: limit
    max_results: 8
  - type: token_budget_formatter
    formatter: {type: chunk_markdown}
    budget: 2000
```

### 6.2 Current LIKE member pipeline

YAML (`python/pydocs_mcp/presets/member_like.yaml`):

```yaml
name: like_member
stages:
  - type: module_member_retrieval
    retriever: {type: like_member}
  - type: package_filter
  - type: scope_filter
  - type: limit
    max_results: 15
  - type: token_budget_formatter
    formatter: {type: member_markdown}
    budget: 2000
```

### 6.3 Config-driven adaptive chunk pipeline (illustrative; uses `RouteStage`)

`pydocs-mcp.yaml`:

```yaml
chunk:
  routes:
    - predicate: query_has_multiple_terms
      pipeline_path: presets/chunk_hybrid.yaml
    - default: true
      pipeline_path: presets/chunk_fts.yaml
```

After `AppConfig.load()` + `build_chunk_pipeline_from_config`, the result is equivalent to:

```python
CodeRetrieverPipeline(
    name="chunk_from_config",
    stages=(
        RouteStage(
            routes=(
                RouteCase(
                    predicate_name="query_has_multiple_terms",
                    stage=SubPipelineStage(pipeline=<hybrid YAML loaded>),
                ),
            ),
            default=SubPipelineStage(pipeline=<fts YAML loaded>),
        ),
    ),
)
```

Handler calls `chunk_pipeline.run(query)` — unaware it's backed by a router. One abstraction, no duplication across variants.

### 6.4 Future hybrid pipeline (illustrative; ships in a later PR)

```python
CodeRetrieverPipeline(
    name="hybrid_chunk",
    stages=(
        ParallelRetrievalStage(stages=(
            ChunkRetrievalStage(retriever=Bm25ChunkRetriever(provider)),
            ChunkRetrievalStage(retriever=DenseChunkRetriever(vec_store)),  # future
        )),
        ReciprocalRankFusionStage(k=60),
        PackageFilterStage(),
        ScopeFilterStage(),
        LimitStage(max_results=20),
        ConditionalStage(
            stage=LlmRerankStage(client=llm),                                # future
            predicate_name="query_has_multiple_terms",
        ),
        LimitStage(max_results=8),
        ConditionalStage(
            stage=TokenBudgetFormatterStage(
                formatter=ChunkMarkdownFormatter(),
                budget=CONTEXT_TOKEN_BUDGET,
            ),
            predicate_name="has_matches",
        ),
    ),
)
```

Only `DenseChunkRetriever` and `LlmRerankStage` aren't in this PR. Every other primitive ships.

### 6.5 Serialization round-trip (illustrative)

```python
pipeline = ... # FTS chunk pipeline above
d = pipeline.to_dict()
yaml_text = yaml.safe_dump(d, sort_keys=False)
# Save, load later:
restored = CodeRetrieverPipeline.from_dict(yaml.safe_load(yaml_text), context)
assert restored.to_dict() == d
```

---

## 7. Error handling

- **Retrievers propagate.** No blanket `except Exception: return []` in any retriever.
- **Stages propagate.** Stage-level exceptions abort the pipeline.
- **`CodeRetrieverPipeline.run()` propagates.**
- **Config loading errors are reported early** (with clear messages) at server/CLI startup:
  - Missing referenced preset file → raises `FileNotFoundError` with path.
  - Invalid YAML → the parser's error propagates with location info.
  - Unknown component type in YAML → `KeyError` listing registered type names.
  - Unregistered predicate name → `KeyError` listing registered predicates.
  - Invalid route entry (neither `predicate` nor `default`) → `ValueError`.
- **MCP handlers catch broadly** at the outermost boundary: `try/except Exception: log.warning(...); return "No matches found."` — preserves today's user-visible behavior.
- **CLI (`__main__.py`) catches at its top level**, prints a concise error + exits non-zero.

A future `TryStage` (sub-PR #7) lets callers opt into per-stage error tolerance. Nothing in this PR blocks that.

---

## 8. Code organization — files touched

### New package `python/pydocs_mcp/retrieval/`

| File | Contents | ~LOC |
|---|---|---|
| `__init__.py` | Public re-exports | 25 |
| `protocols.py` | 6 Protocols | 50 |
| `pipeline.py` | `PipelineState`, `CodeRetrieverPipeline`, `PerCallConnectionProvider` | 100 |
| `serialization.py` | `ComponentRegistry`, `BuildContext`, 3 shared registries | 80 |
| `predicates.py` | `PredicateRegistry`, `@predicate`, `default_predicate_registry`, 4 built-ins | 80 |
| `retrievers.py` | 4 retriever classes | 180 |
| `stages.py` | **12 stage classes** + `RouteCase` | 440 |
| `formatters.py` | 2 formatters | 70 |
| `config.py` | `AppConfig`, `HandlerConfig`, `PipelineRouteEntry`, `AppConfig.load()`, `build_*_pipeline_from_config`, `_load_preset_yaml` | 160 |

**Retrieval package subtotal: ~1,185 LOC.**

### New package `python/pydocs_mcp/presets/`

| File | ~LOC |
|---|---|
| `__init__.py` (empty; `importlib.resources` entry point) | 1 |
| `chunk_fts.yaml` | 15 |
| `member_like.yaml` | 12 |

**Presets subtotal: ~28 LOC.**

### Existing files modified

| File | Change | ~LOC diff |
|---|---|---|
| `python/pydocs_mcp/search.py` | delete | −151 |
| `python/pydocs_mcp/db.py` | Add `build_connection_provider(cache_path: Path) -> ConnectionProvider` | +30 |
| `python/pydocs_mcp/server.py` | All 5 handlers become async. `search_docs`/`search_api` use pipelines built from `AppConfig.load()` at startup. `list_packages`/`get_package_doc`/`inspect_module` use `async with provider.acquire() as connection`. | ±240 |
| `python/pydocs_mcp/__main__.py` | Add `--config PATH` top-level flag. `query`/`api` CLI subcommands build a throwaway minimal pipeline inline. | +50 |
| `pyproject.toml` | Add deps: `pydantic-settings>=2.0`, `pyyaml>=6.0`. Bump project version. | +5 |

**Existing-files subtotal: ±476 LOC** (incl. `search.py` deletion).

### Tests

| File | Change / contents | ~LOC |
|---|---|---|
| `tests/test_server.py` | edit — cover async handlers; fake pipeline. **No existing test deleted.** | +50 |
| `tests/test_db.py` | edit — cover the new factory. **No existing test deleted.** | +10 |
| `tests/retrieval/test_pipeline.py` (new) | `CodeRetrieverPipeline.run()` state threading; stage sequencing | 60 |
| `tests/retrieval/test_provider.py` (new) | `PerCallConnectionProvider` round-trip on a tmp-dir DB | 50 |
| `tests/retrieval/test_serialization.py` (new) | `ComponentRegistry` register/get/collision/unknown; full `to_dict`/`from_dict` round-trip; default-field omission | 80 |
| `tests/retrieval/test_predicates.py` (new) | `PredicateRegistry`; 4 built-ins; collision | 40 |
| `tests/retrieval/test_retrievers.py` (new) | `Bm25ChunkRetriever`, `LikeMemberRetriever` against fixture DB; adapter forwarding | 120 |
| `tests/retrieval/test_stages.py` (new) | Every stage class including `RouteStage` and `SubPipelineStage` with fake retrievers + states; RRF arithmetic; `ConditionalStage` / `RouteStage` with fake registry | 180 |
| `tests/retrieval/test_formatters.py` (new) | 2 formatters | 30 |
| `tests/retrieval/test_config.py` (new) | `AppConfig.load()` precedence (CLI flag, env var, local file, home file, absent); YAML preset loading; built-in default presets match pre-PR output; YAML/config error messages | 150 |

**Tests subtotal: ~770 LOC.**

### PR size rollup

| | LOC |
|---|---|
| `retrieval/` package (new) | ~1,185 |
| `presets/` package (new) | ~28 |
| Existing files modified | ~476 |
| Tests | ~770 |
| **Total** | **~2,459** |

Large but reviewable. Natural split if needed is **#2a** (protocols + pipeline + retrievers + stages + serialization) and **#2b** (config + presets + routing + route tests). Preference is one PR.

---

## 9. Cross-PR interface commitments (ratified)

| # | Commitment |
|---|---|
| 1 | Retrievers own retrieval SQL. Repositories in PR #3 will own CRUD SQL. Zero overlap. |
| 2 | Single `ConnectionProvider` protocol — no read-only/read-write type split. |
| 3 | Transactions are caller-driven. Provider does not own transactions. |
| 4 | DDL stays in `db.py` for this PR. PR #3 may relocate. |
| 5 | One `ConnectionProvider` instance per server process, built at startup. |
| 6 | Default implementation is `PerCallConnectionProvider`. |
| 7 | All 5 MCP handlers use `provider.acquire()` by the end of this PR. |
| 8 | Only retrieval stages touch connections. Filters/fusion/routing/formatter — connection-free. |
| 9 | `retrieval/` package is flat (8 files). |
| 10 | `ConnectionProvider` is the testing seam for retrievers. |
| 11 | `AppConfig` is the single source of truth for runtime config. Handlers never read env vars or files directly. |
| 12 | Absent `pydocs-mcp.yaml` is valid; server boots with built-in default presets; output identical to pre-PR. |

---

## 10. Risks and rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| New `pydantic-settings` / `pyyaml` deps break an installation environment | Low | Both are mainstream; already used across the Python ecosystem. Pinned loosely (`>=2.0`, `>=6.0`). |
| User writes invalid YAML → server fails to start | Medium | Config-load errors surface at startup with parser location + the offending key path. Fall-back is not automatic (explicit config should fail loudly). |
| User references a preset path that doesn't exist | Medium | `FileNotFoundError` raised at load with clear path. Tested. |
| Unknown component `type` in user-supplied YAML | Medium | `ComponentRegistry.build` raises `KeyError` listing registered type names. Tested. |
| Unregistered predicate name in user-supplied YAML | Medium | `PredicateRegistry.get` raises `KeyError` listing registered predicates. Tested. |
| Built-in default presets drift from Python literal pipelines | Medium | Acceptance test §11-#20 asserts byte-identical MCP output when no config file is present, and again when the shipped presets are explicitly referenced. |
| `RouteStage` predicate evaluates on empty initial matches and always picks the same branch | Low (intentional) | Predicates are explicitly state-based. Route-selection predicates usually inspect `state.query`, not `state.matches`. Documented in `RouteStage` docstring. |
| Serialization omits a default-valued field; `from_dict` reconstructs wrong value | Medium | Tests assert full round-trip equality for every stage, including defaults. |
| Registry collisions at import time | Low | `register` raises on collision; CI catches. |
| Migrating long-lived-conn handlers breaks a handler subtly | Medium | Acceptance tests run each MCP tool through a mock client against an in-memory DB; outputs compared to pre-PR. |
| Stale compiled `_native` extension after `git pull` | Low | PR description instructs `maturin develop --release`; CI rebuilds wheel. |

**Rollback:** revert the merge commit. `~/.pydocs-mcp/*.db` untouched. `pydocs-mcp.yaml` is optional; removing or ignoring it restores pre-config behavior. The `pyproject.toml` deps come back out.

---

## 11. Acceptance criteria

1. `python/pydocs_mcp/retrieval/` exists with 9 Python files (§8): `__init__.py`, `protocols.py`, `pipeline.py`, `serialization.py`, `predicates.py`, `retrievers.py`, `stages.py`, `formatters.py`, `config.py`.
2. `python/pydocs_mcp/presets/` exists with `__init__.py`, `chunk_fts.yaml`, `member_like.yaml`.
3. `python/pydocs_mcp/search.py` no longer exists.
4. `pyproject.toml` declares `pydantic-settings>=2.0` and `pyyaml>=6.0` as runtime dependencies; project version bumped.
5. **No existing test is deleted.** The full existing test suite on `main` is still present in the PR branch, with only mechanical updates for renamed imports/symbols; every test passes.
6. Protocols in `protocols.py`: `Retriever`, `ChunkRetriever`, `ModuleMemberRetriever`, `PipelineStage`, `ConnectionProvider`, `ResultFormatter`. `ChunkRetriever`/`ModuleMemberRetriever` inherit from `Retriever`.
7. `CodeRetrieverPipeline` is `frozen=True, slots=True`; has `name`, `stages`; exposes `async run(query) -> PipelineState`.
8. `PipelineState` is `frozen=True, slots=True` with fields `query`, `result: PipelineResultItem | None`, `duration_ms: float = 0.0` (canonical per sub-PR #1 §5; earlier wording in this spec listed `matches` + `output` which are superseded).
9. `PerCallConnectionProvider(cache_path)` opens a new SQLite connection per `acquire()` with `row_factory=Row`, `check_same_thread=False`, WAL + `synchronous=NORMAL`.
10. `ComponentRegistry[C]` exists with `register`/`build`/`names`. Three shared instances: `stage_registry`, `retriever_registry`, `formatter_registry`. Collision raises `ValueError`; unknown type raises `KeyError` listing known names.
11. `BuildContext` is a frozen dataclass holding `connection_provider`, `predicate_registry`, and the three component registries.
12. `PredicateRegistry` + `@predicate` + `default_predicate_registry` exist. Built-ins: `has_matches`, `query_has_multiple_terms`, `scope_includes_dependencies`, `scope_includes_project`.
13. Retriever classes `Bm25ChunkRetriever`, `LikeMemberRetriever`, `PipelineChunkRetriever`, `PipelineModuleMemberRetriever` — each with `name: str` + `to_dict`/`from_dict`.
14. **12 stage classes** exist with their `to_dict`/`from_dict`: `ChunkRetrievalStage`, `ModuleMemberRetrievalStage`, `ParallelRetrievalStage`, `PackageFilterStage`, `ScopeFilterStage`, `TitleFilterStage`, `LimitStage`, `ReciprocalRankFusionStage`, `ConditionalStage`, `RouteStage`, `SubPipelineStage`, `TokenBudgetFormatterStage`.
15. `RouteStage` implements first-match-wins over `routes: tuple[RouteCase, ...]` with an optional `default: PipelineStage | None`. Returns `state` unchanged if no match and no default.
16. `SubPipelineStage` runs a nested `CodeRetrieverPipeline.stages` tuple on the incoming `state` (does NOT reset state).
17. `ConditionalStage.predicate_name` and `RouteCase.predicate_name` are plain `str`; resolved against `PredicateRegistry.get()` at run time. No inline callables.
18. Concrete formatters `ChunkMarkdownFormatter`, `ModuleMemberMarkdownFormatter` implement `ResultFormatter`; both registered with `formatter_registry`.
19. **Full `to_dict` / `from_dict` round-trip** passes for every stage (including `RouteStage`, `SubPipelineStage`), every retriever, every formatter, and a full `CodeRetrieverPipeline`. Default-valued fields absent from output.
20. `AppConfig.load()` resolves config path with this precedence: explicit `--config` > `PYDOCS_CONFIG_PATH` env var > `./pydocs-mcp.yaml` > `~/.config/pydocs-mcp/config.yaml` > absent (returns `AppConfig()` with `chunk=None`, `member=None`).
21. When no config file is present (and neither env nor CLI flag supplied), `search_docs` and `search_api` produce strings byte-identical to `main`'s pre-PR output on the same fixture. Golden test locked in `tests/retrieval/test_parity_golden.py`. **Byte-parity contract (explicitly enforced):**
    - `ChunkMarkdownFormatter.format(chunk)` MUST emit `## {title}\n{body}` — exactly one `\n` between heading and body, NOT `\n\n`. Any future formatter change that alters whitespace is a breaking change and MUST bump the preset YAML format version.
    - `TokenBudgetFormatterStage.run(state)` MUST NOT call `.rstrip()` (or any equivalent trailing-whitespace strip) on the composite output text. The trailing `\n` after the last joined piece is load-bearing for downstream consumers comparing bytes or running line-anchored regex.
    - The parity test MUST cover both the single-newline-between-heading-and-body invariant AND the preserved-trailing-newline invariant. Regression of either fails the test.
22. When a `pydocs-mcp.yaml` references the shipped `chunk_fts.yaml` and `member_like.yaml` presets with single `default` routes, output is byte-identical to the no-config case. Golden test.
23. When a `pydocs-mcp.yaml` references a conditional route, the predicate decides which sub-pipeline runs; verified with a fake predicate + fixture.
24. Config-loading error modes are covered by acceptance tests:
    - Missing referenced preset file → clear `FileNotFoundError`.
    - Malformed YAML → parser error surfaces with location.
    - Unknown component `type` → `KeyError` listing registered types.
    - Unregistered predicate name → `KeyError` listing registered predicates.
    - Invalid route entry (neither `predicate` nor `default`) → `ValueError`.
25. The word `legacy` does not appear in any source file added or modified by this PR.
26. **No blanket-swallow contract (explicitly enforced):** No `try/except Exception: return ...`-style catch lives in any retriever, stage, or the `CodeRetrieverPipeline.run()` method. Blanket catches are confined to the outermost MCP handlers (`server.py`) and the CLI top-level (`__main__.py`). **Specific narrowing rules:**
    - Concrete retrievers (`Bm25ChunkRetriever._retrieve_sync`, `LikeMemberRetriever._retrieve_sync`, and any future `*Retriever` that talks to SQLite) MUST narrow their `except` to `sqlite3.DatabaseError` (which covers `OperationalError`, `IntegrityError`, `DataError`, `NotSupportedError`, `InterfaceError`, and `ProgrammingError`). Programming errors (`TypeError`, `AttributeError`, `KeyError` in internal logic) MUST propagate. `SystemExit`/`KeyboardInterrupt` MUST NOT be intercepted.
    - Stages (`ChunkRetrievalStage`, `ModuleMemberRetrievalStage`, `PackageFilterStage`, `ScopeFilterStage`, `TitleFilterStage`, `LimitStage`, `ParallelRetrievalStage`, `ReciprocalRankFusionStage`, `ConditionalStage`, `RouteStage`, `SubPipelineStage`, `TokenBudgetFormatterStage`) MUST propagate exceptions unchanged. Future `TryStage` (sub-PR #7) is the opt-in mechanism for per-stage error tolerance.
    - `CodeRetrieverPipeline.run()` MUST NOT wrap the inner stage-iteration loop in any `try/except`. Full propagation to the caller (MCP handler or CLI `asyncio.run`) is required.
    - Grep invariant for CI: `rg -n 'except Exception' python/pydocs_mcp/retrieval/` MUST return zero matches in any file under `python/pydocs_mcp/retrieval/` EXCEPT if the caught exception is re-raised unchanged or the except body is `raise`.
27. `server.py` after this PR:
    - All 5 MCP tools `async def`.
    - Tool signatures (names, params, types, docstrings, return-string shape) byte-identical to `main`.
    - `search_docs` / `search_api` use pipelines built from `AppConfig.load()` at server startup.
    - `list_packages` / `get_package_doc` / `inspect_module` use `async with provider.acquire() as connection` + `asyncio.to_thread`.
    - No module-level long-lived `sqlite3.Connection`.
28. `__main__.py`: top-level `--config PATH` flag accepted; `query` / `api` CLI subcommands build a throwaway pipeline inline with output textually identical to `main`.
29. Behavior parity: for the golden fixture repo indexed under sub-PR #1, every MCP tool and every CLI subcommand produces byte-identical output before and after sub-PR #2 when no config file is present. See AC #21 for the specific formatter/stage byte-parity contract that enforces this. A regression of AC #21 automatically regresses AC #29.
30. `tests/retrieval/` subtree exists as listed in §8, contributes ≥ ~90% statement coverage for new code.
31. `pyproject.toml` advertises `requires-python=">=3.11"` (unchanged from sub-PR #1). Deps added: `pydantic-settings>=2.0`, `pyyaml>=6.0`. Rust side unchanged.
32. **`ParallelRetrievalStage` content-keyed dedup (explicitly enforced):** `ParallelRetrievalStage.run()` MUST dedup branch outputs by content key, not by positional slice. The positional form (`branch_state.result.items[len(initial_items):]`) is BANNED — any branch that filters, reorders, or partially drops items would silently lose legitimate outputs with that approach. **Required implementation:**
    - Compute a key per item: `item.id` if it is not `None`, else `id(item)` (Python identity fallback).
    - Maintain a `seen_keys: set` while iterating initial items (if any) followed by each branch's full `result.items`. An item is appended to the accumulator only when its key is not yet in `seen_keys`. Keys that have been appended are added to `seen_keys`.
    - The first-seen wins: if a key appears in both the initial state and a branch, only the initial copy is kept. Between branches, first branch contribution wins. This preserves stable ordering across reruns with the same input.
    - Result type dispatch: the accumulator is wrapped in `ChunkList` if the first non-None `state.result` or branch result was a `ChunkList`, else `ModuleMemberList`. Mixed result types across branches are NOT supported in this PR.
    - A regression test (`test_parallel_retrieval_stage_preserves_filtered_branches` in `tests/retrieval/test_stages.py`) MUST assert that a branch which drops one initial item and adds one new item still contributes its new item to the accumulator, and that a new item shared between two branches is deduped to a single entry.

---

## 12. Open items

None — all design decisions ratified during brainstorming. If implementation reveals a decision that doesn't survive reality, I'll surface it before writing code.

---

## 13. Implementation notes — pitfalls for implementers without full brainstorming context

Points that are easy to get wrong if you only have the spec and not the brainstorming history.

### Registry + `@register` is the universal pattern
- Four registries appear: `stage_registry`, `retriever_registry`, `formatter_registry`, `PredicateRegistry`. All use the SAME pattern: decorator-based registration, `ValueError` on duplicate name, `KeyError` on lookup miss listing known names. Don't hand-roll a new registry variant.
- Registry collisions fail at import time. CI catches duplicates automatically.

### `BuildContext` is the only recursion mechanism for `from_dict`
- Every `from_dict(data, context)` passes `context` to nested `registry.build(sub_data, context)` calls unchanged. Do not thread explicit keyword arguments like `from_dict(data, *, connection_provider, llm_client, ...)`. Explicit kwargs look innocent but break when a new dependency is added in a future PR — every intermediate call site would need updating.
- `BuildContext` is a frozen dataclass. Add fields additively when new runtime deps appear.

### `to_dict` omits default-valued fields; `from_dict` reapplies them
- E.g., `LimitStage(max_results=8)` serializes to `{"type": "limit"}`, not `{"type": "limit", "max_results": 8}`. `from_dict({"type": "limit"}, context)` reconstructs the default value. Round-trip tests (AC #15) verify equality, so a broken omission/reapplication pair fails fast.

### `ConditionalStage.predicate_name` is a STRING, not a callable
- `ConditionalStage(stage=..., predicate_name="has_matches")` — the registry resolves the name at `run()` time. Don't write `predicate=lambda s: ...`; the dataclass won't accept a callable and the YAML path won't parse either.
- To add a new predicate: write a function, decorate with `@predicate("name")`, use the name everywhere. Don't inline.

### Concrete classes inherit Protocols explicitly
- `class SqliteVectorStore(ChunkStore, TextSearchable):` — Python's Protocols are structural, but inheriting explicitly gives mypy method-completeness checks and ensures `isinstance(x, TextSearchable)` works at runtime. Structural-only typing is legal but foregoes the verification.

### Pipeline semantics
- `ParallelRetrievalStage`: each inner stage sees the **same input state**; their newly-added matches are concatenated in branch order. Pre-existing `state.matches` (from an upstream retriever) is preserved — the stage is additive, not replacing.
- `RouteStage`: **first match wins**; later routes are not evaluated. If no predicate matches and `default` is `None`, `state` is returned unchanged — it's a no-op, not an error.
- `SubPipelineStage`: runs a nested `CodeRetrieverPipeline.stages` tuple on the **incoming state** (does NOT create a fresh state). This is what lets it compose with outer filters and formatters without losing accumulated matches.

### Pipelines don't catch exceptions
- `CodeRetrieverPipeline.run()` propagates. Retrievers propagate. Stages propagate. The ONLY catch sites are MCP handlers in `server.py` and the CLI top level in `__main__.py` — each returns a benign user-visible fallback.
- Don't add `try/except` inside any stage, retriever, or the pipeline itself "defensively". A future `TryStage(stage, on_error=None)` is the opt-in mechanism for per-stage fault tolerance (sub-PR #7).

### YAML config — precedence and loader
- Resolution order: CLI `--config PATH` > `PYDOCS_CONFIG_PATH` env > `./pydocs-mcp.yaml` > `~/.config/pydocs-mcp/config.yaml` > fall through to in-code defaults (sub-PR #2) or shipped baseline (sub-PR #3).
- The pydantic-settings plumbing uses `settings_customise_sources` + `YamlConfigSettingsSource`. It's non-obvious. Copy the shape shown in §5.8 verbatim; don't improvise the source ordering.
- Absent user YAML is always valid. Built-in defaults (sub-PR #2) or shipped `default_config.yaml` (sub-PR #3) are sufficient for the server to boot.

### Pipeline naming final: `CodeRetrieverPipeline`
- The class is `CodeRetrieverPipeline`, not `SearchPipeline`. Pipeline primitives (`PipelineStage`, `PipelineState`, `PipelineChunkRetriever`, `PipelineModuleMemberRetriever`, `SubPipelineStage`) keep the generic `Pipeline*` prefix. Easy to mix up in fresh code.

### `search.py` is deleted
- Don't look for `search.search_chunks` / `search.search_symbols`. They're replaced by concrete retriever classes in `retrieval/retrievers.py`. If you find yourself grepping for the old module name, your patch is stale.

---

## 14. Usage examples and design patterns

### How consumers interact with this PR

**1. Build a pipeline inline (what `server.py` does at startup):**

```python
from pydocs_mcp.retrieval import (
    CodeRetrieverPipeline, PerCallConnectionProvider,
)
from pydocs_mcp.retrieval.stages import (
    ChunkRetrievalStage, PackageFilterStage, LimitStage, TokenBudgetFormatterStage,
)
from pydocs_mcp.retrieval.retrievers import Bm25ChunkRetriever
from pydocs_mcp.retrieval.formatters import ChunkMarkdownFormatter

provider = PerCallConnectionProvider(cache_path=Path.home() / ".pydocs-mcp" / "db.sqlite")

pipeline = CodeRetrieverPipeline(
    name="fts_chunk",
    stages=(
        ChunkRetrievalStage(retriever=Bm25ChunkRetriever(provider)),
        PackageFilterStage(),
        LimitStage(max_results=8),
        TokenBudgetFormatterStage(formatter=ChunkMarkdownFormatter(), budget=2000),
    ),
)

state = await pipeline.run(SearchQuery(terms="routing", package_filter="fastapi"))
print(state.output)
```

**2. Register a custom predicate and use it:**

```python
from pydocs_mcp.retrieval.predicates import predicate

@predicate("is_long_query")
def _is_long_query(state):
    return len(state.query.terms.split()) > 5

# Now referenceable by name:
ConditionalStage(
    stage=LlmRerankStage(...),
    predicate_name="is_long_query",
)
```

**3. Register a custom stage + use it in YAML:**

```python
@stage_registry.register("my_boost")
@dataclass(frozen=True, slots=True)
class MyBoostStage:
    weight: float = 1.5
    name: str = "my_boost"
    async def run(self, state): ...
    def to_dict(self):    return {"type": "my_boost", "weight": self.weight}
    @classmethod
    def from_dict(cls, d, ctx): return cls(weight=d.get("weight", 1.5))
```

```yaml
# presets/custom.yaml
name: boosted_chunk
stages:
  - type: chunk_retrieval
    retriever: {type: bm25_chunk}
  - type: my_boost
    weight: 2.0
  - type: limit
    max_results: 8
  - type: token_budget_formatter
    formatter: {type: chunk_markdown}
    budget: 2000
```

**4. Load a pipeline from YAML config:**

```python
from pydocs_mcp.retrieval.config import AppConfig

config = AppConfig.load()                                       # or AppConfig.load(Path("my-config.yaml"))
chunk_pipeline = build_chunk_pipeline_from_config(config, context)
member_pipeline = build_member_pipeline_from_config(config, context)
```

### Design patterns used

| Pattern | Where | Role |
|---|---|---|
| **Chain of Responsibility (GoF)** | `PipelineStage` sequence in `CodeRetrieverPipeline` | Each stage takes state, transforms it, passes to the next. |
| **Pipes and Filters** | Same as above | Alternative name; same concept. State flows through named filters. |
| **Strategy (GoF)** | `Retriever` Protocol hierarchy | Interchangeable retrieval implementations (BM25, dense, hybrid) behind one contract. |
| **Plugin Registry** | `ComponentRegistry` + `@register` decorator (stages / retrievers / formatters); `PredicateRegistry` + `@predicate` | Late binding by name; enables config-driven composition; registration is import-time. |
| **Decorator (GoF)** | `ConditionalStage`, `ParallelRetrievalStage`, `SubPipelineStage` | Each wraps inner stages and adds behavior (condition, concurrency, composition) without modifying them. |
| **Adapter (GoF)** | `PipelineChunkRetriever`, `PipelineModuleMemberRetriever` | Expose a `CodeRetrieverPipeline` as a `ChunkRetriever` / `ModuleMemberRetriever` — any sub-pipeline becomes a drop-in retriever. |
| **Specification (DDD)** | `PipelinePredicate` functions + `PredicateRegistry` | Named boolean expressions on `PipelineState`; composable via `ConditionalStage` / `RouteStage`. |
| **Dependency Injection** | `BuildContext` threaded through `from_dict` | Runtime deps (provider, LLM client, predicate registry) injected at deserialization time. |
| **Command-pattern serialization** | `to_dict` / `from_dict` on every component | Any pipeline → JSON/YAML; round-trip identity. Enables config-driven reloads and A/B testing of pipeline variants. |
| **12-factor configuration** | `pydantic-settings` + `YamlConfigSettingsSource` | Layered config — CLI > env > user YAML > (sub-PR #2 code defaults / sub-PR #3 shipped YAML). |

### Architectural choices

- **Uniform `PipelineStage` protocol over role-typed protocols.** Option A (uniform stages) beats Option B (one Protocol per role — `Retriever`, `Filter`, `Fuser`, …) because compound stages (`ParallelRetrievalStage`, `ConditionalStage`, future `TryStage`, `CachingStage`) compose only if every stage has the same shape. Role typing is convention (class-name suffix); the system enforces the contract.
- **`@runtime_checkable` Protocols + explicit inheritance.** Concrete classes write `class X(ChunkStore, TextSearchable):` so mypy enforces method completeness AND `isinstance` works for runtime dispatch. Structural-only typing is legal but forgoes both checks.
- **Pipeline composition over retriever inheritance for hybrid retrieval.** A "hybrid chunk retriever" is a sub-pipeline (`ParallelRetrievalStage + ReciprocalRankFusionStage`) exposed via the `PipelineChunkRetriever` adapter. This beats a dedicated `HybridChunkRetriever` class on ~7 axes (see spec §5 rationale): inspection, per-branch filtering, fusion-algorithm swap, arity, …
- **No factory layer.** Pipelines built inline at each use site (server startup + CLI subcommand). Removing the factory cut ~60 LOC with no loss of flexibility.
- **Async throughout the pipeline; sync inside adapters.** Pipeline, stages, and retrievers are `async def`. CPU-bound SQLite work lives inside concrete stores via `asyncio.to_thread(...)`. Contextvars (sub-PR #3) preserve transaction scope across the thread hop on 3.11+.

---

## 15. Follow-up sub-PRs (not in scope)

Each will get its own brainstorm + spec.

- **Sub-PR #3** — Storage repository layer.
- **Sub-PR #4** — Use-case layer.
- **Sub-PR #5** — Indexer strategy split, including **pluggable chunking strategies**.
  - New `python/pydocs_mcp/chunking/` subpackage mirroring `retrieval/`: `Chunker` Protocol, `chunker_registry`, `@chunker.register("name")` decorator, serializable `to_dict`/`from_dict`, `ChunkerSelector` dispatching per `SourceKind` (markdown_doc, rst_doc, python_module, python_docstring, readme).
  - Concrete strategies: `HeadingChunker` (today's Rust behavior), `SlidingWindowChunker(size, overlap)`, `AstPythonChunker`, `SentenceChunker`, `FixedCharChunker`.
  - Extends the sub-PR #2 `AppConfig` with a `chunking:` YAML section (per-`SourceKind` chunker selection + default).
  - Indexer call sites change from `chunk_text(text)` → `selector.for_source(kind).chunk(text)`. One-line swap per call.
  - Also splits the inspect/static modes inside `indexer.py` into `Extractor` strategies and introduces `SourceProvider` / `IndexCache` abstractions.
  - Optional follow-up hook (not required in #5): add a `chunker` column to the `chunks` table so multiple chunkings of the same source can coexist — useful only once a dense retriever arrives and wants a different chunking scheme than BM25.
- **Sub-PR #6** — Query parsing component + Pydantic at MCP boundary.
- **Sub-PR #7** — Error-tolerance primitives (`TryStage`, `RetryStage`, `CircuitBreakerStage`, `TimedStage`, `CachingStage`).

Future additive work this PR unblocks (no new sub-PR needed beyond adding the external dependency and an extra YAML preset):
- **`DenseChunkRetriever`** — implements `ChunkRetriever`, backed by a vector store. User ships a `chunk_hybrid.yaml` preset referencing it.
- **`LlmRerankStage`** — implements `PipelineStage`. User references it in a YAML preset.
- **More built-in predicates** (e.g., `scope_is_project_only` for exact match, `query_is_code_snippet` for heuristics) — add via `@predicate` decorator in `retrieval/predicates.py`.
