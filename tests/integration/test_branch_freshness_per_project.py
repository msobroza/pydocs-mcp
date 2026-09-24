"""Branch resolution and freshness over real bundles (#311: spec §6.4, §6.11, O19, AC-31).

- Two bundles on one server each answer with their OWN freshness (O19): the
  project a response names is the project whose index head and staleness it
  carries, not the first-loaded one's.
- An unknown branch / a sha that is no landing here is refused at the tool
  with the spec's sentences (§6.11), over a real ``branches`` table.
- The default selector after a checkout of an unindexed branch answers from
  the indexed branch, suggests the index command on the suggestion tools,
  and keeps today's ``index_stale`` reading (working tree against the pass).
- No tool call spawns a process (AC-31): the directory reads git plumbing only.
- A bundle removed from under the server leaves the filesystem tools answering.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.mcp_inputs import (
    GlobInput,
    GrepInput,
    ReadFileInput,
    SearchInput,
    SymbolInput,
)
from pydocs_mcp.git.refs import resolve_git_head
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db
from tests.application._router_fakes import BranchSelectedInput
from tests.integration.test_branch_selector_identity import _nine_tool_calls
from tests.integration.test_tree_tier_branch_key_identity import _git, _write_project

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

_CHECKOUT_SUGGESTION = (
    "[suggestion: checked-out branch 'feature/y' is not indexed; "
    "run: pydocs-mcp index . --branch feature/y]"
)


def _indexed_git_project(tmp_path: Path, name: str) -> tuple[Path, Path]:
    root = _write_project(tmp_path / name)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    # The name in the message gives each project its own commit id (the
    # fixture pins every date), so a head read from the wrong bundle shows.
    _git(root, "commit", "-q", "-m", f"init {name}")
    return root, index_project_to_db(root, tmp_path / f"{name}.db")


def _commit_a_change(root: Path) -> None:
    (root / "app" / "extra.py").write_text('"""Added after the index pass."""\n', "utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "after the pass")


def _call(router: Any, method: str, payload: Any) -> Any:
    return asyncio.run(getattr(router, method)(payload))


def test_each_bundle_on_a_two_bundle_server_answers_with_its_own_freshness(
    tmp_path: Path,
) -> None:
    alpha, alpha_db = _indexed_git_project(tmp_path, "alpha")
    beta, beta_db = _indexed_git_project(tmp_path, "beta")
    alpha_indexed = resolve_git_head(alpha)
    _commit_a_change(alpha)  # alpha's index is now behind its checkout; beta's is not
    router, _ = build_routers(AppConfig.load(), db_paths=[alpha_db, beta_db], surface="mcp")

    on_beta = _call(router, "glob", GlobInput(pattern="app/*.py", project="beta"))
    assert on_beta.meta["project"] == "beta"
    assert on_beta.meta["indexed_git_head"] == resolve_git_head(beta)
    assert on_beta.meta["index_stale"] is False
    assert on_beta.text.startswith(f"[index: {resolve_git_head(beta)[:7]} ")

    on_alpha = _call(router, "glob", GlobInput(pattern="app/*.py", project="alpha"))
    assert on_alpha.meta["indexed_git_head"] == alpha_indexed
    assert on_alpha.meta["live_git_head"] == resolve_git_head(alpha)
    assert on_alpha.meta["index_stale"] is True
    assert "[⚠ index stale" in on_alpha.text


def test_unknown_names_and_shas_are_refused_at_the_tool_with_the_spec_sentences(
    tmp_path: Path,
) -> None:
    _root, db = _indexed_git_project(tmp_path, "proj")
    router, _ = build_routers(AppConfig.load(), db_path=db, surface="mcp")
    with pytest.raises(InvalidArgumentError) as unknown_name:
        _call(router, "search_codebase", BranchSelectedInput(SearchInput(query="store"), "nope"))
    assert str(unknown_name.value) == (
        "no indexed branch 'nope'; indexed: ['main']; run pydocs-mcp index . --branch nope"
    )
    with pytest.raises(InvalidArgumentError) as unknown_sha:
        _call(
            router,
            "get_symbol",
            BranchSelectedInput(SymbolInput(target="app.core.Store"), "deadbee"),
        )
    assert str(unknown_sha.value) == (
        "no branch or landing unit matches 'deadbee'; landings in the window: []"
    )
    named = _call(router, "grep", BranchSelectedInput(GrepInput(pattern="def encode"), "main"))
    assert named.meta["branch"] == "main" and named.meta["index_stale"] is False


def test_an_unindexed_checkout_answers_from_the_indexed_branch_with_the_suggestion(
    tmp_path: Path,
) -> None:
    root, db = _indexed_git_project(tmp_path, "proj")
    _git(root, "switch", "-q", "-c", "feature/y")
    router, _ = build_routers(AppConfig.load(), db_path=db, surface="mcp")
    search = _call(router, "search_codebase", SearchInput(query="store save encode"))
    grep = _call(router, "grep", GrepInput(pattern="def encode"))
    symbol = _call(router, "get_symbol", SymbolInput(target="app.core.Store"))
    for response in (search, grep, symbol):
        assert response.meta["branch"] == "main" and response.meta["index_stale"] is False
    assert search.meta["suggestion"] == grep.meta["suggestion"] == _CHECKOUT_SUGGESTION
    assert "suggestion" not in symbol.meta  # get_symbol declares no such field
    assert _CHECKOUT_SUGGESTION not in search.text + grep.text  # meta only (§6.7)

    _commit_a_change(root)  # the working tree moves past the pass: today's stale reading
    router, _ = build_routers(AppConfig.load(), db_path=db, surface="mcp")
    stale = _call(router, "grep", GrepInput(pattern="def encode"))
    assert stale.meta["branch"] == "main" and stale.meta["index_stale"] is True


class _NoProcessSpawned(subprocess.Popen):  # type: ignore[type-arg]
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(f"a tool call spawned a process: {args!r}")


def test_no_tool_call_spawns_a_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, db = _indexed_git_project(tmp_path, "proj")
    _git(root, "switch", "-q", "-c", "feature/y")  # the directory reads live refs here
    router, _ = build_routers(AppConfig.load(), db_path=db, surface="mcp")
    monkeypatch.setattr(subprocess, "Popen", _NoProcessSpawned)
    for name, (method, payload) in _nine_tool_calls().items():
        response = _call(router, method, payload)
        assert response.meta["branch"] == "main", name


def _no_ttl_config(tmp_path: Path) -> AppConfig:
    # TTL 0: every call re-reads the directory and the probe, so the test sees
    # both states a removed bundle passes through without waiting on a clock.
    overlay = tmp_path / "no-ttl.yaml"
    overlay.write_text("output:\n  envelope: { head_check_ttl_seconds: 0 }\n", "utf-8")
    return AppConfig.load(explicit_path=overlay)


def test_the_filesystem_tools_keep_answering_when_the_bundle_is_removed(tmp_path: Path) -> None:
    """grep / glob / read_file never needed the index: a bundle removed from
    under the server leaves them answering with null freshness, as before
    #311, and meta.branch null (the directory degrades like the probe)."""
    _root, db = _indexed_git_project(tmp_path, "proj")
    router, _ = build_routers(_no_ttl_config(tmp_path), db_path=db, surface="mcp")
    calls = {
        "grep": GrepInput(pattern="def encode"),
        "glob": GlobInput(pattern="app/*.py"),
        "read_file": ReadFileInput(file_path="app/core.py", limit=3),
    }
    before = {method: _call(router, method, payload) for method, payload in calls.items()}
    for path in (db, Path(f"{db}-wal"), Path(f"{db}-shm")):
        path.unlink(missing_ok=True)
    # Twice: first no file at all, then the empty one the probe's plain
    # ``sqlite3.connect`` leaves where the bundle was.
    for _ in range(2):
        for method, payload in calls.items():
            after = _call(router, method, payload)
            assert after.items == before[method].items, method
            assert after.meta["branch"] is None and after.meta["indexed_git_head"] is None
