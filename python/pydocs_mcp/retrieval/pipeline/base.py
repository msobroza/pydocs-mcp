"""RetrieverStep ABC + RetrieverPipeline class.

The retrieval-pipeline contract. Every step in a retrieval pipeline
subclasses ``RetrieverStep`` and implements ``async def run(state)``.
A ``RetrieverPipeline`` is itself a ``RetrieverStep`` — they compose
recursively.

Naming: ``RetrieverStep`` (not ``Stage``) differentiates this contract
from the extraction-side ``IngestionStage`` Protocol at
``pydocs_mcp/extraction/pipeline/ingestion.py``. Different pipelines,
different state shapes, different contracts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydocs_mcp.retrieval.pipeline.state import RetrieverState


@dataclass(frozen=True, slots=True)
class RetrieverStep(ABC):
    """A single retrieval-pipeline step. Pure: take a state, return a NEW state.

    Subclasses set ``name: str`` (used for addressing + debug logs) and
    implement ``async def run(self, state) -> state``.

    Subclasses MUST also declare ``@dataclass(frozen=True, slots=True)``
    — without ``slots=True`` on the subclass, ``__dict__`` reappears and
    the slots discipline is lost (instances become 5-10x larger and
    accept arbitrary attribute assignment). The frozen contract still
    propagates from the parent but slots does not.
    """

    name: str

    @abstractmethod
    async def run(self, state: RetrieverState) -> RetrieverState: ...

    def to_dict(self) -> dict:
        """Serialize the step to a YAML-loadable dict.

        Default raises ``NotImplementedError`` so subclasses opt in
        explicitly; ``@abstractmethod`` would force every nested
        ``@dataclass`` subclass (including ``RetrieverPipeline``) to
        re-declare the method even when its serialization is owned by
        a higher-level wrapper. Every concrete shipped step under
        ``retrieval/steps/`` overrides this; the declaration here
        codifies the contract that ``ParallelStep`` / ``RouteStep`` /
        ``ConditionalStep`` / ``TokenBudgetStep`` already rely on when
        they call ``step.to_dict()`` on nested children.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement to_dict() — see "
            f"existing concrete steps under retrieval/steps/ for the "
            f"shape ({{'type': '<name>', ...}})."
        )


@dataclass(frozen=True, slots=True)
class RetrieverPipeline(RetrieverStep):
    """An ordered tuple of named ``RetrieverStep``s. A Pipeline IS a Step.

    Construction (sklearn-shaped):

        chunk_pipeline = RetrieverPipeline(
            name="chunk_search",
            steps=(
                ("fetch", ChunkFetcherStep(provider, filter_adapter=adapter, name="fetch", limit=200)),
                ("score", BM25ScorerStep(name="score")),
                ("topk", TopKFilterStep(name="topk", k=50)),
                ("budget", TokenBudgetStep(formatter, 2000, name="budget")),
            ),
        )

    Addressing:

        chunk_pipeline["fetch"]  # -> ChunkFetcherStep
        chunk_pipeline.step_names  # -> ("fetch", "score", "topk", "budget")
    """

    steps: tuple[tuple[str, RetrieverStep], ...]

    def __post_init__(self) -> None:
        names = [n for n, _ in self.steps]
        # WHY: validate "shape exists" before "shape is well-formed".
        # An empty steps tuple has no duplicates trivially, so the
        # duplicate check would silently miss the "no steps" case.
        if not names:
            raise ValueError(f"pipeline {self.name!r} has no steps")
        if len(names) != len(set(names)):
            raise ValueError(
                f"duplicate step names in {self.name!r}: {names}",
            )

    def __getitem__(self, name: str) -> RetrieverStep:
        for n, step in self.steps:
            if n == name:
                return step
        raise KeyError(f"pipeline {self.name!r} has no step {name!r}")

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(n for n, _ in self.steps)

    async def run(self, state: RetrieverState) -> RetrieverState:
        for _, step in self.steps:
            state = await step.run(state)
        return state
