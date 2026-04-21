"""Decorator-registered stage + chunker registries (spec §7.5).

Reuses :class:`~pydocs_mcp.retrieval.serialization.ComponentRegistry` — same
decorator pattern (``@stage_registry.register("name")``) already used by the
retrieval pipeline. The ingestion ``stage_registry`` here is a SEPARATE
instance from ``retrieval.stage_registry``; :func:`build_ingestion_pipeline`
constructs a :class:`BuildContext` that points at the ingestion registry
when building an :class:`~pydocs_mcp.extraction.pipeline.IngestionPipeline`.

:data:`chunker_registry` is a plain ``dict`` keyed by file extension, not a
:class:`ComponentRegistry`: extension→class dispatch has no YAML dict to
decode and no type-name string to look up, so the :class:`ComponentRegistry`
machinery (``from_dict`` forwarding, depth tracking) would be dead weight.
Keys must include the leading dot (``".py"``, not ``"py"``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydocs_mcp.retrieval.serialization import ComponentRegistry

if TYPE_CHECKING:
    from pydocs_mcp.extraction.protocols import Chunker, IngestionStage


stage_registry: "ComponentRegistry[IngestionStage]" = ComponentRegistry()
"""Decorator-populated registry for ``@stage_registry.register('type_name')``.

Populated by side-effect import of ``extraction.stages`` (Task 11) — the
six concrete :class:`IngestionStage` classes each carry the decorator at
module scope so importing the module registers them.
"""


chunker_registry: dict[str, type["Chunker"]] = {}
"""Extension → Chunker class. Populated by :func:`_register_chunker` decorator.

Keys are lowercased file extensions with a leading dot (``".py"``, ``".md"``,
``".ipynb"``). Values are the :class:`Chunker` class itself, not an
instance — the :class:`~pydocs_mcp.extraction.stages.ChunkingStage`
calls ``cls.from_config(cfg)`` to construct a cached instance per
extension.
"""


def _register_chunker(ext: str):
    """Decorator: registers a :class:`Chunker` class by file extension.

    Usage::

        @_register_chunker('.py')
        @dataclass(frozen=True, slots=True)
        class AstPythonChunker:
            ...

    The decorator returns the class unchanged so stacking it below a
    ``@dataclass`` decorator works as expected. Duplicate registration
    raises :class:`ValueError` — extension conflicts are a wiring bug we
    want to surface at import time, not at first extraction.
    """
    def deco(cls: type["Chunker"]) -> type["Chunker"]:
        if ext in chunker_registry:
            raise ValueError(f"chunker for {ext!r} already registered")
        chunker_registry[ext] = cls
        return cls
    return deco
