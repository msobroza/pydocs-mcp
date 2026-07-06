"""get_symbol depth=source — verbatim bytes + path + line-cap (spec §D1/§D7)."""

import asyncio

import pytest

from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.symbol_source import SymbolSourceService
from pydocs_mcp.models import Chunk
from tests._fakes import InMemoryChunkStore, make_fake_uow_factory


def _store(*chunks: Chunk) -> InMemoryChunkStore:
    # make_fake_uow_factory(chunks=...) expects an InMemoryChunkStore, not a
    # raw tuple — build + seed one here so each test states only its chunks.
    store = InMemoryChunkStore()
    asyncio.run(store.upsert(chunks))
    return store


def _chunk(*, qualified_name: str, source_path: str, text: str) -> Chunk:
    # Mirrors this suite's chunk-construction convention (see
    # tests/application/test_reference_service.py): metadata carries
    # ``qualified_name`` + ``source_path`` so SymbolSourceService can find the
    # symbol and render its file path.
    return Chunk(
        text=text,
        metadata={
            "package": "pkg",
            "qualified_name": qualified_name,
            "source_path": source_path,
        },
    )


def _service(store: InMemoryChunkStore) -> SymbolSourceService:
    return SymbolSourceService(
        uow_factory=make_fake_uow_factory(chunks=store),
        max_lines=5,
    )


def test_returns_source_block_with_path() -> None:
    store = _store(
        _chunk(
            qualified_name="pkg.mod.f",
            source_path="pkg/mod.py",
            text="def f():\n    return 1\n",
        )
    )
    out = asyncio.run(_service(store).source_for("pkg.mod.f"))
    assert "```python" in out and "def f():" in out
    assert "pkg/mod.py" in out


def test_line_cap_truncates_with_recovery_note() -> None:
    body = "\n".join(f"line{i}" for i in range(20))
    store = _store(_chunk(qualified_name="pkg.mod.big", source_path="pkg/mod.py", text=body))
    out = asyncio.run(_service(store).source_for("pkg.mod.big"))
    assert "line4" in out and "line5" not in out
    assert "pkg/mod.py" in out  # the file path is the terminal recovery step


def test_unknown_symbol_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        asyncio.run(_service(InMemoryChunkStore()).source_for("nope.missing"))
