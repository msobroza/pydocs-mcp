"""AC #15: corpus-level CALLS resolution rate floor.

Indexes a CURATED fixture corpus at tests/integration/fixtures/ac15_corpus/
through the full extraction → resolution → storage pipeline, then asks
SQLite what fraction of captured ``kind='calls'`` edges got resolved to a
real ``to_node_id`` (i.e. linked into the cross-package qname universe).

The corpus is deterministic — same files, same imports, same call graph
every run. The floor catches REGRESSIONS in the resolver, not venv-shape
drift. (Earlier versions of this test scanned the live worktree, which
made the result depend on which deps were pip-installed: CI venvs gave
one number, local dev venvs with benchmark deps like pandas/numpy gave
another. Same code, different measurements — not a useful regression
detector.)

Spec §16 AC #15 — target: CALLS resolution rate ≥ 35%.

The corpus exercises every resolver rule:

- Rule A / B (exact qname match): cross-module function calls
  (``compute_sum``, ``compute_product``, ``normalize`` from
  ``types_and_helpers``).
- Rule 0 (``self.X.Y`` rewrite): ``Indexer.index_pair`` calls
  ``self.pipeline.process`` — resolver records ``self.pipeline: Pipeline``
  from ``__init__`` and rewrites the call to ``Pipeline.process``.
- Rule 5 (self-method short-circuit): ``Pipeline.process`` calls
  ``self.scale`` — resolved against the enclosing class qname.
- INHERITS edges: ``Base`` → ``Middle`` → ``Leaf`` chain in
  ``inheritance.py``.
- Stdlib bundle lookups: ``hashlib.sha256``, ``json.loads``,
  ``asyncio.to_thread`` — resolved against the pre-baked stdlib qname
  universe merged in by ``IndexingService``.
- Cross-module composition: ``orchestrator.py`` ties everything together
  so the resolver has to walk through multiple packages in one project.
"""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.project_indexer import ProjectIndexer
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction import (
    AstMemberExtractor,
    PipelineChunkExtractor,
    StaticDependencyResolver,
    build_ingestion_pipeline,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import (
    build_sqlite_indexing_service,
    build_sqlite_uow_factory,
)

# Curated, deterministic corpus that drives the floor. ~8 modules, hand-picked
# to exercise every resolver rule (see module docstring). Lives in-tree under
# tests/integration/fixtures/ so it's frozen across machines + venvs.
_FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "ac15_corpus"

# AC #15 spec target. Documented as the long-run goal; the floor below
# tracks the corpus-specific measurement so a real resolver regression
# fails the test.
SPEC_TARGET_AC15 = 0.35

# Empirical floor measured against the fixture corpus at
# tests/integration/fixtures/ac15_corpus/. Floor = MEASURED_RATE - margin
# so the relationship stays mechanical when the resolver moves: bumping
# MEASURED_RATE on an intentional improvement automatically lifts the floor.
#
# Margin (5.8pp) sized to absorb the ~1-edge worth of ripple a single
# fixture change can introduce (the corpus captures ~24 CALLS edges) while
# still catching a real regression of >1 edge.
#
# The corpus is hand-picked to exercise every rule (see module docstring),
# so a rate that drops below the floor almost certainly means a resolver
# behavior change — investigate before lowering MEASURED_RATE.
MEASURED_RATE: float = 0.708  # 17/24 as of the self.X.Y type-inference PR
FLOOR_MARGIN: float = 0.058
EMPIRICAL_FLOOR: float = MEASURED_RATE - FLOOR_MARGIN


async def test_self_index_calls_resolution_rate_floor(tmp_path: Path) -> None:
    """Index fixture corpus → assert CALLS resolution rate above stable floor.

    Prints the actual rate. Future PRs that improve resolution should
    raise ``EMPIRICAL_FLOOR``; a PR that clears 35% closes AC #15 and
    should drop the SPEC vs FLOOR distinction here entirely.
    """
    repo_root = _FIXTURE_CORPUS
    assert (repo_root / "ac15_pkg" / "__init__.py").is_file(), (
        f"expected fixture corpus at {repo_root} — files appear to be missing"
    )

    db_path = tmp_path / "self_index.db"
    open_index_database(db_path).close()

    uow_factory = build_sqlite_uow_factory(db_path)
    indexing_service = build_sqlite_indexing_service(db_path)
    pipeline = build_ingestion_pipeline(AppConfig.load())

    orchestrator = ProjectIndexer(
        indexing_service=indexing_service,
        dependency_resolver=StaticDependencyResolver(),
        chunk_extractor=PipelineChunkExtractor(pipeline=pipeline),
        member_extractor=AstMemberExtractor(),
        uow_factory=uow_factory,
    )

    # ``include_project_source=True`` — that's the WHOLE point. Deps
    # that aren't installed in this venv are skipped (logged as
    # warnings); the rate is computed over whatever capture produced.
    await orchestrator.index_project(
        repo_root,
        force=True,
        include_project_source=True,
        workers=1,
    )

    # Reach through the UoW into the raw connection for a COUNT(*)
    # over ``node_references`` — the only place the resolver's actual
    # hit rate is observable on a fresh DB. Going via
    # ReferenceService would force us to enumerate rows; raw SQL is
    # exactly what AC #15 measures.
    async with uow_factory() as uow:
        conn = uow._held_conn  # noqa: SLF001 -- AC #15 raw-SQL probe
        assert conn is not None, "UoW must hold a live connection"
        total = conn.execute(
            "SELECT COUNT(*) FROM node_references WHERE kind='calls'",
        ).fetchone()[0]
        resolved = conn.execute(
            "SELECT COUNT(*) FROM node_references "
            "WHERE kind='calls' AND to_node_id IS NOT NULL",
        ).fetchone()[0]

    rate = (resolved / total) if total else 0.0
    print(
        f"\nAC #15: {resolved}/{total} CALLS resolved "
        f"({rate:.1%}) — spec target {SPEC_TARGET_AC15:.0%}, "
        f"empirical floor {EMPIRICAL_FLOOR:.0%}",
    )

    # Capture sanity: the fixture corpus deterministically emits ~24 CALLS
    # edges. A drop below 20 means the capture stage itself broke
    # (regression in chunker ref_collector wiring, etc.) rather than just
    # the resolver hit rate.
    assert total >= 20, (
        f"too few CALLS captured ({total}) — the capture stage itself "
        "is probably broken, not just resolution"
    )

    # Stable floor measured against the curated corpus. Floor is well
    # above the spec's 35% target; tracking the higher number locks in
    # the resolver lift PRs that already landed.
    assert rate >= EMPIRICAL_FLOOR, (
        f"CALLS resolution rate {rate:.1%} ({resolved}/{total}) "
        f"is below the empirical floor {EMPIRICAL_FLOOR:.0%}. This "
        "indicates a resolver or capture-stage regression — investigate "
        "before lowering the floor."
    )

    # Upper-bound sanity: if the rate suddenly jumps over 95%, the
    # resolver is probably matching too aggressively (false positives).
    # The corpus has a small handful of unresolvable patterns by design
    # (e.g. ``Path(path).read_text`` chained off a constructor); a
    # near-perfect rate is a bug, not a feature.
    assert rate < 0.95, (
        f"CALLS resolution rate {rate:.1%} unexpectedly high — "
        "the resolver is likely matching false positives. Inspect "
        "``node_references`` for refs whose ``to_node_id`` doesn't "
        "actually match ``to_name``."
    )
