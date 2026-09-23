"""The branch-keyed tree tier answers exactly as before it (#307, spec R7).

The write path now stamps the working tree's branch on every project row of the
five tree-tier tables, and the readers pass no branch — they read the branch
the bundle serves (``branches.is_default``) plus the dependency tier. The
``text`` and ``items`` of every tool the tree tier feeds must stay byte-
identical to what the code before the branch key answered, which
``tests/fixtures/goldens/tree_tier_single_branch_answers.json`` records: it was
written by this module's golden writer run against the parent commit
(origin/main at c22d72eb). Three passes are pinned: a git project indexed on
``main``; the same project after a checkout of a new branch that edits it; and
the project indexed outside any git repository.

The checkout pass must also leave nothing behind: no row of the old branch in
any tree-tier table, every project row stamped with the new branch.

Regenerate (only when an answer is meant to change, and from the code whose
answers are the reference)::

    PYDOCS_WRITE_TREE_TIER_GOLDEN=1 uv run --no-sync pytest \\
        tests/integration/test_tree_tier_branch_key_identity.py -k write_golden
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    OverviewInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db

_GOLDEN_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "goldens"
    / "tree_tier_single_branch_answers.json"
)
_GOLDEN_ENV = "PYDOCS_WRITE_TREE_TIER_GOLDEN"
_NEEDS_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

_CORE_PY = '''\
"""Core storage helpers."""


class Store:
    """Keeps rows in memory."""

    def save(self, row: str) -> int:
        return encode(row)


def encode(row: str) -> int:
    """Encodes one row."""
    return len(row)
'''

_APP_PY = '''\
"""The application entry points."""

from app.core import Store, encode


def run() -> int:
    """Saves and encodes a row."""
    return Store().save("x") + encode("y")
'''

_ADR = """\
# 1. Keep rows in memory

Status: Accepted
Date: 2026-03-04

app.core.Store keeps rows in memory because the data set is small.
"""

_COMMIT_DATE = "2026-03-04T05:06:07+00:00"
# (table, package column) of the five branch-keyed tables.
_TREE_TIER = (
    ("document_trees", "package"),
    ("module_members", "package"),
    ("node_references", "from_package"),
    ("node_scores", "package"),
    ("decision_records", "package"),
)


def _git(root: Path, *args: str) -> None:
    # Pinned identity AND dates: every answer's header names the indexed head,
    # so the commit ids must come out the same on every run for the golden.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_AUTHOR_DATE": _COMMIT_DATE,
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
        "GIT_COMMITTER_DATE": _COMMIT_DATE,
        "HOME": str(root),
    }
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def _write_project(root: Path) -> Path:
    (root / "app").mkdir(parents=True)
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "core.py").write_text(_CORE_PY, encoding="utf-8")
    (root / "app" / "main.py").write_text(_APP_PY, encoding="utf-8")
    (root / "docs" / "adr" / "0001-memory.md").write_text(_ADR, encoding="utf-8")
    return root


def _git_project(tmp_path: Path) -> Path:
    root = _write_project(tmp_path / "proj")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _checkout_an_edited_branch(root: Path) -> None:
    """A new branch that rewrites a function and adds one: every table moves."""
    _git(root, "switch", "-q", "-c", "feature/x")
    (root / "app" / "core.py").write_text(
        _CORE_PY.replace("return len(row)\n", "return checksum(row)\n")
        + '\n\ndef checksum(row: str) -> int:\n    """New on the branch."""\n    return 7\n',
        encoding="utf-8",
    )
    _git(root, "commit", "-q", "-am", "edit")


def _calls() -> dict[str, tuple[str, object]]:
    return {
        "search": ("search_codebase", SearchInput(query="store save encode")),
        "search_decision": ("search_codebase", SearchInput(query="memory", kind="decision")),
        "overview": ("get_overview", OverviewInput()),
        "overview_project": ("get_overview", OverviewInput(package=PROJECT_PACKAGE_NAME)),
        "symbol": ("get_symbol", SymbolInput(target="app.core.Store")),
        "symbol_source": ("get_symbol", SymbolInput(target="app.core.encode", depth="source")),
        "callers": ("get_references", ReferencesInput(target="app.core.encode")),
        "callees": (
            "get_references",
            ReferencesInput(target="app.main.run", direction="callees"),
        ),
        "impact": (
            "get_references",
            ReferencesInput(target="app.core.encode", direction="impact"),
        ),
        "governed_by": (
            "get_references",
            ReferencesInput(target="app.core.Store", direction="governed_by"),
        ),
        "context": ("get_context", ContextInput(targets=["app.core.Store", "app.main.run"])),
        "why": ("get_why", WhyInput(query="why keep rows in memory")),
    }


def _answers(db: Path, root: Path) -> dict[str, dict[str, object]]:
    """``text`` and ``items`` of each call, JSON-normalized, the root path masked."""
    router, _services = build_routers(AppConfig.load(), db_path=db, surface="mcp")
    answers = {}
    for name, (method, payload) in _calls().items():
        response = asyncio.run(getattr(router, method)(payload))
        answers[name] = {"text": response.text, "items": response.items}
    rendered = json.dumps(answers, sort_keys=True, default=str).replace(str(root), "<root>")
    return json.loads(rendered)


def _all_scenarios(tmp_path: Path) -> dict[str, dict[str, dict[str, object]]]:
    root = _git_project(tmp_path / "git")
    db = index_project_to_db(root, tmp_path / "git.db")
    on_main = _answers(db, root)
    _checkout_an_edited_branch(root)
    index_project_to_db(root, db)
    after_checkout = _answers(db, root)
    plain = _write_project(tmp_path / "plain" / "proj")
    plain_db = index_project_to_db(plain, tmp_path / "plain.db")
    return {
        "git_main": on_main,
        "git_after_checkout": after_checkout,
        "no_git": _answers(plain_db, plain),
    }


def _branches_of_project_rows(db: Path) -> set[str]:
    with closing(sqlite3.connect(db)) as conn:
        return {
            branch
            for table, column in _TREE_TIER
            for (branch,) in conn.execute(
                f"SELECT DISTINCT branch FROM {table} WHERE {column} = ?",
                (PROJECT_PACKAGE_NAME,),
            )
        }


@_NEEDS_GIT
def test_every_tree_tier_answer_is_byte_identical_to_the_code_before_the_branch_key(
    tmp_path: Path,
) -> None:
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    answers = _all_scenarios(tmp_path)
    assert sorted(answers) == sorted(golden)
    for scenario, calls in golden.items():
        assert sorted(answers[scenario]) == sorted(calls), scenario
        for call, expected in calls.items():
            assert answers[scenario][call] == expected, f"{scenario}/{call}"


@_NEEDS_GIT
def test_a_checkout_leaves_only_the_new_branch_rows(tmp_path: Path) -> None:
    root = _git_project(tmp_path)
    db = index_project_to_db(root, tmp_path / "b.db")
    assert _branches_of_project_rows(db) == {"main"}
    _checkout_an_edited_branch(root)
    index_project_to_db(root, db)
    assert _branches_of_project_rows(db) == {"feature/x"}
    answers = _answers(db, root)
    assert "checksum" in answers["symbol_source"]["text"]


def test_a_project_outside_git_stamps_the_placeholder_branch(tmp_path: Path) -> None:
    root = _write_project(tmp_path / "proj")
    db = index_project_to_db(root, tmp_path / "p.db")
    assert _branches_of_project_rows(db) == {NON_GIT_BRANCH_NAME}


@pytest.mark.skipif(not os.environ.get(_GOLDEN_ENV), reason=f"set {_GOLDEN_ENV}=1 to rewrite")
def test_write_golden(tmp_path: Path) -> None:
    answers = _all_scenarios(tmp_path)
    _GOLDEN_PATH.write_text(json.dumps(answers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
