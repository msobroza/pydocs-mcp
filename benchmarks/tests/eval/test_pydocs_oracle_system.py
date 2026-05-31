"""Pin PydocsOracleSystem + PydocsOracleGoldResolver (oracle-indexing mode).

HERMETIC: an INJECTED ``rows_source`` feeds 5 canned
``code-rag-bench/library-documentation``-shaped rows (``doc_id`` /
``doc_content`` / ``library``) so the test never imports ``datasets`` and
never touches the network. The real pydocs_mcp store + chunk pipeline run
(pydocs_mcp IS installed in this venv), so this exercises the genuine
tmp-SQLite -> upsert -> rebuild_index -> pipeline round-trip the parent
``PydocsMcpSystem`` uses, only with the chunk SOURCE swapped to the rows.

Identity coherence under test: each oracle chunk is a real store row, so
``search()`` stamps ``chunk_id = chunk.id`` and the exact-match resolver
returns ``chunk:{id}`` (NOT the doc_id) — keyed exactly like Task 3's
``_item_key`` so the metric's membership check lines up.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest
from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.gold_resolver import PydocsOracleGoldResolver, _item_key
from benchmarks.eval.serialization import system_registry
from benchmarks.eval.systems import PydocsOracleSystem
from pydocs_mcp.retrieval.config import AppConfig

# WHY: 5 canned rows mimicking the HF ``library-documentation`` schema.
# Two libraries so the per-library Package upsert + package-filtered
# resolver scan are both exercised. ``doc_content`` carries a distinctive
# token so the FTS pipeline can surface each row by a targeted query.
_ROWS: tuple[dict[str, str], ...] = (
    {
        "doc_id": "pandas#merge",
        "doc_content": "pandas merge joins two DataFrames on a shared key zorptoken",
        "library": "pandas",
    },
    {
        "doc_id": "pandas#groupby",
        "doc_content": "pandas groupby aggregates rows by a grouping column wibbletoken",
        "library": "pandas",
    },
    {
        "doc_id": "pandas#concat",
        "doc_content": "pandas concat stacks frames along an axis flumphtoken",
        "library": "pandas",
    },
    {
        "doc_id": "numpy#reshape",
        "doc_content": "numpy reshape changes an array shape without copying snorktoken",
        "library": "numpy",
    },
    {
        "doc_id": "numpy#stack",
        "doc_content": "numpy stack joins a sequence of arrays on a new axis blarftoken",
        "library": "numpy",
    },
)


def _rows_source() -> Iterable[Mapping[str, str]]:
    return list(_ROWS)


# WHY: the REAL ``code-rag-bench/library-documentation`` rows carry ONLY
# ``{doc_id, doc_content}`` — NO ``library`` / ``source`` field. The
# ``doc_id`` IS the library source (dot-separated, library first segment),
# so the oracle must DERIVE the package from that prefix or every chunk
# lands under package ``""`` and the package-filtered resolver scan never
# matches (recall 0). These rows mirror that real shape: no library field,
# dot-separated doc_ids, including the ``sklearn`` -> ``scikit-learn`` remap.
_REAL_SHAPE_ROWS: tuple[dict[str, str], ...] = (
    {
        "doc_id": "numpy.reference.generated.numpy.imag",
        "doc_content": "numpy imag returns the imaginary part of an array zorptoken",
    },
    {
        "doc_id": "sklearn.generated.sklearn.cluster.kmeans",
        "doc_content": "sklearn KMeans clusters samples into k groups wibbletoken",
    },
)


def _real_shape_rows_source() -> Iterable[Mapping[str, str]]:
    return list(_REAL_SHAPE_ROWS)


def _task(doc_ids: tuple[str, ...], *, library: str = "pandas") -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra={"doc_ids": doc_ids}),
        corpus_source=lambda: Path("/dev/null"),
        metadata={"library": library},
    )


@pytest.mark.asyncio
async def test_index_ignores_corpus_dir_and_search_surfaces_rows() -> None:
    config = AppConfig.load()
    system = PydocsOracleSystem(rows_source=_rows_source)

    try:
        # WHY: corpus_dir is IGNORED — DS-1000's corpus_source is /dev/null;
        # the oracle loads rows from rows_source, not from disk.
        await system.index(Path("/dev/null"), config)

        items = await system.search("pandas merge shared key zorptoken", limit=10)

        assert items, "oracle pipeline returned no items"
        # Every retrieved oracle chunk is a real store row -> chunk_id set.
        assert all(it.chunk_id is not None for it in items)
        # doc_id is stored in metadata["title"] and surfaces as qualified_name.
        names = {it.qualified_name for it in items}
        assert "pandas#merge" in names
        # The targeted query's top hit is the merge doc.
        assert items[0].qualified_name == "pandas#merge"
    finally:
        await system.teardown()


@pytest.mark.asyncio
async def test_registry_builds_oracle_system() -> None:
    system = system_registry.build("pydocs-oracle")
    assert system.name == "pydocs-oracle"


@pytest.mark.asyncio
async def test_oracle_resolver_exact_match_returns_chunk_ids() -> None:
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    config = AppConfig.load()
    system = PydocsOracleSystem(rows_source=_rows_source)
    try:
        await system.index(Path("/dev/null"), config)

        resolver = PydocsOracleGoldResolver(
            build_sqlite_uow_factory(system._db_path),
        )

        # Two of the three pandas doc_ids are gold (plus a non-existent one
        # that must be ignored). EXACT membership on doc_id, NOT fuzzy.
        task = _task(
            ("pandas#merge", "pandas#groupby", "does-not-exist"),
            library="pandas",
        )
        resolved = await resolver.resolve(task, ())

        # Map doc_id -> store id by re-reading the package's chunks.
        uow_factory = build_sqlite_uow_factory(system._db_path)
        async with uow_factory() as uow:
            pandas_chunks = await uow.chunks.list(filter={"package": "pandas"})
        by_doc = {c.metadata.get("title"): c.id for c in pandas_chunks}

        assert resolved == frozenset(
            {f"chunk:{by_doc['pandas#merge']}", f"chunk:{by_doc['pandas#groupby']}"}
        )
        # concat was NOT in the gold doc_ids -> excluded.
        assert f"chunk:{by_doc['pandas#concat']}" not in resolved

        # Coherence with Task 3's _item_key: a retrieved matched item's key
        # is a member of the resolved set.
        items = await system.search("pandas merge shared key zorptoken", limit=10)
        merge_item = next(it for it in items if it.qualified_name == "pandas#merge")
        assert _item_key(merge_item) in resolved
    finally:
        await system.teardown()


@pytest.mark.asyncio
async def test_oracle_resolver_empty_doc_ids_returns_empty_without_db() -> None:
    # WHY: same RepoQA-safety rationale as the fuzzy resolver — an empty
    # doc_ids early-returns BEFORE any DB access. A bogus db path proves the
    # UoW is never opened (a real open would raise).
    resolver = PydocsOracleGoldResolver(
        lambda: (_ for _ in ()).throw(
            AssertionError("uow_factory must not be called on empty doc_ids")
        )
    )
    task = _task((), library="pandas")
    assert await resolver.resolve(task, ()) == frozenset()


@pytest.mark.asyncio
async def test_oracle_resolver_scopes_to_library_package() -> None:
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    config = AppConfig.load()
    system = PydocsOracleSystem(rows_source=_rows_source)
    try:
        await system.index(Path("/dev/null"), config)
        resolver = PydocsOracleGoldResolver(
            build_sqlite_uow_factory(system._db_path),
        )
        # numpy gold doc_ids resolve only against the numpy package's chunks.
        task = _task(("numpy#reshape",), library="numpy")
        resolved = await resolver.resolve(task, ())

        uow_factory = build_sqlite_uow_factory(system._db_path)
        async with uow_factory() as uow:
            numpy_chunks = await uow.chunks.list(filter={"package": "numpy"})
        by_doc = {c.metadata.get("title"): c.id for c in numpy_chunks}

        assert resolved == frozenset({f"chunk:{by_doc['numpy#reshape']}"})
    finally:
        await system.teardown()


@pytest.mark.asyncio
async def test_index_derives_library_from_doc_id_prefix() -> None:
    """REGRESSION: the real ``code-rag-bench/library-documentation`` rows have
    NO ``library`` / ``source`` field — only ``{doc_id, doc_content}``. The
    oracle must DERIVE the package from the ``doc_id``'s first dot-segment
    (``numpy.reference.generated.numpy.imag`` -> ``numpy``;
    ``sklearn.generated.sklearn.cluster.kmeans`` -> ``sklearn`` -> PyPI
    ``scikit-learn``), normalized the SAME way the resolver filters. Pre-fix
    every chunk landed under package ``""`` (resolver scan -> recall 0)."""
    from pydocs_mcp.deps import normalize_package_name
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    config = AppConfig.load()
    system = PydocsOracleSystem(rows_source=_real_shape_rows_source)
    try:
        await system.index(Path("/dev/null"), config)

        uow_factory = build_sqlite_uow_factory(system._db_path)
        async with uow_factory() as uow:
            numpy_chunks = await uow.chunks.list(
                filter={"package": normalize_package_name("numpy")},
            )
            sklearn_chunks = await uow.chunks.list(
                filter={"package": normalize_package_name("scikit-learn")},
            )

        numpy_titles = {c.metadata.get("title") for c in numpy_chunks}
        sklearn_titles = {c.metadata.get("title") for c in sklearn_chunks}
        assert "numpy.reference.generated.numpy.imag" in numpy_titles
        assert "sklearn.generated.sklearn.cluster.kmeans" in sklearn_titles
        # The chunks must NOT have collapsed under the empty package name.
        async with uow_factory() as uow:
            empty_pkg_chunks = await uow.chunks.list(filter={"package": ""})
        assert not empty_pkg_chunks, "chunks must not land under empty package"
    finally:
        await system.teardown()
