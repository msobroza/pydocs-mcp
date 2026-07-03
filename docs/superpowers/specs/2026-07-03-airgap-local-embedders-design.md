# Airgap / local-path model loading for all embedders — Design

**Date:** 2026-07-03
**Status:** Approved (design review with user)
**Goal:** In an air-gapped environment, every embedder must be able to load its
model from a local directory given in `embedding.model_name` — with **zero
network calls** — while the default online behavior stays byte-for-byte
unchanged.

## Problem

Today no embedder accepts a local filesystem path:

- `FastEmbedEmbedder` forwards only `model_name` to `fastembed.TextEmbedding`,
  which resolves it against fastembed's internal model registry and downloads
  weights on first use.
- `PyLateEmbedder` passes `model_name` to `pylate.models.ColBERT`
  (sentence-transformers), which *would* accept a local dir, but nothing
  guards against a fallback network fetch when a file is missing.
- `OpenAIEmbedder` is a remote API; a local path is meaningless there.

In an airgap deployment the weights are side-loaded onto disk and any attempt
to reach huggingface.co must fail fast and loudly, not hang or retry.

## Decisions

### D1 — Detection: overload `model_name` (no new `model_path` field)

If `Path(model_name).is_dir()` → local mode. Otherwise → current behavior,
untouched.

- **Pros:** zero new YAML surface; one mental model ("model_name is where the
  model lives"); mirrors sentence-transformers' native convention; nothing to
  keep in sync between two fields.
- **Cons:** the pipeline hash folds the *path string*, so two different
  absolute paths to identical weights re-embed (accepted: airgap appliances
  are single-host, path is stable); a typo'd path silently falls through to
  "HF repo id" mode — mitigated because in airgap the download then fails
  immediately (and offline guard, D5, makes it fail locally).
- **Alternatives weighed:** explicit `model_path` field (more explicit, but a
  second field that can contradict `model_name` and needs its own hash
  folding); global `offline: true` flag (doesn't say *where* the model is —
  orthogonal to, and subsumed by, D5's env guard).

Shared helper — single source of truth for all embedders:

```python
# extraction/strategies/embedders/local_source.py
def local_model_dir(model_name: str) -> Path | None:
    """Return the model directory when model_name is a local path, else None."""
```

### D2 — FastEmbed local mode: `add_custom_model` + `specific_model_path`

fastembed cannot load an arbitrary folder: it needs registry metadata
(pooling, normalization, dim, ONNX filename). Local mode therefore:

1. registers the model via `TextEmbedding.add_custom_model(model=<label>,
   pooling=…, normalization=…, dim=cfg.dim, model_file=cfg.model_file,
   sources=ModelSource())` — idempotent (guard against double registration,
   process-global class state);
2. instantiates `TextEmbedding(model_name=<label>,
   specific_model_path=str(dir))`.

**Verified on fastembed 0.8.0:** `ModelManagement.download_model()`
short-circuits `if specific_model_path: return Path(specific_model_path)` —
no HTTP call is ever made.

The registration **label** is the directory basename (e.g.
`bge-small-en-v1.5`); collision between two different dirs with the same
basename in one process is theoretical (one embedder per process) and guarded
by the idempotence check comparing the registered dim.

New YAML fields on `EmbeddingConfig`, consumed **only** in local mode:

```yaml
embedding:
  provider: fastembed
  model_name: /opt/models/bge-small-en-v1.5   # a directory → local mode
  dim: 384
  pooling: mean          # mean | cls | disabled   (default: mean)
  normalization: true    # default: true
  model_file: onnx/model.onnx   # path inside the dir (default)
```

- **Pros:** only sanctioned fastembed extension point (no private-surface
  monkey-patching, survives upgrades); stays on the torch-free ONNX path;
  correct for the BERT-family models (bge et al.) that realistically ship to
  airgap sites.
- **Cons:** user must know the model's recipe (pooling/normalization/dim) —
  wrong pooling degrades recall *silently*; fastembed's pooling menu is
  `CLS|MEAN|DISABLED` only — **no last-token pooling**, hence D3.
- **Alternatives weighed:** subclass fastembed internals to add last-token
  pooling (fragile private API, rejected); bypass fastembed with raw
  onnxruntime for everything (reinvents batching/tokenization fastembed
  already does well for BERT-family).

### D3 — Qwen3 / last-token models: restore `OnnxEmbedder` (revert `f13a667`)

Qwen3-Embedding's ONNX export outputs raw `last_hidden_state`; the correct
pooling is **last-token**, which fastembed cannot express. Forcing it through
D2 with `mean` would run fine and silently produce degraded embeddings — the
worst failure mode for a retrieval system.

The repo already solved this: commit `5c8e9d2` added a torch-free
`OnnxEmbedder` (onnxruntime + tokenizers, last-token pooling + L2 norm,
`provider: onnx`, `onnx_file`, `query_instruction` fields), removed in
`f13a667` solely for "no remaining consumer". Airgap Qwen3 *is* the consumer.

Plan: revert `f13a667` (self-contained 7-file diff incl. tests), then teach
its model resolution the same `local_model_dir` rule (it already loads
`tokenizer.json` + ONNX from a resolved dir — small patch).

- **Pros:** correct pooling, already-tested code from git history, torch-free,
  provider choice now *encodes* the pooling contract instead of guessing.
- **Cons:** 4 providers instead of 3 (`fastembed|onnx|openai` + pylate
  multi-vector) — slightly less uniform story; restores ~380 lines.
- **Alternatives weighed:** fake last-token via fastembed `DISABLED` pooling
  (output is per-token, shape mismatch — doesn't work); document "Qwen3
  unsupported" (fails the stated goal).

### D4 — Per-provider behavior on a local path

| provider | behavior when `model_name` is a directory |
|---|---|
| `fastembed` | D2: register custom + `specific_model_path`, no download |
| `onnx` (restored) | resolve tokenizer/ONNX from the dir, no `hf_hub` call |
| `pylate` (late_interaction) | pass dir through (sentence-transformers native) + D5 guard |
| `openai` | **`ValueError` at construction** — remote API, a local path is a misconfiguration; fail at config/build time next to the offending line |

- **Pros (OpenAI hard error):** consistent with the repo's fail-at-load
  philosophy (`_validate_dim_matches_known_model`, `dim % 8`); silent
  acceptance would send a filesystem path as an API model id and fail
  confusingly server-side.
- **Cons:** none material.

### D5 — Offline hardening: set HF offline env in local mode

When `local_model_dir()` hits, set `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` (via `os.environ.setdefault`) *before* the model
import, so a missing file fails **locally and immediately** instead of
attempting the network.

- **Pros:** belt-and-braces for pylate/sentence-transformers (whose loaders
  otherwise happily fall back to the Hub); makes misconfiguration errors
  airgap-shaped ("file not found") rather than network-shaped (DNS timeout).
- **Cons:** process-wide env mutation — affects any *other* HF loading in the
  same process (accepted: in local mode the whole process is meant to be
  offline; `setdefault` respects an operator's explicit setting).
- **Alternative weighed:** `local_files_only=True` kwarg threading — cleaner
  per-call but not uniformly exposed across fastembed/pylate call chains.

### D6 — Validation interplay

- `_KNOWN_MODEL_DIMS` check: a path is never a known model → auto-skipped
  (existing "custom model" carve-out). User owns `dim`; `dim % 8 == 0`
  (TurboQuant) still enforced.
- `compute_pipeline_hash()`: unchanged — folds `model_name` (the path) plus,
  for fastembed local mode, the new `pooling`/`normalization`/`model_file`
  fields **must** be folded too (they change vector identity). `onnx`
  provider's `onnx_file`/`query_instruction` fold restored with the revert.
- `extra="forbid"` stays; the new fields are proper `EmbeddingConfig` fields
  with defaults, so online configs are untouched.

## Out of scope (YAGNI)

- No separate `model_path` YAML field; no global `airgap: true` flag.
- No auto-detection of pooling/dim by inspecting the ONNX graph.
- No local-path support for the LLM (`llm:`) config — that's a chat API,
  different concern.
- No registry/manifest of side-loaded models.

## Testing

One documented command, no network in any test:

- `local_model_dir`: existing dir → Path; repo id / nonexistent path → None.
- FastEmbed local: `add_custom_model` called with YAML recipe;
  `specific_model_path` passed; idempotent double-build; **no network**
  (assert `download_model` short-circuit via injected fake / monkeypatched
  class, consistent with existing embedder tests).
- FastEmbed online: repo-id config takes the exact pre-change code path
  (non-regression).
- OpenAI + dir → `ValueError` with the path and the reason in the message.
- OnnxEmbedder: revert restores its test suite (`test_onnx_embedder.py`,
  injectable session/tokenizer — already network-free); add one local-dir
  resolution test.
- Pipeline-hash: pooling/normalization/model_file changes alter the fastembed
  hash; batch_size still doesn't.
- Offline env: local mode sets the two env vars; online mode doesn't touch
  them.

## Open items

1. **Qwen3 export check (implementation-time):** confirm the target
   `onnx-community/Qwen3-Embedding-0.6B-ONNX` snapshot outputs
   `last_hidden_state` (as the removed embedder assumed) — if a future export
   bakes pooling into the graph, `OnnxEmbedder` pooling must be conditional.
2. **README:** restore the `onnx` provider section (came out with `f13a667`)
   extended with the local-directory + airgap story and one full YAML example
   per provider.
