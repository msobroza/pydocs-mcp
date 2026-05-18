"""AC #15: self-index CALLS resolution rate floor.

Load-bearing test that proves the reference resolver actually works on
real code: indexes THIS repo's own source (project + installed deps)
through the full extraction → resolution → storage pipeline, then asks
SQLite what fraction of captured ``kind='calls'`` edges got resolved to
a real ``to_node_id`` (i.e. linked into the cross-package qname
universe).

Spec §16 AC #15 — target: CALLS resolution rate ≥ 35%.

**Current measured rate (post-stdlib-indexing follow-up to #5c): 41.7%**
(9179/22017 on this codebase). This clears the 35% spec target outright —
AC #15 is **met**. The trajectory:

- 11.6% — pre-#5c baseline (before project-qname-prefix fix).
- 16.4% — post-#5c (intra-project edges resolve via ``__project__``
  qname composition).
- 41.7% — post-stdlib-indexing (this PR; ``IndexingService`` merges
  pre-baked stdlib + builtins qnames into the resolver universe, so
  ``isinstance``, ``len``, ``asyncio.to_thread``, ``warnings.warn``,
  ``hashlib.sha256`` etc. now link to real ``to_node_id`` values).

The +25.3pp lift from #5c is dominated by builtins (~40% of CALLS on
this codebase) and stdlib (~10%) becoming resolvable. The remaining
unresolved ~58% is mostly:

- ``self.X.Y`` patterns (Rule 5 short-circuit by design — resolver
  returns None for receiver-typed calls; would need class-context type
  inference to close).
- third-party dep calls whose qname doesn't suffix-uniquely match any
  indexed package (low-confidence rejections — intentional).

Closing the remaining gap requires class-context type inference for
``self.method`` calls — tracked as a future follow-up, NOT a blocker
for AC #15.

This test asserts a stable floor below the current measured rate so
- a regression that drops capture or resolver hit rate gets caught;
- a future PR that lands type-inference and pushes higher is visible
  (and that PR should bump the floor).

The 50-edge minimum is a capture-stage sanity check — well below the
~22k edges this codebase actually produces.
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

# AC #15 spec target. Documented as the long-run goal; the test asserts
# the empirical floor below because the codebase + resolver currently
# sit well below this. A separate PR closes the gap (see module
# docstring for the three available levers).
SPEC_TARGET_AC15 = 0.35

# Empirical floor on this codebase as of the AC #15 stdlib-indexing follow-up
# to #5c. Measured rate: 41.7% (9179/22017). Floor set ~2pp below the measured
# rate so unrelated ripples don't break the test, but a real resolver
# regression does.
#
# Rate trajectory:
#   - 11.6% — pre-#5c baseline (before project-qname-prefix fix)
#   - 16.4% — post-#5c (project-qname-prefix fix landed; intra-project resolved)
#   - 41.7% — post-stdlib-idx (this PR; stdlib + builtins targets resolve)
#
# Spec AC #15 target is 35% and is now MET (41.7% > 35%). The remaining
# unresolved ~58% is dominated by self.X.Y refs which Rule 5 short-circuits
# by design — closing that gap requires class-context type inference,
# tracked as a future follow-up.
EMPIRICAL_FLOOR: float = 0.39


async def test_self_index_calls_resolution_rate_floor(tmp_path: Path) -> None:
    """Self-index this repo → assert CALLS resolution rate above stable floor.

    Prints the actual rate. Future PRs that improve resolution should
    raise ``EMPIRICAL_FLOOR``; a PR that clears 35% closes AC #15 and
    should drop the SPEC vs FLOOR distinction here entirely.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    assert (repo_root / "python" / "pydocs_mcp").is_dir(), (
        f"expected repo_root={repo_root} to contain python/pydocs_mcp/ — "
        "the test's parent.parent.parent walk is wrong for this layout"
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

    # Capture sanity: capture stage emitting <50 edges across the whole
    # repo means it's structurally broken (regression in chunker
    # ref_collector wiring, etc.). Healthy capture sits in the tens of
    # thousands.
    assert total >= 50, (
        f"too few CALLS captured ({total}) — the capture stage itself "
        "is probably broken, not just resolution"
    )

    # Stable floor — see module docstring for why this is below the
    # spec's 35% target.
    assert rate >= EMPIRICAL_FLOOR, (
        f"CALLS resolution rate {rate:.1%} ({resolved}/{total}) "
        f"is below the empirical floor {EMPIRICAL_FLOOR:.0%}. This "
        "indicates a resolver or capture-stage regression — investigate "
        "before lowering the floor."
    )

    # Upper-bound sanity: if the rate suddenly jumps over 95%, the
    # resolver is probably matching too aggressively (false positives).
    # The natural ceiling on this codebase is ~30% (see module docstring
    # for why); a near-perfect rate is a bug, not a feature.
    assert rate < 0.95, (
        f"CALLS resolution rate {rate:.1%} unexpectedly high — "
        "the resolver is likely matching false positives. Inspect "
        "``node_references`` for refs whose ``to_node_id`` doesn't "
        "actually match ``to_name``."
    )
