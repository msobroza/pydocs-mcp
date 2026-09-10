"""Target-resolution fallbacks (spec 2026-09-10 §2.2, §2.4, §3, §4).

Pure helpers plus ``ProjectTargetResolver`` over ``make_fake_uow_factory``;
no hook is wired yet, so every case drives the module directly.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator, Sequence

import pytest

from pydocs_mcp.application import target_resolution as tr
from pydocs_mcp.application.mcp_inputs import is_symbol_target
from pydocs_mcp.application.protocols import TargetResolver
from pydocs_mcp.application.target_resolution import (
    NullTargetResolver,
    ProjectTargetResolver,
    TargetResolution,
    TargetRewrite,
    is_resolvable_symbol_name,
    is_stripped_source_root,
    render_miss_message,
)
from pydocs_mcp.models import Chunk, ChunkSymbolName, Package, PackageOrigin
from pydocs_mcp.retrieval.config import TargetResolutionConfig
from tests._fakes import (
    FakeTargetResolver,
    InMemoryChunkStore,
    InMemoryPackageStore,
    make_fake_uow_factory,
)

_P = "__project__"


def _chunk(qname: str, module: str, source_path: str | None, *, package: str = _P) -> Chunk:
    md: dict[str, object] = {"package": package, "module": module, "qualified_name": qname}
    if source_path is not None:
        md["source_path"] = source_path
    return Chunk(text=f"body {qname}", metadata=md)


_STRAT = "src/needle/scoring/strategies.py"
_NEEDLE_ROWS = (
    _chunk("needle.scoring.strategies", "needle.scoring.strategies", _STRAT),
    _chunk("needle.scoring.strategies.MaxSimScorer", "needle.scoring.strategies", _STRAT),
    _chunk("needle.scoring.strategies.MaxSimScorer.score", "needle.scoring.strategies", _STRAT),
    _chunk("needle.scoring.strategies.CosineScorer", "needle.scoring.strategies", _STRAT),
    _chunk("needle.scoring.strategies.CosineScorer.score", "needle.scoring.strategies", _STRAT),
    _chunk("needle.scoring.strategies.ScoringStrategy.score", "needle.scoring.strategies", _STRAT),
    _chunk("needle.scoring", "needle.scoring", "src/needle/scoring/__init__.py"),
    _chunk("needle.cli.main", "needle.cli", "src/needle/cli.py"),
    _chunk("run.main", "run", "scripts/run.py"),
    _chunk("needle_core.domain.ranking.Score", "needle_core.domain.ranking", "src/nc/ranking.py"),
    _chunk("needle.scoring.strategies.__imports__", "needle.scoring.strategies", _STRAT),
    _chunk("AGENTS.md", "AGENTS.md", "AGENTS.md"),
    _chunk("pyproject.toml", "pyproject.toml", "pyproject.toml"),
    _chunk("src.example_needle.egg-info.SOURCES.txt#L1-80", "x", "SOURCES.txt"),
)


def _package(name: str) -> Package:
    return Package(
        name=name,
        version="1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


async def _resolver(
    chunks: tuple[Chunk, ...] = _NEEDLE_ROWS,
    *,
    packages: tuple[str, ...] = (),
    **flags: object,
) -> ProjectTargetResolver:
    store = InMemoryChunkStore()
    await store.upsert(chunks)
    pkgs = InMemoryPackageStore(items={n: _package(n) for n in packages})
    factory = make_fake_uow_factory(chunks=store, packages=pkgs)
    return ProjectTargetResolver(factory, TargetResolutionConfig(**flags))  # type: ignore[arg-type]


# ── is_stripped_source_root [AC3] ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("segment", "source_path", "module", "expected"),
    [
        ("src", "src/needle/scoring/strategies.py", "needle.scoring.strategies", True),
        ("src", "src/needle/scoring/__init__.py", "needle.scoring", True),
        ("src", "needle/scoring/strategies.py", "needle.scoring.strategies", False),
        ("src", "src/docs/guide.md", "docs.guide", False),
        ("src", "src\\needle\\scoring\\strategies.py", "needle.scoring.strategies", True),
        ("src", "lib/needle/scoring/strategies.py", "needle.scoring.strategies", False),
        ("src", "src/lib/needle/strategies.py", "needle.strategies", False),
        ("src", None, "needle.strategies", False),
    ],
)
def test_is_stripped_source_root_table(
    segment: str, source_path: str | None, module: str, expected: bool
) -> None:
    assert is_stripped_source_root(segment, source_path, module) is expected


# ── is_resolvable_symbol_name [AC2, AC6] ──────────────────────────────────


@pytest.mark.parametrize(
    ("row", "entry", "expected"),
    [
        (ChunkSymbolName("pkg.mod.Cls", "pkg.mod", "pkg/mod.py"), "lookup", True),
        (ChunkSymbolName("pkg.SOURCES.txt#L1-80", "pkg", "pkg/x.py"), "lookup", False),
        (ChunkSymbolName(".github.workflows.ci", ".github", "x.py"), "lookup", False),
        (ChunkSymbolName("AGENTS.md", "AGENTS.md", "AGENTS.md"), "lookup", False),
        (ChunkSymbolName("pyproject.toml", "pyproject.toml", "pyproject.toml"), "lookup", False),
        (ChunkSymbolName("pkg.mod", "pkg.mod", "pkg/notes.md"), "lookup", False),
        (ChunkSymbolName("pkg.mod.__imports__", "pkg.mod", "pkg/mod.py"), "lookup", False),
        (ChunkSymbolName("pkg.mod.Cls", "pkg.mod", None), "lookup", False),
        (ChunkSymbolName("pkg.mod", "pkg.mod", "pkg/mod.py"), "lookup", True),
        (ChunkSymbolName("pkg.mod", "pkg.mod", "pkg/mod.py"), "source", True),
        (ChunkSymbolName("pkg.mod", "pkg.mod", "pkg/mod.py"), "context", False),
        (ChunkSymbolName("pkg.mod.Cls", "pkg.mod", "pkg/mod.py"), "context", True),
    ],
)
def test_is_resolvable_symbol_name(row: ChunkSymbolName, entry: str, expected: bool) -> None:
    assert is_resolvable_symbol_name(row, entry) is expected  # type: ignore[arg-type]


# ── Rule 1: source-root strip [AC1, AC2, AC3, AC12] ───────────────────────


async def test_rule1_strips_the_source_root_segment() -> None:
    resolver = await _resolver()
    res = await resolver.resolve("src.needle.scoring.strategies.MaxSimScorer", entry="lookup")
    assert res.rewrite == TargetRewrite(
        rule="source_root_strip",
        canonical="needle.scoring.strategies.MaxSimScorer",
        module="needle.scoring.strategies",
        symbol_path=("MaxSimScorer",),
    )
    assert res.candidates == ()


async def test_rule1_strip_resolves_a_method_path() -> None:
    resolver = await _resolver()
    res = await resolver.resolve(
        "src.needle.scoring.strategies.MaxSimScorer.score", entry="context"
    )
    assert res.rewrite is not None
    assert res.rewrite.symbol_path == ("MaxSimScorer", "score")


async def test_rule1_is_shadowed_by_an_indexed_package_named_like_the_segment() -> None:
    resolver = await _resolver(packages=("src",))
    res = await resolver.resolve("src.needle.scoring.strategies.MaxSimScorer", entry="lookup")
    assert res.rewrite is None


async def test_rule1_module_only_strip_rewrites_for_lookup_but_not_context() -> None:
    resolver = await _resolver()
    lookup = await resolver.resolve("src.needle.scoring.strategies", entry="lookup")
    context = await resolver.resolve("src.needle.scoring.strategies", entry="context")
    assert lookup.rewrite is not None
    assert lookup.rewrite.module == "needle.scoring.strategies"
    assert lookup.rewrite.symbol_path == ()
    assert context.rewrite is None
    assert "needle.scoring.strategies" not in context.candidates


async def test_rule1_rejects_a_hit_whose_source_path_does_not_prove_the_strip() -> None:
    rows = (_chunk("needle.mod.Cls", "needle.mod", "lib/needle/mod.py"),)
    resolver = await _resolver(rows)
    res = await resolver.resolve("src.needle.mod.Cls", entry="lookup")
    assert res.rewrite is None


async def test_rule1_off_does_not_strip() -> None:
    resolver = await _resolver(source_root_strip=False)
    res = await resolver.resolve("src.needle.scoring.strategies.MaxSimScorer", entry="lookup")
    assert res.rewrite is None


# ── Rule 2: unique bare name [AC4, AC5, AC6, AC12] ────────────────────────


async def test_rule2_unique_bare_name_rewrites() -> None:
    resolver = await _resolver()
    res = await resolver.resolve("MaxSimScorer", entry="lookup")
    assert res.rewrite == TargetRewrite(
        rule="unique_bare_name",
        canonical="needle.scoring.strategies.MaxSimScorer",
        module="needle.scoring.strategies",
        symbol_path=("MaxSimScorer",),
    )
    assert res.exact_leaf_count == 1
    assert res.ambiguous is False


async def test_rule2_two_matches_are_ambiguous_with_sorted_candidates() -> None:
    resolver = await _resolver()
    res = await resolver.resolve("main", entry="lookup")
    assert res.rewrite is None
    assert res.ambiguous is True
    assert res.exact_leaf_count == 2
    assert res.candidates == ("needle.cli.main", "run.main")


async def test_rule2_exact_leaf_is_case_sensitive() -> None:
    resolver = await _resolver()
    res = await resolver.resolve("score", entry="lookup")
    assert res.candidates == (
        "needle.scoring.strategies.CosineScorer.score",
        "needle.scoring.strategies.MaxSimScorer.score",
        "needle.scoring.strategies.ScoringStrategy.score",
    )
    assert res.exact_leaf_count == 3
    assert "needle_core.domain.ranking.Score" not in res.candidates


@pytest.mark.parametrize("target", ["md", "toml", "__imports__"])
async def test_rule2_non_code_leaves_neither_rewrite_nor_suggest(target: str) -> None:
    resolver = await _resolver()
    res = await resolver.resolve(target, entry="lookup")
    assert res == TargetResolution()


async def test_rule2_module_only_match_counts_for_lookup_not_context() -> None:
    resolver = await _resolver()
    lookup = await resolver.resolve("scoring", entry="lookup")
    context = await resolver.resolve("scoring", entry="context")
    assert lookup.rewrite is not None
    assert lookup.rewrite.canonical == "needle.scoring"
    assert lookup.rewrite.symbol_path == ()
    assert context.rewrite is None
    assert context.candidates == ()


async def test_rule2_off_still_produces_candidates() -> None:
    resolver = await _resolver(unique_bare_name=False)
    res = await resolver.resolve("MaxSimScorer", entry="lookup")
    assert res.rewrite is None
    assert res.candidates == ("needle.scoring.strategies.MaxSimScorer",)


async def test_miss_candidates_off_produces_no_candidates() -> None:
    resolver = await _resolver(miss_candidates=False)
    res = await resolver.resolve("main", entry="lookup")
    assert res.candidates == ()
    assert res.rewrite is None


# ── truncated scan [AC15] ─────────────────────────────────────────────────


async def test_truncated_scan_never_rewrites_nor_suggests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tr, "_SYMBOL_NAME_SCAN_LIMIT", 3)
    resolver = await _resolver()
    bare = await resolver.resolve("MaxSimScorer", entry="lookup")
    dotted = await resolver.resolve("needle.scoring.MaxSimScorer", entry="lookup")
    assert bare == TargetResolution(scan_truncated=True)
    assert dotted == TargetResolution(scan_truncated=True)


async def test_scan_reads_limit_plus_one_rows() -> None:
    store = InMemoryChunkStore()
    await store.upsert(_NEEDLE_ROWS)
    resolver = ProjectTargetResolver(make_fake_uow_factory(chunks=store), TargetResolutionConfig())
    await resolver.resolve("MaxSimScorer", entry="lookup")
    scans = [c.payload for c in store.calls if c.method == "list_symbol_names"]
    assert scans == [{"package": _P, "limit": tr._SYMBOL_NAME_SCAN_LIMIT + 1}]


# ── Rule 3: candidate ranking [AC7, AC8] ──────────────────────────────────


async def test_rule3_tier1_ranks_by_shared_trailing_segments() -> None:
    rows = (
        _chunk("a.z.Cls", "a.z", "a/z.py"),
        _chunk("b.y.Cls", "b.y", "b/y.py"),
        _chunk("c.x.Cls", "c.x", "c/x.py"),
    )
    resolver = await _resolver(rows)
    res = await resolver.resolve("q.y.Cls", entry="lookup")
    assert res.candidates == ("b.y.Cls", "a.z.Cls", "c.x.Cls")
    assert res.ambiguous is False


@pytest.mark.parametrize("target", ["MxaSimScorer", "maxsimscorer", "MaxSimScorr"])
async def test_rule3_tier2_finds_typos_and_case_variants(target: str) -> None:
    resolver = await _resolver()
    res = await resolver.resolve(target, entry="lookup")
    assert res.rewrite is None
    assert res.candidates == ("needle.scoring.strategies.MaxSimScorer",)


async def test_rule3_tier2_cutoff_excludes_score_vs_scoring() -> None:
    rows = (_chunk("pkg.scoring", "pkg.scoring", "pkg/scoring.py"),)
    resolver = await _resolver(rows)
    res = await resolver.resolve("pkg.score", entry="lookup")
    assert res.candidates == ()


async def test_rule3_caps_candidates_and_keeps_the_total() -> None:
    rows = tuple(_chunk(f"m{i}.run", f"m{i}", f"m{i}.py") for i in range(7))
    resolver = await _resolver(rows, max_candidates=3)
    res = await resolver.resolve("run", entry="lookup")
    assert res.candidates == ("m0.run", "m1.run", "m2.run")
    assert res.candidate_total == 7


async def test_rule3_re_export_yields_candidates_and_never_a_rewrite() -> None:
    rows = (
        _chunk("srcpkg", "srcpkg", "src/srcpkg/__init__.py"),
        _chunk("srcpkg.scoring.MaxSimScorer", "srcpkg.scoring", "src/srcpkg/scoring.py"),
    )
    resolver = await _resolver(rows)
    res = await resolver.resolve("srcpkg.MaxSimScorer", entry="lookup")
    assert res.rewrite is None
    assert res.candidates == ("srcpkg.scoring.MaxSimScorer",)


async def test_rule3_pool_is_the_indexed_dependency_named_by_the_first_segment() -> None:
    rows = (
        _chunk("dep.api.Router", "dep.api", "dep/api.py", package="dep"),
        _chunk("proj.api.Router", "proj.api", "proj/api.py"),
    )
    resolver = await _resolver(rows, packages=("dep",))
    res = await resolver.resolve("dep.Router", entry="lookup")
    assert res.candidates == ("dep.api.Router",)


async def _resolve_counting_packages(
    target: str, *, packages: tuple[str, ...] = (), **flags: object
) -> tuple[TargetResolution, list[object]]:
    """Resolve ``target`` and return the ``packages`` reads it performed."""
    store = InMemoryChunkStore()
    await store.upsert(_NEEDLE_ROWS)
    pkgs = InMemoryPackageStore(items={n: _package(n) for n in packages})
    factory = make_fake_uow_factory(chunks=store, packages=pkgs)
    resolver = ProjectTargetResolver(factory, TargetResolutionConfig(**flags))  # type: ignore[arg-type]
    res = await resolver.resolve(target, entry="lookup")
    return res, [c.payload for c in pkgs.calls if c.method == "get"]


async def test_a_dotted_miss_reads_packages_once() -> None:
    res, reads = await _resolve_counting_packages("needle.scoring.MaxSimScorer")
    assert res.candidates == ("needle.scoring.strategies.MaxSimScorer",)
    assert reads == ["needle"]


async def test_a_dependency_shadowed_dotted_miss_reads_packages_once() -> None:
    res, reads = await _resolve_counting_packages(
        "needle.scoring.MaxSimScorer", packages=("needle",)
    )
    assert res.rewrite is None
    assert reads == ["needle"]


async def test_a_dotted_miss_with_both_dotted_rules_off_reads_no_packages() -> None:
    res, reads = await _resolve_counting_packages(
        "needle.scoring.MaxSimScorer", source_root_strip=False, miss_candidates=False
    )
    assert res == TargetResolution()
    assert reads == []


async def test_every_emitted_name_passes_is_symbol_target() -> None:
    resolver = await _resolver()
    for target in ("main", "score", "md", "AGENTS", "SOURCES", "MxaSimScorer"):
        res = await resolver.resolve(target, entry="lookup")
        assert all(is_symbol_target(name) for name in res.candidates)


# ── Rule 3 ranking never blocks the event loop ────────────────────────────


class ThreadRecordingSymbolNames(Sequence[ChunkSymbolName]):
    """Named fake projection that records which thread scanned it."""

    def __init__(self, rows: tuple[ChunkSymbolName, ...]) -> None:
        self.rows = rows
        self.scan_threads: list[int] = []

    def __getitem__(self, index: int) -> ChunkSymbolName:
        return self.rows[index]

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[ChunkSymbolName]:
        self.scan_threads.append(threading.get_ident())
        return iter(self.rows)


async def test_ranking_runs_on_a_worker_thread_not_the_loop_thread() -> None:
    rows = ThreadRecordingSymbolNames((ChunkSymbolName("p.m.Cls", "p.m", "p/m.py"),))
    ranked = await tr.rank_candidates_off_loop(("Cls",), rows, "lookup", 0.75)
    assert ranked == ["p.m.Cls"]
    assert rows.scan_threads
    assert threading.get_ident() not in rows.scan_threads


async def test_resolve_keeps_the_event_loop_turning_while_ranking() -> None:
    """Rule 3's difflib pass is CPU-bound (~0.9 s over the 50k-row scan cap)."""
    turns = 0

    async def count_loop_turns() -> None:
        nonlocal turns
        while True:
            await asyncio.sleep(0)
            turns += 1

    resolver = await _resolver()
    ticker = asyncio.create_task(count_loop_turns())
    await asyncio.sleep(0)
    turns = 0
    await resolver.resolve("MxaSimScorer", entry="lookup")
    ticker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ticker
    assert turns > 0


# ── render_miss_message [AC7] ─────────────────────────────────────────────


def test_render_returns_the_same_message_without_candidates() -> None:
    message = "package 'md' not indexed"
    assert render_miss_message(message, TargetResolution()) is message


@pytest.mark.parametrize(
    ("message", "expected_joiner"),
    [
        ("'x' not found in pkg.", " "),
        ("'x' has no indexed source. [[next:search:x]]", " "),
        ("package 'x' not indexed", ". "),
    ],
)
def test_render_joiner(message: str, expected_joiner: str) -> None:
    res = TargetResolution(candidates=("a.b",), candidate_total=1)
    rendered = render_miss_message(message, res)
    assert rendered == f"{message}{expected_joiner}Closest indexed names: a.b."


def test_render_ambiguity_sentence_with_more_suffix() -> None:
    res = TargetResolution(
        candidates=("a.main", "b.main"), candidate_total=10, exact_leaf_count=10, ambiguous=True
    )
    rendered = render_miss_message("package 'main' not indexed", res)
    assert rendered == (
        "package 'main' not indexed. Ambiguous name 'main' matches 10 indexed symbols: "
        "a.main, b.main (+8 more)."
    )


async def test_render_matches_the_spec_score_string() -> None:
    resolver = await _resolver()
    res = await resolver.resolve("score", entry="lookup")
    assert render_miss_message("package 'score' not indexed", res) == (
        "package 'score' not indexed. Ambiguous name 'score' matches 3 indexed symbols: "
        "needle.scoring.strategies.CosineScorer.score, "
        "needle.scoring.strategies.MaxSimScorer.score, "
        "needle.scoring.strategies.ScoringStrategy.score."
    )


# ── NullTargetResolver [AC12] ─────────────────────────────────────────────


async def test_null_resolver_returns_an_empty_resolution() -> None:
    null = NullTargetResolver()
    assert isinstance(null, TargetResolver)
    assert await null.resolve("MaxSimScorer", entry="lookup") == TargetResolution()


def test_project_resolver_conforms_to_the_protocol() -> None:
    resolver = ProjectTargetResolver(make_fake_uow_factory(), TargetResolutionConfig())
    assert isinstance(resolver, TargetResolver)
    assert isinstance(FakeTargetResolver(), TargetResolver)
