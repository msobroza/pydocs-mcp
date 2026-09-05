"""LI benchmark overlays must ingest via the SHIPPED late-interaction preset.

``_resolve_pipeline_path`` gives ``pipelines/foo.yaml`` references
search-path semantics: a file next to the config SHADOWS the shipped one.
A local ``benchmarks/configs/pipelines/ingestion_late_interaction.yaml``
copy exploited that and drifted — it missed the ``capture_decisions`` and
``dependency_doc_pages`` stages that landed in the default ingestion
preset, so every LI sweep column indexed a different corpus than its
non-LI siblings (which ingest via the shipped ``ingestion.yaml``). The
copy was deleted 2026-07-10; these tests keep the resolution pointed at
the shipped preset, whose parity with ``ingestion.yaml`` is enforced by
``tests/pipelines/test_late_interaction_yaml_round_trip.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydocs_mcp.extraction.factories import _resolve_ingestion_pipeline_path
from pydocs_mcp.retrieval.config import AppConfig

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"

# All four LI overlays shipped under configs/. The former DS-1000-prefixed
# duplicate was consolidated into hybrid_li_rrf.yaml (2026-07-18 dataset-prefix
# removal), so one entry now covers what used to be two byte-identical files.
_LI_OVERLAYS = (
    "li.yaml",
    "li_edge.yaml",
    "hybrid_li_rrf.yaml",
    "hybrid_li_wsi.yaml",
)


def _shipped_preset() -> Path:
    import pydocs_mcp

    return (
        Path(pydocs_mcp.__file__).parent / "pipelines" / "ingestion_late_interaction.yaml"
    ).resolve()


def test_no_local_ingestion_preset_shadow_copy() -> None:
    """A config-local copy would silently win resolution and drift again."""
    shadow = _CONFIGS_DIR / "pipelines" / "ingestion_late_interaction.yaml"
    assert not shadow.exists(), (
        f"{shadow} shadows the shipped preset via pipeline_path search-path "
        "semantics; delete it — LI overlays must fall back to the shipped file"
    )


@pytest.mark.parametrize("overlay", _LI_OVERLAYS)
def test_li_overlay_ingestion_resolves_to_shipped_preset(overlay: str) -> None:
    config = AppConfig.load(explicit_path=_CONFIGS_DIR / overlay)
    override = config.extraction.ingestion.pipeline_path
    assert override is not None, f"{overlay} must pin the LI ingestion pipeline"
    resolved = _resolve_ingestion_pipeline_path(Path(override), config)
    assert resolved == _shipped_preset(), (
        f"{overlay} resolved its ingestion pipeline to {resolved}, "
        "expected the shipped late-interaction preset"
    )
    assert resolved.is_file()
