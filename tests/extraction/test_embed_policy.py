"""EmbedPolicy tiers + the selective EmbedChunksStage + tier-aware hashing."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.embed_policy import EmbedPolicy
from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages.assign_chunk_content_hash import (
    AssignChunkContentHashStage,
)
from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from tests._fakes import MockEmbedder

# ── EmbedPolicy tiers ──


def test_project_is_always_full_tier() -> None:
    assert EmbedPolicy().tier(TargetKind.PROJECT, "whatever") == "full"


def test_dependency_default_tier_is_doc_pages() -> None:
    assert EmbedPolicy().tier(TargetKind.DEPENDENCY, "torch") == "doc_pages"


def test_policy_full_and_none() -> None:
    assert EmbedPolicy(dependency_policy="full").tier(TargetKind.DEPENDENCY, "torch") == "full"
    assert EmbedPolicy(dependency_policy="none").tier(TargetKind.DEPENDENCY, "torch") == "none"


def test_invalid_policy_rejected() -> None:
    with pytest.raises(ValueError, match="dependency_policy"):
        EmbedPolicy(dependency_policy="everything")


def test_full_index_promotion_exact_and_glob() -> None:
    pol = EmbedPolicy(full_index_dependencies=("numpy", "internal_*"))
    assert pol.tier(TargetKind.DEPENDENCY, "numpy") == "full"
    assert pol.tier(TargetKind.DEPENDENCY, "internal-lib") == "full"  # dash-folded glob
    assert pol.tier(TargetKind.DEPENDENCY, "torch") == "doc_pages"


def test_from_config_normalizes_names() -> None:
    class _Cfg:
        dependency_policy = "doc_pages"
        full_index_dependencies = ("My-Internal-Lib",)

    pol = EmbedPolicy.from_config(_Cfg())
    assert pol.is_full_indexed("my_internal_lib")
    assert EmbedPolicy.from_config(None) == EmbedPolicy()


def test_should_embed_by_origin() -> None:
    assert EmbedPolicy.should_embed("python_def", "full")
    assert not EmbedPolicy.should_embed("python_def", "doc_pages")
    assert EmbedPolicy.should_embed("dependency_module_doc", "doc_pages")
    assert EmbedPolicy.should_embed("markdown_section", "doc_pages")
    assert not EmbedPolicy.should_embed("dependency_module_doc", "none")


# ── EmbedChunksStage under the policy ──


def _chunk(title: str, origin: str) -> Chunk:
    return Chunk(
        text=f"text {title}",
        metadata={"package": "torch", "title": title, "origin": origin},
    )


def _dep_state(*chunks: Chunk, package: bool = True) -> IngestionState:
    pkg = (
        Package(
            name="torch",
            version="1",
            summary="",
            homepage="",
            dependencies=(),
            content_hash="h",
            origin=PackageOrigin.DEPENDENCY,
        )
        if package
        else None
    )
    return IngestionState(
        files=FileBundle(
            target="torch",
            target_kind=TargetKind.DEPENDENCY,
            package_name="torch",
            root=Path("/site"),
        ),
        chunks=ChunkBundle(chunks=tuple(chunks)),
        package=pkg,
    )


@pytest.mark.asyncio
async def test_dependency_code_chunks_not_embedded_doc_pages_are() -> None:
    code = _chunk("def", "python_def")
    page = _chunk("page", "dependency_module_doc")
    md = _chunk("readme", "markdown_section")
    out = await EmbedChunksStage(embedder=MockEmbedder()).run(_dep_state(code, page, md))
    by_title = {c.metadata["title"]: c for c in out.chunks.chunks}
    assert by_title["def"].embedding is None  # code: indexed, not embedded
    assert by_title["page"].embedding is not None  # doc page: embedded
    assert by_title["readme"].embedding is not None  # markdown: embedded


@pytest.mark.asyncio
async def test_promoted_dependency_embeds_everything() -> None:
    code = _chunk("def", "python_def")
    stage = EmbedChunksStage(
        embedder=MockEmbedder(),
        embed_policy=EmbedPolicy(full_index_dependencies=("torch",)),
    )
    out = await stage.run(_dep_state(code))
    assert out.chunks.chunks[0].embedding is not None


@pytest.mark.asyncio
async def test_embedding_model_stamped_only_when_eligible() -> None:
    # Only code chunks -> nothing eligible under doc_pages -> no stamp.
    out = await EmbedChunksStage(embedder=MockEmbedder()).run(_dep_state(_chunk("d", "python_def")))
    assert out.package is not None and out.package.embedding_model is None
    # A doc page makes the package eligible -> stamped.
    out2 = await EmbedChunksStage(embedder=MockEmbedder()).run(
        _dep_state(_chunk("p", "dependency_module_doc"))
    )
    assert out2.package is not None and out2.package.embedding_model is not None


@pytest.mark.asyncio
async def test_policy_none_embeds_nothing_and_never_stamps() -> None:
    stage = EmbedChunksStage(
        embedder=MockEmbedder(), embed_policy=EmbedPolicy(dependency_policy="none")
    )
    out = await stage.run(_dep_state(_chunk("p", "dependency_module_doc")))
    assert all(c.embedding is None for c in out.chunks.chunks)
    assert out.package is not None and out.package.embedding_model is None


# ── tier-aware content hashing (promotion re-embeds ONLY that package) ──


@pytest.mark.asyncio
async def test_hash_changes_with_tier_only_for_affected_package() -> None:
    chunk = _chunk("def", "python_def")
    base = AssignChunkContentHashStage(pipeline_hash="P")
    promoted = AssignChunkContentHashStage(
        pipeline_hash="P", embed_policy=EmbedPolicy(full_index_dependencies=("torch",))
    )
    h_base = (await base.run(_dep_state(chunk))).chunks.chunks[0].content_hash
    h_promoted = (await promoted.run(_dep_state(chunk))).chunks.chunks[0].content_hash
    assert h_base != h_promoted  # tier flip -> hash flip -> re-embed via diff-merge

    # An UNRELATED dependency's hash is untouched by torch's promotion.
    other_state = IngestionState(
        files=FileBundle(
            target="requests",
            target_kind=TargetKind.DEPENDENCY,
            package_name="requests",
            root=Path("/site"),
        ),
        chunks=ChunkBundle(
            chunks=(Chunk(text="t", metadata={"package": "requests", "title": "x"}),)
        ),
    )
    h_other_base = (await base.run(other_state)).chunks.chunks[0].content_hash
    h_other_promoted = (await promoted.run(other_state)).chunks.chunks[0].content_hash
    assert h_other_base == h_other_promoted


@pytest.mark.asyncio
async def test_project_hash_unchanged_by_dependency_policy() -> None:
    state = IngestionState(
        files=FileBundle(target=Path(), target_kind=TargetKind.PROJECT, package_name="__project__"),
        chunks=ChunkBundle(
            chunks=(Chunk(text="t", metadata={"package": "__project__", "title": "x"}),)
        ),
    )
    h1 = (
        (await AssignChunkContentHashStage(pipeline_hash="P").run(state))
        .chunks.chunks[0]
        .content_hash
    )
    h2 = (
        (
            await AssignChunkContentHashStage(
                pipeline_hash="P", embed_policy=EmbedPolicy(dependency_policy="none")
            ).run(state)
        )
        .chunks.chunks[0]
        .content_hash
    )
    assert h1 == h2  # project tier is always "full"


# ── config plumbing ──


def test_with_full_index_dependencies_merges_dedup() -> None:
    from pydocs_mcp.retrieval.config import AppConfig

    cfg = AppConfig()
    assert cfg.with_full_index_dependencies(()) is cfg  # no-op
    merged = cfg.with_full_index_dependencies(("numpy", "numpy", "pandas"))
    assert merged.embedding.full_index_dependencies == ["numpy", "pandas"]
    again = merged.with_full_index_dependencies(("numpy", "scipy"))
    assert again.embedding.full_index_dependencies == ["numpy", "pandas", "scipy"]
