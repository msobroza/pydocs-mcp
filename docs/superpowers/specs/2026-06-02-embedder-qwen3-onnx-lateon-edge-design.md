# Embedder model support: Qwen3-Embedding-0.6B (ONNX) + LateOn-Code-edge (PyLate)

**Status:** Approved design (spike-validated). One PR.

**Goal:** Let pydocs-mcp's embedder layer use two additional models —
`Qwen/Qwen3-Embedding-0.6B` as a torch-free dense embedder served via ONNX, and
`lightonai/LateOn-Code-edge` as a late-interaction (multi-vector) model via the
existing PyLate path — selectable through YAML config, without changing the
shipped defaults.

**Non-goals:** Changing the default embedder (stays `BAAI/bge-small-en-v1.5`);
GPU execution providers; MRL sub-1024 truncation for Qwen3 (noted as future);
adding Qwen3 to FastEmbed's curated list.

---

## 1. Background & spike result

The dense default uses FastEmbed (ONNX, no torch). Two requested models:

- **Qwen3-Embedding-0.6B** — single-vector dense, dim 1024, 32K ctx. It is a
  *causal decoder* using **last-token pooling + L2-norm**, and queries take an
  `Instruct: {task}\nQuery:{q}` prefix (documents plain). FastEmbed's
  `add_custom_model` only offers CLS/MEAN/DISABLED pooling (no last-token) and
  feeds encoder-style inputs, so **FastEmbed's high-level API cannot serve it
  correctly**. sentence-transformers would work but pulls torch.

- **LateOn-Code-edge** — ColBERT multi-vector, 48-dim/token, doc/query length
  2048/256, 17M params. PyLate already loads it; the repo already supports
  PyLate via `late_interaction`.

**Spike (validated, no torch):** running `onnx-community/Qwen3-Embedding-0.6B-ONNX`
(`model_q4f16.onnx`) under **onnxruntime** reproduced the model card's reference
similarity matrix:

```
computed (q4f16 ONNX, onnxruntime):   reference (fp32 sentence-transformers):
 [[0.7462 0.2437]                       [[0.7646 0.1414]
  [0.19   0.6599]]                       [0.1355 0.6000]]
 max abs diff 0.10 — entirely q4f16 quantization; structure exact (diagonal dominates).
```

Spike-pinned implementation facts:

1. The export is a **decoder-with-past** ONNX. Inputs: `input_ids`,
   `attention_mask`, `position_ids`, and `past_key_values.{0..27}.{key,value}`
   (28 layers, shape `[batch, 8, past_len, 128]`, **float16**). Output:
   `last_hidden_state` `[batch, seq, 1024]` (+ `present.*` KV, ignored). A
   single forward pass feeds **empty** KV tensors (`past_len=0`).
2. The tokenizer **already appends `<|endoftext|>` (151643)** as the final
   token; pool that position. Do NOT append `<|im_end|>` (151645) — that was
   the spike's first bug.
3. **Last-token pooling** (`last_hidden_state[i, last_real_index_i]`) → **L2-norm**.
4. **Query asymmetry:** queries get `Instruct: {task}\nQuery:{q}`; documents are
   embedded plain.
5. dim **1024** (%8 ✓ for TurboQuant).
6. Runtime deps — `onnxruntime`, `huggingface_hub`, `tokenizers` — are **already
   in the core ~90 MB install** (pulled by `fastembed`). **No torch, no new pip
   deps.** Model weights download at runtime (q4f16 ≈ 567 MB / fp16 ≈ 1.2 GB),
   like bge-small does today.

---

## 2. Architecture

Two independent components, one PR. Both ride the existing strategy seam: the
`Embedder` / `MultiVectorEmbedder` Protocols (`storage/protocols.py`) +
`build_embedder` / `build_multi_vector_embedder` factories
(`extraction/strategies/embedders/__init__.py`) + the `EmbeddingConfig` /
`LateInteractionConfig` sub-models (`retrieval/config.py`).

### Component A — `OnnxEmbedder` (new dense provider, torch-free)

New file `python/pydocs_mcp/extraction/strategies/embedders/onnx.py`, a concrete
`Embedder` mirroring `FastEmbedEmbedder`'s shape (`@dataclass`, lazy model build
in `__post_init__`, `embed_query` / `embed_chunks` via `asyncio.to_thread`). It:

- Downloads the ONNX repo + tokenizer via `huggingface_hub` (snapshot of the
  chosen `onnx_file` + tokenizer/config files); caches under HF cache.
- Loads `onnxruntime.InferenceSession` (CPUExecutionProvider) and the tokenizer
  via the Rust **`tokenizers`** lib (`Tokenizer.from_file(tokenizer.json)`) — NOT
  `transformers` (keeps it core-dep-only). After encoding, ensure the final
  token id is `<|endoftext|>` (151643); append it if the tokenizer.json
  post-processor doesn't.
- Builds the feed: `input_ids`, `attention_mask`, `position_ids = arange(L)`, and
  28×2 empty `past_key_values.*` tensors (`[batch, 8, 0, 128]`, float16). The KV
  layer count / head dims / dtype are **read from `session.get_inputs()`** at
  construction (don't hard-code 28/8/128 — derive them, so a different export
  still works).
- Runs the session, requests only `last_hidden_state`, does masked **last-token
  pooling** (`attention_mask.sum(axis=1) - 1` per row) + **L2-norm**, returns
  `np.ndarray` (float32, 1D) per text.
- `embed_query(text)` wraps `text` as `Instruct: {query_instruction}\nQuery:{text}`
  before encoding; `embed_chunks(texts)` embeds the raw doc texts. Batches
  `embed_chunks` in `batch_size` slices with **right-padding** (pad id 151643,
  attention_mask 0 on pads; causal model → real-token outputs unaffected;
  `position_ids = arange` broadcast is correct under right-padding).
- `model_name` (for `Package.embedding_model` / stale-embed sweep) = the config
  `model_name`; `dim` = config `dim`.

### Component B — LateOn-Code-edge via PyLate (config-only)

No new code path — `PyLateEmbedder.from_config` already maps
`LateInteractionConfig` → `models.ColBERT(...)`. LateOn-Code-edge is selected by
config values (see §3). The only code-adjacent change is a dependency pin
(`transformers >= 4.57.3`, see §5).

---

## 3. Config changes (`python/pydocs_mcp/retrieval/config.py`)

### `EmbeddingConfig`

- `provider: Literal["fastembed", "openai", "onnx"]` — add `"onnx"`.
- New optional fields (only meaningful for `provider="onnx"`; ignored otherwise):
  - `onnx_file: str = "onnx/model_fp16.onnx"` — which ONNX variant to load
    (fp16 default for fidelity; q4f16/int8 selectable for a smaller download).
  - `query_instruction: str = "Given a web search query, retrieve relevant passages that answer the query"`
    — the task string injected into the query prefix.
- `_KNOWN_MODEL_DIMS`: add `"onnx-community/Qwen3-Embedding-0.6B-ONNX": 1024`.
  (1024 % 8 == 0, so the existing TurboQuant validator passes.)
- `compute_pipeline_hash`: fold `onnx_file` + `query_instruction` into the
  identity string (so changing the ONNX variant or the instruction re-embeds).
  Keep `batch_size` excluded (throughput-only), consistent with today.

`build_embedder` (`embedders/__init__.py`): add the `provider == "onnx"` branch
→ `OnnxEmbedder(model_name=cfg.model_name, dim=cfg.dim, onnx_file=cfg.onnx_file,
query_instruction=cfg.query_instruction, batch_size=cfg.batch_size)`. Update the
`ValueError` "Supported:" message to include `'onnx'`. Lazy import (matches the
fastembed/openai branches — no onnxruntime import at module load).

### `LateInteractionConfig` (no schema change; usage/docs only)

LateOn-Code-edge is enabled by these values (in a YAML overlay / benchmark
config), NOT by changing the defaults:

```yaml
late_interaction:
  enabled: true
  provider: pylate
  model_name: lightonai/LateOn-Code-edge
  embedding_dim: 48
  document_length: 2048
  query_length: 256
```

The defaults stay `LateOn-Code` / 128 / 180 / 32. `compute_pipeline_hash`
already folds `model_name` + `embedding_dim` + lengths, so swapping to the edge
model invalidates caches correctly.

---

## 4. Data flow

- **Index time (documents):** `EmbedChunksStage` → `embedder.embed_chunks(texts)`
  → `OnnxEmbedder` plain-embeds (last-token pool + L2-norm) → vectors persisted
  to the `.tq` sidecar (dense) exactly as today. dim 1024 must match
  `EmbeddingConfig.dim` (validated at config load).
- **Query time:** retrieval calls `embedder.embed_query(q)` → `OnnxEmbedder`
  prepends the `Instruct:` prefix → single 1024-d vector. The dense retrieval
  pipeline is unchanged (it already consumes `embed_query`).
- **LateOn-Code-edge:** identical to the existing LateOn-Code multi-vector flow
  (fast-plaid index via the `[late-interaction]` extra); only the model + dims
  differ.

---

## 5. Dependencies

- **Qwen3 ONNX provider: no new pip deps.** `onnxruntime`, `huggingface_hub`,
  `tokenizers` are already pulled transitively by `fastembed` (core). It stays
  in the light default install; only model *weights* download at runtime.
- **LateOn-Code-edge:** behind the existing `[late-interaction]` extra
  (`pylate`, `fast-plaid`, torch). Bump that extra to require
  `transformers >= 4.57.3` (LateOn-Code-edge's floor; the env currently resolves
  4.48.2). Verify `pylate` 1.5 + `fast-plaid` still import and the
  late-interaction tests pass after the bump. Core CI (no extra) is unaffected.

---

## 6. Error handling

- `OnnxEmbedder.__post_init__`: if `onnxruntime` / `huggingface_hub` /
  `tokenizers` import fails, raise a clear `ImportError` (they're core, so this
  is defensive). If the HF download fails (offline/unreachable), surface the HF
  error unwrapped (matches FastEmbed's behavior).
- Derive KV-input names/shape/dtype from `session.get_inputs()`; if no
  `past_key_values.*` inputs exist (a "without-past" export), skip the empty-KV
  feed — so the provider also works with non-decoder-with-past exports.
- `EmbeddingConfig` validators already fail config-load on dim mismatch (1024)
  and non-%8 dims — no change needed beyond the known-dims entry.

---

## 7. Testing

New `tests/extraction/strategies/embedders/test_onnx_embedder.py`:

- **Numerical-parity guard (the key test):** embed the model card's two queries
  (with the instruct prefix) + two documents via `OnnxEmbedder`, compute the
  query×doc cosine matrix, assert it matches the reference
  `[[0.7646,0.1414],[0.1355,0.6000]]` within a tolerance that admits the chosen
  ONNX variant's quantization (fp16 tight ≈ 0.03; q4f16 ≈ 0.12). Mark
  network/model-download — gate it so CI without the model is skipped (mirror how
  other model-dependent tests guard the FastEmbed download), but it MUST be
  runnable locally and is the acceptance gate.
- **Shape/contract:** `embed_query` returns a 1D float32 `np.ndarray` of len
  `dim`; `embed_chunks([...])` returns a tuple aligned 1:1; empty input → `()`.
- **Query asymmetry:** `embed_query(x)` ≠ `embed_chunks([x])[0]` (prefix applied
  to queries only).
- **Batched == single:** `embed_chunks([a,b])` equals embedding `a` and `b`
  individually (within fp tolerance) — guards the right-padding + masked
  last-token pooling.

`tests/extraction/strategies/embedders/test_build_embedder.py`: add a case that
`build_embedder(EmbeddingConfig(provider="onnx", model_name=..., dim=1024))`
returns an `OnnxEmbedder`; unknown-provider message lists `onnx`.

Config tests: `provider="onnx"` accepted; `_KNOWN_MODEL_DIMS` entry enforces
1024; `compute_pipeline_hash` changes when `onnx_file` / `query_instruction`
change.

LateOn-Code-edge: a config test that the edge YAML values construct a valid
`LateInteractionConfig` (dim 48, lengths 2048/256) and that
`compute_pipeline_hash` differs from the LateOn-Code default. A PyLate
load/parity test is **opt-in** (needs the extra + model download) — gate it like
the existing `test_pylate_embedder.py`.

---

## 8. Acceptance criteria

- [ ] `provider: onnx` + `model_name: onnx-community/Qwen3-Embedding-0.6B-ONNX`
      yields an `OnnxEmbedder` that reproduces the reference similarity matrix
      within the variant's tolerance, using **no torch** (onnxruntime only).
- [ ] Documents embed plain, queries get the instruct prefix; batched embedding
      matches single-text embedding.
- [ ] `late_interaction` config with `model_name: lightonai/LateOn-Code-edge` +
      `embedding_dim: 48` constructs and (with the extra installed) loads via
      PyLate; pipeline hash invalidates on the swap.
- [ ] Default install footprint unchanged (no new core deps; defaults still
      bge-small / LateOn-Code). `[late-interaction]` extra pins
      `transformers >= 4.57.3`.
- [ ] `ruff format --check` + `ruff check` + `mypy python/pydocs_mcp` clean; unit
      + benchmark suites green.

---

## 9. Out of scope / future

- MRL truncation of Qwen3 to <1024 dims (would need truncate-then-renormalize +
  a `dim` other than 1024; defer until a smaller dense vector is wanted).
- GPU `CUDAExecutionProvider` selection for the ONNX provider.
- Wiring these as benchmark `configs/*.yaml` presets (separate follow-up;
  trivial once the providers exist).
