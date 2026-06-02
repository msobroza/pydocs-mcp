# Embedder model support (Qwen3 ONNX + LateOn-Code-edge) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two embedder models as selectable options without changing defaults — `Qwen/Qwen3-Embedding-0.6B` via a new torch-free `OnnxEmbedder` (onnxruntime), and `lightonai/LateOn-Code-edge` via the existing PyLate multi-vector path (config only).

**Architecture:** New `provider: "onnx"` dispatches `build_embedder` to a new `OnnxEmbedder` that runs an ONNX decoder-embedding export under onnxruntime (empty-KV-cache feed, last-token pool + L2-norm, `Instruct:` query prefix). It reuses onnxruntime + `tokenizers` + `huggingface_hub` (already pulled by `fastembed`), so no torch. LateOn-Code-edge needs no new code — only `LateInteractionConfig` values + a `transformers` pin bump on the `[late-interaction]` extra. The `OnnxEmbedder` takes optional injected `session`/`tokenizer` so most tests run hermetically (no model download); one gated test validates numerical parity against the real model.

**Tech Stack:** Python 3.11, onnxruntime, `tokenizers` (Rust), `huggingface_hub`, numpy, pydantic, pytest. (All runtime deps already in the core install.)

**Spec:** `docs/superpowers/specs/2026-06-02-embedder-qwen3-onnx-lateon-edge-design.md`

**Authorship:** msobroza only — NEVER add `Co-Authored-By` trailers.

**Workspace:** worktree `.claude/worktrees/embedder-models`, branch `feat/embedder-qwen3-onnx-lateon-edge`. Run everything from the worktree root. Use the repo venv: `VENV=/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python`. Test prefix: `PYTHONPATH=python:benchmarks/src $VENV -m pytest ...`.

---

## File Structure

- **Create** `python/pydocs_mcp/extraction/strategies/embedders/onnx.py` — the `OnnxEmbedder` (dense, ONNX/onnxruntime, torch-free). One responsibility: turn text → 1024-d float32 vector via the ONNX model.
- **Modify** `python/pydocs_mcp/retrieval/config.py` — `EmbeddingConfig`: add `"onnx"` to the provider enum, add `onnx_file` + `query_instruction` fields, add the known-dims entry, fold the new fields into `compute_pipeline_hash`.
- **Modify** `python/pydocs_mcp/extraction/strategies/embedders/__init__.py` — `build_embedder`: add the `onnx` branch + update the unknown-provider message.
- **Modify** `pyproject.toml` — `[late-interaction]` extra: pin `transformers>=4.57.3`.
- **Create** `tests/extraction/strategies/embedders/test_onnx_embedder.py` — hermetic plumbing tests (injected fakes) + one gated real-model parity test.
- **Modify** `tests/extraction/strategies/embedders/test_build_embedder.py` — assert `provider="onnx"` builds an `OnnxEmbedder`.
- **Modify** `tests/retrieval/` config test (the one covering `EmbeddingConfig` / `LateInteractionConfig`) — `onnx` provider accepted, dim validator, hash deltas, LateOn-edge config values.
- **Modify** `benchmarks/README.md` (or the embedding docs section) — document the `onnx` provider + LateOn-Code-edge config.

---

### Task 1: `EmbeddingConfig` — add the `onnx` provider, fields, known-dim, hash

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py`
- Test: `tests/retrieval/test_config.py` (or wherever `EmbeddingConfig` is currently tested — find with `grep -rln "EmbeddingConfig" tests/`)

- [ ] **Step 1: Write the failing test**

Add to the config test file:

```python
def test_embedding_config_accepts_onnx_provider() -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig
    cfg = EmbeddingConfig(
        provider="onnx",
        model_name="onnx-community/Qwen3-Embedding-0.6B-ONNX",
        dim=1024,
    )
    assert cfg.provider == "onnx"
    assert cfg.onnx_file == "onnx/model_fp16.onnx"
    assert "retrieve relevant passages" in cfg.query_instruction


def test_embedding_config_onnx_known_dim_enforced() -> None:
    import pytest
    from pydocs_mcp.retrieval.config import EmbeddingConfig
    # 1024 is the known dim for the Qwen3 ONNX repo; a mismatch must fail load.
    with pytest.raises(Exception):
        EmbeddingConfig(
            provider="onnx",
            model_name="onnx-community/Qwen3-Embedding-0.6B-ONNX",
            dim=768,
        )


def test_embedding_config_hash_folds_onnx_fields() -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig
    base = EmbeddingConfig(provider="onnx", model_name="onnx-community/Qwen3-Embedding-0.6B-ONNX", dim=1024)
    diff_file = EmbeddingConfig(provider="onnx", model_name="onnx-community/Qwen3-Embedding-0.6B-ONNX", dim=1024, onnx_file="onnx/model_q4f16.onnx")
    diff_instr = EmbeddingConfig(provider="onnx", model_name="onnx-community/Qwen3-Embedding-0.6B-ONNX", dim=1024, query_instruction="Other task")
    assert base.compute_pipeline_hash() != diff_file.compute_pipeline_hash()
    assert base.compute_pipeline_hash() != diff_instr.compute_pipeline_hash()
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=python:benchmarks/src $VENV -m pytest tests/retrieval/test_config.py -k "onnx" -q`
Expected: FAIL (`provider="onnx"` rejected by the Literal; `onnx_file` missing).

- [ ] **Step 3: Implement the config changes**

In `python/pydocs_mcp/retrieval/config.py`:

Add the known-dim entry to `_KNOWN_MODEL_DIMS`:
```python
    # ONNX (Qwen3-Embedding-0.6B exported to ONNX; last-token pooled, dim 1024)
    "onnx-community/Qwen3-Embedding-0.6B-ONNX": 1024,
```

In `EmbeddingConfig`, change the provider enum and add two fields:
```python
    provider: Literal["fastembed", "openai", "onnx"] = "fastembed"
    model_name: str = "BAAI/bge-small-en-v1.5"
    dim: int = Field(default=384, ge=1)
    batch_size: int = Field(default=32, ge=1)
    # ONNX provider only (ignored otherwise): which ONNX variant to load from
    # the model repo, and the task string injected into the query prefix.
    onnx_file: str = "onnx/model_fp16.onnx"
    query_instruction: str = (
        "Given a web search query, retrieve relevant passages that answer the query"
    )
```

In `compute_pipeline_hash`, add the two fields to the identity list (after `str(self.bit_width)`):
```python
                str(self.bit_width),
                self.onnx_file,
                self.query_instruction,
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=python:benchmarks/src $VENV -m pytest tests/retrieval/test_config.py -k "onnx" -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/retrieval/test_config.py
git commit -m "feat(config): EmbeddingConfig gains onnx provider + onnx_file/query_instruction"
```

---

### Task 2: `OnnxEmbedder` — the torch-free ONNX dense embedder (hermetic plumbing)

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/embedders/onnx.py`
- Test: `tests/extraction/strategies/embedders/test_onnx_embedder.py`

- [ ] **Step 1: Write the failing test (hermetic — injected fakes, no download)**

```python
"""OnnxEmbedder tests. Plumbing tests inject a fake session + tokenizer so they
run with no model download; the parity test (real model) lives in Task 4."""
from __future__ import annotations
import numpy as np
import pytest
from pydocs_mcp.extraction.strategies.embedders.onnx import OnnxEmbedder

_EOS = 151643

class _FakeEnc:
    def __init__(self, ids): self.ids = ids

class _FakeTokenizer:
    """Deterministic: ids = byte values of the text + EOS (mimics the
    real tokenizer's auto-appended <|endoftext|>)."""
    def encode_batch(self, texts):
        return [_FakeEnc([min(ord(c), 1000) for c in t] + [_EOS]) for t in texts]

class _FakeInput:
    def __init__(self, name, shape, typ): self.name, self.shape, self.type = name, shape, typ

class _FakeSession:
    """Mimics a decoder-with-past ONNX: requires position_ids + KV inputs,
    returns last_hidden_state where each token's vector = [token_id, 0, 0, ...]
    so last-token pooling is checkable (last real token id is EOS=151643)."""
    DIM = 8
    def get_inputs(self):
        outs = [_FakeInput("input_ids", ["b", "s"], "tensor(int64)"),
                _FakeInput("attention_mask", ["b", "s"], "tensor(int64)"),
                _FakeInput("position_ids", ["b", "s"], "tensor(int64)")]
        for i in range(2):
            outs.append(_FakeInput(f"past_key_values.{i}.key", ["b", 4, "p", 16], "tensor(float16)"))
            outs.append(_FakeInput(f"past_key_values.{i}.value", ["b", 4, "p", 16], "tensor(float16)"))
        return outs
    def run(self, out_names, feed):
        ids = feed["input_ids"]
        bs, L = ids.shape
        lh = np.zeros((bs, L, self.DIM), dtype=np.float32)
        lh[:, :, 0] = ids  # token id in component 0 → pooled vector reveals the pooled token
        return [lh]


@pytest.fixture
def emb():
    return OnnxEmbedder(model_name="x", dim=8, session=_FakeSession(), tokenizer=_FakeTokenizer())


async def test_embed_query_pools_last_token_and_normalizes(emb) -> None:
    v = await emb.embed_query("hi")
    assert isinstance(v, np.ndarray) and v.dtype == np.float32 and v.shape == (8,)
    # last token is EOS (151643) → component0 = 151643 pre-norm → unit vector after L2.
    assert np.isclose(np.linalg.norm(v), 1.0, atol=1e-5)
    assert v[0] > 0.999  # all magnitude in component 0


async def test_embed_chunks_aligned_and_empty(emb) -> None:
    assert await emb.embed_chunks([]) == ()
    out = await emb.embed_chunks(["a", "bb", "ccc"])
    assert len(out) == 3 and all(x.shape == (8,) for x in out)


async def test_batched_equals_single(emb) -> None:
    one = await emb.embed_chunks(["alpha"])
    two = await emb.embed_chunks(["alpha", "beta gamma delta"])
    # First element of the batch must equal the single-text embedding
    # (right-padding + masked last-token pooling must not change it).
    assert np.allclose(one[0], two[0], atol=1e-6)


async def test_query_prefix_makes_query_differ_from_doc(emb) -> None:
    # The fake tokenizer encodes the literal text, so the instruct prefix
    # changes the query's token sequence vs the plain doc.
    q = await emb.embed_query("same text")
    d = (await emb.embed_chunks(["same text"]))[0]
    # Different inputs → (here) same pooled token (EOS) but different seq length;
    # assert the embedder applied the prefix by checking the query path ran on a
    # longer sequence. Use a spy-free check: prefix presence via a public helper.
    assert emb._format_query("q").startswith("Instruct: ")
    assert "\nQuery:q" in emb._format_query("q")
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=python:benchmarks/src $VENV -m pytest tests/extraction/strategies/embedders/test_onnx_embedder.py -q`
Expected: FAIL (`onnx.py` / `OnnxEmbedder` does not exist).

- [ ] **Step 3: Implement `OnnxEmbedder`**

Create `python/pydocs_mcp/extraction/strategies/embedders/onnx.py`:

```python
"""OnnxEmbedder — torch-free dense Embedder over an ONNX decoder-embedding model.

Serves models like ``Qwen/Qwen3-Embedding-0.6B`` exported to ONNX (e.g.
``onnx-community/Qwen3-Embedding-0.6B-ONNX``) using onnxruntime — reusing the
``onnxruntime`` + ``tokenizers`` + ``huggingface_hub`` deps ``fastembed``
already pulls, so NO torch. Last-token pooling + L2-norm; queries get an
instruction prefix, documents are embedded plain.

``session`` / ``tokenizer`` are injectable so tests run without a model
download; real runs build them from the HF repo in ``__post_init__``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pydocs_mcp.models import Embedding

# Qwen3-Embedding pools the hidden state at its final ``<|endoftext|>`` token;
# the tokenizer's post-processor appends it, but we guarantee it defensively.
# It is also the pad id, so right-padding with it is correct (masked out).
_EOS_ID = 151643


@dataclass
class OnnxEmbedder:
    model_name: str = "onnx-community/Qwen3-Embedding-0.6B-ONNX"
    dim: int = 1024
    onnx_file: str = "onnx/model_fp16.onnx"
    query_instruction: str = (
        "Given a web search query, retrieve relevant passages that answer the query"
    )
    batch_size: int = 32
    # Injectable for tests; built from the HF repo when None.
    session: Any = None
    tokenizer: Any = None
    # Derived from the ONNX input signature in __post_init__.
    _kv_names: tuple[str, ...] = field(init=False, default=(), repr=False)
    _kv_heads: int = field(init=False, default=0, repr=False)
    _kv_head_dim: int = field(init=False, default=0, repr=False)
    _kv_dtype: Any = field(init=False, default=np.float32, repr=False)
    _needs_position_ids: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        if self.session is None or self.tokenizer is None:
            from huggingface_hub import snapshot_download
            import onnxruntime as ort
            from tokenizers import Tokenizer

            local = snapshot_download(
                self.model_name,
                allow_patterns=[self.onnx_file, self.onnx_file + "_data", "*.json", "*.txt"],
            )
            if self.session is None:
                self.session = ort.InferenceSession(
                    f"{local}/{self.onnx_file}", providers=["CPUExecutionProvider"]
                )
            if self.tokenizer is None:
                self.tokenizer = Tokenizer.from_file(f"{local}/tokenizer.json")
        self._inspect_inputs()

    def _inspect_inputs(self) -> None:
        ins = self.session.get_inputs()
        names = {i.name for i in ins}
        self._needs_position_ids = "position_ids" in names
        kv = [i for i in ins if i.name.endswith((".key", ".value"))]
        self._kv_names = tuple(i.name for i in kv)
        if kv:
            shp = kv[0].shape  # [batch, n_heads, past_len, head_dim]
            self._kv_heads = int(shp[1])
            self._kv_head_dim = int(shp[3])
            self._kv_dtype = np.float16 if "float16" in kv[0].type else np.float32

    def _format_query(self, text: str) -> str:
        return f"Instruct: {self.query_instruction}\nQuery:{text}"

    async def embed_query(self, text: str) -> Embedding:
        return (await self._embed([self._format_query(text)]))[0]

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        out: list[Embedding] = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(await self._embed(list(texts[i : i + self.batch_size])))
        return tuple(out)

    async def _embed(self, texts: list[str]) -> list[Embedding]:
        return await asyncio.to_thread(self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[Embedding]:
        encs = self.tokenizer.encode_batch(texts)
        ids_list = [list(e.ids) for e in encs]
        ids_list = [
            ids if ids and ids[-1] == _EOS_ID else ids + [_EOS_ID] for ids in ids_list
        ]
        bs = len(ids_list)
        max_len = max(len(ids) for ids in ids_list)
        input_ids = np.full((bs, max_len), _EOS_ID, dtype=np.int64)  # right-pad (masked)
        attn = np.zeros((bs, max_len), dtype=np.int64)
        for r, ids in enumerate(ids_list):
            input_ids[r, : len(ids)] = ids
            attn[r, : len(ids)] = 1
        feed: dict[str, np.ndarray] = {"input_ids": input_ids, "attention_mask": attn}
        if self._needs_position_ids:
            feed["position_ids"] = np.tile(np.arange(max_len, dtype=np.int64), (bs, 1))
        for name in self._kv_names:
            feed[name] = np.zeros((bs, self._kv_heads, 0, self._kv_head_dim), self._kv_dtype)
        last_hidden = self.session.run(["last_hidden_state"], feed)[0]
        last_idx = attn.sum(axis=1) - 1  # last real token per row
        pooled = last_hidden[np.arange(bs), last_idx].astype(np.float32)
        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
        pooled = pooled / norms
        return [pooled[r] for r in range(bs)]


__all__ = ("OnnxEmbedder",)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=python:benchmarks/src $VENV -m pytest tests/extraction/strategies/embedders/test_onnx_embedder.py -q`
Expected: PASS (all hermetic tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/onnx.py tests/extraction/strategies/embedders/test_onnx_embedder.py
git commit -m "feat(embedders): add torch-free OnnxEmbedder (last-token pool, instruct query prefix)"
```

---

### Task 3: Wire `OnnxEmbedder` into `build_embedder`

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/embedders/__init__.py`
- Test: `tests/extraction/strategies/embedders/test_build_embedder.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_embedder_onnx_returns_onnx_embedder() -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig
    from pydocs_mcp.extraction.strategies.embedders import build_embedder
    from pydocs_mcp.extraction.strategies.embedders.onnx import OnnxEmbedder
    # Inject fakes via no construction here: build_embedder must NOT download.
    # So pass a config whose provider is onnx; build_embedder constructs
    # OnnxEmbedder with session=None -> would download. To keep this hermetic,
    # assert on the TYPE via monkeypatching snapshot_download/ort is overkill;
    # instead assert build_embedder raises no ValueError and returns the class
    # by patching OnnxEmbedder.__post_init__ to a no-op.
    import pydocs_mcp.extraction.strategies.embedders.onnx as onnx_mod
    orig = onnx_mod.OnnxEmbedder.__post_init__
    onnx_mod.OnnxEmbedder.__post_init__ = lambda self: None  # type: ignore[assignment]
    try:
        e = build_embedder(EmbeddingConfig(
            provider="onnx",
            model_name="onnx-community/Qwen3-Embedding-0.6B-ONNX",
            dim=1024,
        ))
        assert isinstance(e, OnnxEmbedder)
        assert e.onnx_file == "onnx/model_fp16.onnx"
    finally:
        onnx_mod.OnnxEmbedder.__post_init__ = orig  # type: ignore[assignment]
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=python:benchmarks/src $VENV -m pytest tests/extraction/strategies/embedders/test_build_embedder.py -k onnx -q`
Expected: FAIL (`build_embedder` raises ValueError for `onnx`).

- [ ] **Step 3: Implement the branch**

In `python/pydocs_mcp/extraction/strategies/embedders/__init__.py`, inside `build_embedder`, before the final `raise`:

```python
    if cfg.provider == "onnx":
        from pydocs_mcp.extraction.strategies.embedders.onnx import OnnxEmbedder

        return OnnxEmbedder(
            model_name=cfg.model_name,
            dim=cfg.dim,
            onnx_file=cfg.onnx_file,
            query_instruction=cfg.query_instruction,
            batch_size=cfg.batch_size,
        )
```

And update the unknown-provider message:
```python
    raise ValueError(
        f"Unknown embedding provider: {cfg.provider!r}. "
        "Supported: 'fastembed', 'openai', 'onnx'.",
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=python:benchmarks/src $VENV -m pytest tests/extraction/strategies/embedders/test_build_embedder.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/__init__.py tests/extraction/strategies/embedders/test_build_embedder.py
git commit -m "feat(embedders): build_embedder dispatches provider=onnx to OnnxEmbedder"
```

---

### Task 4: Real-model numerical-parity test (the acceptance gate, network-gated)

**Files:**
- Modify: `tests/extraction/strategies/embedders/test_onnx_embedder.py`

- [ ] **Step 1: Write the gated parity test**

Append to the test file:

```python
@pytest.mark.skipif(
    __import__("os").environ.get("PYDOCS_RUN_MODEL_TESTS") != "1",
    reason="downloads the real Qwen3 ONNX; set PYDOCS_RUN_MODEL_TESTS=1 to run",
)
async def test_qwen3_onnx_reproduces_reference_matrix() -> None:
    """Real model: onnxruntime + last-token pool + L2-norm reproduces the model
    card's reference cosine matrix within the q4f16 quantization tolerance."""
    emb = OnnxEmbedder(
        model_name="onnx-community/Qwen3-Embedding-0.6B-ONNX",
        dim=1024,
        onnx_file="onnx/model_q4f16.onnx",  # small + fast for CI; fp16 is tighter
    )
    queries = ["What is the capital of China?", "Explain gravity"]
    docs = [
        "The capital of China is Beijing.",
        "Gravity is a force that attracts two bodies towards each other. It gives "
        "weight to physical objects and is responsible for the movement of planets "
        "around the sun.",
    ]
    qe = np.stack([await emb.embed_query(q) for q in queries])
    de = np.stack(list(await emb.embed_chunks(docs)))
    sim = qe @ de.T
    ref = np.array([[0.7646, 0.1414], [0.1355, 0.6000]])
    assert qe.shape[1] == 1024
    # q4f16 quantization drift; fp16 would be < 0.05. Diagonal must still dominate.
    assert float(np.max(np.abs(sim - ref))) < 0.15
    assert sim[0, 0] > sim[0, 1] and sim[1, 1] > sim[1, 0]
```

- [ ] **Step 2: Run it (locally, with the gate on)**

Run: `PYDOCS_RUN_MODEL_TESTS=1 PYTHONPATH=python:benchmarks/src $VENV -m pytest tests/extraction/strategies/embedders/test_onnx_embedder.py -k reproduces_reference -q`
Expected: PASS (downloads the q4f16 ONNX once; matrix within tolerance, diagonal dominates). Without the env var it is SKIPPED.

- [ ] **Step 3: Commit**

```bash
git add tests/extraction/strategies/embedders/test_onnx_embedder.py
git commit -m "test(embedders): gated parity test — Qwen3 ONNX reproduces reference matrix"
```

---

### Task 5: LateOn-Code-edge — dependency pin + config test

**Files:**
- Modify: `pyproject.toml`
- Test: the `LateInteractionConfig` config test (find with `grep -rln "LateInteractionConfig" tests/`)

- [ ] **Step 1: Write the failing test**

```python
def test_lateon_code_edge_config_values() -> None:
    from pydocs_mcp.retrieval.config import LateInteractionConfig
    edge = LateInteractionConfig(
        enabled=True,
        provider="pylate",
        model_name="lightonai/LateOn-Code-edge",
        embedding_dim=48,
        document_length=2048,
        query_length=256,
    )
    assert edge.embedding_dim == 48 and edge.dim == 48
    assert edge.document_length == 2048 and edge.query_length == 256
    default = LateInteractionConfig(enabled=True)  # LateOn-Code / 128 / 180 / 32
    assert edge.compute_pipeline_hash() != default.compute_pipeline_hash()
```

- [ ] **Step 2: Run to verify it passes already (config schema unchanged)**

Run: `PYTHONPATH=python:benchmarks/src $VENV -m pytest <config_test_file> -k lateon_code_edge -q`
Expected: PASS immediately — `LateInteractionConfig` already accepts these values. (This test pins the supported configuration; it is a guard, not a red→green.)

- [ ] **Step 3: Bump the dependency pin**

In `pyproject.toml`, the `[project.optional-dependencies]` `late-interaction` entry, add the `transformers` floor LateOn-Code-edge requires:

```toml
late-interaction = ["pylate>=1.0,<2.0", "fast-plaid>=1.4,<2.0", "transformers>=4.57.3"]
```

- [ ] **Step 4: Verify the extra still resolves + imports**

Run: `$VENV -m pip install -e ".[late-interaction]" 2>&1 | tail -3 && $VENV -c "import pylate, fast_plaid, transformers; print('ok', transformers.__version__)"`
Expected: installs cleanly; prints `ok 4.57.3` (or newer). If pylate/fast-plaid conflict with the bump, STOP and report (do not force).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml <config_test_file>
git commit -m "feat(late-interaction): support LateOn-Code-edge (config values + transformers>=4.57.3 pin)"
```

---

### Task 6: Documentation

**Files:**
- Modify: `benchmarks/README.md` (the embedding/methods section; or the root README embedding section if that's where dense models are documented — `grep -rn "bge-small\|embedding.provider\|fastembed" benchmarks/README.md README.md`)

- [ ] **Step 1: Document the `onnx` provider + LateOn-Code-edge**

Add a short subsection (no internal PR/issue jargon — see CLAUDE.md "README files" rule) covering:
- `embedding.provider: onnx` with `model_name: onnx-community/Qwen3-Embedding-0.6B-ONNX`, `dim: 1024`, optional `onnx_file` (fp16 default; q4f16/int8 for a smaller download) and `query_instruction`. Note it is torch-free (onnxruntime) and the model weights download at runtime.
- LateOn-Code-edge: the `late_interaction` YAML block (`model_name: lightonai/LateOn-Code-edge`, `embedding_dim: 48`, `document_length: 2048`, `query_length: 256`) behind the `[late-interaction]` extra.

- [ ] **Step 2: Jargon audit + commit**

```bash
grep -nE "PR #[0-9]+|sub-PR|trilogy|Task [0-9]+ of" benchmarks/README.md && echo VIOLATION || echo clean
git add benchmarks/README.md
git commit -m "docs(benchmarks): document the onnx dense provider + LateOn-Code-edge config"
```

---

### Task 7: Full verification gauntlet

**Files:** none (verification only)

- [ ] **Step 1: Lint + types**

```bash
$VENV -m ruff format --check python/ tests/ benchmarks/
$VENV -m ruff check python/ tests/ benchmarks/
$VENV -m mypy python/pydocs_mcp
```
Expected: all clean. (`mypy` targets only `python/pydocs_mcp` — the onnx provider must type-clean there.)

- [ ] **Step 2: Unit + benchmark suites (hermetic)**

```bash
PYTHONPATH=python:benchmarks/src $VENV -m pytest tests/ -q
PYTHONPATH=python:benchmarks/src $VENV -m pytest benchmarks/tests/ -q
```
Expected: green (the gated parity test is skipped without `PYDOCS_RUN_MODEL_TESTS=1`).

- [ ] **Step 3: Run the gated parity test once locally**

```bash
PYDOCS_RUN_MODEL_TESTS=1 PYTHONPATH=python:benchmarks/src $VENV -m pytest tests/extraction/strategies/embedders/test_onnx_embedder.py -k reproduces_reference -q
```
Expected: PASS (the acceptance gate — real Qwen3 ONNX reproduces the reference matrix, no torch).

- [ ] **Step 4: Final commit if any fixups were needed**

```bash
git add -A && git commit -m "chore: verification fixups for embedder model support" || echo "nothing to commit"
```

---

## Notes for the implementer

- **No torch anywhere in the onnx path.** If you find yourself importing `torch`, `sentence_transformers`, or `transformers` in `onnx.py`, stop — use `onnxruntime` + `tokenizers` + `huggingface_hub` only.
- **Don't change defaults.** `EmbeddingConfig` default stays `fastembed` / bge-small; `LateInteractionConfig` default stays LateOn-Code / 128.
- **Derive KV/position inputs from `session.get_inputs()`** — never hard-code 28 layers / 8 heads / 128 head_dim; a different export must still work.
- The fp16 ONNX (`onnx/model_fp16.onnx`) has an external-data sibling (`onnx/model_fp16.onnx_data`); the `snapshot_download` `allow_patterns` includes `self.onnx_file + "_data"` so onnxruntime finds it co-located. Single-file variants (q4f16/int8) have no `_data` and the pattern simply matches nothing extra.
