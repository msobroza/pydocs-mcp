"""ParallelStep.from_dict accepts the named-branches YAML shape (AC-21).

The hybrid chunk-search YAML pipelines model each retriever as a named
sub-pipeline so a downstream :class:`RRFFusionStep` can read its ranked
output from ``state.scratch[<branch>.ranked]``. The new shape is::

    type: parallel_retrieval
    branches:
      - name: bm25
        steps:
          - {name: limit, type: limit, params: {max_results: 5}}
      - name: dense
        steps:
          - {name: limit, type: limit, params: {max_results: 5}}

Each branch is decoded as a :class:`CodeRetrieverPipeline` whose ``.name``
carries the branch identifier. The legacy ``stages:`` shape (raw step list
without per-branch naming) is still accepted so existing in-memory
``ParallelStep(stages=(...))`` constructions and their ``to_dict``/``from_dict``
round-trips keep working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.pipeline import (
    CodeRetrieverPipeline,
    PerCallConnectionProvider,
)
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.parallel import ParallelStep


def _ctx(tmp_path: Path) -> BuildContext:
    """Minimal BuildContext — PreCall provider points at a never-opened path.

    None of the steps used in these tests touch the connection, so the path
    not existing is fine; we just need a non-None provider for the dataclass.
    """
    provider = PerCallConnectionProvider(cache_path=tmp_path / "unused.db")
    return BuildContext(connection_provider=provider)


def test_named_branches_yaml_parses(tmp_path: Path) -> None:
    """branches: [{name, steps}] becomes a tuple of named CodeRetrieverPipelines."""
    data = {
        "type": "parallel_retrieval",
        "branches": [
            {
                "name": "bm25",
                "steps": [
                    {"name": "limit", "type": "limit", "params": {"max_results": 5}},
                ],
            },
            {
                "name": "dense",
                "steps": [
                    {"name": "limit", "type": "limit", "params": {"max_results": 5}},
                ],
            },
        ],
    }
    step = ParallelStep.from_dict(data, _ctx(tmp_path))
    assert len(step.stages) == 2
    # Each branch should be a CodeRetrieverPipeline carrying its branch name.
    bm25, dense = step.stages
    assert isinstance(bm25, CodeRetrieverPipeline)
    assert isinstance(dense, CodeRetrieverPipeline)
    assert bm25.name == "bm25"
    assert dense.name == "dense"


def test_legacy_stages_yaml_still_parses(tmp_path: Path) -> None:
    """The pre-existing stages: [...] shape (raw step list) still works.

    ParallelStep.to_dict emits this shape, so round-trips for any
    in-code ParallelStep(stages=(...)) construction must keep loading.
    """
    data = {
        "type": "parallel_retrieval",
        "stages": [
            {"type": "limit", "max_results": 5},
        ],
    }
    step = ParallelStep.from_dict(data, _ctx(tmp_path))
    assert len(step.stages) == 1


def test_named_branches_and_stages_mutually_exclusive(tmp_path: Path) -> None:
    """Mixing both keys is a YAML schema mistake — surface it loudly."""
    data = {
        "type": "parallel_retrieval",
        "branches": [{"name": "bm25", "steps": []}],
        "stages": [{"type": "limit", "max_results": 5}],
    }
    with pytest.raises(ValueError, match="branches"):
        ParallelStep.from_dict(data, _ctx(tmp_path))
