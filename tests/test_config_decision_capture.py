"""The decision_capture: config block + ChunkOrigin.DECISION_RECORD (spec §D8)."""

import pytest
from pydantic import ValidationError

from pydocs_mcp.models import ChunkOrigin
from pydocs_mcp.retrieval.config import AppConfig


def test_decision_record_origin_value() -> None:
    assert ChunkOrigin.DECISION_RECORD.value == "decision_record"


def test_decision_capture_defaults_present() -> None:
    config = AppConfig.load()
    dc = config.decision_capture
    assert dc.enabled is True
    assert dc.merge_jaccard == 0.85
    assert dc.sources == [
        "adr_files",
        "inline_markers",
        "commit_messages",
        "changelog",
        "docs_prose",
    ]
    assert dc.include_deps is False
    assert dc.inline_markers.context_lines == 20
    assert dc.commit_messages.max_commits == 2000
    assert dc.commit_messages.timeout_seconds == 30.0
    assert dc.docs_prose.max_files == 10
    assert dc.docs_prose.max_kb_per_file == 50


def test_decision_capture_llm_structuring_defaults_off() -> None:
    config = AppConfig.load()
    llm = config.decision_capture.llm_structuring
    assert llm.enabled is False
    assert llm.grounding_threshold == 0.60
    assert llm.batch_size == 5


def test_decision_capture_overridable_via_overlay(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "decision_capture:\n"
        "  enabled: false\n"
        "  merge_jaccard: 0.5\n"
        "  llm_structuring:\n"
        "    enabled: true\n"
    )
    config = AppConfig.load(explicit_path=overlay)
    assert config.decision_capture.enabled is False
    assert config.decision_capture.merge_jaccard == 0.5
    assert config.decision_capture.llm_structuring.enabled is True


def test_decision_capture_unknown_key_rejected(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("decision_capture:\n  bogus_key: 1\n")
    with pytest.raises(ValidationError):
        AppConfig.load(explicit_path=overlay)
