"""The PRODUCT one child process imports: its worktree, its path, its identity.

A before/after run measures two commits of ``pydocs_mcp``, and two copies of one
package cannot live in one interpreter — so anything that has to ask a question
OF a specific commit runs in a child whose ``PYTHONPATH`` puts that commit's
worktree first. Two callers need exactly that mechanism, which is why it lives
here rather than inside either of them: the arm that answers the split
(``before_after_command``) and the plan-time probe that asks each arm's product
whether it accepts the ``--llm-block`` (``before_after_block_probe``). Sharing
one implementation is what makes the probe's answer trustworthy — it asks under
the environment the arm will really run in.

Only the PRODUCT comes from the worktree. The eval suite — the harness bridge,
the split loader, the metric layer — always comes from the checkout the command
was launched from, so one implementation of every metric scores both arms.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pydocs_eval
from pydocs_eval.campaign.before_after import MeasurementPlanError


@contextlib.contextmanager
def product_worktree(repo: Path, sha: str, root: Path) -> Iterator[Path]:
    """A detached git worktree of ``sha``, removed again when the caller finishes."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"src-{sha[:12]}"
    _run_git(repo, "worktree", "add", "--detach", str(path), sha)
    try:
        yield path
    finally:
        _run_git(repo, "worktree", "remove", "--force", str(path), check=False)


def arm_environment(worktree: Path) -> dict[str, str]:
    """The child's path: the worktree's PRODUCT, then THIS checkout's eval suite."""
    eval_src = Path(pydocs_eval.__file__).resolve().parents[1]
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(worktree / "python"), str(eval_src)]),
    }


def assert_product_under(expected: Path) -> None:
    """Refuse to measure a product the path did not actually switch.

    An installed distribution or a ``.pth`` entry can win over ``PYTHONPATH``;
    the child would then import the SAME code for both commits and the report
    would show a difference of zero that means nothing.
    """
    import pydocs_mcp

    resolved = Path(pydocs_mcp.__file__).resolve()
    if expected.resolve() not in resolved.parents:
        raise MeasurementPlanError(
            f"imported pydocs_mcp from {resolved}, expected it under {expected}; "
            "an installed copy is shadowing the worktree, so this child would not "
            "measure the commit it was asked to measure"
        )


def _run_git(repo: Path, *args: str, check: bool = True) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if check and completed.returncode != 0:
        raise MeasurementPlanError(
            f"git {' '.join(args)} failed in {repo}: {completed.stderr.strip()}"
        )


__all__ = ("arm_environment", "assert_product_under", "product_worktree")
