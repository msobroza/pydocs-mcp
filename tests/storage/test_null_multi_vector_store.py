"""NullMultiVectorStore — silent writes + loud read (spec Null Object pattern)."""

from __future__ import annotations

import numpy as np
import pytest

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
from pydocs_mcp.storage.protocols import MultiVectorStore


def test_satisfies_protocol() -> None:
    assert isinstance(NullMultiVectorStore(), MultiVectorStore)


@pytest.mark.asyncio
async def test_writes_are_no_op() -> None:
    s = NullMultiVectorStore()
    await s.add_vectors([1], [[np.zeros((1,), dtype=np.float32)]])
    await s.remove_vectors([1])
    await s.clear_all()
    # No assertions — silent success is the contract.


@pytest.mark.asyncio
async def test_score_raises_with_actionable_message() -> None:
    s = NullMultiVectorStore()
    with pytest.raises(ServiceUnavailableError) as exc:
        await s.score(
            [np.zeros((128,), dtype=np.float32)],
            subset_chunk_ids=[1, 2, 3],
            top_k=10,
        )
    assert "late_interaction" in str(exc.value).lower()
    assert "enabled" in str(exc.value).lower()
