"""FastEmbedEmbedder construction (AC-13, AC-16) + airgap local-dir mode."""

import os
import sys
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest


def test_fastembedembedder_construction_with_mocked_fastembed() -> None:
    """When fastembed is mocked, FastEmbedEmbedder constructs OK."""
    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = MagicMock()

    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed",
        None,
    )
    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        emb = FastEmbedEmbedder(model_name="BAAI/bge-small-en-v1.5", dim=384)
        assert emb.dim == 384
        assert emb.model_name == "BAAI/bge-small-en-v1.5"

    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed",
        None,
    )


def test_fastembed_cuda_passes_gpu_providers() -> None:
    """device='cuda' constructs TextEmbedding with CUDA-first providers."""
    import sys
    from unittest.mock import MagicMock, patch

    captured = {}

    def _fake_text_embedding(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = _fake_text_embedding

    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        FastEmbedEmbedder(model_name="m", dim=384, device="cuda")

    assert captured["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_fastembed_cpu_omits_providers() -> None:
    """device='cpu' (default) constructs without a providers kwarg."""
    import sys
    from unittest.mock import MagicMock, patch

    captured = {}

    def _fake_text_embedding(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = _fake_text_embedding

    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        FastEmbedEmbedder(model_name="m", dim=384)

    assert "providers" not in captured
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def _patched_fastembed_modules():
    """Mock fastembed + the model_description submodule (lazy-imported in local mode)."""
    calls: dict = {"add_custom_model": [], "ctor": []}

    class _FakeTextEmbedding:
        @classmethod
        def add_custom_model(cls, **kwargs):
            calls["add_custom_model"].append(kwargs)

        def __init__(self, **kwargs):
            calls["ctor"].append(kwargs)

    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = _FakeTextEmbedding
    mock_md = MagicMock()  # fastembed.common.model_description
    return calls, {
        "fastembed": mock_fastembed,
        "fastembed.common": MagicMock(),
        "fastembed.common.model_description": mock_md,
    }


def _fresh_fastembed_embedder(modules):
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
    with patch.dict(sys.modules, modules):
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        return FastEmbedEmbedder


def test_local_dir_registers_custom_model_and_pins_path(tmp_path) -> None:
    calls, modules = _patched_fastembed_modules()
    model_dir = tmp_path / "bge-small-local"
    model_dir.mkdir()

    # patch.dict snapshots/restores the whole environ — a plain
    # monkeypatch.delenv would NOT restore a var that was absent, leaking
    # HF_HUB_OFFLINE=1 into the rest of the suite.
    with mock.patch.dict(os.environ):
        os.environ.pop("HF_HUB_OFFLINE", None)
        with patch.dict(sys.modules, modules):
            cls = _fresh_fastembed_embedder(modules)
            cls(
                model_name=str(model_dir),
                dim=384,
                pooling="cls",
                normalize=False,
                model_file_name="onnx/model_q.onnx",
            )
        assert os.environ["HF_HUB_OFFLINE"] == "1"  # D5 guard fired

    (reg,) = calls["add_custom_model"]
    assert reg["model"] == "bge-small-local"
    assert reg["dim"] == 384
    assert reg["normalization"] is False
    assert reg["model_file"] == "onnx/model_q.onnx"
    (ctor,) = calls["ctor"]
    assert ctor["model_name"] == "bge-small-local"
    assert ctor["specific_model_path"] == str(model_dir)
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_local_dir_registration_is_idempotent(tmp_path) -> None:
    calls, modules = _patched_fastembed_modules()
    model_dir = tmp_path / "bge-small-local"
    model_dir.mkdir()

    # Local-dir construction fires enable_hf_offline(); snapshot/restore the
    # environ so HF_HUB_OFFLINE=1 can't leak into the rest of the suite.
    with mock.patch.dict(os.environ), patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        cls(model_name=str(model_dir), dim=384)
        # Must NOT re-register (real fastembed raises on a duplicate label).
        cls(model_name=str(model_dir), dim=384)

    assert len(calls["add_custom_model"]) == 1
    assert len(calls["ctor"]) == 2
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_same_label_conflicting_recipe_raises(tmp_path) -> None:
    calls, modules = _patched_fastembed_modules()
    a = tmp_path / "a" / "same-name"
    b = tmp_path / "b" / "same-name"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    # Env snapshot: local-dir construction mutates HF offline vars (D5).
    with mock.patch.dict(os.environ), patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        cls(model_name=str(a), dim=384)
        with pytest.raises(ValueError, match="same-name"):
            cls(model_name=str(b), dim=384)

    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_repo_id_never_registers() -> None:
    # Non-regression: the online path is byte-identical — no registration,
    # no specific_model_path, no offline env mutation.
    calls, modules = _patched_fastembed_modules()

    with mock.patch.dict(os.environ):
        os.environ.pop("HF_HUB_OFFLINE", None)
        with patch.dict(sys.modules, modules):
            cls = _fresh_fastembed_embedder(modules)
            cls(model_name="BAAI/bge-small-en-v1.5", dim=384)
        assert "HF_HUB_OFFLINE" not in os.environ

    assert calls["add_custom_model"] == []
    (ctor,) = calls["ctor"]
    assert "specific_model_path" not in ctor
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_local_dir_cuda_keeps_gpu_providers(tmp_path) -> None:
    calls, modules = _patched_fastembed_modules()
    model_dir = tmp_path / "bge-small-local"
    model_dir.mkdir()

    # Env snapshot: local-dir construction mutates HF offline vars (D5).
    with mock.patch.dict(os.environ), patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        cls(model_name=str(model_dir), dim=384, device="cuda")

    (ctor,) = calls["ctor"]
    assert ctor["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert ctor["specific_model_path"] == str(model_dir)
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_dot_model_name_raises_on_empty_label() -> None:
    # model_name="." IS an existing directory but its basename is "" —
    # an empty label must fail loudly, not register with ModelSource(hf="").
    calls, modules = _patched_fastembed_modules()

    with mock.patch.dict(os.environ), patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        with pytest.raises(ValueError, match="label"):
            cls(model_name=".", dim=384)

    assert calls["add_custom_model"] == []
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_unknown_pooling_error_names_sentence_transformers(tmp_path) -> None:
    # Direct construction bypasses the YAML Literal guard — the error must
    # steer the operator toward the provider that supports exotic pooling.
    calls, modules = _patched_fastembed_modules()
    model_dir = tmp_path / "bge-small-local"
    model_dir.mkdir()

    with mock.patch.dict(os.environ), patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        with pytest.raises(ValueError, match="sentence_transformers"):
            cls(model_name=str(model_dir), dim=384, pooling="last")

    assert calls["add_custom_model"] == []
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
