"""``pipelines:`` handler keys are a closed set — unknown keys must fail load.

``pipelines`` is typed as an open ``Mapping[str, HandlerConfig]``, so unlike
every ``BaseModel`` sub-config (which rejects unknown fields), a stray handler
key used to pass validation and be silently ignored — ``pipeline_assembly``
only ever reads ``pipelines["chunk"]`` / ``pipelines["member"]``. A
``pipelines.ingestion`` route list in three benchmark overlays rode that
silence for weeks: the ingestion pipeline is selected by
``extraction.ingestion.pipeline_path``, so the key did nothing, fast-plaid
stayed empty, and the hybrid late-interaction results were effectively
BM25-only (see benchmarks/EXPERIMENTS.md §Late-interaction conditions).
"""

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig

_INGESTION_OVERLAY = """\
pipelines:
  ingestion:
    - default: true
      pipeline_path: pipelines/ingestion_late_interaction.yaml
"""


def test_pipelines_ingestion_key_rejected_with_pointer(tmp_path) -> None:
    """The historical footgun: reject AND point at the correct setting."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(_INGESTION_OVERLAY)
    with pytest.raises(ValidationError, match="extraction.ingestion.pipeline_path"):
        AppConfig.load(explicit_path=overlay)


def test_pipelines_unknown_key_rejected_naming_offender_and_allowed(tmp_path) -> None:
    """The error carries the offending key and the supported handler set."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "pipelines:\n  chnk:\n    - default: true\n      pipeline_path: pipelines/chunk_search.yaml\n"
    )
    with pytest.raises(ValidationError, match="chnk") as excinfo:
        AppConfig.load(explicit_path=overlay)
    message = str(excinfo.value)
    assert "chunk" in message
    assert "member" in message


def test_pipelines_supported_handlers_still_load(tmp_path) -> None:
    """Guard against over-restriction: chunk/member overlays and defaults load."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "pipelines:\n  chunk:\n    - default: true\n      pipeline_path: pipelines/chunk_search.yaml\n"
    )
    config = AppConfig.load(explicit_path=overlay)
    assert set(config.pipelines) == {"chunk", "member"}
    assert set(AppConfig.load().pipelines) == {"chunk", "member"}
