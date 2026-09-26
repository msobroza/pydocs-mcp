"""#314 end to end: ``grep`` / ``glob`` / ``read_file`` answer from the selected branch.

A real repository indexed through ``pydocs-mcp index`` itself — ``main`` from
the checkout, then ``feature/x`` from git objects — served through
``build_routers``: a path that differs between the branches reads each
branch's own bytes, ``glob`` lists each branch's own files, and a request that
names no branch answers exactly as the single-branch bundle did (AC-3).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application.mcp_inputs import GlobInput, GrepInput, ReadFileInput
from pydocs_mcp.db import cache_path_for_project
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._git_sandbox import isolate_git_config, requires_git, run_git
from tests.application._router_fakes import with_branch

pytestmark = requires_git

_CALLS: dict[str, tuple[str, Any]] = {
    "grep": ("grep", GrepInput(pattern="return", output_mode="content")),
    "grep_miss": ("grep", GrepInput(pattern="gamma")),
    "glob": ("glob", GlobInput(pattern="app/*.py")),
    "read_file": ("read_file", ReadFileInput(file_path="app/core.py")),
}


@pytest.fixture(autouse=True)
def _sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _two_branch_project(tmp_path: Path) -> Path:
    """``feature/x`` rewrites ``app/core.py`` and adds ``app/extra.py``; ``main`` stays out."""
    root = tmp_path / "proj"
    _write(root / "app" / "__init__.py", "")
    _write(root / "app" / "core.py", 'def encode(row):\n    return "main"\n')
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "init")
    run_git(root, "switch", "-q", "-c", "feature/x")
    _write(root / "app" / "core.py", 'def encode(row):\n    return "feature"\n')
    _write(root / "app" / "extra.py", "def gamma():\n    return 3\n")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "feature")
    run_git(root, "switch", "-q", "main")
    return root


def _index(monkeypatch: pytest.MonkeyPatch, root: Path, *flags: str) -> None:
    argv = ["pydocs-mcp", "index", str(root), "--no-inspect", "--skip-deps", *flags]
    monkeypatch.setattr("sys.argv", argv)
    assert _cli_main() == 0


def _router(root: Path) -> Any:
    router, _services = build_routers(
        AppConfig.load(), db_path=cache_path_for_project(root.resolve()), surface="mcp"
    )
    return router


def _call(router: Any, method: str, payload: Any) -> Any:
    return asyncio.run(getattr(router, method)(payload))


def _answers(router: Any) -> dict[str, tuple[str, Any, Any]]:
    answers = {}
    for name, (method, payload) in _CALLS.items():
        response = _call(router, method, payload)
        answers[name] = (response.text, response.items, response.meta)
    return answers


def test_each_branch_serves_its_own_files_and_no_selector_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _two_branch_project(tmp_path)
    _index(monkeypatch, root)
    single_branch = _answers(_router(root))
    _index(monkeypatch, root, "--branch", "feature/x")
    router = _router(root)
    assert _answers(router) == single_branch

    def on(branch: str, method: str, payload: Any) -> Any:
        return _call(router, method, with_branch(payload, branch))

    feature = on("feature/x", "read_file", ReadFileInput(file_path="app/core.py"))
    main = on("main", "read_file", ReadFileInput(file_path="app/core.py"))
    assert '    return "feature"' in feature.text and feature.meta["branch"] == "feature/x"
    assert '    return "main"' in main.text and main.meta["branch"] == "main"
    listed = on("feature/x", "glob", GlobInput(pattern="app/*.py"))
    assert [row["path"] for row in listed.items] == [
        "app/__init__.py",
        "app/core.py",
        "app/extra.py",
    ]
    assert "app/extra.py" not in on("main", "glob", GlobInput(pattern="app/*.py")).text
    found = on("feature/x", "grep", GrepInput(pattern="gamma", output_mode="content"))
    assert [row["path"] for row in found.items] == ["app/extra.py"]
    # No read pointer: a branchless read_file reads main's checkout, which lacks the file.
    assert "read_file(" not in found.text
