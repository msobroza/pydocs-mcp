"""Embedding / LLM / late-interaction config sub-models + pipeline hashes.

Every ``compute_pipeline_hash`` lives here — the vector-identity story has
one file. ``_DEFAULT_DEVICE`` lives here too because device is an embedder
runtime knob deliberately excluded from every hash (see its WHY comment).
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Single source of truth for the embedder execution device. Device is a
# runtime latency knob (where inference runs), NOT part of vector identity —
# it is deliberately excluded from every compute_pipeline_hash so GPU and CPU
# share the same index cache. Toggled by the --gpu CLI flag via
# AppConfig.with_device.
_DEFAULT_DEVICE = "cpu"


# Known-model dim lookup. Add entries as the model-selection follow-up
# PR locks in benchmarked models. Models not in this table skip the
# check (caller is on the hook).
_KNOWN_MODEL_DIMS: dict[str, int] = {
    # FastEmbed
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    # OpenAI (text-embedding-3-* default dims; can be reduced via .dimensions)
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # sentence-transformers (torch / GPU-reliable Qwen3-Embedding)
    "Qwen/Qwen3-Embedding-0.6B": 1024,
    "Alibaba-NLP/gte-modernbert-base": 768,
    "codefuse-ai/F2LLM-v2-0.6B": 1024,
    # F2LLM-v2-330M: Qwen3-0.6B-Base backbone, hidden_size 896, last-token
    # pooled + L2-normalized, no projection module → output dim 896.
    "codefuse-ai/F2LLM-v2-330M": 896,
    # Mistral codestral-embed (code-specialized) served via an OpenAI-
    # compatible endpoint (e.g. OpenRouter). Default output dimension is
    # 1536 (reducible up to 3072 via Mistral's own output_dimension, which
    # we do NOT drive — see EmbeddingConfig.send_dimensions).
    "mistralai/codestral-embed-2505": 1536,
    # Qwen3-Embedding-4B served via an OpenAI-compatible endpoint (OpenRouter
    # id "qwen/qwen3-embedding-4b"). Native output dimension is 2560 (the
    # model supports Matryoshka truncation, which we do NOT drive — see
    # send_dimensions). The on-device 0.6B sibling is Qwen/Qwen3-Embedding-0.6B.
    "qwen/qwen3-embedding-4b": 2560,
    # Qwen3-Embedding-8B (OpenRouter id "qwen/qwen3-embedding-8b"). Native
    # output dimension is 4096 — the largest of the Qwen3-Embedding family.
    "qwen/qwen3-embedding-8b": 4096,
}


# Query-embedding cache defaults (single source of truth — the YAML
# restatement in default_config.yaml is the sanctioned user-facing copy).
_DEFAULT_QUERY_CACHE_ENABLED = True
_DEFAULT_QUERY_CACHE_MAX_ENTRIES = 512
_DEFAULT_QUERY_CACHE_TTL_SECONDS = 0.0  # 0 = entries never expire by age
# Late-interaction entries are per-token matrices (query_length × dim) —
# ~30-60× larger than one pooled vector — so the LI cache defaults to a
# smaller LRU. Every other default is shared with QueryCacheConfig.
_DEFAULT_LI_QUERY_CACHE_MAX_ENTRIES = 128


class QueryCacheConfig(BaseModel):
    """Query-embedding result cache + singleflight coalescing tunables.

    Consumed by ``wrap_query_cache`` at the composition roots — never
    surfaced as an MCP param or CLI flag (CLAUDE.md §"MCP API surface vs
    YAML configuration"). ``ttl_seconds`` exists only as a memory-hygiene
    escape hatch: embeddings are deterministic per identity, so age-based
    expiry buys correctness nothing.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=_DEFAULT_QUERY_CACHE_ENABLED)
    max_entries: int = Field(default=_DEFAULT_QUERY_CACHE_MAX_ENTRIES, ge=1)
    ttl_seconds: float = Field(default=_DEFAULT_QUERY_CACHE_TTL_SECONDS, ge=0.0)


class EmbeddingConfig(BaseModel):
    """Embedding + vector-quantization config (spec §5.10).

    YAML-tunable; no MCP tool params (per CLAUDE.md §"MCP API surface
    vs YAML configuration").
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["fastembed", "openai", "sentence_transformers"] = "fastembed"
    # Execution device. NOT folded into compute_pipeline_hash — see _DEFAULT_DEVICE.
    device: Literal["cpu", "cuda"] = _DEFAULT_DEVICE
    model_name: str = "BAAI/bge-small-en-v1.5"
    dim: int = Field(default=384, ge=1)
    batch_size: int = Field(default=32, ge=1)
    # WHY: max_seq_length / normalize / query_prompt_name apply to the on-device
    # torch provider (``sentence_transformers``). They are inert for fastembed /
    # openai — those providers never read them — but they live on the shared
    # embedding block so a single block stays the source of truth.
    # ``max_seq_length`` caps the token sequence (attention is O(seq^2) — the
    # OOM guard); ``None`` inherits the embedder's own default so the cap stays
    # single-sourced in the embedder class. ``normalize`` toggles L2-normalized
    # output. ``query_prompt_name`` selects an asymmetric query prompt (``None``
    # = use whatever prompt the model itself defines).
    max_seq_length: int | None = Field(default=None, ge=1)
    normalize: bool = True
    query_prompt_name: str | None = None
    # ``backend`` / ``model_file_name`` are likewise sentence_transformers-only
    # (inert for fastembed / openai). ``backend`` selects the ST inference
    # runtime: ``torch`` (default), ``onnx``, or ``openvino`` — the latter two
    # enable fast CPU inference, typically ~2-4x with a qint8-quantized file.
    # ``model_file_name`` picks a specific exported weight file inside the HF
    # repo (e.g. ``openvino/openvino_model_qint8_quantized.xml`` or
    # ``onnx/model_qint8_avx512.onnx``); ``None`` uses the backend's default
    # export. Non-torch backends need the matching ST extra installed
    # (``sentence-transformers[openvino]`` / ``[onnx]``). Both fields fold into
    # compute_pipeline_hash ONLY when non-default — quantization changes the
    # produced vectors, but defaults must keep existing index hashes stable.
    backend: Literal["torch", "onnx", "openvino"] = "torch"
    model_file_name: str | None = None
    # ``pooling`` is read ONLY by the fastembed provider in LOCAL-directory
    # mode (airgap spec D2): fastembed's custom-model registration needs the
    # pooling recipe stated explicitly because an arbitrary ONNX folder does
    # not carry it. Inert for every other provider / online fastembed (same
    # inert-field pattern as the sentence_transformers-only knobs above).
    # fastembed 0.8.0 offers exactly CLS | MEAN | DISABLED — notably NO
    # last-token pooling, so Qwen3-class models must use
    # provider: sentence_transformers instead (spec D3).
    pooling: Literal["mean", "cls", "disabled"] = "mean"
    # ``base_url`` / ``api_key_env`` / ``send_dimensions`` are the openai
    # provider's OpenAI-COMPATIBLE-ENDPOINT knobs (inert for fastembed /
    # sentence_transformers — those providers never read them).
    #   base_url        points the client at any OpenAI-shaped
    #                   /v1/embeddings service; None = api.openai.com.
    #                   e.g. https://openrouter.ai/api/v1 for Mistral
    #                   codestral-embed.
    #   api_key_env     names the env var holding the key; None = the SDK
    #                   default OPENAI_API_KEY. Lets a non-OpenAI key
    #                   (OPENROUTER_API_KEY) stay in the environment instead
    #                   of a committed YAML.
    #   send_dimensions toggles OpenAI's Matryoshka ``dimensions`` request
    #                   param: True (default) preserves the text-embedding-3-*
    #                   behavior; set False for endpoints that reject it and
    #                   serve their own native/default dimension (codestral).
    base_url: str | None = None
    api_key_env: str | None = None
    send_dimensions: bool = True
    # TurboQuant scalar-quantization bit width. 4 is the sweet spot per
    # turbovec README — ~16x compression with minimal recall loss on
    # 384-1536 dim embeddings. Tune up to 8 for higher quality, down to
    # 2 for max compression.
    bit_width: int = Field(default=4, ge=1, le=8)
    # Selective embedding for dependencies (indexing-latency control; big deps
    # like torch carry ~50k code chunks and embedding them all takes ~an hour
    # on CPU). Everything stays FTS/BM25-indexed regardless; this only decides
    # which chunks get dense vectors:
    #   doc_pages (default) — dependency docstring pages + markdown/README only
    #   full               — every dependency chunk (pre-v12 behavior)
    #   none               — dependencies are BM25-only
    # NOT folded into compute_pipeline_hash: the per-package tier is folded
    # into chunk content hashes instead (AssignChunkContentHashStage), so a
    # policy change re-embeds only the packages whose tier actually changed.
    dependency_policy: Literal["doc_pages", "full", "none"] = "doc_pages"
    # Dependencies promoted to the project-grade `full` tier — exact PyPI names
    # or fnmatch globs ("internal-*"). CLI `--full-dep NAME` merges into this.
    full_index_dependencies: list[str] = Field(default_factory=list)
    # Serve-time query-embedding cache + singleflight tunables. Deliberately
    # NOT folded into compute_pipeline_hash: a cache setting changes no
    # stored document vector, so toggling it must never force a reindex.
    query_cache: QueryCacheConfig = Field(default_factory=QueryCacheConfig)

    @field_validator("dim")
    @classmethod
    def _validate_dim_multiple_of_8(cls, v: int) -> int:
        # WHY: turbovec's ``IdMapIndex`` asserts ``dim % 8 == 0`` (see
        # turbovec/src/lib.rs). Without this validator, a YAML setting
        # like ``embedding.dim: 100`` would load fine and only blow up
        # at first write — far from the misconfiguration. Failing at
        # config-load surfaces it next to the offending line.
        if v % 8 != 0:
            raise ValueError(
                f"embedding.dim={v} must be a multiple of 8 (TurboQuant "
                "IdMapIndex constraint). Common values: 384, 512, 768, "
                "1024, 1536, 3072."
            )
        return v

    @model_validator(mode="after")
    def _validate_dim_matches_known_model(self) -> EmbeddingConfig:
        # WHY: without this check, setting ``model_name: BAAI/bge-base-en-v1.5``
        # (768-dim) while leaving ``dim=384`` (the shipped default) silently
        # produces a corrupt vector store at query time — every embedded
        # vector lands in a column dimensioned for the wrong model. Failing
        # at config-load time surfaces the misconfiguration immediately.
        # Unknown models skip the check so custom / locally-finetuned models
        # remain usable (caller is on the hook for matching dim).
        expected = _KNOWN_MODEL_DIMS.get(self.model_name)
        if expected is not None and self.dim != expected:
            raise ValueError(
                f"embedding.dim={self.dim} does not match the known "
                f"dimension of {self.model_name!r} (expected {expected}). "
                "Either set dim to the model's native dimension or "
                "remove the model from the known-dims lookup if you "
                "intend a custom configuration."
            )
        return self

    @model_validator(mode="after")
    def _validate_backend_device(self) -> EmbeddingConfig:
        # WHY: sentence-transformers' OpenVINO backend runs on CPU/iGPU and
        # does not understand torch's "cuda" device string — failing at
        # config-load time beats a cryptic backend error at first embed.
        # (onnx + cuda stays permissive: onnxruntime-gpu is a real setup.)
        if self.backend == "openvino" and self.device == "cuda":
            raise ValueError(
                "The OpenVINO backend (embedding.backend: openvino) runs on "
                "CPU/iGPU and is incompatible with device: cuda. Keep device: "
                "cpu (drop --gpu), or use backend: torch/onnx for CUDA."
            )
        return self

    def compute_pipeline_hash(self) -> str:
        """SHA-256 of embedder fields that affect vector identity.

        ``batch_size`` and ``device`` are deliberately excluded — they affect
        throughput / latency, not vector contents. The folded fields are
        ``provider``, ``model_name``, ``dim``, ``bit_width``, and the on-device
        torch knobs ``max_seq_length`` / ``normalize`` (both change the produced
        vectors — a tighter token cap truncates long chunks differently, and
        toggling normalization changes magnitudes — so they must invalidate the
        chunk-cache when edited). ``query_prompt_name`` is NOT folded: it only
        shapes the query-time embedding, never the stored document vectors.
        Pipe-separated to keep the hash input human-readable in a debugger; the
        field set is small enough that no escaping is required (``provider`` /
        ``bit_width`` / ``max_seq_length`` / ``normalize`` are bounded enums /
        ints / bools, and ``model_name`` cannot legally contain a pipe).

        ``backend`` / ``model_file_name`` / ``pooling`` fold in ONLY when
        non-default: a non-torch backend or a quantized weight file changes
        the produced document vectors (qint8 outputs differ from full
        precision), and a wrong pooling recipe produces entirely different
        vectors from the same ONNX graph — so setting any of them must
        invalidate the chunk cache. The conditional append keeps the hash
        byte-identical for every pre-existing config (the "default install
        hash is stable" invariant, same pattern as the late-interaction fold
        in ``ingestion_pipeline_hash``).
        """
        parts = [
            self.provider,
            self.model_name,
            str(self.dim),
            str(self.bit_width),
            str(self.max_seq_length),
            str(self.normalize),
        ]
        if self.backend != "torch":
            parts.append(f"backend:{self.backend}")
        if self.model_file_name is not None:
            parts.append(f"file:{self.model_file_name}")
        if self.pooling != "mean":
            parts.append(f"pooling:{self.pooling}")
        # send_dimensions changes the produced document vectors when the
        # endpoint's native dimension differs from ``dim`` (dropping the
        # Matryoshka request yields the model's full-precision vector), so
        # opting out must invalidate the chunk cache. Conditional append
        # keeps every pre-existing config's hash byte-identical.
        # base_url / api_key_env are endpoint/credential knobs — they never
        # change vector contents for the same model, so (like ``device``)
        # they are excluded from the hash entirely.
        if not self.send_dimensions:
            parts.append("send_dimensions:false")
        identity = "|".join(parts)
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def compute_query_identity_hash(self) -> str:
        """Identity of QUERY-time embeddings (the query-cache key component).

        ``compute_pipeline_hash`` deliberately excludes ``query_prompt_name``
        because it only shapes query vectors, never stored document vectors
        (see its docstring). A query-embedding cache is the dual case: the
        prompt name CHANGES the query vector (the sentence_transformers
        provider injects it as ``prompt_name`` at encode time), so it MUST
        be folded in here — reusing ``compute_pipeline_hash`` verbatim would
        wrongly serve cached query vectors across a prompt change. ``device``
        stays excluded (numerically equivalent output) and ``query_cache``
        settings stay excluded from BOTH hashes (a cache tunable is not part
        of vector identity).
        """
        base = self.compute_pipeline_hash()
        prompt = self.query_prompt_name or ""
        return hashlib.sha256(f"{base}|query_prompt={prompt}".encode()).hexdigest()[:16]


class LlmConfig(BaseModel):
    """LLM chat-completion client configuration.

    Architectural twin of ``EmbeddingConfig`` — same shape (provider,
    model_name, tuning params), used by ``build_llm_client(cfg)`` to
    construct the right concrete client. Defaults selected for cost
    efficiency: gpt-4o-mini is OpenAI's cheap-but-capable model and the
    right baseline for a retrieval re-ranking step where calls are
    frequent but small.
    """

    provider: Literal["openai"] = "openai"
    model_name: str = "gpt-4o-mini"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    api_key: str | None = None  # None -> SDK reads OPENAI_API_KEY env var


class LateInteractionConfig(BaseModel):
    """Late-interaction (ColBERT / PyLate) embedder config.

    Sibling of :class:`EmbeddingConfig` / :class:`LlmConfig`. Defaults to
    ``enabled=False`` — opt-in. Consumed by
    ``build_multi_vector_embedder(cfg)`` (lazy import of pylate) and
    folded into ``ingestion_pipeline_hash`` when the active ingestion
    pipeline references ``embed_chunks_multi_vector``.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: Literal["pylate"] = "pylate"
    model_name: str = "lightonai/LateOn-Code"
    embedding_dim: int = Field(default=128, ge=1)
    document_length: int = Field(default=180, ge=8)
    query_length: int = Field(default=32, ge=4)
    pool_factor: int = Field(default=1, ge=1)
    # Execution device. NOT folded into compute_pipeline_hash — see _DEFAULT_DEVICE.
    device: Literal["cpu", "cuda"] = _DEFAULT_DEVICE
    # Serve-time multi-vector query cache. Same shape as
    # ``embedding.query_cache`` but LI-sized (see the WHY on
    # _DEFAULT_LI_QUERY_CACHE_MAX_ENTRIES). NOT folded into
    # compute_pipeline_hash — a cache tunable never changes stored vectors.
    query_cache: QueryCacheConfig = Field(
        default_factory=lambda: QueryCacheConfig(max_entries=_DEFAULT_LI_QUERY_CACHE_MAX_ENTRIES)
    )

    @property
    def dim(self) -> int:
        # ``dim`` reads naturally in embedder code; ``embedding_dim``
        # matches PyLate's kwarg. The property keeps both surfaces alive
        # without storing the same value twice.
        return self.embedding_dim

    def compute_pipeline_hash(self) -> str:
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
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()
