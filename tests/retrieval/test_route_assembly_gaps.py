"""Regression tests for silent no-op routing + untested multiple-default error.

Three edge cases in ``_build_handler_pipeline`` / ``RouteStep`` (spec §5.9):

1. An empty ``pipelines.chunk: []`` overlay validates cleanly (``HandlerConfig.routes``
   has no min-length) and assembles a ``RouteStep(routes=(), default=None)``. Running
   that pipeline returns the input state unchanged — every search against that handler
   silently yields zero results with no error, indistinguishable from an empty index.
2. Predicate-only routes with no ``default: true`` entry: a query that matches none of
   the predicates falls through ``RouteStep.run`` and returns the state unchanged —
   same silent-empty-result failure mode, this time reachable even with a non-empty
   route list.
3. Two ``default: true`` entries in the same handler: ``_build_handler_pipeline`` raises
   ``ValueError`` at pipeline_assembly.py — this branch had zero test coverage before
   this file (confirmed by grep: only the source line matched, no test hit).

Fix: ``_build_handler_pipeline`` now raises ``ValueError`` at assembly time (config
load / server start) when a handler's route list is empty, or when it has no
``default: true`` entry — both cases that previously produced a silently-empty
``RouteStep``. This matches the existing fail-fast precedent for unregistered
predicate names in ``test_pipeline_assembly_validation.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig, build_chunk_pipeline_from_config
from pydocs_mcp.retrieval.serialization import BuildContext


def _write_trivial_preset(tmp_path: Path) -> None:
    (tmp_path / "pipelines").mkdir(exist_ok=True)
    (tmp_path / "pipelines" / "trivial.yaml").write_text(
        "name: trivial\nsteps:\n  - name: cap\n    type: limit\n"
    )


def _write_config(tmp_path: Path, routes_yaml: str) -> Path:
    _write_trivial_preset(tmp_path)
    config_path = tmp_path / "pydocs-mcp.yaml"
    config_path.write_text(f"pipelines:\n  chunk:\n{routes_yaml}")
    return config_path


def test_empty_route_list_raises_at_assembly_time(tmp_path: Path) -> None:
    """``pipelines: {chunk: []}`` must fail fast at build time, not silently
    build a ``RouteStep(routes=(), default=None)`` that no-ops on every query
    (gap case 1)."""
    config_path = tmp_path / "pydocs-mcp.yaml"
    config_path.write_text("pipelines:\n  chunk: []\n")
    config = AppConfig.load(explicit_path=config_path)
    context = BuildContext(app_config=config)

    with pytest.raises(ValueError, match="no routes"):
        build_chunk_pipeline_from_config(config, context)


def test_predicate_only_routes_without_default_raise_at_assembly_time(
    tmp_path: Path,
) -> None:
    """A handler with only predicate routes and no ``default: true`` entry
    must fail fast at build time — otherwise a query matching none of the
    predicates silently falls through ``RouteStep.run`` to a no-op (gap case 2)."""
    config_path = _write_config(
        tmp_path,
        "    - predicate: scope_is_dependencies_only\n      pipeline_path: pipelines/trivial.yaml\n",
    )
    config = AppConfig.load(explicit_path=config_path)
    context = BuildContext(app_config=config)

    with pytest.raises(ValueError, match="no default route"):
        build_chunk_pipeline_from_config(config, context)


def test_multiple_default_routes_raise_at_assembly_time(tmp_path: Path) -> None:
    """Two ``default: true`` entries in the same handler must raise
    ``ValueError`` naming the handler (gap case 3 — this branch previously had
    zero test coverage: only the source line matched a repo-wide grep)."""
    config_path = _write_config(
        tmp_path,
        "    - default: true\n      pipeline_path: pipelines/trivial.yaml\n"
        "    - default: true\n      pipeline_path: pipelines/trivial.yaml\n",
    )
    config = AppConfig.load(explicit_path=config_path)
    context = BuildContext(app_config=config)

    with pytest.raises(ValueError, match="multiple default routes declared"):
        build_chunk_pipeline_from_config(config, context)


def test_single_default_route_still_collapses_to_inner_pipeline(tmp_path: Path) -> None:
    """Control case: a single ``default: true`` entry with no predicates is
    the documented collapse-to-inner-pipeline path and must keep working."""
    config_path = _write_config(
        tmp_path,
        "    - default: true\n      pipeline_path: pipelines/trivial.yaml\n",
    )
    config = AppConfig.load(explicit_path=config_path)
    context = BuildContext(app_config=config)

    pipeline = build_chunk_pipeline_from_config(config, context)

    assert pipeline.name == "trivial"


def test_predicate_and_default_route_still_builds(tmp_path: Path) -> None:
    """Control case: a predicate route plus a default route is the normal,
    well-formed shape and must keep building a ``RouteStep``."""
    config_path = _write_config(
        tmp_path,
        "    - predicate: scope_is_dependencies_only\n      pipeline_path: pipelines/trivial.yaml\n"
        "    - default: true\n      pipeline_path: pipelines/trivial.yaml\n",
    )
    config = AppConfig.load(explicit_path=config_path)
    context = BuildContext(app_config=config)

    pipeline = build_chunk_pipeline_from_config(config, context)

    assert pipeline.name == "chunk_from_config"
