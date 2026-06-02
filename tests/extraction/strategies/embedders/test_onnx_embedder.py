"""OnnxEmbedder tests. Plumbing tests inject a fake session + tokenizer so they
run with no model download; the real-model parity test lives in a later task."""

from __future__ import annotations

import numpy as np
import pytest

from pydocs_mcp.extraction.strategies.embedders.onnx import OnnxEmbedder

_EOS = 151643


class _FakeEnc:
    def __init__(self, ids):
        self.ids = ids


class _FakeTokenizer:
    def encode_batch(self, texts):
        return [_FakeEnc([min(ord(c), 1000) for c in t] + [_EOS]) for t in texts]


class _FakeInput:
    def __init__(self, name, shape, typ):
        self.name, self.shape, self.type = name, shape, typ


class _FakeSession:
    DIM = 8

    def get_inputs(self):
        outs = [
            _FakeInput("input_ids", ["b", "s"], "tensor(int64)"),
            _FakeInput("attention_mask", ["b", "s"], "tensor(int64)"),
            _FakeInput("position_ids", ["b", "s"], "tensor(int64)"),
        ]
        for i in range(2):
            outs.append(
                _FakeInput(f"past_key_values.{i}.key", ["b", 4, "p", 16], "tensor(float16)")
            )
            outs.append(
                _FakeInput(f"past_key_values.{i}.value", ["b", 4, "p", 16], "tensor(float16)")
            )
        return outs

    def run(self, out_names, feed):
        ids = feed["input_ids"]
        bs, L = ids.shape
        lh = np.zeros((bs, L, self.DIM), dtype=np.float32)
        lh[:, :, 0] = ids  # component0 = token id (reveals the pooled token)
        lh[:, :, 1] = np.arange(L)[None, :]  # component1 = position (so pooling-index errors show)
        return [lh]


@pytest.fixture
def emb():
    return OnnxEmbedder(model_name="x", dim=8, session=_FakeSession(), tokenizer=_FakeTokenizer())


async def test_embed_query_pools_last_token_and_normalizes(emb) -> None:
    v = await emb.embed_query("hi")
    assert isinstance(v, np.ndarray) and v.dtype == np.float32 and v.shape == (8,)
    assert np.isclose(np.linalg.norm(v), 1.0, atol=1e-5)
    # last real token is EOS(151643); component0 dominates after norm
    assert v[0] > 0.99
    # Guard the last-REAL-token index: the fake puts position on component 1, so a
    # correctly pooled last token (position = seq_len-1 > 0) has v[1] > 0; pooling
    # index 0 (position 0) would make this exactly 0.
    assert v[1] > 0.0


async def test_pools_last_real_token_not_first(emb) -> None:
    # "hi" -> fake ids [h, i, 151643] at positions [0, 1, 2]; the last real token is
    # index 2. The fake's last_hidden is [id, position, 0...], so the pre-norm pooled
    # vector is [151643, 2, 0, ...]; assert the normalized result matches exactly.
    # This fails if the embedder pools index 0 -> pre-norm [104, 0, 0, ...].
    # Plain doc path (embed_chunks) avoids the instruct prefix so the token sequence
    # is exactly [ord('h'), ord('i'), EOS]. ord('h')=104, ord('i')=105, EOS=151643.
    expected = np.array([151643.0, 2.0] + [0.0] * 6, dtype=np.float32)
    expected /= np.linalg.norm(expected)
    v = (await emb.embed_chunks(["hi"]))[0]
    assert np.allclose(v, expected, atol=1e-5)


async def test_embed_chunks_aligned_and_empty(emb) -> None:
    assert await emb.embed_chunks([]) == ()
    out = await emb.embed_chunks(["a", "bb", "ccc"])
    assert len(out) == 3 and all(x.shape == (8,) for x in out)


async def test_batched_equals_single(emb) -> None:
    one = await emb.embed_chunks(["alpha"])
    two = await emb.embed_chunks(["alpha", "beta gamma delta epsilon"])
    # right-padding + masked last-token pooling must leave row 0 identical
    assert np.allclose(one[0], two[0], atol=1e-6)


def test_format_query_applies_instruct_prefix() -> None:
    e = OnnxEmbedder(
        model_name="x",
        dim=8,
        session=_FakeSession(),
        tokenizer=_FakeTokenizer(),
        query_instruction="Do the thing",
    )
    q = e._format_query("the query")
    assert q.startswith("Instruct: Do the thing")
    assert "\nQuery:the query" in q
