"""Query-driven cross-repo SIMILAR generation (spec §A1.2, AC27)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from pydocs_mcp.application.similar_linker import (
    NullSimilarLinkGenerator,
    SimilarLinkGenerator,
)
from pydocs_mcp.application.workspace_linker import BundleHandle, WorkspaceLinker
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.in_memory_cross_link_store import InMemoryCrossLinkStore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

from ._fakes import (
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryPackageStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)

_DIM = 8  # turbovec requires a positive multiple of 8
# One-hot "word vectors": identical texts score ~1.0 under quantized inner
# product, orthogonal texts ~0.0 — robust against 4-bit quantization noise.
_VOCAB = {
    "alpha": (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "beta": (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "gamma": (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
}
_FINGERPRINT = ("fastembed", "fake-model", _DIM)


@dataclass(frozen=True, slots=True)
class FakeEmbedder:
    dim: int = _DIM
    model_name: str = "fake-model"

    async def embed_query(self, text: str) -> np.ndarray:
        return np.asarray(_VOCAB[text], dtype=np.float32)

    async def embed_chunks(self, texts) -> tuple[np.ndarray, ...]:
        return tuple(np.asarray(_VOCAB[t], dtype=np.float32) for t in texts)


def _chunk(chunk_id: int, qname: str, text: str) -> Chunk:
    metadata = {"package": "__project__"}
    if qname:
        metadata["qualified_name"] = qname
    return Chunk(text=text, id=chunk_id, metadata=metadata, content_hash=f"h{chunk_id}")


def _bundle(
    tmp_path: Path,
    project: str,
    chunks: tuple[Chunk, ...],
    *,
    pipeline_hash: str = "ph",
    model: str = "fake-model",
) -> BundleHandle:
    packages = InMemoryPackageStore()
    packages.items["__project__"] = Package(
        name="__project__",
        version="1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash=f"c-{project}",
        origin=PackageOrigin.PROJECT,
    )
    chunk_store = InMemoryChunkStore()
    chunk_store.by_package["__project__"] = list(chunks)
    return BundleHandle(
        project=project,
        bundle_stem=f"{project}_stem",
        bundle_path=str(tmp_path / f"{project}_stem.db"),
        indexed_at=1000.0,
        git_head="head",
        uow_factory=make_fake_uow_factory(
            packages=packages,
            chunks=chunk_store,
            trees=InMemoryDocumentTreeStore(),
            references=InMemoryReferenceStore(),
        ),
        embedding_provider="fastembed",
        embedding_model=model,
        embedding_dim=_DIM,
        pipeline_hash=pipeline_hash,
    )


async def _write_tq(bundle: BundleHandle, vectors: dict[int, tuple[float, ...]]) -> None:
    uow = TurboQuantUnitOfWork(index_path=Path(bundle.bundle_path).with_suffix(".tq"), dim=_DIM)
    async with uow:
        await uow.add_vectors(
            list(vectors), [np.asarray(v, dtype=np.float32) for v in vectors.values()]
        )
        await uow.commit()


def _generator(top_k: int = 5, min_score: float = 0.5) -> SimilarLinkGenerator:
    return SimilarLinkGenerator(
        embedder=FakeEmbedder(),
        serving_fingerprint=_FINGERPRINT,
        top_k=top_k,
        min_score=min_score,
    )


class TestGeneratePair:
    async def test_query_driven_edges_with_dedup_and_bounds(self, tmp_path: Path) -> None:
        # AC27 core: re-embed source project chunk texts, search the target
        # .tq, dedup chunk hits per qname (max score kept), respect min_score.
        source = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),))
        target = _bundle(
            tmp_path,
            "repob",
            (
                _chunk(10, "repob.core.parse", "alpha"),  # identical → ~1.0
                _chunk(11, "repob.core.parse", "beta"),  # same qname, low score
                _chunk(12, "repob.other", "beta"),  # orthogonal → below min
                _chunk(13, "", "alpha"),  # empty qname → skipped
            ),
        )
        await _write_tq(
            target,
            {10: _VOCAB["alpha"], 11: _VOCAB["beta"], 12: _VOCAB["beta"], 13: _VOCAB["alpha"]},
        )
        outcome = await _generator().generate_pair(source, target)
        assert not outcome.embedder_mismatch
        assert [(e.from_node_id, e.to_node_id) for e in outcome.edges] == [
            ("repoa.x", "repob.core.parse")
        ]
        edge = outcome.edges[0]
        assert edge.kind is ReferenceKind.SIMILAR
        assert edge.to_name == "repob.core.parse"  # audit analogue (§A1.2)
        assert outcome.seconds >= 0.0

    async def test_top_k_caps_edges_per_source_qname(self, tmp_path: Path) -> None:
        source = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),))
        target = _bundle(
            tmp_path,
            "repob",
            tuple(_chunk(10 + i, f"repob.t{i}", "alpha") for i in range(4)),
        )
        await _write_tq(target, {10 + i: _VOCAB["alpha"] for i in range(4)})
        outcome = await _generator(top_k=2).generate_pair(source, target)
        assert len(outcome.edges) == 2

    async def test_bundle_fingerprint_mismatch_skips_pair(self, tmp_path: Path) -> None:
        # AC27: ANY differing fingerprint component (here pipeline_hash) →
        # pair skipped, zero edges, mismatch flagged.
        source = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),))
        target = _bundle(
            tmp_path, "repob", (_chunk(10, "repob.y", "alpha"),), pipeline_hash="OTHER"
        )
        await _write_tq(target, {10: _VOCAB["alpha"]})
        outcome = await _generator().generate_pair(source, target)
        assert outcome.embedder_mismatch and outcome.edges == ()

    async def test_serving_embedder_mismatch_skips_pair(self, tmp_path: Path) -> None:
        source = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),), model="other-model")
        target = _bundle(tmp_path, "repob", (_chunk(10, "repob.y", "alpha"),), model="other-model")
        await _write_tq(target, {10: _VOCAB["alpha"]})
        # Bundles agree with each other but NOT with the serving embedder.
        outcome = await _generator().generate_pair(source, target)
        assert outcome.embedder_mismatch and outcome.edges == ()

    async def test_missing_tq_sidecar_warns_and_skips(self, tmp_path: Path, caplog) -> None:
        # AC31 posture: no .tq → zero edges + a warning, never a raise, and
        # NOT counted as an embedder mismatch.
        source = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),))
        target = _bundle(tmp_path, "repob", (_chunk(10, "repob.y", "alpha"),))
        with caplog.at_level("WARNING"):
            outcome = await _generator().generate_pair(source, target)
        assert outcome.edges == () and not outcome.embedder_mismatch
        assert any(".tq" in r.message for r in caplog.records)


class TestLinkerIntegration:
    def _linker(
        self, bundles: tuple[BundleHandle, ...], generator=None
    ) -> tuple[WorkspaceLinker, InMemoryCrossLinkStore]:
        store = InMemoryCrossLinkStore()
        kwargs = {} if generator is None else {"similar_generator": generator}
        return (
            WorkspaceLinker(
                bundles=bundles,
                cross_links=store,
                kinds=(ReferenceKind.CALLS, ReferenceKind.SIMILAR),
                match_scope="project_only",
                alias_resolution="imports_graph",
                workspace_scores=False,
                **kwargs,
            ),
            store,
        )

    async def test_link_persists_similar_edges_and_report_counters(self, tmp_path: Path) -> None:
        repoa = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),))
        repob = _bundle(tmp_path, "repob", (_chunk(10, "repob.y", "alpha"),))
        await _write_tq(repoa, {1: _VOCAB["alpha"]})
        await _write_tq(repob, {10: _VOCAB["alpha"]})
        linker, store = self._linker((repoa, repob), _generator())
        report = await linker.link()
        # Both ordered pairs ran and each produced one edge.
        assert report.similar_edges == 2
        assert set(report.per_pair_similar_seconds) == {"repoa->repob", "repob->repoa"}
        edges = await store.edges_from("repoa", "repoa.x", kinds=(ReferenceKind.SIMILAR,))
        assert [(e.to_project, e.to_node_id) for e in edges] == [("repob", "repob.y")]

    async def test_incremental_relink_regenerates_stale_pairs_only(self, tmp_path: Path) -> None:
        # §3.8 step (iii): relink(stale={repoa}) re-runs SIMILAR for every
        # (repoa, sibling) ordered pair — and only those.
        repoa = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),))
        repob = _bundle(tmp_path, "repob", (_chunk(10, "repob.y", "alpha"),))
        repoc = _bundle(tmp_path, "repoc", (_chunk(20, "repoc.z", "beta"),))
        for bundle, vectors in (
            (repoa, {1: _VOCAB["alpha"]}),
            (repob, {10: _VOCAB["alpha"]}),
            (repoc, {20: _VOCAB["beta"]}),
        ):
            await _write_tq(bundle, vectors)
        linker, store = self._linker((repoa, repob, repoc), _generator())
        await linker.link()
        report = await linker.link(stale_projects=frozenset({"repoa"}))
        assert set(report.per_pair_similar_seconds) == {
            "repoa->repob",
            "repob->repoa",
            "repoa->repoc",
            "repoc->repoa",
        }
        edges = await store.edges_from("repoa", "repoa.x", kinds=(ReferenceKind.SIMILAR,))
        assert [(e.to_project, e.to_node_id) for e in edges] == [("repob", "repob.y")]

    async def test_mismatched_pair_counted_zero_edges_persisted(self, tmp_path: Path) -> None:
        repoa = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),))
        repob = _bundle(tmp_path, "repob", (_chunk(10, "repob.y", "alpha"),), pipeline_hash="OTHER")
        await _write_tq(repoa, {1: _VOCAB["alpha"]})
        await _write_tq(repob, {10: _VOCAB["alpha"]})
        linker, store = self._linker((repoa, repob), _generator())
        report = await linker.link()
        assert report.embedder_mismatches == 2  # both ordered pairs
        assert report.similar_edges == 0
        assert await store.edges_from("repoa", "repoa.x", kinds=(ReferenceKind.SIMILAR,)) == ()

    async def test_null_generator_keeps_similar_inert(self, tmp_path: Path) -> None:
        # similar in kinds but no generator wired (e.g. no embedder): the
        # Null object keeps the pass inert — no counters, no edges.
        repoa = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),))
        repob = _bundle(tmp_path, "repob", (_chunk(10, "repob.y", "alpha"),))
        await _write_tq(repob, {10: _VOCAB["alpha"]})
        linker, store = self._linker((repoa, repob), NullSimilarLinkGenerator())
        report = await linker.link()
        assert report.similar_edges == 0
        assert report.per_pair_similar_seconds == {}
        assert await store.edges_from("repoa", "repoa.x", kinds=(ReferenceKind.SIMILAR,)) == ()

    async def test_similar_not_in_kinds_never_calls_generator(self, tmp_path: Path) -> None:
        calls: list[str] = []

        class _Spy:
            async def generate_pair(self, source, target):
                calls.append(f"{source.project}->{target.project}")
                raise AssertionError("must not be called")

        repoa = _bundle(tmp_path, "repoa", (_chunk(1, "repoa.x", "alpha"),))
        repob = _bundle(tmp_path, "repob", (_chunk(10, "repob.y", "alpha"),))
        store = InMemoryCrossLinkStore()
        linker = WorkspaceLinker(
            bundles=(repoa, repob),
            cross_links=store,
            kinds=(ReferenceKind.CALLS,),
            match_scope="project_only",
            alias_resolution="imports_graph",
            workspace_scores=False,
            similar_generator=_Spy(),
        )
        await linker.link()
        assert calls == []


class TestCompositionRoot:
    def test_default_config_wires_the_null_generator(self) -> None:
        from pydocs_mcp.retrieval.config import AppConfig
        from pydocs_mcp.server import _build_similar_generator

        generator = _build_similar_generator(AppConfig.load())
        assert isinstance(generator, NullSimilarLinkGenerator)

    def test_similar_opt_in_wires_the_real_generator(self) -> None:
        # The shared embedder is reused (no second model load) and the
        # serving fingerprint + bounds come from the config single sources.
        from pydocs_mcp.retrieval.config import AppConfig
        from pydocs_mcp.server import _build_similar_generator

        config = AppConfig.load()
        object.__setattr__(config.reference_graph.cross_repo, "kinds", ("calls", "similar"))
        embedder = FakeEmbedder()
        generator = _build_similar_generator(config, embedder)
        assert isinstance(generator, SimilarLinkGenerator)
        assert generator.embedder is embedder
        assert generator.serving_fingerprint == (
            config.embedding.provider,
            config.embedding.model_name,
            config.embedding.dim,
        )
        assert generator.top_k == config.reference_graph.cross_repo.similar.top_k
        assert generator.min_score == config.reference_graph.cross_repo.similar.min_score
