"""run_index_pass — the write-side index-pass sequence (application layer)."""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from pydocs_mcp.application.index_project import run_index_pass, stamped_grammars
from pydocs_mcp.application.indexing_service import IndexingStats
from pydocs_mcp.storage.index_metadata import IndexMetadata, PriorBundleState

# What the fake process "can load"; deliberately a strict subset of the real
# extension set so widening and narrowing are both observable.
_FAKE_FINGERPRINT = ".c,.rs,.ts"


class FakeIndexOrchestrator:
    """Named fake for ProjectIndexer — records index_project kwargs."""

    def __init__(
        self,
        calls: list[str],
        stats: IndexingStats,
        *,
        raises: Exception | None = None,
    ) -> None:
        self._calls = calls
        self._stats = stats
        self._raises = raises
        self.kwargs: dict[str, object] | None = None

    async def index_project(self, project: Path, **kwargs: object) -> IndexingStats:
        self._calls.append("index_project")
        self.kwargs = {"project": project, **kwargs}
        if self._raises is not None:
            raise self._raises
        return self._stats


class FakeInvalidatingService:
    """Named fake for IndexingService — records the invalidation call."""

    def __init__(self, calls: list[str], stale: list[str]) -> None:
        self._calls = calls
        self._stale = stale
        self.current_model: str | None = None

    async def invalidate_stale_embeddings(self, *, current_model: str) -> list[str]:
        self._calls.append("invalidate")
        self.current_model = current_model
        return self._stale


def _harness(
    *,
    repaired: list[str] | None = None,
    stale: list[str] | None = None,
    orchestrator_raises: Exception | None = None,
    rebuild_fts_raises: Exception | None = None,
    prior: PriorBundleState | None = None,
    failed: int = 0,
    fingerprint: str = _FAKE_FINGERPRINT,
):
    calls: list[str] = []
    stamped: list[IndexMetadata] = []
    # Default: a fresh, never-stamped bundle — the shape every pre-existing
    # test here assumed. Stamp-policy tests pass an explicit prior state.
    prior_state = prior if prior is not None else PriorBundleState.empty()

    async def check_integrity() -> list[str]:
        calls.append("check_integrity")
        return list(repaired or [])

    async def rebuild_fts() -> None:
        calls.append("rebuild_fts")
        if rebuild_fts_raises is not None:
            raise rebuild_fts_raises

    def stamp_metadata(meta: IndexMetadata) -> None:
        calls.append("stamp_metadata")
        stamped.append(meta)

    def read_prior_state() -> PriorBundleState:
        calls.append("read_prior_state")
        return prior_state

    def grammar_fingerprint() -> str:
        # Injected rather than the live `loadable_grammar_fingerprint()`: on a
        # wheel-less environment the live value is "" and a stamp mutated to
        # "" would pass unnoticed (review mutant (e)).
        return fingerprint

    async def write_aggregates(_project: Path) -> None:
        calls.append("write_aggregates")

    orchestrator = FakeIndexOrchestrator(
        calls, IndexingStats(indexed=2, cached=1, failed=failed), raises=orchestrator_raises
    )
    service = FakeInvalidatingService(calls, list(stale or []))
    # The stamp's two collaborators ride on the stamp callback's tuple slot so
    # the many positional `_run(...)` callers stay unchanged: `_run` unpacks it.
    return (
        calls,
        stamped,
        orchestrator,
        service,
        check_integrity,
        rebuild_fts,
        (stamp_metadata, read_prior_state, grammar_fingerprint),
        write_aggregates,
    )


async def _run(
    orchestrator,
    service,
    check_integrity,
    rebuild_fts,
    stamp_metadata,
    write_aggregates,
    *,
    force=False,
    project=Path("/tmp/proj"),
    include_project_source=True,
    include_dependencies=False,
):
    stamp, read_prior_state, grammar_fingerprint = stamp_metadata
    return await run_index_pass(
        orchestrator=orchestrator,
        indexing_service=service,
        pipeline_hash="hash-1",
        project=project,
        embedding_provider="fastembed",
        embedding_model="model-b",
        embedding_dim=384,
        force=force,
        include_project_source=include_project_source,
        include_dependencies=include_dependencies,
        workers=3,
        check_integrity=check_integrity,
        rebuild_fts=rebuild_fts,
        stamp_metadata=stamp,
        read_prior_state=read_prior_state,
        grammar_fingerprint=grammar_fingerprint,
        write_aggregates=write_aggregates,
    )


async def _run_index_pass_with_fakes(*, project: Path, stamp_metadata) -> None:
    """Drive ``run_index_pass`` with this file's fakes, overriding only ``project``.

    Used by the git-head stamp tests, which care solely about ``project`` (whether
    it is a git repo) and the ``stamp_metadata`` callback that captures the result.
    """
    _calls, _stamped, orch, svc, ci, rf, (_stamp, read_prior, fp), wa = _harness()
    await _run(orch, svc, ci, rf, (stamp_metadata, read_prior, fp), wa, project=project)


async def test_sequence_and_forwarding() -> None:
    calls, _stamped, orch, svc, ci, rf, sm, wa = _harness()
    stats = await _run(orch, svc, ci, rf, sm, wa)

    # `read_prior_state` runs BEFORE `index_project`: the pass populates the
    # packages table, and the stamp policy needs what the bundle held first.
    assert calls == [
        "check_integrity",
        "invalidate",
        "read_prior_state",
        "index_project",
        "rebuild_fts",
        "stamp_metadata",
        "write_aggregates",
    ]
    assert stats.indexed == 2
    assert stats.cached == 1
    assert svc.current_model == "model-b"
    assert orch.kwargs == {
        "project": Path("/tmp/proj"),
        "force": False,
        "include_project_source": True,
        "include_dependencies": False,
        "workers": 3,
    }


async def test_force_skips_stale_invalidation() -> None:
    calls, _stamped, orch, svc, ci, rf, sm, wa = _harness()
    await _run(orch, svc, ci, rf, sm, wa, force=True)

    assert "invalidate" not in calls
    assert orch.kwargs is not None
    assert orch.kwargs["force"] is True


async def test_metadata_stamp_carries_identity_and_recency() -> None:
    _calls, stamped, orch, svc, ci, rf, sm, wa = _harness()
    await _run(orch, svc, ci, rf, sm, wa)

    (meta,) = stamped
    assert meta.project_name == "proj"
    assert meta.project_root == "/tmp/proj"
    assert meta.embedding_provider == "fastembed"
    assert meta.embedding_model == "model-b"
    assert meta.embedding_dim == 384
    assert meta.pipeline_hash == "hash-1"
    assert meta.indexed_at > 0.0


async def test_stamp_includes_git_head_when_project_is_a_repo(tmp_path) -> None:
    # Arrange a minimal git layout so resolve_git_head returns a sha.
    sha = "d" * 40
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text(f"{sha}\n")

    stamped: list = []
    await _run_index_pass_with_fakes(project=tmp_path, stamp_metadata=stamped.append)

    assert stamped and stamped[0].git_head == sha


async def test_stamp_git_head_empty_for_non_git_tree(tmp_path) -> None:
    stamped: list = []
    await _run_index_pass_with_fakes(project=tmp_path, stamp_metadata=stamped.append)
    assert stamped and stamped[0].git_head == ""


async def test_repair_and_stale_warnings_logged(caplog) -> None:
    calls, _stamped, orch, svc, ci, rf, sm, wa = _harness(
        repaired=["numpy"], stale=["fastapi", "attrs"]
    )
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _run(orch, svc, ci, rf, sm, wa)

    assert "Cache integrity" in caplog.text
    assert "Embedding model changed; re-embedding 2 package(s): fastapi, attrs" in caplog.text


async def test_force_logs_cache_cleared(caplog) -> None:
    _calls, _stamped, orch, svc, ci, rf, sm, wa = _harness()
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _run(orch, svc, ci, rf, sm, wa, force=True)

    assert "Cache cleared" in caplog.text


def test_run_index_pass_is_package_exported() -> None:
    import pydocs_mcp.application as application

    assert application.run_index_pass is run_index_pass


async def test_stamp_withheld_when_orchestrator_index_project_raises() -> None:
    """Stamp-last design: a crash inside index_project must leave no stamp.

    A half-indexed db must never carry the identity stamp — a portable load
    or multi-repo router trusts the stamp to mean "fully indexed", and a
    stale/mismatched-embedder .tq that slips past that check returns garbage
    dense-similarity scores with no error.
    """
    calls, stamped, orch, svc, ci, rf, sm, wa = _harness(
        orchestrator_raises=RuntimeError("boom mid-index")
    )

    with pytest.raises(RuntimeError, match="boom mid-index"):
        await _run(orch, svc, ci, rf, sm, wa)

    assert stamped == []
    assert "stamp_metadata" not in calls
    assert "rebuild_fts" not in calls
    assert "write_aggregates" not in calls
    # Sanity: the crash happened where we intended it to.
    assert calls == ["check_integrity", "invalidate", "read_prior_state", "index_project"]


async def test_stamp_withheld_when_rebuild_fts_raises() -> None:
    """Same stamp-last guarantee when the crash happens after index_project,
    inside the FTS rebuild step — the stamp call must still never fire.
    """
    calls, stamped, orch, svc, ci, rf, sm, wa = _harness(
        rebuild_fts_raises=RuntimeError("fts rebuild exploded")
    )

    with pytest.raises(RuntimeError, match="fts rebuild exploded"):
        await _run(orch, svc, ci, rf, sm, wa)

    assert stamped == []
    assert "stamp_metadata" not in calls
    assert "write_aggregates" not in calls
    assert calls == [
        "check_integrity",
        "invalidate",
        "read_prior_state",
        "index_project",
        "rebuild_fts",
    ]


async def test_stamp_records_the_fingerprint_the_pass_read(tmp_path: Path) -> None:
    """A fresh bundle stamps exactly what ``grammar_fingerprint`` reported for
    this pass — in production the SAME memoized verdict the content-hash salt
    read, so the stamped set can never disagree with what extraction captured
    (issue #246 item 3)."""
    stamped: list[IndexMetadata] = []
    await _run_index_pass_with_fakes(project=tmp_path, stamp_metadata=stamped.append)
    (meta,) = stamped
    assert meta.loadable_grammars == _FAKE_FINGERPRINT


def test_the_default_fingerprint_is_the_chunkers_memoized_verdict() -> None:
    """The composition root passes no provider, so the default must be the one
    memo the salt reads — two verdicts would let the stamp and the hash drift."""
    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        loadable_grammar_fingerprint,
    )

    default = inspect.signature(run_index_pass).parameters["grammar_fingerprint"].default
    assert default is loadable_grammar_fingerprint


# --- the stamp describes what the pass COVERED, not what the process can load -
#
# The grammar salt in a package's content hash re-extracts that package when
# the fingerprint changes — but only for packages the pass VISITS. A skipped
# scope that holds rows (`--skip-deps` on a bundle whose dependencies were
# indexed earlier; `serve --watch` inherits the flags) or a dependency whose
# re-extraction failed keeps rows captured under an older set. A stamp that
# simply advertised the process fingerprint then vouched for rows nobody
# re-checked: `syntactic` over an empty graph in one direction, `unavailable`
# over a real one in the other (issue #246 item 3 review, reproduced with
# real passes). Scopes that hold NO rows have nothing to protect, so the
# common `serve --skip-deps --watch` deployment still picks a grammar install
# up on its next pass.


def _intersect(stamp: str, fingerprint: str) -> str:
    return ",".join(sorted(set(stamp.split(",")) & set(fingerprint.split(","))))


async def _stamp_after(
    prior: PriorBundleState,
    *,
    project: bool = True,
    dependencies: bool = True,
    failed: int = 0,
    force: bool = False,
) -> str:
    _calls, stamped, orch, svc, ci, rf, sm, wa = _harness(prior=prior, failed=failed)
    await _run(
        orch,
        svc,
        ci,
        rf,
        sm,
        wa,
        force=force,
        include_project_source=project,
        include_dependencies=dependencies,
    )
    (meta,) = stamped
    return meta.loadable_grammars


_STAMPED_BOTH_SCOPES = PriorBundleState(".rs,.ts", has_project_rows=True, has_dependency_rows=True)


async def test_a_full_clean_pass_stamps_the_process_fingerprint() -> None:
    assert await _stamp_after(_STAMPED_BOTH_SCOPES) == _FAKE_FINGERPRINT


async def test_skipping_a_scope_that_holds_rows_never_widens_the_stamp() -> None:
    prior = PriorBundleState(".rs", has_project_rows=True, has_dependency_rows=True)
    assert await _stamp_after(prior, dependencies=False) == ".rs"
    assert await _stamp_after(prior, project=False) == ".rs"


async def test_a_partial_pass_still_drops_what_the_process_lost() -> None:
    """Narrowing is always safe: the unvisited scope may still hold a real
    `.js` graph, but declining it is a missing claim, never a wrong one."""
    prior = PriorBundleState(".js,.rs", has_project_rows=True, has_dependency_rows=True)
    assert await _stamp_after(prior, dependencies=False) == ".rs"


async def test_skipping_a_scope_that_holds_no_rows_stamps_in_full() -> None:
    """`serve --skip-deps --watch` from day one never indexes dependencies, so
    every row the bundle holds sits in the scope the pass re-checked — a
    grammar install must take effect on the next watch pass, not wait for a
    full `index` run that would also index every dependency."""
    prior = PriorBundleState("", has_project_rows=True, has_dependency_rows=False)
    assert await _stamp_after(prior, dependencies=False) == _FAKE_FINGERPRINT


async def test_a_failed_dependency_never_widens_the_stamp() -> None:
    """Full flags, but one dependency's re-extraction raised: its old rows stay
    (`stats.failed`), captured under whatever set loaded back then."""
    got = await _stamp_after(_STAMPED_BOTH_SCOPES, failed=1)
    assert got == _intersect(".rs,.ts", _FAKE_FINGERPRINT)


async def test_a_failed_dependency_without_dependency_rows_is_harmless() -> None:
    """A first pass with one uninspectable dependency is common; that package
    holds no rows, so nothing stale exists to protect."""
    prior = PriorBundleState("", has_project_rows=True, has_dependency_rows=False)
    assert await _stamp_after(prior, failed=1) == _FAKE_FINGERPRINT


async def test_a_partial_pass_on_an_unstamped_bundle_with_rows_stays_unstamped() -> None:
    """Rows captured under an unknown set (a pre-stamp bundle) can never be
    vouched for by a pass that did not re-check them."""
    prior = PriorBundleState("", has_project_rows=True, has_dependency_rows=True)
    assert await _stamp_after(prior, dependencies=False) == ""


async def test_a_partial_pass_on_an_empty_bundle_stamps_the_fingerprint() -> None:
    """`pydocs-mcp index . --skip-deps` on a fresh cache is the common first
    pass: nothing stale can exist, so the stamp is exact."""
    assert await _stamp_after(PriorBundleState.empty(), dependencies=False) == _FAKE_FINGERPRINT


async def test_a_forced_pass_stamps_the_fingerprint_without_reading_the_bundle() -> None:
    """`--force` wipes the bundle inside the pass (`IndexingService.clear_all`):
    nothing older survives it, not even a failed dependency's rows, so the
    prior state is moot and is not read."""
    prior = PriorBundleState("", has_project_rows=True, has_dependency_rows=True)
    calls, stamped, orch, svc, ci, rf, sm, wa = _harness(prior=prior, failed=1)
    await _run(orch, svc, ci, rf, sm, wa, force=True, include_dependencies=False)
    assert stamped[0].loadable_grammars == _FAKE_FINGERPRINT
    assert "read_prior_state" not in calls


async def test_prior_state_is_read_before_the_pass_runs() -> None:
    """The pass populates the packages table; the evidence of what the bundle
    held must be captured first."""
    calls, _stamped, orch, svc, ci, rf, sm, wa = _harness()
    await _run(orch, svc, ci, rf, sm, wa)
    assert calls.index("read_prior_state") < calls.index("index_project")


async def test_a_withheld_stamp_is_logged_with_the_way_out(caplog) -> None:
    """An operator who just installed grammar wheels and sees `unavailable`
    needs to learn WHY from the index log, and what run fixes it."""
    prior = PriorBundleState(".rs", has_project_rows=True, has_dependency_rows=True)
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await _stamp_after(prior, dependencies=False)
    assert "Grammar stamp withheld for .c,.ts" in caplog.text
    assert "pydocs-mcp index" in caplog.text


async def test_a_full_stamp_logs_no_withheld_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await _stamp_after(_STAMPED_BOTH_SCOPES)
    assert "Grammar stamp withheld" not in caplog.text


@pytest.mark.parametrize(
    ("prior", "fingerprint", "project", "dependencies", "failed", "expected"),
    [
        # A complete, clean pass stamps the fingerprint.
        (PriorBundleState(".rs,.ts", True, True), ".c,.rs,.ts", True, True, 0, ".c,.rs,.ts"),
        # A skipped scope that holds rows never widens (either scope).
        (PriorBundleState(".rs,.ts", True, True), ".c,.rs,.ts", True, False, 0, ".rs,.ts"),
        (PriorBundleState(".rs,.ts", True, True), ".c,.rs,.ts", False, True, 0, ".rs,.ts"),
        # Narrowing always applies.
        (PriorBundleState(".rs,.ts", True, True), ".rs", True, False, 0, ".rs"),
        # A skipped scope that holds NO rows leaves nothing unchecked.
        (PriorBundleState(".rs", True, False), ".c,.rs", True, False, 0, ".c,.rs"),
        (PriorBundleState(".rs", False, True), ".c,.rs", False, True, 0, ".c,.rs"),
        # A failed dependency never widens — unless no dependency rows existed.
        (PriorBundleState(".rs", True, True), ".c,.rs", True, True, 1, ".rs"),
        (PriorBundleState(".rs", True, False), ".c,.rs", True, True, 1, ".c,.rs"),
        # Unstamped with rows stays unstamped; an empty bundle is exact.
        (PriorBundleState("", True, True), ".rs", True, False, 0, ""),
        (PriorBundleState("", False, False), ".rs", True, False, 0, ".rs"),
        # A process that lost every grammar stamps "" either way.
        (PriorBundleState(".rs", True, True), "", True, False, 0, ""),
        (PriorBundleState(".rs", True, True), "", True, True, 0, ""),
    ],
)
def test_stamped_grammars_policy(prior, fingerprint, project, dependencies, failed, expected):
    got = stamped_grammars(
        prior,
        fingerprint,
        project_visited=project,
        dependencies_visited=dependencies,
        failed=failed,
    )
    assert got == expected
