"""``sub_pipeline`` YAML decoder — returns a bare nested pipeline (no wrapper).

A previous adapter class wrapped a ``CodeRetrieverPipeline`` so it could
be slotted into a ``RouteStep`` as a ``PipelineStage``. Now
``CodeRetrieverPipeline.run`` is polymorphic (accepts ``PipelineState`` as
well as ``SearchQuery``), so the pipeline itself satisfies the
``PipelineStage`` Protocol and can be used directly as a stage. The
adapter class has been removed; only the YAML decoder remains so existing
``{"type": "sub_pipeline", "pipeline": {...}}`` YAML keeps loading until
Task 8 flips the schema to bare nested pipelines.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.pipeline_legacy import CodeRetrieverPipeline
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry


class _SubPipelineDecoder:
    """Decoder shim — its ``from_dict`` returns a bare ``CodeRetrieverPipeline``.

    Registered under the ``sub_pipeline`` type key. The registry's
    ``build`` method calls ``cls.from_dict(data, context, _depth=_depth)``,
    so we expose a class-method-shaped entry point that forwards the
    depth counter to the inner pipeline decoder for the recursion guard.
    """

    @classmethod
    def from_dict(
        cls,
        data: dict,
        context: BuildContext,
        _depth: int = 0,
    ) -> CodeRetrieverPipeline:
        return CodeRetrieverPipeline.from_dict(
            data["pipeline"], context, _depth=_depth + 1,
        )


stage_registry.register("sub_pipeline")(_SubPipelineDecoder)


__all__ = ()
