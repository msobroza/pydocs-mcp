# Sub-PR #2 — Async Retrievers + `CodeRetrieverPipeline` + YAML Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce an async, pluggable retrieval + pipeline abstraction with YAML-driven configuration — operators can swap FTS5/hybrid/reranker strategies and route between them without changing user-observable MCP behavior when config is absent.

**Architecture:** New `python/pydocs_mcp/retrieval/` subpackage (9 files) built around: (a) a base `Retriever` Protocol + sub-Protocols per result type, (b) a uniform `PipelineStage` Protocol with compound stages (`ParallelRetrievalStage`, `ConditionalStage`, `RouteStage`, `SubPipelineStage`) that are themselves `PipelineStage`s, (c) component registries (`stage_registry`, `retriever_registry`, `formatter_registry`, `PredicateRegistry`) enabling late binding by name, (d) `pydantic-settings`-backed `AppConfig` loading an optional `pydocs-mcp.yaml` that composes pipelines from shipped YAML presets. All server handlers become async; long-lived SQLite connections are replaced by a `PerCallConnectionProvider` that opens a fresh connection per `acquire()`.

**Tech Stack:** Python 3.11+, `pydantic-settings>=2.0`, `pyyaml>=6.0`, stdlib `asyncio` / `contextlib.asynccontextmanager` / `importlib.resources`, existing SQLite+FTS5 schema from sub-PR #1, Rust native module unchanged.

**Spec source of truth:** [`docs/superpowers/specs/2026-04-19-sub-pr-2-async-retriever-pipeline-design.md`](../specs/2026-04-19-sub-pr-2-async-retriever-pipeline-design.md). §5 of the **sub-PR #1** spec is the authoritative data-model definition — this plan treats `Chunk` / `ModuleMember` / `ChunkList` / `ModuleMemberList` / `SearchQuery` / `SearchResponse` / `PipelineResultItem` exactly as built in sub-PR #1.

**Work location:** Worktree `.claude/worktrees/sub-pr-2-async-retriever-pipeline/` on branch `feature/sub-pr-2-async-retriever-pipeline`, draft PR [#14](https://github.com/msobroza/pydocs-mcp/pull/14).

**Depends on:** sub-PR #1 merged as `a083c90` on `main`. All models, enums, `_row_to_*` helpers, schema v2, and Rust `ParsedMember` are already in place. The 3 `TODO(sub-PR #2)` markers in `search.py` are resolved by **deleting** `search.py` entirely (Task 21) and redistributing its logic into the new retrieval components.

**Repo policy (critical):** No `Co-Authored-By:` trailers on any commit. All commits authored solely by `msobroza`. No git config changes, no `--author` overrides.

---

## File structure

### Files created

- `python/pydocs_mcp/retrieval/__init__.py` — public re-exports
- `python/pydocs_mcp/retrieval/protocols.py` — 6 `Protocol` definitions
- `python/pydocs_mcp/retrieval/pipeline.py` — `PipelineState`, `CodeRetrieverPipeline`, `PerCallConnectionProvider`
- `python/pydocs_mcp/retrieval/serialization.py` — `ComponentRegistry`, `BuildContext`, `stage_registry`, `retriever_registry`, `formatter_registry`
- `python/pydocs_mcp/retrieval/predicates.py` — `PredicateRegistry`, `@predicate` decorator, `default_predicate_registry`, 4 built-ins
- `python/pydocs_mcp/retrieval/formatters.py` — `ChunkMarkdownFormatter`, `ModuleMemberMarkdownFormatter`
- `python/pydocs_mcp/retrieval/retrievers.py` — `Bm25ChunkRetriever`, `LikeMemberRetriever`, `PipelineChunkRetriever`, `PipelineModuleMemberRetriever`
- `python/pydocs_mcp/retrieval/stages.py` — 12 concrete stage classes + `RouteCase`
- `python/pydocs_mcp/retrieval/config.py` — `AppConfig`, `HandlerConfig`, `PipelineRouteEntry`, `AppConfig.load()`, `build_chunk_pipeline_from_config`, `build_member_pipeline_from_config`, internal helpers
- `python/pydocs_mcp/presets/__init__.py` — empty; entry point for `importlib.resources`
- `python/pydocs_mcp/presets/chunk_fts.yaml` — default chunk preset
- `python/pydocs_mcp/presets/member_like.yaml` — default member preset
- `tests/retrieval/__init__.py` — empty; marks directory as pytest package
- `tests/retrieval/test_pipeline.py`
- `tests/retrieval/test_provider.py`
- `tests/retrieval/test_serialization.py`
- `tests/retrieval/test_predicates.py`
- `tests/retrieval/test_formatters.py`
- `tests/retrieval/test_retrievers.py`
- `tests/retrieval/test_stages.py`
- `tests/retrieval/test_config.py`

### Files modified

- `pyproject.toml` — add `pydantic-settings>=2.0` + `pyyaml>=6.0` runtime deps; bump project version to `0.2.0`
- `python/pydocs_mcp/db.py` — add `build_connection_provider(cache_path) -> ConnectionProvider`
- `python/pydocs_mcp/server.py` — all 5 MCP handlers become `async`; `list_packages`/`get_package_doc`/`inspect_module` use `async with provider.acquire()`; `search_docs`/`search_api` call pre-built pipelines; MCP surface byte-identical
- `python/pydocs_mcp/__main__.py` — top-level `--config PATH` flag; `query`/`api` CLI subcommands build throwaway pipelines inline
- `CLAUDE.md` — architecture section documents `retrieval/` subpackage + new deps
- `tests/test_server.py` — mechanical updates for async handlers (no behavior assertions changed)
- `tests/test_db.py` — add coverage for `build_connection_provider`

### Files deleted

- `python/pydocs_mcp/search.py` — logic redistributed into `retrieval/retrievers.py` (SQL) and `retrieval/stages.py` (formatting)

---

## Task 0 — Baseline verification

- [ ] **Step 0.1:** Confirm worktree is current.

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-2-async-retriever-pipeline
git log -1 --oneline
```

Expected: `<sha> chore: scaffold branch for sub-PR #2 — ...`.

- [ ] **Step 0.2:** Activate venv and re-baseline.

```bash
source .venv/bin/activate
pytest -q
```

Expected: `330 passed`.

- [ ] **Step 0.3:** Rust toolchain check.

```bash
. "$HOME/.cargo/env"
cargo fmt --check && cargo clippy -- -D warnings
```

Expected: exit 0.

- [ ] **Step 0.4:** No commit yet.

---

## Task 1 — Add runtime dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1.1:** Edit `pyproject.toml`.

Change `dependencies = ["mcp>=1.0", "pydantic>=2.0"]` to:

```toml
dependencies = ["mcp>=1.0", "pydantic>=2.0", "pydantic-settings>=2.0", "pyyaml>=6.0"]
```

Bump `version = "0.1.0"` to `version = "0.2.0"`.

- [ ] **Step 1.2:** Reinstall the project so the new deps land in the venv.

```bash
uv pip install -e ".[dev]"
```

- [ ] **Step 1.3:** Smoke test.

```bash
python -c "import pydantic_settings, yaml; print(pydantic_settings.__version__, yaml.__version__)"
pytest -q | tail -3
```

Expected: both import; `330 passed`.

- [ ] **Step 1.4:** Commit.

```bash
git add pyproject.toml
git commit -m "chore: add pydantic-settings + pyyaml deps, bump to 0.2.0"
```

---

## Task 2 — Retrieval protocols

**Files:**
- Create: `python/pydocs_mcp/retrieval/__init__.py`
- Create: `python/pydocs_mcp/retrieval/protocols.py`
- Create: `tests/retrieval/__init__.py` (empty)
- Create: `tests/retrieval/test_protocols.py`

- [ ] **Step 2.1:** Create the empty package markers.

```bash
mkdir -p python/pydocs_mcp/retrieval tests/retrieval
touch python/pydocs_mcp/retrieval/__init__.py tests/retrieval/__init__.py
```

- [ ] **Step 2.2:** Write failing test — `tests/retrieval/test_protocols.py`:

```python
"""Protocol smoke tests — verify imports and basic structural shape."""
from __future__ import annotations

from pydocs_mcp.retrieval.protocols import (
    ChunkRetriever,
    ConnectionProvider,
    ModuleMemberRetriever,
    PipelineStage,
    ResultFormatter,
    Retriever,
)


def test_protocol_imports():
    # Each Protocol class should have the `name` attribute declared (for Retriever/Stage)
    assert hasattr(Retriever, "__mro__")
    assert hasattr(PipelineStage, "__mro__")
    assert hasattr(ConnectionProvider, "__mro__")
    assert hasattr(ResultFormatter, "__mro__")


def test_chunk_retriever_subtypes_retriever():
    # ChunkRetriever must inherit from Retriever
    assert Retriever in ChunkRetriever.__mro__


def test_module_member_retriever_subtypes_retriever():
    assert Retriever in ModuleMemberRetriever.__mro__
```

- [ ] **Step 2.3:** Run — expect fail (ImportError).

```bash
pytest tests/retrieval/test_protocols.py -v
```

- [ ] **Step 2.4:** Create `python/pydocs_mcp/retrieval/protocols.py`:

```python
"""Retrieval-pipeline protocols — all structural types for sub-PR #2."""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydocs_mcp.models import (
    Chunk,
    ChunkList,
    ModuleMember,
    ModuleMemberList,
    SearchQuery,
)


@runtime_checkable
class Retriever(Protocol):
    """Any component that produces a ranked list of results from a SearchQuery."""

    name: str


@runtime_checkable
class ChunkRetriever(Retriever, Protocol):
    """A Retriever that returns a ChunkList."""

    async def retrieve(self, query: SearchQuery) -> ChunkList: ...


@runtime_checkable
class ModuleMemberRetriever(Retriever, Protocol):
    """A Retriever that returns a ModuleMemberList."""

    async def retrieve(self, query: SearchQuery) -> ModuleMemberList: ...


@runtime_checkable
class PipelineStage(Protocol):
    """One stage in a CodeRetrieverPipeline. Takes state, returns state."""

    name: str

    async def run(self, state): ...


@runtime_checkable
class ConnectionProvider(Protocol):
    """Yields a SQLite connection scoped to a single operation."""

    def acquire(self) -> AsyncIterator[sqlite3.Connection]: ...


@runtime_checkable
class ResultFormatter(Protocol):
    """Renders one result (Chunk or ModuleMember) as a string payload."""

    def format(self, result: Chunk | ModuleMember) -> str: ...
```

**Note** on `acquire()`: the decorator `@asynccontextmanager` is applied on concrete implementations (e.g. `PerCallConnectionProvider` in Task 3). The Protocol describes the shape; you cannot declaratively type the decorator inside the Protocol itself in a way mypy accepts cleanly — keeping the return type as `AsyncIterator` matches the async-context-manager protocol at runtime.

- [ ] **Step 2.5:** Run — expect 3 passing.

```bash
pytest tests/retrieval/test_protocols.py -v
```

- [ ] **Step 2.6:** Commit.

```bash
git add python/pydocs_mcp/retrieval/__init__.py python/pydocs_mcp/retrieval/protocols.py tests/retrieval/__init__.py tests/retrieval/test_protocols.py
git commit -m "feat(retrieval): add Retriever/PipelineStage/ConnectionProvider/ResultFormatter protocols (spec §5.1)"
```

---

## Task 3 — Pipeline primitives

**Files:**
- Create: `python/pydocs_mcp/retrieval/pipeline.py`
- Create: `tests/retrieval/test_pipeline.py`
- Create: `tests/retrieval/test_provider.py`

- [ ] **Step 3.1:** Write failing tests — `tests/retrieval/test_pipeline.py`:

```python
"""Tests for CodeRetrieverPipeline + PipelineState."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from pydocs_mcp.models import ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState


@dataclass(frozen=True, slots=True)
class _AppendStage:
    """Test fake — records runs in a shared list."""
    name: str
    log: list

    async def run(self, state: PipelineState) -> PipelineState:
        self.log.append(self.name)
        return state


@pytest.mark.asyncio
async def test_pipeline_state_defaults():
    q = SearchQuery(terms="x")
    s = PipelineState(query=q)
    assert s.query is q
    assert s.result is None
    assert s.duration_ms == 0.0


def test_pipeline_state_frozen():
    s = PipelineState(query=SearchQuery(terms="x"))
    with pytest.raises(Exception):
        s.query = SearchQuery(terms="y")


@pytest.mark.asyncio
async def test_pipeline_runs_stages_in_order():
    log: list[str] = []
    pipeline = CodeRetrieverPipeline(
        name="p",
        stages=(_AppendStage(name="a", log=log), _AppendStage(name="b", log=log)),
    )
    state = await pipeline.run(SearchQuery(terms="x"))
    assert log == ["a", "b"]
    assert state.query.terms == "x"


@pytest.mark.asyncio
async def test_pipeline_empty_stages_is_noop():
    pipeline = CodeRetrieverPipeline(name="empty", stages=())
    state = await pipeline.run(SearchQuery(terms="x"))
    assert state.query.terms == "x"
    assert state.result is None


@pytest.mark.asyncio
async def test_pipeline_to_dict_roundtrip_name_and_stages_shape():
    @dataclass(frozen=True, slots=True)
    class _TrivialStage:
        name: str = "trivial"

        async def run(self, state): return state
        def to_dict(self) -> dict: return {"type": "trivial"}

    pipeline = CodeRetrieverPipeline(name="p", stages=(_TrivialStage(),))
    d = pipeline.to_dict()
    assert d == {"name": "p", "stages": [{"type": "trivial"}]}
```

Create `tests/retrieval/test_provider.py`:

```python
"""Tests for PerCallConnectionProvider."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider


@pytest.mark.asyncio
async def test_provider_opens_and_closes_connection(tmp_path: Path):
    db_file = tmp_path / "test.db"
    # Seed a real SQLite DB (so the provider can open it)
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE ping (id INTEGER)")
    con.close()

    provider = PerCallConnectionProvider(cache_path=db_file)
    async with provider.acquire() as conn:
        # row_factory + WAL + read-write usage all work on the yielded connection
        assert conn.row_factory is sqlite3.Row
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ping'")
        assert cur.fetchone()["name"] == "ping"

    # After the context exits, the connection is closed — a second op should fail
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1").fetchone()


@pytest.mark.asyncio
async def test_provider_independent_connections_per_call(tmp_path: Path):
    db_file = tmp_path / "test.db"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE t (v INTEGER)")
    con.commit()
    con.close()

    provider = PerCallConnectionProvider(cache_path=db_file)
    async with provider.acquire() as c1:
        c1.execute("INSERT INTO t VALUES (1)")
        c1.commit()

    async with provider.acquire() as c2:
        row = c2.execute("SELECT v FROM t").fetchone()
        assert row["v"] == 1
```

- [ ] **Step 3.2:** Add `pytest-asyncio` to dev deps (if not already present). Check `pyproject.toml`'s `[project.optional-dependencies]` block — likely need to add `pytest-asyncio>=0.23` and then `uv pip install -e ".[dev]"` again.

Also update `pyproject.toml`'s `[tool.pytest.ini_options]` to enable asyncio mode so `@pytest.mark.asyncio` works globally:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["python"]
asyncio_mode = "auto"
```

- [ ] **Step 3.3:** Run — expect fail (ImportError).

```bash
pytest tests/retrieval/test_pipeline.py tests/retrieval/test_provider.py -v
```

- [ ] **Step 3.4:** Create `python/pydocs_mcp/retrieval/pipeline.py`:

```python
"""Pipeline primitives: PipelineState, CodeRetrieverPipeline, PerCallConnectionProvider."""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.models import (
    PipelineResultItem,
    SearchQuery,
)

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import PipelineStage
    from pydocs_mcp.retrieval.serialization import BuildContext


@dataclass(frozen=True, slots=True)
class PipelineState:
    """Immutable state threaded through a CodeRetrieverPipeline's stages."""

    query: SearchQuery
    result: PipelineResultItem | None = None
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class CodeRetrieverPipeline:
    """Linear async pipeline of PipelineStages; runs them in order."""

    name: str
    stages: tuple["PipelineStage", ...]

    async def run(self, query: SearchQuery) -> PipelineState:
        state = PipelineState(query=query)
        for stage in self.stages:
            state = await stage.run(state)
        return state

    def to_dict(self) -> dict:
        return {"name": self.name, "stages": [s.to_dict() for s in self.stages]}

    @classmethod
    def from_dict(cls, data: dict, context: "BuildContext") -> "CodeRetrieverPipeline":
        return cls(
            name=data["name"],
            stages=tuple(
                context.stage_registry.build(s, context) for s in data["stages"]
            ),
        )


@dataclass(frozen=True, slots=True)
class PerCallConnectionProvider:
    """Default ConnectionProvider — opens/closes a fresh SQLite conn per acquire()."""

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

- [ ] **Step 3.5:** Run — expect pass.

```bash
pytest tests/retrieval/test_pipeline.py tests/retrieval/test_provider.py -v
```

- [ ] **Step 3.6:** Run full suite — still green.

```bash
pytest -q | tail -3
```

- [ ] **Step 3.7:** Commit.

```bash
git add python/pydocs_mcp/retrieval/pipeline.py tests/retrieval/test_pipeline.py tests/retrieval/test_provider.py pyproject.toml
git commit -m "feat(retrieval): add PipelineState, CodeRetrieverPipeline, PerCallConnectionProvider (spec §5.2)"
```

---

## Task 4 — `db.build_connection_provider` factory

**Files:**
- Modify: `python/pydocs_mcp/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 4.1:** Append to `tests/test_db.py` (merge any new imports into the existing top block; no mid-file imports):

```python
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database


@pytest.mark.asyncio
async def test_build_connection_provider_opens_valid_db(tmp_path: Path):
    db_file = tmp_path / "factory.db"
    # Initialize v2 schema
    conn = open_index_database(db_file)
    conn.close()

    provider = build_connection_provider(db_file)
    async with provider.acquire() as c:
        assert c.row_factory is sqlite3.Row
        tables = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"packages", "chunks", "module_members"}.issubset(tables)
```

- [ ] **Step 4.2:** Run — expect fail (ImportError on `build_connection_provider`).

- [ ] **Step 4.3:** Append to `python/pydocs_mcp/db.py`:

```python
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider


def build_connection_provider(cache_path: Path):
    """Factory — returns the default ConnectionProvider for a given DB path.

    Kept inside db.py so call sites (server, CLI) don't need to know about
    PerCallConnectionProvider directly; they just ask db for a provider.
    """
    return PerCallConnectionProvider(cache_path=cache_path)
```

- [ ] **Step 4.4:** Run — expect pass.

- [ ] **Step 4.5:** Full-suite re-check.

```bash
pytest -q | tail -3
```

- [ ] **Step 4.6:** Commit.

```bash
git add python/pydocs_mcp/db.py tests/test_db.py
git commit -m "feat(db): add build_connection_provider factory (spec §8)"
```

---

## Task 5 — Registries + `BuildContext`

**Files:**
- Create: `python/pydocs_mcp/retrieval/serialization.py`
- Create: `tests/retrieval/test_serialization.py`

- [ ] **Step 5.1:** Write failing tests — `tests/retrieval/test_serialization.py`:

```python
"""Tests for ComponentRegistry + BuildContext (spec §5.3)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    ComponentRegistry,
    formatter_registry,
    retriever_registry,
    stage_registry,
)


def test_registry_register_and_build(tmp_path: Path):
    registry: ComponentRegistry = ComponentRegistry()

    @registry.register("echo")
    @dataclass(frozen=True, slots=True)
    class Echo:
        msg: str
        name: str = "echo"

        def to_dict(self) -> dict: return {"type": "echo", "msg": self.msg}

        @classmethod
        def from_dict(cls, d, ctx):
            return cls(msg=d["msg"])

    assert registry.names() == ("echo",)

    ctx = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )
    instance = registry.build({"type": "echo", "msg": "hi"}, ctx)
    assert isinstance(instance, Echo)
    assert instance.msg == "hi"


def test_registry_collision_raises():
    registry: ComponentRegistry = ComponentRegistry()

    @registry.register("dup")
    @dataclass(frozen=True, slots=True)
    class A:
        name: str = "a"

    with pytest.raises(ValueError, match="already registered"):
        @registry.register("dup")
        @dataclass(frozen=True, slots=True)
        class B:
            name: str = "b"


def test_registry_unknown_type_raises_listing_known(tmp_path: Path):
    registry: ComponentRegistry = ComponentRegistry()

    @registry.register("known1")
    @dataclass(frozen=True, slots=True)
    class K1:
        name: str = "k1"
        def to_dict(self): return {"type": "known1"}
        @classmethod
        def from_dict(cls, d, ctx): return cls()

    ctx = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )
    with pytest.raises(KeyError, match="unknown component type 'missing'"):
        registry.build({"type": "missing"}, ctx)


def test_shared_registries_exist():
    # Shared module-level instances
    assert isinstance(stage_registry, ComponentRegistry)
    assert isinstance(retriever_registry, ComponentRegistry)
    assert isinstance(formatter_registry, ComponentRegistry)


def test_build_context_defaults(tmp_path: Path):
    ctx = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )
    # default registries populated
    assert ctx.stage_registry is stage_registry
    assert ctx.retriever_registry is retriever_registry
    assert ctx.formatter_registry is formatter_registry
    # predicate_registry defaults to the default_predicate_registry (Task 6)
    assert ctx.predicate_registry is not None
```

- [ ] **Step 5.2:** Run — expect fail.

- [ ] **Step 5.3:** Create `python/pydocs_mcp/retrieval/serialization.py`:

```python
"""Component registries + BuildContext for config-driven pipeline assembly."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.predicates import PredicateRegistry
    from pydocs_mcp.retrieval.protocols import ConnectionProvider


C = TypeVar("C")


class ComponentRegistry(Generic[C]):
    """Decorator-based registry mapping a short type-name string to a class.

    Used by the config layer to build pipeline components from YAML dicts. Each
    component's ``to_dict`` output includes ``{"type": "<name>"}``; the
    registry's ``build()`` looks up the class and delegates to its
    ``from_dict``.
    """

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


# Three shared registries — concrete stages / retrievers / formatters decorate against these.
stage_registry: ComponentRegistry = ComponentRegistry()
retriever_registry: ComponentRegistry = ComponentRegistry()
formatter_registry: ComponentRegistry = ComponentRegistry()


@dataclass(frozen=True, slots=True)
class BuildContext:
    """Runtime dependencies passed through every from_dict recursion.

    Add fields here additively when new runtime deps appear in later PRs; that
    way intermediate call sites don't have to be updated to thread new kwargs.
    """

    connection_provider: "ConnectionProvider"
    predicate_registry: "PredicateRegistry" = field(
        default_factory=lambda: _lazy_default_predicate_registry()
    )
    stage_registry: ComponentRegistry = field(default_factory=lambda: stage_registry)
    retriever_registry: ComponentRegistry = field(default_factory=lambda: retriever_registry)
    formatter_registry: ComponentRegistry = field(default_factory=lambda: formatter_registry)


def _lazy_default_predicate_registry():
    """Lazy import to avoid circular dep — predicates module imports from here."""
    from pydocs_mcp.retrieval.predicates import default_predicate_registry
    return default_predicate_registry
```

- [ ] **Step 5.4:** Run — the `test_build_context_defaults` test will fail until Task 6 creates `default_predicate_registry`. Until then, stub: add a minimal `predicates.py` creating `default_predicate_registry = None` — or re-order Task 6 to run before 5. **Do Task 6 first**, then return here.

**Reorder note:** Swap the Task 5 and Task 6 order in execution — implement Task 6 first so `default_predicate_registry` exists, then Task 5 can reference it cleanly. The rest of the plan still holds.

- [ ] **Step 5.5:** After Task 6 lands, re-run the serialization tests — expect all 5 passing.

- [ ] **Step 5.6:** Commit.

```bash
git add python/pydocs_mcp/retrieval/serialization.py tests/retrieval/test_serialization.py
git commit -m "feat(retrieval): add ComponentRegistry + BuildContext + 3 shared registries (spec §5.3)"
```

---

## Task 6 — Predicate registry + 4 built-ins

**Files:**
- Create: `python/pydocs_mcp/retrieval/predicates.py`
- Create: `tests/retrieval/test_predicates.py`

**Execute before Task 5** (see reorder note above).

- [ ] **Step 6.1:** Write failing tests:

```python
"""Tests for PredicateRegistry + built-in predicates."""
from __future__ import annotations

import pytest

from pydocs_mcp.models import ChunkFilterField, ChunkList, SearchQuery, SearchScope
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.predicates import (
    PredicateRegistry,
    default_predicate_registry,
    predicate,
)


def _state_with(*, terms: str = "x", scope_value: str | None = None, result=None):
    pre_filter = {ChunkFilterField.SCOPE.value: scope_value} if scope_value else None
    q = SearchQuery(terms=terms, pre_filter=pre_filter)
    return PipelineState(query=q, result=result)


def test_predicate_registration():
    registry = PredicateRegistry()

    @predicate("always_true", registry=registry)
    def _t(state): return True

    assert "always_true" in registry.names()
    assert registry.get("always_true")(_state_with()) is True


def test_predicate_collision_raises():
    registry = PredicateRegistry()

    @predicate("dup", registry=registry)
    def _a(state): return True

    with pytest.raises(ValueError, match="already registered"):
        @predicate("dup", registry=registry)
        def _b(state): return False


def test_predicate_unknown_raises_with_known_list():
    registry = PredicateRegistry()

    @predicate("one", registry=registry)
    def _p(state): return True

    with pytest.raises(KeyError, match="registered: \\['one'\\]"):
        registry.get("missing")


def test_has_matches_builtin():
    has_matches = default_predicate_registry.get("has_matches")
    assert has_matches(_state_with(result=None)) is False
    assert has_matches(_state_with(result=ChunkList(items=()))) is False
    from pydocs_mcp.models import Chunk
    assert has_matches(_state_with(result=ChunkList(items=(Chunk(text="x"),)))) is True


def test_query_has_multiple_terms_builtin():
    pred = default_predicate_registry.get("query_has_multiple_terms")
    # 3-word query: not "multiple" per the >=4 rule from spec §5.4
    assert pred(_state_with(terms="a b c")) is False
    assert pred(_state_with(terms="a b c d")) is True


def test_scope_predicates_with_missing_scope():
    """Missing scope in pre_filter means `ALL` semantically — both predicates true."""
    incl_deps = default_predicate_registry.get("scope_includes_dependencies")
    incl_proj = default_predicate_registry.get("scope_includes_project")
    s = _state_with(terms="x")
    assert incl_deps(s) is True
    assert incl_proj(s) is True


def test_scope_predicates_project_only():
    incl_deps = default_predicate_registry.get("scope_includes_dependencies")
    incl_proj = default_predicate_registry.get("scope_includes_project")
    s = _state_with(scope_value=SearchScope.PROJECT_ONLY.value)
    assert incl_deps(s) is False
    assert incl_proj(s) is True


def test_scope_predicates_dependencies_only():
    incl_deps = default_predicate_registry.get("scope_includes_dependencies")
    incl_proj = default_predicate_registry.get("scope_includes_project")
    s = _state_with(scope_value=SearchScope.DEPENDENCIES_ONLY.value)
    assert incl_deps(s) is True
    assert incl_proj(s) is False
```

- [ ] **Step 6.2:** Run — expect fail.

- [ ] **Step 6.3:** Create `python/pydocs_mcp/retrieval/predicates.py`:

```python
"""Named-predicate registry for Conditional/Route stages (spec §5.4).

Predicates are functions on PipelineState -> bool. Referenced by plain string
names in YAML configs; resolved at stage run() time via a PredicateRegistry.
"""
from __future__ import annotations

from collections.abc import Callable

from pydocs_mcp.models import ChunkFilterField, SearchScope
from pydocs_mcp.retrieval.pipeline import PipelineState

PipelinePredicate = Callable[[PipelineState], bool]


class PredicateRegistry:
    """Registry mapping predicate-name -> predicate callable."""

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
    """Decorator — registers a predicate with the given registry by name."""
    def decorator(fn: PipelinePredicate) -> PipelinePredicate:
        registry.register(name, fn)
        return fn
    return decorator


# ── Built-in predicates (registered on module import) ─────────────────────

def _scope_value(state: PipelineState) -> str | None:
    pf = state.query.pre_filter or {}
    v = pf.get(ChunkFilterField.SCOPE.value)
    return v


@predicate("has_matches")
def _has_matches(state: PipelineState) -> bool:
    """True if state.result carries at least one item."""
    if state.result is None:
        return False
    return len(state.result.items) > 0


@predicate("query_has_multiple_terms")
def _query_has_multiple_terms(state: PipelineState) -> bool:
    """True when the query has 4+ space-separated terms (heuristic for 'long query')."""
    return len(state.query.terms.split()) >= 4


@predicate("scope_includes_dependencies")
def _scope_includes_dependencies(state: PipelineState) -> bool:
    """True unless scope is explicitly PROJECT_ONLY."""
    v = _scope_value(state)
    return v != SearchScope.PROJECT_ONLY.value


@predicate("scope_includes_project")
def _scope_includes_project(state: PipelineState) -> bool:
    """True unless scope is explicitly DEPENDENCIES_ONLY."""
    v = _scope_value(state)
    return v != SearchScope.DEPENDENCIES_ONLY.value
```

- [ ] **Step 6.4:** Run — expect all tests green.

```bash
pytest tests/retrieval/test_predicates.py -v
```

- [ ] **Step 6.5:** Commit.

```bash
git add python/pydocs_mcp/retrieval/predicates.py tests/retrieval/test_predicates.py
git commit -m "feat(retrieval): add PredicateRegistry + @predicate + 4 built-ins (spec §5.4)"
```

---

## Task 7 — Concrete formatters

**Files:**
- Create: `python/pydocs_mcp/retrieval/formatters.py`
- Create: `tests/retrieval/test_formatters.py`

- [ ] **Step 7.1:** Write failing tests:

```python
"""Tests for ChunkMarkdownFormatter + ModuleMemberMarkdownFormatter."""
from __future__ import annotations

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    MemberKind,
    ModuleMember,
    ModuleMemberFilterField,
)
from pydocs_mcp.retrieval.formatters import (
    ChunkMarkdownFormatter,
    ModuleMemberMarkdownFormatter,
)
from pydocs_mcp.retrieval.serialization import BuildContext, formatter_registry
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider


def test_chunk_markdown_formatter_renders_title_and_text():
    f = ChunkMarkdownFormatter()
    c = Chunk(
        text="body text",
        metadata={ChunkFilterField.TITLE.value: "Hello"},
    )
    assert f.format(c) == "## Hello\n\nbody text"


def test_chunk_markdown_formatter_empty_title_ok():
    f = ChunkMarkdownFormatter()
    c = Chunk(text="body")
    assert f.format(c) == "## \n\nbody"


def test_member_markdown_formatter_renders_fields():
    f = ModuleMemberMarkdownFormatter()
    m = ModuleMember(metadata={
        ModuleMemberFilterField.PACKAGE.value: "fastapi",
        ModuleMemberFilterField.MODULE.value: "fastapi.routing",
        ModuleMemberFilterField.NAME.value: "APIRouter",
        ModuleMemberFilterField.KIND.value: MemberKind.CLASS.value,
        "signature": "(prefix: str = '')",
        "docstring": "Groups endpoints.",
    })
    result = f.format(m)
    assert "[fastapi]" in result
    assert "fastapi.routing.APIRouter" in result
    assert "(prefix: str = '')" in result
    assert "(class)" in result
    assert "Groups endpoints." in result


def test_formatter_to_dict_from_dict_roundtrip(tmp_path):
    for cls in (ChunkMarkdownFormatter, ModuleMemberMarkdownFormatter):
        instance = cls()
        d = instance.to_dict()
        ctx = BuildContext(
            connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
        )
        rebuilt = formatter_registry.build(d, ctx)
        assert type(rebuilt) is cls
```

- [ ] **Step 7.2:** Run — expect fail.

- [ ] **Step 7.3:** Create `python/pydocs_mcp/retrieval/formatters.py`:

```python
"""Result formatters — render Chunks / ModuleMembers as markdown strings."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
)
from pydocs_mcp.retrieval.serialization import BuildContext, formatter_registry


@formatter_registry.register("chunk_markdown")
@dataclass(frozen=True, slots=True)
class ChunkMarkdownFormatter:
    """Renders a Chunk as `## {title}\\n\\n{text}`."""

    name: str = "chunk_markdown"

    def format(self, result: Chunk | ModuleMember) -> str:
        # Type-narrow: this formatter is registered for chunks.
        title = ""
        if isinstance(result, Chunk):
            title = result.metadata.get(ChunkFilterField.TITLE.value, "") or ""
            body = result.text or ""
        else:  # defensive — but not expected per registry dispatch
            body = ""
        return f"## {title}\n\n{body}"

    def to_dict(self) -> dict:
        return {"type": "chunk_markdown"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ChunkMarkdownFormatter":
        return cls()


@formatter_registry.register("member_markdown")
@dataclass(frozen=True, slots=True)
class ModuleMemberMarkdownFormatter:
    """Renders a ModuleMember as `**[{package}] {module}.{name}{signature}** ({kind})\\n{docstring}`."""

    name: str = "member_markdown"

    def format(self, result: Chunk | ModuleMember) -> str:
        if not isinstance(result, ModuleMember):
            return ""
        md = result.metadata
        package = md.get(ModuleMemberFilterField.PACKAGE.value, "")
        module = md.get(ModuleMemberFilterField.MODULE.value, "")
        name = md.get(ModuleMemberFilterField.NAME.value, "")
        kind = md.get(ModuleMemberFilterField.KIND.value, "")
        signature = md.get("signature", "") or ""
        docstring = md.get("docstring", "") or ""
        header = f"**[{package}] {module}.{name}{signature}** ({kind})"
        return f"{header}\n{docstring}"

    def to_dict(self) -> dict:
        return {"type": "member_markdown"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ModuleMemberMarkdownFormatter":
        return cls()
```

- [ ] **Step 7.4:** Run — expect pass; commit.

```bash
pytest tests/retrieval/test_formatters.py -v
git add python/pydocs_mcp/retrieval/formatters.py tests/retrieval/test_formatters.py
git commit -m "feat(retrieval): add ChunkMarkdown + ModuleMemberMarkdown formatters (spec §5.7)"
```

---

## Task 8 — Concrete retrievers: `Bm25ChunkRetriever` + `LikeMemberRetriever`

**Files:**
- Create: `python/pydocs_mcp/retrieval/retrievers.py`
- Create: `tests/retrieval/test_retrievers.py`

The SQL inside these retrievers replaces the work currently done by `search.py::retrieve_chunks` and `retrieve_module_members`. Drop the column-aliasing `AS pkg / AS heading / AS body / AS doc / AS returns / AS params` — retrievers produce domain models directly via the `_row_to_chunk` / `_row_to_module_member` helpers already in `db.py`.

- [ ] **Step 8.1:** Write failing tests — core coverage for each retriever against a tmp SQLite DB seeded with v2 schema rows:

```python
"""Tests for concrete retrievers against fixture DB."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import (
    ChunkFilterField,
    ChunkList,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
    SearchScope,
)
from pydocs_mcp.retrieval.retrievers import Bm25ChunkRetriever, LikeMemberRetriever


@pytest.fixture
def seeded_db(tmp_path: Path):
    db_file = tmp_path / "seed.db"
    conn = open_index_database(db_file)
    conn.execute(
        "INSERT INTO chunks (package, title, text, origin) VALUES (?,?,?,?)",
        ("fastapi", "Routing", "Use APIRouter to group related endpoints.", "dependency_doc_file"),
    )
    conn.execute(
        "INSERT INTO chunks (package, title, text, origin) VALUES (?,?,?,?)",
        ("__project__", "README", "Project overview", "project_module_doc"),
    )
    conn.execute(
        "INSERT INTO module_members "
        "(package, module, name, kind, signature, return_annotation, parameters, docstring) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("fastapi", "fastapi.routing", "APIRouter", "class",
         "(prefix: str = '')", "", json.dumps([]), "Groups endpoints."),
    )
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return db_file


@pytest.mark.asyncio
async def test_bm25_chunk_retriever_returns_chunk_list(seeded_db: Path):
    provider = build_connection_provider(seeded_db)
    r = Bm25ChunkRetriever(provider=provider)
    result = await r.retrieve(SearchQuery(terms="APIRouter"))
    assert isinstance(result, ChunkList)
    assert len(result.items) >= 1
    first = result.items[0]
    assert first.metadata[ChunkFilterField.PACKAGE.value] == "fastapi"
    assert first.retriever_name == "bm25_chunk"
    assert first.relevance is not None


@pytest.mark.asyncio
async def test_bm25_chunk_retriever_respects_package_filter(seeded_db: Path):
    provider = build_connection_provider(seeded_db)
    r = Bm25ChunkRetriever(provider=provider)
    result = await r.retrieve(SearchQuery(
        terms="Project",
        pre_filter={ChunkFilterField.PACKAGE.value: "__project__"},
    ))
    for chunk in result.items:
        assert chunk.metadata[ChunkFilterField.PACKAGE.value] == "__project__"


@pytest.mark.asyncio
async def test_like_member_retriever_returns_module_member_list(seeded_db: Path):
    provider = build_connection_provider(seeded_db)
    r = LikeMemberRetriever(provider=provider)
    result = await r.retrieve(SearchQuery(terms="APIRouter"))
    assert isinstance(result, ModuleMemberList)
    assert len(result.items) >= 1
    m = result.items[0]
    assert m.metadata[ModuleMemberFilterField.NAME.value] == "APIRouter"
    assert m.retriever_name == "like_member"
```

- [ ] **Step 8.2:** Run — expect fail.

- [ ] **Step 8.3:** Create `python/pydocs_mcp/retrieval/retrievers.py`:

```python
"""Concrete retrievers — replace the retrieval half of the deleted search.py."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.db import _row_to_chunk, _row_to_module_member
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
    SearchScope,
)
from pydocs_mcp.retrieval.serialization import BuildContext, retriever_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
    from pydocs_mcp.retrieval.protocols import ConnectionProvider


def _apply_scope(where: list[str], scope: SearchScope, column: str) -> None:
    if scope is SearchScope.PROJECT_ONLY:
        where.append(f"{column} = '__project__'")
    elif scope is SearchScope.DEPENDENCIES_ONLY:
        where.append(f"{column} != '__project__'")


@retriever_registry.register("bm25_chunk")
@dataclass(frozen=True, slots=True)
class Bm25ChunkRetriever:
    """BM25 FTS5 retriever over the `chunks` table."""

    provider: "ConnectionProvider"
    name: str = "bm25_chunk"

    async def retrieve(self, query: SearchQuery) -> ChunkList:
        return await asyncio.to_thread(self._retrieve_sync, query)

    def _retrieve_sync(self, query: SearchQuery) -> ChunkList:
        fts_ops = {"OR", "AND", "NOT"}
        tokens = query.terms.split()
        if any(t in fts_ops for t in tokens):
            fulltext = query.terms
        else:
            words = [w for w in tokens if len(w) > 1]
            if not words:
                return ChunkList(items=())
            fulltext = " OR ".join(f'"{w}"' for w in words)

        where = ["chunks_fts MATCH ?"]
        params: list = [fulltext]

        pf = query.pre_filter or {}
        package = pf.get(ChunkFilterField.PACKAGE.value)
        if package is not None:
            literal = package if package == "__project__" else normalize_package_name(package)
            where.append("c.package = ?")
            params.append(literal)

        scope_value = pf.get(ChunkFilterField.SCOPE.value)
        if scope_value is not None:
            _apply_scope(where, SearchScope(scope_value), "c.package")

        params.append(query.max_results)
        sql = (
            "SELECT c.id, c.package, c.title, c.text, c.origin, -m.rank AS rank "
            "FROM chunks_fts m JOIN chunks c ON c.id = m.rowid "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY rank LIMIT ?"
        )

        import sqlite3
        # Synchronous open inside the worker thread
        conn = sqlite3.connect(str(self.provider.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            return ChunkList(items=())
        finally:
            conn.close()

        items: list[Chunk] = []
        for row in rows:
            chunk = _row_to_chunk(row)
            items.append(
                Chunk(
                    text=chunk.text,
                    id=chunk.id,
                    relevance=float(row["rank"]),
                    retriever_name=self.name,
                    metadata=dict(chunk.metadata),  # unwrap MappingProxy for re-wrapping
                )
            )
        return ChunkList(items=tuple(items))

    def to_dict(self) -> dict:
        return {"type": "bm25_chunk"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "Bm25ChunkRetriever":
        return cls(provider=context.connection_provider)


@retriever_registry.register("like_member")
@dataclass(frozen=True, slots=True)
class LikeMemberRetriever:
    """LIKE retriever over `module_members.name` / `docstring`."""

    provider: "ConnectionProvider"
    name: str = "like_member"

    async def retrieve(self, query: SearchQuery) -> ModuleMemberList:
        return await asyncio.to_thread(self._retrieve_sync, query)

    def _retrieve_sync(self, query: SearchQuery) -> ModuleMemberList:
        escaped = (query.terms
                   .replace("\\", "\\\\")
                   .replace("%", "\\%")
                   .replace("_", "\\_"))
        pat = f"%{escaped}%"

        where = ["(lower(name) LIKE ? ESCAPE '\\' OR lower(docstring) LIKE ? ESCAPE '\\')"]
        params: list = [pat, pat]

        pf = query.pre_filter or {}
        package = pf.get(ModuleMemberFilterField.PACKAGE.value)
        if package is not None:
            literal = package if package == "__project__" else normalize_package_name(package)
            where.append("package = ?")
            params.append(literal)

        scope_value = pf.get(ChunkFilterField.SCOPE.value)
        if scope_value is not None:
            _apply_scope(where, SearchScope(scope_value), "package")

        params.append(query.max_results)
        sql = (
            "SELECT id, package, module, name, kind, signature, "
            "return_annotation, parameters, docstring "
            "FROM module_members "
            f"WHERE {' AND '.join(where)} "
            "LIMIT ?"
        )

        import sqlite3
        conn = sqlite3.connect(str(self.provider.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            return ModuleMemberList(items=())
        finally:
            conn.close()

        items: list[ModuleMember] = []
        for row in rows:
            member = _row_to_module_member(row)
            items.append(
                ModuleMember(
                    id=member.id,
                    relevance=None,
                    retriever_name=self.name,
                    metadata=dict(member.metadata),
                )
            )
        return ModuleMemberList(items=tuple(items))

    def to_dict(self) -> dict:
        return {"type": "like_member"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "LikeMemberRetriever":
        return cls(provider=context.connection_provider)
```

- [ ] **Step 8.4:** Run — expect pass.

- [ ] **Step 8.5:** Commit.

```bash
git add python/pydocs_mcp/retrieval/retrievers.py tests/retrieval/test_retrievers.py
git commit -m "feat(retrieval): add Bm25ChunkRetriever + LikeMemberRetriever (spec §5.5)"
```

---

## Task 9 — Pipeline-as-retriever adapters

**Files:**
- Modify: `python/pydocs_mcp/retrieval/retrievers.py` (append)
- Modify: `tests/retrieval/test_retrievers.py` (append)

- [ ] **Step 9.1:** Append tests:

```python
@pytest.mark.asyncio
async def test_pipeline_chunk_retriever_forwards_to_inner_pipeline(tmp_path):
    """Adapter runs the inner pipeline and returns the ChunkList at state.result."""
    from dataclasses import dataclass
    from pydocs_mcp.models import Chunk
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState
    from pydocs_mcp.retrieval.retrievers import PipelineChunkRetriever

    @dataclass(frozen=True, slots=True)
    class _ReturnOneChunk:
        name: str = "return_one"
        async def run(self, state: PipelineState) -> PipelineState:
            return PipelineState(
                query=state.query,
                result=ChunkList(items=(Chunk(text="payload"),)),
            )

    inner = CodeRetrieverPipeline(name="inner", stages=(_ReturnOneChunk(),))
    adapter = PipelineChunkRetriever(pipeline=inner)
    out = await adapter.retrieve(SearchQuery(terms="x"))
    assert isinstance(out, ChunkList)
    assert len(out.items) == 1
    assert out.items[0].text == "payload"


@pytest.mark.asyncio
async def test_pipeline_module_member_retriever_forwards():
    from dataclasses import dataclass
    from pydocs_mcp.models import ModuleMember
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState
    from pydocs_mcp.retrieval.retrievers import PipelineModuleMemberRetriever

    @dataclass(frozen=True, slots=True)
    class _ReturnOneMember:
        name: str = "return_one"
        async def run(self, state: PipelineState) -> PipelineState:
            return PipelineState(
                query=state.query,
                result=ModuleMemberList(items=(ModuleMember(metadata={"name": "f"}),)),
            )

    inner = CodeRetrieverPipeline(name="inner", stages=(_ReturnOneMember(),))
    adapter = PipelineModuleMemberRetriever(pipeline=inner)
    out = await adapter.retrieve(SearchQuery(terms="x"))
    assert isinstance(out, ModuleMemberList)
    assert len(out.items) == 1
```

- [ ] **Step 9.2:** Run — expect fail.

- [ ] **Step 9.3:** Append to `python/pydocs_mcp/retrieval/retrievers.py`:

```python
@retriever_registry.register("pipeline_chunk")
@dataclass(frozen=True, slots=True)
class PipelineChunkRetriever:
    """Adapter — exposes an inner pipeline that produces a ChunkList as a ChunkRetriever."""

    pipeline: "CodeRetrieverPipeline"
    name: str = "pipeline_chunk"

    async def retrieve(self, query: SearchQuery) -> ChunkList:
        state = await self.pipeline.run(query)
        if isinstance(state.result, ChunkList):
            return state.result
        return ChunkList(items=())

    def to_dict(self) -> dict:
        return {"type": "pipeline_chunk", "pipeline": self.pipeline.to_dict()}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "PipelineChunkRetriever":
        from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
        return cls(pipeline=CodeRetrieverPipeline.from_dict(data["pipeline"], context))


@retriever_registry.register("pipeline_member")
@dataclass(frozen=True, slots=True)
class PipelineModuleMemberRetriever:
    """Adapter — exposes an inner pipeline that produces a ModuleMemberList as a ModuleMemberRetriever."""

    pipeline: "CodeRetrieverPipeline"
    name: str = "pipeline_member"

    async def retrieve(self, query: SearchQuery) -> ModuleMemberList:
        state = await self.pipeline.run(query)
        if isinstance(state.result, ModuleMemberList):
            return state.result
        return ModuleMemberList(items=())

    def to_dict(self) -> dict:
        return {"type": "pipeline_member", "pipeline": self.pipeline.to_dict()}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "PipelineModuleMemberRetriever":
        from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
        return cls(pipeline=CodeRetrieverPipeline.from_dict(data["pipeline"], context))
```

- [ ] **Step 9.4:** Run — expect pass; commit.

```bash
pytest tests/retrieval/test_retrievers.py -v
git add python/pydocs_mcp/retrieval/retrievers.py tests/retrieval/test_retrievers.py
git commit -m "feat(retrieval): add Pipeline*Retriever adapters (spec §5.5)"
```

---

## Task 10 — Retrieval stages + filter stages

**Files:**
- Create: `python/pydocs_mcp/retrieval/stages.py`
- Create: `tests/retrieval/test_stages.py`

This task covers the first 6 of the 12 stage classes. The other 6 land in Tasks 11–13 via append. Start the file with the imports + the first 6 stages:

- [ ] **Step 10.1:** Write failing tests — cover `ChunkRetrievalStage`, `ModuleMemberRetrievalStage`, `PackageFilterStage`, `ScopeFilterStage`, `TitleFilterStage`, `LimitStage`:

```python
"""Tests for stage classes — Part 1 (retrieval + filters + limit)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
    SearchScope,
)
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.stages import (
    ChunkRetrievalStage,
    LimitStage,
    ModuleMemberRetrievalStage,
    PackageFilterStage,
    ScopeFilterStage,
    TitleFilterStage,
)


@dataclass(frozen=True, slots=True)
class _StaticChunkRetriever:
    name: str = "static_chunk"
    _payload: tuple[Chunk, ...] = ()
    async def retrieve(self, query): return ChunkList(items=self._payload)


@dataclass(frozen=True, slots=True)
class _StaticMemberRetriever:
    name: str = "static_member"
    _payload: tuple[ModuleMember, ...] = ()
    async def retrieve(self, query): return ModuleMemberList(items=self._payload)


@pytest.mark.asyncio
async def test_chunk_retrieval_stage_sets_result():
    stage = ChunkRetrievalStage(retriever=_StaticChunkRetriever(_payload=(Chunk(text="a"),)))
    state = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert isinstance(state.result, ChunkList)
    assert state.result.items[0].text == "a"


@pytest.mark.asyncio
async def test_member_retrieval_stage_sets_result():
    stage = ModuleMemberRetrievalStage(
        retriever=_StaticMemberRetriever(_payload=(ModuleMember(metadata={"n": "f"}),))
    )
    state = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert isinstance(state.result, ModuleMemberList)


@pytest.mark.asyncio
async def test_package_filter_stage_keeps_matching_package():
    payload = ChunkList(items=(
        Chunk(text="a", metadata={ChunkFilterField.PACKAGE.value: "keep"}),
        Chunk(text="b", metadata={ChunkFilterField.PACKAGE.value: "drop"}),
    ))
    state = PipelineState(
        query=SearchQuery(terms="x", pre_filter={ChunkFilterField.PACKAGE.value: "keep"}),
        result=payload,
    )
    out = await PackageFilterStage().run(state)
    assert len(out.result.items) == 1
    assert out.result.items[0].text == "a"


@pytest.mark.asyncio
async def test_package_filter_stage_no_filter_is_noop():
    payload = ChunkList(items=(Chunk(text="a"), Chunk(text="b")))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await PackageFilterStage().run(state)
    assert len(out.result.items) == 2


@pytest.mark.asyncio
async def test_scope_filter_stage_project_only():
    payload = ChunkList(items=(
        Chunk(text="proj", metadata={ChunkFilterField.PACKAGE.value: "__project__"}),
        Chunk(text="dep", metadata={ChunkFilterField.PACKAGE.value: "fastapi"}),
    ))
    state = PipelineState(
        query=SearchQuery(terms="x", pre_filter={ChunkFilterField.SCOPE.value: SearchScope.PROJECT_ONLY.value}),
        result=payload,
    )
    out = await ScopeFilterStage().run(state)
    assert len(out.result.items) == 1
    assert out.result.items[0].text == "proj"


@pytest.mark.asyncio
async def test_title_filter_stage_substring_match():
    payload = ChunkList(items=(
        Chunk(text="a", metadata={ChunkFilterField.TITLE.value: "Routing"}),
        Chunk(text="b", metadata={ChunkFilterField.TITLE.value: "Middleware"}),
    ))
    state = PipelineState(
        query=SearchQuery(terms="x", pre_filter={ChunkFilterField.TITLE.value: "rout"}),
        result=payload,
    )
    out = await TitleFilterStage().run(state)
    assert len(out.result.items) == 1


@pytest.mark.asyncio
async def test_limit_stage_truncates():
    payload = ChunkList(items=tuple(Chunk(text=str(i)) for i in range(10)))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await LimitStage(max_results=3).run(state)
    assert len(out.result.items) == 3


@pytest.mark.asyncio
async def test_limit_stage_default_eight():
    payload = ChunkList(items=tuple(Chunk(text=str(i)) for i in range(20)))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await LimitStage().run(state)
    assert len(out.result.items) == 8
```

- [ ] **Step 10.2:** Run — expect fail.

- [ ] **Step 10.3:** Create `python/pydocs_mcp/retrieval/stages.py`:

```python
"""Pipeline stages — spec §5.6 (12 classes). Part 1: retrieval + filters + limit."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ChunkOrigin,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    PipelineResultItem,
    SearchQuery,
    SearchScope,
)
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.predicates import PredicateRegistry
    from pydocs_mcp.retrieval.protocols import (
        ChunkRetriever,
        ModuleMemberRetriever,
        ResultFormatter,
    )


# ── Retrieval stages ─────────────────────────────────────────────────────


@stage_registry.register("chunk_retrieval")
@dataclass(frozen=True, slots=True)
class ChunkRetrievalStage:
    retriever: "ChunkRetriever"
    name: str = "chunk_retrieval"

    async def run(self, state: PipelineState) -> PipelineState:
        result = await self.retriever.retrieve(state.query)
        return replace(state, result=result)

    def to_dict(self) -> dict:
        return {"type": "chunk_retrieval", "retriever": self.retriever.to_dict()}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ChunkRetrievalStage":
        return cls(retriever=context.retriever_registry.build(data["retriever"], context))


@stage_registry.register("module_member_retrieval")
@dataclass(frozen=True, slots=True)
class ModuleMemberRetrievalStage:
    retriever: "ModuleMemberRetriever"
    name: str = "module_member_retrieval"

    async def run(self, state: PipelineState) -> PipelineState:
        result = await self.retriever.retrieve(state.query)
        return replace(state, result=result)

    def to_dict(self) -> dict:
        return {"type": "module_member_retrieval", "retriever": self.retriever.to_dict()}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ModuleMemberRetrievalStage":
        return cls(retriever=context.retriever_registry.build(data["retriever"], context))


# ── Filter stages ────────────────────────────────────────────────────────


def _filter_result_items(result: PipelineResultItem | None, predicate) -> PipelineResultItem | None:
    if result is None:
        return None
    if isinstance(result, ChunkList):
        return ChunkList(items=tuple(item for item in result.items if predicate(item)))
    # ModuleMemberList
    return ModuleMemberList(items=tuple(item for item in result.items if predicate(item)))


@stage_registry.register("package_filter")
@dataclass(frozen=True, slots=True)
class PackageFilterStage:
    name: str = "package_filter"

    async def run(self, state: PipelineState) -> PipelineState:
        target = (state.query.pre_filter or {}).get(ChunkFilterField.PACKAGE.value)
        if not target:
            return state
        def keep(item):
            return item.metadata.get(ChunkFilterField.PACKAGE.value) == target
        return replace(state, result=_filter_result_items(state.result, keep))

    def to_dict(self) -> dict:
        return {"type": "package_filter"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "PackageFilterStage":
        return cls()


@stage_registry.register("scope_filter")
@dataclass(frozen=True, slots=True)
class ScopeFilterStage:
    name: str = "scope_filter"

    async def run(self, state: PipelineState) -> PipelineState:
        raw = (state.query.pre_filter or {}).get(ChunkFilterField.SCOPE.value)
        if raw is None:
            return state
        scope = SearchScope(raw)
        def keep(item):
            package = item.metadata.get(ChunkFilterField.PACKAGE.value, "")
            if scope is SearchScope.PROJECT_ONLY:
                return package == "__project__"
            if scope is SearchScope.DEPENDENCIES_ONLY:
                return package != "__project__"
            return True
        return replace(state, result=_filter_result_items(state.result, keep))

    def to_dict(self) -> dict:
        return {"type": "scope_filter"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ScopeFilterStage":
        return cls()


@stage_registry.register("title_filter")
@dataclass(frozen=True, slots=True)
class TitleFilterStage:
    name: str = "title_filter"

    async def run(self, state: PipelineState) -> PipelineState:
        target = (state.query.pre_filter or {}).get(ChunkFilterField.TITLE.value)
        if not target:
            return state
        pattern = str(target).lower()
        def keep(item):
            title = (item.metadata.get(ChunkFilterField.TITLE.value, "") or "").lower()
            return pattern in title
        return replace(state, result=_filter_result_items(state.result, keep))

    def to_dict(self) -> dict:
        return {"type": "title_filter"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "TitleFilterStage":
        return cls()


@stage_registry.register("limit")
@dataclass(frozen=True, slots=True)
class LimitStage:
    max_results: int = 8
    name: str = "limit"

    async def run(self, state: PipelineState) -> PipelineState:
        if state.result is None:
            return state
        capped = state.result.items[: self.max_results]
        if isinstance(state.result, ChunkList):
            return replace(state, result=ChunkList(items=tuple(capped)))
        return replace(state, result=ModuleMemberList(items=tuple(capped)))

    def to_dict(self) -> dict:
        d: dict = {"type": "limit"}
        if self.max_results != 8:
            d["max_results"] = self.max_results
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "LimitStage":
        return cls(max_results=data.get("max_results", 8))
```

- [ ] **Step 10.4:** Run — expect pass; commit.

```bash
pytest tests/retrieval/test_stages.py -v
git add python/pydocs_mcp/retrieval/stages.py tests/retrieval/test_stages.py
git commit -m "feat(retrieval): add ChunkRetrievalStage/ModuleMemberRetrievalStage + 4 filter/limit stages (spec §5.6)"
```

---

## Task 11 — Composition stages: `ParallelRetrievalStage` + `ReciprocalRankFusionStage`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/stages.py` (append)
- Modify: `tests/retrieval/test_stages.py` (append)

- [ ] **Step 11.1:** Append tests:

```python
@pytest.mark.asyncio
async def test_parallel_retrieval_stage_runs_branches_concurrently():
    """Each inner stage sees the same input state. Their results are CONCATENATED."""
    from pydocs_mcp.retrieval.stages import ParallelRetrievalStage

    @dataclass(frozen=True, slots=True)
    class _AppendA:
        name: str = "append_a"
        async def run(self, state):
            existing = state.result.items if state.result else ()
            return replace(state, result=ChunkList(items=existing + (Chunk(text="A"),)))

    @dataclass(frozen=True, slots=True)
    class _AppendB:
        name: str = "append_b"
        async def run(self, state):
            existing = state.result.items if state.result else ()
            return replace(state, result=ChunkList(items=existing + (Chunk(text="B"),)))

    from dataclasses import replace
    stage = ParallelRetrievalStage(stages=(_AppendA(), _AppendB()))
    state = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    texts = [c.text for c in state.result.items]
    # Both branch contributions should be present (order depends on gather)
    assert set(texts) == {"A", "B"}


@pytest.mark.asyncio
async def test_reciprocal_rank_fusion_basic():
    from pydocs_mcp.retrieval.stages import ReciprocalRankFusionStage

    # 4 chunks, 2 duplicates — RRF sums 1/(k+rank) across duplicates
    # The duplicate should rank higher than singletons
    items = (
        Chunk(text="a", id=1),
        Chunk(text="b", id=2),
        Chunk(text="a", id=1),  # duplicate of #1 at a lower initial position
    )
    state = PipelineState(query=SearchQuery(terms="x"), result=ChunkList(items=items))
    out = await ReciprocalRankFusionStage(k=60).run(state)
    # "a" (id=1) has 2 appearances; its RRF score is strictly higher than "b"'s single.
    assert out.result.items[0].id == 1
    # Duplicates deduplicated by id
    ids = [c.id for c in out.result.items]
    assert ids.count(1) == 1
```

- [ ] **Step 11.2:** Run — expect fail.

- [ ] **Step 11.3:** Append to `stages.py`:

```python
import asyncio


@stage_registry.register("parallel_retrieval")
@dataclass(frozen=True, slots=True)
class ParallelRetrievalStage:
    stages: tuple["PipelineStage", ...] = ()
    name: str = "parallel_retrieval"

    async def run(self, state: PipelineState) -> PipelineState:
        # Each inner stage sees the SAME input state independently; results concatenate.
        results = await asyncio.gather(*(s.run(state) for s in self.stages))
        # Concatenate the new items from each branch onto the initial state.result
        initial_items = ()
        if state.result is not None:
            initial_items = state.result.items

        accumulated_items: list = list(initial_items)
        first_type = type(state.result) if state.result is not None else None

        for branch_state in results:
            if branch_state.result is None:
                continue
            branch_type = type(branch_state.result)
            # Skip items that are already in accumulated_items (branch inherited the input)
            new_items = branch_state.result.items[len(initial_items):]
            accumulated_items.extend(new_items)
            if first_type is None:
                first_type = branch_type

        if first_type is ChunkList:
            return replace(state, result=ChunkList(items=tuple(accumulated_items)))
        if first_type is ModuleMemberList:
            return replace(state, result=ModuleMemberList(items=tuple(accumulated_items)))
        return state

    def to_dict(self) -> dict:
        return {"type": "parallel_retrieval", "stages": [s.to_dict() for s in self.stages]}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ParallelRetrievalStage":
        return cls(stages=tuple(context.stage_registry.build(s, context) for s in data["stages"]))


@stage_registry.register("reciprocal_rank_fusion")
@dataclass(frozen=True, slots=True)
class ReciprocalRankFusionStage:
    k: int = 60
    name: str = "reciprocal_rank_fusion"

    async def run(self, state: PipelineState) -> PipelineState:
        if state.result is None or not state.result.items:
            return state
        # Score by 1/(k+rank), keyed by item id (fall back to id(item))
        scores: dict = {}
        items_by_key: dict = {}
        for rank, item in enumerate(state.result.items):
            key = item.id if item.id is not None else id(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (self.k + rank)
            items_by_key[key] = item

        # Rebuild ordered result, stable by score desc
        sorted_keys = sorted(scores.keys(), key=lambda k_: scores[k_], reverse=True)
        sorted_items = tuple(items_by_key[k_] for k_ in sorted_keys)
        if isinstance(state.result, ChunkList):
            return replace(state, result=ChunkList(items=sorted_items))
        return replace(state, result=ModuleMemberList(items=sorted_items))

    def to_dict(self) -> dict:
        d: dict = {"type": "reciprocal_rank_fusion"}
        if self.k != 60:
            d["k"] = self.k
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ReciprocalRankFusionStage":
        return cls(k=data.get("k", 60))
```

- [ ] **Step 11.4:** Run — expect pass; commit.

```bash
pytest tests/retrieval/test_stages.py -v
git add python/pydocs_mcp/retrieval/stages.py tests/retrieval/test_stages.py
git commit -m "feat(retrieval): add ParallelRetrievalStage + ReciprocalRankFusionStage (spec §5.6)"
```

---

## Task 12 — Routing stages: `ConditionalStage` + `RouteStage` + `SubPipelineStage` + `RouteCase`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/stages.py` (append)
- Modify: `tests/retrieval/test_stages.py` (append)

- [ ] **Step 12.1:** Append tests covering routing semantics per spec §5.6, §13:

```python
@pytest.mark.asyncio
async def test_conditional_stage_runs_when_predicate_true():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import ConditionalStage

    registry = PredicateRegistry()

    @predicate("always", registry=registry)
    def _true(state): return True

    @dataclass(frozen=True, slots=True)
    class _Sentinel:
        name: str = "sentinel"
        async def run(self, state):
            return replace(state, result=ChunkList(items=(Chunk(text="fired"),)))

    from dataclasses import replace
    stage = ConditionalStage(stage=_Sentinel(), predicate_name="always", registry=registry)
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result.items[0].text == "fired"


@pytest.mark.asyncio
async def test_conditional_stage_skipped_when_predicate_false():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import ConditionalStage

    registry = PredicateRegistry()

    @predicate("never", registry=registry)
    def _false(state): return False

    @dataclass(frozen=True, slots=True)
    class _Sentinel:
        name: str = "sentinel"
        async def run(self, state): raise AssertionError("should not run")

    stage = ConditionalStage(stage=_Sentinel(), predicate_name="never", registry=registry)
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result is None  # state unchanged


@pytest.mark.asyncio
async def test_route_stage_first_match_wins():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import RouteCase, RouteStage

    registry = PredicateRegistry()

    @predicate("always", registry=registry)
    def _t1(s): return True

    @predicate("also_always", registry=registry)
    def _t2(s): return True

    @dataclass(frozen=True, slots=True)
    class _Tag:
        tag: str
        name: str = "tag"
        async def run(self, state):
            return replace(state, result=ChunkList(items=(Chunk(text=self.tag),)))

    from dataclasses import replace
    stage = RouteStage(
        routes=(
            RouteCase(predicate_name="always", stage=_Tag("first")),
            RouteCase(predicate_name="also_always", stage=_Tag("second")),
        ),
        registry=registry,
    )
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result.items[0].text == "first"


@pytest.mark.asyncio
async def test_route_stage_falls_through_to_default():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import RouteCase, RouteStage

    registry = PredicateRegistry()

    @predicate("never", registry=registry)
    def _f(s): return False

    @dataclass(frozen=True, slots=True)
    class _Tag:
        tag: str
        name: str = "tag"
        async def run(self, state):
            return replace(state, result=ChunkList(items=(Chunk(text=self.tag),)))

    from dataclasses import replace
    stage = RouteStage(
        routes=(RouteCase(predicate_name="never", stage=_Tag("route")),),
        default=_Tag("fallback"),
        registry=registry,
    )
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result.items[0].text == "fallback"


@pytest.mark.asyncio
async def test_route_stage_no_match_no_default_is_noop():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import RouteStage

    registry = PredicateRegistry()

    @predicate("never", registry=registry)
    def _f(s): return False

    stage = RouteStage(routes=(), default=None, registry=registry)
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result is None


@pytest.mark.asyncio
async def test_sub_pipeline_stage_runs_nested_stages_on_incoming_state():
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
    from pydocs_mcp.retrieval.stages import SubPipelineStage

    @dataclass(frozen=True, slots=True)
    class _Tag:
        tag: str
        name: str = "tag"
        async def run(self, state):
            existing = state.result.items if state.result else ()
            return replace(state, result=ChunkList(items=existing + (Chunk(text=self.tag),)))

    from dataclasses import replace
    nested = CodeRetrieverPipeline(name="n", stages=(_Tag("inner1"), _Tag("inner2")))
    state = PipelineState(
        query=SearchQuery(terms="x"),
        result=ChunkList(items=(Chunk(text="pre"),)),  # incoming state is preserved
    )
    out = await SubPipelineStage(pipeline=nested).run(state)
    texts = [c.text for c in out.result.items]
    assert texts == ["pre", "inner1", "inner2"]  # state was threaded, not reset
```

- [ ] **Step 12.2:** Run — expect fail.

- [ ] **Step 12.3:** Append to `stages.py`:

```python
from pydocs_mcp.retrieval.predicates import default_predicate_registry


@stage_registry.register("conditional")
@dataclass(frozen=True, slots=True)
class ConditionalStage:
    stage: "PipelineStage"
    predicate_name: str
    registry: "PredicateRegistry" = field(default_factory=lambda: default_predicate_registry)
    name: str = "conditional"

    async def run(self, state: PipelineState) -> PipelineState:
        if self.registry.get(self.predicate_name)(state):
            return await self.stage.run(state)
        return state

    def to_dict(self) -> dict:
        return {
            "type": "conditional",
            "stage": self.stage.to_dict(),
            "predicate_name": self.predicate_name,
        }

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ConditionalStage":
        return cls(
            stage=context.stage_registry.build(data["stage"], context),
            predicate_name=data["predicate_name"],
            registry=context.predicate_registry,
        )


@dataclass(frozen=True, slots=True)
class RouteCase:
    predicate_name: str
    stage: "PipelineStage"


@stage_registry.register("route")
@dataclass(frozen=True, slots=True)
class RouteStage:
    routes: tuple[RouteCase, ...]
    default: "PipelineStage | None" = None
    registry: "PredicateRegistry" = field(default_factory=lambda: default_predicate_registry)
    name: str = "route"

    async def run(self, state: PipelineState) -> PipelineState:
        for case in self.routes:
            if self.registry.get(case.predicate_name)(state):
                return await case.stage.run(state)
        if self.default is not None:
            return await self.default.run(state)
        return state

    def to_dict(self) -> dict:
        d: dict = {
            "type": "route",
            "routes": [
                {"predicate_name": c.predicate_name, "stage": c.stage.to_dict()}
                for c in self.routes
            ],
        }
        if self.default is not None:
            d["default"] = self.default.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "RouteStage":
        routes = tuple(
            RouteCase(
                predicate_name=r["predicate_name"],
                stage=context.stage_registry.build(r["stage"], context),
            )
            for r in data.get("routes", [])
        )
        default_data = data.get("default")
        default = context.stage_registry.build(default_data, context) if default_data else None
        return cls(routes=routes, default=default, registry=context.predicate_registry)


@stage_registry.register("sub_pipeline")
@dataclass(frozen=True, slots=True)
class SubPipelineStage:
    pipeline: CodeRetrieverPipeline
    name: str = "sub_pipeline"

    async def run(self, state: PipelineState) -> PipelineState:
        # Run the inner pipeline's stages ON the incoming state (do NOT reset).
        for stage in self.pipeline.stages:
            state = await stage.run(state)
        return state

    def to_dict(self) -> dict:
        return {"type": "sub_pipeline", "pipeline": self.pipeline.to_dict()}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "SubPipelineStage":
        return cls(pipeline=CodeRetrieverPipeline.from_dict(data["pipeline"], context))
```

- [ ] **Step 12.4:** Run — expect pass; commit.

```bash
pytest tests/retrieval/test_stages.py -v
git add python/pydocs_mcp/retrieval/stages.py tests/retrieval/test_stages.py
git commit -m "feat(retrieval): add ConditionalStage + RouteStage + SubPipelineStage (spec §5.6)"
```

---

## Task 13 — Formatter stage: `TokenBudgetFormatterStage`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/stages.py` (append)
- Modify: `tests/retrieval/test_stages.py` (append)

The formatter stage is the terminal stage — it consumes `state.result`, renders each item with the configured `ResultFormatter`, accumulates within a byte budget, and produces a one-item `ChunkList` whose single `Chunk` has `metadata["origin"] == ChunkOrigin.COMPOSITE_OUTPUT.value`.

- [ ] **Step 13.1:** Append tests:

```python
@pytest.mark.asyncio
async def test_token_budget_formatter_stage_composite_output():
    from pydocs_mcp.retrieval.formatters import ChunkMarkdownFormatter
    from pydocs_mcp.retrieval.stages import TokenBudgetFormatterStage

    payload = ChunkList(items=(
        Chunk(text="abc", metadata={ChunkFilterField.TITLE.value: "A"}),
        Chunk(text="def", metadata={ChunkFilterField.TITLE.value: "B"}),
    ))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await TokenBudgetFormatterStage(
        formatter=ChunkMarkdownFormatter(),
        budget=10_000,
    ).run(state)
    # Result is a ChunkList of length 1 whose metadata origin is COMPOSITE_OUTPUT
    assert isinstance(out.result, ChunkList)
    assert len(out.result.items) == 1
    composite = out.result.items[0]
    assert composite.metadata[ChunkFilterField.ORIGIN.value] == ChunkOrigin.COMPOSITE_OUTPUT.value
    assert "## A" in composite.text
    assert "## B" in composite.text


@pytest.mark.asyncio
async def test_token_budget_formatter_respects_budget():
    from pydocs_mcp.retrieval.formatters import ChunkMarkdownFormatter
    from pydocs_mcp.retrieval.stages import TokenBudgetFormatterStage

    # 100 chunks * ~10-byte render ≈ 1000 bytes. Budget = 50 tokens ≈ 200 bytes (cut early).
    payload = ChunkList(items=tuple(
        Chunk(text="x" * 20, metadata={ChunkFilterField.TITLE.value: f"T{i}"})
        for i in range(100)
    ))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await TokenBudgetFormatterStage(
        formatter=ChunkMarkdownFormatter(),
        budget=50,
    ).run(state)
    composite = out.result.items[0]
    assert len(composite.text) <= 50 * 4 + 200  # budget is bytes, 4 bytes/token, some slack


@pytest.mark.asyncio
async def test_token_budget_formatter_none_result_noop():
    from pydocs_mcp.retrieval.formatters import ChunkMarkdownFormatter
    from pydocs_mcp.retrieval.stages import TokenBudgetFormatterStage

    state = PipelineState(query=SearchQuery(terms="x"), result=None)
    out = await TokenBudgetFormatterStage(
        formatter=ChunkMarkdownFormatter(),
        budget=1000,
    ).run(state)
    assert out.result is None
```

- [ ] **Step 13.2:** Run — expect fail.

- [ ] **Step 13.3:** Append to `stages.py`:

```python
_CHARS_PER_TOKEN = 4


@stage_registry.register("token_budget_formatter")
@dataclass(frozen=True, slots=True)
class TokenBudgetFormatterStage:
    formatter: "ResultFormatter"
    budget: int
    name: str = "token_budget_formatter"

    async def run(self, state: PipelineState) -> PipelineState:
        if state.result is None or not state.result.items:
            return state
        max_chars = self.budget * _CHARS_PER_TOKEN
        parts: list[str] = []
        total = 0
        for item in state.result.items:
            rendered = self.formatter.format(item)
            piece = f"{rendered}\n"
            if total + len(piece) > max_chars:
                remaining = max_chars - total
                if remaining > 100:
                    parts.append(piece[:remaining])
                break
            parts.append(piece)
            total += len(piece)

        composite_text = "\n".join(parts).rstrip()
        composite = Chunk(
            text=composite_text,
            metadata={ChunkFilterField.ORIGIN.value: ChunkOrigin.COMPOSITE_OUTPUT.value},
        )
        return replace(state, result=ChunkList(items=(composite,)))

    def to_dict(self) -> dict:
        return {
            "type": "token_budget_formatter",
            "formatter": self.formatter.to_dict(),
            "budget": self.budget,
        }

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "TokenBudgetFormatterStage":
        return cls(
            formatter=context.formatter_registry.build(data["formatter"], context),
            budget=data["budget"],
        )
```

- [ ] **Step 13.4:** Run — expect pass; commit.

```bash
pytest tests/retrieval/test_stages.py -v
git add python/pydocs_mcp/retrieval/stages.py tests/retrieval/test_stages.py
git commit -m "feat(retrieval): add TokenBudgetFormatterStage (spec §5.6)"
```

---

## Task 14 — Package public API

**Files:**
- Modify: `python/pydocs_mcp/retrieval/__init__.py`

- [ ] **Step 14.1:** Write:

```python
"""Retrieval subpackage — async pipelines, retrievers, stages, registries.

Public API surface. Concrete class re-exports live in submodules; users
typically construct pipelines inline, or load them from YAML via config.py.
"""
from pydocs_mcp.retrieval.pipeline import (
    CodeRetrieverPipeline,
    PerCallConnectionProvider,
    PipelineState,
)
from pydocs_mcp.retrieval.protocols import (
    ChunkRetriever,
    ConnectionProvider,
    ModuleMemberRetriever,
    PipelineStage,
    ResultFormatter,
    Retriever,
)
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    ComponentRegistry,
    formatter_registry,
    retriever_registry,
    stage_registry,
)

__all__ = [
    "BuildContext",
    "ChunkRetriever",
    "CodeRetrieverPipeline",
    "ComponentRegistry",
    "ConnectionProvider",
    "ModuleMemberRetriever",
    "PerCallConnectionProvider",
    "PipelineStage",
    "PipelineState",
    "ResultFormatter",
    "Retriever",
    "formatter_registry",
    "retriever_registry",
    "stage_registry",
]
```

- [ ] **Step 14.2:** Smoke test.

```bash
python -c "from pydocs_mcp.retrieval import CodeRetrieverPipeline, BuildContext; print('ok')"
pytest -q | tail -3
```

- [ ] **Step 14.3:** Commit.

```bash
git add python/pydocs_mcp/retrieval/__init__.py
git commit -m "feat(retrieval): public re-exports in __init__.py"
```

---

## Task 15 — `AppConfig` (pydantic-settings)

**Files:**
- Create: `python/pydocs_mcp/retrieval/config.py`
- Create: `tests/retrieval/test_config.py`

Focus: the `AppConfig` BaseSettings class with YAML source + 5-level load precedence. Pipeline-construction helpers land in Task 16.

- [ ] **Step 15.1:** Write failing tests covering precedence + absent-file:

```python
"""Tests for AppConfig.load() precedence."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig


def test_appconfig_defaults_absent_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    # No explicit path, no env, no cwd file → defaults
    config = AppConfig.load()
    assert config.chunk is None
    assert config.member is None
    assert config.cache_dir == Path.home() / ".pydocs-mcp"


def test_appconfig_explicit_path_wins(tmp_path, monkeypatch):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("log_level: debug\n")
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)

    config = AppConfig.load(explicit_path=yaml_file)
    assert config.log_level == "debug"


def test_appconfig_env_var_used_when_no_explicit(tmp_path, monkeypatch):
    yaml_file = tmp_path / "env.yaml"
    yaml_file.write_text("log_level: warning\n")
    monkeypatch.setenv("PYDOCS_CONFIG_PATH", str(yaml_file))

    config = AppConfig.load()
    assert config.log_level == "warning"


def test_appconfig_cwd_local_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    yaml_file = tmp_path / "pydocs-mcp.yaml"
    yaml_file.write_text("log_level: error\n")
    monkeypatch.chdir(tmp_path)

    config = AppConfig.load()
    assert config.log_level == "error"
```

- [ ] **Step 15.2:** Run — expect fail.

- [ ] **Step 15.3:** Create `python/pydocs_mcp/retrieval/config.py` (partial — AppConfig only; helpers in Task 16):

```python
"""Runtime config — pydantic-settings + YAML source + load precedence."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineRouteEntry(BaseModel):
    """One entry in a handler's route list.

    - If `predicate` is set, run the pipeline at `pipeline_path` when the predicate matches.
    - If `default` is True, use the pipeline at `pipeline_path` as fallback.
    Exactly one of `predicate` or `default` must be set.
    """
    predicate: str | None = None
    default: bool = False
    pipeline_path: Path


class HandlerConfig(BaseModel):
    routes: tuple[PipelineRouteEntry, ...]


class AppConfig(BaseSettings):
    cache_dir: Path = Path.home() / ".pydocs-mcp"
    log_level: str = "info"
    chunk: HandlerConfig | None = None
    member: HandlerConfig | None = None

    model_config = SettingsConfigDict(env_prefix="PYDOCS_", yaml_file=None)

    @classmethod
    def load(cls, explicit_path: Path | None = None) -> "AppConfig":
        """Resolve + load config per precedence: explicit > env > cwd > home > defaults."""
        path = cls._resolve_path(explicit_path)
        if path is None or not path.exists():
            return cls()  # defaults
        with path.open("r", encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        return cls(**data)

    @staticmethod
    def _resolve_path(explicit_path: Path | None) -> Path | None:
        if explicit_path is not None:
            return explicit_path
        env = os.environ.get("PYDOCS_CONFIG_PATH")
        if env:
            return Path(env)
        cwd_candidate = Path.cwd() / "pydocs-mcp.yaml"
        if cwd_candidate.exists():
            return cwd_candidate
        home_candidate = Path.home() / ".config" / "pydocs-mcp" / "config.yaml"
        if home_candidate.exists():
            return home_candidate
        return None
```

- [ ] **Step 15.4:** Run — expect pass; commit.

```bash
pytest tests/retrieval/test_config.py -v
git add python/pydocs_mcp/retrieval/config.py tests/retrieval/test_config.py
git commit -m "feat(config): add AppConfig with 5-level load precedence (spec §5.8)"
```

---

## Task 16 — Pipeline-from-config helpers + preset YAMLs

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (append)
- Create: `python/pydocs_mcp/presets/__init__.py`
- Create: `python/pydocs_mcp/presets/chunk_fts.yaml`
- Create: `python/pydocs_mcp/presets/member_like.yaml`
- Modify: `pyproject.toml` — include presets YAML in package data
- Modify: `tests/retrieval/test_config.py` (append)

- [ ] **Step 16.1:** Create the preset YAMLs verbatim from spec §6.1 / §6.2:

**`python/pydocs_mcp/presets/__init__.py`** — empty file.

**`python/pydocs_mcp/presets/chunk_fts.yaml`**:

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

**`python/pydocs_mcp/presets/member_like.yaml`**:

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

- [ ] **Step 16.2:** Update `pyproject.toml` to include YAML files in package data:

```toml
[tool.maturin]
features = ["pyo3/extension-module"]
module-name = "pydocs_mcp._native"
python-source = "python"
compatibility = "manylinux2014"
include = ["python/pydocs_mcp/presets/*.yaml"]
```

Or if `maturin` ignores this: add under `[project]`:

```toml
[tool.setuptools.package-data]
"pydocs_mcp.presets" = ["*.yaml"]
```

Test that `importlib.resources.files("pydocs_mcp.presets").joinpath("chunk_fts.yaml").exists()` is True.

- [ ] **Step 16.3:** Append tests:

```python
import importlib.resources


def test_preset_chunk_fts_loadable():
    chunk_yaml = importlib.resources.files("pydocs_mcp.presets").joinpath("chunk_fts.yaml")
    assert chunk_yaml.is_file()


def test_preset_member_like_loadable():
    member_yaml = importlib.resources.files("pydocs_mcp.presets").joinpath("member_like.yaml")
    assert member_yaml.is_file()


@pytest.mark.asyncio
async def test_build_chunk_pipeline_from_config_defaults(tmp_path):
    """No user config → built-in chunk_fts.yaml preset is loaded."""
    from pydocs_mcp.retrieval.config import build_chunk_pipeline_from_config, AppConfig
    from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
    from pydocs_mcp.retrieval.serialization import BuildContext

    config = AppConfig()  # defaults, chunk=None
    ctx = BuildContext(connection_provider=PerCallConnectionProvider(tmp_path / "x.db"))
    pipeline = build_chunk_pipeline_from_config(config, ctx)
    assert pipeline.name == "fts_chunk"
    assert len(pipeline.stages) == 6  # chunk_retrieval + 3 filters + limit + token_budget_formatter
```

- [ ] **Step 16.4:** Append to `config.py`:

```python
import importlib.resources

from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.stages import RouteCase, RouteStage, SubPipelineStage


def build_chunk_pipeline_from_config(
    config: AppConfig, context: BuildContext
) -> CodeRetrieverPipeline:
    if config.chunk is None:
        return _load_preset_yaml(
            _preset_path("chunk_fts.yaml"),
            context,
        )
    return _build_handler_pipeline("chunk", config.chunk, context)


def build_member_pipeline_from_config(
    config: AppConfig, context: BuildContext
) -> CodeRetrieverPipeline:
    if config.member is None:
        return _load_preset_yaml(
            _preset_path("member_like.yaml"),
            context,
        )
    return _build_handler_pipeline("member", config.member, context)


def _preset_path(name: str) -> Path:
    return Path(str(importlib.resources.files("pydocs_mcp.presets").joinpath(name)))


def _load_preset_yaml(path: Path, context: BuildContext) -> CodeRetrieverPipeline:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return CodeRetrieverPipeline.from_dict(data, context)


def _build_handler_pipeline(
    handler_name: str, handler_config: HandlerConfig, context: BuildContext
) -> CodeRetrieverPipeline:
    routes: list[RouteCase] = []
    default = None
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
        return CodeRetrieverPipeline(name=f"{handler_name}_from_config", stages=(default,))
    return CodeRetrieverPipeline(
        name=f"{handler_name}_from_config",
        stages=(RouteStage(routes=tuple(routes), default=default),),
    )
```

- [ ] **Step 16.5:** Run — expect pass; commit.

```bash
pytest tests/retrieval/ -v | tail -10
git add python/pydocs_mcp/retrieval/config.py python/pydocs_mcp/presets/ pyproject.toml tests/retrieval/test_config.py
git commit -m "feat(config): build_*_pipeline_from_config + shipped YAML presets (spec §5.8, §6)"
```

---

## Task 17 — Server async migration

**Files:**
- Modify: `python/pydocs_mcp/server.py`
- Modify: `tests/test_server.py`

All 5 MCP handlers become `async`. `list_packages` / `get_package_doc` / `inspect_module` use `async with provider.acquire()`. `search_docs` / `search_api` call pre-built pipelines constructed at startup via `AppConfig.load()`.

**MCP surface invariant:** tool names, parameter names, types, docstrings, return string shapes byte-identical to `main`.

- [ ] **Step 17.1:** Rewrite `python/pydocs_mcp/server.py`:

```python
"""MCP server exposing search tools over indexed docs.

All 5 tools are async. Long-lived-conn tools use ConnectionProvider.acquire().
search_docs / search_api run pre-built CodeRetrieverPipeline instances.
"""
from __future__ import annotations

import asyncio
import atexit
import inspect
import json
import logging
import pkgutil
import re as _re
import sys
from pathlib import Path

from pydocs_mcp.constants import (
    LIVE_DOC_MAX,
    LIVE_SIGNATURE_MAX,
    PACKAGE_DOC_LINE_MAX,
    PACKAGE_DOC_MAX,
    REQUIREMENTS_DISPLAY,
)
from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import ChunkFilterField, ModuleMemberFilterField, SearchQuery, SearchScope
from pydocs_mcp.retrieval.config import (
    AppConfig,
    build_chunk_pipeline_from_config,
    build_member_pipeline_from_config,
)
from pydocs_mcp.retrieval.serialization import BuildContext

log = logging.getLogger("pydocs-mcp")

_SUBMODULE_RE = _re.compile(r'^([A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*)?$')


def _validate_submodule(submodule: str) -> bool:
    return bool(_SUBMODULE_RE.match(submodule))


def _scope_from_internal(internal: bool | None) -> SearchScope:
    if internal is True:
        return SearchScope.PROJECT_ONLY
    if internal is False:
        return SearchScope.DEPENDENCIES_ONLY
    return SearchScope.ALL


def run(db_path: Path, config_path: Path | None = None):
    """Start the MCP server."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        log.error("Missing dependency: pip install mcp")
        sys.exit(1)

    # Build provider + load config + build pipelines once at startup.
    provider = build_connection_provider(db_path)
    config = AppConfig.load(explicit_path=config_path)
    context = BuildContext(connection_provider=provider)
    chunk_pipeline = build_chunk_pipeline_from_config(config, context)
    member_pipeline = build_member_pipeline_from_config(config, context)

    mcp = FastMCP("pydocs-mcp")

    @mcp.tool()
    async def list_packages() -> str:
        """List indexed packages. '__project__' = your source code."""
        async with provider.acquire() as connection:
            rows = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT name, version, summary FROM packages ORDER BY name"
                ).fetchall()
            )
        return "\n".join(
            f"- {r['name']} {r['version']} — {r['summary']}" for r in rows
        )

    @mcp.tool()
    async def get_package_doc(package: str) -> str:
        """Full docs for a package. Use '__project__' for your own code.

        Args:
            package: e.g. 'fastapi', 'vllm', '__project__'
        """
        pkg = "__project__" if package == "__project__" else normalize_package_name(package)
        async with provider.acquire() as connection:
            info = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT * FROM packages WHERE name=?", (pkg,)
                ).fetchone()
            )
            if not info:
                return f"'{package}' not found."

            parts = [f"# {info['name']} {info['version']}\n{info['summary']}"]
            if info["homepage"]:
                parts.append(f"Homepage: {info['homepage']}")
            deps = json.loads(info["dependencies"] or "[]")
            if deps:
                parts.append("Deps: " + ", ".join(deps[:REQUIREMENTS_DISPLAY]))

            chunks = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT title, text FROM chunks WHERE package=? ORDER BY id LIMIT 10",
                    (pkg,),
                ).fetchall()
            )
            for r in chunks:
                parts.append(f"## {r['title']}\n{r['text']}")

            members = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT kind, name, signature, docstring "
                    "FROM module_members WHERE package=? LIMIT 30",
                    (pkg,),
                ).fetchall()
            )
        if members:
            parts.append("## API\n" + "\n".join(
                f"- `{s['kind']} {s['name']}{s['signature']}` — "
                f"{(s['docstring'] or '').split(chr(10))[0][:PACKAGE_DOC_LINE_MAX]}"
                for s in members
            ))
        return "\n\n".join(parts)[:PACKAGE_DOC_MAX]

    @mcp.tool()
    async def search_docs(
        query: str,
        package: str = "",
        internal: bool | None = None,
        topic: str = "",
    ) -> str:
        """Search documentation and source chunks with BM25 ranking.

        Args:
            query: Search terms (space-separated words, OR logic).
            package: Restrict to a specific package name. Leave empty for all packages.
            internal: True → search only the project's own source; False → search only
                dependency packages; omit (None) → search everything.
            topic: If given, restrict to chunks whose heading contains this string.
        """
        scope = _scope_from_internal(internal)
        pre_filter: dict = {ChunkFilterField.SCOPE.value: scope.value}
        if package.strip():
            pre_filter[ChunkFilterField.PACKAGE.value] = package.strip()
        if topic.strip():
            pre_filter[ChunkFilterField.TITLE.value] = topic.strip()
        search_query = SearchQuery(terms=query, pre_filter=pre_filter)
        try:
            state = await chunk_pipeline.run(search_query)
        except Exception:
            log.warning("search_docs failed", exc_info=True)
            return "No matches found."
        if state.result is None or not state.result.items:
            return "No matches found."
        # Final item is the composite formatted chunk
        return state.result.items[0].text

    @mcp.tool()
    async def search_api(
        query: str,
        package: str = "",
        internal: bool | None = None,
    ) -> str:
        """Search symbols (functions, classes) by name or docstring.

        Args:
            query: Name fragment or docstring keyword to search for.
            package: Restrict to a specific package name. Leave empty for all packages.
            internal: True → project symbols only; False → dependency symbols only;
                omit (None) → all symbols.
        """
        scope = _scope_from_internal(internal)
        pre_filter: dict = {ChunkFilterField.SCOPE.value: scope.value}
        if package.strip():
            pre_filter[ModuleMemberFilterField.PACKAGE.value] = package.strip()
        search_query = SearchQuery(terms=query, pre_filter=pre_filter)
        try:
            state = await member_pipeline.run(search_query)
        except Exception:
            log.warning("search_api failed", exc_info=True)
            return "No symbols found."
        if state.result is None or not state.result.items:
            return "No symbols found."
        return state.result.items[0].text

    @mcp.tool()
    async def inspect_module(package: str, submodule: str = "") -> str:
        """Live-import a module to show its current API.

        Args:
            package: e.g. 'fastapi'
            submodule: e.g. 'routing' → fastapi.routing
        """
        import importlib
        pkg_name = normalize_package_name(package)
        async with provider.acquire() as connection:
            row = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT name FROM packages WHERE name=?", (pkg_name,)
                ).fetchone()
            )
        if not row:
            return f"'{package}' is not indexed. Use list_packages() to see available packages."
        if submodule and not _validate_submodule(submodule):
            return f"Invalid submodule '{submodule}'. Use only letters, digits, underscores, and dots."
        target = pkg_name + (f".{submodule}" if submodule else "")
        try:
            mod = importlib.import_module(target)
        except ImportError:
            return f"Cannot import '{target}'."

        items = []
        try:
            for name, obj in inspect.getmembers(mod):
                if name.startswith("_"):
                    continue
                if not (inspect.isfunction(obj) or inspect.isclass(obj)):
                    continue
                try:
                    sig = str(inspect.signature(obj))[:LIVE_SIGNATURE_MAX]
                except (ValueError, TypeError):
                    sig = "(...)"
                doc = (inspect.getdoc(obj) or "").split("\n")[0][:LIVE_DOC_MAX]
                kind = "class" if inspect.isclass(obj) else "def"
                items.append(f"{kind} {name}{sig}\n    {doc}")
                if len(items) >= 50:
                    break
        except Exception:
            pass

        if not items and hasattr(mod, "__path__"):
            try:
                subs = [
                    s for _, s, _ in pkgutil.iter_modules(mod.__path__)
                    if not s.startswith("_")
                ]
                return f"# {target}\nSubmodules: {', '.join(subs)}"
            except Exception:
                pass

        return (f"# {target}\n\n" + "\n\n".join(items)) if items else f"No API in '{target}'."

    log.info("MCP ready (db: %s)", db_path)
    mcp.run(transport="stdio")
```

- [ ] **Step 17.2:** Update `tests/test_server.py` — mechanical async migration. Change `def test_...` that call handlers to `async def` + `pytest.mark.asyncio`, and `handler(...)` to `await handler(...)`. Since the old server used module-level `open_db(db_path)` the tests likely need updating to create a `provider` + `config`. Keep ALL behavior assertions. The tests should still pass.

- [ ] **Step 17.3:** Run full suite.

```bash
pytest -q | tail -3
```

Expected: green.

- [ ] **Step 17.4:** Commit.

```bash
git add python/pydocs_mcp/server.py tests/test_server.py
git commit -m "refactor(server): all 5 MCP handlers async; pipelines + ConnectionProvider (spec §5, §11 AC #27)"
```

---

## Task 18 — CLI `--config` flag + throwaway pipelines

**Files:**
- Modify: `python/pydocs_mcp/__main__.py`

- [ ] **Step 18.1:** Edit `__main__.py`:

1. Add `p.add_argument("--config", type=Path, help="Path to pydocs-mcp.yaml")` at the top-level parser.
2. For `serve`: propagate `args.config` into `run(db_path, config_path=args.config)`.
3. For `query` / `api`: build throwaway pipeline inline using `AppConfig.load(args.config)` + `build_*_pipeline_from_config`. Call `asyncio.run(pipeline.run(SearchQuery(...)))`.

Concretely, replace the `query` / `api` branch:

```python
elif args.cmd in ("query", "api"):
    import asyncio
    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    provider = build_connection_provider(db_path)
    context = BuildContext(connection_provider=provider)
    terms = " ".join(args.terms)
    pre_filter = {ChunkFilterField.PACKAGE.value: args.package} if args.package else None
    search_query = SearchQuery(terms=terms, pre_filter=pre_filter)

    if args.cmd == "query":
        pipeline = build_chunk_pipeline_from_config(config, context)
    else:
        pipeline = build_member_pipeline_from_config(config, context)

    state = asyncio.run(pipeline.run(search_query))
    if state.result is not None and state.result.items:
        print(state.result.items[0].text)
```

- [ ] **Step 18.2:** Smoke test CLI.

```bash
pydocs-mcp query "routing" 2>&1 | head -5
pydocs-mcp api APIRouter 2>&1 | head -5
```

- [ ] **Step 18.3:** Full suite.

```bash
pytest -q | tail -3
```

- [ ] **Step 18.4:** Commit.

```bash
git add python/pydocs_mcp/__main__.py
git commit -m "refactor(cli): --config flag + throwaway pipelines for query/api (spec §5.8, AC #28)"
```

---

## Task 19 — Delete `search.py`

**Files:**
- Delete: `python/pydocs_mcp/search.py`

- [ ] **Step 19.1:** Grep to confirm no remaining importer:

```bash
grep -RIn "from pydocs_mcp.search\|from pydocs_mcp import search" python/ tests/
```

Must be empty.

- [ ] **Step 19.2:** Delete + run tests.

```bash
rm python/pydocs_mcp/search.py
pytest -q | tail -3
```

- [ ] **Step 19.3:** Commit.

```bash
git add -A
git commit -m "refactor: delete search.py — logic redistributed to retrieval/ (spec §3, AC #3)"
```

---

## Task 20 — Integration smoke + zero-residue + parity

**Files:** verification only.

- [ ] **Step 20.1:** Clear cache + full re-index.

```bash
rm -rf ~/.pydocs-mcp
time pydocs-mcp index .
time pydocs-mcp index .
```

Expected: first run full re-index; second run <1s.

- [ ] **Step 20.2:** Golden-output parity check. On `main`, capture output:

```bash
git stash  # if anything uncommitted
git checkout main -- :!docs/superpowers/
pydocs-mcp query "fastapi routing" > /tmp/main_query.txt
git checkout feature/sub-pr-2-async-retriever-pipeline
pydocs-mcp query "fastapi routing" > /tmp/branch_query.txt
diff /tmp/main_query.txt /tmp/branch_query.txt
```

Empty diff = behavior parity (AC #21).

- [ ] **Step 20.3:** Ruff + cargo + pytest final.

```bash
ruff check python/ tests/
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
pytest -q | tail -3
```

All green.

- [ ] **Step 20.4:** Zero-residue grep for banned terms per AC #25 (no "legacy") and the handful still stale:

```bash
grep -RIn "\blegacy\b" python/ src/ tests/
grep -RIn "from pydocs_mcp.search" python/ tests/
grep -RIn "search_chunks\|search_symbols\|concat_context" python/ tests/
```

All must be empty.

- [ ] **Step 20.5:** Commit fixes if any.

---

## Task 21 — `CLAUDE.md` architecture refresh

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 21.1:** Update the architecture diagram + Key Technical Details:

Change:
```
├── search.py      # FTS5 BM25 chunk search + module-member LIKE queries
```
to:
```
├── retrieval/     # Async pipelines, retrievers, stages, registries, YAML config
├── presets/       # Built-in pipeline YAML presets (chunk_fts, member_like)
```

Remove the line about `search.py` if still there. Add under Key Technical Details:
- `pydantic-settings>=2.0` and `pyyaml>=6.0` are runtime deps (added in sub-PR #2 for YAML-driven pipeline config).
- `retrieval/` uses a uniform `PipelineStage` protocol + compound stages (`RouteStage`, `SubPipelineStage`) for composition.

- [ ] **Step 21.2:** Commit.

```bash
git add CLAUDE.md
git commit -m "docs: refresh CLAUDE.md architecture — retrieval/ subpackage + YAML config"
```

---

## Task 22 — Mark PR ready for review

- [ ] **Step 22.1:** Push all commits.

```bash
git push
```

- [ ] **Step 22.2:** Flip draft → ready.

```bash
gh pr ready 14
```

- [ ] **Step 22.3:** Post completion comment summarizing: AC coverage (all 31 ACs), test counts, coverage %, integration parity, and the three `TODO(sub-PR #2)` markers from sub-PR #1 resolved by `search.py` deletion.

---

## Self-review

**1. Spec coverage:**
- AC #1 (9 retrieval files) → Tasks 2-17
- AC #2 (presets directory) → Task 16
- AC #3 (search.py deleted) → Task 19
- AC #4 (deps added + version bump) → Task 1
- AC #5 (no existing test deleted) → invariant across every task; Task 20 grep
- AC #6 (protocols) → Task 2
- AC #7 (CodeRetrieverPipeline) → Task 3
- AC #8 (PipelineState canonical shape) → Task 3
- AC #9 (PerCallConnectionProvider) → Task 3
- AC #10 (ComponentRegistry + 3 shared instances) → Task 5
- AC #11 (BuildContext) → Task 5
- AC #12 (PredicateRegistry + 4 built-ins) → Task 6
- AC #13 (4 retriever classes) → Tasks 8, 9
- AC #14 (12 stage classes) → Tasks 10-13
- AC #15 (RouteStage first-match-wins + default) → Task 12
- AC #16 (SubPipelineStage shared-state) → Task 12
- AC #17 (predicate_name as string) → Task 12 (ConditionalStage + RouteCase)
- AC #18 (formatters registered) → Task 7
- AC #19 (full round-trip for every component) → covered incrementally; Task 16 adds final round-trip test for a full preset pipeline
- AC #20 (AppConfig.load precedence) → Task 15
- AC #21 (behavior parity no-config) → Task 20 golden-diff
- AC #22 (explicit preset references match) → Task 16 test
- AC #23 (conditional routes) → Task 12 tests
- AC #24 (error modes) → Tasks 15/16 tests (FileNotFoundError / KeyError / ValueError paths)
- AC #25 (no "legacy") → Task 20 grep
- AC #26 (no blanket swallow in stages/retrievers) → code review; stages propagate per spec §7
- AC #27 (server all async, signatures identical, pipelines built at startup) → Task 17
- AC #28 (CLI --config + throwaway pipelines) → Task 18
- AC #29 (byte-identical output absent config) → Task 20 golden-diff
- AC #30 (tests/retrieval/ ≥ 90% coverage for new code) → covered by each feat commit; Task 20 final
- AC #31 (requires-python unchanged, deps added) → Task 1

**2. Placeholder scan:** No TBD / TODO / "implement later" / "add validation" anywhere. Every code block is concrete.

**3. Type consistency:**
- `PipelineState(query, result, duration_ms)` — canonical per sub-PR #1 §5, used consistently.
- `ChunkList.items: tuple[Chunk, ...]` / `ModuleMemberList.items: tuple[ModuleMember, ...]` — consistent.
- `predicate_name: str` — always string, always resolved at run time via `registry.get()`.
- `to_dict` omits default-valued fields (`LimitStage.max_results=8` → omitted, `ReciprocalRankFusionStage.k=60` → omitted).

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-19-sub-pr-2-async-retriever-pipeline.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh Opus subagent per task, review between tasks, fast iteration; invokes `superpowers:subagent-driven-development`.

**2. Inline Execution** — execute tasks in this session using `executing-plans`, batch with checkpoints.
