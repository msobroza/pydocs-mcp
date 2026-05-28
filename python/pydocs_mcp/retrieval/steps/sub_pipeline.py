"""``sub_pipeline`` YAML decoder — returns a bare nested pipeline (no wrapper).

A previous adapter class wrapped a ``CodeRetrieverPipeline`` so it could
be slotted into a ``RouteStep`` as a nested step. Now ``CodeRetrieverPipeline``
subclasses :class:`RetrieverStep` directly, so the pipeline itself
satisfies the ``RetrieverStep`` ABC and can be used directly as a step.
The adapter class has been removed; only the YAML decoder remains so
existing ``{"type": "sub_pipeline", "pipeline": {...}}`` YAML keeps
loading.
"""

from __future__ import annotations

from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry


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
            data["pipeline"],
            context,
            _depth=_depth + 1,
        )


step_registry.register("sub_pipeline")(_SubPipelineDecoder)


__all__ = ()
