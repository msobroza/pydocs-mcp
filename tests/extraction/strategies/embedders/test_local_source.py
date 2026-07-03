"""local_model_dir + enable_hf_offline (airgap spec D1/D5)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

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


def test_empty_model_name_is_none() -> None:
    # Path("") is Path(".") — a blank YAML value must not resolve to the cwd.
    assert local_model_dir("") is None


def test_whitespace_model_name_is_none() -> None:
    assert local_model_dir("   ") is None


def test_existing_file_is_none(tmp_path: Path) -> None:
    weights = tmp_path / "weights.onnx"
    weights.touch()
    assert local_model_dir(str(weights)) is None


def test_tilde_is_expanded(tmp_path: Path, monkeypatch) -> None:
    # POSIX-only: WindowsPath.expanduser reads USERPROFILE, but this project
    # is POSIX-deployed so pinning HOME is sufficient.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "models").mkdir()
    assert local_model_dir("~/models") == tmp_path / "models"


def test_enable_hf_offline_sets_both_vars() -> None:
    # mock.patch.dict snapshots/restores the WHOLE environ, so the vars we
    # pop-then-set inside cannot leak into later tests in the session.
    with mock.patch.dict(os.environ):
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
        enable_hf_offline()
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_enable_hf_offline_respects_operator_setting() -> None:
    # setdefault semantics: an operator's explicit value (even "0") wins.
    with mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "0"}):
        enable_hf_offline()
        assert os.environ["HF_HUB_OFFLINE"] == "0"
