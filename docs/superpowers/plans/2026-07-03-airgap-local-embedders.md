# Airgap / local-path model loading for all embedders — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `embedding.model_name` is a local directory, every embedder loads the model from disk with zero network calls (airgap); when it is a repo id, behavior is byte-for-byte unchanged.

**Architecture:** A shared `local_model_dir()` helper detects directory-form `model_name`. FastEmbed local mode registers the folder via `TextEmbedding.add_custom_model` (idempotence-guarded — duplicate registration raises in fastembed 0.8.0) and loads via `specific_model_path` (verified: short-circuits all downloads). sentence-transformers/PyLate accept dirs natively and only gain an offline env guard. OpenAI + dir fails fast in `build_embedder`. One new YAML field (`pooling`), folded into the pipeline hash only when non-default.

**Tech Stack:** Python 3.11, pydantic v2, fastembed 0.8.0, pytest. Repo venv: `.venv/bin/python`. Spec: `docs/superpowers/specs/2026-07-03-airgap-local-embedders-design.md` (rev 2).

**Branch:** `feat/airgap-local-embedders` (based on origin/main v0.4.0). Run everything from `/Users/msobroza/Projects/pyctx7-mcp`.

**Conventions that bind every task:**
- TDD: write the failing test, watch it fail, implement, watch it pass, commit.
- Comments explain WHY (constraints, verified facts), never WHAT.
- No `Co-Authored-By` trailers on any commit (user authorship policy).
- Test runner: `.venv/bin/python -m pytest <path> -q`. Lint: `.venv/bin/ruff check python/ tests/`.

---

### Task 1: `local_source.py` — shared detection + offline guard

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/embedders/local_source.py`
- Test: `tests/extraction/strategies/embedders/test_local_source.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/extraction/strategies/embedders/test_local_source.py -q`
Expected: FAIL — `ModuleNotFoundError: ... local_source`

- [ ] **Step 3: Write the implementation**

```python
"""Local-directory model resolution shared by every embedder (airgap).

Spec: docs/superpowers/specs/2026-07-03-airgap-local-embedders-design.md
(D1: model_name-as-directory detection; D5: HF offline hardening).
"""

from __future__ import annotations

import os
from pathlib import Path


def local_model_dir(model_name: str) -> Path | None:
    """Return the model directory when ``model_name`` is a local path, else None.

    A repo id like ``BAAI/bge-small-en-v1.5`` never names an existing
    directory relative to the server's cwd in practice; anything that IS an
    existing directory is treated as side-loaded weights (spec D1 overloads
    ``model_name`` instead of adding a second YAML field).
    """
    path = Path(model_name).expanduser()
    if path.is_dir():
        return path
    return None


def enable_hf_offline() -> None:
    """Force huggingface_hub / transformers offline for airgap loads.

    ``setdefault`` so an operator's explicit setting (including an explicit
    opt-out ``HF_HUB_OFFLINE=0``) always wins over ours. Process-wide by
    design: in local mode the whole process is meant to be offline (D5).
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


__all__ = ("enable_hf_offline", "local_model_dir")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/extraction/strategies/embedders/test_local_source.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/local_source.py tests/extraction/strategies/embedders/test_local_source.py
git commit -m "feat(embedders): local_model_dir + enable_hf_offline airgap helpers"
```

---

### Task 2: `EmbeddingConfig.pooling` field + conditional hash fold

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (field block ~line 433, `compute_pipeline_hash` ~line 529)
- Test: `tests/retrieval/test_config.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `tests/retrieval/test_config.py`)

```python
def test_pooling_default_mean_and_validated() -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    assert EmbeddingConfig().pooling == "mean"
    assert EmbeddingConfig(pooling="cls").pooling == "cls"
    with pytest.raises(ValidationError):
        EmbeddingConfig(pooling="last_token")


def test_pooling_default_keeps_hash_stable() -> None:
    # The "default install hash is stable" invariant: adding the field must
    # not invalidate any existing chunk cache.
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    assert (
        EmbeddingConfig().compute_pipeline_hash()
        == EmbeddingConfig(pooling="mean").compute_pipeline_hash()
    )


def test_pooling_non_default_changes_hash() -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    assert (
        EmbeddingConfig(pooling="cls").compute_pipeline_hash()
        != EmbeddingConfig().compute_pipeline_hash()
    )
```

(`pytest` and `ValidationError` are already imported at the top of this test file; verify before running — if `ValidationError` is missing add `from pydantic import ValidationError`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/retrieval/test_config.py -q -k pooling`
Expected: FAIL — `pooling` extra field forbidden / AttributeError

- [ ] **Step 3: Implement**

In `python/pydocs_mcp/retrieval/config.py`, directly after the `model_file_name: str | None = None` field (~line 434), add:

```python
    # ``pooling`` is read ONLY by the fastembed provider in LOCAL-directory
    # mode (airgap spec D2): fastembed's custom-model registration needs the
    # pooling recipe stated explicitly because an arbitrary ONNX folder does
    # not carry it. Inert for every other provider / online fastembed (same
    # inert-field pattern as the sentence_transformers-only knobs above).
    # fastembed 0.8.0 offers exactly CLS | MEAN | DISABLED — notably NO
    # last-token pooling, so Qwen3-class models must use
    # provider: sentence_transformers instead (spec D3).
    pooling: Literal["mean", "cls", "disabled"] = "mean"
```

In `compute_pipeline_hash`, after the `model_file_name` conditional append (~line 540), add:

```python
        if self.pooling != "mean":
            parts.append(f"pooling:{self.pooling}")
```

And extend the docstring's conditional-fold sentence: mention `pooling` alongside `backend` / `model_file_name` (wrong pooling produces different vectors, so a non-default value must invalidate the chunk cache; the conditional append keeps pre-existing hashes byte-identical).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/retrieval/test_config.py -q`
Expected: all pass (existing hash-stability tests in this file must stay green — they prove the default hash didn't move)

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/retrieval/test_config.py
git commit -m "feat(config): embedding.pooling knob for fastembed local mode, conditional hash fold"
```

---

### Task 3: FastEmbed local mode — `add_custom_model` + `specific_model_path`

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py`
- Test: `tests/extraction/strategies/embedders/test_fastembed_embedder.py` (append)

**Verified facts (fastembed 0.8.0, probed in the repo venv):**
- `add_custom_model(model, pooling: PoolingType, normalization: bool, sources: ModelSource, dim, model_file='onnx/model.onnx', ...)`
- `ModelSource()` with no source **raises** — pass a dummy `ModelSource(hf=<label>)`; it is never consulted because
- `ModelManagement.download_model()` short-circuits: `if specific_model_path: return Path(specific_model_path)` — zero HTTP.
- Registering the same label twice **raises ValueError** — the module-level guard below is mandatory.

- [ ] **Step 1: Write the failing tests** (append; follow the file's existing `sys.modules` patch convention)

```python
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


def test_local_dir_registers_custom_model_and_pins_path(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    calls, modules = _patched_fastembed_modules()
    model_dir = tmp_path / "bge-small-local"
    model_dir.mkdir()

    with patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        cls(model_name=str(model_dir), dim=384, pooling="cls", normalize=False,
            model_file_name="onnx/model_q.onnx")

    (reg,) = calls["add_custom_model"]
    assert reg["model"] == "bge-small-local"
    assert reg["dim"] == 384
    assert reg["normalization"] is False
    assert reg["model_file"] == "onnx/model_q.onnx"
    (ctor,) = calls["ctor"]
    assert ctor["model_name"] == "bge-small-local"
    assert ctor["specific_model_path"] == str(model_dir)
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "1"  # D5 guard fired
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_local_dir_registration_is_idempotent(tmp_path) -> None:
    calls, modules = _patched_fastembed_modules()
    model_dir = tmp_path / "bge-small-local"
    model_dir.mkdir()

    with patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        cls(model_name=str(model_dir), dim=384)
        cls(model_name=str(model_dir), dim=384)  # must NOT re-register (raises in real fastembed)

    assert len(calls["add_custom_model"]) == 1
    assert len(calls["ctor"]) == 2
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_same_label_conflicting_recipe_raises(tmp_path) -> None:
    calls, modules = _patched_fastembed_modules()
    a = tmp_path / "a" / "same-name"
    b = tmp_path / "b" / "same-name"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    with patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        cls(model_name=str(a), dim=384)
        with pytest.raises(ValueError, match="same-name"):
            cls(model_name=str(b), dim=384)

    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_repo_id_never_registers(monkeypatch) -> None:
    # Non-regression: the online path is byte-identical — no registration,
    # no specific_model_path, no offline env mutation.
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    calls, modules = _patched_fastembed_modules()

    with patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        cls(model_name="BAAI/bge-small-en-v1.5", dim=384)

    assert calls["add_custom_model"] == []
    (ctor,) = calls["ctor"]
    assert "specific_model_path" not in ctor
    import os

    assert "HF_HUB_OFFLINE" not in os.environ
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_local_dir_cuda_keeps_gpu_providers(tmp_path) -> None:
    calls, modules = _patched_fastembed_modules()
    model_dir = tmp_path / "bge-small-local"
    model_dir.mkdir()

    with patch.dict(sys.modules, modules):
        cls = _fresh_fastembed_embedder(modules)
        cls(model_name=str(model_dir), dim=384, device="cuda")

    (ctor,) = calls["ctor"]
    assert ctor["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert ctor["specific_model_path"] == str(model_dir)
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
```

Add the needed imports at the top of the test file if absent: `import sys`, `import pytest`, `from unittest.mock import MagicMock, patch`.

Note the idempotence-test caveat: `_REGISTERED_LOCAL_MODELS` lives at module level, and the module is re-imported per test via `sys.modules.pop` — each test starts with a clean registry, which is exactly what the tests assume.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/extraction/strategies/embedders/test_fastembed_embedder.py -q`
Expected: new tests FAIL (`TypeError: unexpected keyword 'pooling'` etc.); the 3 pre-existing tests still pass.

- [ ] **Step 3: Implement** — replace `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py` with:

```python
"""FastEmbedEmbedder — Embedder backed by fastembed.TextEmbedding."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastembed import TextEmbedding  # type: ignore[import-not-found]

from pydocs_mcp.extraction.strategies.embedders.local_source import (
    enable_hf_offline,
    local_model_dir,
)
from pydocs_mcp.models import Embedding

# fastembed's custom-model registry is process-global CLASS state and
# re-registering a label raises ValueError (verified on 0.8.0) — so local-dir
# registrations are memoized here. Keyed by label (directory basename);
# the value is the full recipe so a same-label/different-recipe collision
# fails loudly instead of silently serving the first directory's model.
_REGISTERED_LOCAL_MODELS: dict[str, tuple[str, int, str, bool, str]] = {}


def _register_local_model(
    model_dir: Path,
    *,
    dim: int,
    pooling: str,
    normalize: bool,
    model_file: str,
) -> str:
    """Register a side-loaded model dir with fastembed; return its label."""
    # Lazy import: the submodule is only needed in local mode, and tests mock
    # the fastembed package per the file's existing convention.
    from fastembed.common.model_description import (  # type: ignore[import-not-found]
        ModelSource,
        PoolingType,
    )

    label = model_dir.name
    recipe = (str(model_dir), dim, pooling, normalize, model_file)
    previous = _REGISTERED_LOCAL_MODELS.get(label)
    if previous == recipe:
        return label
    if previous is not None:
        raise ValueError(
            f"Local model label {label!r} is already registered for "
            f"{previous[0]!r} with a different recipe; cannot re-register it "
            f"for {model_dir}. Rename one model directory so labels are unique."
        )
    pooling_types = {
        "mean": PoolingType.MEAN,
        "cls": PoolingType.CLS,
        "disabled": PoolingType.DISABLED,
    }
    # ModelSource requires at least one source (0.8.0 raises on empty), but a
    # local load never consults it: TextEmbedding's download_model()
    # short-circuits on specific_model_path. The label is a harmless dummy.
    TextEmbedding.add_custom_model(
        model=label,
        pooling=pooling_types[pooling],
        normalization=normalize,
        sources=ModelSource(hf=label),
        dim=dim,
        model_file=model_file,
    )
    _REGISTERED_LOCAL_MODELS[label] = recipe
    return label


@dataclass
class FastEmbedEmbedder:
    """Embedder backed by FastEmbed (ONNX-accelerated, no API key).

    Zero-copy from FastEmbed's TextEmbedding.embed() yields straight
    through to our Embedding type — both are np.ndarray (1D, float32).

    When ``model_name`` is a local DIRECTORY (airgap side-load), the folder
    is registered as a fastembed custom model and loaded via
    ``specific_model_path`` — zero network. ``pooling`` / ``normalize`` /
    ``model_file_name`` state the recipe fastembed cannot read from an
    arbitrary ONNX folder; they are ignored on the online repo-id path.
    """

    model_name: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384
    # Execution device — drives the onnxruntime provider list so the same
    # config can run CPU or GPU without code changes.
    device: str = "cpu"
    pooling: str = "mean"
    normalize: bool = True
    model_file_name: str | None = None
    _model: TextEmbedding = field(init=False, repr=False)

    def __post_init__(self) -> None:
        ctor_kwargs: dict[str, Any] = {}
        if self.device == "cuda":
            # CPU listed second as graceful fallback when the GPU runtime
            # is absent (onnxruntime warns and uses CPU rather than crashing).
            ctor_kwargs["providers"] = [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
        model_dir = local_model_dir(self.model_name)
        if model_dir is not None:
            enable_hf_offline()
            label = _register_local_model(
                model_dir,
                dim=self.dim,
                pooling=self.pooling,
                normalize=self.normalize,
                model_file=self.model_file_name or "onnx/model.onnx",
            )
            self._model = TextEmbedding(
                model_name=label,
                specific_model_path=str(model_dir),
                **ctor_kwargs,
            )
            return
        self._model = TextEmbedding(model_name=self.model_name, **ctor_kwargs)

    async def embed_query(self, text: str) -> Embedding:
        results = await asyncio.to_thread(
            lambda: list(self._model.embed([text])),
        )
        # FastEmbed yields np.ndarray (float32, 1D) per document.
        return np.asarray(results[0], dtype=np.float32)

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        results = await asyncio.to_thread(
            lambda: list(self._model.embed(list(texts))),
        )
        return tuple(np.asarray(v, dtype=np.float32) for v in results)


__all__ = ("FastEmbedEmbedder",)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/extraction/strategies/embedders/test_fastembed_embedder.py -q`
Expected: all pass (3 pre-existing + 5 new)

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/fastembed.py tests/extraction/strategies/embedders/test_fastembed_embedder.py
git commit -m "feat(fastembed): airgap local-directory mode via add_custom_model + specific_model_path"
```

---

### Task 4: Factory wiring — pass the recipe; OpenAI + dir fails fast

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/__init__.py:20-31`
- Test: `tests/extraction/strategies/embedders/test_build_embedder.py` (append)

- [ ] **Step 1: Write the failing tests** (append; this file already mocks concrete modules — follow its conventions)

```python
def test_build_fastembed_threads_local_recipe(tmp_path) -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    captured = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_mod = MagicMock()
    fake_mod.FastEmbedEmbedder = _Fake
    with patch.dict(
        sys.modules,
        {"pydocs_mcp.extraction.strategies.embedders.fastembed": fake_mod},
    ):
        from pydocs_mcp.extraction.strategies.embedders import build_embedder

        build_embedder(
            EmbeddingConfig(
                model_name=str(tmp_path),
                pooling="cls",
                normalize=False,
                model_file_name="onnx/model_q.onnx",
            )
        )

    assert captured["model_name"] == str(tmp_path)
    assert captured["pooling"] == "cls"
    assert captured["normalize"] is False
    assert captured["model_file_name"] == "onnx/model_q.onnx"


def test_build_openai_rejects_local_directory(tmp_path) -> None:
    from pydocs_mcp.extraction.strategies.embedders import build_embedder
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    cfg = EmbeddingConfig(provider="openai", model_name=str(tmp_path), dim=1536)
    with pytest.raises(ValueError, match=str(tmp_path)):
        build_embedder(cfg)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/extraction/strategies/embedders/test_build_embedder.py -q`
Expected: the 2 new tests FAIL (recipe kwargs not passed; no ValueError)

- [ ] **Step 3: Implement** — in `__init__.py`, replace the fastembed and openai branches of `build_embedder`:

```python
    if cfg.provider == "fastembed":
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        # pooling / normalize / model_file_name are the local-directory
        # recipe (airgap spec D2); FastEmbedEmbedder ignores them on the
        # online repo-id path.
        return FastEmbedEmbedder(
            model_name=cfg.model_name,
            dim=cfg.dim,
            device=cfg.device,
            pooling=cfg.pooling,
            normalize=cfg.normalize,
            model_file_name=cfg.model_file_name,
        )
    if cfg.provider == "openai":
        from pydocs_mcp.extraction.strategies.embedders.local_source import (
            local_model_dir,
        )
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        # A filesystem path would be sent verbatim as an API model id and
        # fail confusingly server-side — fail here, next to the config
        # (airgap spec D4).
        if local_model_dir(cfg.model_name) is not None:
            raise ValueError(
                f"embedding.provider: openai cannot serve a local model "
                f"directory ({cfg.model_name!r}) — OpenAI embeddings are a "
                "remote API. Use provider: fastembed or "
                "sentence_transformers for side-loaded/airgap models."
            )
        return OpenAIEmbedder(model_name=cfg.model_name, dim=cfg.dim)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/extraction/strategies/embedders/test_build_embedder.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/__init__.py tests/extraction/strategies/embedders/test_build_embedder.py
git commit -m "feat(embedders): thread local recipe to fastembed; reject openai + local dir"
```

---

### Task 5: sentence-transformers + PyLate — offline guard on local dirs

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/sentence_transformers.py:76-81`
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/pylate.py:39-47`
- Test: `tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py` (append)
- Test: `tests/extraction/strategies/embedders/test_pylate_embedder.py` (append)

Both libraries accept a local directory natively (sentence-transformers reads
pooling/prompts from the dir's own config — that's why Qwen3 needs no code
here, spec D3). The only gap is the D5 offline guard so a missing file fails
locally instead of reaching for the Hub.

- [ ] **Step 1: Write the failing tests**

Append to `test_sentence_transformers_embedder.py` (an injected `model`
skips the real load, so the guard must fire *before* the injection check):

```python
def test_local_dir_sets_offline_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    SentenceTransformersEmbedder(model_name=str(tmp_path), dim=8, model=_FakeModel())
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_repo_id_does_not_touch_offline_env(monkeypatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    SentenceTransformersEmbedder(model_name="Qwen/Qwen3-Embedding-0.6B", dim=8, model=_FakeModel())
    import os

    assert "HF_HUB_OFFLINE" not in os.environ
```

Append to `test_pylate_embedder.py` (follow that file's existing fake/mock
convention for `pylate.models`; the assertion pattern is identical):

```python
def test_from_config_local_dir_sets_offline_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    cfg = LateInteractionConfig(enabled=True, model_name=str(tmp_path))
    _build_pylate_with_fake_models(cfg)  # use the file's existing fake-pylate helper/pattern
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "1"
```

(If `test_pylate_embedder.py` has no reusable helper, inline the same
`patch.dict(sys.modules, {"pylate": mock, "pylate.models": mock.models})`
pattern it already uses in its construction tests.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py tests/extraction/strategies/embedders/test_pylate_embedder.py -q`
Expected: the 3 new tests FAIL (`HF_HUB_OFFLINE` not set)

- [ ] **Step 3: Implement**

`sentence_transformers.py` — at the TOP of `__post_init__` (before the
`if self.model is None:` block, so the guard also covers injected-model test
symmetry and, crucially, fires before the lazy `SentenceTransformer` load):

```python
        # Airgap (spec D5): a side-loaded model dir must never fall back to
        # the Hub for a missing file — force HF offline before any load.
        if local_model_dir(self.model_name) is not None:
            enable_hf_offline()
```

with the import added at the top of the file:

```python
from pydocs_mcp.extraction.strategies.embedders.local_source import (
    enable_hf_offline,
    local_model_dir,
)
```

`pylate.py` — in `from_config`, immediately before the `from pylate import
models` line, add the same two import lines at module top and:

```python
        # Airgap (spec D5): see local_source — force HF offline before pylate
        # (sentence-transformers underneath) can attempt a Hub fallback.
        if local_model_dir(cfg.model_name) is not None:
            enable_hf_offline()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/extraction/strategies/embedders/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/sentence_transformers.py python/pydocs_mcp/extraction/strategies/embedders/pylate.py tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py tests/extraction/strategies/embedders/test_pylate_embedder.py
git commit -m "feat(embedders): HF offline guard when ST/pylate load a local model dir"
```

---

### Task 6: Docs — default_config.yaml + README airgap section

**Files:**
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (embedding block, ~line 86)
- Modify: `README.md` (dense-retrieval "Embedder" bullet area, ~line 161)

- [ ] **Step 1: default_config.yaml** — extend the `embedding:` block comment. After the existing block, add:

```yaml
# Airgap / offline: set model_name to a LOCAL DIRECTORY of side-loaded
# weights and nothing is ever downloaded (HF offline is forced). For
# provider: fastembed also state the model's recipe — fastembed cannot
# read it from an arbitrary ONNX folder:
#
#   embedding:
#     provider: fastembed
#     model_name: /opt/models/bge-small-en-v1.5
#     dim: 384
#     pooling: mean            # mean | cls | disabled — MUST match the model
#     normalize: true
#     model_file_name: onnx/model.onnx
#
# provider: sentence_transformers reads pooling/prompts from the model dir
# itself — the right choice for last-token models like Qwen3-Embedding
# (fastembed has no last-token pooling):
#
#   embedding:
#     provider: sentence_transformers
#     model_name: /opt/models/Qwen3-Embedding-0.6B
#     dim: 1024
#
# provider: openai rejects a local path (remote API).
```

- [ ] **Step 2: README.md** — in the dense-retrieval section (after the "Embedder." bullet), add one bullet:

```markdown
- **Air-gapped / offline deployments.** Point `embedding.model_name` at a
  local directory of side-loaded weights (e.g. a `git clone` of the HF repo
  made on a connected machine) and nothing is downloaded — HF offline mode
  is forced, so a missing file fails locally instead of reaching for the
  network. Works for every provider: `fastembed` additionally needs the
  model's recipe in YAML (`pooling`, `normalize`, `model_file_name`) since
  an arbitrary ONNX folder doesn't carry it — and note fastembed pools only
  `mean`/`cls`, so last-token models like Qwen3-Embedding must use
  `provider: sentence_transformers` (which reads the recipe from the model
  directory itself). `openai` rejects a local path. See
  `python/pydocs_mcp/defaults/default_config.yaml` for full examples.
```

- [ ] **Step 3: Commit**

```bash
git add python/pydocs_mcp/defaults/default_config.yaml README.md
git commit -m "docs: airgap local-directory embedding examples (fastembed recipe, ST/Qwen3, openai rejection)"
```

---

### Task 7: Full gates, push, PR

- [ ] **Step 1: Full Python suite**

Run: `.venv/bin/python -m pytest -q`
Expected: everything green (main was 1367 unit tests; plus the ~16 new ones). Investigate ANY failure — do not skip.

- [ ] **Step 2: Lint**

Run: `.venv/bin/ruff check python/ tests/ && .venv/bin/ruff format --check python/ tests/`
Expected: clean. Fix and amend if not.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/airgap-local-embedders
gh pr create --title "feat: airgap local-directory model loading for all embedders" --body "$(cat <<'EOF'
## Summary
- `embedding.model_name` set to a local directory now loads side-loaded weights with ZERO network calls (airgap); repo ids behave byte-for-byte as before
- fastembed: registers the folder via `add_custom_model` (idempotence-guarded) + `specific_model_path` (verified download short-circuit on 0.8.0); recipe stated in YAML (`pooling` [new field], `normalize`, `model_file_name`)
- sentence_transformers / pylate: local dirs pass through natively + forced `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` (setdefault) so a missing file fails locally
- openai + local dir → `ValueError` at build time
- `pooling` folds into the pipeline hash only when non-default (existing conditional-fold pattern — default hashes stable)

Spec: docs/superpowers/specs/2026-07-03-airgap-local-embedders-design.md (rev 2)

## Test plan
- [ ] `pytest -q` green
- [ ] ruff check + format clean
- [ ] All new tests network-free (mocked fastembed / injected models)
EOF
)"
```

---

## Self-review (done at plan-writing time)

- **Spec coverage:** D1→Task 1, D2→Tasks 2+3, D3→Task 5 (ST needs only the guard) + Task 6 docs, D4→Tasks 4+5, D5→Tasks 1/3/5, D6→Task 2. Open item 1 (README)→Task 6.
- **Placeholders:** none — every code step carries the code; the one delegation (pylate test helper) points at a concrete existing pattern in the named file.
- **Type consistency:** `pooling: str` on the dataclass vs `Literal` on the config (config validates, dataclass mirrors ST-embedder convention of plain `str` fields); `model_file_name: str | None` matches config; `local_model_dir(str) -> Path | None` used consistently in Tasks 1/3/4/5.
