"""Before/after's product clone: blobless, reused when present, holding both frozen commits.

Git runs for real against the fixture's bare ``origin`` (prior art: the
campaign ``test_index_cache``), served with partial-clone filtering on, as
GitHub serves the public product repository.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bounded_command import CompletedCommand, run_subprocess
from pydocs_eval.bench_vm.product_clone import ProductCloneError, ensure_product_clone

from ._git_fixture import FixtureRepo, make_fixture_repo, run_git


@pytest.fixture
def repo(tmp_path: Path) -> FixtureRepo:
    fixture = make_fixture_repo(tmp_path / "git")
    run_git(fixture.origin, "config", "uploadpack.allowFilter", "true")
    return fixture


def _url(repo: FixtureRepo) -> str:
    return repo.origin.as_uri()


def _has_commit(clone: Path, sha: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def test_a_first_call_makes_a_blobless_clone_holding_both_commits(
    repo: FixtureRepo, tmp_path: Path
) -> None:
    baseline = repo.head()
    candidate = repo.commit_unpushed("candidate")
    repo.push_main()
    clone = tmp_path / "cache" / "product-repo"

    ensure_product_clone(_url(repo), clone, (baseline, candidate))

    assert run_git(clone, "config", "remote.origin.partialclonefilter") == "blob:none"
    assert _has_commit(clone, baseline)
    assert _has_commit(clone, candidate)


def test_both_commits_check_out_as_worktrees(repo: FixtureRepo, tmp_path: Path) -> None:
    baseline = repo.head()
    candidate = repo.commit_unpushed("candidate")
    repo.push_main()
    clone = tmp_path / "product-repo"

    ensure_product_clone(_url(repo), clone, (baseline, candidate))

    for name, sha in (("base", baseline), ("cand", candidate)):
        worktree = tmp_path / "worktrees" / name
        run_git(clone, "worktree", "add", "--detach", str(worktree), sha)
        assert run_git(worktree, "rev-parse", "HEAD") == sha
    assert (tmp_path / "worktrees" / "cand" / "notes" / "candidate.txt").is_file()


def test_an_existing_clone_is_reused_and_fetches_a_newer_commit(
    repo: FixtureRepo, tmp_path: Path
) -> None:
    clone = tmp_path / "product-repo"
    ensure_product_clone(_url(repo), clone, (repo.head(),))
    marker = clone / ".git" / "bench-marker"
    marker.write_text("kept", encoding="utf-8")
    newer = repo.commit_unpushed("newer")
    repo.push_main()

    ensure_product_clone(_url(repo), clone, (newer,))

    assert marker.read_text(encoding="utf-8") == "kept"
    assert _has_commit(clone, newer)


def test_a_commit_the_remote_does_not_have_fails_naming_it(
    repo: FixtureRepo, tmp_path: Path
) -> None:
    unpushed = repo.commit_unpushed("never pushed")

    with pytest.raises(ProductCloneError, match=unpushed):
        ensure_product_clone(_url(repo), tmp_path / "product-repo", (unpushed,))


def test_an_unreachable_remote_fails_naming_the_url(tmp_path: Path) -> None:
    missing = (tmp_path / "no-such-origin.git").as_uri()

    with pytest.raises(ProductCloneError, match="no-such-origin"):
        ensure_product_clone(missing, tmp_path / "product-repo", ("a" * 40,))
    assert not (tmp_path / "product-repo").exists()


@dataclass
class CloneCutShortOnce:
    """Runs git for real, except that the first clone leaves a half-written clone and times out."""

    cut_short: bool = False

    def __call__(
        self, argv: Sequence[str], *, stdin: bytes | None, timeout_s: float, env: Mapping[str, str]
    ) -> CompletedCommand:
        if "clone" in argv and not self.cut_short:
            self.cut_short = True
            destination = Path(argv[-1])
            (destination / ".git").mkdir(parents=True)
            (destination / ".git" / "HEAD").write_text("half", encoding="utf-8")
            raise subprocess.TimeoutExpired(list(argv), timeout_s)
        return run_subprocess(argv, stdin=stdin, timeout_s=timeout_s, env=env)


def test_a_clone_retried_after_a_timeout_starts_from_an_empty_directory(
    repo: FixtureRepo, tmp_path: Path
) -> None:
    runner = CloneCutShortOnce()
    clone = tmp_path / "product-repo"

    ensure_product_clone(_url(repo), clone, (repo.head(),), runner=runner, sleep=lambda _s: None)

    assert runner.cut_short
    assert _has_commit(clone, repo.head())


def test_a_directory_without_a_clone_in_it_is_replaced(repo: FixtureRepo, tmp_path: Path) -> None:
    clone = tmp_path / "product-repo"
    (clone / "stray").mkdir(parents=True)
    (clone / "stray" / "file.txt").write_text("left over", encoding="utf-8")

    ensure_product_clone(_url(repo), clone, (repo.head(),))

    assert _has_commit(clone, repo.head())
    assert not (clone / "stray").exists()
