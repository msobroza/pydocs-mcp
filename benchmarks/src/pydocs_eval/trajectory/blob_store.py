"""Eval-side content-addressed blob store + canonical JSON (ADR 0010).

Deliberately a byte-for-byte re-implementation of the product recorder's
``blobs/<sha256-hex>`` convention (``pydocs_mcp.observability.trace_writer``):
the format is the contract, NOT shared code, because the eval package keeps a
zero-``pydocs_mcp``-import floor and vice versa (ADR 0009 placement). A parity
test pins identical bytes → identical blob names across the boundary; if you
touch either convention, that test must stay green.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path


def canonical_json(payload: Mapping[str, object]) -> str:
    """Canonical one-line JSON: sorted keys, no spaces, non-ASCII preserved.

    Sorted keys make identical dicts byte-identical regardless of insertion
    order (R6 determinism); ``default=str`` keeps serialization from crashing
    on an exotic value. Mirrors ``trace_writer.canonical_trace_json`` exactly.

    Example:
        >>> canonical_json({"b": 1, "_event": "x"})
        '{"_event":"x","b":1}'
    """
    return json.dumps(
        dict(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def write_result_blob(blobs_dir: Path, data: bytes) -> str:
    """Write ``data`` to ``blobs_dir/<sha256-hex>`` and return the hex digest.

    Write-once by hash: a name collision IS a content match, so repeated reads
    dedupe to one blob and capture retries are idempotent (R6). Mirrors
    ``trace_writer.write_result_blob`` byte-for-byte (the parity contract).

    Example:
        >>> write_result_blob(Path("/tmp/run/blobs"), b"x")  # doctest: +SKIP
        '2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881'
    """
    digest = hashlib.sha256(data).hexdigest()
    blob_path = blobs_dir / digest
    if not blob_path.exists():
        blobs_dir.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(data)
    return digest
