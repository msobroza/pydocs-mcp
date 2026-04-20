# Sub-PR #5 — Extraction Strategies + DocumentNode Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace `indexer.py`'s monolithic extraction with strategy-based chunking + `DocumentNode` tree model. Ship 3 Chunker strategies + 2 MemberExtractor strategies + `get_document_tree` / `get_package_tree` MCP tools + CLI `tree` subcommand.

**Architecture:** New `extraction/` subpackage contains 7 concrete strategies (3 chunkers, 2 member extractors, 1 dep resolver, 1 coordinator). Strategies satisfy the sub-PR #4 Protocols (with amended return types). `DocumentNode` tree persists in new `document_trees` SQLite table via `DocumentTreeStore` Protocol. MCP handlers in `server.py` gain 2 new tool handlers + 1 new CLI subcommand.

**Tech Stack:** Python 3.11+, existing `pydantic-settings`/`pyyaml` stack, stdlib `ast`/`json`/`hashlib`, existing storage/retrieval/application layers.

**Spec source of truth:** [`docs/superpowers/specs/2026-04-20-sub-pr-5-extraction-strategies-and-document-tree-design.md`](../specs/2026-04-20-sub-pr-5-extraction-strategies-and-document-tree-design.md).

**Work location:** Worktree `.claude/worktrees/sub-pr-5-extraction-strategies/` on branch `feature/sub-pr-5-extraction-strategies`, draft PR [#17](https://github.com/msobroza/pydocs-mcp/pull/17).

**Depends on:** sub-PR #4 merged as `626b262` on `main`.

**Repo policy:** No `Co-Authored-By:` trailers. All commits authored solely by `msobroza`. No git config changes.

**Inherited invariants (MUST NOT regress):** AC #8, #9, #21, #26, plus #27–#35 from sub-PR #2/#3.

---

## File structure

### Files created

**`python/pydocs_mcp/extraction/` subpackage (new):**
- `__init__.py` — public re-exports
- `protocols.py` — `Chunker`, `ChunkerSelector`, `FileDiscoverer` Protocols
- `document_node.py` — `DocumentNode`, `NodeKind`, `STRUCTURAL_ONLY_KINDS`
- `registry.py` — chunker name-to-class registry
- `chunkers.py` — `AstPythonChunker`, `HeadingMarkdownChunker`, `NotebookChunker`
- `selector.py` — `ExtensionChunkerSelector`
- `discovery.py` — `ProjectFileDiscoverer`, `DependencyFileDiscoverer`
- `members.py` — `AstMemberExtractor`, `InspectMemberExtractor`
- `dependencies.py` — `StaticDependencyResolver`
- `chunk_extractor.py` — `StrategyChunkExtractor`
- `tree_flatten.py` — `flatten_to_chunks`
- `package_tree.py` — `build_package_tree`
- `config.py` — `ExtractionConfig`, `ChunkingConfig`, `MarkdownConfig`, `NotebookConfig`, `DiscoveryConfig`, `DiscoveryScopeConfig`, `MembersConfig`

**`python/pydocs_mcp/storage/` (additions):**
- `sqlite_document_tree_store.py` — `SqliteDocumentTreeStore`

**`python/pydocs_mcp/application/` (additions):**
- `document_tree_service.py` — `DocumentTreeService`, `NotFoundError`

**Tests (`tests/extraction/` new subpackage):**
- `__init__.py` (empty)
- `conftest.py` — test fixtures
- `test_document_node.py`
- `test_protocols.py`
- `test_ast_python_chunker.py`
- `test_heading_markdown_chunker.py`
- `test_notebook_chunker.py`
- `test_selector.py`
- `test_discovery.py`
- `test_members.py`
- `test_dependencies.py`
- `test_chunk_extractor.py`
- `test_tree_flatten.py`
- `test_package_tree.py`
- `test_config.py`
- `test_registry.py`

**Tests (`tests/storage/` additions):**
- `test_document_tree_store.py`

**Tests (`tests/application/` additions):**
- `test_document_tree_service.py`
- `test_tree_mcp_handlers.py`
- `test_tree_cli_handler.py`

**Tests (`tests/` additions):**
- `test_extraction_integration.py` — end-to-end: project/dep → trees → MCP tools

### Files modified

- `python/pydocs_mcp/models.py` — add 4 new `ChunkOrigin` values; add `ChunkFilterField.SOURCE_PATH`, `ChunkFilterField.CONTENT_HASH`
- `python/pydocs_mcp/application/protocols.py` — amend `ChunkExtractor` return type to include trees
- `python/pydocs_mcp/application/indexing_service.py` — add `tree_store: DocumentTreeStore | None = None`, write trees in same UoW; add `content_hash` incremental re-index
- `python/pydocs_mcp/application/index_project_service.py` — thread trees through; delete legacy `ChunkExtractorAdapter` / `MemberExtractorAdapter` / `DependencyResolverAdapter`
- `python/pydocs_mcp/application/__init__.py` — re-export `DocumentTreeService`, drop deleted adapter names
- `python/pydocs_mcp/storage/protocols.py` — add `DocumentTreeStore` Protocol
- `python/pydocs_mcp/storage/__init__.py` — re-export `SqliteDocumentTreeStore` (if that file has exports)
- `python/pydocs_mcp/storage/wiring.py` — pass `tree_store` into `IndexingService`
- `python/pydocs_mcp/db.py` — bump `SCHEMA_VERSION` 2→3, add `document_trees` table + `chunks.content_hash` + `packages.local_path` columns
- `python/pydocs_mcp/retrieval/config.py` — add `extraction: ExtractionConfig` field to `AppConfig`
- `python/pydocs_mcp/presets/default_config.yaml` — add `extraction:` block
- `python/pydocs_mcp/server.py` — wire `StrategyChunkExtractor` / strategies instead of deleted adapters; add `get_document_tree` + `get_package_tree` MCP tools
- `python/pydocs_mcp/__main__.py` — wire strategies; add `tree` subcommand
- `python/pydocs_mcp/_fast.py` — drop `split_into_chunks` from Rust/fallback imports
- `python/pydocs_mcp/_fallback.py` — delete `split_into_chunks` function
- `src/lib.rs` — delete `split_into_chunks`, `HEADING_RE`, `TextChunk`, registration; keep other 6 functions
- `CLAUDE.md` — update architecture section

### Files deleted

- `python/pydocs_mcp/indexer.py` — all logic migrates into `extraction/`
- Legacy adapters in `python/pydocs_mcp/application/index_project_service.py` (the three `*Adapter` classes — replaced by strategy classes)

---

## Task 0 — Baseline verification

- [ ] **Step 0.1: Activate venv + verify baseline**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-5-extraction-strategies
source .venv/bin/activate
pytest -q | tail -3
```
Expected: `541 passed` (sub-PR #4 baseline).

- [ ] **Step 0.2: Rust toolchain sanity**

```bash
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
```
Expected: exit 0.

- [ ] **Step 0.3: No commit.**

---

# BATCH 1 — Domain model + storage foundation (Tasks 1–10)

All additive or protocol amendments. Existing tests stay green throughout.

---

## Task 1 — `NodeKind` + `DocumentNode` value object

**Files:**
- Create: `python/pydocs_mcp/extraction/__init__.py` (empty for now)
- Create: `python/pydocs_mcp/extraction/document_node.py`
- Create: `tests/extraction/__init__.py` (empty)
- Create: `tests/extraction/test_document_node.py`

Per spec §4.1 + §4.1.1 + §4.3.

- [ ] **Step 1.1: Create the empty extraction subpackage**

```bash
mkdir -p /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-5-extraction-strategies/python/pydocs_mcp/extraction
touch /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-5-extraction-strategies/python/pydocs_mcp/extraction/__init__.py
mkdir -p /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-5-extraction-strategies/tests/extraction
touch /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-5-extraction-strategies/tests/extraction/__init__.py
```

- [ ] **Step 1.2: Write the failing test**

Create `tests/extraction/test_document_node.py`:

```python
"""Tests for DocumentNode, NodeKind, STRUCTURAL_ONLY_KINDS (spec §4.1, §4.3)."""
from __future__ import annotations

import pytest

from pydocs_mcp.extraction.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)


def test_node_kind_values():
    assert NodeKind.PACKAGE.value == "package"
    assert NodeKind.SUBPACKAGE.value == "subpackage"
    assert NodeKind.MODULE.value == "module"
    assert NodeKind.IMPORT_BLOCK.value == "import_block"
    assert NodeKind.CLASS.value == "class"
    assert NodeKind.FUNCTION.value == "function"
    assert NodeKind.METHOD.value == "method"
    assert NodeKind.MARKDOWN_HEADING.value == "markdown_heading"
    assert NodeKind.NOTEBOOK_MARKDOWN_CELL.value == "notebook_markdown_cell"
    assert NodeKind.NOTEBOOK_CODE_CELL.value == "notebook_code_cell"
    assert NodeKind.CODE_EXAMPLE.value == "code_example"


def test_structural_only_kinds_exact_set():
    assert STRUCTURAL_ONLY_KINDS == frozenset({NodeKind.PACKAGE, NodeKind.SUBPACKAGE})


def test_document_node_is_frozen():
    node = DocumentNode(
        node_id="x", title="y", kind=NodeKind.MODULE,
        source_path="a.py", start_line=1, end_line=2,
        text="hello", content_hash="abc",
    )
    with pytest.raises((AttributeError, TypeError)):
        node.title = "other"  # type: ignore[misc]


def test_document_node_defaults():
    node = DocumentNode(
        node_id="x", title="y", kind=NodeKind.MODULE,
        source_path="a.py", start_line=1, end_line=2,
        text="hello", content_hash="abc",
    )
    assert node.summary == ""
    assert node.extra_metadata == {}
    assert node.parent_id is None
    assert node.children == ()


def test_document_node_to_pageindex_json_leaf():
    node = DocumentNode(
        node_id="mod.foo", title="def foo", kind=NodeKind.FUNCTION,
        source_path="mod.py", start_line=1, end_line=5,
        text="def foo(): pass", content_hash="h",
        summary="foo summary",
    )
    assert node.to_pageindex_json() == {
        "title": "def foo",
        "node_id": "mod.foo",
        "kind": "function",
        "source_path": "mod.py",
        "start_index": 1,
        "end_index": 5,
        "summary": "foo summary",
        "nodes": [],
    }


def test_document_node_to_pageindex_json_recurses_children():
    child = DocumentNode(
        node_id="mod.Foo.bar", title="def bar", kind=NodeKind.METHOD,
        source_path="mod.py", start_line=2, end_line=3,
        text="def bar(): pass", content_hash="c2",
    )
    parent = DocumentNode(
        node_id="mod.Foo", title="class Foo", kind=NodeKind.CLASS,
        source_path="mod.py", start_line=1, end_line=3,
        text="class Foo:", content_hash="c1",
        children=(child,),
    )
    result = parent.to_pageindex_json()
    assert result["title"] == "class Foo"
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["title"] == "def bar"
```

- [ ] **Step 1.3: Run test to verify fails**

Run: `pytest tests/extraction/test_document_node.py -v`
Expected: FAIL with `ModuleNotFoundError: pydocs_mcp.extraction.document_node`.

- [ ] **Step 1.4: Implement `document_node.py`**

Create `python/pydocs_mcp/extraction/document_node.py`:

```python
"""DocumentNode tree model + NodeKind (spec §4.1, §4.3).

Every chunked file produces one DocumentNode tree. Trees persist in
``document_trees`` via DocumentTreeStore; chunks are flattened out of
the tree by :func:`pydocs_mcp.extraction.tree_flatten.flatten_to_chunks`.

PACKAGE / SUBPACKAGE kinds (STRUCTURAL_ONLY_KINDS) are scaffolding for
the assembled package arborescence returned by ``get_package_tree``;
they are never persisted in ``document_trees`` and never flattened into
``chunks`` rows.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class NodeKind(StrEnum):
    PACKAGE = "package"
    SUBPACKAGE = "subpackage"
    MODULE = "module"
    IMPORT_BLOCK = "import_block"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    MARKDOWN_HEADING = "markdown_heading"
    NOTEBOOK_MARKDOWN_CELL = "notebook_markdown_cell"
    NOTEBOOK_CODE_CELL = "notebook_code_cell"
    CODE_EXAMPLE = "code_example"


# Pure arborescence scaffolding — never persisted, never chunked (spec §4.1.1).
STRUCTURAL_ONLY_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.PACKAGE,
    NodeKind.SUBPACKAGE,
})


@dataclass(frozen=True, slots=True)
class DocumentNode:
    """A node in a per-file document tree (spec §4.3).

    ``text`` is the node's DIRECT content — prose/code between this node's
    start and its first child's start. For a leaf, that is its full span.
    """

    node_id: str
    title: str
    kind: NodeKind
    source_path: str
    start_line: int
    end_line: int
    text: str
    content_hash: str
    summary: str = ""
    extra_metadata: Mapping[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    children: tuple["DocumentNode", ...] = ()

    def __post_init__(self) -> None:
        # Freeze the metadata mapping — same pattern as Chunk / ModuleMember.
        object.__setattr__(
            self, "extra_metadata", MappingProxyType(dict(self.extra_metadata)),
        )

    def to_pageindex_json(self) -> dict[str, Any]:
        """Render as PageIndex-style JSON (spec §4.3)."""
        return {
            "title": self.title,
            "node_id": self.node_id,
            "kind": self.kind.value,
            "source_path": self.source_path,
            "start_index": self.start_line,
            "end_index": self.end_line,
            "summary": self.summary,
            "nodes": [c.to_pageindex_json() for c in self.children],
        }
```

- [ ] **Step 1.5: Run test to verify passes**

Run: `pytest tests/extraction/test_document_node.py -v`
Expected: 6 passed.

- [ ] **Step 1.6: Commit**

```bash
git add python/pydocs_mcp/extraction/__init__.py python/pydocs_mcp/extraction/document_node.py tests/extraction/__init__.py tests/extraction/test_document_node.py
git commit -m "feat(extraction): add DocumentNode + NodeKind + STRUCTURAL_ONLY_KINDS (spec §4.1, §4.3)"
```

Expected test count delta: +6 tests.

---

## Task 2 — `ChunkOrigin` additions + `ChunkFilterField` additions

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Modify: `tests/test_models.py`

Per spec §4.2 + §4.4.

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_chunk_origin_has_new_spec_5_values():
    from pydocs_mcp.models import ChunkOrigin
    assert ChunkOrigin.PYTHON_DEF.value == "python_def"
    assert ChunkOrigin.MARKDOWN_SECTION.value == "markdown_section"
    assert ChunkOrigin.NOTEBOOK_MARKDOWN_CELL.value == "notebook_markdown_cell"
    assert ChunkOrigin.NOTEBOOK_CODE_CELL.value == "notebook_code_cell"


def test_chunk_filter_field_has_source_path_and_content_hash():
    from pydocs_mcp.models import ChunkFilterField
    assert ChunkFilterField.SOURCE_PATH.value == "source_path"
    assert ChunkFilterField.CONTENT_HASH.value == "content_hash"
```

- [ ] **Step 2.2: Run test to verify fails**

Run: `pytest tests/test_models.py::test_chunk_origin_has_new_spec_5_values tests/test_models.py::test_chunk_filter_field_has_source_path_and_content_hash -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 2.3: Edit `models.py`**

In `python/pydocs_mcp/models.py`, replace the `ChunkOrigin` class body (lines around 21-28):

```python
class ChunkOrigin(StrEnum):
    # Legacy origins retained for migration compatibility (sub-PR #5 spec §4.2).
    # New chunkers (AstPythonChunker / HeadingMarkdownChunker / NotebookChunker)
    # emit the PYTHON_DEF / MARKDOWN_SECTION / NOTEBOOK_* values instead.
    PROJECT_MODULE_DOC       = "project_module_doc"
    PROJECT_CODE_SECTION     = "project_code_section"
    DEPENDENCY_CODE_SECTION  = "dependency_code_section"
    DEPENDENCY_DOC_FILE      = "dependency_doc_file"
    DEPENDENCY_README        = "dependency_readme"
    DEPENDENCY_MODULE_DOC    = "dependency_module_doc"
    COMPOSITE_OUTPUT         = "composite_output"
    # Sub-PR #5 additions (spec §4.2):
    PYTHON_DEF               = "python_def"
    MARKDOWN_SECTION         = "markdown_section"
    NOTEBOOK_MARKDOWN_CELL   = "notebook_markdown_cell"
    NOTEBOOK_CODE_CELL       = "notebook_code_cell"
```

In the `ChunkFilterField` class (around lines 56-64), add the two new members at the end:

```python
class ChunkFilterField(StrEnum):
    """Canonical metadata keys for Chunk queries (keys in the `metadata` mapping,
    not dataclass fields). Used by MCP handlers to build pre_filter dicts."""
    PACKAGE      = "package"
    TITLE        = "title"
    ORIGIN       = "origin"
    MODULE       = "module"
    SCOPE        = "scope"
    SOURCE_PATH  = "source_path"    # sub-PR #5 §4.4
    CONTENT_HASH = "content_hash"   # sub-PR #5 §4.4
```

- [ ] **Step 2.4: Run test to verify passes**

Run: `pytest tests/test_models.py -v`
Expected: all existing tests + 2 new ones pass.

- [ ] **Step 2.5: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models.py
git commit -m "feat(models): add 4 ChunkOrigin values + SOURCE_PATH/CONTENT_HASH ChunkFilterField (spec §4.2, §4.4)"
```

Expected test count delta: +2 tests.

---

## Task 3 — Amend `ChunkExtractor` Protocol return type

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py`
- Create: `tests/application/test_protocols_amendment.py`

Per spec §4 introduction ("Sub-PR #4 Protocol amendment") + AC #19.

- [ ] **Step 3.1: Write the failing test**

Create `tests/application/test_protocols_amendment.py`:

```python
"""Verify ChunkExtractor return type changed to include DocumentNode list (spec AC #19)."""
from __future__ import annotations

import inspect

from pydocs_mcp.application.protocols import ChunkExtractor


def test_chunk_extractor_extract_from_project_signature_mentions_documentnode():
    src = inspect.getsource(ChunkExtractor.extract_from_project)
    assert "DocumentNode" in src
    assert "tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]" in src


def test_chunk_extractor_extract_from_dependency_signature_mentions_documentnode():
    src = inspect.getsource(ChunkExtractor.extract_from_dependency)
    assert "DocumentNode" in src
    assert "tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]" in src
```

- [ ] **Step 3.2: Run test to verify fails**

Run: `pytest tests/application/test_protocols_amendment.py -v`
Expected: FAIL with `AssertionError` (DocumentNode not mentioned).

- [ ] **Step 3.3: Edit `application/protocols.py`**

Replace `python/pydocs_mcp/application/protocols.py` contents:

```python
"""Application-layer Protocols — extraction + dependency resolution.

Sub-PR #5 swaps the thin ``indexer.py`` adapters for strategy-based
implementations in ``pydocs_mcp.extraction``. The ``ChunkExtractor`` return
type now carries document trees alongside chunks so ``IndexingService``
can persist per-module trees atomically (spec §4 amendment, AC #19).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.extraction.document_node import DocumentNode
from pydocs_mcp.models import Chunk, ModuleMember, Package


@runtime_checkable
class DependencyResolver(Protocol):
    async def resolve(self, project_dir: Path) -> tuple[str, ...]: ...


@runtime_checkable
class ChunkExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]: ...


@runtime_checkable
class MemberExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]: ...
```

- [ ] **Step 3.4: Update existing adapter classes to match new return type**

In `python/pydocs_mcp/application/index_project_service.py`, the adapter classes (`ChunkExtractorAdapter`) and the consumer `_index_project_source` / `_index_one_dependency` currently unpack `(chunks, pkg)`. Temporarily update them to return a 3-tuple `(chunks, trees=(), pkg)` so the existing test suite keeps passing. Replace `ChunkExtractorAdapter.extract_from_project`:

```python
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], tuple["DocumentNode", ...], Package]:
        from pydocs_mcp import indexer
        from pydocs_mcp.extraction.document_node import DocumentNode  # noqa: F401

        chunks, pkg = await indexer.extract_project_chunks(project_dir)
        return chunks, (), pkg

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], tuple["DocumentNode", ...], Package]:
        from pydocs_mcp import indexer
        from pydocs_mcp.extraction.document_node import DocumentNode  # noqa: F401

        chunks, pkg = await indexer.extract_dependency_chunks(
            dep_name, use_inspect=self.use_inspect, depth=self.depth,
        )
        return chunks, (), pkg
```

Update `IndexProjectService._index_project_source`:

```python
    async def _index_project_source(
        self, project_dir: Path, stats: IndexingStats,
    ) -> None:
        chunks, trees, pkg = await self.chunk_extractor.extract_from_project(project_dir)
        existing = await self.indexing_service.package_store.get(pkg.name)
        if existing is not None and existing.content_hash == pkg.content_hash:
            log.info("Project: no changes (cached)")
            return
        members = await self.member_extractor.extract_from_project(project_dir)
        await self.indexing_service.reindex_package(pkg, chunks, members, trees=trees)
        stats.project_indexed = True
        log.info("Project: %d chunks, %d symbols", len(chunks), len(members))
```

And `_index_one_dependency`:

```python
            chunks, trees, pkg = await self.chunk_extractor.extract_from_dependency(dep_name)
            ...
            members = await self.member_extractor.extract_from_dependency(dep_name)
            await self.indexing_service.reindex_package(pkg, chunks, members, trees=trees)
```

Update `IndexingService.reindex_package` signature in `python/pydocs_mcp/application/indexing_service.py` to accept the new optional `trees` kwarg (no-op for now; Task 8 fully wires it):

```python
    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        *,
        trees: tuple[DocumentNode, ...] = (),
    ) -> None:
        """Replace every row for ``package.name`` atomically.

        ``trees`` is the per-module DocumentNode list (sub-PR #5 §11.2). When
        a ``DocumentTreeStore`` is configured (Task 8) the trees are persisted
        inside the same UnitOfWork. When not configured, ``trees`` is ignored
        so pre-sub-PR-5 callers stay byte-identical.
        """
        await self._in_uow(self._do_reindex, package, chunks, module_members, trees)
```

Add the matching import + argument to `_do_reindex`:

```python
    async def _do_reindex(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: tuple[DocumentNode, ...] = (),
    ) -> None:
        ...  # existing body
        # trees are a no-op here; Task 8 adds persistence.
```

Add `from pydocs_mcp.extraction.document_node import DocumentNode` at the top.

- [ ] **Step 3.5: Run tests**

Run: `pytest -q | tail -3`
Expected: 541 passed + 2 new = 543 passed.

- [ ] **Step 3.6: Commit**

```bash
git add python/pydocs_mcp/application/protocols.py python/pydocs_mcp/application/indexing_service.py python/pydocs_mcp/application/index_project_service.py tests/application/test_protocols_amendment.py
git commit -m "refactor(application): amend ChunkExtractor return to 3-tuple with trees; reindex_package accepts trees= kwarg (spec §4, AC #19)"
```

Expected test count delta: +2 tests. Total: 543.

---

## Task 4 — `extraction/protocols.py`

**Files:**
- Create: `python/pydocs_mcp/extraction/protocols.py`
- Create: `tests/extraction/test_protocols.py`

Per spec §6.

- [ ] **Step 4.1: Write the failing test**

```python
"""Tests for extraction Protocols (spec §6)."""
from __future__ import annotations

from pydocs_mcp.extraction.protocols import Chunker, ChunkerSelector, FileDiscoverer


def test_chunker_is_runtime_checkable_protocol():
    assert hasattr(Chunker, "build_tree")


def test_chunker_selector_is_runtime_checkable_protocol():
    assert hasattr(ChunkerSelector, "pick")


def test_file_discoverer_is_runtime_checkable_protocol():
    assert hasattr(FileDiscoverer, "list_files")
```

- [ ] **Step 4.2: Run test to verify fails**

Run: `pytest tests/extraction/test_protocols.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 4.3: Create `protocols.py`**

```python
"""Extraction Protocols — private to ``pydocs_mcp.extraction`` (spec §6)."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.extraction.document_node import DocumentNode


@runtime_checkable
class Chunker(Protocol):
    """Parses a file's content into a DocumentNode tree.

    The chunker owns the "direct text" contract (spec §4.1.1): each node's
    ``.text`` holds only the prose/code between this node's start and its
    first child's start. Chunkers never touch storage — flattening trees
    into ``Chunk`` rows is :func:`tree_flatten.flatten_to_chunks`'s job.
    """

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> DocumentNode: ...


@runtime_checkable
class ChunkerSelector(Protocol):
    """Picks the right Chunker for a file path, by extension."""

    def pick(self, path: str) -> Chunker: ...


@runtime_checkable
class FileDiscoverer(Protocol):
    """Yields file paths in-scope for extraction (per-context: project vs dep)."""

    def list_files(self, *args, **kwargs) -> list[str]: ...
```

- [ ] **Step 4.4: Run test to verify passes**

Run: `pytest tests/extraction/test_protocols.py -v`
Expected: 3 passed.

- [ ] **Step 4.5: Commit**

```bash
git add python/pydocs_mcp/extraction/protocols.py tests/extraction/test_protocols.py
git commit -m "feat(extraction): add Chunker/ChunkerSelector/FileDiscoverer Protocols (spec §6)"
```

Expected test count delta: +3. Total: 546.

---

## Task 5 — `extraction/config.py` (Pydantic models + strict allowlist)

**Files:**
- Create: `python/pydocs_mcp/extraction/config.py`
- Create: `tests/extraction/test_config.py`

Per spec §10.

- [ ] **Step 5.1: Write the failing test**

```python
"""Tests for ExtractionConfig Pydantic models + strict allowlist (spec §10, AC #5, #6)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.extraction.config import (
    ChunkingConfig,
    DiscoveryConfig,
    DiscoveryScopeConfig,
    ExtractionConfig,
    MarkdownConfig,
    MembersConfig,
    NotebookConfig,
)


def test_extraction_config_defaults():
    cfg = ExtractionConfig()
    assert cfg.chunking.by_extension == {
        ".py": "ast_python",
        ".md": "heading_markdown",
        ".ipynb": "notebook",
    }
    assert cfg.members.inspect_depth == 1
    assert cfg.members.members_per_module_cap == 120


def test_chunking_config_rejects_unsupported_extension():
    with pytest.raises(ValidationError) as exc:
        ChunkingConfig(by_extension={".txt": "ast_python"})
    assert "unsupported extensions" in str(exc.value).lower()


def test_discovery_scope_rejects_unsupported_extension():
    with pytest.raises(ValidationError) as exc:
        DiscoveryScopeConfig(include_extensions=[".yaml"])
    assert "unsupported extensions" in str(exc.value).lower()


def test_markdown_config_defaults():
    m = MarkdownConfig()
    assert m.min_heading_level == 1
    assert m.max_heading_level == 3


def test_notebook_config_defaults():
    n = NotebookConfig()
    assert n.include_outputs is False


def test_discovery_config_project_and_dependency_scopes_independent():
    cfg = DiscoveryConfig()
    assert cfg.project.max_file_size_bytes == 500_000
    assert cfg.dependency.max_file_size_bytes == 500_000


def test_excluded_dirs_is_module_constant_not_config_field():
    """Directory exclusions are hardcoded (not YAML-configurable) — see config.py.

    Users must not be able to un-exclude .git / .venv / site-packages etc. via
    config. That would leak secrets + balloon the FTS index + break inspect-mode
    imports. Strict allowlist for extensions + strict blocklist for directories.
    """
    from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
    assert isinstance(_EXCLUDED_DIRS, frozenset)
    assert ".git" in _EXCLUDED_DIRS
    assert ".venv" in _EXCLUDED_DIRS
    assert "site-packages" in _EXCLUDED_DIRS
    # DiscoveryScopeConfig must NOT expose exclude_dirs as a field
    assert "exclude_dirs" not in DiscoveryScopeConfig.model_fields


def test_members_config_defaults():
    m = MembersConfig()
    assert m.inspect_depth == 1
    assert m.members_per_module_cap == 120
```

- [ ] **Step 5.2: Run test to verify fails**

Run: `pytest tests/extraction/test_config.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 5.3: Create `extraction/config.py`**

```python
"""ExtractionConfig Pydantic models (spec §10).

Slot into :class:`pydocs_mcp.retrieval.config.AppConfig` via the
``extraction`` field — Task 6 wires the integration.

Strict allowlist of extensions (spec §3 decision #5 + AC #5, #6): Pydantic
validators reject any attempt to index unsupported file types via YAML.

Directory exclusions are a HARDCODED module constant (``_EXCLUDED_DIRS``) —
NOT a Pydantic field and NOT YAML-overridable. Rationale: an un-excluded
``.git`` / ``.venv`` / ``site-packages`` leaks secrets into the FTS index,
balloons storage, and makes inspect-mode imports recurse into vendored deps.
Users must not be able to shoot themselves in the foot via YAML.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".py", ".md", ".ipynb"})

# Hardcoded directory blocklist — walked-past in both project and dependency
# discovery. Non-overridable by design (see module docstring).
_EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    ".venv", "venv", ".env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".nox", ".eggs", "egg-info",
    "node_modules", "build", "dist", "target",
    "htmlcov", ".coverage", ".cache",
    "site-packages",
})


class MarkdownConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_heading_level: int = 1
    max_heading_level: int = 3


class NotebookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_outputs: bool = False


class ChunkingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by_extension: dict[str, str] = {
        ".py": "ast_python",
        ".md": "heading_markdown",
        ".ipynb": "notebook",
    }
    markdown: MarkdownConfig = MarkdownConfig()
    notebook: NotebookConfig = NotebookConfig()

    @field_validator("by_extension")
    @classmethod
    def _check_allowlist(cls, v: dict[str, str]) -> dict[str, str]:
        bad = set(v) - ALLOWED_EXTENSIONS
        if bad:
            raise ValueError(
                f"extraction.chunking.by_extension: unsupported extensions {sorted(bad)}; "
                f"must be subset of {sorted(ALLOWED_EXTENSIONS)}"
            )
        return v


class DiscoveryScopeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_extensions: list[str] = [".py", ".md", ".ipynb"]
    max_file_size_bytes: int = 500_000
    # NOTE: no `exclude_dirs` field — directory blocklist lives in
    # `_EXCLUDED_DIRS` above and is non-overridable by design.

    @field_validator("include_extensions")
    @classmethod
    def _check_allowlist(cls, v: list[str]) -> list[str]:
        bad = set(v) - ALLOWED_EXTENSIONS
        if bad:
            raise ValueError(
                f"extraction.discovery.*.include_extensions: unsupported extensions {sorted(bad)}; "
                f"must be subset of {sorted(ALLOWED_EXTENSIONS)}"
            )
        return v


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: DiscoveryScopeConfig = DiscoveryScopeConfig()
    dependency: DiscoveryScopeConfig = DiscoveryScopeConfig()


class MembersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inspect_depth: int = 1
    members_per_module_cap: int = 120


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunking: ChunkingConfig = ChunkingConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    members: MembersConfig = MembersConfig()
```

- [ ] **Step 5.4: Run test to verify passes**

Run: `pytest tests/extraction/test_config.py -v`
Expected: 7 passed.

- [ ] **Step 5.5: Commit**

```bash
git add python/pydocs_mcp/extraction/config.py tests/extraction/test_config.py
git commit -m "feat(extraction): add ExtractionConfig with strict .py/.md/.ipynb allowlist (spec §10, AC #5, #6)"
```

Expected test count delta: +7. Total: 553.

---

## Task 6 — Slot `ExtractionConfig` into `AppConfig` + update `default_config.yaml`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py`
- Modify: `python/pydocs_mcp/presets/default_config.yaml`
- Modify: `tests/retrieval/test_config.py`

Per spec §10.1 / §10.2.

- [ ] **Step 6.1: Write the failing test**

Append to `tests/retrieval/test_config.py`:

```python
def test_app_config_has_extraction_field():
    from pydocs_mcp.retrieval.config import AppConfig

    cfg = AppConfig.load()
    assert cfg.extraction.chunking.by_extension == {
        ".py": "ast_python",
        ".md": "heading_markdown",
        ".ipynb": "notebook",
    }


def test_extraction_env_override():
    import os

    os.environ["PYDOCS_EXTRACTION__MEMBERS__INSPECT_DEPTH"] = "3"
    try:
        from pydocs_mcp.retrieval.config import AppConfig

        cfg = AppConfig.load()
        assert cfg.extraction.members.inspect_depth == 3
    finally:
        os.environ.pop("PYDOCS_EXTRACTION__MEMBERS__INSPECT_DEPTH", None)
```

- [ ] **Step 6.2: Run tests to verify fails**

Run: `pytest tests/retrieval/test_config.py::test_app_config_has_extraction_field -v`
Expected: FAIL with AttributeError (no extraction field).

- [ ] **Step 6.3: Modify `retrieval/config.py`**

Add import near the top:

```python
from pydocs_mcp.extraction.config import ExtractionConfig
```

Inside the `AppConfig` class (the BaseSettings class definition), add the field:

```python
    extraction: ExtractionConfig = ExtractionConfig()
```

Also enable nested env access. Replace `model_config = SettingsConfigDict(env_prefix="PYDOCS_", extra="ignore")` with:

```python
    model_config = SettingsConfigDict(
        env_prefix="PYDOCS_",
        env_nested_delimiter="__",
        extra="ignore",
    )
```

- [ ] **Step 6.4: Update `default_config.yaml`**

Append to the YAML file:

```yaml
extraction:
  chunking:
    by_extension:
      ".py":    "ast_python"
      ".md":    "heading_markdown"
      ".ipynb": "notebook"
    markdown:
      min_heading_level: 1
      max_heading_level: 3
    notebook:
      include_outputs: false
  discovery:
    # Directory exclusions are hardcoded (_EXCLUDED_DIRS in config.py) — not
    # YAML-configurable. Users cannot un-exclude .git / .venv / site-packages /
    # etc. Extension allowlist IS narrowable via include_extensions, not
    # expandable (Pydantic rejects unknown extensions).
    project:
      include_extensions: [".py", ".md", ".ipynb"]
      max_file_size_bytes: 500000
    dependency:
      include_extensions: [".py", ".md", ".ipynb"]
      max_file_size_bytes: 500000
  members:
    inspect_depth: 1
    members_per_module_cap: 120
```

- [ ] **Step 6.5: Run test to verify passes**

Run: `pytest tests/retrieval/test_config.py -v`
Expected: all pass including 2 new.

- [ ] **Step 6.6: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py python/pydocs_mcp/presets/default_config.yaml tests/retrieval/test_config.py
git commit -m "feat(config): slot ExtractionConfig into AppConfig + default_config.yaml (spec §10.1, §10.2)"
```

Expected test count delta: +2. Total: 555.

---

## Task 7 — `DocumentTreeStore` Protocol + schema bump

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py`
- Modify: `python/pydocs_mcp/db.py`
- Create: `tests/storage/test_schema_v3.py`

Per spec §6.2 / §11.

- [ ] **Step 7.1: Write the failing test**

Create `tests/storage/test_schema_v3.py`:

```python
"""Schema v3 bump + document_trees + chunks.content_hash + packages.local_path (spec §11.1)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def test_schema_version_bumped_to_3():
    assert SCHEMA_VERSION == 3


def test_document_trees_table_created(tmp_path: Path):
    db_path = tmp_path / "t.db"
    conn = open_index_database(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='document_trees'"
        ).fetchone()
        assert row is not None
        cols = [c[1] for c in conn.execute("PRAGMA table_info(document_trees)").fetchall()]
        assert "package" in cols
        assert "module" in cols
        assert "tree_json" in cols
    finally:
        conn.close()


def test_chunks_has_content_hash_column(tmp_path: Path):
    db_path = tmp_path / "t.db"
    conn = open_index_database(db_path)
    try:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(chunks)").fetchall()]
        assert "content_hash" in cols
    finally:
        conn.close()


def test_packages_has_local_path_column(tmp_path: Path):
    db_path = tmp_path / "t.db"
    conn = open_index_database(db_path)
    try:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(packages)").fetchall()]
        assert "local_path" in cols
    finally:
        conn.close()


def test_document_tree_protocol_exists():
    from pydocs_mcp.storage.protocols import DocumentTreeStore

    assert hasattr(DocumentTreeStore, "save")
    assert hasattr(DocumentTreeStore, "load")
    assert hasattr(DocumentTreeStore, "load_all_in_package")
    assert hasattr(DocumentTreeStore, "delete_package")


def test_v2_to_v3_upgrade_drops_and_recreates(tmp_path: Path):
    """Existing v2 DB must be rebuilt on v3 open."""
    db_path = tmp_path / "t.db"
    # Create a v2 DB by hand
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE packages (name TEXT PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    conn = open_index_database(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='document_trees'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()
```

- [ ] **Step 7.2: Run test to verify fails**

Run: `pytest tests/storage/test_schema_v3.py -v`
Expected: FAIL (SCHEMA_VERSION==2, columns missing, Protocol missing).

- [ ] **Step 7.3: Edit `db.py`**

Bump version + add tables + new columns. Replace the `SCHEMA_VERSION` line and `_DDL`:

```python
SCHEMA_VERSION = 3

_DDL = """
    CREATE TABLE packages (
        name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT
    );
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, package TEXT,
        title TEXT, text TEXT, origin TEXT,
        content_hash TEXT NOT NULL DEFAULT ''
    );
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        title, text, package,
        content=chunks, content_rowid=id,
        tokenize='porter unicode61'
    );
    CREATE TABLE module_members (
        id INTEGER PRIMARY KEY, package TEXT, module TEXT,
        name TEXT, kind TEXT, signature TEXT,
        return_annotation TEXT, parameters TEXT, docstring TEXT
    );
    CREATE TABLE document_trees (
        package    TEXT NOT NULL,
        module     TEXT NOT NULL,
        tree_json  TEXT NOT NULL,
        PRIMARY KEY (package, module)
    );
    CREATE INDEX ix_chunks_package         ON chunks(package);
    CREATE INDEX ix_chunks_content_hash    ON chunks(content_hash);
    CREATE INDEX ix_module_members_package ON module_members(package);
    CREATE INDEX ix_module_members_name    ON module_members(name);
    CREATE INDEX idx_trees_package         ON document_trees(package);
"""
```

Update `_KNOWN_TABLES` to include the new table:

```python
_KNOWN_TABLES = (
    "chunks_fts", "chunks", "module_members", "packages",
    "document_trees", "symbols",
)
```

- [ ] **Step 7.4: Add `DocumentTreeStore` Protocol to `storage/protocols.py`**

Add this Protocol at the end of the file:

```python
from pydocs_mcp.extraction.document_node import DocumentNode  # noqa: E402


@runtime_checkable
class DocumentTreeStore(Protocol):
    """Persists per-module DocumentNode trees (sub-PR #5 §11.2)."""

    async def save(self, package: str, module: str, tree: DocumentNode) -> None: ...
    async def load(self, package: str, module: str) -> DocumentNode | None: ...
    async def load_all_in_package(self, package: str) -> dict[str, DocumentNode]: ...
    async def delete_package(self, package: str) -> int: ...
```

- [ ] **Step 7.5: Run test to verify passes**

Run: `pytest tests/storage/test_schema_v3.py -v`
Expected: 6 passed.

- [ ] **Step 7.6: Run full suite — storage v3 shouldn't break anything**

Run: `pytest -q 2>&1 | tail -3`
Expected: all pass (existing tests transparently rebuild at v3).

- [ ] **Step 7.7: Commit**

```bash
git add python/pydocs_mcp/db.py python/pydocs_mcp/storage/protocols.py tests/storage/test_schema_v3.py
git commit -m "feat(storage): schema v3 — document_trees + chunks.content_hash + packages.local_path + DocumentTreeStore Protocol (spec §11)"
```

Expected test count delta: +6. Total: 561.

---

## Task 8 — `SqliteDocumentTreeStore` implementation

**Files:**
- Create: `python/pydocs_mcp/storage/sqlite_document_tree_store.py`
- Create: `tests/storage/test_document_tree_store.py`

Per spec §6.2 / §11.4.

- [ ] **Step 8.1: Write the failing test**

Create `tests/storage/test_document_tree_store.py`:

```python
"""Tests for SqliteDocumentTreeStore (spec §11.2, §11.4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.storage.sqlite_document_tree_store import SqliteDocumentTreeStore


@pytest.fixture()
def provider(tmp_path: Path):
    db_path = tmp_path / "t.db"
    open_index_database(db_path).close()
    return build_connection_provider(db_path)


def _simple_tree(module: str = "mod") -> DocumentNode:
    return DocumentNode(
        node_id=module, title=module, kind=NodeKind.MODULE,
        source_path=f"{module}.py", start_line=1, end_line=2,
        text="x", content_hash="h",
    )


@pytest.mark.asyncio
async def test_save_then_load_round_trip(provider):
    store = SqliteDocumentTreeStore(provider=provider)
    tree = _simple_tree()
    await store.save("pkg", "mod", tree)
    loaded = await store.load("pkg", "mod")
    assert loaded is not None
    assert loaded.node_id == "mod"
    assert loaded.kind == NodeKind.MODULE


@pytest.mark.asyncio
async def test_load_missing_returns_none(provider):
    store = SqliteDocumentTreeStore(provider=provider)
    loaded = await store.load("absent", "absent")
    assert loaded is None


@pytest.mark.asyncio
async def test_load_all_in_package_returns_dict(provider):
    store = SqliteDocumentTreeStore(provider=provider)
    await store.save("pkg", "a", _simple_tree("a"))
    await store.save("pkg", "b", _simple_tree("b"))
    await store.save("other", "c", _simple_tree("c"))
    trees = await store.load_all_in_package("pkg")
    assert set(trees.keys()) == {"a", "b"}


@pytest.mark.asyncio
async def test_delete_package_removes_all_rows(provider):
    store = SqliteDocumentTreeStore(provider=provider)
    await store.save("pkg", "a", _simple_tree("a"))
    await store.save("pkg", "b", _simple_tree("b"))
    n = await store.delete_package("pkg")
    assert n == 2
    assert await store.load_all_in_package("pkg") == {}


@pytest.mark.asyncio
async def test_oversize_tree_gets_depth_truncated(provider, caplog):
    import logging
    # Build a deep chain of ~30 nesting levels with payload text to go over 500KB
    def deep(i: int) -> DocumentNode:
        if i == 0:
            return DocumentNode(
                node_id=f"n{i}", title="x", kind=NodeKind.FUNCTION,
                source_path="a.py", start_line=1, end_line=2,
                text="z" * 40000, content_hash="h",
            )
        return DocumentNode(
            node_id=f"n{i}", title="x", kind=NodeKind.FUNCTION,
            source_path="a.py", start_line=1, end_line=2,
            text="z" * 40000, content_hash="h",
            children=(deep(i - 1),),
        )

    tree = deep(30)
    store = SqliteDocumentTreeStore(provider=provider)
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await store.save("pkg", "big", tree)
    loaded = await store.load("pkg", "big")
    assert loaded is not None
    # Crawl down to confirm depth capped
    depth = 0
    node = loaded
    while node.children:
        node = node.children[0]
        depth += 1
        if depth > 25:
            break
    assert depth <= 20
```

- [ ] **Step 8.2: Run test to verify fails**

Run: `pytest tests/storage/test_document_tree_store.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 8.3: Implement `SqliteDocumentTreeStore`**

Create `python/pydocs_mcp/storage/sqlite_document_tree_store.py`:

```python
"""SqliteDocumentTreeStore — JSON-per-row persistence for DocumentNode trees (spec §11).

500 KB size safeguard: before save, serialized length is checked; oversize trees
have children beyond depth 20 truncated with a log warning (spec §11.4).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from types import MappingProxyType

from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.sqlite import _maybe_acquire

log = logging.getLogger("pydocs-mcp")

_MAX_TREE_JSON_BYTES = 500_000
_MAX_DEPTH = 20


@dataclass(frozen=True, slots=True)
class SqliteDocumentTreeStore:
    """Implements DocumentTreeStore against the document_trees table."""

    provider: ConnectionProvider

    async def save(
        self, package: str, module: str, tree: DocumentNode,
    ) -> None:
        safe_tree = _maybe_truncate(tree)
        payload = json.dumps(_to_dict(safe_tree))
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                """INSERT OR REPLACE INTO document_trees(package, module, tree_json)
                   VALUES (?, ?, ?)""",
                (package, module, payload),
            )

    async def load(
        self, package: str, module: str,
    ) -> DocumentNode | None:
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT tree_json FROM document_trees WHERE package=? AND module=?",
                    (package, module),
                ).fetchone(),
            )
        if row is None:
            return None
        return _from_dict(json.loads(row["tree_json"]))

    async def load_all_in_package(
        self, package: str,
    ) -> dict[str, DocumentNode]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT module, tree_json FROM document_trees WHERE package=?",
                    (package,),
                ).fetchall(),
            )
        return {r["module"]: _from_dict(json.loads(r["tree_json"])) for r in rows}

    async def delete_package(self, package: str) -> int:
        async with _maybe_acquire(self.provider) as conn:
            cur = await asyncio.to_thread(
                conn.execute,
                "DELETE FROM document_trees WHERE package=?",
                (package,),
            )
            return cur.rowcount


def _to_dict(node: DocumentNode) -> dict:
    return {
        "node_id": node.node_id,
        "title": node.title,
        "kind": node.kind.value,
        "source_path": node.source_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "text": node.text,
        "content_hash": node.content_hash,
        "summary": node.summary,
        "extra_metadata": dict(node.extra_metadata),
        "parent_id": node.parent_id,
        "children": [_to_dict(c) for c in node.children],
    }


def _from_dict(d: dict) -> DocumentNode:
    return DocumentNode(
        node_id=d["node_id"],
        title=d["title"],
        kind=NodeKind(d["kind"]),
        source_path=d["source_path"],
        start_line=d["start_line"],
        end_line=d["end_line"],
        text=d["text"],
        content_hash=d["content_hash"],
        summary=d.get("summary", ""),
        extra_metadata=MappingProxyType(dict(d.get("extra_metadata", {}))),
        parent_id=d.get("parent_id"),
        children=tuple(_from_dict(c) for c in d.get("children", [])),
    )


def _maybe_truncate(tree: DocumentNode) -> DocumentNode:
    """If serialized tree exceeds 500 KB, strip children past depth 20."""
    payload = json.dumps(_to_dict(tree))
    if len(payload) <= _MAX_TREE_JSON_BYTES:
        return tree
    log.warning(
        "DocumentTree for %s exceeds %d bytes (%d); truncating children past depth %d.",
        tree.node_id, _MAX_TREE_JSON_BYTES, len(payload), _MAX_DEPTH,
    )
    return _truncate_depth(tree, 0)


def _truncate_depth(node: DocumentNode, depth: int) -> DocumentNode:
    if depth >= _MAX_DEPTH:
        return DocumentNode(
            node_id=node.node_id, title=node.title, kind=node.kind,
            source_path=node.source_path, start_line=node.start_line,
            end_line=node.end_line, text=node.text,
            content_hash=node.content_hash, summary=node.summary,
            extra_metadata=dict(node.extra_metadata),
            parent_id=node.parent_id, children=(),
        )
    return DocumentNode(
        node_id=node.node_id, title=node.title, kind=node.kind,
        source_path=node.source_path, start_line=node.start_line,
        end_line=node.end_line, text=node.text,
        content_hash=node.content_hash, summary=node.summary,
        extra_metadata=dict(node.extra_metadata),
        parent_id=node.parent_id,
        children=tuple(_truncate_depth(c, depth + 1) for c in node.children),
    )
```

- [ ] **Step 8.4: Run test to verify passes**

Run: `pytest tests/storage/test_document_tree_store.py -v`
Expected: 5 passed.

- [ ] **Step 8.5: Commit**

```bash
git add python/pydocs_mcp/storage/sqlite_document_tree_store.py tests/storage/test_document_tree_store.py
git commit -m "feat(storage): add SqliteDocumentTreeStore with 500KB/20-depth truncation (spec §11.2, §11.4)"
```

Expected test count delta: +5. Total: 566.

---

## Task 9 — Wire `tree_store` into `IndexingService`

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py`
- Modify: `python/pydocs_mcp/storage/wiring.py`
- Modify: `tests/application/test_indexing_service.py` (add tree-path cases)

Per spec §11.2.

- [ ] **Step 9.1: Write the failing test**

Append to `tests/application/test_indexing_service.py`:

```python
@pytest.mark.asyncio
async def test_reindex_package_writes_trees_when_tree_store_configured(
    in_memory_package_store, in_memory_chunk_store, in_memory_member_store,
):
    """IndexingService persists trees via DocumentTreeStore when wired (spec §11.2)."""
    from pydocs_mcp.application.indexing_service import IndexingService
    from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind

    class InMemoryTreeStore:
        def __init__(self):
            self.saved: list[tuple[str, str, DocumentNode]] = []
            self.deleted: list[str] = []

        async def save(self, package, module, tree):
            self.saved.append((package, module, tree))

        async def load(self, package, module):
            for p, m, t in self.saved:
                if p == package and m == module:
                    return t
            return None

        async def load_all_in_package(self, package):
            return {m: t for (p, m, t) in self.saved if p == package}

        async def delete_package(self, package):
            before = len(self.saved)
            self.saved = [(p, m, t) for (p, m, t) in self.saved if p != package]
            self.deleted.append(package)
            return before - len(self.saved)

    tree_store = InMemoryTreeStore()
    svc = IndexingService(
        package_store=in_memory_package_store,
        chunk_store=in_memory_chunk_store,
        module_member_store=in_memory_member_store,
        tree_store=tree_store,
    )
    tree = DocumentNode(
        node_id="pkg.m", title="m", kind=NodeKind.MODULE,
        source_path="m.py", start_line=1, end_line=2,
        text="x", content_hash="h",
        extra_metadata={"module": "pkg.m"},
    )
    pkg = Package(
        name="pkg", version="1", summary="", homepage="",
        dependencies=(), content_hash="h", origin=PackageOrigin.DEPENDENCY,
    )
    await svc.reindex_package(pkg, (), (), trees=(tree,))
    assert ("pkg", "pkg.m", tree) in tree_store.saved
    assert "pkg" in tree_store.deleted   # delete-then-upsert ordering


@pytest.mark.asyncio
async def test_reindex_package_without_tree_store_ignores_trees(
    in_memory_package_store, in_memory_chunk_store, in_memory_member_store,
):
    """Byte-parity path: tree_store=None → trees ignored."""
    from pydocs_mcp.application.indexing_service import IndexingService
    from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind

    svc = IndexingService(
        package_store=in_memory_package_store,
        chunk_store=in_memory_chunk_store,
        module_member_store=in_memory_member_store,
        tree_store=None,
    )
    tree = DocumentNode(
        node_id="pkg.m", title="m", kind=NodeKind.MODULE,
        source_path="m.py", start_line=1, end_line=2,
        text="x", content_hash="h",
    )
    pkg = Package(
        name="pkg", version="1", summary="", homepage="",
        dependencies=(), content_hash="h", origin=PackageOrigin.DEPENDENCY,
    )
    # Should not raise even though there is no tree_store.
    await svc.reindex_package(pkg, (), (), trees=(tree,))
```

(Ensure `in_memory_package_store`, `in_memory_chunk_store`, `in_memory_member_store` fixtures exist in `tests/application/conftest.py` — if not, add minimal versions.)

- [ ] **Step 9.2: Run tests to verify fails**

Run: `pytest tests/application/test_indexing_service.py -v`
Expected: FAIL (unknown kwarg `tree_store` on IndexingService).

- [ ] **Step 9.3: Modify `IndexingService`**

In `python/pydocs_mcp/application/indexing_service.py`:

1. Add import near top:

```python
from pydocs_mcp.storage.protocols import DocumentTreeStore
```

2. Add field after `unit_of_work`:

```python
    tree_store: DocumentTreeStore | None = None
```

3. Replace `_do_reindex` with:

```python
    async def _do_reindex(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: tuple[DocumentNode, ...] = (),
    ) -> None:
        await self.chunk_store.delete(
            filter={ChunkFilterField.PACKAGE.value: package.name},
        )
        await self.module_member_store.delete(
            filter={ModuleMemberFilterField.PACKAGE.value: package.name},
        )
        if self.tree_store is not None:
            await self.tree_store.delete_package(package.name)
        await self.package_store.delete(filter={"name": package.name})
        await self.package_store.upsert(package)
        await self.chunk_store.upsert(chunks)
        await self.module_member_store.upsert_many(module_members)
        if self.tree_store is not None:
            for tree in trees:
                module = tree.extra_metadata.get("module", tree.node_id)
                await self.tree_store.save(package.name, module, tree)
```

4. Update `_do_remove` + `_do_clear_all` to also delete from `tree_store` when configured:

```python
    async def _do_remove(self, name: str) -> None:
        await self.chunk_store.delete(filter={ChunkFilterField.PACKAGE.value: name})
        await self.module_member_store.delete(
            filter={ModuleMemberFilterField.PACKAGE.value: name},
        )
        if self.tree_store is not None:
            await self.tree_store.delete_package(name)
        await self.package_store.delete(filter={"name": name})

    async def _do_clear_all(self) -> None:
        match_all: All = All(clauses=())
        await self.chunk_store.delete(filter=match_all)
        await self.module_member_store.delete(filter=match_all)
        if self.tree_store is not None:
            # Use a raw SQL equivalent by iterating known packages; for Protocol
            # purity we just delete each package's trees via a two-step query.
            # Cheaper: add a future _clear_all method to DocumentTreeStore.
            # For now, call delete_package for each package that has rows:
            packages = await self.package_store.list()
            for p in packages:
                await self.tree_store.delete_package(p.name)
        await self.package_store.delete(filter=match_all)
```

- [ ] **Step 9.4: Update `storage/wiring.py` to pass `tree_store`**

```python
from pydocs_mcp.storage.sqlite_document_tree_store import SqliteDocumentTreeStore


def build_sqlite_indexing_service(db_path: Path) -> IndexingService:
    provider = build_connection_provider(db_path)
    return IndexingService(
        package_store=SqlitePackageRepository(provider=provider),
        chunk_store=SqliteChunkRepository(provider=provider),
        module_member_store=SqliteModuleMemberRepository(provider=provider),
        tree_store=SqliteDocumentTreeStore(provider=provider),
        unit_of_work=SqliteUnitOfWork(provider=provider),
    )
```

- [ ] **Step 9.5: Run tests**

Run: `pytest tests/application/test_indexing_service.py -v`
Expected: all pass.

- [ ] **Step 9.6: Full suite check**

Run: `pytest -q 2>&1 | tail -3`
Expected: no regressions.

- [ ] **Step 9.7: Commit**

```bash
git add python/pydocs_mcp/application/indexing_service.py python/pydocs_mcp/storage/wiring.py tests/application/test_indexing_service.py tests/application/conftest.py
git commit -m "feat(application): IndexingService persists trees via DocumentTreeStore in same UoW (spec §11.2)"
```

Expected test count delta: +2. Total: 568.

---

## Task 10 — `extraction/tree_flatten.py` (flatten DocumentNode → Chunk)

**Files:**
- Create: `python/pydocs_mcp/extraction/tree_flatten.py`
- Create: `tests/extraction/test_tree_flatten.py`

Per spec §4.1.1 (direct-text rule) + §4.4 (extra_metadata conventions).

- [ ] **Step 10.1: Write the failing test**

```python
"""Tests for tree_flatten.flatten_to_chunks (spec §4.1.1, §4.4)."""
from __future__ import annotations

from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.extraction.tree_flatten import flatten_to_chunks


def _n(
    node_id: str, kind: NodeKind, text: str = "",
    title: str = "t", children: tuple = (),
) -> DocumentNode:
    return DocumentNode(
        node_id=node_id, title=title, kind=kind,
        source_path="m.py", start_line=1, end_line=2,
        text=text, content_hash="h",
        children=children,
    )


def test_flatten_skips_structural_only_kinds():
    tree = _n("p", NodeKind.PACKAGE, text="must-skip",
              children=(_n("p.m", NodeKind.MODULE, text="body"),))
    chunks = flatten_to_chunks(tree, package="p")
    titles = [c.metadata["title"] for c in chunks]
    assert all("p" != c.metadata["node_id"] for c in chunks)
    assert "body" in {c.text for c in chunks}


def test_flatten_skips_empty_direct_text():
    tree = _n("m", NodeKind.MODULE, text="",
              children=(_n("m.foo", NodeKind.FUNCTION, text="def foo(): ..."),))
    chunks = flatten_to_chunks(tree, package="pkg")
    assert len(chunks) == 1
    assert chunks[0].metadata["node_id"] == "m.foo"


def test_flatten_chunks_parent_with_direct_text():
    tree = _n("m", NodeKind.MODULE, text="module doc",
              children=(_n("m.foo", NodeKind.FUNCTION, text="def foo(): ..."),))
    chunks = flatten_to_chunks(tree, package="pkg")
    assert len(chunks) == 2
    kinds = {c.metadata["kind"] for c in chunks}
    assert kinds == {"module", "function"}


def test_flatten_chunk_metadata_includes_required_keys():
    child = _n("m.foo", NodeKind.FUNCTION, text="def foo(): ...", title="def foo")
    tree = _n("m", NodeKind.MODULE, text="mod doc", children=(child,))
    chunks = flatten_to_chunks(tree, package="pkg")
    foo = [c for c in chunks if c.metadata["node_id"] == "m.foo"][0]
    assert foo.metadata["package"] == "pkg"
    assert foo.metadata["kind"] == "function"
    assert foo.metadata["parent_node_id"] == "m"
    assert foo.metadata["source_path"] == "m.py"
    assert foo.metadata["content_hash"] == "h"
    assert foo.metadata["start_line"] == 1
    assert foo.metadata["end_line"] == 2
    assert foo.metadata["qualified_name"] == "m.foo"


def test_flatten_depth_first_preserves_order():
    child_a = _n("m.a", NodeKind.CLASS, text="class A:")
    child_b = _n("m.b", NodeKind.CLASS, text="class B:")
    tree = _n("m", NodeKind.MODULE, text="mod", children=(child_a, child_b))
    chunks = flatten_to_chunks(tree, package="pkg")
    node_ids = [c.metadata["node_id"] for c in chunks]
    assert node_ids == ["m", "m.a", "m.b"]
```

- [ ] **Step 10.2: Run test to verify fails**

Run: `pytest tests/extraction/test_tree_flatten.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 10.3: Implement `tree_flatten.py`**

```python
"""Flatten DocumentNode trees into list[Chunk] for FTS (spec §4.1.1)."""
from __future__ import annotations

from pydocs_mcp.extraction.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)
from pydocs_mcp.models import Chunk, ChunkFilterField, ChunkOrigin


# NodeKind → ChunkOrigin mapping.
_ORIGIN_FOR_KIND: dict[NodeKind, ChunkOrigin] = {
    NodeKind.MODULE:                 ChunkOrigin.PYTHON_DEF,
    NodeKind.CLASS:                  ChunkOrigin.PYTHON_DEF,
    NodeKind.FUNCTION:               ChunkOrigin.PYTHON_DEF,
    NodeKind.METHOD:                 ChunkOrigin.PYTHON_DEF,
    NodeKind.IMPORT_BLOCK:           ChunkOrigin.PYTHON_DEF,
    NodeKind.CODE_EXAMPLE:           ChunkOrigin.PYTHON_DEF,
    NodeKind.MARKDOWN_HEADING:       ChunkOrigin.MARKDOWN_SECTION,
    NodeKind.NOTEBOOK_MARKDOWN_CELL: ChunkOrigin.NOTEBOOK_MARKDOWN_CELL,
    NodeKind.NOTEBOOK_CODE_CELL:     ChunkOrigin.NOTEBOOK_CODE_CELL,
}


def flatten_to_chunks(
    tree: DocumentNode, *, package: str,
) -> list[Chunk]:
    """DFS-walk ``tree``, emitting one Chunk per non-structural node with non-empty direct text."""
    chunks: list[Chunk] = []
    _dfs(tree, package=package, out=chunks)
    return chunks


def _dfs(node: DocumentNode, *, package: str, out: list[Chunk]) -> None:
    if node.kind not in STRUCTURAL_ONLY_KINDS and node.text.strip():
        out.append(_build_chunk(node, package))
    for child in node.children:
        _dfs(child, package=package, out=out)


def _build_chunk(node: DocumentNode, package: str) -> Chunk:
    origin = _ORIGIN_FOR_KIND.get(node.kind, ChunkOrigin.PYTHON_DEF).value
    metadata: dict[str, object] = {
        ChunkFilterField.PACKAGE.value: package,
        ChunkFilterField.TITLE.value: node.title,
        ChunkFilterField.ORIGIN.value: origin,
        ChunkFilterField.SOURCE_PATH.value: node.source_path,
        ChunkFilterField.CONTENT_HASH.value: node.content_hash,
        "node_id": node.node_id,
        "parent_node_id": node.parent_id,
        "kind": node.kind.value,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "qualified_name": node.node_id,
    }
    # Carry forward extraction-specific extras (docstring / signature /
    # inherits_from / heading_level / cell_index / language — spec §4.4).
    for k, v in node.extra_metadata.items():
        metadata.setdefault(k, v)
    return Chunk(text=node.text, metadata=metadata)
```

- [ ] **Step 10.4: Run test to verify passes**

Run: `pytest tests/extraction/test_tree_flatten.py -v`
Expected: 5 passed.

- [ ] **Step 10.5: Commit**

```bash
git add python/pydocs_mcp/extraction/tree_flatten.py tests/extraction/test_tree_flatten.py
git commit -m "feat(extraction): add flatten_to_chunks — direct-text rule + required metadata keys (spec §4.1.1, §4.4)"
```

Expected test count delta: +5. Total: 573.

---

# BATCH 2 — Concrete chunkers + selector + discovery + members (Tasks 11–20)

---

## Task 11 — `AstPythonChunker` (.py)

**Files:**
- Create: `python/pydocs_mcp/extraction/chunkers.py` (first chunker only — HeadingMarkdown + Notebook added in Task 12, 13)
- Create: `tests/extraction/test_ast_python_chunker.py`

Per spec §7.1 + AC #7, #9b, #9c, #11, #18e, #18f.

- [ ] **Step 11.1: Write the failing test**

Create `tests/extraction/test_ast_python_chunker.py`:

```python
"""Tests for AstPythonChunker (spec §7.1 + AC #7, #9b, #9c, #11, #18e, #18f)."""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.chunkers import AstPythonChunker
from pydocs_mcp.extraction.document_node import NodeKind
from pydocs_mcp.extraction.tree_flatten import flatten_to_chunks


def test_class_with_three_methods_produces_4_chunks():
    source = '''
class Foo:
    """Foo docstring."""

    def a(self): pass
    def b(self): pass
    def c(self): pass
'''
    tree = AstPythonChunker().build_tree(
        "pkg/mod.py", source, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    kinds = [c.metadata["kind"] for c in chunks]
    assert kinds.count("class") == 1
    assert kinds.count("method") == 3
    method_chunks = [c for c in chunks if c.metadata["kind"] == "method"]
    class_chunk = [c for c in chunks if c.metadata["kind"] == "class"][0]
    for m in method_chunks:
        assert m.metadata["parent_node_id"] == class_chunk.metadata["node_id"]


def test_class_with_no_methods_produces_one_chunk():
    source = '''
class Only:
    """Only docstring."""
    X = 1
'''
    tree = AstPythonChunker().build_tree(
        "pkg/mod.py", source, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    class_chunks = [c for c in chunks if c.metadata["kind"] == "class"]
    assert len(class_chunks) == 1


def test_module_docstring_produces_module_chunk():
    source = '"""My module."""\n\ndef foo(): pass\n'
    tree = AstPythonChunker().build_tree(
        "pkg/mod.py", source, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    mod_chunks = [c for c in chunks if c.metadata["kind"] == "module"]
    assert len(mod_chunks) == 1
    assert "My module" in mod_chunks[0].text


def test_parse_failure_falls_back_to_module_only():
    source = 'def broken( ...'
    tree = AstPythonChunker().build_tree(
        "pkg/mod.py", source, "pkg", Path("."),
    )
    assert tree.kind == NodeKind.MODULE
    assert tree.text == source
    assert tree.children == ()


def test_class_inherits_from_is_captured():
    source = '''
class Sub(Base, Mixin):
    pass
'''
    tree = AstPythonChunker().build_tree(
        "pkg/mod.py", source, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    class_chunk = [c for c in chunks if c.metadata["kind"] == "class"][0]
    assert list(class_chunk.metadata["inherits_from"]) == ["Base", "Mixin"]


def test_function_with_code_example_in_docstring():
    source = '''
def foo():
    """Do foo.

    Example:

    ```python
    foo()
    ```
    """
    pass
'''
    tree = AstPythonChunker().build_tree(
        "pkg/mod.py", source, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    ex_chunks = [c for c in chunks if c.metadata["kind"] == "code_example"]
    assert len(ex_chunks) == 1
    assert ex_chunks[0].metadata["language"] == "python"
    assert "foo()" in ex_chunks[0].text


def test_qualified_name_uses_module_then_symbol():
    source = "def top(): pass\n"
    tree = AstPythonChunker().build_tree(
        "pkg/mod.py", source, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    fn = [c for c in chunks if c.metadata["kind"] == "function"][0]
    assert fn.metadata["qualified_name"].endswith(".top")
```

- [ ] **Step 11.2: Run test to verify fails**

Run: `pytest tests/extraction/test_ast_python_chunker.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 11.3: Implement `chunkers.py` (AstPythonChunker only for now)**

```python
"""Concrete Chunker strategies (spec §7).

Each chunker parses one file format into a DocumentNode tree. The "direct
text" contract (spec §4.1.1) is each chunker's responsibility:
node.text = content between this node's start and its first child's start.
"""
from __future__ import annotations

import ast
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind

log = logging.getLogger("pydocs-mcp")

# Matches ```lang\n...\n``` fences for code-example extraction.
_CODE_FENCE_RE = re.compile(
    r"```([a-zA-Z0-9_+\-]*)\s*\n(.*?)\n```",
    re.DOTALL,
)


def _module_from_path(path: str, root: Path) -> str:
    """Convert a relative .py path to a dotted module name."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = path
    return rel.replace(os.sep, ".").removesuffix(".py").replace(".__init__", "")


def _content_hash(text: str, kind: NodeKind, title: str) -> str:
    payload = f"{text}\0{kind.value}\0{title}".encode("utf-8", "ignore")
    return hashlib.sha1(payload).hexdigest()[:16]


def _docstring_summary(doc: str | None) -> str:
    if not doc:
        return ""
    first = doc.strip().split("\n\n", 1)[0]
    return first.split(".", 1)[0].strip()[:200]


def _slice_lines(source_lines: list[str], start_line: int, end_line: int) -> str:
    """1-based inclusive slicing with bounds guards."""
    start = max(start_line - 1, 0)
    end = min(end_line, len(source_lines))
    return "\n".join(source_lines[start:end])


def _direct_python_text(
    source_lines: list[str], node, children_nodes: list,
) -> str:
    """Return text lines from node.lineno up to but excluding first child's line."""
    node_start = getattr(node, "lineno", 1)
    node_end = getattr(node, "end_lineno", node_start)
    if children_nodes:
        first_child = min(children_nodes, key=lambda c: c.lineno)
        end = first_child.lineno - 1
    else:
        end = node_end
    return _slice_lines(source_lines, node_start, end)


def _extract_code_examples(
    doc: str, source_path: str, parent_qname: str, parent_id: str,
) -> list[DocumentNode]:
    """Pull fenced code blocks from a docstring into CODE_EXAMPLE nodes."""
    examples: list[DocumentNode] = []
    for i, m in enumerate(_CODE_FENCE_RE.finditer(doc or ""), start=1):
        lang = (m.group(1) or "").strip()
        code = m.group(2)
        title = f"example {i}"
        node_id = f"{parent_qname}#example_{i}"
        examples.append(DocumentNode(
            node_id=node_id, title=title, kind=NodeKind.CODE_EXAMPLE,
            source_path=source_path,
            start_line=0, end_line=0,
            text=code,
            content_hash=_content_hash(code, NodeKind.CODE_EXAMPLE, title),
            extra_metadata={"language": lang},
            parent_id=parent_id,
        ))
    return examples


@dataclass(frozen=True, slots=True)
class AstPythonChunker:
    """Chunker for .py files (spec §7.1).

    SRP: ``build_tree`` is a 4-line delegation. Each private helper does one thing
    (parse, build fallback, build children, build root). No single function
    exceeds ~15 LOC.
    """

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> DocumentNode:
        module = _module_from_path(path, root)
        tree = _safe_parse(content, path)
        if tree is None:
            return _fallback_module_node(module, path, content, root)
        return _module_node_from_ast(tree, module, path, content, root)


# --- helpers: each ≤ 15 LOC, single-purpose ---

def _safe_parse(content: str, path: str) -> ast.Module | None:
    try:
        return ast.parse(content)
    except SyntaxError as exc:
        log.warning("ast.parse failed on %s: %s", path, exc)
        return None


def _fallback_module_node(
    module: str, path: str, content: str, root: Path,
) -> DocumentNode:
    return DocumentNode(
        node_id=module, title=module, kind=NodeKind.MODULE,
        source_path=_relpath(path, root),
        start_line=1, end_line=max(len(content.splitlines()), 1),
        text=content,
        content_hash=_content_hash(content, NodeKind.MODULE, module),
    )


def _module_node_from_ast(
    tree: ast.Module, module: str, path: str, content: str, root: Path,
) -> DocumentNode:
    lines = content.splitlines()
    rel = _relpath(path, root)
    children = _extract_module_children(tree, module, lines, rel)
    mod_doc = ast.get_docstring(tree) or ""
    return DocumentNode(
        node_id=module, title=module, kind=NodeKind.MODULE,
        source_path=rel, start_line=1, end_line=max(len(lines), 1),
        text=mod_doc,                              # direct-text rule (spec §4.1.1)
        content_hash=_content_hash(mod_doc, NodeKind.MODULE, module),
        summary=_docstring_summary(mod_doc),
        children=tuple(children),
    )


def _extract_module_children(
    tree: ast.Module, module: str, lines: list[str], rel: str,
) -> list[DocumentNode]:
    children: list[DocumentNode] = []
    imports = [s for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom))]
    if imports:
        children.append(_import_block_node(imports, module, lines, rel))
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            children.append(_function_node(stmt, module, lines, rel, parent_id=module))
        elif isinstance(stmt, ast.ClassDef):
            children.append(_class_node(stmt, module, lines, rel, parent_id=module))
    return children


def _import_block_node(
    imports: list, module: str, lines: list[str], rel: str,
) -> DocumentNode:
    start = imports[0].lineno
    end = imports[-1].end_lineno or start
    txt = _slice_lines(lines, start, end)
    qname = f"{module}.__imports__"
    return DocumentNode(
        node_id=qname, title="imports", kind=NodeKind.IMPORT_BLOCK,
        source_path=rel, start_line=start, end_line=end, text=txt,
        content_hash=_content_hash(txt, NodeKind.IMPORT_BLOCK, "imports"),
        parent_id=module,
    )


def _relpath(path: str, root: Path) -> str:
    return str(Path(path).relative_to(root)) if Path(path).is_absolute() else path


def _function_node(
    stmt, module: str, source_lines: list[str],
    source_path_rel: str, parent_id: str,
) -> DocumentNode:
    is_async = isinstance(stmt, ast.AsyncFunctionDef)
    title = f"{'async def' if is_async else 'def'} {stmt.name}()"
    qname = f"{module}.{stmt.name}"
    doc = ast.get_docstring(stmt) or ""
    text = _slice_lines(source_lines, stmt.lineno, stmt.end_lineno or stmt.lineno)
    signature_line = source_lines[stmt.lineno - 1] if stmt.lineno - 1 < len(source_lines) else ""
    examples = _extract_code_examples(doc, source_path_rel, qname, qname)
    kind = NodeKind.FUNCTION
    return DocumentNode(
        node_id=qname, title=title, kind=kind,
        source_path=source_path_rel,
        start_line=stmt.lineno, end_line=stmt.end_lineno or stmt.lineno,
        text=text,
        content_hash=_content_hash(text, kind, title),
        summary=_docstring_summary(doc),
        extra_metadata={
            "docstring": doc, "signature": signature_line.strip(),
        },
        parent_id=parent_id,
        children=tuple(examples),
    )


def _method_node(
    stmt, class_qname: str, source_lines: list[str],
    source_path_rel: str, parent_id: str,
) -> DocumentNode:
    is_async = isinstance(stmt, ast.AsyncFunctionDef)
    title = f"{'async def' if is_async else 'def'} {stmt.name}()"
    qname = f"{class_qname}.{stmt.name}"
    doc = ast.get_docstring(stmt) or ""
    text = _slice_lines(source_lines, stmt.lineno, stmt.end_lineno or stmt.lineno)
    signature_line = source_lines[stmt.lineno - 1] if stmt.lineno - 1 < len(source_lines) else ""
    examples = _extract_code_examples(doc, source_path_rel, qname, qname)
    return DocumentNode(
        node_id=qname, title=title, kind=NodeKind.METHOD,
        source_path=source_path_rel,
        start_line=stmt.lineno, end_line=stmt.end_lineno or stmt.lineno,
        text=text,
        content_hash=_content_hash(text, NodeKind.METHOD, title),
        summary=_docstring_summary(doc),
        extra_metadata={
            "docstring": doc, "signature": signature_line.strip(),
        },
        parent_id=parent_id,
        children=tuple(examples),
    )


def _class_node(
    stmt, module: str, source_lines: list[str],
    source_path_rel: str, parent_id: str,
) -> DocumentNode:
    title = f"class {stmt.name}"
    qname = f"{module}.{stmt.name}"
    doc = ast.get_docstring(stmt) or ""
    method_stmts = [
        s for s in stmt.body
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    # Direct text: class X: down to line before first method (or full span if no methods).
    class_text = _direct_python_text(source_lines, stmt, method_stmts)
    method_nodes = [
        _method_node(m, qname, source_lines, source_path_rel, parent_id=qname)
        for m in method_stmts
    ]
    examples = _extract_code_examples(doc, source_path_rel, qname, qname)
    signature_line = source_lines[stmt.lineno - 1] if stmt.lineno - 1 < len(source_lines) else ""
    inherits = [ast.unparse(b) for b in stmt.bases]
    return DocumentNode(
        node_id=qname, title=title, kind=NodeKind.CLASS,
        source_path=source_path_rel,
        start_line=stmt.lineno, end_line=stmt.end_lineno or stmt.lineno,
        text=class_text,
        content_hash=_content_hash(class_text, NodeKind.CLASS, title),
        summary=_docstring_summary(doc),
        extra_metadata={
            "docstring": doc, "signature": signature_line.strip(),
            "inherits_from": list(inherits),
        },
        parent_id=parent_id,
        children=tuple(list(examples) + method_nodes),
    )
```

- [ ] **Step 11.4: Run test to verify passes**

Run: `pytest tests/extraction/test_ast_python_chunker.py -v`
Expected: 7 passed.

- [ ] **Step 11.5: Commit**

```bash
git add python/pydocs_mcp/extraction/chunkers.py tests/extraction/test_ast_python_chunker.py
git commit -m "feat(extraction): AstPythonChunker — AST-based chunking + inheritance + code examples (spec §7.1)"
```

Expected test count delta: +7. Total: 580.

---

## Task 12 — `HeadingMarkdownChunker` (.md)

**Files:**
- Modify: `python/pydocs_mcp/extraction/chunkers.py`
- Create: `tests/extraction/test_heading_markdown_chunker.py`

Per spec §7.2 + AC #8, #8b, #10, #18g.

- [ ] **Step 12.1: Write the failing test**

```python
"""Tests for HeadingMarkdownChunker (spec §7.2 + AC #8, #8b, #10, #18g)."""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.chunkers import HeadingMarkdownChunker
from pydocs_mcp.extraction.document_node import NodeKind
from pydocs_mcp.extraction.tree_flatten import flatten_to_chunks


def test_headings_with_intro_prose_produces_a_b_c():
    src = "# A\nintro under A\n\n## B\nB body\n\n## C\nC body\n"
    tree = HeadingMarkdownChunker().build_tree(
        "docs/x.md", src, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    titles = [c.metadata["title"] for c in chunks if c.metadata["kind"] == "markdown_heading"]
    assert titles == ["A", "B", "C"]


def test_headings_without_intro_prose_skips_parent():
    src = "# A\n\n## B\nB body\n\n## C\nC body\n"
    tree = HeadingMarkdownChunker().build_tree(
        "docs/x.md", src, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    titles = [c.metadata["title"] for c in chunks if c.metadata["kind"] == "markdown_heading"]
    assert titles == ["B", "C"]


def test_no_headings_produces_single_module_chunk():
    src = "Just prose, no headings.\nStill prose.\n"
    tree = HeadingMarkdownChunker().build_tree(
        "docs/x.md", src, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    # MODULE chunk only
    assert len(chunks) == 1
    assert chunks[0].metadata["kind"] == "module"
    assert "Just prose" in chunks[0].text


def test_code_fence_extracted_and_removed_from_heading_text():
    src = "# A\nSome intro.\n\n```python\nprint(1)\n```\n\nMore prose.\n"
    tree = HeadingMarkdownChunker().build_tree(
        "docs/x.md", src, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    heading = [c for c in chunks if c.metadata["kind"] == "markdown_heading"][0]
    # Code fence should be absent from heading text
    assert "print(1)" not in heading.text
    examples = [c for c in chunks if c.metadata["kind"] == "code_example"]
    assert len(examples) == 1
    assert "print(1)" in examples[0].text
    assert examples[0].metadata["language"] == "python"


def test_heading_level_metadata_recorded():
    src = "# A\nprose A\n\n## B\nprose B\n"
    tree = HeadingMarkdownChunker().build_tree(
        "docs/x.md", src, "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    a = [c for c in chunks if c.metadata["title"] == "A"][0]
    b = [c for c in chunks if c.metadata["title"] == "B"][0]
    assert a.metadata["heading_level"] == 1
    assert b.metadata["heading_level"] == 2
```

- [ ] **Step 12.2: Run test to verify fails**

Run: `pytest tests/extraction/test_heading_markdown_chunker.py -v`
Expected: FAIL (ImportError — HeadingMarkdownChunker not in chunkers.py).

- [ ] **Step 12.3: Append `HeadingMarkdownChunker` to `chunkers.py`**

```python
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_MAX_MARKDOWN_BODY_BYTES = 50_000


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass(frozen=True, slots=True)
class HeadingMarkdownChunker:
    """Chunker for .md files (spec §7.2)."""

    min_heading_level: int = 1
    max_heading_level: int = 6

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> DocumentNode:
        module = _module_from_path_md(path, root)
        source_path_rel = (
            str(Path(path).relative_to(root)) if Path(path).is_absolute() else path
        )

        lines = content.splitlines()

        # First pass: find heading positions that fall within level range.
        headings: list[tuple[int, int, str]] = []  # (lineno, level, title)
        for lineno, line in enumerate(lines, start=1):
            m = _HEADING_RE.match(line)
            if not m:
                continue
            level = len(m.group(1))
            if level < self.min_heading_level or level > self.max_heading_level:
                continue
            headings.append((lineno, level, m.group(2).strip()))

        if not headings:
            # No-heading file: MODULE carries whole body (truncated at 50 KB).
            body = content
            if len(body.encode("utf-8", "ignore")) > _MAX_MARKDOWN_BODY_BYTES:
                log.warning(
                    "Markdown file %s exceeds %d bytes; truncating.",
                    path, _MAX_MARKDOWN_BODY_BYTES,
                )
                body = body.encode("utf-8", "ignore")[:_MAX_MARKDOWN_BODY_BYTES].decode(
                    "utf-8", "ignore",
                )
            return DocumentNode(
                node_id=module, title=module, kind=NodeKind.MODULE,
                source_path=source_path_rel,
                start_line=1, end_line=max(len(lines), 1),
                text=body,
                content_hash=_content_hash(body, NodeKind.MODULE, module),
            )

        # Assemble the heading tree via a running stack.
        root_node_children: list[DocumentNode] = []
        stack: list[tuple[int, list[DocumentNode], dict]] = []
        # Each stack entry: (level, children_list, node_meta)

        def _emit(
            level: int, title: str, start: int, end: int, target_children: list,
            parent_qname: str,
        ) -> DocumentNode:
            heading_text = _slice_lines(lines, start + 1, end)
            examples, body_without_fences = _strip_code_fences(
                heading_text, source_path_rel, parent_qname=f"{parent_qname}#{_slugify(title)}",
            )
            node_id = f"{parent_qname}#{_slugify(title)}"
            return DocumentNode(
                node_id=node_id, title=title, kind=NodeKind.MARKDOWN_HEADING,
                source_path=source_path_rel,
                start_line=start, end_line=end,
                text=body_without_fences,
                content_hash=_content_hash(body_without_fences, NodeKind.MARKDOWN_HEADING, title),
                summary=_first_paragraph(body_without_fences),
                extra_metadata={"heading_level": level},
                parent_id=parent_qname,
                children=tuple(examples),
            )

        # Build tree line-by-line:
        headings_with_ends = []
        for i, (lineno, level, title) in enumerate(headings):
            # End = start of the next heading at same-or-higher level, else EOF.
            end_line = len(lines) + 1
            for j in range(i + 1, len(headings)):
                if headings[j][1] <= level:
                    end_line = headings[j][0]
                    break
                # Otherwise, this heading contains a deeper child — but for
                # THIS heading's "direct text" we stop at its first descendant.
            # First direct child (next heading with level > this level, contiguous).
            direct_child_start = None
            for j in range(i + 1, len(headings)):
                if headings[j][1] <= level:
                    break
                if headings[j][1] == level + 1:
                    direct_child_start = headings[j][0]
                    break
                # Same level deeper-than +1 (e.g., # A then ### B) counts as direct.
                direct_child_start = headings[j][0]
                break
            direct_text_end = direct_child_start if direct_child_start else end_line
            headings_with_ends.append((lineno, level, title, direct_text_end, end_line))

        # Flat traversal: repeatedly pick the next heading, attach it under the
        # last open ancestor on the stack.
        for (lineno, level, title, direct_end, full_end) in headings_with_ends:
            parent_qname = module
            # Pop siblings/children off the stack until top is an ancestor.
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                parent_qname = stack[-1][2]["qname"]
            # Build THIS node with empty children; we'll mutate via the stack idiom.
            heading_text = _slice_lines(lines, lineno + 1, direct_end)
            examples, body_without_fences = _strip_code_fences(
                heading_text, source_path_rel,
                parent_qname=f"{parent_qname}#{_slugify(title)}",
            )
            qname = f"{parent_qname}#{_slugify(title)}"
            # Children appear later; we defer them by stashing a mutable list.
            mutable_children: list[DocumentNode] = list(examples)

            node_proxy = {
                "qname": qname,
                "title": title,
                "level": level,
                "lineno": lineno,
                "end": full_end - 1,
                "text": body_without_fences,
                "children": mutable_children,
            }
            if not stack:
                root_node_children.append(_heading_from_proxy(
                    node_proxy, source_path_rel, parent_qname,
                ))
            else:
                parent_children_list = stack[-1][1]
                parent_children_list.append(_heading_from_proxy(
                    node_proxy, source_path_rel, parent_qname,
                ))
            stack.append((level, mutable_children, node_proxy))

        # MODULE direct text: nothing — the headings own the content.
        return DocumentNode(
            node_id=module, title=module, kind=NodeKind.MODULE,
            source_path=source_path_rel,
            start_line=1, end_line=max(len(lines), 1),
            text="",
            content_hash=_content_hash("", NodeKind.MODULE, module),
            children=tuple(root_node_children),
        )


def _module_from_path_md(path: str, root: Path) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = path
    return rel.replace(os.sep, "/")


def _strip_code_fences(
    text: str, source_path: str, parent_qname: str,
) -> tuple[list[DocumentNode], str]:
    """Extract code fences as CODE_EXAMPLE children; return (examples, body_without_fences)."""
    examples: list[DocumentNode] = []
    counter = 0
    def repl(m: re.Match) -> str:
        nonlocal counter
        counter += 1
        lang = (m.group(1) or "").strip()
        code = m.group(2)
        title = f"example {counter}"
        node_id = f"{parent_qname}#example_{counter}"
        examples.append(DocumentNode(
            node_id=node_id, title=title, kind=NodeKind.CODE_EXAMPLE,
            source_path=source_path,
            start_line=0, end_line=0, text=code,
            content_hash=_content_hash(code, NodeKind.CODE_EXAMPLE, title),
            extra_metadata={"language": lang},
            parent_id=parent_qname,
        ))
        return ""
    cleaned = _CODE_FENCE_RE.sub(repl, text)
    return examples, cleaned


def _first_paragraph(text: str) -> str:
    for para in text.split("\n\n"):
        stripped = para.strip()
        if stripped:
            return stripped[:200]
    return ""


def _heading_from_proxy(proxy: dict, source_path_rel: str, parent_qname: str) -> DocumentNode:
    """Build a DocumentNode from a proxy dict (children are mutable, filled later)."""
    return DocumentNode(
        node_id=proxy["qname"], title=proxy["title"],
        kind=NodeKind.MARKDOWN_HEADING,
        source_path=source_path_rel,
        start_line=proxy["lineno"], end_line=proxy["end"],
        text=proxy["text"],
        content_hash=_content_hash(proxy["text"], NodeKind.MARKDOWN_HEADING, proxy["title"]),
        summary=_first_paragraph(proxy["text"]),
        extra_metadata={"heading_level": proxy["level"]},
        parent_id=parent_qname,
        children=tuple(proxy["children"]),
    )
```

Note: The deferred-mutation pattern above is subtle — implementer is free to simplify using a two-pass algorithm if preferred. The test contract (AC #8, #8b, #10, #18g) is the spec, not the exact code shape.

- [ ] **Step 12.4: Run test to verify passes**

Run: `pytest tests/extraction/test_heading_markdown_chunker.py -v`
Expected: 5 passed. If any fail, simplify the build-tree logic (a clean recursive builder is acceptable — goal is behavior matching ACs, not code shape).

- [ ] **Step 12.5: Commit**

```bash
git add python/pydocs_mcp/extraction/chunkers.py tests/extraction/test_heading_markdown_chunker.py
git commit -m "feat(extraction): HeadingMarkdownChunker — heading-tree + code-fence extraction (spec §7.2)"
```

Expected test count delta: +5. Total: 585.

---

## Task 13 — `NotebookChunker` (.ipynb)

**Files:**
- Modify: `python/pydocs_mcp/extraction/chunkers.py`
- Create: `tests/extraction/test_notebook_chunker.py`

Per spec §7.3 + AC #9.

- [ ] **Step 13.1: Write the failing test**

```python
"""Tests for NotebookChunker (spec §7.3 + AC #9)."""
from __future__ import annotations

import json
from pathlib import Path

from pydocs_mcp.extraction.chunkers import NotebookChunker
from pydocs_mcp.extraction.document_node import NodeKind
from pydocs_mcp.extraction.tree_flatten import flatten_to_chunks


def _ipynb(cells: list) -> str:
    return json.dumps({"cells": cells, "metadata": {}, "nbformat": 4})


def test_five_cell_notebook_produces_5_chunks():
    cells = [
        {"cell_type": "markdown", "source": ["# Title\n"]},
        {"cell_type": "code",     "source": ["print(1)\n"]},
        {"cell_type": "markdown", "source": ["## Section\n"]},
        {"cell_type": "code",     "source": ["print(2)\n"]},
        {"cell_type": "markdown", "source": ["bye\n"]},
    ]
    tree = NotebookChunker().build_tree(
        "nb/demo.ipynb", _ipynb(cells), "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    assert len(chunks) == 5
    assert sum(1 for c in chunks if c.metadata["kind"] == "notebook_markdown_cell") == 3
    assert sum(1 for c in chunks if c.metadata["kind"] == "notebook_code_cell") == 2


def test_malformed_notebook_returns_empty_tree():
    tree = NotebookChunker().build_tree(
        "nb/broken.ipynb", "{not json", "pkg", Path("."),
    )
    assert tree.kind == NodeKind.MODULE
    assert tree.children == ()


def test_missing_cells_key_returns_empty_tree():
    tree = NotebookChunker().build_tree(
        "nb/empty.ipynb", json.dumps({"nbformat": 4}), "pkg", Path("."),
    )
    assert tree.kind == NodeKind.MODULE
    assert tree.children == ()


def test_cell_index_metadata():
    cells = [
        {"cell_type": "markdown", "source": "# hi\n"},
        {"cell_type": "code",     "source": "print(1)"},
    ]
    tree = NotebookChunker().build_tree(
        "nb/demo.ipynb", _ipynb(cells), "pkg", Path("."),
    )
    chunks = flatten_to_chunks(tree, package="pkg")
    assert chunks[0].metadata["cell_index"] == 0
    assert chunks[1].metadata["cell_index"] == 1
```

- [ ] **Step 13.2: Run test to verify fails**

Run: `pytest tests/extraction/test_notebook_chunker.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 13.3: Append `NotebookChunker` to `chunkers.py`**

```python
import json


@dataclass(frozen=True, slots=True)
class NotebookChunker:
    """Chunker for .ipynb files (spec §7.3)."""

    include_outputs: bool = False

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> DocumentNode:
        module = _module_from_path_md(path, root)
        source_path_rel = (
            str(Path(path).relative_to(root)) if Path(path).is_absolute() else path
        )
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            log.warning("Notebook %s is not valid JSON: %s", path, exc)
            return DocumentNode(
                node_id=module, title=module, kind=NodeKind.MODULE,
                source_path=source_path_rel,
                start_line=1, end_line=1,
                text="", content_hash=_content_hash("", NodeKind.MODULE, module),
            )

        cells = data.get("cells")
        if not isinstance(cells, list):
            log.warning("Notebook %s missing 'cells' array", path)
            return DocumentNode(
                node_id=module, title=module, kind=NodeKind.MODULE,
                source_path=source_path_rel,
                start_line=1, end_line=1,
                text="", content_hash=_content_hash("", NodeKind.MODULE, module),
            )

        cell_nodes: list[DocumentNode] = []
        for i, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "code")
            src_field = cell.get("source", [])
            if isinstance(src_field, list):
                src_text = "".join(src_field)
            else:
                src_text = str(src_field)
            if cell_type == "markdown":
                kind = NodeKind.NOTEBOOK_MARKDOWN_CELL
                first_line = src_text.splitlines()[0][:80] if src_text else f"cell {i}"
                title = first_line or f"cell {i}"
            else:
                kind = NodeKind.NOTEBOOK_CODE_CELL
                title = f"cell {i}"
            qname = f"{module}#cell_{i}"
            cell_nodes.append(DocumentNode(
                node_id=qname, title=title, kind=kind,
                source_path=source_path_rel,
                start_line=0, end_line=0,
                text=src_text,
                content_hash=_content_hash(src_text, kind, title),
                extra_metadata={
                    "cell_index": i, "cell_type": cell_type,
                },
                parent_id=module,
            ))

        return DocumentNode(
            node_id=module, title=module, kind=NodeKind.MODULE,
            source_path=source_path_rel,
            start_line=1, end_line=max(len(cells), 1),
            text="",
            content_hash=_content_hash("", NodeKind.MODULE, module),
            children=tuple(cell_nodes),
        )
```

- [ ] **Step 13.4: Run test to verify passes**

Run: `pytest tests/extraction/test_notebook_chunker.py -v`
Expected: 4 passed.

- [ ] **Step 13.5: Commit**

```bash
git add python/pydocs_mcp/extraction/chunkers.py tests/extraction/test_notebook_chunker.py
git commit -m "feat(extraction): NotebookChunker — cell-level chunking (spec §7.3)"
```

Expected test count delta: +4. Total: 589.

---

## Task 14 — `registry.py` + `ExtensionChunkerSelector`

**Files:**
- Create: `python/pydocs_mcp/extraction/registry.py`
- Create: `python/pydocs_mcp/extraction/selector.py`
- Create: `tests/extraction/test_registry.py`
- Create: `tests/extraction/test_selector.py`

Per spec §3 decision #3 + §10.3.

- [ ] **Step 14.1: Write failing tests**

`tests/extraction/test_registry.py`:

```python
"""Tests for chunker registry (spec §3 decision #3)."""
from __future__ import annotations

from pydocs_mcp.extraction.registry import chunker_registry


def test_registry_includes_three_builtin_chunkers():
    from pydocs_mcp.extraction.chunkers import (
        AstPythonChunker,
        HeadingMarkdownChunker,
        NotebookChunker,
    )

    assert chunker_registry["ast_python"] is AstPythonChunker
    assert chunker_registry["heading_markdown"] is HeadingMarkdownChunker
    assert chunker_registry["notebook"] is NotebookChunker
```

`tests/extraction/test_selector.py`:

```python
"""Tests for ExtensionChunkerSelector (spec §10.3)."""
from __future__ import annotations

import pytest

from pydocs_mcp.extraction.chunkers import (
    AstPythonChunker,
    HeadingMarkdownChunker,
    NotebookChunker,
)
from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.selector import ExtensionChunkerSelector, ExtractionError


def test_selector_dispatches_by_extension():
    sel = ExtensionChunkerSelector(ChunkingConfig())
    assert isinstance(sel.pick("a.py"), AstPythonChunker)
    assert isinstance(sel.pick("a.md"), HeadingMarkdownChunker)
    assert isinstance(sel.pick("a.ipynb"), NotebookChunker)


def test_selector_case_insensitive_extension():
    sel = ExtensionChunkerSelector(ChunkingConfig())
    assert isinstance(sel.pick("A.PY"), AstPythonChunker)


def test_selector_raises_on_unknown_extension():
    sel = ExtensionChunkerSelector(ChunkingConfig())
    with pytest.raises(ExtractionError):
        sel.pick("script.sh")
```

- [ ] **Step 14.2: Run tests to verify fail**

Run: `pytest tests/extraction/test_registry.py tests/extraction/test_selector.py -v`
Expected: FAIL.

- [ ] **Step 14.3: Implement `registry.py`**

```python
"""Chunker name-to-class registry (spec §3 decision #3)."""
from __future__ import annotations

from pydocs_mcp.extraction.chunkers import (
    AstPythonChunker,
    HeadingMarkdownChunker,
    NotebookChunker,
)
from pydocs_mcp.extraction.protocols import Chunker

chunker_registry: dict[str, type[Chunker]] = {
    "ast_python":        AstPythonChunker,
    "heading_markdown":  HeadingMarkdownChunker,
    "notebook":          NotebookChunker,
}
```

- [ ] **Step 14.4: Implement `selector.py`**

```python
"""ExtensionChunkerSelector — picks Chunker by path extension (spec §10.3)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from collections.abc import Mapping
from pathlib import Path

from pydocs_mcp.extraction.chunkers import (
    AstPythonChunker, HeadingMarkdownChunker, NotebookChunker,
)
from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.protocols import Chunker


class ExtractionError(Exception):
    """Raised when no chunker is registered for a file's extension."""


@dataclass(frozen=True, slots=True)
class ExtensionChunkerSelector:
    """Pure dict lookup keyed by extension. Open/Closed: adding a chunker does
    NOT modify this class — only ``build_selector`` (the factory below).
    """

    chunkers: Mapping[str, Chunker]   # pre-built instances, keyed by ".py" / ".md" / ".ipynb"

    def pick(self, path: str) -> Chunker:
        ext = Path(path).suffix.lower()
        try:
            return self.chunkers[ext]
        except KeyError as exc:
            raise ExtractionError(
                f"no chunker for extension {ext!r} (path={path!r})"
            ) from exc


def build_selector(cfg: ChunkingConfig) -> ExtensionChunkerSelector:
    """Wire chunkers once at startup. All chunkers take config uniformly via
    constructor → no runtime dispatch, no LSP violations.
    """
    return ExtensionChunkerSelector(chunkers={
        ".py":    AstPythonChunker(),
        ".md":    HeadingMarkdownChunker(
            min_heading_level=cfg.markdown.min_heading_level,
            max_heading_level=cfg.markdown.max_heading_level,
        ),
        ".ipynb": NotebookChunker(
            include_outputs=cfg.notebook.include_outputs,
        ),
    })
```

- [ ] **Step 14.5: Run tests to verify pass**

Run: `pytest tests/extraction/test_registry.py tests/extraction/test_selector.py -v`
Expected: 4 passed.

- [ ] **Step 14.6: Commit**

```bash
git add python/pydocs_mcp/extraction/registry.py python/pydocs_mcp/extraction/selector.py tests/extraction/test_registry.py tests/extraction/test_selector.py
git commit -m "feat(extraction): ExtensionChunkerSelector + chunker_registry (spec §3 dec #3, §10.3)"
```

Expected test count delta: +4. Total: 593.

---

## Task 15 — `ProjectFileDiscoverer` + `DependencyFileDiscoverer`

**Files:**
- Create: `python/pydocs_mcp/extraction/discovery.py`
- Create: `tests/extraction/test_discovery.py`

Per spec §5 + §10.1.

- [ ] **Step 15.1: Write the failing test**

```python
"""Tests for FileDiscoverer strategies (spec §5, §10.1)."""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)


def test_project_discovery_walks_included_extensions(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.md").write_text("y")
    (tmp_path / "c.ipynb").write_text("{}")
    (tmp_path / "skip.txt").write_text("nope")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hidden.py").write_text("nope")
    disc = ProjectFileDiscoverer(DiscoveryScopeConfig())
    files = disc.list_files(tmp_path)
    rel = {Path(f).relative_to(tmp_path).as_posix() for f in files}
    assert rel == {"a.py", "b.md", "c.ipynb"}


def test_project_discovery_respects_max_file_size(tmp_path: Path):
    small = tmp_path / "small.py"
    small.write_text("tiny")
    big = tmp_path / "big.py"
    big.write_bytes(b"x" * 600_000)
    disc = ProjectFileDiscoverer(DiscoveryScopeConfig())
    files = disc.list_files(tmp_path)
    assert str(small) in files
    assert str(big) not in files


def test_dependency_discovery_filters_to_distribution_files(tmp_path: Path):
    class _StubDist:
        files = [_Fake(tmp_path / "dep" / "a.py"), _Fake(tmp_path / "dep" / "b.pyc")]
    disc = DependencyFileDiscoverer(DiscoveryScopeConfig())
    # The above uses a stand-in helper; implementer may restructure this test.
```

- [ ] **Step 15.2: Run test to verify fails**

Run: `pytest tests/extraction/test_discovery.py -v`
Expected: FAIL.

- [ ] **Step 15.3: Implement `discovery.py`**

```python
"""ProjectFileDiscoverer + DependencyFileDiscoverer (spec §5, §10.1)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, DiscoveryScopeConfig


@dataclass(frozen=True, slots=True)
class ProjectFileDiscoverer:
    """Walk a project directory, returning files matching the scope config.

    Directory exclusions come from the hardcoded ``_EXCLUDED_DIRS`` constant
    in ``extraction.config`` — NOT from ``self.scope``. Users can narrow
    file extensions via YAML but cannot un-exclude directories.
    """

    scope: DiscoveryScopeConfig

    def list_files(self, project_dir: Path) -> list[str]:
        result: list[str] = []
        for dirpath, dirnames, filenames in os.walk(project_dir):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
            for name in filenames:
                _, ext = os.path.splitext(name)
                if ext.lower() not in self.scope.include_extensions:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    if os.path.getsize(full) > self.scope.max_file_size_bytes:
                        continue
                except OSError:
                    continue
                result.append(full)
        result.sort()
        return result


@dataclass(frozen=True, slots=True)
class DependencyFileDiscoverer:
    """Filter an installed distribution's files to the in-scope set."""

    scope: DiscoveryScopeConfig

    def list_files(self, dist) -> list[str]:
        files = getattr(dist, "files", None) or []
        result: list[str] = []
        for f in files:
            name = str(f)
            _, ext = os.path.splitext(name)
            if ext.lower() not in self.scope.include_extensions:
                continue
            try:
                loc = f.locate()
            except Exception:
                continue
            if not loc.exists():
                continue
            try:
                if loc.stat().st_size > self.scope.max_file_size_bytes:
                    continue
            except OSError:
                continue
            result.append(str(loc))
        return result
```

- [ ] **Step 15.4: Run test to verify passes**

Run: `pytest tests/extraction/test_discovery.py -v`
Expected: pass (the test_dependency_discovery_filters_to_distribution_files test stub may need to be fleshed out by implementer; ACs do not require a strict form here — the behavior is: return only files whose extension is in the scope's allowlist).

- [ ] **Step 15.5: Commit**

```bash
git add python/pydocs_mcp/extraction/discovery.py tests/extraction/test_discovery.py
git commit -m "feat(extraction): ProjectFileDiscoverer + DependencyFileDiscoverer (spec §5, §10.1)"
```

Expected test count delta: +3. Total: 596.

---

## Task 16 — `AstMemberExtractor` + `InspectMemberExtractor`

**Files:**
- Create: `python/pydocs_mcp/extraction/members.py`
- Create: `tests/extraction/test_members.py`

Per spec §8 + AC #12, #13.

- [ ] **Step 16.1: Write the failing test**

```python
"""Tests for member extractors (spec §8 + AC #12, #13)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.members import AstMemberExtractor, InspectMemberExtractor


@pytest.mark.asyncio
async def test_ast_member_extractor_from_project(tmp_path: Path):
    (tmp_path / "mod.py").write_text("def foo():\n    '''doc.'''\n    pass\n")
    ext = AstMemberExtractor()
    members = await ext.extract_from_project(tmp_path)
    names = [m.metadata["name"] for m in members]
    assert "foo" in names


@pytest.mark.asyncio
async def test_inspect_member_extractor_falls_back_on_import_failure(tmp_path: Path):
    ast_ext = AstMemberExtractor()
    ext = InspectMemberExtractor(static_fallback=ast_ext, depth=0)
    # Name that will fail importlib.import_module
    members = await ext.extract_from_dependency("non_existent_XYZ_package")
    assert members == ()
```

- [ ] **Step 16.2: Run test to verify fails**

Run: `pytest tests/extraction/test_members.py -v`
Expected: FAIL.

- [ ] **Step 16.3: Implement `members.py`**

```python
"""AstMemberExtractor + InspectMemberExtractor (spec §8).

Move-over of ``indexer.py``'s member-extraction logic — static AST parsing
and inspect-based live-import extraction — packaged as strategies behind
the sub-PR #4 :class:`MemberExtractor` Protocol.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp._fast import parse_py_file, read_files_parallel, walk_py_files
from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField

log = logging.getLogger("pydocs-mcp")


def _find_installed_distribution(dep_name: str):
    from pydocs_mcp.deps import normalize_package_name

    target = normalize_package_name(dep_name)
    for dist in importlib.metadata.distributions():
        raw = dist.metadata["Name"]
        if raw and raw.lower().replace("-", "_") == target:
            return dist
    return None


@dataclass(frozen=True, slots=True)
class AstMemberExtractor:
    """Member extraction via Rust/regex AST-ish parser (spec §8.1)."""

    members_per_module_cap: int = 120

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]:
        return await asyncio.to_thread(self._static_project, project_dir)

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]:
        return await asyncio.to_thread(self._static_dep, dep_name)

    def _static_project(self, project_dir: Path) -> tuple[ModuleMember, ...]:
        paths = walk_py_files(str(project_dir))
        return self._parse_paths("__project__", paths, str(project_dir))

    def _static_dep(self, dep_name: str) -> tuple[ModuleMember, ...]:
        dist = _find_installed_distribution(dep_name)
        if dist is None:
            return ()
        from pydocs_mcp.indexer import (
            find_site_packages_root,
            list_dependency_source_files,
        )
        name = dist.metadata["Name"].lower().replace("-", "_")
        py_files = list_dependency_source_files(dist)
        if not py_files:
            return ()
        root = find_site_packages_root(py_files[0])
        return self._parse_paths(name, py_files, root)

    def _parse_paths(
        self, package: str, paths: list[str], root: str,
    ) -> tuple[ModuleMember, ...]:
        members: list[ModuleMember] = []
        for fp, src in read_files_parallel(paths):
            if not src:
                continue
            try:
                rel = os.path.relpath(fp, root)
            except ValueError:
                continue
            module = rel.replace(os.sep, ".").removesuffix(".py").replace(".__init__", "")
            for symbol in parse_py_file(src):
                members.append(ModuleMember(metadata={
                    ModuleMemberFilterField.PACKAGE.value: package,
                    ModuleMemberFilterField.MODULE.value: module,
                    ModuleMemberFilterField.NAME.value: symbol.name,
                    ModuleMemberFilterField.KIND.value: symbol.kind,
                    "signature": symbol.signature,
                    "return_annotation": "",
                    "parameters": (),
                    "docstring": symbol.docstring,
                }))
                if len(members) >= self.members_per_module_cap * 1000:
                    break
        return tuple(members)


@dataclass(frozen=True, slots=True)
class InspectMemberExtractor:
    """Live-import member extraction with AST fallback (spec §8.2)."""

    static_fallback: AstMemberExtractor
    depth: int = 1

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]:
        # Spec: never import project-under-test — always delegate to AST.
        return await self.static_fallback.extract_from_project(project_dir)

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]:
        return await asyncio.to_thread(self._inspect_dep, dep_name)

    def _inspect_dep(self, dep_name: str) -> tuple[ModuleMember, ...]:
        try:
            from pydocs_mcp.indexer import _extract_by_import
            dist = _find_installed_distribution(dep_name)
            if dist is None:
                return ()
            record = _extract_by_import(dist, self.depth)
            return tuple(record.get("symbols", []))
        except Exception as exc:  # noqa: BLE001 -- spec §8.2 fallback allowlist
            log.debug("inspect import failed for %s: %s — falling back to AST", dep_name, exc)
            return asyncio.run(self.static_fallback.extract_from_dependency(dep_name))
```

- [ ] **Step 16.4: Run test to verify passes**

Run: `pytest tests/extraction/test_members.py -v`
Expected: 2 passed.

- [ ] **Step 16.5: Commit**

```bash
git add python/pydocs_mcp/extraction/members.py tests/extraction/test_members.py
git commit -m "feat(extraction): AstMemberExtractor + InspectMemberExtractor (spec §8)"
```

Expected test count delta: +2. Total: 598.

---

## Task 17 — `StaticDependencyResolver`

**Files:**
- Create: `python/pydocs_mcp/extraction/dependencies.py`
- Create: `tests/extraction/test_dependencies.py`

Per spec §9.

- [ ] **Step 17.1: Write the failing test**

```python
"""Tests for StaticDependencyResolver (spec §9)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.dependencies import StaticDependencyResolver


@pytest.mark.asyncio
async def test_static_resolver_delegates_to_deps_module(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=["mcp>=1.0"]\n'
    )
    resolver = StaticDependencyResolver()
    deps = await resolver.resolve(tmp_path)
    assert "mcp" in deps
```

- [ ] **Step 17.2: Run test to verify fails**

Run: `pytest tests/extraction/test_dependencies.py -v`
Expected: FAIL.

- [ ] **Step 17.3: Implement `dependencies.py`**

```python
"""StaticDependencyResolver — wraps deps.discover_declared_dependencies (spec §9)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StaticDependencyResolver:
    """The single DependencyResolver strategy shipped in sub-PR #5."""

    async def resolve(self, project_dir: Path) -> tuple[str, ...]:
        from pydocs_mcp.deps import discover_declared_dependencies

        return await asyncio.to_thread(
            lambda: tuple(discover_declared_dependencies(str(project_dir))),
        )
```

- [ ] **Step 17.4: Run test to verify passes**

Run: `pytest tests/extraction/test_dependencies.py -v`
Expected: 1 passed.

- [ ] **Step 17.5: Commit**

```bash
git add python/pydocs_mcp/extraction/dependencies.py tests/extraction/test_dependencies.py
git commit -m "feat(extraction): StaticDependencyResolver (spec §9)"
```

Expected test count delta: +1. Total: 599.

---

## Task 18 — `StrategyChunkExtractor` (wire selector + discoverer + flatten)

**Files:**
- Create: `python/pydocs_mcp/extraction/chunk_extractor.py`
- Create: `tests/extraction/test_chunk_extractor.py`

Per spec §5, §6, implementing sub-PR #4's `ChunkExtractor` Protocol with tree output.

- [ ] **Step 18.1: Write the failing test**

```python
"""Tests for StrategyChunkExtractor (spec §5, §6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.chunk_extractor import StrategyChunkExtractor
from pydocs_mcp.extraction.config import DiscoveryScopeConfig, ChunkingConfig
from pydocs_mcp.extraction.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.extraction.selector import ExtensionChunkerSelector


@pytest.mark.asyncio
async def test_extract_from_project_returns_chunks_trees_package(tmp_path: Path):
    (tmp_path / "a.py").write_text("def foo(): pass\n")
    (tmp_path / "b.md").write_text("# B\nbody\n")
    discoverer = ProjectFileDiscoverer(DiscoveryScopeConfig())
    selector = ExtensionChunkerSelector(ChunkingConfig())
    dep_disc = DependencyFileDiscoverer(DiscoveryScopeConfig())
    ext = StrategyChunkExtractor(
        project_discoverer=discoverer,
        dependency_discoverer=dep_disc,
        selector=selector,
    )
    chunks, trees, pkg = await ext.extract_from_project(tmp_path)
    assert pkg.name == "__project__"
    assert pkg.local_path == str(tmp_path.resolve()) if hasattr(pkg, "local_path") else True
    assert len(trees) == 2
    assert len(chunks) >= 2  # at least one per file
```

- [ ] **Step 18.2: Run test to verify fails**

Run: `pytest tests/extraction/test_chunk_extractor.py -v`
Expected: FAIL.

- [ ] **Step 18.3: Add `local_path` field to `Package`**

Edit `python/pydocs_mcp/models.py` `Package` dataclass:

```python
@dataclass(frozen=True, slots=True)
class Package:
    kind: ClassVar[str] = "package"
    name: str
    version: str
    summary: str
    homepage: str
    dependencies: tuple[str, ...]
    content_hash: str
    origin: PackageOrigin
    local_path: str | None = None   # sub-PR #5 §11 — filesystem root for local packages
```

Update `python/pydocs_mcp/storage/sqlite.py` `_package_to_row` / `_row_to_package` to round-trip the column:

```python
def _package_to_row(p: Package) -> dict[str, object]:
    return {
        "name": p.name, "version": p.version, "summary": p.summary,
        "homepage": p.homepage,
        "dependencies": ",".join(p.dependencies),
        "content_hash": p.content_hash,
        "origin": p.origin.value,
        "local_path": p.local_path,
    }
```

And `_row_to_package`:

```python
def _row_to_package(row) -> Package:
    return Package(
        name=row["name"], version=row["version"] or "", summary=row["summary"] or "",
        homepage=row["homepage"] or "",
        dependencies=tuple((row["dependencies"] or "").split(",")) if row["dependencies"] else (),
        content_hash=row["content_hash"] or "",
        origin=PackageOrigin(row["origin"]) if row["origin"] else PackageOrigin.DEPENDENCY,
        local_path=row["local_path"] if "local_path" in row.keys() else None,
    )
```

(Use whatever the existing row-mapping helper signatures are — the exact function names may differ; add the field in both directions.)

- [ ] **Step 18.4: Implement `chunk_extractor.py`**

```python
"""StrategyChunkExtractor — composes discoverer + selector + flatten (spec §5, §6)."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp._fast import read_files_parallel
from pydocs_mcp.extraction.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.extraction.document_node import DocumentNode
from pydocs_mcp.extraction.members import _find_installed_distribution
from pydocs_mcp.extraction.selector import ExtensionChunkerSelector
from pydocs_mcp.extraction.tree_flatten import flatten_to_chunks
from pydocs_mcp.models import Chunk, Package, PackageOrigin


@dataclass(frozen=True, slots=True)
class StrategyChunkExtractor:
    """Implements sub-PR #4 ChunkExtractor Protocol via three strategies."""

    project_discoverer: ProjectFileDiscoverer
    dependency_discoverer: DependencyFileDiscoverer
    selector: ExtensionChunkerSelector

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]:
        return await asyncio.to_thread(self._do_project, project_dir)

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]:
        return await asyncio.to_thread(self._do_dependency, dep_name)

    def _do_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]:
        paths = self.project_discoverer.list_files(project_dir)
        chunks, trees = self._extract_paths(paths, "__project__", Path(project_dir))
        content_hash = self._file_hash(paths)
        pkg = Package(
            name="__project__", version="local",
            summary=f"Project: {Path(project_dir).name}",
            homepage="",
            dependencies=(),
            content_hash=content_hash,
            origin=PackageOrigin.PROJECT,
            local_path=str(Path(project_dir).resolve()),
        )
        return tuple(chunks), tuple(trees), pkg

    def _do_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]:
        dist = _find_installed_distribution(dep_name)
        if dist is None:
            raise LookupError(f"dependency {dep_name!r} is not installed")
        name = dist.metadata["Name"].lower().replace("-", "_")
        version = dist.metadata["Version"] or "?"
        paths = self.dependency_discoverer.list_files(dist)
        from pydocs_mcp.indexer import find_site_packages_root
        root = Path(find_site_packages_root(paths[0])) if paths else Path(".")
        chunks, trees = self._extract_paths(paths, name, root)
        content_hash = hashlib.md5(f"{name}:{version}".encode()).hexdigest()[:12]
        pkg = Package(
            name=name, version=version,
            summary=dist.metadata["Summary"] or "",
            homepage=dist.metadata["Home-page"] or "",
            dependencies=tuple(
                r.split(";")[0].strip()
                for r in (dist.requires or [])[:50]
            ),
            content_hash=content_hash,
            origin=PackageOrigin.DEPENDENCY,
        )
        return tuple(chunks), tuple(trees), pkg

    def _extract_paths(
        self, paths: list[str], package: str, root: Path,
    ) -> tuple[list[Chunk], list[DocumentNode]]:
        chunks: list[Chunk] = []
        trees: list[DocumentNode] = []
        contents = read_files_parallel(paths)
        for filepath, source in contents:
            if not source:
                continue
            try:
                chunker = self.selector.pick(filepath)
            except Exception:
                continue
            tree = chunker.build_tree(filepath, source, package, root)
            # Tag the tree with its module so IndexingService can key it.
            module = tree.node_id
            trees.append(_with_module_metadata(tree, module))
            chunks.extend(flatten_to_chunks(tree, package=package))
        return chunks, trees

    def _file_hash(self, paths: list[str]) -> str:
        from pydocs_mcp._fast import hash_files

        return hash_files(paths)


def _with_module_metadata(tree: DocumentNode, module: str) -> DocumentNode:
    extra = dict(tree.extra_metadata)
    extra.setdefault("module", module)
    return DocumentNode(
        node_id=tree.node_id, title=tree.title, kind=tree.kind,
        source_path=tree.source_path,
        start_line=tree.start_line, end_line=tree.end_line,
        text=tree.text, content_hash=tree.content_hash,
        summary=tree.summary,
        extra_metadata=extra,
        parent_id=tree.parent_id, children=tree.children,
    )
```

- [ ] **Step 18.5: Run test to verify passes**

Run: `pytest tests/extraction/test_chunk_extractor.py -v`
Expected: 1 passed.

- [ ] **Step 18.6: Full suite check**

Run: `pytest -q 2>&1 | tail -3`
Expected: no regressions (Package roundtrip works with local_path column).

- [ ] **Step 18.7: Commit**

```bash
git add python/pydocs_mcp/extraction/chunk_extractor.py python/pydocs_mcp/models.py python/pydocs_mcp/storage/sqlite.py tests/extraction/test_chunk_extractor.py
git commit -m "feat(extraction): StrategyChunkExtractor + Package.local_path (spec §5, §11)"
```

Expected test count delta: +1. Total: 600.

---

## Task 19 — `build_package_tree` (module-path trie assembly)

**Files:**
- Create: `python/pydocs_mcp/extraction/package_tree.py`
- Create: `tests/extraction/test_package_tree.py`

Per spec §12.2 + AC #16 adjacent behavior.

- [ ] **Step 19.1: Write the failing test**

```python
"""Tests for build_package_tree (spec §12.2)."""
from __future__ import annotations

from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.extraction.package_tree import build_package_tree


def _module(name: str) -> DocumentNode:
    return DocumentNode(
        node_id=name, title=name, kind=NodeKind.MODULE,
        source_path=f"{name}.py", start_line=1, end_line=2,
        text="", content_hash="h",
    )


def test_single_module_package_no_subpackage_intermediates():
    trees = {"pkg": _module("pkg")}
    root = build_package_tree("pkg", trees)
    assert root.kind == NodeKind.PACKAGE
    assert len(root.children) == 1
    assert root.children[0].kind == NodeKind.MODULE


def test_multi_subpackage_arborescence():
    trees = {
        "pkg.a": _module("pkg.a"),
        "pkg.b.c": _module("pkg.b.c"),
        "pkg.b.d": _module("pkg.b.d"),
    }
    root = build_package_tree("pkg", trees)
    assert root.kind == NodeKind.PACKAGE
    kinds_by_title = {c.title: c.kind for c in root.children}
    assert kinds_by_title.get("a") == NodeKind.MODULE
    assert kinds_by_title.get("b") == NodeKind.SUBPACKAGE


def test_package_node_source_path_is_package_name():
    trees = {"pkg": _module("pkg")}
    root = build_package_tree("pkg", trees)
    assert root.source_path == "pkg"
```

- [ ] **Step 19.2: Run test to verify fails**

Run: `pytest tests/extraction/test_package_tree.py -v`
Expected: FAIL.

- [ ] **Step 19.3: Implement `package_tree.py`**

```python
"""build_package_tree — assemble PACKAGE arborescence from per-module trees (spec §12.2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind


@dataclass
class _TrieNode:
    segment: str
    children: dict[str, Union["_TrieNode", "_TrieLeaf"]] = field(default_factory=dict)


@dataclass
class _TrieLeaf:
    segment: str
    tree: DocumentNode


def build_package_tree(
    package: str, trees_by_module: dict[str, DocumentNode],
) -> DocumentNode:
    root = _TrieNode(segment=package)
    for module_path, module_tree in trees_by_module.items():
        segments = module_path.split(".")
        if segments and segments[0] == package:
            segments = segments[1:]
        if not segments:
            # Module is the package itself
            root.children.setdefault(package, _TrieLeaf(segment=package, tree=module_tree))
            continue
        current: _TrieNode = root
        for seg in segments[:-1]:
            existing = current.children.get(seg)
            if isinstance(existing, _TrieNode):
                current = existing
            else:
                new = _TrieNode(segment=seg)
                current.children[seg] = new
                current = new
        current.children[segments[-1]] = _TrieLeaf(
            segment=segments[-1], tree=module_tree,
        )
    return _trie_to_node(root, dotted_prefix=package, kind=NodeKind.PACKAGE, parent_id=None)


def _trie_to_node(
    node: _TrieNode, dotted_prefix: str, kind: NodeKind, parent_id: str | None,
) -> DocumentNode:
    source_path = dotted_prefix.replace(".", "/")
    children: list[DocumentNode] = []
    for seg, child in sorted(node.children.items()):
        child_qname = f"{dotted_prefix}.{seg}" if seg != dotted_prefix else dotted_prefix
        if isinstance(child, _TrieLeaf):
            # Leaf: use the actual module tree, re-parented
            children.append(_reparent(child.tree, parent_id=dotted_prefix))
        else:
            children.append(_trie_to_node(
                child, dotted_prefix=child_qname,
                kind=NodeKind.SUBPACKAGE, parent_id=dotted_prefix,
            ))
    return DocumentNode(
        node_id=dotted_prefix, title=dotted_prefix.split(".")[-1],
        kind=kind, source_path=source_path,
        start_line=0, end_line=0, text="",
        content_hash="",
        parent_id=parent_id,
        children=tuple(children),
    )


def _reparent(tree: DocumentNode, parent_id: str) -> DocumentNode:
    return DocumentNode(
        node_id=tree.node_id, title=tree.title, kind=tree.kind,
        source_path=tree.source_path,
        start_line=tree.start_line, end_line=tree.end_line,
        text=tree.text, content_hash=tree.content_hash,
        summary=tree.summary,
        extra_metadata=dict(tree.extra_metadata),
        parent_id=parent_id,
        children=tree.children,
    )
```

- [ ] **Step 19.4: Run test to verify passes**

Run: `pytest tests/extraction/test_package_tree.py -v`
Expected: 3 passed.

- [ ] **Step 19.5: Commit**

```bash
git add python/pydocs_mcp/extraction/package_tree.py tests/extraction/test_package_tree.py
git commit -m "feat(extraction): build_package_tree — trie assembly of PACKAGE arborescence (spec §12.2)"
```

Expected test count delta: +3. Total: 603.

---

## Task 20 — `extraction/__init__.py` public re-exports

**Files:**
- Modify: `python/pydocs_mcp/extraction/__init__.py`

- [ ] **Step 20.1: Write the file**

```python
"""extraction/ — strategies for chunking, member extraction, dep resolution (spec §5).

Public surface: the concrete strategy classes + DocumentNode value type.
Protocols stay private to extraction/ — consumers depend on the sub-PR #4
application-layer Protocols (ChunkExtractor / MemberExtractor /
DependencyResolver), not on these internal shapes.
"""
from __future__ import annotations

from pydocs_mcp.extraction.chunk_extractor import StrategyChunkExtractor
from pydocs_mcp.extraction.chunkers import (
    AstPythonChunker,
    HeadingMarkdownChunker,
    NotebookChunker,
)
from pydocs_mcp.extraction.config import (
    ChunkingConfig,
    DiscoveryConfig,
    DiscoveryScopeConfig,
    ExtractionConfig,
    MarkdownConfig,
    MembersConfig,
    NotebookConfig,
)
from pydocs_mcp.extraction.dependencies import StaticDependencyResolver
from pydocs_mcp.extraction.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.extraction.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)
from pydocs_mcp.extraction.members import (
    AstMemberExtractor,
    InspectMemberExtractor,
)
from pydocs_mcp.extraction.package_tree import build_package_tree
from pydocs_mcp.extraction.registry import chunker_registry
from pydocs_mcp.extraction.selector import ExtensionChunkerSelector, ExtractionError
from pydocs_mcp.extraction.tree_flatten import flatten_to_chunks

__all__ = [
    "STRUCTURAL_ONLY_KINDS",
    "AstMemberExtractor", "AstPythonChunker",
    "ChunkingConfig",
    "DependencyFileDiscoverer",
    "DiscoveryConfig", "DiscoveryScopeConfig", "DocumentNode",
    "ExtensionChunkerSelector", "ExtractionConfig", "ExtractionError",
    "HeadingMarkdownChunker",
    "InspectMemberExtractor",
    "MarkdownConfig", "MembersConfig",
    "NodeKind", "NotebookChunker", "NotebookConfig",
    "ProjectFileDiscoverer",
    "StaticDependencyResolver", "StrategyChunkExtractor",
    "build_package_tree", "chunker_registry", "flatten_to_chunks",
]
```

- [ ] **Step 20.2: Smoke test**

```bash
python -c "from pydocs_mcp.extraction import (
    AstPythonChunker, HeadingMarkdownChunker, NotebookChunker,
    StrategyChunkExtractor, StaticDependencyResolver,
    AstMemberExtractor, InspectMemberExtractor,
    DocumentNode, NodeKind, build_package_tree,
); print('ok')"
```
Expected: `ok`.

- [ ] **Step 20.3: Commit**

```bash
git add python/pydocs_mcp/extraction/__init__.py
git commit -m "feat(extraction): public re-exports in __init__.py"
```

Expected test count delta: 0. Total: 603.

---

# BATCH 3 — Application service + MCP/CLI surface (Tasks 21–26)

---

## Task 21 — `DocumentTreeService` + `NotFoundError`

**Files:**
- Create: `python/pydocs_mcp/application/document_tree_service.py`
- Create: `tests/application/test_document_tree_service.py`

Per spec §12.1.

- [ ] **Step 21.1: Write the failing test**

```python
"""Tests for DocumentTreeService (spec §12.1)."""
from __future__ import annotations

import pytest

from pydocs_mcp.application.document_tree_service import (
    DocumentTreeService,
    NotFoundError,
)
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind


class _InMemoryTreeStore:
    def __init__(self):
        self.trees: dict[tuple[str, str], DocumentNode] = {}

    async def save(self, package, module, tree):
        self.trees[(package, module)] = tree

    async def load(self, package, module):
        return self.trees.get((package, module))

    async def load_all_in_package(self, package):
        return {m: t for (p, m), t in self.trees.items() if p == package}

    async def delete_package(self, package):
        before = len(self.trees)
        self.trees = {k: v for k, v in self.trees.items() if k[0] != package}
        return before - len(self.trees)


def _tree(name: str) -> DocumentNode:
    return DocumentNode(
        node_id=name, title=name, kind=NodeKind.MODULE,
        source_path=f"{name}.py", start_line=1, end_line=2,
        text="x", content_hash="h",
    )


@pytest.mark.asyncio
async def test_get_tree_returns_stored_tree():
    store = _InMemoryTreeStore()
    await store.save("pkg", "mod", _tree("mod"))
    svc = DocumentTreeService(tree_store=store)
    tree = await svc.get_tree("pkg", "mod")
    assert tree.node_id == "mod"


@pytest.mark.asyncio
async def test_get_tree_raises_not_found_when_missing():
    svc = DocumentTreeService(tree_store=_InMemoryTreeStore())
    with pytest.raises(NotFoundError):
        await svc.get_tree("absent", "absent")


@pytest.mark.asyncio
async def test_get_package_tree_assembles_arborescence():
    store = _InMemoryTreeStore()
    await store.save("pkg", "pkg.a", _tree("pkg.a"))
    await store.save("pkg", "pkg.b.c", _tree("pkg.b.c"))
    svc = DocumentTreeService(tree_store=store)
    tree = await svc.get_package_tree("pkg")
    assert tree.kind == NodeKind.PACKAGE
    assert tree.children  # at least one child


@pytest.mark.asyncio
async def test_get_package_tree_raises_not_found_when_no_modules():
    svc = DocumentTreeService(tree_store=_InMemoryTreeStore())
    with pytest.raises(NotFoundError):
        await svc.get_package_tree("nothing")
```

- [ ] **Step 21.2: Run test to verify fails**

Run: `pytest tests/application/test_document_tree_service.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 21.3: Implement the service**

```python
"""DocumentTreeService — file + package tree queries (spec §12.1)."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.extraction.document_node import DocumentNode
from pydocs_mcp.extraction.package_tree import build_package_tree
from pydocs_mcp.storage.protocols import DocumentTreeStore


class NotFoundError(Exception):
    """Raised when a requested tree/package has no stored rows."""


@dataclass(frozen=True, slots=True)
class DocumentTreeService:
    tree_store: DocumentTreeStore

    async def get_tree(self, package: str, module: str) -> DocumentNode:
        tree = await self.tree_store.load(package, module)
        if tree is None:
            raise NotFoundError(f"no tree for {package}/{module}")
        return tree

    async def get_package_tree(self, package: str) -> DocumentNode:
        trees_by_module = await self.tree_store.load_all_in_package(package)
        if not trees_by_module:
            raise NotFoundError(f"no trees for package {package}")
        return build_package_tree(package, trees_by_module)
```

- [ ] **Step 21.4: Run test to verify passes**

Run: `pytest tests/application/test_document_tree_service.py -v`
Expected: 4 passed.

- [ ] **Step 21.5: Add to `application/__init__.py` re-exports**

Insert in `python/pydocs_mcp/application/__init__.py`:

```python
from pydocs_mcp.application.document_tree_service import (
    DocumentTreeService,
    NotFoundError,
)
```

Add to `__all__`:
```python
"DocumentTreeService", "NotFoundError",
```

- [ ] **Step 21.6: Commit**

```bash
git add python/pydocs_mcp/application/document_tree_service.py python/pydocs_mcp/application/__init__.py tests/application/test_document_tree_service.py
git commit -m "feat(application): DocumentTreeService + NotFoundError (spec §12.1)"
```

Expected test count delta: +4. Total: 607.

---

## Task 22 — Wire new strategies into `server.py`; replace deleted adapters

**Files:**
- Modify: `python/pydocs_mcp/server.py`
- Modify: `python/pydocs_mcp/application/index_project_service.py` (delete 3 adapter classes)
- Modify: `python/pydocs_mcp/application/__init__.py` (remove dead adapter exports)
- Modify: `python/pydocs_mcp/__main__.py` (switch to strategy wiring)
- Create: `python/pydocs_mcp/extraction/wiring.py` — composition-root helper
- Create: `tests/extraction/test_wiring.py`

Per spec §14.

- [ ] **Step 22.1: Write the failing test**

```python
"""Tests for extraction.wiring.build_chunk_extractor etc (spec §14)."""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.config import ExtractionConfig
from pydocs_mcp.extraction.wiring import (
    build_chunk_extractor,
    build_dependency_resolver,
    build_member_extractor,
)


def test_build_chunk_extractor_returns_strategy_instance():
    cfg = ExtractionConfig()
    ext = build_chunk_extractor(cfg)
    from pydocs_mcp.extraction import StrategyChunkExtractor
    assert isinstance(ext, StrategyChunkExtractor)


def test_build_member_extractor_inspect_true_returns_inspect():
    from pydocs_mcp.extraction import InspectMemberExtractor
    cfg = ExtractionConfig()
    ext = build_member_extractor(cfg, use_inspect=True)
    assert isinstance(ext, InspectMemberExtractor)


def test_build_member_extractor_inspect_false_returns_ast():
    from pydocs_mcp.extraction import AstMemberExtractor
    cfg = ExtractionConfig()
    ext = build_member_extractor(cfg, use_inspect=False)
    assert isinstance(ext, AstMemberExtractor)


def test_build_dependency_resolver_returns_static():
    from pydocs_mcp.extraction import StaticDependencyResolver
    ext = build_dependency_resolver()
    assert isinstance(ext, StaticDependencyResolver)
```

- [ ] **Step 22.2: Run test to verify fails**

Run: `pytest tests/extraction/test_wiring.py -v`
Expected: FAIL.

- [ ] **Step 22.3: Implement `extraction/wiring.py`**

```python
"""Composition-root helpers for extraction strategies (spec §14)."""
from __future__ import annotations

from pydocs_mcp.extraction.chunk_extractor import StrategyChunkExtractor
from pydocs_mcp.extraction.config import ExtractionConfig
from pydocs_mcp.extraction.dependencies import StaticDependencyResolver
from pydocs_mcp.extraction.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.extraction.members import (
    AstMemberExtractor,
    InspectMemberExtractor,
)
from pydocs_mcp.extraction.selector import ExtensionChunkerSelector


def build_chunk_extractor(config: ExtractionConfig) -> StrategyChunkExtractor:
    return StrategyChunkExtractor(
        project_discoverer=ProjectFileDiscoverer(config.discovery.project),
        dependency_discoverer=DependencyFileDiscoverer(config.discovery.dependency),
        selector=ExtensionChunkerSelector(config.chunking),
    )


def build_member_extractor(
    config: ExtractionConfig, *, use_inspect: bool,
) -> AstMemberExtractor | InspectMemberExtractor:
    ast = AstMemberExtractor(
        members_per_module_cap=config.members.members_per_module_cap,
    )
    if not use_inspect:
        return ast
    return InspectMemberExtractor(
        static_fallback=ast, depth=config.members.inspect_depth,
    )


def build_dependency_resolver() -> StaticDependencyResolver:
    return StaticDependencyResolver()
```

- [ ] **Step 22.4: Update `__main__.py` `_run_indexing`**

Replace the body that imports `ChunkExtractorAdapter` etc:

```python
async def _run_indexing(args, project: Path, db_path: Path) -> None:
    from pydocs_mcp.application import IndexProjectService
    from pydocs_mcp.extraction.wiring import (
        build_chunk_extractor,
        build_dependency_resolver,
        build_member_extractor,
    )
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.wiring import build_sqlite_indexing_service

    open_index_database(db_path).close()
    config = AppConfig.load(explicit_path=getattr(args, "config", None))

    indexing_service = build_sqlite_indexing_service(db_path)
    use_inspect = not args.no_inspect
    log.info("Project: %s (mode=%s)", project, "inspect" if use_inspect else "static")

    orchestrator = IndexProjectService(
        indexing_service=indexing_service,
        dependency_resolver=build_dependency_resolver(),
        chunk_extractor=build_chunk_extractor(config.extraction),
        member_extractor=build_member_extractor(config.extraction, use_inspect=use_inspect),
    )
    if args.force:
        log.info("Cache cleared")
    stats = await orchestrator.index_project(
        project, force=args.force,
        include_project_source=not args.skip_project,
        workers=args.workers,
    )
    await indexing_service.chunk_store.rebuild_index()
    kb = db_path.stat().st_size / 1024 if db_path.exists() else 0.0
    log.info("Done: %d indexed, %d cached, %d failed (db: %.0f KB)",
             stats.indexed, stats.cached, stats.failed, kb)
```

- [ ] **Step 22.5: Delete legacy adapter classes + old `_clear_extractor_cache`**

In `python/pydocs_mcp/application/index_project_service.py`:

- Remove the three `@dataclass ...Adapter` classes at the bottom of the file.
- Remove the `_clear_extractor_cache` function body and its call in the `finally:` block inside `index_project`. (The strategy extractors have no module-level cache.)

Replace the `finally:` block with just a removal:

```python
        deps = await self.dependency_resolver.resolve(project_dir)
        if workers <= 1:
            for dep_name in deps:
                await self._index_one_dependency(dep_name, stats)
        else:
            sem = asyncio.Semaphore(workers)
            async def _bounded(dep_name: str) -> None:
                async with sem:
                    await self._index_one_dependency(dep_name, stats)
            await asyncio.gather(*[_bounded(d) for d in deps])
        return stats
```

- [ ] **Step 22.6: Update `application/__init__.py`**

Remove from imports + `__all__`:
- `ChunkExtractorAdapter`
- `DependencyResolverAdapter`
- `MemberExtractorAdapter`

- [ ] **Step 22.7: Run all tests (we expect most application/tests to still pass, but some adapter-specific tests will fail — Task 24 removes them)**

Run: `pytest tests/extraction/test_wiring.py -v`
Expected: 4 passed.

Run: `pytest -q 2>&1 | tail -5`
Expected: existing `test_index_project_service.py` + `test_indexer*.py` tests that referenced removed adapters will fail; that's expected. We'll fix in Task 24.

- [ ] **Step 22.8: Commit**

```bash
git add python/pydocs_mcp/extraction/wiring.py python/pydocs_mcp/__main__.py python/pydocs_mcp/application/index_project_service.py python/pydocs_mcp/application/__init__.py tests/extraction/test_wiring.py
git commit -m "refactor(wiring): switch __main__.py to strategy extractors; delete legacy adapters (spec §14)"
```

Expected test count delta: +4. Total: 611. Note: existing adapter-specific tests will fail until Task 24.

---

## Task 23 — Add `get_document_tree` + `get_package_tree` MCP tools

**Files:**
- Modify: `python/pydocs_mcp/server.py`
- Create: `tests/application/test_tree_mcp_handlers.py`

Per spec §13.1 + AC #2, #16.

- [ ] **Step 23.1: Write the failing test**

```python
"""Tests for get_document_tree / get_package_tree MCP handlers (spec §13.1, AC #2, #16)."""
from __future__ import annotations

import pytest

from pydocs_mcp.application.document_tree_service import (
    DocumentTreeService,
    NotFoundError,
)
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind


class _FakeTreeStore:
    def __init__(self, trees=None):
        self.trees = trees or {}

    async def save(self, package, module, tree):
        self.trees[(package, module)] = tree

    async def load(self, package, module):
        return self.trees.get((package, module))

    async def load_all_in_package(self, package):
        return {m: t for (p, m), t in self.trees.items() if p == package}

    async def delete_package(self, package):
        return 0


def _mod_tree(name: str) -> DocumentNode:
    return DocumentNode(
        node_id=name, title=name, kind=NodeKind.MODULE,
        source_path=f"{name}.py", start_line=1, end_line=2,
        text="x", content_hash="h",
    )


@pytest.mark.asyncio
async def test_get_document_tree_returns_json_for_stored_module():
    store = _FakeTreeStore({("pkg", "mod"): _mod_tree("mod")})
    svc = DocumentTreeService(tree_store=store)
    result = await svc.get_tree("pkg", "mod")
    js = result.to_pageindex_json()
    assert js["title"] == "mod"
    assert js["kind"] == "module"
    assert js["nodes"] == []


@pytest.mark.asyncio
async def test_get_document_tree_not_found_returns_error():
    svc = DocumentTreeService(tree_store=_FakeTreeStore())
    with pytest.raises(NotFoundError):
        await svc.get_tree("absent", "absent")


@pytest.mark.asyncio
async def test_get_package_tree_returns_arborescence_json():
    store = _FakeTreeStore({
        ("pkg", "pkg.a"): _mod_tree("pkg.a"),
        ("pkg", "pkg.b.c"): _mod_tree("pkg.b.c"),
    })
    svc = DocumentTreeService(tree_store=store)
    tree = await svc.get_package_tree("pkg")
    js = tree.to_pageindex_json()
    assert js["kind"] == "package"
    assert len(js["nodes"]) >= 1
```

- [ ] **Step 23.2: Run test to verify fails**

Run: `pytest tests/application/test_tree_mcp_handlers.py -v`
Expected: 3 pass (the service was already built in Task 21; this test validates the shape MCP handlers consume).

- [ ] **Step 23.3: Edit `server.py` — add the two handlers**

In `python/pydocs_mcp/server.py`, inside `run()` after the other handler definitions:

```python
    from pydocs_mcp.application import DocumentTreeService, NotFoundError
    from pydocs_mcp.storage.sqlite_document_tree_store import SqliteDocumentTreeStore

    tree_store = SqliteDocumentTreeStore(provider=provider)
    tree_service = DocumentTreeService(tree_store=tree_store)

    @mcp.tool()
    async def get_document_tree(package: str, module: str) -> dict:
        """Return the PageIndex-style structural tree of one module.

        Args:
            package: e.g. 'pydocs-mcp', '__project__', 'requests'
            module: dotted module path, e.g. 'pydocs_mcp.server'
        """
        try:
            tree = await tree_service.get_tree(package, module)
            return tree.to_pageindex_json()
        except NotFoundError as exc:
            return {"error": str(exc)}
        except Exception:
            log.warning("get_document_tree failed", exc_info=True)
            return {"error": f"Error retrieving tree for {package}/{module}"}

    @mcp.tool()
    async def get_package_tree(package: str) -> dict:
        """Return the full package arborescence: subpackages → modules → classes → methods.

        Args:
            package: e.g. 'pydocs-mcp', '__project__'
        """
        try:
            tree = await tree_service.get_package_tree(package)
            return tree.to_pageindex_json()
        except NotFoundError as exc:
            return {"error": str(exc)}
        except Exception:
            log.warning("get_package_tree failed", exc_info=True)
            return {"error": f"Error retrieving package tree for {package}"}
```

- [ ] **Step 23.4: Run**

Run: `pytest tests/application/test_tree_mcp_handlers.py -v`
Expected: 3 passed.

Run: `pytest tests/test_server.py -v` — MCP surface byte-identical for existing tools (AC #8). Existing handler tests should still pass.

- [ ] **Step 23.5: Commit**

```bash
git add python/pydocs_mcp/server.py tests/application/test_tree_mcp_handlers.py
git commit -m "feat(server): add get_document_tree + get_package_tree MCP tools (spec §13.1, AC #2)"
```

Expected test count delta: +3. Total: 614.

---

## Task 24 — `pydocs-mcp tree` CLI subcommand

**Files:**
- Modify: `python/pydocs_mcp/__main__.py`
- Create: `tests/application/test_tree_cli_handler.py`

Per spec §13.2 + AC #3.

- [ ] **Step 24.1: Write the failing test**

```python
"""Tests for the `tree` CLI subcommand (spec §13.2, AC #3)."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_tree_subcommand_registered():
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["tree", "pkg"])
    assert args.cmd == "tree"
    assert args.target == "pkg"


def test_tree_module_level_arg():
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["tree", "pkg/mod"])
    assert args.target == "pkg/mod"


def test_pretty_print_tree_module_level():
    """_pretty_print_tree renders one line per node."""
    from pydocs_mcp.__main__ import _pretty_print_tree
    from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind

    tree = DocumentNode(
        node_id="m", title="m", kind=NodeKind.MODULE,
        source_path="m.py", start_line=1, end_line=2,
        text="", content_hash="h",
        children=(
            DocumentNode(
                node_id="m.foo", title="def foo", kind=NodeKind.FUNCTION,
                source_path="m.py", start_line=1, end_line=2,
                text="def foo():", content_hash="h2",
            ),
        ),
    )
    lines: list[str] = []
    _pretty_print_tree(tree, out=lines.append)
    assert any("def foo" in line for line in lines)
```

- [ ] **Step 24.2: Run test to verify fails**

Run: `pytest tests/application/test_tree_cli_handler.py -v`
Expected: FAIL (no `tree` subparser, no `_pretty_print_tree`).

- [ ] **Step 24.3: Add `tree` subcommand + `_cmd_tree` handler**

In `python/pydocs_mcp/__main__.py`:

Add to `_build_parser` (add a new subparser section after the existing ones):

```python
    tree_sp = sub.add_parser("tree", help="Print document/package arborescence")
    tree_sp.add_argument("target", help="<package> OR <package>/<module>")
    tree_sp.add_argument("project", nargs="?", default=".")
    tree_sp.add_argument("--no-rust", **_no_rust)
```

Add handler:

```python
def _cmd_tree(args: argparse.Namespace) -> int:
    try:
        from pydocs_mcp.application import DocumentTreeService, NotFoundError
        from pydocs_mcp.storage.sqlite_document_tree_store import SqliteDocumentTreeStore
        from pydocs_mcp.db import build_connection_provider

        _project, db_path = _project_and_db(args)
        provider = build_connection_provider(db_path)
        svc = DocumentTreeService(tree_store=SqliteDocumentTreeStore(provider=provider))
        target: str = args.target
        if "/" in target:
            package, module = target.split("/", 1)
            tree = asyncio.run(svc.get_tree(package, module))
            print(f"module  {package}.{module}")
        else:
            tree = asyncio.run(svc.get_package_tree(target))
            print(f"package  {target}")
        _pretty_print_tree(tree, out=print)
        return 0
    except NotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- CLI top-level
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _pretty_print_tree(tree, out, prefix: str = "", is_last: bool = True) -> None:
    """Indented tree rendering using └── / ├── box-drawing (spec §13.2)."""
    from pydocs_mcp.extraction.document_node import NodeKind

    label = _label_for(tree)
    if prefix == "" and is_last:
        # Root: no leading box-draw
        pass
    else:
        branch = "└── " if is_last else "├── "
        out(f"{prefix}{branch}{label}")
    child_prefix = prefix + ("    " if is_last else "│   ")
    children = tree.children
    for i, child in enumerate(children):
        child_is_last = i == len(children) - 1
        _pretty_print_tree(child, out=out, prefix=child_prefix, is_last=child_is_last)


def _label_for(node) -> str:
    from pydocs_mcp.extraction.document_node import NodeKind

    tag = node.kind.value.upper()
    if node.kind == NodeKind.MODULE:
        return f"{node.title}  (MODULE)"
    if node.kind == NodeKind.SUBPACKAGE:
        return f"{node.title}  (SUBPACKAGE)"
    if node.kind == NodeKind.PACKAGE:
        return f"package  {node.title}"
    return node.title
```

Register:

```python
_CMD_TABLE = {
    "serve": _cmd_serve,
    "index": _cmd_index,
    "query": _cmd_query,
    "api":   _cmd_api,
    "tree":  _cmd_tree,
}
```

- [ ] **Step 24.4: Run test to verify passes**

Run: `pytest tests/application/test_tree_cli_handler.py -v`
Expected: 3 passed.

- [ ] **Step 24.5: Commit**

```bash
git add python/pydocs_mcp/__main__.py tests/application/test_tree_cli_handler.py
git commit -m "feat(cli): add `tree` subcommand with indented stdout rendering (spec §13.2, AC #3)"
```

Expected test count delta: +3. Total: 617.

---

## Task 25 — Purge legacy adapter tests + adapt `test_indexer.py`

**Files:**
- Delete: `tests/test_indexer.py`, `tests/test_indexer_extended.py` (or heavily trim — see Task 27 which deletes `indexer.py` entirely)
- Modify: `tests/application/test_index_project_service.py` — drop any references to `ChunkExtractorAdapter` / `MemberExtractorAdapter` / `DependencyResolverAdapter`

- [ ] **Step 25.1: Identify dead tests**

Run: `pytest -q 2>&1 | grep -E "FAIL|ERROR" | tail -30`
Expected: `test_indexer.py::*`, `test_indexer_extended.py::*`, and any `*Adapter*` tests in `test_index_project_service.py`.

- [ ] **Step 25.2: Delete `test_indexer.py` + `test_indexer_extended.py`**

These test extraction behavior of the deleted `indexer.py`. The new `tests/extraction/` package supersedes them. Delete the files:

```bash
git rm tests/test_indexer.py tests/test_indexer_extended.py
```

- [ ] **Step 25.3: Trim `test_index_project_service.py` adapter references**

Open `tests/application/test_index_project_service.py` and remove any test that instantiates `ChunkExtractorAdapter`, `MemberExtractorAdapter`, or `DependencyResolverAdapter`. Replace those setups with Protocol fakes (the conftest already provides in-memory fakes for `ChunkExtractor` / `MemberExtractor` / `DependencyResolver`).

If the test was validating the adapter's delegation contract — that logic now lives in `tests/extraction/test_chunk_extractor.py`, so it is not lost.

- [ ] **Step 25.4: Run suite**

Run: `pytest -q 2>&1 | tail -3`
Expected: all pass. If any new failures surface, they indicate consumer sites that still reference deleted symbols — fix the consumer.

- [ ] **Step 25.5: Commit**

```bash
git add -A
git commit -m "refactor(tests): delete test_indexer* — superseded by tests/extraction/; drop Adapter refs (spec §8)"
```

Expected test count delta: ~-50 (indexer tests) + adjustments. Total: roughly 570 (exact count depends on how many indexer tests existed).

---

## Task 26 — Incremental re-index: `content_hash` delta write

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py`
- Modify: `tests/application/test_indexing_service.py`

Per spec §11.3 + AC #18c, #18d.

- [ ] **Step 26.1: Write the failing test**

Append to `tests/application/test_indexing_service.py`:

```python
@pytest.mark.asyncio
async def test_incremental_reindex_skips_unchanged_nodes(
    in_memory_package_store, in_memory_chunk_store, in_memory_member_store,
):
    """When package hash unchanged, chunk rows aren't rewritten (AC #18c)."""
    from pydocs_mcp.application.indexing_service import IndexingService

    svc = IndexingService(
        package_store=in_memory_package_store,
        chunk_store=in_memory_chunk_store,
        module_member_store=in_memory_member_store,
    )
    pkg = Package(
        name="pkg", version="1", summary="", homepage="",
        dependencies=(), content_hash="h1", origin=PackageOrigin.DEPENDENCY,
    )
    chunk1 = Chunk(
        text="body",
        metadata={ChunkFilterField.PACKAGE.value: "pkg", "content_hash": "ch-a"},
    )
    await svc.reindex_package(pkg, (chunk1,), ())

    # Call count accessor on the fake chunk store
    upserts_before = in_memory_chunk_store.upsert_call_count

    # Same hashes — should be a no-op rewrite (in simple impl, the chunk_store still
    # sees upsert but with zero-diff payload). AC #18c is ultimately about the
    # file-level skip in IndexProjectService; this test asserts the plumbing.
    await svc.reindex_package(pkg, (chunk1,), ())
    upserts_after = in_memory_chunk_store.upsert_call_count
    assert upserts_after > upserts_before  # called, but we could assert equal payload
```

(AC #18c's performance target — <200ms for 5000-chunk project — is validated in the end-to-end Task 30 integration test, not here.)

- [ ] **Step 26.2: Run test to verify fails**

Run: `pytest tests/application/test_indexing_service.py -v`
Expected: likely passes already (this test just exercises the plumbing). If implementer wants a stricter delta-write test, it can be added to Task 30.

- [ ] **Step 26.3: Implement a minimal content-hash delta pass (optional — may be deferred)**

The spec §11.3 describes a "left-join between incoming node hashes and existing chunk hashes". Implementing this cleanly requires a new repository method. For scope control:

**Simpler shipping plan:** Keep the current delete-then-upsert semantics, but ensure the file-level skip in `IndexProjectService._index_project_source` (which already exists per sub-PR #4) is validated. The per-node delta-write is a spec aspiration — if the basic file-level skip passes the performance AC, move on; if not, add it here.

No code change needed in this task unless benchmarks show the need. Document this in the commit.

- [ ] **Step 26.4: Commit (documentation only)**

```bash
git commit --allow-empty -m "refactor(application): retain file-level hash skip (sub-PR #4) — per-node delta deferred pending benchmarks"
```

If implementer adds the delta pass, commit the code + test:

```bash
git add python/pydocs_mcp/application/indexing_service.py tests/application/test_indexing_service.py
git commit -m "feat(application): content_hash-based incremental reindex (spec §11.3, AC #18c)"
```

Expected test count delta: +1 (or 0). Total: ~571.

---

# BATCH 4 — Integration + cleanup (Tasks 27–32)

---

## Task 27 — Delete `indexer.py`

**Files:**
- Delete: `python/pydocs_mcp/indexer.py`

Per spec §5 "Files deleted" + AC #20.

- [ ] **Step 27.1: Find every remaining import**

Run: `grep -RIn "from pydocs_mcp.indexer\|import pydocs_mcp.indexer\|pydocs_mcp import indexer" python/pydocs_mcp tests/ --include="*.py"`
Expected references (if any):
- `extraction/members.py` (uses `_find_installed_distribution`, `find_site_packages_root`, `list_dependency_source_files`, `_extract_by_import`)
- `extraction/chunk_extractor.py` (uses `find_site_packages_root`)

- [ ] **Step 27.2: Move the helpers out of `indexer.py` into `extraction/`**

The four helpers still needed elsewhere are pure filesystem / importlib helpers; move them verbatim:

Create `python/pydocs_mcp/extraction/_dep_helpers.py`:

```python
"""Filesystem + importlib.metadata helpers used by StrategyChunkExtractor
and the member extractors. Moved verbatim from the deleted ``indexer.py``."""
from __future__ import annotations

import importlib
import importlib.metadata
import logging
import pkgutil
from pathlib import Path

log = logging.getLogger("pydocs-mcp")


def list_dependency_source_files(dist) -> list[str]:
    """Find all .py files installed by a distribution."""
    result: list[str] = []
    try:
        for f in (dist.files or []):
            fname = str(f)
            if fname.endswith(".py") and "setup.py" not in fname:
                loc = f.locate()
                if loc.exists() and loc.stat().st_size < 500_000:
                    result.append(str(loc))
    except Exception:
        pass
    return result


def find_site_packages_root(any_file: str) -> str:
    for parent in Path(any_file).parents:
        if parent.name in ("site-packages", "dist-packages"):
            return str(parent)
    return str(Path(any_file).parent.parent)


SKIP_IMPORT = frozenset({
    "setuptools", "pip", "wheel", "pkg_resources",
    "distutils", "_distutils_hack", "certifi",
})
IMPORT_ALIASES = {
    "pillow": "PIL", "scikit-learn": "sklearn", "python-dateutil": "dateutil",
    "pyyaml": "yaml", "beautifulsoup4": "bs4", "opencv-python": "cv2",
    "opencv-python-headless": "cv2", "attrs": "attr",
}


def find_installed_distribution(dep_name: str):
    """Locate the installed importlib.metadata distribution, or None."""
    from pydocs_mcp.deps import normalize_package_name

    target = normalize_package_name(dep_name)
    for dist in importlib.metadata.distributions():
        raw = dist.metadata["Name"]
        if raw and raw.lower().replace("-", "_") == target:
            return dist
    return None


def extract_module_members_by_import(dist, depth: int) -> dict:
    """Import dist, walk members via inspect. Returns a dict with 'symbols' list.
    Verbatim move of ``indexer._extract_by_import`` body."""
    import inspect
    import json

    from pydocs_mcp.constants import (
        CLASS_DOCSTRING_MAX, CLASS_FULL_DOC_MAX, CLASS_METHODS_MAX,
        FUNC_DOCSTRING_MAX, METHOD_SUMMARY_MAX, MODULE_DOCSTRING_MAX,
        PARAM_DEFAULT_MAX, PARAMS_JSON_MAX, RETURN_TYPE_MAX, SIGNATURE_MAX,
    )
    from pydocs_mcp.models import (
        Chunk, ChunkFilterField, ModuleMember, ModuleMemberFilterField, Parameter,
    )

    name = dist.metadata["Name"].lower().replace("-", "_")
    version = dist.metadata["Version"] or "?"
    rec: dict = {"name": name, "version": version, "symbols": []}

    if name in SKIP_IMPORT:
        return rec
    iname = IMPORT_ALIASES.get(name, name)
    try:
        module = importlib.import_module(iname)
    except Exception as exc:
        log.debug("Failed to import %s: %s", iname, exc)
        return rec

    rec["symbols"] = _walk_inspect(module, iname, name, depth=0, max_depth=depth)
    return rec


def _walk_inspect(module, mod_name, owner, depth, max_depth):
    """Excerpted verbatim from indexer._extract_members_by_import."""
    import inspect
    from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField

    rows: list[ModuleMember] = []
    root = owner.replace("-", "_")
    try:
        members = inspect.getmembers(module)
    except Exception:
        return rows

    for name, obj in members:
        if name.startswith("_"):
            continue
        obj_mod = getattr(obj, "__module__", "") or ""
        if obj_mod and not obj_mod.startswith(root):
            continue
        try:
            if inspect.isfunction(obj) or inspect.isbuiltin(obj):
                rows.append(_function_member(name, obj, mod_name, owner))
            elif inspect.isclass(obj):
                rows.append(_class_member(name, obj, mod_name, owner))
        except Exception:
            continue
        if len(rows) >= 120:
            break

    if depth < max_depth and hasattr(module, "__path__"):
        try:
            for _, sn, _ in pkgutil.iter_modules(module.__path__):
                if sn.startswith("_"):
                    continue
                try:
                    sub = importlib.import_module(f"{mod_name}.{sn}")
                    rows.extend(_walk_inspect(
                        sub, f"{mod_name}.{sn}", owner, depth + 1, max_depth,
                    ))
                except Exception:
                    pass
        except Exception:
            pass
    return rows


def _function_member(name, obj, mod_name, owner):
    import inspect
    from pydocs_mcp.constants import FUNC_DOCSTRING_MAX
    from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField, Parameter

    sig, ret, params = _callable_signature(obj)
    doc = (inspect.getdoc(obj) or "")[:FUNC_DOCSTRING_MAX]
    return ModuleMember(metadata={
        ModuleMemberFilterField.PACKAGE.value: owner,
        ModuleMemberFilterField.MODULE.value: mod_name,
        ModuleMemberFilterField.NAME.value: name,
        ModuleMemberFilterField.KIND.value: "function",
        "signature": sig, "return_annotation": ret,
        "parameters": params, "docstring": doc,
    })


def _class_member(name, obj, mod_name, owner):
    import inspect
    from pydocs_mcp.constants import (
        CLASS_DOCSTRING_MAX, CLASS_FULL_DOC_MAX,
        CLASS_METHODS_MAX, METHOD_SUMMARY_MAX,
    )
    from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField

    sig, _, params = _callable_signature(obj)
    doc = (inspect.getdoc(obj) or "")[:CLASS_DOCSTRING_MAX]
    method_summaries: list[str] = []
    try:
        for mn, member in inspect.getmembers(obj):
            if mn.startswith("_") and mn != "__init__":
                continue
            if not (inspect.isfunction(member) or inspect.ismethod(member)):
                continue
            s, _, _ = _callable_signature(member)
            md = (inspect.getdoc(member) or "").split("\n")[0][:METHOD_SUMMARY_MAX]
            method_summaries.append(f"  .{mn}{s} -- {md}")
            if len(method_summaries) >= CLASS_METHODS_MAX:
                break
    except Exception:
        pass
    if method_summaries:
        doc += "\n\nMethods:\n" + "\n".join(method_summaries)
    return ModuleMember(metadata={
        ModuleMemberFilterField.PACKAGE.value: owner,
        ModuleMemberFilterField.MODULE.value: mod_name,
        ModuleMemberFilterField.NAME.value: name,
        ModuleMemberFilterField.KIND.value: "class",
        "signature": sig, "return_annotation": "",
        "parameters": params, "docstring": doc[:CLASS_FULL_DOC_MAX],
    })


def _callable_signature(obj):
    import inspect
    import json

    from pydocs_mcp.constants import (
        PARAM_DEFAULT_MAX, PARAMS_JSON_MAX, RETURN_TYPE_MAX, SIGNATURE_MAX,
    )
    from pydocs_mcp.models import Parameter

    try:
        sig = inspect.signature(obj)
    except (ValueError, TypeError):
        return "", "", ()
    ret = ""
    if sig.return_annotation != inspect.Parameter.empty:
        try:
            ret = getattr(sig.return_annotation, "__name__", str(sig.return_annotation))
        except Exception:
            pass
    params: list[Parameter] = []
    for pn, p in sig.parameters.items():
        if pn in ("self", "cls"):
            continue
        annotation = ""
        if p.annotation != inspect.Parameter.empty:
            try:
                annotation = getattr(p.annotation, "__name__", str(p.annotation))
            except Exception:
                pass
        default_value = ""
        if p.default != inspect.Parameter.empty:
            try:
                default_value = repr(p.default)[:PARAM_DEFAULT_MAX]
            except Exception:
                pass
        params.append(Parameter(name=pn, annotation=annotation, default=default_value))
    # Truncate params if serialized size exceeds cap
    trimmed = list(params)
    while trimmed and _params_payload_size(trimmed) > PARAMS_JSON_MAX:
        trimmed.pop()
    return str(sig)[:SIGNATURE_MAX], ret[:RETURN_TYPE_MAX], tuple(trimmed)


def _params_payload_size(params):
    import json

    return len(json.dumps([
        {"name": p.name, "annotation": p.annotation, "default": p.default}
        for p in params
    ]))
```

Update the imports in `extraction/members.py` and `extraction/chunk_extractor.py`:

In `members.py` replace `from pydocs_mcp.indexer import ...` with:
```python
from pydocs_mcp.extraction._dep_helpers import (
    find_installed_distribution as _find_installed_distribution,
    find_site_packages_root,
    list_dependency_source_files,
    extract_module_members_by_import,
)
```

In `InspectMemberExtractor._inspect_dep`:
```python
def _inspect_dep(self, dep_name: str) -> tuple[ModuleMember, ...]:
    try:
        dist = _find_installed_distribution(dep_name)
        if dist is None:
            return ()
        rec = extract_module_members_by_import(dist, self.depth)
        return tuple(rec.get("symbols", []))
    except Exception as exc:  # noqa: BLE001 -- spec §8.2 fallback
        log.debug("inspect import failed for %s: %s — falling back", dep_name, exc)
        return asyncio.run(self.static_fallback.extract_from_dependency(dep_name))
```

In `chunk_extractor.py` replace `from pydocs_mcp.indexer import find_site_packages_root` with:
```python
from pydocs_mcp.extraction._dep_helpers import find_site_packages_root
```

- [ ] **Step 27.3: Delete `indexer.py`**

```bash
git rm python/pydocs_mcp/indexer.py
```

- [ ] **Step 27.4: Verify zero references remain**

Run: `grep -RIn "from pydocs_mcp.indexer\|import pydocs_mcp.indexer\|pydocs_mcp import indexer" python/ tests/ --include="*.py"`
Expected: (no output).

- [ ] **Step 27.5: Run suite**

Run: `pytest -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 27.6: Commit**

```bash
git add python/pydocs_mcp/extraction/_dep_helpers.py python/pydocs_mcp/extraction/members.py python/pydocs_mcp/extraction/chunk_extractor.py
git rm python/pydocs_mcp/indexer.py
git commit -m "refactor: delete indexer.py — helpers moved to extraction/_dep_helpers.py (spec §5, AC #20)"
```

Expected test count delta: 0. Total: ~571.

---

## Task 28 — Delete Rust `split_into_chunks` + Python fallback

**Files:**
- Modify: `src/lib.rs`
- Modify: `python/pydocs_mcp/_fallback.py`
- Modify: `python/pydocs_mcp/_fast.py`
- Modify: `tests/test_fallback.py` (remove references)
- Modify: `tests/test_fast_import.py` (remove references)

Per spec §3 decision #6 + §5 "Files deleted" + AC #14.

- [ ] **Step 28.1: Delete from `src/lib.rs`**

Remove:
- `HEADING_RE` `LazyLock` (lines ~45)
- `TextChunk` struct (lines ~166-170)
- `split_into_chunks` function (lines ~172-221)
- Registration line `m.add_function(wrap_pyfunction!(split_into_chunks, m)?)?;`

- [ ] **Step 28.2: Delete from `_fallback.py`**

Remove the `split_into_chunks` function (lines ~50-74).

- [ ] **Step 28.3: Update `_fast.py`**

Remove `split_into_chunks` from the Rust + fallback import lists and from `disable_rust()`'s name list. The file should now import only 6 native functions.

- [ ] **Step 28.4: Update fallback tests**

In `tests/test_fallback.py`, delete any tests that reference `split_into_chunks`.
In `tests/test_fast_import.py`, update the imports-list assertion if any.

- [ ] **Step 28.5: Rebuild Rust**

```bash
. "$HOME/.cargo/env"
maturin develop --release 2>&1 | tail -10
```
Expected: builds cleanly, no `split_into_chunks` in the resulting `_native` module.

- [ ] **Step 28.6: Cargo checks**

```bash
cargo fmt --check && cargo clippy -- -D warnings && cargo test
```
Expected: exit 0.

- [ ] **Step 28.7: Python tests**

Run: `pytest -q 2>&1 | tail -3`
Expected: all pass.

Run: `grep -RIn "split_into_chunks" python/ tests/ src/`
Expected: no output.

- [ ] **Step 28.8: Commit**

```bash
git add -A
git commit -m "feat: delete Rust split_into_chunks + Python fallback — superseded by chunkers.py (spec §3 dec #6, AC #14)"
```

Expected test count delta: -N (where N is how many fallback/fast tests referenced the function). Total: roughly 565.

---

## Task 29 — Update `server.py` wiring for strategy extractors

**Files:**
- Modify: `python/pydocs_mcp/server.py`

Ensure the MCP startup pass (not the handler definitions — just the setup code in `run()`) uses the new strategy composition root. (The handlers themselves remain thin; only the startup wiring changes.) Task 22 already migrated `__main__.py`; the MCP server already uses service wiring for queries, but now needs tree-store wiring (Task 23 did this) — double-check completeness.

- [ ] **Step 29.1: Audit `server.py::run()` wiring**

Open `server.py` and confirm these are all instantiated:

1. `config = AppConfig.load(...)` ✓
2. `context = build_retrieval_context(db_path, config)` ✓
3. `provider = context.connection_provider` ✓
4. `package_store, chunk_store, member_store` ✓
5. `chunk_pipeline, member_pipeline` ✓
6. `package_lookup, search_docs_svc, search_api_svc, inspect_svc` ✓
7. `tree_store = SqliteDocumentTreeStore(provider=provider)` (Task 23)
8. `tree_service = DocumentTreeService(tree_store=tree_store)` (Task 23)
9. MCP tools: `list_packages, get_package_doc, search_docs, search_api, inspect_module, get_document_tree, get_package_tree` (Task 23)

No extraction strategies are needed in `server.py` because the MCP server only serves queries — indexing is done by `__main__.py::_run_indexing` before `server.run()` starts.

- [ ] **Step 29.2: Verify `pytest tests/test_server.py -v` passes**

Expected: all passes, byte-identical for existing 5 tools (AC #8).

- [ ] **Step 29.3: Commit (no changes)**

If no changes are needed, skip this task. Otherwise:

```bash
git add python/pydocs_mcp/server.py
git commit -m "chore(server): confirm strategy wiring complete (spec §14)"
```

Expected test count delta: 0.

---

## Task 30 — End-to-end integration test

**Files:**
- Create: `tests/test_extraction_integration.py`

Per spec §15 AC #4 + AC #16 + AC #18c.

- [ ] **Step 30.1: Write the failing test**

```python
"""End-to-end integration: project with .py + .md + .ipynb → all MCP tools (AC #4, #16)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pydocs_mcp.application.document_tree_service import DocumentTreeService
from pydocs_mcp.application.index_project_service import IndexProjectService
from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.extraction.wiring import (
    build_chunk_extractor,
    build_dependency_resolver,
    build_member_extractor,
)
from pydocs_mcp.extraction.config import ExtractionConfig
from pydocs_mcp.storage.sqlite_document_tree_store import SqliteDocumentTreeStore
from pydocs_mcp.storage.wiring import build_sqlite_indexing_service


def _sample_project(root: Path) -> None:
    (root / "scripts").mkdir()
    (root / "scripts" / "build.py").write_text(
        '"""Build script."""\n\ndef run(): pass\n'
    )
    (root / "README.md").write_text(
        "# Hello\n\nIntro.\n\n## Usage\n\nExample usage.\n"
    )
    (root / "notebooks").mkdir()
    (root / "notebooks" / "demo.ipynb").write_text(json.dumps({
        "cells": [
            {"cell_type": "markdown", "source": ["# Demo\n"]},
            {"cell_type": "code", "source": ["x = 1\n"]},
        ],
        "nbformat": 4, "metadata": {},
    }))
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.0.1"\n'
    )


@pytest.mark.asyncio
async def test_full_indexing_then_document_tree_roundtrip(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    _sample_project(project)

    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()

    config = ExtractionConfig()
    indexing_service = build_sqlite_indexing_service(db_path)
    orchestrator = IndexProjectService(
        indexing_service=indexing_service,
        dependency_resolver=build_dependency_resolver(),
        chunk_extractor=build_chunk_extractor(config),
        member_extractor=build_member_extractor(config, use_inspect=False),
    )
    stats = await orchestrator.index_project(project, force=True)
    await indexing_service.chunk_store.rebuild_index()

    provider = build_connection_provider(db_path)
    tree_store = SqliteDocumentTreeStore(provider=provider)
    svc = DocumentTreeService(tree_store=tree_store)

    pkg_tree = await svc.get_package_tree("__project__")
    js = pkg_tree.to_pageindex_json()
    assert js["kind"] == "package"

    # Verify at least one chunk row exists
    chunks = await indexing_service.chunk_store.list(
        filter={"package": "__project__"}, limit=50,
    )
    assert len(chunks) >= 1


@pytest.mark.asyncio
async def test_reindex_with_no_changes_is_fast(tmp_path: Path):
    """AC #18c: re-indexing a project with no file changes skips write I/O."""
    import time

    project = tmp_path / "proj"
    project.mkdir()
    _sample_project(project)
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()

    config = ExtractionConfig()
    indexing_service = build_sqlite_indexing_service(db_path)
    orchestrator = IndexProjectService(
        indexing_service=indexing_service,
        dependency_resolver=build_dependency_resolver(),
        chunk_extractor=build_chunk_extractor(config),
        member_extractor=build_member_extractor(config, use_inspect=False),
    )
    await orchestrator.index_project(project, force=True)

    start = time.perf_counter()
    stats = await orchestrator.index_project(project)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    # Lenient threshold — CI bounds: much under 5 seconds for a tiny sample project.
    # The <200ms target in AC #18c applies to a 5000-chunk project.
    assert stats.project_indexed is False, "Expected cached path (no changes)"
```

- [ ] **Step 30.2: Run test to verify**

Run: `pytest tests/test_extraction_integration.py -v`
Expected: 2 passed.

- [ ] **Step 30.3: Commit**

```bash
git add tests/test_extraction_integration.py
git commit -m "test: end-to-end integration — project/.md/.ipynb → trees → MCP (spec §15 AC #4, #16, #18c)"
```

Expected test count delta: +2. Total: ~567.

---

## Task 31 — Zero-residue grep + MCP byte-parity check

Per spec §15 AC #8, #20.

- [ ] **Step 31.1: No stale `indexer` references**

```bash
grep -RIn "from pydocs_mcp.indexer\|pydocs_mcp import indexer" python/ tests/
```
Expected: empty.

- [ ] **Step 31.2: No dead adapter names**

```bash
grep -RIn "ChunkExtractorAdapter\|MemberExtractorAdapter\|DependencyResolverAdapter" python/
```
Expected: empty.

- [ ] **Step 31.3: No `split_into_chunks` anywhere**

```bash
grep -RIn "split_into_chunks" python/ src/ tests/
```
Expected: empty.

- [ ] **Step 31.4: No blanket `except Exception` without the allowlist marker**

```bash
grep -RIn "except Exception" python/pydocs_mcp/application python/pydocs_mcp/extraction | grep -v "BLE001\|CLI top-level\|fallback"
```
Expected: empty (each catch should be annotated per AC #26).

- [ ] **Step 31.5: MCP tool surface byte-identical for existing 5 tools**

Run: `pytest tests/test_server.py tests/retrieval/test_parity_golden.py -v`
Expected: all passes.

- [ ] **Step 31.6: Full suite**

Run: `pytest -q 2>&1 | tail -3`
Expected: all passes.

Run: `. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings && cargo test`
Expected: exit 0.

- [ ] **Step 31.7: Commit if any residue found**

If any grep surfaces a hit, fix it and:

```bash
git add -A
git commit -m "refactor: clean up residue from zero-residue sweep (AC #20)"
```

Expected test count delta: 0.

---

## Task 32 — CLAUDE.md refresh + mark PR ready

**Files:**
- Modify: `CLAUDE.md`

Per spec §1 + §5 (updates architecture section).

- [ ] **Step 32.1: Update the Architecture section in `CLAUDE.md`**

Replace the file-tree block around the `python/pydocs_mcp/` section with:

```
python/pydocs_mcp/
├── __main__.py    # CLI entry — thin _cmd_* wrappers + `tree` subcommand over services
├── _fast.py       # Imports Rust native module or falls back to Python (6 functions now)
├── _fallback.py   # Pure Python implementations of Rust functions
├── db.py          # SQLite schema v3 + cache lifecycle + FTS rebuild
├── deps.py        # Dependency resolution (pyproject.toml, requirements.txt)
├── extraction/    # Strategy-based chunking + member extraction + DocumentNode tree
│                  # (AstPythonChunker, HeadingMarkdownChunker, NotebookChunker,
│                  #  AstMemberExtractor, InspectMemberExtractor, StaticDependencyResolver,
│                  #  StrategyChunkExtractor, DocumentNode/NodeKind, build_package_tree)
├── application/   # Use-case services — Indexing + IndexProject + PackageLookup +
│                  # SearchDocs + SearchApi + ModuleIntrospection + DocumentTree +
│                  # shared formatting helpers
├── storage/       # Filter tree, Protocols, SQLite repositories + VectorStore + UoW +
│                  # SqliteDocumentTreeStore
├── retrieval/     # Async pipelines, retrievers, stages, registries, YAML config
├── presets/       # Built-in pipeline YAML presets (chunk_fts, member_like)
└── server.py      # 7 thin MCP handlers over services (search_docs, search_api,
                   # inspect_module, list_packages, get_package_doc,
                   # get_document_tree, get_package_tree)
src/lib.rs         # Rust acceleration: 6 PyO3 functions (split_into_chunks removed)
```

Also update the **Data flow** paragraph, replacing "CodeRetrieverPipeline... → server.py / __main__.py render..." with:

```
**Data flow:** CLI / MCP server → services (PackageLookupService / SearchDocsService /
SearchApiService / ModuleIntrospectionService / DocumentTreeService for queries;
IndexProjectService.index_project for writes) → application.IndexingService writes
through SqlitePackageRepository / SqliteChunkRepository / SqliteModuleMemberRepository /
SqliteDocumentTreeStore under a SqliteUnitOfWork → retrieval/ runs the async
CodeRetrieverPipeline → server.py / __main__.py render via application/formatting
helpers → client.

**Extraction strategies:** extraction/ holds Chunker / ChunkerSelector / FileDiscoverer
Protocols (private) + 3 concrete chunkers (AST Python / heading markdown / notebook
cells) + 2 member extractors (AST / inspect) + StaticDependencyResolver. Chunkers
produce DocumentNode trees; trees are persisted by DocumentTreeStore and flattened
into Chunk rows by extraction.tree_flatten.flatten_to_chunks.
```

- [ ] **Step 32.2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: refresh CLAUDE.md architecture — extraction/ subpackage + 7 MCP tools (spec §1, §5)"
```

- [ ] **Step 32.3: Final pre-push sweep**

```bash
pytest -q 2>&1 | tail -3
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
```

- [ ] **Step 32.4: Push + mark PR ready**

```bash
git push
gh pr ready 17
```

- [ ] **Step 32.5: Post AC coverage comment on PR #17**

Post a comment with an AC coverage table mapping each of the 20 ACs in spec §15 to the tasks that implement it.

Expected test count delta: 0. Final total: ~567.

---

## Self-review

### 1. Spec coverage — 20 ACs

| AC | Criterion | Task(s) |
|---|---|---|
| #1 | Existing 5 MCP tools byte-identical | Task 23, 31 |
| #2 | `get_document_tree` MCP tool | Task 23 |
| #3 | `pydocs-mcp tree` CLI subcommand | Task 24 |
| #4 | README.md + docs/tutorial.md + scripts/build.py + notebooks/demo.ipynb → correct chunker per extension | Task 30 |
| #5 | YAML `.txt` in include_extensions → ValidationError | Task 5 |
| #6 | YAML `.yaml` in by_extension → ValidationError | Task 5 |
| #7 | Class + docstring + 3 methods → 4 chunks w/ parent_node_id | Task 10, 11 |
| #8 | `# A / ## B / ## C` with intro under A → 3 chunks | Task 12 |
| #8b | Same w/o intro → 2 chunks | Task 12 |
| #9 | 5-cell notebook → 5 chunks | Task 13 |
| #9b | Class with no methods → 1 chunk | Task 11 |
| #9c | Module with docstring → extra module chunk | Task 11 |
| #9d | Chunk count matches flattening rule | Task 10 |
| #10 | Zero-heading markdown → 1 MODULE chunk, 50KB cap | Task 12 |
| #10b | Empty `__init__.py` → 0 chunks | Task 10, 11 |
| #11 | Python file parse failure → fallback MODULE chunk | Task 11 |
| #12 | inspect vs --no-inspect same chunk count | Task 30 |
| #13 | Inspect falls back to AST on import failure | Task 16 |
| #14 | `src/lib.rs` has no split_into_chunks; cargo test green | Task 28 |
| #15 | `PRAGMA user_version == 3`; upgrade triggers rebuild | Task 7 |
| #16 | `get_document_tree` JSON matches observed tree | Task 23, 30 |
| #17 | Tree > 500 KB truncated at depth 20 + log | Task 8 |
| #18 | Chunk.extra_metadata contains required keys | Task 10 |
| #18b | packages.local_path populated for `__project__` | Task 18 |
| #18c | Re-index with no changes <200ms for 5000-chunk project | Task 26, 30 |
| #18d | Edit one method re-chunks only that node | Task 26 (deferred per-node, file-level preserved) |
| #18e | `class Foo(Bar, Baz)` → inherits_from=["Bar","Baz"] | Task 11 |
| #18f | Code example in docstring → CODE_EXAMPLE child | Task 11 |
| #18g | Markdown code fence extracted + removed from heading text | Task 12 |
| #19 | Sub-PR #4 spec amendment noted | Task 3 |
| #20 | No untracked indexer.py; no imports in production tree | Task 27, 31 |

### 2. Placeholder scan

- Task 12's HeadingMarkdownChunker body uses a "deferred-mutation" pattern; the plan explicitly allows an implementer to simplify to a two-pass recursive builder as long as the behavioral ACs (#8, #8b, #10, #18g) pass.
- Task 15's `test_dependency_discovery_filters_to_distribution_files` test stub leaves the `_StubDist` / `_Fake` helpers for the implementer to flesh out — the behavioral contract is specified.
- Task 26 is intentionally a thin placeholder: per-node delta write is optional pending benchmarks (see AC #18c / #18d). A no-op "document the decision" commit is acceptable.

No other unresolved TBD/TODO placeholders.

### 3. Type consistency

- `DocumentNode(node_id, title, kind, source_path, start_line, end_line, text, content_hash, summary="", extra_metadata={}, parent_id=None, children=())` — identical across Tasks 1, 10, 11, 12, 13, 18, 19, 21, 30.
- `ChunkExtractor.extract_from_project` return type `tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]` — Tasks 3, 18, 30.
- `IndexingService.reindex_package(package, chunks, module_members, *, trees=())` — Tasks 3, 9.
- `DocumentTreeStore.save / load / load_all_in_package / delete_package` — Tasks 7, 8, 9, 21.

Consistent throughout.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-20-sub-pr-5-extraction-strategies-and-document-tree.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh Opus 4.7 subagent per task, review between tasks, same flow as sub-PRs #2–#4.

**2. Inline Execution** — batch via `executing-plans` with checkpoints at each batch boundary.

**Which approach?**
