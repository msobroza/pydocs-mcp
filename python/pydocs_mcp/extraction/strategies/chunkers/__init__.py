"""Concrete :class:`~pydocs_mcp.extraction.protocols.Chunker` strategies —
one file per chunker.

Ships four chunkers, each registered at import time so
:data:`~pydocs_mcp.extraction.serialization.chunker_registry` is populated
for :class:`~pydocs_mcp.extraction.pipeline.stages.ChunkingStage` (spec §7.5):

- :mod:`.ast_python` — :class:`AstPythonChunker` (``.py``)
- :mod:`.heading_markdown` — :class:`HeadingMarkdownChunker` (``.md``)
- :mod:`.notebook` — :class:`NotebookChunker` (``.ipynb``)
- :mod:`.text_section` — :class:`TextSectionChunker` (ADR 0021 T2: the
  text/config set ``.rst .txt .toml .yaml .yml .cfg .ini .json``)
- :mod:`.multilang_treesitter` — :class:`MultilangChunker` (ADR 0021 T3: the
  code set ``.js .ts .tsx .c .h .rs`` behind the ``[multilang]`` extra, with an
  internal text-window fallback when the extra is absent)

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
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    MultilangChunker,
)
from pydocs_mcp.extraction.strategies.chunkers.notebook import NotebookChunker
from pydocs_mcp.extraction.strategies.chunkers.text_section import TextSectionChunker

__all__ = (
    "AstPythonChunker",
    "Chunker",
    "HeadingMarkdownChunker",
    "MultilangChunker",
    "NotebookChunker",
    "TextSectionChunker",
    "_module_from_path",
    "_python_package_root",
)
