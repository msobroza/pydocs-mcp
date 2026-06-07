# GPU Inference Flag (`--gpu`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single boolean `--gpu` CLI flag — on the benchmark runner and the `pydocs-mcp` CLI — that moves all embedder inference (FastEmbed, ONNX, PyLate) onto CUDA, with no YAML edits and no index re-build on toggle.

**Architecture:** A post-load config override. `AppConfig.with_device(gpu=...)` stamps `device` onto `embedding` and `late_interaction`; each embedder reads device from its own config (the pattern PyLate/FastPlaid already use). Device is excluded from every pipeline hash, so GPU and CPU share the index cache.

**Tech Stack:** Python 3.11, pydantic v2 (`BaseModel.model_copy`), pytest, argparse, fastembed/onnxruntime providers, PyLate.

**Spec:** `docs/superpowers/specs/2026-06-07-gpu-inference-flag-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `python/pydocs_mcp/retrieval/config.py` | Config models + device override helper | Add `_DEFAULT_DEVICE`, `EmbeddingConfig.device`, drop device from LI hash, add `AppConfig.with_device` |
| `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py` | FastEmbed embedder | Add `device` field + GPU providers |
| `python/pydocs_mcp/extraction/strategies/embedders/onnx.py` | ONNX embedder | Add `device` field + `_providers_for_device` helper |
| `python/pydocs_mcp/extraction/strategies/embedders/__init__.py` | Embedder factory | Pass `device` into the two single-vector branches |
| `benchmarks/src/benchmarks/eval/runner.py` | Sweep runner | `--gpu` flag → `run_sweep(gpu=...)` → `with_device` |
| `python/pydocs_mcp/__main__.py` | CLI | `--gpu` on serve/index/watch → `with_device` |
| `benchmarks/EXPERIMENTS.md`, `INSTALL.md` | Docs | GPU runtime deps + usage |

---

## Task 1: `_DEFAULT_DEVICE` + `EmbeddingConfig.device` (excluded from hash)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py`
- Test: `tests/retrieval/test_embedding_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/retrieval/test_embedding_config.py`:

```python
def test_embedding_device_defaults_to_cpu_and_accepts_cuda() -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    assert EmbeddingConfig().device == "cpu"
    assert EmbeddingConfig(device="cuda").device == "cuda"


def test_embedding_device_excluded_from_pipeline_hash() -> None:
    """Device is a runtime latency knob, not part of vector identity."""
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    cpu = EmbeddingConfig(device="cpu")
    cuda = EmbeddingConfig(device="cuda")
    assert cpu.compute_pipeline_hash() == cuda.compute_pipeline_hash()


def test_embedding_device_rejects_unknown() -> None:
    import pytest
    from pydantic import ValidationError

    from pydocs_mcp.retrieval.config import EmbeddingConfig

    with pytest.raises(ValidationError):
        EmbeddingConfig(device="tpu")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python pytest tests/retrieval/test_embedding_config.py -k device -v`
Expected: FAIL — `EmbeddingConfig` has no `device` field (`ValidationError: extra` or `AttributeError`).

- [ ] **Step 3: Add the constant + field**

In `python/pydocs_mcp/retrieval/config.py`, add a module-level constant near the other `_DEFAULT_*` constants (e.g. after line ~229 `_DEFAULT_WATCH_DEBOUNCE_MS`):

```python
# Single source of truth for the embedder execution device. Device is a
# runtime latency knob (where inference runs), NOT part of vector identity —
# it is deliberately excluded from every compute_pipeline_hash so GPU and CPU
# share the same index cache. Toggled by the --gpu CLI flag via
# AppConfig.with_device.
_DEFAULT_DEVICE = "cpu"
```

In `EmbeddingConfig` (after the `provider` field at line ~312), add:

```python
    # Execution device for the embedder. NOT folded into
    # compute_pipeline_hash — see _DEFAULT_DEVICE.
    device: Literal["cpu", "cuda"] = _DEFAULT_DEVICE
```

Do NOT touch `compute_pipeline_hash`'s `identity` list — `device` stays out.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=python pytest tests/retrieval/test_embedding_config.py -k device -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/retrieval/test_embedding_config.py
git commit -m "feat(config): EmbeddingConfig.device field (excluded from pipeline hash)"
```

---

## Task 2: Drop `device` from `LateInteractionConfig.compute_pipeline_hash`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (LI hash, line ~461-473)
- Modify: `python/pydocs_mcp/retrieval/config.py` (LI `device` default → `_DEFAULT_DEVICE`, line ~453)
- Test: `tests/retrieval/test_late_interaction_pipeline_hash.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/retrieval/test_late_interaction_pipeline_hash.py`:

```python
def test_li_device_excluded_from_pipeline_hash() -> None:
    """Switching cpu<->cuda must NOT invalidate the LI index cache."""
    from pydocs_mcp.retrieval.config import LateInteractionConfig

    cpu = LateInteractionConfig(enabled=True, device="cpu")
    cuda = LateInteractionConfig(enabled=True, device="cuda")
    assert cpu.compute_pipeline_hash() == cuda.compute_pipeline_hash()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python pytest tests/retrieval/test_late_interaction_pipeline_hash.py -k device -v`
Expected: FAIL — hashes differ (device currently folded in).

- [ ] **Step 3: Remove device from the hash + share the default constant**

In `LateInteractionConfig.compute_pipeline_hash` (line ~461), delete `self.device,` from the `identity` list so it reads:

```python
        identity = "|".join(
            [
                self.provider,
                self.model_name,
                str(self.embedding_dim),
                str(self.document_length),
                str(self.query_length),
                str(self.pool_factor),
            ]
        )
```

Change the `device` field default (line ~453) to use the shared constant:

```python
    device: Literal["cpu", "cuda"] = _DEFAULT_DEVICE
```

- [ ] **Step 4: Run tests to verify pass (new + existing LI hash suite)**

Run: `PYTHONPATH=python pytest tests/retrieval/test_late_interaction_pipeline_hash.py tests/retrieval/test_late_interaction_config.py -v`
Expected: PASS (the new device test passes; existing tests still pass — none asserted device-in-hash).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/retrieval/test_late_interaction_pipeline_hash.py
git commit -m "feat(config): exclude device from LI pipeline hash (cache reuse across cpu/cuda)"
```

---

## Task 3: `AppConfig.with_device(gpu=...)` helper

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (inside `class AppConfig`, after the field declarations)
- Test: `tests/retrieval/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/retrieval/test_config.py`:

```python
def test_with_device_gpu_true_sets_cuda_on_both_embedders() -> None:
    from pydocs_mcp.retrieval.config import AppConfig

    base = AppConfig()
    gpu = base.with_device(gpu=True)

    assert gpu.embedding.device == "cuda"
    assert gpu.late_interaction.device == "cuda"
    # original is unmutated (copy semantics)
    assert base.embedding.device == "cpu"
    assert base.late_interaction.device == "cpu"


def test_with_device_gpu_false_sets_cpu() -> None:
    from pydocs_mcp.retrieval.config import AppConfig

    cpu = AppConfig().with_device(gpu=False)
    assert cpu.embedding.device == "cpu"
    assert cpu.late_interaction.device == "cpu"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python pytest tests/retrieval/test_config.py -k with_device -v`
Expected: FAIL — `AppConfig` has no `with_device` (`AttributeError`).

- [ ] **Step 3: Add the helper**

Inside `class AppConfig` in `python/pydocs_mcp/retrieval/config.py`, add a method (place it after the field declarations, before any existing classmethods like `load`):

```python
    def with_device(self, *, gpu: bool) -> "AppConfig":
        """Return a copy with the embedder execution device set.

        ``--gpu`` maps to ``"cuda"``, absent to ``"cpu"``. Device is a
        runtime latency knob excluded from every pipeline hash (see
        _DEFAULT_DEVICE), so this never invalidates an index cache. Pure
        function — the receiver is unmutated (pydantic ``model_copy``).
        """
        device = "cuda" if gpu else "cpu"
        return self.model_copy(
            update={
                "embedding": self.embedding.model_copy(update={"device": device}),
                "late_interaction": self.late_interaction.model_copy(
                    update={"device": device},
                ),
            },
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=python pytest tests/retrieval/test_config.py -k with_device -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/retrieval/test_config.py
git commit -m "feat(config): AppConfig.with_device(gpu) override helper"
```

---

## Task 4: `FastEmbedEmbedder` device + GPU providers

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py`
- Test: `tests/extraction/strategies/embedders/test_fastembed_embedder.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/extraction/strategies/embedders/test_fastembed_embedder.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python pytest tests/extraction/strategies/embedders/test_fastembed_embedder.py -k "cuda or cpu_omits" -v`
Expected: FAIL — `FastEmbedEmbedder` has no `device` field (`TypeError: unexpected keyword argument 'device'`).

- [ ] **Step 3: Add device field + provider branch**

In `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py`, add the field and update `__post_init__`:

```python
    model_name: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384
    # Execution device. 'cuda' selects the GPU ONNX provider (requires the
    # fastembed-gpu package); 'cpu' keeps the default CPU runtime.
    device: str = "cpu"
    _model: TextEmbedding = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.device == "cuda":
            # CPU listed second as graceful fallback when the GPU runtime
            # is absent (onnxruntime warns and uses CPU rather than crashing).
            self._model = TextEmbedding(
                model_name=self.model_name,
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
        else:
            self._model = TextEmbedding(model_name=self.model_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=python pytest tests/extraction/strategies/embedders/test_fastembed_embedder.py -v`
Expected: PASS (new tests + existing construction test).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/fastembed.py tests/extraction/strategies/embedders/test_fastembed_embedder.py
git commit -m "feat(embedders): FastEmbed GPU support via device field"
```

---

## Task 5: `OnnxEmbedder` device + `_providers_for_device` helper

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/onnx.py`
- Test: `tests/extraction/strategies/embedders/test_onnx_embedder.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/extraction/strategies/embedders/test_onnx_embedder.py`:

```python
def test_onnx_providers_for_device_cuda() -> None:
    from pydocs_mcp.extraction.strategies.embedders.onnx import _providers_for_device

    assert _providers_for_device("cuda") == [
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]


def test_onnx_providers_for_device_cpu() -> None:
    from pydocs_mcp.extraction.strategies.embedders.onnx import _providers_for_device

    assert _providers_for_device("cpu") == ["CPUExecutionProvider"]


def test_onnx_embedder_accepts_device_field() -> None:
    """Device is constructor-settable (injected session skips the download)."""
    from pydocs_mcp.extraction.strategies.embedders.onnx import OnnxEmbedder

    emb = OnnxEmbedder(
        session=_FakeSession(),
        tokenizer=_FakeTokenizer(),
        device="cuda",
    )
    assert emb.device == "cuda"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python pytest tests/extraction/strategies/embedders/test_onnx_embedder.py -k "providers or device" -v`
Expected: FAIL — `_providers_for_device` undefined / `OnnxEmbedder` has no `device` field.

- [ ] **Step 3: Add helper + field + wire into `__post_init__`**

In `python/pydocs_mcp/extraction/strategies/embedders/onnx.py`, add the helper near the top (after `_EOS_ID`):

```python
def _providers_for_device(device: str) -> list[str]:
    """onnxruntime execution providers for the chosen device.

    CUDA-first with a CPU fallback entry so a missing GPU runtime degrades
    to CPU (onnxruntime warns) instead of crashing.
    """
    if device == "cuda":
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]
```

Add the field to `OnnxEmbedder` (after `batch_size`):

```python
    batch_size: int = 32
    # Execution device. 'cuda' adds the CUDA provider (requires
    # onnxruntime-gpu); 'cpu' is the default torch-free runtime.
    device: str = "cpu"
    session: Any = None
    tokenizer: Any = None
```

In `__post_init__`, replace the hardcoded providers (line ~59):

```python
            if self.session is None:
                self.session = ort.InferenceSession(
                    f"{local}/{self.onnx_file}",
                    providers=_providers_for_device(self.device),
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=python pytest tests/extraction/strategies/embedders/test_onnx_embedder.py -v`
Expected: PASS (new tests + existing plumbing tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/onnx.py tests/extraction/strategies/embedders/test_onnx_embedder.py
git commit -m "feat(embedders): ONNX GPU support via device field + provider helper"
```

---

## Task 6: `build_embedder` threads `cfg.device`

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/__init__.py`
- Test: `tests/extraction/strategies/embedders/test_build_embedder.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/extraction/strategies/embedders/test_build_embedder.py`:

```python
def test_build_embedder_passes_device_to_fastembed() -> None:
    import sys
    from unittest.mock import MagicMock, patch

    captured = {}

    def _fake_text_embedding(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = _fake_text_embedding

    from pydocs_mcp.retrieval.config import EmbeddingConfig

    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        from pydocs_mcp.extraction.strategies.embedders import build_embedder

        build_embedder(EmbeddingConfig(provider="fastembed", device="cuda"))

    assert captured["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python pytest tests/extraction/strategies/embedders/test_build_embedder.py -k device -v`
Expected: FAIL — `build_embedder` builds FastEmbed without `device`, so no `providers` captured (`KeyError`).

- [ ] **Step 3: Pass device in both single-vector branches**

In `python/pydocs_mcp/extraction/strategies/embedders/__init__.py`, update `build_embedder`:

```python
    if cfg.provider == "fastembed":
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        return FastEmbedEmbedder(
            model_name=cfg.model_name, dim=cfg.dim, device=cfg.device
        )
```

and the onnx branch:

```python
    if cfg.provider == "onnx":
        from pydocs_mcp.extraction.strategies.embedders.onnx import OnnxEmbedder

        return OnnxEmbedder(
            model_name=cfg.model_name,
            dim=cfg.dim,
            onnx_file=cfg.onnx_file,
            query_instruction=cfg.query_instruction,
            batch_size=cfg.batch_size,
            device=cfg.device,
        )
```

Leave the `openai` branch unchanged (a remote API has no device).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=python pytest tests/extraction/strategies/embedders/test_build_embedder.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/__init__.py tests/extraction/strategies/embedders/test_build_embedder.py
git commit -m "feat(embedders): build_embedder threads device into FastEmbed/ONNX"
```

---

## Task 7: Benchmark runner `--gpu` flag

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/runner.py` (`run_sweep` signature ~line 86-96; config build ~line 155; `_build_arg_parser`; `main`)
- Test: `benchmarks/tests/eval/test_runner_smoke.py`

- [ ] **Step 1: Write the failing test**

Add to `benchmarks/tests/eval/test_runner_smoke.py`:

```python
def test_arg_parser_accepts_gpu_flag() -> None:
    from benchmarks.eval.runner import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(["--configs", "x.yaml", "--gpu"])
    assert args.gpu is True

    args_default = parser.parse_args(["--configs", "x.yaml"])
    assert args_default.gpu is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=benchmarks/src:python pytest benchmarks/tests/eval/test_runner_smoke.py -k gpu -v`
Expected: FAIL — unrecognized argument `--gpu`.

- [ ] **Step 3: Add the flag, thread it, apply the override**

In `benchmarks/src/benchmarks/eval/runner.py`:

(a) Add a `gpu` parameter to `run_sweep` (keyword-only, after `corpus_dir`):

```python
    corpus_dir: Path | None = None,
    gpu: bool = False,
) -> tuple[SweepResults, int]:
```

(b) Apply it where the per-leg config is loaded (line ~155):

```python
        config = AppConfig.load(explicit_path=cfg_path).with_device(gpu=gpu)
```

(c) Add the argparse flag in `_build_arg_parser` (after `--corpus-dir`):

```python
    parser.add_argument(
        "--gpu",
        action="store_true",
        help=(
            "Run embedder inference on CUDA (FastEmbed / ONNX / PyLate). "
            "Requires the matching GPU runtime (onnxruntime-gpu / "
            "fastembed-gpu / CUDA torch). Device is excluded from the index "
            "cache key, so toggling --gpu does NOT trigger a re-index."
        ),
    )
```

(d) Pass it through in `main` (the `asyncio.run(run_sweep(...))` call ~line 745):

```python
                corpus_dir=args.corpus_dir,
                gpu=args.gpu,
            ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=benchmarks/src:python pytest benchmarks/tests/eval/test_runner_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/runner.py benchmarks/tests/eval/test_runner_smoke.py
git commit -m "feat(benchmarks): --gpu flag routes device through AppConfig.with_device"
```

---

## Task 8: `pydocs-mcp` CLI `--gpu` flag

**Files:**
- Modify: `python/pydocs_mcp/__main__.py` (subparser loop ~line 94; config apply ~line 325)
- Test: `tests/test_main_cli.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create/append `tests/test_main_cli.py`:

```python
def test_serve_index_watch_accept_gpu_flag() -> None:
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    for cmd in ("serve", "index", "watch"):
        args = parser.parse_args([cmd, ".", "--gpu"])
        assert args.gpu is True
        args_default = parser.parse_args([cmd, "."])
        assert args_default.gpu is False
```

> `_build_parser()` already exists at `python/pydocs_mcp/__main__.py:48` — no
> refactor needed; just add the flag inside its subparser loop.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python pytest tests/test_main_cli.py -k gpu -v`
Expected: FAIL — unrecognized argument `--gpu` (or missing `_build_parser`).

- [ ] **Step 3: Add the flag + apply the override**

In `python/pydocs_mcp/__main__.py`, inside the `for cmd, hlp in [("serve",...),("index",...),("watch",...)]` loop (after `sp.add_argument("--no-inspect", ...)`), add:

```python
        sp.add_argument(
            "--gpu",
            action="store_true",
            help="Run embedder inference on CUDA. Requires the matching GPU "
            "runtime (onnxruntime-gpu / fastembed-gpu / CUDA torch). Does not "
            "trigger a re-index (device is excluded from the cache key).",
        )
```

Where the config is loaded for index/serve (line ~325):

```python
    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    config = config.with_device(gpu=getattr(args, "gpu", False))
```

`getattr(..., False)` keeps non-index/serve code paths (search/lookup, which
lack `--gpu`) safe.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=python pytest tests/test_main_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/__main__.py tests/test_main_cli.py
git commit -m "feat(cli): --gpu flag on serve/index/watch via AppConfig.with_device"
```

---

## Task 9: Docs — GPU runtime deps + usage

**Files:**
- Modify: `benchmarks/EXPERIMENTS.md` (late-interaction section)
- Modify: `INSTALL.md`

- [ ] **Step 1: Add a GPU note to `INSTALL.md`**

Append a section:

```markdown
## GPU inference (optional)

Pass `--gpu` to `pydocs-mcp serve|index` or to the benchmark runner to run
embedder inference on CUDA. It requires the GPU runtime for whichever embedder
you use (the CPU packages are the default):

- ONNX dense provider: `pip install onnxruntime-gpu` (replaces `onnxruntime`).
- FastEmbed dense: `pip install fastembed-gpu` (replaces `fastembed`; the two
  conflict — install one).
- PyLate late-interaction: a CUDA build of torch (already pulled by the
  `[late-interaction]` extra on a CUDA host).

`--gpu` is a runtime latency knob: it does not change retrieval results and
does not trigger a re-index (device is excluded from the index-cache key). With
the CPU runtimes installed, FastEmbed/ONNX fall back to CPU; only the PyLate
path requires real CUDA.
```

- [ ] **Step 2: Add a usage note to `EXPERIMENTS.md`**

Under the "Late-interaction conditions" run block, append:

```markdown
Add `--gpu` to any runner command to move embedder inference (FastEmbed / ONNX /
PyLate) onto CUDA — no YAML change, no re-index. Needs the matching GPU runtime
(see INSTALL.md §"GPU inference").
```

- [ ] **Step 3: Run the audit grep (no internal jargon)**

Run: `grep -nE "PR #[0-9]+|sub-PR|Task [0-9]+ of" benchmarks/EXPERIMENTS.md INSTALL.md`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add benchmarks/EXPERIMENTS.md INSTALL.md
git commit -m "docs: document --gpu flag and GPU runtime dependencies"
```

---

## Task 10: Full verification

- [ ] **Step 1: Run the full affected suites**

Run:
```bash
PYTHONPATH=python pytest tests/retrieval/test_embedding_config.py \
  tests/retrieval/test_late_interaction_pipeline_hash.py \
  tests/retrieval/test_late_interaction_config.py \
  tests/retrieval/test_config.py \
  tests/extraction/strategies/embedders/ \
  tests/test_main_cli.py -q
PYTHONPATH=benchmarks/src:python pytest benchmarks/tests/eval/test_runner_smoke.py -q
```
Expected: all PASS.

- [ ] **Step 2: Lint**

Run: `ruff check python/ tests/ benchmarks/`
Expected: no errors.

- [ ] **Step 3: Smoke the flag end-to-end (CPU fallback, no GPU needed)**

Run:
```bash
PYTHONPATH=python python -c "from pydocs_mcp.retrieval.config import AppConfig; c=AppConfig().with_device(gpu=True); print(c.embedding.device, c.late_interaction.device)"
```
Expected: `cuda cuda`.

- [ ] **Step 4: Final commit (if any lint fixups)**

```bash
git add -A && git commit -m "chore: lint fixups for --gpu flag" || echo "nothing to commit"
```
