"""Blob-write + canonical-JSON parity across the packaging boundary.

The eval-side ``blob_store`` re-implements the product recorder's convention
independently (zero import coupling — ADR 0009/0010). The format is the
contract, so identical bytes MUST yield identical blob names and identical
canonical JSON on both sides. This test is the pin that keeps them in lockstep.
"""

from __future__ import annotations

import pytest

from pydocs_eval.trajectory.blob_store import canonical_json, write_result_blob

# The product-side writer is the parity oracle; skip cleanly if the library
# extra isn't installed (the base eval install keeps a zero-pydocs floor).
product_writer = pytest.importorskip("pydocs_mcp.observability.trace_writer")


@pytest.mark.parametrize(
    "data",
    [b"", b"x", b"hello world", "café — unicode".encode(), b'{"text":"a","items":[]}'],
)
def test_identical_bytes_yield_identical_blob_name(tmp_path, data) -> None:
    eval_dir = tmp_path / "eval_blobs"
    product_dir = tmp_path / "product_blobs"
    eval_name = write_result_blob(eval_dir, data)
    product_name = product_writer.write_result_blob(product_dir, data)
    assert eval_name == product_name
    assert (eval_dir / eval_name).read_bytes() == (product_dir / product_name).read_bytes() == data


@pytest.mark.parametrize(
    "payload",
    [
        {"b": 1, "_event": "x"},
        {"text": "hi", "items": [], "meta": {"truncated": False}},
        {"z": "café", "a": [3, 2, 1]},
    ],
)
def test_canonical_json_matches_product(payload) -> None:
    assert canonical_json(payload) == product_writer.canonical_trace_json(payload)
