"""SentenceTransformersEmbedder tests.

All tests inject a fake ``model`` (small object with ``encode_query`` /
``encode_document`` returning canned np arrays) so NO torch / sentence-
transformers load is needed — they run in the default ``.venv``.
"""

from __future__ import annotations

import os
import sys
import types
from unittest import mock

import numpy as np
import pytest

from pydocs_mcp.extraction.strategies.embedders.sentence_transformers import (
    _TORCHVISION_HINT,
    SentenceTransformersEmbedder,
)
from tests.extraction.strategies.embedders._failing_import import install_failing_import

_DIM = 8


class _FakeModel:
    """Records call args + returns deterministic, distinguishable vectors.

    ``encode_query`` and ``encode_document`` return different leading
    components so a test can assert the query path went through
    ``encode_query`` (the asymmetric prompt path) and the document path
    through ``encode_document``.
    """

    def __init__(self) -> None:
        self.max_seq_length = 0
        self.query_calls: list[dict] = []
        self.document_calls: list[dict] = []

    def encode_query(self, sentences, **kwargs):
        self.query_calls.append({"sentences": sentences, **kwargs})
        out = np.zeros((len(sentences), _DIM), dtype=np.float64)
        out[:, 0] = 1.0  # marker: query path
        return out

    def encode_document(self, sentences, **kwargs):
        self.document_calls.append({"sentences": sentences, **kwargs})
        out = np.zeros((len(sentences), _DIM), dtype=np.float64)
        out[:, 1] = 1.0  # marker: document path
        return out


@pytest.fixture
def emb() -> SentenceTransformersEmbedder:
    return SentenceTransformersEmbedder(model_name="x", dim=_DIM, model=_FakeModel())


def test_dim_field_exposed(emb: SentenceTransformersEmbedder) -> None:
    assert emb.dim == _DIM
    assert emb.model_name == "x"


async def test_embed_query_uses_encode_query_path(emb: SentenceTransformersEmbedder) -> None:
    v = await emb.embed_query("the query")
    # Asymmetric query path: must have gone through encode_query.
    assert len(emb.model.query_calls) == 1
    assert emb.model.document_calls == []
    assert v[0] == 1.0  # query marker component
    assert isinstance(v, np.ndarray)
    assert v.dtype == np.float32
    assert v.shape == (_DIM,)


async def test_embed_query_is_model_agnostic_no_prompt_by_default(
    emb: SentenceTransformersEmbedder,
) -> None:
    # Model-agnostic default: no prompt_name is forced, so a model without a
    # named query prompt is not pushed through a non-existent prompt (which
    # would raise). encode_query still applies the model's OWN default prompt.
    assert emb.query_prompt_name is None
    await emb.embed_query("q")
    call = emb.model.query_calls[0]
    assert "prompt_name" not in call
    assert call.get("normalize_embeddings") is True
    assert call.get("convert_to_numpy") is True


async def test_embed_query_passes_prompt_name_when_configured() -> None:
    # An explicit override IS forwarded — for an asymmetric model where the
    # caller wants a specific named prompt.
    emb = SentenceTransformersEmbedder(
        model_name="x", dim=_DIM, model=_FakeModel(), query_prompt_name="query"
    )
    await emb.embed_query("q")
    assert emb.model.query_calls[0].get("prompt_name") == "query"


# ── query_prefix: native ST path via encode_query(prompt=...) ──
# Fakes cannot catch changes in ST's own precedence rules, so the pinned
# behavior is cited here by method name (verified on sentence-transformers
# 5.3.0 — the uv.lock pin — and 5.5.1): SentenceTransformer.encode_query
# auto-applies the model's "query" prompt only when
# `prompt_name is None and "query" in self.prompts and prompt is None` —
# passing prompt= suppresses it (no double prompting); encode() lets prompt
# win over prompt_name (config makes them mutually exclusive), prepends the
# resolved prompt to each text and records prompt_length; and
# encode_document independently applies the model's own
# document/passage/corpus prompt, which query_prefix never touches.
# test_installed_st_encode_query_prompt_suppresses_named_query_prompt pins
# the first rule against the INSTALLED package, not a fake.

_PREFIX = "Instruct: find the code\nQuery:"


async def test_embed_query_normalizes_text_before_native_prefix() -> None:
    # With the query cache OFF nothing upstream strips the text, so the
    # native path must normalize exactly like CachingEmbedder: the provider
    # input is then one function of the query whatever the cache setting.
    emb = SentenceTransformersEmbedder(
        model_name="x", dim=_DIM, model=_FakeModel(), query_prefix=_PREFIX
    )
    await emb.embed_query("  q \n")
    assert emb.model.query_calls[0]["sentences"] == ["q"]
    assert emb.model.query_calls[0]["prompt"] == _PREFIX


def test_installed_st_encode_query_prompt_suppresses_named_query_prompt() -> None:
    # Contract test against the installed sentence-transformers (no model
    # download): an explicit prompt= must replace the checkpoint's "query"
    # prompt, never stack on it. Breaks loudly if an ST upgrade changes the
    # encode_query "query"-prompt gate that the native query_prefix path
    # relies on.
    st = pytest.importorskip("sentence_transformers")
    model = object.__new__(st.SentenceTransformer)
    recorded: dict = {}
    object.__setattr__(model, "prompts", {"query": "X"})
    object.__setattr__(model, "encode", lambda **kwargs: recorded.update(kwargs))
    model.encode_query(["q"], prompt="P")
    assert recorded["prompt"] == "P"
    assert recorded["prompt_name"] is None
    model.encode_query(["q"])
    assert (recorded["prompt"], recorded["prompt_name"]) == (None, "query")


async def test_embed_query_passes_query_prefix_as_prompt() -> None:
    emb = SentenceTransformersEmbedder(
        model_name="x", dim=_DIM, model=_FakeModel(), query_prefix=_PREFIX
    )
    await emb.embed_query("q")
    call = emb.model.query_calls[0]
    assert call["sentences"] == ["q"]
    assert call["prompt"] == _PREFIX
    assert "prompt_name" not in call


async def test_embed_query_kwargs_unchanged_without_prefix(
    emb: SentenceTransformersEmbedder,
) -> None:
    assert emb.query_prefix is None
    await emb.embed_query("q")
    assert emb.model.query_calls[0] == {
        "sentences": ["q"],
        "normalize_embeddings": True,
        "convert_to_numpy": True,
    }


@pytest.mark.parametrize("blank", ["", "  \n"])
async def test_blank_query_with_prefix_sends_no_prompt(blank: str) -> None:
    emb = SentenceTransformersEmbedder(
        model_name="x", dim=_DIM, model=_FakeModel(), query_prefix=_PREFIX
    )
    await emb.embed_query(blank)
    assert "prompt" not in emb.model.query_calls[0]


async def test_encode_document_never_receives_prefix() -> None:
    emb = SentenceTransformersEmbedder(
        model_name="x", dim=_DIM, model=_FakeModel(), query_prefix=_PREFIX
    )
    await emb.embed_chunks(["a", "b"])
    call = emb.model.document_calls[0]
    assert "prompt" not in call
    assert "prompt_name" not in call
    assert call["sentences"] == ["a", "b"]


def test_st_embedder_declares_native_query_prefix() -> None:
    assert SentenceTransformersEmbedder.applies_query_prefix_natively is True


async def test_embed_chunks_uses_encode_document_path(
    emb: SentenceTransformersEmbedder,
) -> None:
    out = await emb.embed_chunks(["a", "bb", "ccc"])
    assert len(emb.model.document_calls) == 1
    assert emb.model.query_calls == []
    assert len(out) == 3
    assert all(isinstance(v, np.ndarray) for v in out)
    assert all(v.dtype == np.float32 for v in out)
    assert all(v.shape == (_DIM,) for v in out)
    # Document path marker is component 1.
    assert all(v[1] == 1.0 for v in out)


async def test_embed_chunks_passes_batch_size(emb: SentenceTransformersEmbedder) -> None:
    await emb.embed_chunks(["a", "b"])
    call = emb.model.document_calls[0]
    assert call.get("normalize_embeddings") is True
    assert call.get("convert_to_numpy") is True
    assert call.get("batch_size") == emb.batch_size


async def test_embed_chunks_empty_returns_empty_tuple(
    emb: SentenceTransformersEmbedder,
) -> None:
    assert await emb.embed_chunks([]) == ()
    # No model call at all on the empty path.
    assert emb.model.document_calls == []


def test_close_nulls_model_and_is_idempotent(emb: SentenceTransformersEmbedder) -> None:
    assert emb.model is not None
    emb.close()
    assert emb.model is None
    # Idempotent: a second close on an already-closed embedder does not raise.
    emb.close()
    assert emb.model is None


def test_accepts_device_field() -> None:
    e = SentenceTransformersEmbedder(model_name="x", dim=_DIM, model=_FakeModel(), device="cuda")
    assert e.device == "cuda"


def test_injected_model_skips_real_load() -> None:
    """An injected model means __post_init__ must NOT import sentence_transformers."""
    fake = _FakeModel()
    e = SentenceTransformersEmbedder(model_name="x", dim=_DIM, model=fake)
    # __post_init__ should still apply the seq-length cap to the injected model.
    assert e.model is fake
    assert fake.max_seq_length == e.max_seq_length


# ── backend (torch | onnx | openvino) + quantized model_file_name ──


def _install_fake_st_module(monkeypatch, records: list[dict], fail: Exception | None = None):
    """Inject a fake ``sentence_transformers`` module whose constructor records
    kwargs — lets tests assert exactly what ``__post_init__`` passes without
    torch or a model download."""
    import sys
    import types

    mod = types.ModuleType("sentence_transformers")

    class _RecordingCtor:
        def __init__(self, model_name, **kwargs):
            records.append({"model_name": model_name, **kwargs})
            if fail is not None:
                raise fail
            self.max_seq_length = 0

    mod.SentenceTransformer = _RecordingCtor
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)


def test_default_torch_backend_constructor_kwargs_unchanged(monkeypatch) -> None:
    """Defaults must construct EXACTLY as before this feature: model_name +
    device only — no backend=, no model_kwargs= keys leak into the call."""
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records)
    SentenceTransformersEmbedder(model_name="m", dim=_DIM)
    assert records == [{"model_name": "m", "device": "cpu"}]


def test_openvino_backend_and_quantized_file_passed(monkeypatch) -> None:
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records)
    SentenceTransformersEmbedder(
        model_name="m",
        dim=_DIM,
        backend="openvino",
        model_file_name="openvino/openvino_model_qint8_quantized.xml",
    )
    assert records == [
        {
            "model_name": "m",
            "device": "cpu",
            "backend": "openvino",
            "model_kwargs": {"file_name": "openvino/openvino_model_qint8_quantized.xml"},
        }
    ]


def test_onnx_backend_without_file(monkeypatch) -> None:
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records)
    SentenceTransformersEmbedder(model_name="m", dim=_DIM, backend="onnx")
    assert records == [{"model_name": "m", "device": "cpu", "backend": "onnx"}]


def test_nontorch_backend_construction_failure_gets_install_hint(monkeypatch) -> None:
    """A missing optimum/openvino dep surfaces as ImportError deep inside the
    ST constructor — with a non-torch backend configured, re-raise with the
    actionable extras hint."""
    records: list[dict] = []
    _install_fake_st_module(
        monkeypatch, records, fail=ModuleNotFoundError("No module named 'optimum'")
    )
    # The backend probe must see the same absence the constructor hit.
    monkeypatch.setitem(sys.modules, "optimum.intel.openvino", None)
    with pytest.raises(ImportError, match=r"sentence-transformers\[openvino\]") as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM, backend="openvino")
    assert isinstance(excinfo.value.__cause__, ModuleNotFoundError)


def test_torch_backend_construction_failure_propagates_raw(monkeypatch) -> None:
    """Default-backend constructor errors keep today's behavior — no rewrap."""
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records, fail=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        SentenceTransformersEmbedder(model_name="m", dim=_DIM)


# ── non-torch backends: surface the backend's REAL import error ──
# sentence-transformers' backend loaders (sentence_transformers/backend/load.py)
# turn a failed backend import into a bare `Exception` that says "install
# sentence-transformers[openvino]" — even when the extra IS installed and the
# import breaks on a version mismatch (optimum-intel 1.15.0 on openvino 2026).

_ST_OPENVINO_MSG = (
    "Using the OpenVINO backend requires installing Optimum and OpenVINO. "
    "You can install them with pip: `pip install sentence-transformers[openvino]`"
)
_ST_ONNX_MSG = "Using the ONNX backend requires installing Optimum and ONNX Runtime."


def test_openvino_broken_backend_import_surfaces_real_error(monkeypatch) -> None:
    real = ModuleNotFoundError("No module named 'openvino.runtime'", name="openvino.runtime")
    install_failing_import(monkeypatch, "optimum.intel.openvino", real)
    monkeypatch.setitem(sys.modules, "openvino", types.ModuleType("openvino"))  # installed
    _install_fake_st_module(monkeypatch, [], fail=Exception(_ST_OPENVINO_MSG))
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="org/m", dim=_DIM, backend="openvino")
    msg = str(excinfo.value)
    assert "No module named 'openvino.runtime'" in msg
    assert "'optimum.intel.openvino'" in msg
    assert "'org/m'" in msg
    assert "installed" in msg
    assert "Install with" not in msg
    assert excinfo.value.__cause__ is real


def test_openvino_absent_backend_package_gets_install_hint(monkeypatch) -> None:
    """optimum-intel importable, openvino itself not installed — a real
    missing dependency, so the extras hint is the honest answer."""
    real = ModuleNotFoundError("No module named 'openvino'", name="openvino")
    install_failing_import(monkeypatch, "optimum.intel.openvino", real)
    monkeypatch.setitem(sys.modules, "openvino", None)  # not installed
    _install_fake_st_module(monkeypatch, [], fail=Exception(_ST_OPENVINO_MSG))
    with pytest.raises(ImportError, match=r"sentence-transformers\[openvino\]") as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM, backend="openvino")
    assert "No module named 'openvino'" in str(excinfo.value)
    assert excinfo.value.__cause__ is real


def test_onnx_broken_backend_import_surfaces_real_error(monkeypatch) -> None:
    real = ImportError(
        "cannot import name 'ORTModelForFeatureExtraction' from 'optimum.onnxruntime'"
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", types.ModuleType("onnxruntime"))
    install_failing_import(monkeypatch, "optimum.onnxruntime", real)
    _install_fake_st_module(monkeypatch, [], fail=Exception(_ST_ONNX_MSG))
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM, backend="onnx")
    msg = str(excinfo.value)
    assert "cannot import name 'ORTModelForFeatureExtraction'" in msg
    assert "'optimum.onnxruntime'" in msg
    assert "Install with" not in msg
    assert excinfo.value.__cause__ is real


@pytest.mark.parametrize("err", [OSError("openvino_model.xml not found"), ImportError("x")])
def test_nontorch_failure_with_healthy_backend_propagates_raw(monkeypatch, err) -> None:
    """The backend imports fine, so its packages are not the cause — the
    constructor's own error escapes untouched instead of a misleading hint."""
    monkeypatch.setitem(
        sys.modules, "optimum.intel.openvino", types.ModuleType("optimum.intel.openvino")
    )
    _install_fake_st_module(monkeypatch, [], fail=err)
    with pytest.raises(type(err)) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM, backend="openvino")
    assert excinfo.value is err


def test_torch_backend_bare_exception_propagates_raw(monkeypatch) -> None:
    """The torch path never probes or rewraps, even for a bare Exception."""
    err = Exception("some sentence-transformers failure")
    _install_fake_st_module(monkeypatch, [], fail=err)
    with pytest.raises(Exception) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM)
    assert excinfo.value is err


# ── torchvision-mentioning construction failures get an actionable hint ──
# (spec docs/superpowers/specs/2026-07-11-sentence-transformers-torchvision-bug-spec.md §5)


def test_torchvision_failure_gets_hint_on_torch_backend(monkeypatch) -> None:
    """AC1: a torchvision-mentioning ImportError during construction on the
    default torch backend is re-raised as _TORCHVISION_HINT, chained."""
    records: list[dict] = []
    original = ImportError(
        "`SomeImageProcessorFast` requires the Torchvision library but it "
        "was not found in your environment"
    )
    _install_fake_st_module(monkeypatch, records, fail=original)
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="Qwen/Qwen3-Embedding-0.6B", dim=1024)
    msg = str(excinfo.value)
    assert msg == _TORCHVISION_HINT
    assert "torchvision" in msg
    assert "transformers>=5.10" in msg
    assert "exact-pins" in msg
    assert "sentence-transformers[image]" in msg
    assert excinfo.value.__cause__ is original


def test_chained_torchvision_failure_detected_via_cause(monkeypatch) -> None:
    """AC2: the torchvision marker one level down the __cause__ chain (the
    exact shape ST's suggest_extra_on_exception produces — outer message is
    torchvision-free, forcing a genuine chain walk) is still detected."""
    inner = ImportError(
        "`SomeImageProcessorFast` requires the Torchvision library but it "
        "was not found in your environment"
    )
    outer = ImportError(
        'To install the required dependencies, run: pip install -U "sentence-transformers[image]"'
    )
    outer.__cause__ = inner
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records, fail=outer)
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM)
    assert str(excinfo.value) == _TORCHVISION_HINT
    assert excinfo.value.__cause__ is outer


def test_torchvision_failure_on_nontorch_backend_gets_torchvision_hint(monkeypatch) -> None:
    """AC3: the torchvision check runs FIRST — an openvino-backend torchvision
    failure must NOT be misdiagnosed as a missing-optimum problem."""
    records: list[dict] = []
    _install_fake_st_module(
        monkeypatch,
        records,
        fail=ImportError("X requires the Torchvision library but it was not found"),
    )
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM, backend="openvino")
    assert str(excinfo.value) == _TORCHVISION_HINT
    assert "sentence-transformers[openvino]" not in str(excinfo.value)


def test_non_torchvision_import_error_on_torch_backend_propagates_raw(monkeypatch) -> None:
    """AC4(b): a plain ImportError (no torchvision mention) on the torch
    backend escapes un-rewrapped — the widened guard reroutes exactly this
    case, so the existing RuntimeError test alone cannot pin it."""
    err = ImportError("No module named 'foo'")
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records, fail=err)
    with pytest.raises(ImportError) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM)
    assert excinfo.value is err


def test_non_torchvision_attribute_error_propagates_raw(monkeypatch) -> None:
    """AC6: the catch widens to AttributeError only to INSPECT, never to
    swallow — an unrelated AttributeError escapes untouched."""
    err = AttributeError("module 'transformers' has no attribute 'Whatever'")
    records: list[dict] = []
    _install_fake_st_module(monkeypatch, records, fail=err)
    with pytest.raises(AttributeError) as excinfo:
        SentenceTransformersEmbedder(model_name="m", dim=_DIM)
    assert excinfo.value is err


def test_module_import_never_touches_heavy_stack() -> None:
    """AC7: importing the provider module must never pull torch / torchvision /
    sentence_transformers at import time. Checked in a fresh subprocess: in a
    dev venv that HAS the extra installed, sibling tests legitimately land
    torch in sys.modules (close() best-effort-imports it), which would
    false-fail an in-process check."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import pydocs_mcp.extraction.strategies.embedders.sentence_transformers\n"
        "assert 'torchvision' not in sys.modules\n"
        "assert 'torch' not in sys.modules\n"
        "assert 'sentence_transformers' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


# ── airgap (spec D5): local model dir forces HF offline ──


def test_local_dir_sets_offline_env(tmp_path) -> None:
    # Env snapshot/restore: enable_hf_offline() writes os.environ directly,
    # and patch.dict restores even vars that were absent before the test.
    with mock.patch.dict(os.environ):
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
        SentenceTransformersEmbedder(model_name=str(tmp_path), dim=_DIM, model=_FakeModel())
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_repo_id_does_not_touch_offline_env() -> None:
    with mock.patch.dict(os.environ):
        os.environ.pop("HF_HUB_OFFLINE", None)
        SentenceTransformersEmbedder(
            model_name="Qwen/Qwen3-Embedding-0.6B", dim=_DIM, model=_FakeModel()
        )
        assert "HF_HUB_OFFLINE" not in os.environ


def test_local_dir_tilde_is_expanded_for_the_loader(tmp_path, monkeypatch) -> None:
    # SentenceTransformer does not expanduser, so a `~/models/x` spelling
    # must reach the loader in expanded form or it would be rejected as a
    # malformed HF repo id. POSIX-only: expanduser reads HOME.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "models" / "x").mkdir(parents=True)
    with mock.patch.dict(os.environ):
        emb = SentenceTransformersEmbedder(model_name="~/models/x", dim=_DIM, model=_FakeModel())
    assert emb.model_name == str(tmp_path / "models" / "x")
