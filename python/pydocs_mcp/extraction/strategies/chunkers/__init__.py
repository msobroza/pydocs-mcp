"""Concrete :class:`~pydocs_mcp.extraction.protocols.Chunker` strategies —
one file per chunker.

Ships three chunkers, each registered at import time so
:data:`~pydocs_mcp.extraction.serialization.chunker_registry` is populated
for :class:`~pydocs_mcp.extraction.pipeline.stages.ChunkingStage` (spec §7.5):

- :mod:`.ast_python` — :class:`AstPythonChunker` (``.py``)
- :mod:`.heading_markdown` — :class:`HeadingMarkdownChunker` (``.md``)
- :mod:`.notebook` — :class:`NotebookChunker` (``.ipynb``)

Direct-text rule (spec §4.1.1): each node's ``.text`` contains ONLY
prose between this node's start and its first child's start. MODULE
carries the module docstring; CLASS carries the span up to the first
method's line; FUNCTION / METHOD carry the full def span (since their
code-example children live in docstrings, not in the source line range).

Re-exports the underscore-prefixed helpers
(:func:`_module_from_path`, :func:`_python_package_root`) that external
callers depend on — keeps stable imports across the split.
"""

from __future__ import annotations

from pydocs_mcp.extraction.strategies.chunkers.ast_python import (
    AstPythonChunker,
    _module_from_path,
    _python_package_root,
)
from pydocs_mcp.extraction.strategies.chunkers.base_chunker import Chunker
from pydocs_mcp.extraction.strategies.chunkers.heading_markdown import (
    HeadingMarkdownChunker,
)
from pydocs_mcp.extraction.strategies.chunkers.notebook import NotebookChunker

__all__ = (
    "AstPythonChunker",
    "Chunker",
    "HeadingMarkdownChunker",
    "NotebookChunker",
    "_module_from_path",
    "_python_package_root",
)
