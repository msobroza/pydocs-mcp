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
# It is also the pad id, so right-padding with it is correct (it is masked out).
_EOS_ID = 151643


def _providers_for_device(device: str) -> list[str]:
    """onnxruntime execution providers for the chosen device.

    CUDA-first with a CPU fallback entry so a missing GPU runtime degrades
    to CPU (onnxruntime warns) instead of crashing.
    """
    if device == "cuda":
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


@dataclass
class OnnxEmbedder:
    model_name: str = "onnx-community/Qwen3-Embedding-0.6B-ONNX"
    dim: int = 1024
    onnx_file: str = "onnx/model_fp16.onnx"
    query_instruction: str = (
        "Given a web search query, retrieve relevant passages that answer the query"
    )
    batch_size: int = 32
    # Execution device — selects the onnxruntime provider list so the same
    # config can run CPU or GPU without code changes.
    device: str = "cpu"
    session: Any = None
    tokenizer: Any = None
    _kv_names: tuple[str, ...] = field(init=False, default=(), repr=False)
    _kv_heads: int = field(init=False, default=0, repr=False)
    _kv_head_dim: int = field(init=False, default=0, repr=False)
    _kv_dtype: Any = field(init=False, default=np.float32, repr=False)
    _needs_position_ids: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        if self.session is None or self.tokenizer is None:
            import onnxruntime as ort
            from huggingface_hub import snapshot_download
            from tokenizers import Tokenizer

            local = snapshot_download(
                self.model_name,
                allow_patterns=[self.onnx_file, self.onnx_file + "_data", "*.json", "*.txt"],
            )
            if self.session is None:
                self.session = ort.InferenceSession(
                    f"{local}/{self.onnx_file}",
                    providers=_providers_for_device(self.device),
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
        ids_list = [ids if ids and ids[-1] == _EOS_ID else [*ids, _EOS_ID] for ids in ids_list]
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
        last_idx = attn.sum(axis=1) - 1
        pooled = last_hidden[np.arange(bs), last_idx].astype(np.float32)
        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
        pooled = pooled / norms
        return [pooled[r] for r in range(bs)]


__all__ = ("OnnxEmbedder",)
