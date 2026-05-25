"""Pin the two DS-1000 AppConfig overlays to the pipeline blueprint each
selects.

The runner loads every ``--configs`` path via
``AppConfig.load(explicit_path=cfg)`` and reads the resolved chunk handler
to build the search pipeline. These overlays are one-key files that flip
the ``chunk`` handler's default route to a specific blueprint:

  - ``ds1000_ranked.yaml``    -> ``pipelines/chunk_search_ranked.yaml``
    (top-K separate ranked chunks; used by the pydocs-only + oracle runs
    whose recall@k / ndcg@k / mrr metrics need K distinct items).
  - ``ds1000_composite.yaml`` -> ``pipelines/chunk_search.yaml``
    (token-budgeted 1-item composite; used by the cross-system comparison
    run so pydocs emits one blob like Context7/Neuledge).

WHY assert the raw ``pipeline_path`` (not a built pipeline): the route
entry's ``pipeline_path`` is the load-time source of truth the runner's
``AppConfig.load`` exposes verbatim — asserting it is exact and avoids
coupling the test to pipeline-assembly internals.
"""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
_RANKED = _CONFIGS_DIR / "ds1000_ranked.yaml"
_COMPOSITE = _CONFIGS_DIR / "ds1000_composite.yaml"


def _chunk_default_pipeline_path(config: AppConfig) -> Path:
    """Return the ``pipeline_path`` of the ``chunk`` handler's default route.

    The overlays are single-default-route files, so exactly one entry
    carries ``default=True``; this asserts that shape and returns its path.
    """
    routes = config.pipelines["chunk"].routes
    defaults = [r for r in routes if r.default]
    assert len(defaults) == 1, f"expected one default chunk route, got {routes!r}"
    return defaults[0].pipeline_path


def test_both_overlay_files_exist() -> None:
    assert _RANKED.is_file(), f"missing {_RANKED}"
    assert _COMPOSITE.is_file(), f"missing {_COMPOSITE}"


def test_ranked_overlay_selects_chunk_search_ranked() -> None:
    config = AppConfig.load(explicit_path=_RANKED)
    assert _chunk_default_pipeline_path(config) == Path(
        "pipelines/chunk_search_ranked.yaml",
    )


def test_composite_overlay_selects_chunk_search() -> None:
    config = AppConfig.load(explicit_path=_COMPOSITE)
    assert _chunk_default_pipeline_path(config) == Path(
        "pipelines/chunk_search.yaml",
    )


def test_overlays_select_distinct_blueprints() -> None:
    """Guard against both overlays accidentally pointing at the same file —
    the comparison run (composite) and the pydocs-only/oracle runs (ranked)
    MUST resolve to different blueprints or the comparison is meaningless.
    """
    ranked = AppConfig.load(explicit_path=_RANKED)
    composite = AppConfig.load(explicit_path=_COMPOSITE)
    assert _chunk_default_pipeline_path(ranked) != _chunk_default_pipeline_path(
        composite,
    )
