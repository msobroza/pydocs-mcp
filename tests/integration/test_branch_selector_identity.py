"""The branch selector changes nothing a single-branch bundle serves (#311, spec R7, AC-3).

Every tool now resolves the ``branch`` selector (a declared parameter since
#315; these calls omit it, so it is the empty default) against the bundle's
branch directory and hands the resolution to the envelope, and the freshness
facts come from the answering project's own probe (O19). On a bundle that
holds one branch — and on a project outside git — none of that may move a
byte: ``text``, ``items`` and the whole ``meta`` block of all nine tools must
equal what the code before the selector answered (item ``score`` floats
compare within float32 noise).

``tests/fixtures/goldens/branch_selector_single_branch_answers.json`` records
those answers; this module's golden writer produced it against the parent
commit (origin/main at 1558f716). Three passes are pinned, reusing the tree-tier
identity fixture: a git project indexed on ``main``; the same project after a
checkout of an edited branch and a re-index; and the project outside git.

Regenerate (only when an answer is meant to change, and from the code whose
answers are the reference)::

    PYDOCS_WRITE_BRANCH_SELECTOR_GOLDEN=1 uv run --no-sync pytest \\
        tests/integration/test_branch_selector_identity.py -k write_golden
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    GlobInput,
    GrepInput,
    OverviewInput,
    ReadFileInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db
from tests._score_tolerance import scores_within_float_noise
from tests.integration.test_tree_tier_branch_key_identity import (
    _checkout_an_edited_branch,
    _git_project,
    _write_project,
)

_GOLDEN_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "goldens"
    / "branch_selector_single_branch_answers.json"
)
_GOLDEN_ENV = "PYDOCS_WRITE_BRANCH_SELECTOR_GOLDEN"
_NEEDS_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")
# glob answers newest-first by mtime, and a fixture writes its files within the
# same second or not: one pinned mtime makes the order (path ties) and the
# ``items[].mtime`` values the same on every run.
_PINNED_MTIME = 1_772_600_767.0


def _nine_tool_calls() -> dict[str, tuple[str, object]]:
    return {
        "search": ("search_codebase", SearchInput(query="store save encode")),
        "overview": ("get_overview", OverviewInput()),
        "symbol": ("get_symbol", SymbolInput(target="app.core.Store")),
        "symbol_source": ("get_symbol", SymbolInput(target="app.core.encode", depth="source")),
        "context": ("get_context", ContextInput(targets=["app.core.Store", "app.main.run"])),
        "callers": ("get_references", ReferencesInput(target="app.core.encode")),
        "why": ("get_why", WhyInput(query="why keep rows in memory")),
        "grep": ("grep", GrepInput(pattern="def encode", output_mode="content")),
        # A zero-hit grep fires the tool's own meta.suggestion: it must survive
        # the branch resolution unchanged.
        "grep_miss": ("grep", GrepInput(pattern="no_such_token_anywhere")),
        "glob": ("glob", GlobInput(pattern="app/*.py")),
        "read_file": ("read_file", ReadFileInput(file_path="app/core.py", limit=6)),
    }


def _pin_mtimes(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(root).parts:
            os.utime(path, (_PINNED_MTIME, _PINNED_MTIME))


def _index(root: Path, db: Path) -> Path:
    _pin_mtimes(root)
    return index_project_to_db(root, db)


def _answers(db: Path, root: Path) -> dict[str, dict[str, object]]:
    """``text``, ``items`` and ``meta`` of each call, JSON-normalized, the root masked."""
    router, _services = build_routers(AppConfig.load(), db_path=db, surface="mcp")
    answers = {}
    for name, (method, payload) in _nine_tool_calls().items():
        response = asyncio.run(getattr(router, method)(payload))
        answers[name] = {"text": response.text, "items": response.items, "meta": response.meta}
    rendered = json.dumps(answers, sort_keys=True, default=str).replace(str(root), "<root>")
    return json.loads(rendered)


def _all_scenarios(tmp_path: Path) -> dict[str, dict[str, dict[str, object]]]:
    root = _git_project(tmp_path / "git")
    db = _index(root, tmp_path / "git.db")
    on_main = _answers(db, root)
    _checkout_an_edited_branch(root)
    _index(root, db)
    after_checkout = _answers(db, root)
    plain = _write_project(tmp_path / "plain" / "proj")
    plain_db = _index(plain, tmp_path / "plain.db")
    return {
        "git_main": on_main,
        "git_after_checkout": after_checkout,
        "no_git": _answers(plain_db, plain),
    }


@_NEEDS_GIT
def test_all_nine_tools_answer_byte_identically_on_a_single_branch_bundle(
    tmp_path: Path,
) -> None:
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    answers = _all_scenarios(tmp_path)
    assert sorted(answers) == sorted(golden)
    for scenario, calls in golden.items():
        assert sorted(answers[scenario]) == sorted(calls), scenario
        for call, expected in calls.items():
            want = {**expected, "items": scores_within_float_noise(expected["items"])}
            assert answers[scenario][call] == want, f"{scenario}/{call}"


def test_the_golden_pins_the_branch_and_freshness_meta() -> None:
    """Guards the golden itself: it must carry the fields this ticket touches."""
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert {call["meta"]["branch"] for call in golden["git_main"].values()} == {"main"}
    assert {call["meta"]["branch"] for call in golden["git_after_checkout"].values()} == {
        "feature/x"
    }
    assert {call["meta"]["branch"] for call in golden["no_git"].values()} == {None}
    stale = {c["meta"]["index_stale"] for s in golden.values() for c in s.values()}
    assert stale == {False}
    assert golden["git_main"]["grep_miss"]["meta"]["suggestion"].startswith("[suggestion:")


@pytest.mark.skipif(not os.environ.get(_GOLDEN_ENV), reason=f"set {_GOLDEN_ENV}=1 to rewrite")
def test_write_golden(tmp_path: Path) -> None:
    answers = _all_scenarios(tmp_path)
    _GOLDEN_PATH.write_text(json.dumps(answers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
