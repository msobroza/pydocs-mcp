"""The pipeline-identity salt on the package content hash.

``ingestion_pipeline_hash`` folds the embedder, the search backend, the
effective extension scope and the ingestion YAML's raw bytes — every input that
makes previously-extracted chunks or previously-computed vectors wrong. The
CHUNK hashes already folded it; the PACKAGE hash did not, and the package hash
is the gate ``ProjectIndexer`` consults first.

The consequence of that gap was a permanent re-embed loop: a pipeline change
moved every chunk hash, so the skip set missed and the embed stage recomputed
every vector, but the package hash was unchanged, so the pass reported a cache
hit, never called ``reindex_package``, and threw the vectors away — repeating
identically on every subsequent pass. See
tests/integration/test_pipeline_hash_invalidates_package_cache.py for the
end-to-end behaviour; this file pins the hash framing itself.

The salt is the OUTERMOST fold and applies only when a pipeline hash was
supplied, which is always the case through a composition root and never for a
bare ``ContentHashStage()``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ContentHashStage
from tests.extraction._content_hash_oracle import (
    grammar_folded,
    pipeline_folded,
    raw_hash_files,
)


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    """Written ONCE per test: ``hash_files`` folds path+mtime, so rewriting
    identical bytes would move the base digest and mask what we are pinning."""
    f = tmp_path / "mod.py"
    f.write_text("x = 1\n")
    return f


def _state(source_file: Path) -> IngestionState:
    """A file bundle with no user excludes, so only the two unconditional
    salts are in play."""
    return IngestionState(
        files=FileBundle(
            target=source_file.parent,
            target_kind=TargetKind.PROJECT,
            package_name="__project__",
            paths=(str(source_file),),
        )
    )


async def _hash_with(source_file: Path, pipeline_hash: str) -> str:
    out = await ContentHashStage(pipeline_hash=pipeline_hash).run(_state(source_file))
    return out.files.content_hash


@pytest.mark.asyncio
async def test_pipeline_hash_is_the_outermost_fold(source_file: Path) -> None:
    """Framing: base → grammar salt → pipeline salt."""
    got = await _hash_with(source_file, "P1")

    expected = pipeline_folded(grammar_folded(raw_hash_files([str(source_file)])), "P1")
    assert got == expected


@pytest.mark.asyncio
async def test_a_different_pipeline_hash_moves_the_package_hash(source_file: Path) -> None:
    """Without this the package-level cache gate cannot see a pipeline change."""
    assert await _hash_with(source_file, "P1") != await _hash_with(source_file, "P2")


@pytest.mark.asyncio
async def test_the_same_pipeline_hash_is_stable(source_file: Path) -> None:
    """Stability is what lets the pass settle after one re-index instead of
    looping — an unstable salt would make every pass a miss."""
    assert await _hash_with(source_file, "P1") == await _hash_with(source_file, "P1")


@pytest.mark.asyncio
async def test_no_pipeline_hash_leaves_the_framing_untouched(source_file: Path) -> None:
    """Stage-isolation callers pass no hash and must see the pre-salt framing.

    This is what keeps the suites that pin an exact grammar-salted hash valid
    (they construct the stage directly), mirroring
    ``AssignChunkContentHashStage``'s empty-pipeline_hash no-op.
    """
    got = await _hash_with(source_file, "")
    assert got == grammar_folded(raw_hash_files([str(source_file)]))


def test_pipeline_hash_is_wiring_not_config() -> None:
    """It comes from the composition root, so it must not round-trip to YAML."""
    assert ContentHashStage(pipeline_hash="P1").to_dict() == {"type": "content_hash"}


def test_from_dict_reads_the_build_context() -> None:
    """The decoder picks the hash up the same way AssignChunkContentHashStage does."""

    class _Ctx:
        pipeline_hash = "P-from-context"

    assert ContentHashStage.from_dict({}, _Ctx()).pipeline_hash == "P-from-context"


def test_from_dict_tolerates_a_context_without_a_pipeline_hash() -> None:
    """Contexts built for other stages need not carry one; absence means no fold."""
    assert ContentHashStage.from_dict({}, object()).pipeline_hash == ""


@pytest.mark.asyncio
async def test_the_embed_tier_moves_the_package_hash(source_file: Path) -> None:
    """Promoting a dependency must miss the package gate, not just the chunk diff.

    The tier is deliberately kept out of ``ingestion_pipeline_hash`` so that a
    policy change re-embeds only the packages whose tier moved. That makes it a
    separate salt component here: without it ``--full-dep`` re-embedded the whole
    package, hit the package-level cache, and discarded the vectors every pass.
    """
    from pydocs_mcp.extraction.embed_policy import EmbedPolicy
    from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind

    def _dep_state() -> IngestionState:
        return IngestionState(
            files=FileBundle(
                target="somedep",
                target_kind=TargetKind.DEPENDENCY,
                package_name="somedep",
                paths=(str(source_file),),
            )
        )

    default_tier = ContentHashStage(pipeline_hash="P1")
    promoted = ContentHashStage(
        pipeline_hash="P1",
        embed_policy=EmbedPolicy(full_index_dependencies=("somedep",)),
    )

    assert (await default_tier.run(_dep_state())).files.content_hash != (
        await promoted.run(_dep_state())
    ).files.content_hash


def test_from_dict_reads_the_embed_policy() -> None:
    """The tier half of the salt comes from the same config the chunk stage reads."""
    from pydocs_mcp.extraction.embed_policy import EmbedPolicy

    class _Embedding:
        dependency_policy = "none"
        full_index_dependencies = ("torch",)

    class _Cfg:
        embedding = _Embedding()

    class _Ctx:
        pipeline_hash = "P1"
        app_config = _Cfg()

    assert ContentHashStage.from_dict({}, _Ctx()).embed_policy == EmbedPolicy(
        dependency_policy="none", full_index_dependencies=("torch",)
    )
