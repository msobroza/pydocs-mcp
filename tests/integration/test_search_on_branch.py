"""#312 end to end on a real git repository: search answers from the selected branch.

``main`` holds ``alpha`` and ``beta``; ``feature/x`` prepends ``gamma`` (so the
shared ``alpha`` moves down the file) and rewrites ``beta``. Both branches are
indexed into one bundle through ``pydocs-mcp index`` itself, then searched
through the real composition root and through ``pydocs-mcp search --branch``:

- the default selector searches the resolved branch (``main``, the checkout)
  and ``meta.branch`` names it — no ``feature/x`` row leaks into it;
- ``feature/x`` finds ``gamma`` and its own ``beta``, never ``main``'s;
- the chunk pipeline alone — no post-pipeline hydration to mask a leak —
  never returns a row the selected branch lacks;
- a dense hit shared by both branches comes back once, with the selected
  branch's span;
- member search follows the branch too.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application.branch_search import NullChunkBranchHydrator
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.search_query import build_search_query
from pydocs_mcp.db import cache_path_for_project
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._git_sandbox import isolate_git_config, requires_git, run_git

pytestmark = requires_git

MAIN, FEATURE = "main", "feature/x"
_HEADER = '"""Core helpers."""\n'
_ALPHA = '\n\ndef alpha(row: str) -> int:\n    """Alpha counts a row."""\n    return len(row)\n'
_BETA = '\n\ndef beta(row: str) -> int:\n    """Beta scales a row."""\n    return 2 * len(row)\n'
_GAMMA = (
    '\n\ndef gamma(row: str) -> int:\n    """Gamma exists only on the feature branch."""\n'
    "    return 3\n"
)


@pytest.fixture(autouse=True)
def _sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


def _two_branch_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    core = root / "app" / "core.py"
    core.write_text(_HEADER + _ALPHA + _BETA, encoding="utf-8")
    run_git(root, "init", "-q", "-b", MAIN)
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "init")
    run_git(root, "switch", "-q", "-c", FEATURE)
    core.write_text(_HEADER + _GAMMA + _ALPHA + _BETA.replace("2 *", "3 *"), encoding="utf-8")
    run_git(root, "commit", "-q", "-am", "gamma, and a new beta")
    run_git(root, "switch", "-q", MAIN)
    return root


def _cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *argv])
    return _cli_main()


@pytest.fixture
def indexed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _two_branch_project(tmp_path)
    flags = ("--no-inspect", "--skip-deps")
    assert _cli(monkeypatch, "index", str(root), *flags) == 0
    assert _cli(monkeypatch, "index", str(root), *flags, "--branch", FEATURE) == 0
    return root


def _db(root: Path) -> Path:
    return cache_path_for_project(root.resolve())


def _membership(db: Path, branch: str) -> dict[int, tuple[int, int]]:
    sql = "SELECT chunk_id, start_line, end_line FROM branch_chunks WHERE branch = ?"
    with closing(sqlite3.connect(db)) as conn:
        return {row[0]: (row[1], row[2]) for row in conn.execute(sql, (branch,))}


def _search(root: Path, payload: SearchInput, branch: str = ""):
    router, _services = build_routers(AppConfig.load(), db_path=_db(root), surface="mcp")
    return asyncio.run(router.search_codebase(payload, branch=branch))


def _project_hits(response) -> dict[int, dict]:
    return {
        int(item["id"]): item
        for item in response.items
        if item["kind"] == "chunk" and item["package"] == PROJECT_PACKAGE_NAME
    }


def _qnames(hits: dict[int, dict]) -> set[str]:
    return {hit["qualified_name"] for hit in hits.values()}


_EVERY_CHUNK = SearchInput(query="row helpers", kind="docs", limit=50)


def test_the_default_search_answers_from_the_checked_out_branch_only(indexed: Path) -> None:
    db = _db(indexed)
    response = _search(indexed, _EVERY_CHUNK)
    hits = _project_hits(response)
    assert response.meta["branch"] == MAIN
    assert set(hits) <= set(_membership(db, MAIN))
    assert "app.core.gamma" not in _qnames(hits) and "app.core.beta" in _qnames(hits)


def test_a_chunk_changed_only_on_the_branch_is_found_there_and_not_on_main(indexed: Path) -> None:
    db = _db(indexed)
    only_on_feature = set(_membership(db, FEATURE)) - set(_membership(db, MAIN))
    only_on_main = set(_membership(db, MAIN)) - set(_membership(db, FEATURE))
    on_feature = _project_hits(_search(indexed, _EVERY_CHUNK, FEATURE))
    on_main = _project_hits(_search(indexed, _EVERY_CHUNK))
    # Guard: without branch-only rows the checks below would pass vacuously.
    assert only_on_feature and only_on_main
    assert only_on_feature <= set(on_feature) and not only_on_feature & set(on_main)
    assert only_on_main <= set(on_main) and not only_on_main & set(on_feature)
    # The dense allowlist follows membership: nothing the branch lacks.
    assert set(on_feature) <= set(_membership(db, FEATURE))


def _pipeline_project_ids(root: Path, branch: str) -> set[int]:
    """The chunk pipeline's own project rows for a query pinned to ``branch``,
    with no post-pipeline hydration — so only the pushdown (the lexical EXISTS,
    the dense allowlist, graph expansion) can keep another branch's rows out."""
    _router, services = build_routers(AppConfig.load(), db_path=_db(root), surface="mcp")
    docs = dataclasses.replace(services[0].docs, branch_hydrator=NullChunkBranchHydrator())
    ranked = asyncio.run(docs.ranked(build_search_query(_EVERY_CHUNK, branch=branch)))
    return {c.id for c in ranked.items if c.metadata.get("package") == PROJECT_PACKAGE_NAME}


def test_the_pipeline_itself_never_returns_a_row_the_branch_lacks(indexed: Path) -> None:
    """AC2 without the hydrator's guard: the dense allowlist and the lexical
    fetch follow membership, and each branch still finds its own rows."""
    db = _db(indexed)
    for branch, other in ((MAIN, FEATURE), (FEATURE, MAIN)):
        held, other_held = set(_membership(db, branch)), set(_membership(db, other))
        found = _pipeline_project_ids(indexed, branch)
        assert found <= held
        assert (other_held - held) and not found & (other_held - held)
        assert found & (held - other_held)


def test_a_hit_on_both_branches_carries_the_selected_branch_span(indexed: Path) -> None:
    db = _db(indexed)
    main_spans, feature_spans = _membership(db, MAIN), _membership(db, FEATURE)
    on_main = _project_hits(_search(indexed, _EVERY_CHUNK))
    on_feature = _project_hits(_search(indexed, _EVERY_CHUNK, FEATURE))
    (alpha_id,) = [i for i, hit in on_main.items() if hit["qualified_name"] == "app.core.alpha"]
    assert main_spans[alpha_id] != feature_spans[alpha_id]
    for hits, spans in ((on_main, main_spans), (on_feature, feature_spans)):
        alpha = hits[alpha_id]
        assert (alpha["start_line"], alpha["end_line"]) == spans[alpha_id]
    feature_ids = [h["id"] for h in _search(indexed, _EVERY_CHUNK, FEATURE).items]
    assert len(feature_ids) == len(set(feature_ids))


def test_member_search_follows_the_branch(indexed: Path) -> None:
    by_name = SearchInput(query="gamma", kind="api")
    on_feature = _search(indexed, by_name, FEATURE)
    on_main = _search(indexed, by_name)
    assert [item["qualified_name"] for item in on_feature.items] == ["app.core.gamma"]
    assert on_main.items == ()


def test_the_cli_searches_a_named_branch(
    indexed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    common = ("search", "row helpers", "--kind", "docs", "--limit", "50")
    capsys.readouterr()
    assert _cli(monkeypatch, *common, "--project-dir", str(indexed), "--branch", FEATURE) == 0
    on_feature = capsys.readouterr().out
    assert _cli(monkeypatch, *common, "--project-dir", str(indexed)) == 0
    on_main = capsys.readouterr().out
    assert "Gamma exists only on the feature branch" in on_feature
    assert "Gamma exists only on the feature branch" not in on_main
    assert "3 * len(row)" in on_feature and "2 * len(row)" in on_main


def test_the_cli_refuses_a_branch_the_bundle_never_indexed(
    indexed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ("search", "row", "--project-dir", str(indexed), "--branch", "nope")
    assert _cli(monkeypatch, *argv) == 1
    assert "no indexed branch 'nope'; indexed: ['feature/x', 'main']" in capsys.readouterr().err
