"""Extraction-layer Protocols — chunkers + file discovery.

Three Protocols define the boundary between extraction and its consumers:

- :class:`Chunker` — parses one file's content into a ``DocumentNode`` tree.
  Uniform ``from_config(cfg)`` classmethod enables decorator-registered
  discovery without per-chunker construction branches.

- :class:`FileDiscoverer` — yields in-scope file paths for either project
  or dependency context. Returns ``(paths, root)`` where ``root`` is the
  site-packages root (for dependency discovery) or project dir (for project).

- :class:`ChunkerSelector` — optional Protocol for extension→chunker
  lookup. The primary selector is the ``chunker_registry`` dict from
  ``extraction/serialization.py``; this Protocol exists for implementers
  that want typed signatures.

Sub-PR #5 ships three concrete ``Chunker``\\s (`AstPythonChunker`,
`HeadingMarkdownChunker`, `NotebookChunker`) and two ``FileDiscoverer``\\s
(`ProjectFileDiscoverer`, `DependencyFileDiscoverer`).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # ChunkingConfig is referenced in the Chunker.from_config(cfg) contract
    # documented in the docstring; imported here so downstream tooling can
    # resolve the forward reference without a runtime dependency on
    # extraction.config (which arrives in Task 21).
    from pydocs_mcp.extraction.config import ChunkingConfig  # noqa: F401
    from pydocs_mcp.extraction.document_node import DocumentNode


@runtime_checkable
class Chunker(Protocol):
    """Parses ONE file into a :class:`DocumentNode` tree.

    Never touches storage. Each node's ``.text`` holds only its *direct*
    content (spec §4.1.1) — prose between this node's start and its first
    child's start; children hold their own spans.

    Implementations MUST also declare a ``from_config(cfg)`` classmethod
    that returns ``Self`` given a :class:`ChunkingConfig`. This enables
    the dict-registry selector to build instances uniformly.
    """

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> "DocumentNode": ...


@runtime_checkable
class FileDiscoverer(Protocol):
    """Yields (paths, root) for project or dependency context.

    ``target`` is either a project directory :class:`Path` or a dependency
    name :class:`str`. Implementations decide which context they handle.
    """

    def discover(self, target: Path | str) -> tuple[list[str], Path]: ...


@runtime_checkable
class ChunkerSelector(Protocol):
    """Extension → Chunker lookup.

    The primary selector is the ``chunker_registry`` dict (see
    ``extraction/serialization.py``). This Protocol is optional —
    consumers that want typed signatures use it; consumers that work
    directly with the registry dict don't need it.
    """

    def pick(self, path: str) -> Chunker: ...
