# Airgap / local-path model loading for all embedders — Design

**Date:** 2026-07-03 (rev 2 — rebased on main v0.4.0)
**Status:** Approved (design review with user; rev 2 supersedes the D3 decision)
**Goal:** In an air-gapped environment, every embedder must be able to load its
model from a local directory given in `embedding.model_name` — with **zero
network calls** — while the default online behavior stays byte-for-byte
unchanged.

> **Rev 2 note:** rev 1 was written against the `quality-gate-calibrated`
> branch. This branch is based on `origin/main` (v0.4.0), which already ships
> a `sentence_transformers` provider (torch, defaults to
> `Qwen/Qwen3-Embedding-0.6B`, model-agnostic pooling/prompting, `backend:
> torch|onnx|openvino`, `model_file_name`, `normalize`). That **obsoletes rev
> 1's D3** (restore `OnnxEmbedder` via revert of `f13a667`): Qwen3 with
> correct last-token pooling is already served, and sentence-transformers
> accepts a local directory natively. D3 is replaced below.

## Problem

Today no embedder treats `model_name` as a local filesystem path:

- `FastEmbedEmbedder` forwards `model_name` to `fastembed.TextEmbedding`,
  which resolves it against fastembed's internal model registry and downloads
  weights on first use.
- `SentenceTransformersEmbedder` passes `model_name` to
  `SentenceTransformer(...)`, which *does* accept a local dir — but nothing
  guards against a fallback network fetch when a file is missing, and nothing
  documents/tests the airgap contract.
- `PyLateEmbedder` passes `model_name` to `pylate.models.ColBERT`
  (sentence-transformers underneath) — same situation.
- `OpenAIEmbedder` is a remote API; a local path is meaningless there.

In an airgap deployment the weights are side-loaded onto disk and any attempt
to reach huggingface.co must fail fast and loudly, not hang or retry.

## Decisions

### D1 — Detection: overload `model_name` (no new `model_path` field)

If `Path(model_name).is_dir()` → local mode. Otherwise → current behavior,
untouched.

- **Pros:** zero new YAML surface for the common case; one mental model
  ("model_name is where the model lives"); mirrors sentence-transformers'
  native convention; nothing to keep in sync between two fields.
- **Cons:** the pipeline hash folds the *path string*, so two different
  absolute paths to identical weights re-embed (accepted: airgap appliances
  are single-host, path is stable); a typo'd path silently falls through to
  "HF repo id" mode — mitigated because in airgap the download then fails
  immediately (and the offline guard, D5, makes it fail locally).
- **Alternatives weighed:** explicit `model_path` field (more explicit, but a
  second field that can contradict `model_name` and needs its own hash
  folding); global `offline: true` flag (doesn't say *where* the model is —
  orthogonal to, and subsumed by, D5's env guard).

Shared helper — single source of truth for all embedders:

```python
# extraction/strategies/embedders/local_source.py
def local_model_dir(model_name: str) -> Path | None:
    """Return the model directory when model_name is a local path, else None."""

def enable_hf_offline() -> None:
    """setdefault HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 (D5)."""
```

### D2 — FastEmbed local mode: `add_custom_model` + `specific_model_path`

fastembed cannot load an arbitrary folder: it needs registry metadata
(pooling, normalization, dim, ONNX filename). Local mode therefore:

1. registers the model via `TextEmbedding.add_custom_model(model=<label>,
   pooling=…, normalization=…, dim=cfg.dim, model_file=…,
   sources=ModelSource())` — idempotent (module-level guard keyed by label,
   since registration is process-global class state; same label with
   *different* params raises);
2. instantiates `TextEmbedding(model_name=<label>,
   specific_model_path=str(dir))` (CUDA providers list preserved when
   `device: cuda`).

**Verified on fastembed 0.8.0 (installed):** signature
`add_custom_model(model, pooling: PoolingType, normalization: bool, sources:
ModelSource, dim, model_file='onnx/model.onnx', …)`;
`ModelManagement.download_model()` short-circuits
`if specific_model_path: return Path(specific_model_path)` — no HTTP call.

The registration **label** is the directory basename (e.g.
`bge-small-en-v1.5`); a same-basename collision between two different dirs in
one process is guarded by the idempotence check.

YAML: **reuse the existing fields** `normalize` (already on
`EmbeddingConfig`) and `model_file_name` (already exists for the ST backend
export file — same meaning here: which file inside the model dir; `None` →
fastembed's default `onnx/model.onnx`). **One new field:** `pooling`.

```yaml
embedding:
  provider: fastembed
  model_name: /opt/models/bge-small-en-v1.5   # a directory → local mode
  dim: 384
  pooling: mean            # mean | cls | disabled   (NEW; default mean; read only in fastembed local mode)
  normalize: true          # existing field
  model_file_name: onnx/model.onnx   # existing field; None → this default
```

- **Pros:** only sanctioned fastembed extension point (no private-surface
  monkey-patching, survives upgrades); stays on the torch-free ONNX path —
  the only local option when the airgap host cannot ship torch; reuses two
  existing YAML fields, adds one.
- **Cons:** user must know the model's recipe (pooling/normalize/dim) — wrong
  pooling degrades recall *silently*; fastembed's pooling menu is
  `CLS|MEAN|DISABLED` only — **no last-token pooling**, so Qwen3-class models
  must NOT go through this provider (that's D3's job; README must say so).
- **Alternatives weighed:** subclass fastembed internals to add last-token
  pooling (fragile private API, rejected); bypass fastembed with raw
  onnxruntime (reinvents batching/tokenization fastembed already does well
  for BERT-family).

### D3 (rev 2) — Qwen3 / last-token models: `provider: sentence_transformers` + local dir

Qwen3-Embedding needs last-token pooling, which fastembed cannot express.
Main already ships `SentenceTransformersEmbedder` — sentence-transformers
reads the pooling/prompt config **from the model directory itself**, so
pooling is always correct by construction, online or local. Local mode is
therefore just D1 detection + D5 offline hardening around the existing
provider — no new embedder code.

```yaml
embedding:
  provider: sentence_transformers
  model_name: /opt/models/Qwen3-Embedding-0.6B   # a directory → local mode
  dim: 1024
```

- **Pros:** zero new pooling logic to own (the model dir's own ST config is
  the source of truth — the "provider encodes the pooling contract" concern
  from rev 1 dissolves); already tested/merged on main; also covers any other
  ST-format local model.
- **Cons:** requires the `sentence-transformers` extra (torch, ~1-5 GB) on
  the airgap host — heavier side-load than rev 1's onnxruntime-only
  `OnnxEmbedder`. Accepted: torch wheels side-load fine, and D2 remains the
  torch-free path for BERT-family models. If a torch-free Qwen3 path is ever
  demanded, rev 1's revert plan (`f13a667`) is documented history.
- **Alternatives weighed:** restore `OnnxEmbedder` (rev 1's D3 — now
  redundant code to own for a solved problem); fake last-token via fastembed
  `DISABLED` (per-token output shape, doesn't work).

### D4 — Per-provider behavior on a local path

| provider | behavior when `model_name` is a directory |
|---|---|
| `fastembed` | D2: register custom + `specific_model_path`, no download |
| `sentence_transformers` | pass dir through (ST-native) + D5 offline guard |
| `pylate` (late_interaction block) | pass dir through (ST-native) + D5 offline guard |
| `openai` | **`ValueError` in `build_embedder`** — remote API, a local path is a misconfiguration; fail at build time with the path in the message |

- **Pros (OpenAI hard error):** consistent with the repo's fail-at-load
  philosophy (`_validate_dim_matches_known_model`, `dim % 8`); silent
  acceptance would send a filesystem path as an API model id and fail
  confusingly server-side.
- **Cons:** none material.
- **Alternative weighed:** pydantic validator on `EmbeddingConfig` (even
  earlier failure, but makes config parsing filesystem-dependent — rejected
  to keep the config model pure).

### D5 — Offline hardening: set HF offline env in local mode

When `local_model_dir()` hits, call `enable_hf_offline()` —
`os.environ.setdefault("HF_HUB_OFFLINE", "1")` and
`setdefault("TRANSFORMERS_OFFLINE", "1")` — *before* the model library
import/construction, so a missing file fails **locally and immediately**
instead of attempting the network.

- **Pros:** belt-and-braces for ST/pylate (whose loaders otherwise fall back
  to the Hub); makes misconfiguration errors airgap-shaped ("file not
  found") rather than network-shaped (DNS timeout).
- **Cons:** process-wide env mutation — affects any *other* HF loading in the
  same process (accepted: in local mode the whole process is meant to be
  offline; `setdefault` respects an operator's explicit setting, including
  an explicit `HF_HUB_OFFLINE=0`).
- **Alternative weighed:** threading `local_files_only=True` kwargs — cleaner
  per-call but not uniformly exposed across fastembed/ST/pylate call chains.

### D6 — Validation and hash interplay

- `_KNOWN_MODEL_DIMS` check: a path is never a known model → auto-skipped
  (existing "custom model" carve-out). User owns `dim`; `dim % 8 == 0`
  (TurboQuant) still enforced.
- `compute_pipeline_hash()`: `model_name` (the path) and `normalize` already
  fold; `model_file_name` already folds when non-`None`. **New:** `pooling`
  folds only when non-default (`mean`) — same conditional-append pattern as
  `backend`/`model_file_name`, keeping every pre-existing config's hash
  byte-identical.
- `extra="forbid"` stays; `pooling` is a proper `EmbeddingConfig` field with
  a default, inert outside fastembed local mode (same documented pattern as
  the ST-only fields).

## Out of scope (YAGNI)

- No separate `model_path` YAML field; no global `airgap: true` flag.
- No auto-detection of pooling/dim by inspecting the ONNX graph.
- No local-path support for the LLM (`llm:`) config — chat API, different
  concern.
- No registry/manifest of side-loaded models.
- No `OnnxEmbedder` restoration (rev 1 D3 — superseded).

## Testing

All tests network-free, following the existing embedder-test conventions
(`sys.modules` patching for fastembed, injectable `model` for ST/pylate):

- `local_model_dir`: existing dir → `Path`; repo id / nonexistent path →
  `None`. `enable_hf_offline`: sets both vars; respects pre-set values.
- FastEmbed local: `add_custom_model` called with the YAML recipe;
  `specific_model_path` passed; CUDA providers preserved; idempotent
  double-build; same-label different-params raises; online repo-id config
  never calls `add_custom_model` (non-regression).
- ST local: dir passed through unchanged; offline env set; online path
  untouched (env not set).
- PyLate local: same two assertions via `from_config`.
- OpenAI + dir → `ValueError` naming the path and the fix.
- Config: `pooling` accepted/validated; hash unchanged for default `pooling`;
  hash changes for `pooling: cls`; `batch_size`/`device` still excluded.

## Open items

1. **README/DOCUMENTATION airgap section:** one full YAML example per
   provider (fastembed/bge local, sentence_transformers/Qwen3 local), the
   "Qwen3 must use sentence_transformers, not fastembed" pooling warning, and
   the side-loading note (`git clone` of the HF repo or `huggingface-cli
   download` on a connected machine, then copy).
