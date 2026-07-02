"""DependencyDocPagesStage: one docstring page per dependency module."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages.dependency_doc_pages import (
    DependencyDocPagesStage,
    build_doc_page_text,
)
from pydocs_mcp.models import ChunkOrigin

_SRC = '''"""Data loading utilities."""

class DataLoader:
    """Combines a dataset and a sampler."""

    def __len__(self):
        return 0

def default_collate(batch, *, strict=False):
    """Puts each data field into a tensor."""
    return batch

def _private_helper():
    """Never surfaces on the page."""

def undocumented(x):
    return x
'''


def _state(*, kind: TargetKind, files: tuple[tuple[str, str], ...]) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target="torch",
            target_kind=kind,
            package_name="torch",
            root=Path("/site"),
            paths=tuple(p for p, _ in files),
            file_contents=files,
        ),
        chunks=ChunkBundle(),
    )


def test_page_text_module_and_public_docstrings() -> None:
    text = build_doc_page_text(_SRC)
    assert "Data loading utilities." in text  # module docstring
    assert "class DataLoader:" in text and "Combines a dataset" in text
    assert "def default_collate(batch, *, strict=False):" in text  # signature via AST
    assert "Puts each data field" in text
    assert "return batch" not in text  # code bodies excluded
    assert "_private_helper" not in text  # private skipped
    assert "undocumented" not in text  # docstring-less skipped


def test_page_text_empty_for_docstringless_or_broken_source() -> None:
    assert build_doc_page_text("x = 1\n") == ""
    assert build_doc_page_text("def broken(:\n") == ""  # SyntaxError -> skip


@pytest.mark.asyncio
async def test_emits_one_page_chunk_per_dependency_module() -> None:
    state = _state(
        kind=TargetKind.DEPENDENCY,
        files=(("/site/torch/utils/data.py", _SRC), ("/site/torch/empty.py", "x = 1\n")),
    )
    out = await DependencyDocPagesStage().run(state)
    pages = [c for c in out.chunks.chunks if c.metadata.get("origin") == "dependency_module_doc"]
    assert len(pages) == 1  # docstring-less module emits nothing
    page = pages[0]
    assert page.metadata["module"] == "torch.utils.data"
    assert page.metadata["qualified_name"] == "torch.utils.data"
    assert page.metadata["title"] == "torch.utils.data documentation"
    assert page.metadata["package"] == "torch"
    assert page.metadata["origin"] == ChunkOrigin.DEPENDENCY_MODULE_DOC.value


@pytest.mark.asyncio
async def test_noop_for_project_targets() -> None:
    state = _state(kind=TargetKind.PROJECT, files=(("/site/app/mod.py", _SRC),))
    out = await DependencyDocPagesStage().run(state)
    assert out is state  # untouched


@pytest.mark.asyncio
async def test_init_module_name_strips_suffix() -> None:
    state = _state(kind=TargetKind.DEPENDENCY, files=(("/site/torch/__init__.py", _SRC),))
    out = await DependencyDocPagesStage().run(state)
    assert out.chunks.chunks[-1].metadata["module"] == "torch"


@pytest.mark.asyncio
async def test_page_truncated_to_max_chars() -> None:
    state = _state(kind=TargetKind.DEPENDENCY, files=(("/site/t/m.py", _SRC),))
    out = await DependencyDocPagesStage(max_chars=25).run(state)
    assert len(out.chunks.chunks[-1].text) == 25


@pytest.mark.asyncio
async def test_appends_after_existing_chunks() -> None:
    from pydocs_mcp.models import Chunk

    existing = Chunk(text="code", metadata={"package": "torch"})
    state = _state(kind=TargetKind.DEPENDENCY, files=(("/site/t/m.py", _SRC),))
    state = replace(state, chunks=ChunkBundle(chunks=(existing,)))
    out = await DependencyDocPagesStage().run(state)
    assert out.chunks.chunks[0] is existing  # code chunks preserved, page appended
    assert len(out.chunks.chunks) == 2


def test_stage_round_trips_yaml() -> None:
    stage = DependencyDocPagesStage(max_chars=123)
    d = stage.to_dict()
    assert d == {"type": "dependency_doc_pages", "max_chars": 123}
    rebuilt = DependencyDocPagesStage.from_dict({"max_chars": 123}, context=None)
    assert rebuilt == stage
    assert DependencyDocPagesStage.from_dict({}, context=None).max_chars == 8000
