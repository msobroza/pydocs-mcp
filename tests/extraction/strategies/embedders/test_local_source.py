"""local_model_dir + enable_hf_offline (airgap spec D1/D5)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.strategies.embedders.local_source import (
    enable_hf_offline,
    local_model_dir,
)


def test_existing_directory_resolves(tmp_path: Path) -> None:
    assert local_model_dir(str(tmp_path)) == tmp_path


def test_repo_id_is_not_a_directory() -> None:
    assert local_model_dir("BAAI/bge-small-en-v1.5") is None


def test_nonexistent_path_is_none(tmp_path: Path) -> None:
    assert local_model_dir(str(tmp_path / "no-such-dir")) is None


def test_tilde_is_expanded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "models").mkdir()
    assert local_model_dir("~/models") == tmp_path / "models"


def test_enable_hf_offline_sets_both_vars(monkeypatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    enable_hf_offline()
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_enable_hf_offline_respects_operator_setting(monkeypatch) -> None:
    # setdefault semantics: an operator's explicit value (even "0") wins.
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    enable_hf_offline()
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "0"
