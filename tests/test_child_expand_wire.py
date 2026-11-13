"""child_expand through the frozen tool on a REAL index (#393).

The rendered half of the byte-identity contract (the shipped-defaults half is
``tests/retrieval/steps/test_child_expand_overlay_only.py``). Served through
the same routers as the MCP surface, the arm's docs pipeline (the shipped
default plus one ``child_expand`` step) answers a dense search exactly as the
shipped default does — text, items and meta — because a dense fetch fills
the window (or lists the whole corpus, children included). Over a lexical
route, where a class-name query matches few rows, the arm appends the class's
children below the original rows and leaves ``meta.truncated`` as it was.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import pytest
import yaml

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.tool_response import SearchEnvelope
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db

_FILLER_FUNCTIONS = 20
# Far above what this corpus renders, so only the search itself shapes the text.
_BUDGET_NO_CUT_TOKENS = 100_000
_CHILD_STEP = {"name": "children", "type": "child_expand", "params": {}}
_METHODS = ("add_item", "remove_item", "count_items")


def _write_project(root: Path) -> Path:
    """A class whose methods never repeat its name, plus enough filler to fill a window."""
    project = root / "invproj"
    pkg = project / "invpkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "invproj"\nversion = "0.0.0"\n')
    (pkg / "__init__.py").write_text("")
    methods = "".join(
        f'\n    def {m}(self, sku: str) -> int:\n        """Step {i} on a stock-keeping unit."""\n'
        f"        return {i}\n"
        for i, m in enumerate(_METHODS)
    )
    (pkg / "inventory.py").write_text(f'"""Stock records."""\n\n\nclass Inventory:\n{methods}')
    filler = "".join(
        f'\n\ndef helper_{i:02d}() -> int:\n    """Helper {i:02d}."""\n    return {i}\n'
        for i in range(_FILLER_FUNCTIONS)
    )
    (pkg / "helpers.py").write_text(f'"""Helpers."""\n{filler}')
    return project


def _with_child_step(shipped_pipeline: str) -> dict:
    """The shipped pipeline YAML plus the child_expand step right before its limit."""
    text = resources.files("pydocs_mcp.pipelines").joinpath(shipped_pipeline).read_text("utf-8")
    pipeline = yaml.safe_load(text)
    at = [step["type"] for step in pipeline["steps"]].index("limit")
    pipeline["steps"].insert(at, _CHILD_STEP)
    return pipeline


def _overlay(cfg_dir: Path, name: str, default_pipeline: str) -> Path:
    """A serving config whose default docs route is ``default_pipeline``; the
    shipped decision / dependency routes are restated unchanged."""
    shipped = yaml.safe_load(
        resources.files("pydocs_mcp").joinpath("defaults/default_config.yaml").read_text("utf-8")
    )
    routes = shipped["pipelines"]["chunk"]
    routes[-1] = {**routes[-1], "pipeline_path": default_pipeline}
    path = cfg_dir / f"{name}.yaml"
    path.write_text(yaml.safe_dump({"pipelines": {"chunk": routes}}))
    return path


@dataclass(frozen=True, slots=True)
class _Routers:
    shipped: ToolRouter
    dense_arm: ToolRouter
    lexical: ToolRouter
    lexical_arm: ToolRouter


def _router(config_path: Path, db_path: Path) -> ToolRouter:
    config = AppConfig.load(explicit_path=config_path)
    config.search.output.budget_tokens = _BUDGET_NO_CUT_TOKENS
    router, _services = build_routers(config, db_path=db_path, surface="mcp")
    return router


@pytest.fixture(scope="module")
def routers(tmp_path_factory: pytest.TempPathFactory) -> _Routers:
    root = tmp_path_factory.mktemp("child_expand_wire")
    cfg = root / "cfg"
    (cfg / "pipelines").mkdir(parents=True)
    for name, shipped in (
        ("arm_dense", "chunk_search_graph.yaml"),
        ("arm_bm25", "chunk_search.yaml"),
    ):
        (cfg / "pipelines" / f"{name}.yaml").write_text(yaml.safe_dump(_with_child_step(shipped)))
    # An empty user layer: the shipped defaults, never an ambient user config.
    (cfg / "shipped.yaml").write_text("{}\n")
    db = index_project_to_db(_write_project(root), root / "inv.db")
    return _Routers(
        shipped=_router(cfg / "shipped.yaml", db),
        dense_arm=_router(_overlay(cfg, "dense_arm", "pipelines/arm_dense.yaml"), db),
        lexical=_router(_overlay(cfg, "lexical", "pipelines/chunk_search.yaml"), db),
        lexical_arm=_router(_overlay(cfg, "lexical_arm", "pipelines/arm_bm25.yaml"), db),
    )


def _envelope(router: ToolRouter, query: str, **fields: object) -> SearchEnvelope:
    response = asyncio.run(router.search_codebase(SearchInput(query=query, **fields)))
    return SearchEnvelope.model_validate(response.structured())


@pytest.mark.parametrize("limit", [None, 1000], ids=["default-limit", "whole-corpus"])
def test_a_dense_search_renders_byte_identical_under_the_arm(
    routers: _Routers, limit: int | None
) -> None:
    fields = {"kind": "docs"} if limit is None else {"kind": "docs", "limit": limit}

    shipped = _envelope(routers.shipped, "stock keeping inventory", **fields)
    arm = _envelope(routers.dense_arm, "stock keeping inventory", **fields)

    assert arm == shipped


def test_a_lexical_class_hit_gains_its_children_below_the_untouched_originals(
    routers: _Routers,
) -> None:
    base = _envelope(routers.lexical, "Inventory", kind="docs")
    arm = _envelope(routers.lexical_arm, "Inventory", kind="docs")
    (cls,) = [i.qualified_name for i in base.items if i.qualified_name.endswith(".Inventory")]

    assert arm.items[: len(base.items)] == base.items
    assert [i.qualified_name for i in arm.items[len(base.items) :]] == [
        f"{cls}.{m}" for m in _METHODS
    ]
    assert arm.meta.truncated is base.meta.truncated
