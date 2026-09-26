"""grep / glob / read_file on any indexed branch, through one file-source seam (#314, spec §6.6).

- No selector, or the checked-out branch named: the live working tree, exactly
  today's walk and reads (the default path never looks at git).
- A branch checked out in a sibling worktree: that worktree's live files.
- Any other indexed branch: its committed tree ∩ the discovery scope, read
  from git objects — one tree listing plus ONE blob batch per grep or glob,
  one path's listing plus its blob per read_file, since blob bytes have no
  plumbing reader (spec §6.6 sanctions the bounded read; §6.11 maps its
  failure to ``ServiceUnavailableError``).
- Dependency files stay on disk: dependencies are branch-agnostic (Q1).
- A ``read`` pointer carries no branch yet (the tools take one since #315; the
  pointer grammar does not), so only answers read from the project checkout
  offer one.
- A landing unit has no tree: glob and read_file refuse it, grep answers empty
  with the scope hint (the §6.5b split, #315).

The router's threading of the resolution lives in ``test_tool_router_branch.py``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.application.branch_resolution import BranchSelectorKind, ResolvedBranch
from pydocs_mcp.application.file_sources import GitTreeFileSource, WorkingTreeFileSource
from pydocs_mcp.application.file_tools import FileToolsService
from pydocs_mcp.application.mcp_errors import InvalidArgumentError, ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import GlobInput, GrepInput, ReadFileInput
from pydocs_mcp.application.protocols import FileSource
from pydocs_mcp.application.truncation import TruncationEntry, ledger_scope
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.models import BranchIndexSource, LandingKind
from pydocs_mcp.project_toml import ProjectExcludes
from pydocs_mcp.retrieval.config import FilesConfig, SuggestionsConfig
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import FakeGitRepository
from tests._git_sandbox import (
    NoProcessSpawned,
    commit_text,
    isolate_git_config,
    requires_git,
    run_git,
)

INDEXED, LIVE = "1" * 40, "2" * 40
_ALPHA = "def alpha():\n    return 1\n"
_BETA = "def beta():\n    return 2\n"


def _service(
    root: Path | None,
    git: object | None = None,
    *,
    deps: tuple[str, ...] = (),
    scope: DiscoveryScopeConfig | None = None,
) -> FileToolsService:
    project_scope = scope or DiscoveryScopeConfig()

    async def _deps() -> tuple[str, ...]:
        return deps

    extra = {} if git is None else {"git": git}
    return FileToolsService(
        project_root=root,
        project_scope=project_scope,
        dependency_scope=DiscoveryScopeConfig(),
        list_dependency_packages=_deps,
        files_config=FilesConfig(),
        **extra,  # type: ignore[arg-type]
    )


def _record(name: str, head: str = INDEXED, **kw: object) -> BranchRecord:
    return BranchRecord(name, head, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, **kw)


def _named(name: str, *, head: str = INDEXED, live: str | None = None) -> ResolvedBranch:
    return ResolvedBranch(name, _record(name, head), BranchSelectorKind.NAME, live)


def _default(name: str) -> ResolvedBranch:
    return ResolvedBranch(name, _record(name), BranchSelectorKind.DEFAULT)


def _feature_tree_git() -> FakeGitRepository:
    return FakeGitRepository(
        trees={
            INDEXED: (
                ("pkg/a.py", "s1", len(_ALPHA)),
                ("pkg/b.py", "s2", len(_BETA)),
                ("node_modules/x.py", "s3", 6),  # the exclusion floor applies to blobs too
                ("pkg/big.py", "s4", 2_000_000),  # over max_file_size_bytes
                ("README.bin", "s5", 4),  # no allowed extension
            )
        },
        blobs={"s1": _ALPHA, "s2": _BETA, "s3": "x = 1\n", "s4": "big\n", "s5": "bin"},
    )


def _disk_project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def on_disk():\n    return 0\n", encoding="utf-8")
    return tmp_path


# ── a branch checked out nowhere: git objects ─────────────────────────────


async def test_the_three_tools_serve_the_committed_tree_of_a_branch_checked_out_nowhere(
    tmp_path: Path,
) -> None:
    root = _disk_project(tmp_path)
    svc = _service(root, _feature_tree_git())
    branch = _named("feature/x")
    text, items, _ = await svc.grep(
        GrepInput(pattern="return", output_mode="content"), branch=branch
    )
    assert "pkg/b.py:2:    return 2" in text and "on_disk" not in text
    assert {item["path"] for item in items} == {"pkg/a.py", "pkg/b.py"}
    text, items, _ = await svc.glob(GlobInput(pattern="**/*"), branch=branch)
    # Git objects carry no mtime: every entry ties at 0.0 and the path breaks the tie.
    assert text.splitlines() == ["pkg/a.py", "pkg/b.py"]
    assert items == ({"path": "pkg/a.py", "mtime": 0.0}, {"path": "pkg/b.py", "mtime": 0.0})
    text, items, _ = await svc.read_file(ReadFileInput(file_path="pkg/b.py"), branch=branch)
    assert text == "     1\tdef beta():\n     2\t    return 2"
    assert items == ({"path": "pkg/b.py", "start_line": 1, "end_line": 2},)
    # The working tree is untouched by any of the three calls.
    assert (root / "pkg" / "a.py").read_text(encoding="utf-8").startswith("def on_disk")


async def test_grep_reads_the_filtered_candidates_in_one_blob_batch(tmp_path: Path) -> None:
    git = _feature_tree_git()
    svc = _service(_disk_project(tmp_path), git)
    await svc.grep(GrepInput(pattern="beta", path="pkg", glob="b.py"), branch=_named("feature/x"))
    assert git.blob_reads == [(("s2", "pkg/b.py"),)]


async def test_read_file_looks_up_only_the_path_it_reads(tmp_path: Path) -> None:
    """Spec §6.6 reads one file: a read — and each continuation page — costs
    that path's lookup plus its blob, never a listing of the whole tree."""
    git = _feature_tree_git()
    svc = _service(_disk_project(tmp_path), git)
    branch = _named("feature/x")
    first, _, _ = await svc.read_file(ReadFileInput(file_path="pkg/b.py", limit=1), branch=branch)
    rest, _, _ = await svc.read_file(
        ReadFileInput(file_path="pkg/b.py", offset=2, limit=1), branch=branch
    )
    assert (first, rest) == ("     1\tdef beta():", "     2\t    return 2")
    assert git.tree_listings == [(INDEXED, ("pkg/b.py",))] * 2
    assert git.blob_reads == [(("s2", "pkg/b.py"),)] * 2


async def test_the_branch_tip_is_read_when_its_ref_moved_since_the_index_pass(
    tmp_path: Path,
) -> None:
    git = FakeGitRepository(
        trees={INDEXED: (("pkg/a.py", "s1", 5),), LIVE: (("pkg/c.py", "s3", 5),)},
        blobs={"s1": "old\n", "s3": "new\n"},
    )
    svc = _service(_disk_project(tmp_path), git)
    text, _, _ = await svc.glob(GlobInput(pattern="**/*.py"), branch=_named("f", live=LIVE))
    # File tools are live, as on the working tree; meta.index_stale says the index lags.
    assert text == "pkg/c.py"


async def test_read_file_reads_any_committed_path_but_no_other(tmp_path: Path) -> None:
    svc = _service(_disk_project(tmp_path), _feature_tree_git())
    branch = _named("feature/x")
    # The read boundary is the tree, not the discovery scope (contract §3.9).
    text, _, _ = await svc.read_file(ReadFileInput(file_path="node_modules/x.py"), branch=branch)
    assert text == "     1\tx = 1"
    with pytest.raises(InvalidArgumentError, match=r"no such file on branch 'feature/x'"):
        await svc.read_file(ReadFileInput(file_path="pkg/missing.py"), branch=branch)
    with pytest.raises(InvalidArgumentError, match="outside the project root"):
        await svc.read_file(ReadFileInput(file_path="../escape.py"), branch=branch)


async def test_read_file_windows_a_blob_like_a_file(tmp_path: Path) -> None:
    body = "".join(f"line {n}\n" for n in range(1, 11))
    git = FakeGitRepository(trees={INDEXED: (("m.py", "s", len(body)),)}, blobs={"s": body})
    svc = _service(_disk_project(tmp_path), git)
    text, items, meta = await svc.read_file(
        ReadFileInput(file_path="m.py", offset=3, limit=2), branch=_named("f")
    )
    assert text == "     3\tline 3\n     4\tline 4"
    assert items == ({"path": "m.py", "start_line": 3, "end_line": 4},) and meta == {
        "truncated": True
    }


async def test_a_binary_blob_is_skipped_by_grep_and_refused_by_read_file(tmp_path: Path) -> None:
    git = FakeGitRepository(
        trees={INDEXED: (("bin.py", "s1", 7), ("txt.py", "s2", 7))},
        blobs={"s1": "a\x00 = 1\n", "s2": "a = 1\n"},
    )
    svc = _service(_disk_project(tmp_path), git)
    branch = _named("f")
    text, _, _ = await svc.grep(GrepInput(pattern="a"), branch=branch)
    assert text == "txt.py"
    with pytest.raises(InvalidArgumentError, match="looks binary"):
        await svc.read_file(ReadFileInput(file_path="bin.py"), branch=branch)


async def test_dependency_files_stay_on_disk_on_a_branch(tmp_path: Path) -> None:
    svc = _service(_disk_project(tmp_path), _feature_tree_git(), deps=("pyyaml",))
    branch = _named("feature/x")
    text, items, _ = await svc.grep(
        GrepInput(pattern=r"^class YAMLError|^def beta", scope="all"), branch=branch
    )
    paths = [str(item["path"]) for item in items]
    assert paths[0] == "pkg/b.py" and any(p.endswith("yaml/error.py") for p in paths[1:])
    dep_file = next(p for p in paths if p.endswith("yaml/error.py"))
    text, _, _ = await svc.read_file(ReadFileInput(file_path=dep_file, limit=3), branch=branch)
    assert text.startswith("     1\t")


async def test_a_dependency_installed_inside_the_project_reads_from_disk_on_a_branch() -> None:
    """A local virtualenv under the project root is in no committed tree, yet a
    dependency file stays readable on every branch (Q1)."""
    import yaml

    site_packages = Path(yaml.__file__).resolve().parents[1]
    root = site_packages.parent  # the "project" holds site-packages, as a local .venv does
    git = FakeGitRepository(trees={INDEXED: (("pkg/a.py", "s1", 5),)}, blobs={"s1": "x\n"})
    svc = _service(root, git, deps=("pyyaml",))
    branch = _named("feature/x")
    dep_file = str(site_packages / "yaml" / "error.py")
    text, items, _ = await svc.read_file(ReadFileInput(file_path=dep_file, limit=2), branch=branch)
    assert text.startswith("     1\t") and items[0]["path"] == "site-packages/yaml/error.py"
    # A project path the branch lacks is still refused.
    with pytest.raises(InvalidArgumentError, match="no such file on branch"):
        await svc.read_file(ReadFileInput(file_path="pkg/gone.py"), branch=branch)


async def test_a_git_failure_on_the_request_path_is_service_unavailable(tmp_path: Path) -> None:
    svc = _service(_disk_project(tmp_path), FakeGitRepository(fail=True))
    with pytest.raises(ServiceUnavailableError, match=r"cannot read branch 'feature/x'"):
        await svc.glob(GlobInput(pattern="**/*"), branch=_named("feature/x"))


async def test_without_git_a_branch_checked_out_nowhere_is_service_unavailable(
    tmp_path: Path,
) -> None:
    svc = _service(_disk_project(tmp_path))  # the Null port: no binary, git off, no repository
    with pytest.raises(ServiceUnavailableError, match="lists no committed file"):
        await svc.grep(GrepInput(pattern="x"), branch=_named("feature/x"))
    # An empty one-path lookup is git's absence here, not the path's.
    with pytest.raises(ServiceUnavailableError, match="lists no committed file"):
        await svc.read_file(ReadFileInput(file_path="pkg/a.py"), branch=_named("feature/x"))


def _landing_unit() -> ResolvedBranch:
    unit = "abcdef1" + "0" * 33
    record = _record(unit, unit, landing_kind=LandingKind.SINGLE_COMMIT)
    return ResolvedBranch(unit, record, BranchSelectorKind.LANDING_SHA)


async def test_a_landing_unit_is_refused_rather_than_served_as_a_tree(tmp_path: Path) -> None:
    svc = _service(_disk_project(tmp_path), _feature_tree_git())
    with pytest.raises(InvalidArgumentError, match="'abcdef1' is a landing unit"):
        await svc.glob(GlobInput(pattern="**/*"), branch=_landing_unit())
    with pytest.raises(InvalidArgumentError, match="'abcdef1' is a landing unit"):
        await svc.read_file(ReadFileInput(file_path="pkg/a.py"), branch=_landing_unit())


@pytest.mark.parametrize("scope", ["project", "deps", "all"])
async def test_grep_answers_a_landing_unit_empty_with_the_scope_hint(
    tmp_path: Path, scope: str
) -> None:
    """The §6.5b split (#315): grep carries a suggestion field, so a unit — no
    tree to scan, its diff slice shipping with the P2 half of the amendment —
    answers empty and says why, in body and meta like every grep rule, on every
    scope; nothing of git's is read."""
    hint = "[suggestion: landing unit abcdef1 has no tree; use scope=diff or name a branch]"
    svc = _service(_disk_project(tmp_path), FakeGitRepository(), deps=("somedep",))
    answer = await svc.grep(GrepInput(pattern="def", scope=scope), branch=_landing_unit())
    assert answer == (f"No matches.\n{hint}", (), {"suggestion": hint})


async def test_the_grep_landing_hint_logs_its_own_rule(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR 0007 attributes every hint by its fired rule: the landing hint rides
    ``grep_zero_hit``'s flag, but must not log as an ordinary zero hit."""
    svc = _service(_disk_project(tmp_path))
    with caplog.at_level(logging.INFO, logger="pydocs_mcp.application.suggestions"):
        await svc.grep(GrepInput(pattern="def"), branch=_landing_unit())
    fired = [json.loads(r.message) for r in caplog.records if "suggestion_fired" in r.message]
    assert fired == [{"event": "suggestion_fired", "tool": "grep", "rule": "landing_unit"}]


async def test_the_grep_landing_hint_obeys_the_grep_zero_hit_flag(tmp_path: Path) -> None:
    svc = replace(
        _service(_disk_project(tmp_path)), suggestions=SuggestionsConfig(grep_zero_hit=False)
    )
    answer = await svc.grep(GrepInput(pattern="def"), branch=_landing_unit())
    assert answer == ("No matches.", (), {})


async def test_a_read_only_bundle_keeps_its_error_for_a_named_branch(tmp_path: Path) -> None:
    svc = _service(None, _feature_tree_git())
    with pytest.raises(ServiceUnavailableError, match="read-only bundle"):
        await svc.glob(GlobInput(pattern="**/*"), branch=_named("feature/x"))


def test_both_sources_conform_to_the_file_source_protocol(tmp_path: Path) -> None:
    scope = DiscoveryScopeConfig()
    working = WorkingTreeFileSource(tmp_path, scope)
    excludes = ProjectExcludes(frozenset(), frozenset())
    committed = GitTreeFileSource(FakeGitRepository(), "f", INDEXED, scope, excludes, tmp_path)
    assert isinstance(working, FileSource) and isinstance(committed, FileSource)


# ── the default selector: today's working tree, whatever the resolution ────


async def test_no_selector_serves_the_working_tree_without_touching_git(tmp_path: Path) -> None:
    root = _disk_project(tmp_path)
    git = FakeGitRepository(fail=True)  # any port call would raise
    svc = _service(root, git)
    for branch in (None, _default("main")):
        text, _, _ = await svc.glob(GlobInput(pattern="**/*.py"), branch=branch)
        assert text == "pkg/a.py"
        text, _, _ = await svc.grep(GrepInput(pattern="on_disk"), branch=branch)
        assert text == "pkg/a.py"
        text, _, _ = await svc.read_file(ReadFileInput(file_path="pkg/a.py"), branch=branch)
        assert "on_disk" in text


# ── read pointers: a branchless call must read the bytes it points at ──────


def _long_module(last: str) -> str:
    """59 filler lines, then ``last`` on line 60: far enough down that a grep
    hit's read window reaches past the one line the hit renders."""
    return "".join(f"line_{n} = {n}\n" for n in range(1, 60)) + f"{last}\n"


async def test_an_answer_from_git_objects_offers_no_branchless_read_pointer(
    tmp_path: Path,
) -> None:
    """A ``read`` pointer's grammar carries no branch yet (the tools take one
    since #315), and a branchless read_file reads the project checkout:
    pointing there from another branch's answer would hand back the other
    version's lines, or a 'cannot read' error for a file only the branch holds."""
    body = _long_module("def beta():")
    git = FakeGitRepository(trees={INDEXED: (("m.py", "s", len(body)),)}, blobs={"s": body})
    svc = _service(_disk_project(tmp_path), git)
    branch = _named("feature/x")
    text, _, _ = await svc.grep(GrepInput(pattern="beta", output_mode="content"), branch=branch)
    assert text == "m.py:60:def beta():"
    with ledger_scope() as ledger:
        _, _, meta = await svc.read_file(ReadFileInput(file_path="m.py", limit=5), branch=branch)
    # The cut is still named; only the call that would read the wrong bytes is withheld.
    assert meta == {"truncated": True}
    assert ledger.entries == (
        TruncationEntry(description="55 more lines of m.py after line 5", recovery=""),
    )


# ── real repositories ──────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``main`` checked out with an uncommitted edit and an untracked
    ``pkg/scratch.py``; ``feature/x`` changes a.py and adds b.py."""
    isolate_git_config(monkeypatch, tmp_path / "home")
    root = tmp_path / "proj"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "pkg/a.py", "value = 'main'\n", "init")
    run_git(root, "switch", "-q", "-c", "feature/x")
    commit_text(root, "pkg/a.py", "value = 'feature'\n", "edit a")
    commit_text(root, "pkg/b.py", "only = 'feature'\n", "add b")
    run_git(root, "switch", "-q", "main")
    (root / "pkg" / "a.py").write_text("value = 'main, uncommitted'\n", encoding="utf-8")
    (root / "pkg" / "scratch.py").write_text(_long_module("scratch_value = 1"), encoding="utf-8")
    return root


def _real(root: Path, name: str) -> ResolvedBranch:
    head = run_git(root, "rev-parse", name)
    return ResolvedBranch(name, _record(name, head), BranchSelectorKind.NAME, head)


@requires_git
async def test_an_untracked_file_exists_only_on_the_working_tree_branch(repo: Path) -> None:
    """Spec §6.6: a branch checked out nowhere serves its committed tree, so a
    file that lives only on disk is absent from all three tools there."""
    svc = _service(repo, SubprocessGitRepository(repo))
    feature = _real(repo, "feature/x")
    listed, _, _ = await svc.glob(GlobInput(pattern="**/*.py"), branch=feature)
    assert listed.splitlines() == ["pkg/a.py", "pkg/b.py"]
    _, items, _ = await svc.grep(GrepInput(pattern="value"), branch=feature)
    assert [item["path"] for item in items] == ["pkg/a.py"]
    with pytest.raises(InvalidArgumentError, match=r"no such file on branch 'feature/x'"):
        await svc.read_file(ReadFileInput(file_path="pkg/scratch.py"), branch=feature)
    # The checkout serves it, as today.
    assert "pkg/scratch.py" in (await svc.glob(GlobInput(pattern="**/*.py")))[0]


@requires_git
async def test_read_file_returns_the_selected_branchs_content(repo: Path) -> None:
    svc = _service(repo, SubprocessGitRepository(repo))
    on_feature, _, _ = await svc.read_file(
        ReadFileInput(file_path="pkg/a.py"), branch=_real(repo, "feature/x")
    )
    assert on_feature == "     1\tvalue = 'feature'"
    on_main, _, _ = await svc.read_file(
        ReadFileInput(file_path="pkg/a.py"), branch=_real(repo, "main")
    )
    assert on_main == "     1\tvalue = 'main, uncommitted'"  # the checkout: live bytes
    listed, _, _ = await svc.glob(GlobInput(pattern="pkg/*.py"), branch=_real(repo, "feature/x"))
    assert listed.splitlines() == ["pkg/a.py", "pkg/b.py"]


@requires_git
async def test_naming_the_checked_out_branch_answers_like_no_selector_and_spawns_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(repo, SubprocessGitRepository(repo))
    main = _real(repo, "main")
    monkeypatch.setattr(subprocess, "Popen", NoProcessSpawned)
    calls = (
        ("grep", GrepInput(pattern="value", output_mode="content")),
        ("glob", GlobInput(pattern="**/*.py")),
        ("read_file", ReadFileInput(file_path="pkg/a.py")),
    )
    for method, payload in calls:
        named = await getattr(svc, method)(payload, branch=main)
        assert named == await getattr(svc, method)(payload), method
    # The project checkout keeps its read pointers: a branchless call reads these bytes.
    grep_text, _, _ = await svc.grep(calls[0][1], branch=main)  # type: ignore[arg-type]
    assert "Together: [[next:read:pkg/scratch.py:50+11]]" in grep_text


@requires_git
async def test_a_branch_checked_out_in_a_sibling_worktree_serves_its_live_files(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sibling = repo.parent / "wt"
    run_git(repo, "worktree", "add", "-q", str(sibling), "feature/x")
    (sibling / "pkg" / "live.py").write_text(_long_module("live = True"), encoding="utf-8")
    svc = _service(repo, SubprocessGitRepository(repo))
    branch = _real(repo, "feature/x")
    monkeypatch.setattr(subprocess, "Popen", NoProcessSpawned)
    text, _, _ = await svc.glob(GlobInput(pattern="**/*.py"), branch=branch)
    assert sorted(text.splitlines()) == ["pkg/a.py", "pkg/b.py", "pkg/live.py"]
    text, _, _ = await svc.read_file(
        ReadFileInput(file_path="pkg/live.py", offset=60), branch=branch
    )
    assert text == "    60\tlive = True"
    # A branchless read pointer would read the project root's checkout, not this one.
    text, _, _ = await svc.grep(GrepInput(pattern="live =", output_mode="content"), branch=branch)
    assert text == "pkg/live.py:60:live = True"
