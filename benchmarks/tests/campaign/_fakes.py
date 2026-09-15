"""Named doubles and throwaway fixtures shared by the before/after campaign tests.

Mirrors the product suite's ``tests/_fakes.py``: a test double lives here, under
its own name, instead of being monkeypatched together at each call site.

The per-arm block probe is the one seam these tests cannot exercise for real —
the live probe checks a commit out into a git worktree and runs a child process
under it. :class:`FakeArmBlockProbe` answers the same question in memory, so a
plan test stays offline and instant while still proving WHICH arms were asked.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest
from pydocs_eval.campaign.before_after_block_probe import ArmBlockAcceptance, ArmBlockVerdict

# The key that cost the 2026-09-15 run its baseline arm: the candidate knew it,
# the baseline predated it, and the block was validated against the candidate.
REJECTED_KEY = "harness.llm.parallel_tool_calls"


@dataclass
class FakeArmBlockProbe:
    """One arm's product on the block, without a worktree or a child process.

    Every arm accepts unless ``reject_role`` names one — which is the real
    failure shape: both arms are handed the same block and only ONE of them
    refuses it. ``asked`` records what each arm was shown, so a test can prove
    the probe ran per arm and saw the byte-identical block.
    """

    reject_role: str | None = None
    refused_keys: tuple[str, ...] = (REJECTED_KEY,)
    asked: list[tuple[str, str, Mapping[str, object]]] = field(default_factory=list)

    def __call__(
        self, repo: Path, commit: CommitUnderTest, block: Mapping[str, object]
    ) -> ArmBlockAcceptance:
        self.asked.append((commit.role, commit.sha, dict(block)))
        if commit.role != self.reject_role:
            return ArmBlockAcceptance(
                role=commit.role, sha=commit.sha, verdict=ArmBlockVerdict.ACCEPTS
            )
        return ArmBlockAcceptance(
            role=commit.role,
            sha=commit.sha,
            verdict=ArmBlockVerdict.REJECTS,
            detail="Extra inputs are not permitted [type=extra_forbidden]",
            keys=self.refused_keys,
        )


def git_repo_with_two_descriptions(tmp_path: Path) -> Path:
    """A throwaway repo whose two commits carry different description documents."""
    repo = tmp_path / "repo"
    descriptions = repo / "python" / "pydocs_mcp" / "defaults"
    descriptions.mkdir(parents=True)
    init_git_repo(repo)
    for text, message in (("first\n", "first"), ("second document\n", "second")):
        (descriptions / "descriptions.md").write_text(text, encoding="utf-8")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-qm", message)
    return repo


def init_git_repo(repo: Path) -> None:
    """An empty repo that can commit without the machine's own git identity."""
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.email", "t@example.com")
    run_git(repo, "config", "user.name", "t")


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
