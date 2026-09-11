"""An unchanged second index pass must not call the embedder at all.

The chunk-level embed skip (spec Decision 5) exists so a reindex pays the
embedder only for genuinely NEW chunks — model inference locally, billed
API calls when the OpenAI embedder is configured.

Regression: the skip never fired end-to-end.
:class:`LoadExistingChunkHashesStage` scoped its lookup to
``state.package``, but both shipped ingestion presets populate that in
``package_build`` — the LAST stage, well after ``embed_chunks``. The
loader therefore short-circuited on every run, left the skip set empty,
and every pass re-embedded every eligible chunk of every package, cache
hits included. The stage-level tests missed it because they hand-build an
``IngestionState`` with ``package=`` already set, which production never
does at that point.

These tests drive the real composition root (``build_project_indexer``)
so the stage ordering is exercised as shipped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder


@pytest.fixture
def embedder(monkeypatch) -> CountingEmbedder:
    """One CountingEmbedder shared by every bundle built in a test.

    Patches the same lazy ``build_embedder`` seam the storage factory
    resolves, so no ONNX weights are downloaded.
    """
    from pydocs_mcp.extraction.strategies import embedders as _embedders

    counting = CountingEmbedder()
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: counting)
    return counting


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "proj_abc123.db"
    open_index_database(path).close()
    return path


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A minimal importable project with enough prose to produce chunks."""
    root = tmp_path / "proj"
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""Demo package for the embed-skip test."""\n')
    (pkg / "core.py").write_text(
        '"""Core module docstring."""\n'
        "\n"
        "\n"
        "def alpha() -> int:\n"
        '    """Return the first number."""\n'
        "    return 1\n"
        "\n"
        "\n"
        "def beta() -> int:\n"
        '    """Return the second number."""\n'
        "    return 2\n"
    )
    return root


def _index_pass(db_path: Path, project_dir: Path):
    """One full `pydocs-mcp index` equivalent: fresh bundle, same db."""
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(
        AppConfig.load(),
        db_path,
        use_inspect=False,
        inspect_depth=None,
    )
    return asyncio.run(
        bundle.orchestrator.index_project(
            project_dir,
            # Dependency resolution would drag the whole installed env into
            # the pass; the project source alone exercises the same skip path.
            include_dependencies=False,
        )
    )


def test_first_pass_embeds_then_unchanged_pass_embeds_nothing(
    db_path: Path,
    project_dir: Path,
    embedder: CountingEmbedder,
) -> None:
    """The bug: pass 2 re-embedded everything instead of skipping."""
    stats = _index_pass(db_path, project_dir)
    assert stats.project_indexed is True
    assert embedder.calls, "first pass must actually embed something"

    after_first = list(embedder.calls)
    stats2 = _index_pass(db_path, project_dir)

    # The package hash is unchanged, so the indexer reports a cache hit ...
    assert stats2.project_indexed is False
    # ... and — the actual regression — the embedder is never called again.
    assert embedder.calls == after_first, (
        "unchanged reindex re-embedded chunks: "
        f"{embedder.calls[len(after_first) :]!r} (expected no new calls)"
    )


class _CountingMultiVectorEmbedder:
    """Counting stub multi-vector embedder (two 4-d token vectors per text)."""

    dim = 4
    model_name = "fake-mv"

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def embed_query(self, text: str):
        import numpy as np

        return [np.ones((4,), dtype=np.float32) / 2]

    async def embed_chunks(self, texts):
        import numpy as np

        self.calls.append(len(texts))
        return tuple([np.ones((4,), dtype=np.float32) / 2 for _ in range(2)] for _ in texts)


def test_late_interaction_preset_also_skips_unchanged_chunks(
    tmp_path: Path,
    project_dir: Path,
    monkeypatch,
) -> None:
    """``ingestion_late_interaction.yaml`` shares the loader, so it shared the bug.

    ``EmbedChunksMultiVectorStage`` honors ``existing_chunk_hashes``
    correctly on its own — but the LI preset orders its stages exactly
    like the default one, so the skip set reaching it was always empty
    too. Fixing the shared loader fixes both; this pins that.
    """
    from pydocs_mcp import pipelines as _pipelines
    from pydocs_mcp.extraction.factories import load_ingestion_pipeline
    from pydocs_mcp.extraction.pipeline.chunk_extractor import PipelineChunkExtractor
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    mve = _CountingMultiVectorEmbedder()
    monkeypatch.setattr(_embedders, "build_multi_vector_embedder", lambda cfg: mve)

    db = tmp_path / "li.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    li_yaml = Path(_pipelines.__file__).parent / "ingestion_late_interaction.yaml"

    def _run_pass():
        pipeline = load_ingestion_pipeline(
            li_yaml,
            AppConfig.load(),
            uow_factory=uow_factory,
            pipeline_hash="pinned-for-test",
        )
        return asyncio.run(
            PipelineChunkExtractor(pipeline=pipeline).extract_from_project(project_dir)
        )

    result = _run_pass()
    assert mve.calls, "first pass must actually embed something"

    # Persist what the first pass produced, then reindex the same tree.
    async def _persist() -> None:
        async with uow_factory() as uow:
            await uow.chunks.insert(result.chunks)
            await uow.commit()

    asyncio.run(_persist())
    mve.calls.clear()

    _run_pass()
    assert mve.calls == [], f"LI reindex re-embedded {mve.calls!r} (expected none)"


def test_changed_file_embeds_only_the_new_chunks(
    db_path: Path,
    project_dir: Path,
    embedder: CountingEmbedder,
) -> None:
    """The skip must be a diff, not a blanket 'already indexed' short-circuit."""
    _index_pass(db_path, project_dir)
    embedder.calls.clear()

    # Append one new function; every pre-existing chunk keeps its hash.
    core = project_dir / "pkg" / "core.py"
    core.write_text(
        core.read_text()
        + '\n\ndef gamma() -> int:\n    """Return the third number."""\n    return 3\n'
    )

    _index_pass(db_path, project_dir)

    embedded = sum(n for method, n in embedder.calls if method == "embed_chunks")
    assert embedded > 0, "a changed file must re-embed its changed chunks"
    # The untouched sibling module must not be dragged along.
    assert embedded < 4, f"expected only the changed chunks, embedded {embedded}"
