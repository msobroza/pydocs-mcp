"""Each child_expand chat arm (#393) changes ONE thing against its baseline.

Two runner paths, one arm each, both on the same commit as their baseline:

- the vendored chat runner serves ``ask_openrouter_example_needle_chat.yaml``;
  its arm is ``ask_openrouter_example_needle_chat_child_expand.yaml``;
- the before/after verb (after step 7b) serves ``ask_openrouter_qwen3_4b.yaml``;
  its arm is ``ask_openrouter_qwen3_4b_child_expand.yaml``.

An arm serves its baseline field for field except the default docs route,
which points at the baseline's own default pipeline plus one
``child_expand`` step between the top-k cutoff and the limit, at the step
class's defaults. These pins keep each arm a single-variable change: an edit
to a baseline serving file or pipeline fails here until the arm copy follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from pydocs_mcp.retrieval.config import AppConfig, _resolve_pipeline_path

_CONFIGS = Path(__file__).parents[2] / "configs"


@dataclass(frozen=True, slots=True)
class _Arm:
    baseline: Path
    arm: Path


_ARMS = {
    "vendored-chat-runner": _Arm(
        _CONFIGS / "ask_openrouter_example_needle_chat.yaml",
        _CONFIGS / "ask_openrouter_example_needle_chat_child_expand.yaml",
    ),
    "before-after": _Arm(
        _CONFIGS / "ask_openrouter_qwen3_4b.yaml",
        _CONFIGS / "ask_openrouter_qwen3_4b_child_expand.yaml",
    ),
}
_EACH_ARM = pytest.mark.parametrize("arm", list(_ARMS.values()), ids=list(_ARMS))


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolved_routes(config_path: Path) -> list[tuple[str | None, bool, Path]]:
    """(predicate, default, resolved pipeline file) per chunk route, as the product loads them."""
    config = AppConfig.load(explicit_path=config_path)
    return [
        (
            r.predicate,
            r.default,
            _resolve_pipeline_path(r.pipeline_path, config._user_config_path()),
        )
        for r in config.pipelines["chunk"].routes
    ]


def _default_pipeline(config_path: Path) -> Path:
    (path,) = [path for _, default, path in _resolved_routes(config_path) if default]
    return path


def _steps_without_names(pipeline_path: Path) -> list[dict]:
    steps = _load_yaml(pipeline_path)["steps"]
    return [{k: v for k, v in step.items() if k != "name"} for step in steps]


@_EACH_ARM
def test_the_arm_serves_its_baseline_plus_only_a_docs_route_change(arm: _Arm) -> None:
    served, baseline = _load_yaml(arm.arm), _load_yaml(arm.baseline)

    assert {k: v for k, v in served.items() if k != "pipelines"} == {
        k: v for k, v in baseline.items() if k != "pipelines"
    }
    assert set(served["pipelines"]) == {"chunk"}  # the member search stays the baseline's


@_EACH_ARM
def test_the_arm_reroutes_only_the_default_docs_search(arm: _Arm) -> None:
    served, baseline = _resolved_routes(arm.arm), _resolved_routes(arm.baseline)

    assert [route[:2] for route in served] == [route[:2] for route in baseline]
    assert [route for route in served if not route[1]] == [r for r in baseline if not r[1]]
    assert _default_pipeline(arm.arm) != _default_pipeline(arm.baseline)


@_EACH_ARM
def test_the_arm_pipeline_is_the_baseline_s_plus_one_child_expand_step(arm: _Arm) -> None:
    baseline = _steps_without_names(_default_pipeline(arm.baseline))
    served = _steps_without_names(_default_pipeline(arm.arm))
    types = [step["type"] for step in served]

    added_at = types.index("child_expand")

    assert types[added_at - 1 : added_at + 2] == ["top_k_filter", "child_expand", "limit"]
    # params {}: top_s / per_parent / decay come from the step class, their one home.
    assert served[added_at] == {"type": "child_expand", "params": {}}
    assert served[:added_at] + served[added_at + 1 :] == baseline


class _UnusedEmbedder:
    """Assembly only checks the embedder is wired; nothing here embeds."""


class _UnusedVectorStore:
    """Satisfies VectorSearchable structurally; assembly never searches."""

    async def vector_search(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("pipeline assembly must not search")


@_EACH_ARM
def test_the_arm_pipeline_assembles_in_the_product_with_the_step_before_the_limit(
    arm: _Arm,
) -> None:
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
    from pydocs_mcp.retrieval.serialization import BuildContext
    from pydocs_mcp.retrieval.steps import ChildExpandStep, LimitStep

    context = BuildContext(
        app_config=AppConfig.load(explicit_path=arm.arm),
        embedder=_UnusedEmbedder(),  # type: ignore[arg-type]
        vector_store=_UnusedVectorStore(),  # type: ignore[arg-type]
        uow_factory=lambda: None,  # type: ignore[arg-type,return-value]
    )

    pipeline = CodeRetrieverPipeline.from_dict(_load_yaml(_default_pipeline(arm.arm)), context)

    stages = list(pipeline.stages)
    expand = next(i for i, s in enumerate(stages) if isinstance(s, ChildExpandStep))
    built = stages[expand]
    default = ChildExpandStep(uow_factory=context.uow_factory)  # type: ignore[arg-type]
    assert isinstance(stages[expand + 1], LimitStep)
    assert (built.top_s, built.per_parent, built.decay) == (
        default.top_s,
        default.per_parent,
        default.decay,
    )
