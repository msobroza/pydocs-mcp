"""#310 end to end on a real git repository, through ``pydocs-mcp index`` itself:
a second branch costs its diff, the checked-out branch named again is a no-op,
a re-run embeds and extracts nothing, a purge drops the branch and nothing
shared, ``branches`` lists both — and while the member and chunk reads are not
branch-scoped yet (#312, #313), the tree tier keeps answering from the served
branch and a bundle with no extra branch answers exactly as before."""

from __future__ import annotations

import asyncio
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

import pytest

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application import node_score_compute
from pydocs_mcp.db import cache_path_for_project
from pydocs_mcp.extraction import PipelineChunkExtractor
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies import embedders as _embedders
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.node_score import NodeScore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork
from tests._fakes import RecordingEmbedder
from tests._git_sandbox import isolate_git_config, requires_git, run_git
from tests.integration.test_tree_tier_branch_key_identity import (
    _answers,
    _checkout_an_edited_branch,
    _git_project,
)

pytestmark = requires_git

_MODULES = 20
_EDITED = 7
_TABLES = (
    "branches",
    "branch_files",
    "branch_chunks",
    "file_extractions",
    "chunks",
    "packages",
    "document_trees",
    "module_members",
    "node_references",
    "node_scores",
    "decision_records",
)


@pytest.fixture(autouse=True)
def _sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> RecordingEmbedder:
    """The one embedder every pass of the test builds: it records each text."""
    config = AppConfig.load().embedding
    recording = RecordingEmbedder(dim=config.dim, model_name=config.model_name)
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: recording)
    return recording


@pytest.fixture
def explicit_extractions(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Every ``extract_from_paths`` call — the branch pass's only extraction."""
    calls: list[tuple[str, ...]] = []
    original = PipelineChunkExtractor.extract_from_paths

    async def _recording(self, project_root, paths):
        calls.append(tuple(paths))
        return await original(self, project_root, paths)

    monkeypatch.setattr(PipelineChunkExtractor, "extract_from_paths", _recording)
    return calls


def _module_source(index: int, *, edited: bool = False) -> str:
    value = "999" if edited else str(index)
    return (
        f'"""Module {index:02d}."""\n\n\n'
        f'def first_{index:02d}() -> int:\n    """First of {index:02d}."""\n    return {value}\n\n\n'
        f'def second_{index:02d}() -> int:\n    """Second of {index:02d}."""\n    return {index}\n'
    )


def _mostly_shared_project(tmp_path: Path) -> Path:
    """``main`` holds 20 modules; ``feature/x`` edits one function of one of them."""
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    for index in range(_MODULES):
        (root / "app" / f"mod_{index:02d}.py").write_text(_module_source(index), encoding="utf-8")
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "init")
    run_git(root, "switch", "-q", "-c", "feature/x")
    edited = root / "app" / f"mod_{_EDITED:02d}.py"
    edited.write_text(_module_source(_EDITED, edited=True), encoding="utf-8")
    run_git(root, "commit", "-q", "-am", "edit one function")
    run_git(root, "switch", "-q", "main")
    return root


def _cli(
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    root: Path,
    *flags: str,
    config: Path | None = None,
) -> int:
    extra = ("--no-inspect", "--skip-deps") if verb == "index" else ()
    leading = ("--config", str(config)) if config is not None else ()
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *leading, verb, str(root), *extra, *flags])
    return _cli_main()


def _db(root: Path) -> Path:
    return cache_path_for_project(root.resolve())


def _rows(db: Path, sql: str, *params: object) -> list[tuple]:
    with closing(sqlite3.connect(db)) as conn:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]


def _members(db: Path, branch: str) -> set[int]:
    return {r[0] for r in _rows(db, "SELECT chunk_id FROM branch_chunks WHERE branch=?", branch)}


def _snapshot(db: Path) -> dict[str, list[tuple]]:
    return {table: _rows(db, f"SELECT * FROM {table} ORDER BY rowid") for table in _TABLES}


def _embedded_texts(embedder: RecordingEmbedder) -> list[str]:
    return [text for batch in embedder.chunk_batches for text in batch]


def test_a_mostly_shared_branch_embeds_only_its_differing_chunks(
    tmp_path, monkeypatch, embedder, explicit_extractions
) -> None:
    root = _mostly_shared_project(tmp_path)
    assert _cli(monkeypatch, "index", root) == 0
    db = _db(root)
    embedder.chunk_batches.clear()
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    # The extraction half of "the cost of its diff": the checkout's cache rows
    # hit for every other file (one extraction key for both passes).
    assert explicit_extractions == [(f"app/mod_{_EDITED:02d}.py",)]
    only_on_branch = _members(db, "feature/x") - _members(db, "main")
    assert only_on_branch
    rows = _rows(
        db,
        f"SELECT text, source_path FROM chunks WHERE id IN ({','.join(map(str, only_on_branch))})",
    )
    assert sorted(_embedded_texts(embedder)) == sorted(text for text, _ in rows)
    assert {path for _, path in rows} == {f"app/mod_{_EDITED:02d}.py"}
    # The branch shares at least 90% of its chunks with main.
    assert len(only_on_branch) * 10 <= len(_members(db, "feature/x"))


def test_re_runs_extract_nothing_embed_nothing_and_keep_the_chunk_rows(
    tmp_path, monkeypatch, embedder, explicit_extractions
) -> None:
    # Not a no-op, deliberately: the branch's tree tier is rewritten from the
    # blob cache so its references re-resolve against the current dependency
    # tier and its carried SIMILAR edges follow the served branch.
    root = _mostly_shared_project(tmp_path)
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    db = _db(root)
    before = _snapshot(db)
    embedder.chunk_batches.clear()
    explicit_extractions.clear()
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    assert explicit_extractions == [] and embedder.chunk_batches == []
    after = _snapshot(db)
    # The working-tree pass took its package-level skip; the branch was rewritten
    # from the blob cache, onto the very same chunk ids.
    assert _members(db, "feature/x") == {
        r[1] for r in before["branch_chunks"] if r[0] == "feature/x"
    }
    assert after["chunks"] == before["chunks"] and after["packages"] == before["packages"]


def test_naming_the_checked_out_branch_again_is_a_no_op(
    tmp_path, monkeypatch, embedder, explicit_extractions
) -> None:
    root = _mostly_shared_project(tmp_path)
    assert _cli(monkeypatch, "index", root) == 0
    before = _snapshot(_db(root))
    embedder.chunk_batches.clear()
    assert _cli(monkeypatch, "index", root, "--branch", "main") == 0
    assert explicit_extractions == [] and embedder.chunk_batches == []
    assert _snapshot(_db(root)) == before


def _vector_count(db: Path) -> int:
    config = AppConfig.load().embedding

    async def _size() -> int:
        async with TurboQuantUnitOfWork(
            index_path=db.with_suffix(".tq"), dim=config.dim, bit_width=config.bit_width
        ) as uow:
            return uow.size()

    return asyncio.run(_size())


def test_purging_a_branch_drops_its_rows_and_nothing_shared(
    tmp_path, monkeypatch, embedder
) -> None:
    root = _mostly_shared_project(tmp_path)
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    db = _db(root)
    main_ids, branch_ids = _members(db, "main"), _members(db, "feature/x")
    edited = f"app/mod_{_EDITED:02d}.py"
    cached_blobs = "SELECT blob_sha FROM file_extractions WHERE path=? ORDER BY blob_sha"
    assert len(_rows(db, cached_blobs, edited)) == 2  # main's blob and the branch's
    assert _cli(monkeypatch, "branches", root, "--purge", "feature/x") == 0
    assert _members(db, "feature/x") == set() and _members(db, "main") == main_ids
    # The branch-only extraction goes with it; main's stays.
    assert _rows(db, cached_blobs, edited) == [(run_git(root, "rev-parse", f"main:{edited}"),)]
    surviving = {
        r[0] for r in _rows(db, "SELECT id FROM chunks WHERE package=?", PROJECT_PACKAGE_NAME)
    }
    assert surviving == main_ids and not (branch_ids - main_ids) & surviving
    embedded = _rows(db, "SELECT COUNT(*) FROM chunks WHERE embedded = 1")[0][0]
    assert _vector_count(db) == embedded
    tree_rows = "SELECT COUNT(*) FROM document_trees WHERE branch=?"
    assert _rows(db, tree_rows, "feature/x") == [(0,)] and _rows(db, tree_rows, "main")[0][0] > 0


def _layout_changing_project(tmp_path: Path) -> Path:
    """``feature/x`` adds a root ``__init__.py``: ``pkg.a`` becomes ``proj.pkg.a``
    over the very same ``pkg/a.py`` blob — a cache hit the pass must demote."""
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "a.py").write_text(_module_source(1), encoding="utf-8")
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "init")
    run_git(root, "switch", "-q", "-c", "feature/x")
    (root / "__init__.py").write_text("", encoding="utf-8")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "a package root")
    run_git(root, "switch", "-q", "main")
    return root


def test_a_re_added_branch_never_names_chunk_rows_a_purge_freed(
    tmp_path, monkeypatch, embedder
) -> None:
    # #310: the demoted hit's re-extraction rewrote the shared (blob, path) cache
    # row with ids only feature/x held; main's manifest kept the row alive past
    # the purge, and the re-add restored membership over deleted chunk rows.
    root = _layout_changing_project(tmp_path)
    assert _cli(monkeypatch, "index", root) == 0
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    assert _cli(monkeypatch, "branches", root, "--purge", "feature/x") == 0
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    db = _db(root)
    dangling = (
        "SELECT branch, chunk_id FROM branch_chunks WHERE chunk_id NOT IN (SELECT id FROM chunks)"
    )
    assert _rows(db, dangling) == []
    modules = "SELECT DISTINCT c.module FROM branch_chunks b JOIN chunks c ON c.id = b.chunk_id WHERE b.branch=? AND b.source_path=?"
    assert _rows(db, modules, "feature/x", "pkg/a.py") == [("proj.pkg.a",)]
    assert _rows(db, modules, "main", "pkg/a.py") == [("pkg.a",)]


def test_branches_lists_both_branches_with_heads_and_bases(
    tmp_path, monkeypatch, embedder, capsys
) -> None:
    root = _mostly_shared_project(tmp_path)
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    capsys.readouterr()
    assert _cli(monkeypatch, "branches", root) == 0
    lines = capsys.readouterr().out.splitlines()
    heads = {name: run_git(root, "rev-parse", name)[:7] for name in ("main", "feature/x")}
    main_line = next(line for line in lines if line.startswith("* main"))
    branch_line = next(line for line in lines if line.startswith("  feature/x"))
    assert heads["main"] in main_line and heads["feature/x"] in branch_line
    assert main_line.split()[2] == "main" and branch_line.split()[1] == "main"


# ── The interim state (#312, #313 land the branch-scoped member and chunk reads) ──


def test_without_an_extra_branch_every_answer_is_unchanged(tmp_path, monkeypatch, embedder) -> None:
    root = _git_project(tmp_path)
    assert _cli(monkeypatch, "index", root) == 0
    before = _answers(_db(root), root)
    assert _cli(monkeypatch, "index", root, "--branch", "main") == 0
    assert _answers(_db(root), root) == before


def _tree_tier(db: Path, branch: str | None = None) -> dict[str, object]:
    """The five tree-tier reads of one branch; ``None`` is what the bundle serves."""

    async def _read() -> dict[str, object]:
        kinds = tuple(ReferenceKind)
        async with build_sqlite_uow_factory(db)() as uow:
            trees = await uow.trees.load_all_in_package(PROJECT_PACKAGE_NAME, branch=branch)
            scores = await uow.node_scores.for_package(PROJECT_PACKAGE_NAME, branch=branch)
            return {
                # The repr carries every node down the tree, children included.
                "trees": {name: repr(tree) for name, tree in trees.items()},
                "references": sorted(await uow.references.list_resolved(kinds, branch=branch)),
                "scores": {s.qualified_name: s.in_degree for s in scores},
                "decisions": await uow.decisions.list_for_package(
                    PROJECT_PACKAGE_NAME, branch=branch
                ),
            }

    return asyncio.run(_read())


def _in_degree_scores(edges, qname_packages) -> list[NodeScore]:
    """A deterministic stand-in for the [graph] extra's scorer: in-degree only."""
    in_degree = Counter(target for _source, target in edges)
    return [
        NodeScore(package, qname, in_degree[qname]) for qname, package in qname_packages.items()
    ]


@pytest.fixture
def node_scores_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config that turns node scores on, scored without networkx."""
    monkeypatch.setattr(node_score_compute, "compute_scores", _in_degree_scores)
    path = tmp_path / "node-scores.yaml"
    path.write_text("reference_graph:\n  node_scores:\n    enabled: true\n", encoding="utf-8")
    return path


def test_the_tree_tier_answers_from_the_served_branch_after_a_second_one_is_indexed(
    tmp_path, monkeypatch, embedder, node_scores_config
) -> None:
    root = _git_project(tmp_path)
    _checkout_an_edited_branch(root)
    run_git(root, "switch", "-q", "main")
    assert _cli(monkeypatch, "index", root, config=node_scores_config) == 0
    db = _db(root)
    served = _tree_tier(db)
    assert served["scores"] and served["decisions"]
    flags = ("--branch", "feature/x")
    assert _cli(monkeypatch, "index", root, *flags, config=node_scores_config) == 0
    assert _tree_tier(db) == served
    assert "checksum" not in str(served)
    # The branch holds its own copy of every table but decisions (O10 is P2):
    # the edit resolves, and is scored, in the branch's own graph.
    branch = _tree_tier(db, "feature/x")
    assert "checksum" in branch["trees"]["app.core"]
    assert ("app.core.encode", "app.core.checksum") in branch["references"]
    assert branch["scores"]["app.core.checksum"] == 1
    assert _rows(db, "SELECT COUNT(*) FROM decision_records WHERE branch=?", "feature/x") == [(0,)]
    # A later plain run recomputes the served scores while the branch's chunk
    # rows exist: the served graph still scores only the served branch's symbols.
    assert _cli(monkeypatch, "index", root, config=node_scores_config) == 0
    assert _tree_tier(db) == served
