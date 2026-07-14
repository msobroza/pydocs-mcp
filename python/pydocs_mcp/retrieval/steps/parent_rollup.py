"""ParentRollupStep — collapse sibling results into their parent.

A rerank-only step: when enough children of one ``DocumentNode`` parent
are co-retrieved (>= ``_MIN_SIBLINGS`` sibling hits AND kind-resolved
coverage of the parent's chunk-emitting children), the siblings are
replaced by the parent's own indexed chunk at the group's best rank.
Replaces candidates only — adds nothing on failure paths and falls
through to the unchanged input on every data-shaped failure condition
(missing tree, missing parent chunk row, gates unmet, malformed
metadata). Reads ``document_trees`` via ``uow.trees`` and ``chunks`` via
``uow.chunks`` in one read-only UoW per call. Spec:
docs/superpowers/specs/2026-07-14-parent-rollup-retriever-step-design.md.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

from pydocs_mcp.extraction.model.document_node import NodeKind
from pydocs_mcp.models import ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    step_registry,
    step_to_yaml_dict,
    yaml_kwargs,
)
from pydocs_mcp.storage.protocols import UnitOfWork

# WHY: per-kind coverage thresholds — see the spec's §3.6 table. Class
# rollup is eager (top-K caps the numerator hard for classes); a whole-
# module rollup swallows the most granularity, so it demands the
# strongest evidence; doc headings sit in between.
_DEFAULT_MIN_COVERAGE = 0.5
_DEFAULT_MIN_COVERAGE_BY_KIND: Mapping[str, float] = MappingProxyType(
    {"class": 0.3, "module": 0.6, "markdown_heading": 0.5}
)
# WHY: structural floor, not a tunable — collapsing a single retrieved
# child is pure information loss (same list length, less specific
# result), so no deployment wants 1. Not a dataclass field, never
# serialized, absent from _YAML_KEYS.
_MIN_SIBLINGS = 2
_DEFAULT_NAME = "parent_rollup"
_QNAME_KEY = "qualified_name"
_PACKAGE_KEY = "package"
_MODULE_KEY = "module"
_VALID_KIND_KEYS = frozenset(k.value for k in NodeKind)


def _validated_coverage_mapping(raw: object) -> dict[str, float]:
    """Validate a YAML-parsed ``min_coverage_by_kind`` value pre-construction."""
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"ParentRollupStep.min_coverage_by_kind must be a mapping of "
            f"NodeKind value -> float; got {raw!r}."
        )
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in _VALID_KIND_KEYS:
            raise ValueError(
                f"ParentRollupStep.min_coverage_by_kind key {key!r} is not a "
                f"NodeKind value; valid keys: {sorted(_VALID_KIND_KEYS)}."
            )
        # bool is an int subclass, but `class: true` is a YAML typo, not a
        # threshold. 0.0 is allowed: explicit per-kind opt-in to maximum
        # eagerness (the sibling floor still gates).
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= value <= 1.0:
            raise ValueError(
                f"ParentRollupStep.min_coverage_by_kind[{key!r}] must be a "
                f"float in [0.0, 1.0]; got {value!r}."
            )
        out[key] = float(value)
    return out


@step_registry.register("parent_rollup")
@dataclass(frozen=True, slots=True)
class ParentRollupStep(RetrieverStep):
    """Collapse co-retrieved sibling chunks into their parent's chunk."""

    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    min_coverage: float = field(default=_DEFAULT_MIN_COVERAGE, kw_only=True)
    min_coverage_by_kind: Mapping[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_MIN_COVERAGE_BY_KIND),
        kw_only=True,
    )
    name: str = field(default=_DEFAULT_NAME, kw_only=True)
    _YAML_KEYS: ClassVar[tuple[str, ...]] = ("min_coverage", "min_coverage_by_kind", "name")

    def __post_init__(self) -> None:
        # Read-only normalization — the Chunk.metadata precedent
        # (models.py __post_init__): frozen+slots forbids assignment,
        # not object.__setattr__; dataclasses.replace re-runs this and
        # harmlessly re-wraps.
        object.__setattr__(
            self,
            "min_coverage_by_kind",
            MappingProxyType(dict(self.min_coverage_by_kind)),
        )

    async def run(self, state: RetrieverState) -> RetrieverState:
        candidates = state.candidates
        if not isinstance(candidates, ChunkList) or not candidates.items:
            return state
        # Phases 1-6 land in the core-algorithm task; guard-only until then.
        return state

    def to_dict(self) -> dict:
        return step_to_yaml_dict(self, type_name="parent_rollup", keys=self._YAML_KEYS)

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> ParentRollupStep:
        if context.uow_factory is None:
            raise ValueError(
                "ParentRollupStep requires BuildContext.uow_factory. "
                "Production wiring in __main__.py / server.py sets this.",
            )
        kwargs = yaml_kwargs(data, cls, cls._YAML_KEYS)
        if not 0.0 < kwargs["min_coverage"] <= 1.0:
            raise ValueError(
                f"ParentRollupStep.min_coverage must be in (0.0, 1.0]; "
                f"got {kwargs['min_coverage']!r}.",
            )
        kwargs["min_coverage_by_kind"] = _validated_coverage_mapping(kwargs["min_coverage_by_kind"])
        return cls(uow_factory=context.uow_factory, **kwargs)


__all__ = ("ParentRollupStep",)
