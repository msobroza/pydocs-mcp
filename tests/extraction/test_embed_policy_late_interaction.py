"""The late-interaction embed stage must honour ``EmbedPolicy`` like the dense one.

``embedding.dependency_policy`` / ``full_index_dependencies`` decide which
chunks get vectors, by per-package tier (``full`` / ``doc_pages`` / ``none``).
``AssignChunkContentHashStage`` already folds that tier into every chunk hash
under BOTH shipped presets — but ``EmbedChunksMultiVectorStage`` was cloned from
the dense stage five weeks before the policy existed and never received it, so
under ``ingestion_late_interaction.yaml`` a dependency configured as
``dependency_policy: none`` ("dependencies are BM25-only") still got a ColBERT
multi-vector for every single code chunk, and the default ``doc_pages`` tier
embedded everything rather than the documentation pages it names.

There is exactly one policy and it has two readers (the hash stage and the
embed stage); the late-interaction stage is now a third reader of the SAME
policy, so a promoted or demoted dependency behaves identically whichever embed
stage the preset wires.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.extraction.embed_policy import EmbedPolicy
from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage
from pydocs_mcp.extraction.pipeline.stages.embed_chunks_multi_vector import (
    EmbedChunksMultiVectorStage,
)
from pydocs_mcp.models import Chunk, ChunkOrigin
from tests._fakes import MockEmbedder, make_fake_uow_factory


class _CountingMultiVectorEmbedder:
    dim = 4
    model_name = "fake-mv"

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed_query(self, text: str):
        return [np.ones((4,), dtype=np.float32) / 2]

    async def embed_chunks(self, texts):
        self.texts.extend(texts)
        return tuple([np.ones((4,), dtype=np.float32) / 2 for _ in range(2)] for _ in texts)


def _chunk(text: str, origin: str) -> Chunk:
    return Chunk(text=text, metadata={"package": "torch", "origin": origin, "title": text})


def _dep_state(*chunks: Chunk, package: str = "torch") -> IngestionState:
    return IngestionState(
        files=FileBundle(target=package, target_kind=TargetKind.DEPENDENCY, package_name=package),
        chunks=ChunkBundle(chunks=chunks),
    )


_CODE = _chunk("def f(): ...", ChunkOrigin.PYTHON_DEF.value)
_DOC_PAGE = _chunk("torch.nn — neural networks", ChunkOrigin.DEPENDENCY_MODULE_DOC.value)


@pytest.mark.asyncio
async def test_policy_none_embeds_no_dependency_chunk() -> None:
    """``dependency_policy: none`` is documented as BM25-only dependencies."""
    mve = _CountingMultiVectorEmbedder()
    stage = EmbedChunksMultiVectorStage(
        embedder=mve, embed_policy=EmbedPolicy(dependency_policy="none")
    )
    out = await stage.run(_dep_state(_CODE, _DOC_PAGE))
    assert mve.texts == []
    assert all(c.embedding is None for c in out.chunks.chunks)


@pytest.mark.asyncio
async def test_default_doc_pages_tier_embeds_only_documentation() -> None:
    """The shipped default: dependency docstring pages yes, dependency code no."""
    mve = _CountingMultiVectorEmbedder()
    out = await EmbedChunksMultiVectorStage(embedder=mve).run(_dep_state(_CODE, _DOC_PAGE))
    assert mve.texts == [_DOC_PAGE.text]
    code_out, doc_out = out.chunks.chunks
    assert code_out.embedding is None
    assert doc_out.embedding is not None


@pytest.mark.asyncio
async def test_a_promoted_dependency_embeds_everything() -> None:
    mve = _CountingMultiVectorEmbedder()
    stage = EmbedChunksMultiVectorStage(
        embedder=mve, embed_policy=EmbedPolicy(full_index_dependencies=("torch",))
    )
    out = await stage.run(_dep_state(_CODE, _DOC_PAGE))
    assert sorted(mve.texts) == sorted([_CODE.text, _DOC_PAGE.text])
    assert all(c.embedding is not None for c in out.chunks.chunks)


@pytest.mark.asyncio
async def test_the_project_is_always_full_tier() -> None:
    mve = _CountingMultiVectorEmbedder()
    state = IngestionState(
        files=FileBundle(target=Path(), target_kind=TargetKind.PROJECT, package_name="__project__"),
        chunks=ChunkBundle(chunks=(_CODE, _DOC_PAGE)),
    )
    out = await EmbedChunksMultiVectorStage(
        embedder=mve, embed_policy=EmbedPolicy(dependency_policy="none")
    ).run(state)
    assert len(mve.texts) == 2
    assert all(c.embedding is not None for c in out.chunks.chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [
        EmbedPolicy(),
        EmbedPolicy(dependency_policy="none"),
        EmbedPolicy(dependency_policy="full"),
        EmbedPolicy(full_index_dependencies=("torch",)),
    ],
    ids=["doc_pages", "none", "full", "promoted"],
)
async def test_both_embed_stages_agree_on_eligibility(policy: EmbedPolicy) -> None:
    """One policy, two readers: whichever stage a preset wires, the same chunks
    get vectors. Divergence here is exactly the bug this file exists for."""
    chunks = (
        _CODE,
        _DOC_PAGE,
        _chunk("README", ChunkOrigin.DEPENDENCY_README.value),
        _chunk("# Notes", ChunkOrigin.MARKDOWN_SECTION.value),
    )
    dense_out = await EmbedChunksStage(embedder=MockEmbedder(dim=4), embed_policy=policy).run(
        _dep_state(*chunks)
    )
    mv_out = await EmbedChunksMultiVectorStage(
        embedder=_CountingMultiVectorEmbedder(), embed_policy=policy
    ).run(_dep_state(*chunks))

    dense_embedded = [c.text for c in dense_out.chunks.chunks if c.embedding is not None]
    mv_embedded = [c.text for c in mv_out.chunks.chunks if c.embedding is not None]
    assert mv_embedded == dense_embedded


def test_from_dict_reads_the_policy_from_the_embedding_config() -> None:
    """Same config section the hash stage and the dense stage read — one source."""

    class _Embedding:
        dependency_policy = "none"
        full_index_dependencies = ("numpy",)

    class _Cfg:
        embedding = _Embedding()

    class _Ctx:
        multi_vector_embedder = _CountingMultiVectorEmbedder()
        app_config = _Cfg()

    stage = EmbedChunksMultiVectorStage.from_dict({}, _Ctx())
    assert stage.embed_policy == EmbedPolicy(
        dependency_policy="none", full_index_dependencies=("numpy",)
    )


def test_the_shipped_late_interaction_preset_applies_the_policy(
    monkeypatch, tmp_path: Path
) -> None:
    """End to end through the real LI preset over a real installed dependency.

    ``dependency_policy: none`` must reach the multi-vector embedder as zero
    texts; the default tier must vectorise documentation pages only. Runs the
    ingestion pipeline without persisting, so no fast-plaid index is built.
    """
    from pydocs_mcp import pipelines as shipped
    from pydocs_mcp.extraction.embed_policy import EmbedPolicy as _Policy
    from pydocs_mcp.extraction.factories import load_ingestion_pipeline
    from pydocs_mcp.extraction.pipeline.chunk_extractor import PipelineChunkExtractor
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.retrieval.config import AppConfig

    li_yaml = Path(shipped.__file__).parent / "ingestion_late_interaction.yaml"

    def _extract(policy: str):
        mve = _CountingMultiVectorEmbedder()
        monkeypatch.setattr(_embedders, "build_multi_vector_embedder", lambda cfg: mve)
        overlay = tmp_path / f"cfg_{policy}.yaml"
        overlay.write_text(
            "late_interaction:\n  enabled: true\n"
            f"embedding:\n  dependency_policy: {policy}\n"
            f"extraction:\n  ingestion:\n    pipeline_path: {li_yaml}\n"
        )
        pipeline = load_ingestion_pipeline(
            li_yaml,
            AppConfig.load(explicit_path=overlay),
            uow_factory=make_fake_uow_factory(),
            pipeline_hash="pinned",
        )
        result = asyncio.run(
            PipelineChunkExtractor(pipeline=pipeline).extract_from_dependency("sniffio")
        )
        assert result.chunks, "sniffio must yield chunks"
        return mve.texts, result.chunks

    texts_none, _ = _extract("none")
    assert texts_none == []

    texts_docs, chunks = _extract("doc_pages")
    assert texts_docs, "doc_pages must still vectorise the docstring pages"
    vectorised = [c for c in chunks if c.embedding is not None]
    assert vectorised and all(
        _Policy.should_embed(c.metadata.get("origin"), "doc_pages") for c in vectorised
    ), "a non-documentation dependency chunk received a multi-vector"
    assert any(c.embedding is None for c in chunks), "some code chunk must stay vectorless"
