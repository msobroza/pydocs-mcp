"""#310 end to end on a real git repository, through ``pydocs-mcp index`` itself:
a second branch costs its diff, the checked-out branch named again is a no-op,
a re-run embeds and extracts nothing, a purge drops the branch and nothing
shared, ``branches`` lists both — and while the member and chunk reads are not
branch-scoped yet (#312, #313), the tree tier keeps answering from the served
branch and a bundle with no extra branch answers exactly as before.

#320 adds the cost cases of spec §9: AC-1, AC-2 (the git-objects half) and AC-11."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application import node_score_compute
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.db import cache_path_for_project
from pydocs_mcp.extraction import PipelineChunkExtractor
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies import embedders as _embedders
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import _register_tools, build_routers
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.node_score import NodeScore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork
from tests._fakes import RecordingEmbedder
from tests._git_sandbox import isolate_git_config, requires_git, run_git
from tests.integration.test_tree_tier_branch_key_identity import (
    _answers,
    _calls,
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


def _write_module(root: Path, index: int, *, edited: bool = False) -> None:
    (root / "app" / f"mod_{index:02d}.py").write_text(
        _module_source(index, edited=edited), encoding="utf-8"
    )


def _mostly_shared_project(
    tmp_path: Path, *, edited: tuple[int, ...] = (_EDITED,), added: tuple[int, ...] = ()
) -> Path:
    """``main`` holds ``_MODULES`` modules; ``feature/x`` edits one function of
    each ``edited`` module and adds the ``added`` ones."""
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    for index in range(_MODULES):
        _write_module(root, index)
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "init")
    run_git(root, "switch", "-q", "-c", "feature/x")
    for index in edited:
        _write_module(root, index, edited=True)
    for index in added:
        _write_module(root, index)
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "the branch")
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


def _branch_reindex_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    payloads = (json.loads(m) for m in caplog.messages if m.startswith("{"))
    return [p for p in payloads if p.get("event") == "branch_reindex"]


def test_the_branch_reindex_line_counts_k_parses_and_exactly_the_new_chunks(
    tmp_path, monkeypatch, embedder, explicit_extractions, caplog
) -> None:
    # AC-1 (#320): k changed files cost k parses and the chunks whose
    # content_hash is new — and the branch_reindex line reports those counts.
    edited = (3, _EDITED, 11)
    root = _mostly_shared_project(tmp_path, edited=edited)
    assert _cli(monkeypatch, "index", root) == 0
    embedder.chunk_batches.clear()
    caplog.set_level(logging.INFO, logger="pydocs-mcp")
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    assert [sorted(paths) for paths in explicit_extractions] == [
        [f"app/mod_{index:02d}.py" for index in edited]
    ]
    db = _db(root)
    held = _members(db, "feature/x")
    new = held - _members(db, "main")
    rows = _rows(db, f"SELECT text FROM chunks WHERE id IN ({','.join(map(str, new))})")
    assert sorted(_embedded_texts(embedder)) == sorted(text for (text,) in rows)
    assert _branch_reindex_lines(caplog) == [
        {
            "event": "branch_reindex",
            "branch": "feature/x",
            # The empty __init__.py is listed but neither reused nor extracted.
            "files_total": _MODULES + 1,
            "files_reused": _MODULES - len(edited),
            "files_extracted": len(edited),
            "chunks_embedded": len(new),
            "chunks_shared": len(held) - len(new),
            "vectors_removed": 0,
        }
    ]


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


_ADDED = (_MODULES, _MODULES + 1)
_TOP_FIFTY_SEARCH = SearchInput(query="first second module", kind="docs", limit=50)


def _preset_config(tmp_path: Path, preset: str) -> AppConfig:
    path = tmp_path / f"{preset}.config.yaml"
    route = f"    - default: true\n      pipeline_path: pipelines/{preset}\n"
    path.write_text(f"pipelines:\n  chunk:\n{route}", encoding="utf-8")
    return AppConfig.load(explicit_path=path)


def _search(
    db: Path, root: Path, config: AppConfig, branch: str, payload: SearchInput = _TOP_FIFTY_SEARCH
) -> dict[str, object]:
    """``text`` and ``items`` of one search as ``_answers`` records them."""
    router, _services = build_routers(config, db_path=db, surface="mcp")
    on_branch = payload.model_copy(update={"branch": branch})
    response = asyncio.run(router.search_codebase(on_branch))
    rendered = json.dumps({"text": response.text, "items": response.items}, default=str)
    return json.loads(rendered.replace(str(root), "<root>"))


def test_main_answers_byte_identically_after_a_second_branch_and_a_re_index(
    tmp_path, monkeypatch, embedder, explicit_extractions
) -> None:
    # AC-2 (#320) for a branch indexed from git objects: re-indexing main writes
    # and embeds nothing, and main's answers — the default dense + graph search
    # among them — keep their bytes. The checkout-back half is out of reach on
    # this base, and the job queue would not change that: the working-tree stamp
    # still retires the previous checkout's rows (the §6.8a retention policy is
    # not wired in its place), and the working-tree pass parses every file
    # before its package gate (the incremental working tree is P2.6).
    root = _git_project(tmp_path)
    _checkout_an_edited_branch(root)
    run_git(root, "switch", "-q", "main")
    assert _cli(monkeypatch, "index", root) == 0
    db = _db(root)
    before = _answers(db, root)
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    written = _snapshot(db)
    embedder.chunk_batches.clear()
    explicit_extractions.clear()
    assert _cli(monkeypatch, "index", root) == 0
    assert explicit_extractions == [] and embedder.chunk_batches == []
    assert _snapshot(db) == written
    after = _answers(db, root)
    for card in ("overview", "overview_project"):  # AC-8: a two-branch bundle names it
        assert " · branch main" in after[card]["text"]
        after[card]["text"] = after[card]["text"].replace(" · branch main", "", 1)
    assert after == before
    by_name = _search(db, root, AppConfig.load(), "main", _calls()["search"][1])
    assert by_name == before["search"]


def test_a_branch_that_only_adds_files_leaves_mains_dense_and_bm25_top_k_unchanged(
    tmp_path, monkeypatch, embedder
) -> None:
    # AC-11 (#320): the dense allowlist is exact per branch, so main's dense
    # top-k keeps its bytes; BM25 scores follow the shared FTS statistics, so
    # only its ranking is pinned — unchanged on this fixture.
    # More chunks than the dense fetch keeps: a leaking allowlist would show.
    root = _mostly_shared_project(tmp_path, edited=(), added=_ADDED)
    assert _cli(monkeypatch, "index", root) == 0
    db = _db(root)
    dense = _preset_config(tmp_path, "chunk_search_dense.yaml")
    bm25 = _preset_config(tmp_path, "chunk_search.yaml")
    before = {"dense": _search(db, root, dense, ""), "bm25": _search(db, root, bm25, "")}
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    # Not vacuous: the branch's own dense search does rank the added rows.
    on_branch = {item["path"] for item in _search(db, root, dense, "feature/x")["items"]}
    assert on_branch & {f"app/mod_{index:02d}.py" for index in _ADDED}
    assert _search(db, root, dense, "main") == _search(db, root, dense, "") == before["dense"]
    bm25_ranking_on_main = [item["id"] for item in _search(db, root, bm25, "main")["items"]]
    assert bm25_ranking_on_main == [item["id"] for item in before["bm25"]["items"]]


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


def test_a_branch_no_selector_could_name_is_never_indexed_from_git_objects(
    tmp_path, monkeypatch, embedder, capsys
) -> None:
    """#315: ``fix#123`` is a legal git branch outside the selector grammar, so
    its rows could be listed but never selected. ``--branch`` refuses it with
    the boundary's message and ``--all-branches`` skips it."""
    root = _mostly_shared_project(tmp_path)
    run_git(root, "branch", "fix#123", "feature/x")
    assert _cli(monkeypatch, "index", root, "--branch", "fix#123") == 1
    assert "got 'fix#123'" in capsys.readouterr().err
    assert _cli(monkeypatch, "index", root, "--all-branches") == 0
    names = _rows(_db(root), "SELECT name FROM branches ORDER BY name")
    assert names == [("feature/x",), ("main",)]


# ── The interim state (#312, #313 land the branch-scoped member and chunk reads) ──


def test_without_an_extra_branch_every_answer_is_unchanged(tmp_path, monkeypatch, embedder) -> None:
    root = _git_project(tmp_path)
    assert _cli(monkeypatch, "index", root) == 0
    before = _answers(_db(root), root)
    assert _cli(monkeypatch, "index", root, "--branch", "main") == 0
    assert _answers(_db(root), root) == before


# ── #315: the nine tools take the selector on the MCP wire ───────────────

_FIRST_07 = f"app.mod_{_EDITED:02d}.first_{_EDITED:02d}"
_EDITED_PATH = f"app/mod_{_EDITED:02d}.py"
# tool → the smallest arguments that read the edited module.
_WIRE_CALLS: dict[str, dict[str, object]] = {
    "get_overview": {},
    "search_codebase": {"query": "first of module"},
    "get_symbol": {"target": _FIRST_07, "depth": "source"},
    "get_context": {"targets": [_FIRST_07]},
    "get_references": {"target": _FIRST_07},
    "get_why": {"query": "why"},
    "grep": {"pattern": "return 999", "output_mode": "content"},
    "glob": {"pattern": "app/*.py"},
    "read_file": {"file_path": _EDITED_PATH},
}


def _wire(root: Path) -> FastMCP:
    tools, _services = build_routers(AppConfig.load(), db_path=_db(root), surface="mcp")
    mcp = FastMCP("multi-branch-p1")
    _register_tools(mcp, tools)
    return mcp


def _wire_answer(mcp: FastMCP, tool: str, **args: object) -> dict[str, object]:
    result = asyncio.run(mcp.call_tool(tool, {**_WIRE_CALLS[tool], **args}))
    return result.structuredContent  # type: ignore[union-attr]


def test_every_tool_answers_the_named_branch_over_the_mcp_wire(
    tmp_path, monkeypatch, embedder
) -> None:
    root = _mostly_shared_project(tmp_path)
    assert _cli(monkeypatch, "index", root, "--branch", "feature/x") == 0
    mcp = _wire(root)
    for tool in _WIRE_CALLS:
        answer = _wire_answer(mcp, tool, branch="feature/x")
        assert answer["meta"]["branch"] == "feature/x", tool  # type: ignore[index]
    source = {b: _wire_answer(mcp, "get_symbol", branch=b)["text"] for b in ("main", "feature/x")}
    assert "return 999" in str(source["feature/x"]) and "return 999" not in str(source["main"])
    hits = _wire_answer(mcp, "grep", branch="feature/x")["items"]
    assert [row["path"] for row in hits] == [_EDITED_PATH]  # type: ignore[union-attr, index]
    assert _wire_answer(mcp, "grep", branch="main")["items"] == []


def test_a_branch_the_bundle_never_indexed_is_refused_naming_the_indexed_ones(
    tmp_path, monkeypatch, embedder
) -> None:
    root = _mostly_shared_project(tmp_path)
    assert _cli(monkeypatch, "index", root) == 0
    mcp = _wire(root)
    for tool in _WIRE_CALLS:
        with pytest.raises(ToolError) as caught:
            _wire_answer(mcp, tool, branch="feature/x")
        assert "no indexed branch 'feature/x'; indexed: ['main']" in str(caught.value), tool


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
