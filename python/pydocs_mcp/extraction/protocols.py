"""Extraction-layer Protocols — chunkers + file discovery.

Two Protocols define the boundary between extraction and its consumers:

- :class:`Chunker` — parses one file's content into a ``DocumentNode`` tree.
  Implementations also provide a ``from_config(cfg)`` classmethod so the
  decorator-registered selector can build instances uniformly. Python's
  ``@runtime_checkable`` can't structurally enforce classmethods (PEP 544
  method-presence only), so ``from_config`` is a CONVENTIONAL contract
  tested in ``tests/extraction/test_protocols.py``.

- :class:`FileDiscoverer` split into two siblings — :class:`ProjectFileDiscoverer`
  and :class:`DependencyFileDiscoverer` — one per target kind. No
  ``Path | str`` union discriminator; callers pick the right Protocol at
  wiring time. Keeps LSP clean: a project discoverer handed a dependency
  name can't silently misbehave.

Sub-PR #5 ships three concrete :class:`Chunker`\\s (``AstPythonChunker``,
``HeadingMarkdownChunker``, ``NotebookChunker``) and two concrete
discoverers in ``extraction/discovery.py``.

The dict-lookup selector (``chunker_registry`` in
``extraction/serialization.py``) is the registered-chunker dispatch. No
``ChunkerSelector`` Protocol — a dict keyed by extension is the typed
surface; wrapping it adds zero value (spec §5 "modules NOT created").
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydocs_mcp.extraction.document_node import DocumentNode


@runtime_checkable
class Chunker(Protocol):
    """Parses ONE file into a :class:`DocumentNode` tree.

    Never touches storage. Each node's ``.text`` holds only its *direct*
    content (spec §4.1.1) — prose between this node's start and its first
    child's start; children hold their own spans.

    CONVENTIONAL contract (not structurally enforced by ``@runtime_checkable``):
    Implementations MUST declare a ``from_config(cfg)`` classmethod that
    returns a ``Self``-typed instance given a ``ChunkingConfig``.
    The dict-registry selector calls it to build instances uniformly.
    """

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> "DocumentNode": ...


@runtime_checkable
class ProjectFileDiscoverer(Protocol):
    """Yields ``(paths, root)`` for a project directory target."""

    def discover(self, target: Path) -> tuple[list[str], Path]: ...


@runtime_checkable
class DependencyFileDiscoverer(Protocol):
    """Yields ``(paths, root)`` for an installed dependency by name."""

    def discover(self, target: str) -> tuple[list[str], Path]: ...
