"""Fakes for the git-objects branch passes (#310), shared by the extra-branch
driver's tests and the refresh queue's runner tests (#317)."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydocs_mcp.application.branch_pass import BranchPassOutcome
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import BranchIndexSource
from tests._fakes import FakeGitRepository, make_fake_uow_factory

# The local branches every fake repository below carries, and their tips.
MAIN_TIP, FEATURE_TIP, RELEASE_TIP, WIP_TIP = "a" * 40, "b" * 40, "c" * 40, "d" * 40
IDLE_BRANCH_PASS = BranchPassOutcome(1, 1, 0, 0, 1, 0)


@dataclass
class RecordingBranchRefIndexer:
    """A ``BranchRefIndexer`` that records each ``(name, sha)`` pass it is asked for."""

    git: FakeGitRepository
    uow_factory: object = field(default_factory=make_fake_uow_factory)
    failing: frozenset[str] = frozenset()
    # Branch name -> the exception its pass raises (beyond the git failures).
    raising: dict[str, Exception] = field(default_factory=dict)
    outcome: BranchPassOutcome = IDLE_BRANCH_PASS
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def index_ref(
        self, name: str, ref_sha: str, *, source: BranchIndexSource = BranchIndexSource.GIT_OBJECTS
    ) -> BranchPassOutcome:
        self.calls.append((name, ref_sha))
        if name in self.failing:
            raise GitCommandError(("git", "ls-tree", ref_sha), "timeout after 30s")
        if name in self.raising:
            raise self.raising[name]
        return self.outcome


def local_branches_git(checked_out: str | None = "main") -> FakeGitRepository:
    """A repository with ``main``, ``feature/x``, ``release/1`` and ``wip``."""
    refs = {"main": MAIN_TIP, "feature/x": FEATURE_TIP, "release/1": RELEASE_TIP, "wip": WIP_TIP}
    return FakeGitRepository(
        branch=checked_out, refs={f"refs/heads/{n}": s for n, s in refs.items()}
    )
