"""The child_expand chat arm (#393) changes ONE thing against the chat baseline.

The arm serves ``ask_openrouter_example_needle_chat.yaml`` field for field
and adds only a ``pipelines`` block that routes the default docs search to
``pipelines/exp_child_expand.yaml`` — the shipped default docs pipeline plus
one ``child_expand`` step between the top-k cutoff and the limit, at the step
class's own defaults. The decision and dependency routes stay the shipped
ones. These pins keep the arm a single-variable change: an edit to the
baseline serving config, or to the shipped default pipeline, fails here until
the arm copy follows.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest
import yaml

_CONFIGS = Path(__file__).parents[2] / "configs"
_BASELINE_CONFIG = _CONFIGS / "ask_openrouter_example_needle_chat.yaml"
_ARM_CONFIG = _CONFIGS / "ask_openrouter_example_needle_chat_child_expand.yaml"
_ARM_PIPELINE = _CONFIGS / "pipelines" / "exp_child_expand.yaml"
_ARM_PIPELINE_PATH = "pipelines/exp_child_expand.yaml"

pytest.importorskip("pydocs_mcp")


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _shipped(relative: str) -> dict:
    return yaml.safe_load(resources.files("pydocs_mcp").joinpath(relative).read_text("utf-8"))


def _steps_without_names(pipeline: dict) -> list[dict]:
    return [{k: v for k, v in step.items() if k != "name"} for step in pipeline["steps"]]


def test_the_arm_serves_the_chat_baseline_plus_only_a_pipelines_block() -> None:
    arm = _load_yaml(_ARM_CONFIG)
    baseline = _load_yaml(_BASELINE_CONFIG)

    assert "pipelines" not in baseline
    assert {k: v for k, v in arm.items() if k != "pipelines"} == baseline
    assert set(arm["pipelines"]) == {"chunk"}  # the member search stays the shipped one


def test_the_arm_reroutes_only_the_default_docs_search() -> None:
    shipped_routes = _shipped("defaults/default_config.yaml")["pipelines"]["chunk"]
    arm_routes = _load_yaml(_ARM_CONFIG)["pipelines"]["chunk"]

    expected = [*shipped_routes[:-1], {**shipped_routes[-1], "pipeline_path": _ARM_PIPELINE_PATH}]

    assert shipped_routes[-1]["default"] is True
    assert arm_routes == expected


def test_the_arm_pipeline_is_the_shipped_default_plus_one_child_expand_step() -> None:
    shipped = _steps_without_names(_shipped("pipelines/chunk_search_graph.yaml"))
    arm = _steps_without_names(_load_yaml(_ARM_PIPELINE))
    types = [step["type"] for step in arm]

    added_at = types.index("child_expand")

    assert types[added_at - 1 : added_at + 2] == ["top_k_filter", "child_expand", "limit"]
    # params {}: top_s / per_parent / decay come from the step class, their one home.
    assert arm[added_at] == {"type": "child_expand", "params": {}}
    assert arm[:added_at] + arm[added_at + 1 :] == shipped


def test_the_arm_routes_resolve_to_the_arm_pipeline_and_the_shipped_presets() -> None:
    from pydocs_mcp.retrieval.config import AppConfig, _resolve_pipeline_path

    config = AppConfig.load(explicit_path=_ARM_CONFIG)
    shipped_dir = Path(str(resources.files("pydocs_mcp.pipelines"))).resolve()

    resolved = [
        _resolve_pipeline_path(route.pipeline_path, config._user_config_path())
        for route in config.pipelines["chunk"].routes
    ]

    # The overlay's route list REPLACES the shipped one (no merge, no duplicate default).
    assert resolved == [
        shipped_dir / "decision_search.yaml",
        shipped_dir / "chunk_search_deps.yaml",
        _ARM_PIPELINE.resolve(),
    ]


class _UnusedEmbedder:
    """Assembly only checks the embedder is wired; nothing here embeds."""


class _UnusedVectorStore:
    """Satisfies VectorSearchable structurally; assembly never searches."""

    async def vector_search(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("pipeline assembly must not search")


def test_the_arm_pipeline_assembles_in_the_product_with_the_step_before_the_limit() -> None:
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
    from pydocs_mcp.retrieval.serialization import BuildContext
    from pydocs_mcp.retrieval.steps import ChildExpandStep, LimitStep

    context = BuildContext(
        app_config=AppConfig.load(explicit_path=_ARM_CONFIG),
        embedder=_UnusedEmbedder(),  # type: ignore[arg-type]
        vector_store=_UnusedVectorStore(),  # type: ignore[arg-type]
        uow_factory=lambda: None,  # type: ignore[arg-type,return-value]
    )

    pipeline = CodeRetrieverPipeline.from_dict(_load_yaml(_ARM_PIPELINE), context)

    stages = list(pipeline.stages)
    expand = next(i for i, s in enumerate(stages) if isinstance(s, ChildExpandStep))
    assert isinstance(stages[expand + 1], LimitStep)
    assert (stages[expand].top_s, stages[expand].per_parent, stages[expand].decay) == (3, 5, 0.9)
