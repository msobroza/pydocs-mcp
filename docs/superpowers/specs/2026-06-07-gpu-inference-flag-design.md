# GPU inference flag (`--gpu`) — design

## Goal

Let a user move all embedding inference onto a CUDA GPU (or back to CPU) with a
single CLI flag — `--gpu` — on both the benchmark runner and the `pydocs-mcp`
CLI. No YAML edits required. Minimal, simple code: reuse the device wiring that
already exists for late-interaction, add the missing wiring for the two
single-vector embedders, and route the flag through the config object the
entry points already build.

## Non-goals

- A CUDA-availability probe / auto-detect. `--gpu` is an explicit user choice;
  if the runtime is missing, the backend's own behavior applies (see
  "Availability & fallback"). YAGNI for a detector.
- Per-embedder device selection, multi-GPU device ids, or `--device cuda:1`.
  The surface is boolean `--gpu` → `cuda`, absent → `cpu`. A `device` string
  is the internal representation, so a future `--device` flag is additive.
- Packaging a non-conflicting `[gpu]` extra. GPU runtimes are documented per
  backend (see "Dependencies"), not auto-installed — `fastembed-gpu` and
  `fastembed` conflict, so a single extra is not clean.
- Changing the MCP tool surface. This is a deployment/runtime knob (where
  compute runs), not a retrieval-quality feature — it is exempt from the
  "features go in YAML, not flags" rule precisely because it does not affect
  vector identity or ranking, only latency.

## Decisions (locked during brainstorming)

1. **Mechanism:** a CLI flag, boolean `--gpu`.
2. **Scope:** both entry points — `benchmarks.eval.runner` and the
   `pydocs-mcp` CLI (`serve` + `index`).
3. **Cache:** device is a pure runtime latency knob. It is **excluded from the
   pipeline hash**, so GPU and CPU share the same indexed `.tq` / fast-plaid
   sidecar — flipping `--gpu` never forces a re-index.

## Mechanism (Approach A — post-load config override)

The flag value reaches the embedders through the `AppConfig` object the entry
points already construct. After `AppConfig.load(...)`, a single helper stamps
the chosen device onto both embedder config sections; every embedder then reads
device from its own config (the existing PyLate/FastPlaid pattern).

```
--gpu (argparse, both entry points)
   │  gpu: bool
   ▼
AppConfig.with_device(gpu)            # returns a copy; sets two fields
   ├── embedding.device         = "cuda" | "cpu"   (NEW field)
   └── late_interaction.device  = "cuda" | "cpu"   (existing field)
   ▼
build_embedder(cfg.embedding)                 → FastEmbed / ONNX read cfg.device
build_multi_vector_embedder(cfg.late_interaction) → PyLate reads cfg.device (today)
SqliteCompositeBackend → FastPlaidUnitOfWork(device=cfg.late_interaction.device) (today)
```

The late-interaction path needs **no new wiring** — PyLate
(`pylate.py:49,63`) and `FastPlaidUnitOfWork` (`search_backend.py:184,230`)
already read `late_interaction.device`. The only LI change is removing `device`
from its pipeline hash (decision 3).

## Per-file changes

### 1. `python/pydocs_mcp/retrieval/config.py`

- Add a module-level `_DEFAULT_DEVICE = "cpu"` constant (single source of truth
  per CLAUDE.md §"Default values").
- `EmbeddingConfig`: add `device: Literal["cpu", "cuda"] = _DEFAULT_DEVICE`.
  **Do NOT** add it to `compute_pipeline_hash` (the folded-fields list stays
  `provider | model_name | dim | bit_width | onnx_file | query_instruction`) —
  device must not change vector identity.
- `LateInteractionConfig`: change its default to `_DEFAULT_DEVICE` (shared
  constant) and **remove `self.device`** from `compute_pipeline_hash`'s folded
  list. This is the decision-3 change: it makes the cache device-independent.
- Add `AppConfig.with_device(self, *, gpu: bool) -> AppConfig`: returns a
  `model_copy` with `embedding` and `late_interaction` each `model_copy`'d to
  `device = "cuda" if gpu else "cpu"`. Single helper, single source of the
  bool→string mapping. Pure function, no I/O.

### 2. `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py`

- Add `device: str = _DEFAULT_DEVICE` field to `FastEmbedEmbedder`.
- In `__post_init__`, when `device == "cuda"`, construct
  `TextEmbedding(model_name=..., providers=["CUDAExecutionProvider",
  "CPUExecutionProvider"])`; otherwise the current CPU construction. The CPU
  entry in the providers list is the graceful fallback when the GPU runtime is
  absent (onnxruntime logs a warning and uses CPU).

### 3. `python/pydocs_mcp/extraction/strategies/embedders/onnx.py`

- Add `device: str = _DEFAULT_DEVICE` field to `OnnxEmbedder`.
- Replace the hardcoded `providers=["CPUExecutionProvider"]` (`onnx.py:59`)
  with a device-derived list: `["CUDAExecutionProvider",
  "CPUExecutionProvider"]` when `device == "cuda"`, else
  `["CPUExecutionProvider"]`. Same CPU-fallback property.

### 4. `python/pydocs_mcp/extraction/strategies/embedders/__init__.py`

- `build_embedder`: pass `device=cfg.device` to the `FastEmbedEmbedder(...)` and
  `OnnxEmbedder(...)` branches. The `OpenAIEmbedder` branch is unchanged (a
  remote API has no device). PyLate is already wired via `from_config(cfg)`.

### 5. `benchmarks/src/benchmarks/eval/runner.py`

- Add `--gpu` (`action="store_true"`) to `_build_arg_parser`.
- Thread `gpu: bool` into `run_sweep(...)` and apply it where the per-leg
  config is built: `config = AppConfig.load(explicit_path=cfg_path).with_device(gpu=gpu)`
  (line ~155). The modified config flows into `system.index(dir_, config)` and
  into `_bench_cache.make_key(corpus_dir, config)` — and because device is no
  longer in any pipeline hash, the cache key is identical CPU vs GPU.

### 6. `python/pydocs_mcp/__main__.py`

- Add `--gpu` (`action="store_true"`) to the index/serve subparsers (the loop
  at ~`p.add_parser(cmd)` for serve/index).
- After `config = AppConfig.load(explicit_path=getattr(args, "config", None))`
  (line ~325), apply `config = config.with_device(gpu=getattr(args, "gpu", False))`.

## Availability & fallback

- **FastEmbed / ONNX:** the providers list always ends in
  `CPUExecutionProvider`, so a missing CUDA runtime degrades to CPU with an
  onnxruntime warning — no crash.
- **PyLate (late-interaction):** `models.ColBERT(device="cuda")` raises through
  torch if CUDA is unavailable. This path fails loud, which is acceptable: the
  `[late-interaction]` extra already implies a torch install, and a user passing
  `--gpu` for multi-vector retrieval is asserting they have CUDA. Documented,
  not silently swallowed.

## Dependencies (documentation, not auto-installed)

Actually running on GPU needs the right runtime per backend — documented in
`EXPERIMENTS.md` / `INSTALL.md`, not pulled by a new extra:

- ONNX dense provider → `onnxruntime-gpu` (replaces `onnxruntime`).
- FastEmbed dense → `fastembed-gpu` (replaces `fastembed`; the two conflict, so
  no single `[gpu]` extra).
- PyLate late-interaction → a CUDA build of torch (already via
  `[late-interaction]`).

With the stock CPU runtimes installed, `--gpu` is a safe no-op for the
single-vector path (CPU fallback) and a loud error only for PyLate.

## Testing (TDD)

New / changed unit tests:

- `EmbeddingConfig`: `device` defaults to `"cpu"`, accepts `"cuda"`, rejects
  others (Literal). Two configs differing ONLY in `device` produce the **same**
  `compute_pipeline_hash` (device excluded).
- `LateInteractionConfig`: two configs differing ONLY in `device` produce the
  **same** `compute_pipeline_hash` (new assertion). Note: the existing
  `tests/retrieval/test_late_interaction_pipeline_hash.py` does NOT toggle
  device, so removing device from the hash breaks no current test — this is a
  pure addition, not an inversion.
- `AppConfig.with_device(gpu=True)` sets both `embedding.device` and
  `late_interaction.device` to `"cuda"`; `gpu=False` → `"cpu"`; original
  config object is unmutated (copy semantics).
- `FastEmbedEmbedder` / `OnnxEmbedder`: with `device="cuda"`, the constructed
  session/model receives the CUDA-first providers list (mock `TextEmbedding` /
  `onnxruntime.InferenceSession`); with `"cpu"`, the CPU-only list.
- `build_embedder` passes `cfg.device` into both single-vector branches.
- Runner: `--gpu` parses to `True`; `run_sweep(gpu=True)` yields a config whose
  embedder device is `"cuda"` (assert via a fake system capturing the config).
- `__main__`: `index --gpu` / `serve --gpu` parse and apply the override.

All hermetic — mock the heavy constructors; no GPU required to run the suite.

## Risks

- **Cache semantics change** — removing device from the LI hash means a corpus
  indexed on CPU is reused on GPU and vice-versa. Intended per decision 3; no
  existing test depends on the old behavior (verified).
- **fastembed-gpu / fastembed conflict** — handled by documenting rather than
  shipping a conflicting extra.
