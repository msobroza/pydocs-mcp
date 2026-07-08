"""Load-time validation of route predicate names in ``pipelines.chunk`` / ``.member``.

A typo'd ``predicate:`` entry in a user overlay (e.g. ``scope_is_deps_only``
instead of the registered ``scope_is_dependencies_only``) currently passes
``AppConfig`` validation and pipeline assembly cleanly — ``_build_handler_pipeline``
never checks the name against ``context.predicate_registry`` at build time. The
typo only explodes later, per-query, when ``RouteStep.run`` calls
``registry.get(case.predicate_name)`` and raises ``KeyError`` (see
``pydocs_mcp.retrieval.route_predicates.PredicateRegistry.get`` and
``tests/retrieval/test_route_predicates.py::test_unknown_raises_with_known_list``).

This asserts the config -> assembly seam: building the pipeline from a config
with an unregistered predicate name must fail immediately (at server-start /
index-load time), not on the first live search.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig, build_chunk_pipeline_from_config
from pydocs_mcp.retrieval.serialization import BuildContext


def _write_overlay(tmp_path: Path, *, predicate_name: str) -> Path:
    """A minimal user overlay routing on a (possibly typo'd) predicate name.

    ``pipeline_path`` targets a tiny local preset built from the ``limit``
    step only — unlike the shipped chunk-search presets, ``limit`` needs no
    ``connection_provider`` / ``vector_store`` wiring at build time. That
    isolates the failure this test targets to the predicate-name check
    itself, not an unrelated "BuildContext under-wired" error.
    """
    (tmp_path / "pipelines").mkdir(exist_ok=True)
    preset_path = tmp_path / "pipelines" / "trivial.yaml"
    preset_path.write_text("name: trivial\nsteps:\n  - name: cap\n    type: limit\n")

    config_path = tmp_path / "pydocs-mcp.yaml"
    config_path.write_text(
        "pipelines:\n"
        "  chunk:\n"
        f"    - predicate: {predicate_name}\n"
        "      pipeline_path: pipelines/trivial.yaml\n"
        "    - default: true\n"
        "      pipeline_path: pipelines/trivial.yaml\n"
    )
    return config_path


def test_build_chunk_pipeline_rejects_unregistered_predicate_name(tmp_path: Path) -> None:
    """The exact gap scenario: a typo'd predicate name in a user overlay.

    Must raise at pipeline-assembly time (config load / server start), naming
    the bad predicate — never silently build a ``RouteStep`` that only
    explodes on the first live ``search_codebase`` call.
    """
    config_path = _write_overlay(tmp_path, predicate_name="scope_is_deps_only")
    config = AppConfig.load(explicit_path=config_path)
    context = BuildContext(app_config=config)

    with pytest.raises((KeyError, ValueError)) as exc_info:
        build_chunk_pipeline_from_config(config, context)

    message = str(exc_info.value)
    assert "scope_is_deps_only" in message
    # Registry-level errors enumerate known names (see PredicateRegistry.get) —
    # assembly-time validation should carry the same actionable detail.
    assert "scope_is_dependencies_only" in message


def test_build_chunk_pipeline_accepts_registered_predicate_name(tmp_path: Path) -> None:
    """Control case: the correctly-spelled predicate builds without error."""
    config_path = _write_overlay(tmp_path, predicate_name="scope_is_dependencies_only")
    config = AppConfig.load(explicit_path=config_path)
    context = BuildContext(app_config=config)

    pipeline = build_chunk_pipeline_from_config(config, context)

    assert pipeline is not None
