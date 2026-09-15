"""Does EACH arm's own product accept the ``--llm-block``? Asked while the plan is free.

The block reaches both arms byte-identically, but only one of the two commits is
the one under test. ``load_arm_llm_block`` validates it against the product of
the checkout the command runs from — the CANDIDATE's — so a key that commit
added sails through the plan and then explodes in the baseline arm, whose
settings model forbids extras it has never heard of::

    ValidationError: 1 validation error for AskYourDocsRunnerSettings
    harness.llm.parallel_tool_calls
      Extra inputs are not permitted [type=extra_forbidden, input_value=None, ...]

That is not hypothetical. On 2026-09-15 it raised at runner-build time on every
baseline rollout: the arm booked 54 attempts at the plan's assumed cost, halted
under the budget guard with zero answered tasks, and the candidate arm was
spending real money at the endpoint the whole time. So the plan asks each commit
the question itself, in that commit's own worktree, before anything is built,
started or spent:

- :func:`probe_arm_block` (parent) checks one arm's commit out and runs the
  child under it — the same worktree + ``PYTHONPATH`` recipe the arm uses.
- :func:`verdict_here` (child) builds that product's runner settings from the
  block exactly as a rollout does, and reports acceptance or the refusal.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydocs_eval.campaign.before_after import (
    SHORT_SHA_CHARS,
    CommitUnderTest,
    MeasurementPlanError,
)
from pydocs_eval.campaign.before_after_arm import ARM_ARCHITECTURE
from pydocs_eval.campaign.before_after_product import (
    arm_environment,
    assert_product_under,
    product_worktree,
)

# The child: this very module, run under ONE arm's product. Spelled out rather
# than taken from ``__name__``, which is ``__main__`` in the child itself.
CHILD_MODULE = "pydocs_eval.campaign.before_after_block_probe"

_BLOCK_FILENAME = "arm_llm_block.json"
_WORKTREE_DIRNAME = "worktrees"

# Placeholders for the probe's settings: the runner is built and thrown away, so
# nothing reads a workspace, a model or a trace root (the ``_PROBE_UNUSED``
# precedent in ``before_after_llm_block``). The turn budget is likewise not a
# block key — any positive int validates.
_PROBE_UNUSED = ""
_PROBE_TURNS = 1

# 2 is the operator-error exit code the eval CLIs already use.
_EXIT_OK = 0
_EXIT_INPUT_ERROR = 2


class ArmBlockVerdict(StrEnum):
    """What ONE arm's product said about the ``--llm-block`` it was handed."""

    ACCEPTS = "accepts"
    REJECTS = "rejects"


@dataclass(frozen=True, slots=True)
class ArmBlockAcceptance:
    """One arm's verdict on the pinned block, and the key(s) it refused.

    ``keys`` names the dotted block key(s) the product blamed and ``detail``
    carries its own message — the two things an operator needs to fix the block
    file. It travels from the child as JSON, so it is also what a run log keeps.
    """

    role: str
    sha: str
    verdict: ArmBlockVerdict
    detail: str = ""
    keys: tuple[str, ...] = ()

    @property
    def rejected(self) -> bool:
        return self.verdict is ArmBlockVerdict.REJECTS

    def plan_line(self) -> str:
        """The plan's per-arm line, printed under the block it judges."""
        return f"            {self.role} {self.sha[:SHORT_SHA_CHARS]}: {self.verdict} the block"

    def refusal(self, source: str) -> str:
        """Why the run must not start — named arm, named key, named fix."""
        return (
            f"--llm-block {source}: the {self.role} product at "
            f"{self.sha[:SHORT_SHA_CHARS]} REJECTS {self._blamed()} ({self.detail}). "
            "Both arms are handed the SAME block, so either remove the key from "
            "the block, or pick a baseline that knows it."
        )

    def _blamed(self) -> str:
        """The refused key(s), or the block itself when the product named none."""
        return ", ".join(self.keys) if self.keys else "the block"

    def to_json(self) -> str:
        """The child's one line of stdout."""
        return json.dumps(
            {
                "role": self.role,
                "sha": self.sha,
                "verdict": str(self.verdict),
                "detail": self.detail,
                "keys": list(self.keys),
            }
        )


def acceptance_from_json(text: str) -> ArmBlockAcceptance:
    """Rebuild one acceptance from the child's stdout.

    Raises:
        MeasurementPlanError: the child printed something else entirely.
    """
    try:
        payload = json.loads(text)
        return ArmBlockAcceptance(
            role=str(payload["role"]),
            sha=str(payload["sha"]),
            verdict=ArmBlockVerdict(payload["verdict"]),
            detail=str(payload["detail"]),
            keys=tuple(str(key) for key in payload["keys"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise MeasurementPlanError(
            f"the block probe child printed {text!r}, expected one JSON verdict "
            f'({{"role", "sha", "verdict", "detail", "keys"}}): {exc}'
        ) from exc


def probe_arm_block(
    repo: Path, commit: CommitUnderTest, block: Mapping[str, object]
) -> ArmBlockAcceptance:
    """Ask ONE arm's product whether it accepts ``block`` — in that commit's tree.

    Raises:
        MeasurementPlanError: the commit cannot be checked out, or the child
            failed to produce a verdict at all (which is not a "no").
    """
    with tempfile.TemporaryDirectory(prefix="before-after-block-probe-") as scratch:
        block_file = Path(scratch) / _BLOCK_FILENAME
        block_file.write_text(json.dumps(dict(block)), encoding="utf-8")
        with product_worktree(repo, commit.sha, Path(scratch) / _WORKTREE_DIRNAME) as worktree:
            return _child_verdict(worktree, commit=commit, block_file=block_file)


def _child_verdict(
    worktree: Path, *, commit: CommitUnderTest, block_file: Path
) -> ArmBlockAcceptance:
    """Run the probe child under ``worktree``'s product and read its verdict."""
    completed = subprocess.run(
        child_command(worktree, commit=commit, block_file=block_file),
        env=arm_environment(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != _EXIT_OK:
        raise MeasurementPlanError(
            f"could not ask the {commit.role} product at {commit.sha[:SHORT_SHA_CHARS]} "
            f"about --llm-block: the probe child exited {completed.returncode} "
            f"({completed.stderr.strip() or 'no stderr'})"
        )
    return acceptance_from_json(completed.stdout)


def child_command(worktree: Path, *, commit: CommitUnderTest, block_file: Path) -> list[str]:
    """The child's argv: this module, run against ONE commit's product."""
    return [
        sys.executable,
        "-m",
        CHILD_MODULE,
        "--block",
        str(block_file),
        "--role",
        commit.role,
        "--commit",
        commit.sha,
        "--expect-product-under",
        str(worktree),
    ]


def verdict_here(block: Mapping[str, object], *, role: str, sha: str) -> ArmBlockAcceptance:
    """THIS process's product on ``block``, built exactly as a rollout builds it.

    WHY the whole runner and not just ``AskYourDocsRunnerSettings``: this is the
    call the rollout makes (``before_after_arm.build_product_harness_runner`` →
    ``build_ask_harness_runner``), so it is the faithful question — and every
    step of it is pure. The extras guard is a ``find_spec`` check, the factory
    lookup is an import, and the harness runner's ``__init__`` only stores the
    validated settings: no serve child, no endpoint, no token. The faithful
    check is also the free one.
    """
    from pydocs_eval.optimize.ask_binding import build_ask_harness_runner

    try:
        build_ask_harness_runner(
            workspace=Path(_PROBE_UNUSED),
            model=_PROBE_UNUSED,
            architecture=ARM_ARCHITECTURE,
            max_agent_turns=_PROBE_TURNS,
            trace_root=_PROBE_UNUSED,
            harness_llm=dict(block),
        )
    except ValueError as exc:
        # pydantic's ValidationError IS a ValueError, so no pydantic import here.
        return ArmBlockAcceptance(
            role=role,
            sha=sha,
            verdict=ArmBlockVerdict.REJECTS,
            detail=_one_line(str(exc)),
            keys=refused_keys(exc),
        )
    return ArmBlockAcceptance(role=role, sha=sha, verdict=ArmBlockVerdict.ACCEPTS)


def refused_keys(exc: ValueError) -> tuple[str, ...]:
    """The dotted keys a pydantic ``ValidationError`` blames; ``()`` for anything else.

    Duck-typed on ``errors()`` so the probe never imports pydantic: a plain
    ``ValueError`` names no key, and the detail then carries the whole message.

    Example:
        >>> refused_keys(ValueError("no keys here"))
        ()
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return ()
    return tuple(".".join(str(part) for part in error.get("loc", ())) for error in errors())


def _one_line(detail: str) -> str:
    """Pydantic reports over several lines; a plan line and an error read as one."""
    return " ".join(detail.split())


def run_child(argv: Sequence[str] | None = None) -> int:
    """The child's whole job: prove the product, print one verdict as JSON."""
    args = _child_arguments(argv)
    try:
        assert_product_under(args.expect_product_under)
    except MeasurementPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR
    block = json.loads(args.block.read_text(encoding="utf-8"))
    acceptance = verdict_here(block, role=args.role, sha=args.commit)
    sys.stdout.write(acceptance.to_json())
    return _EXIT_OK


def _child_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    """INTERNAL argv: the parent builds it in :func:`child_command`."""
    parser = argparse.ArgumentParser(
        prog=f"python -m {CHILD_MODULE}",
        description="INTERNAL: report whether THIS product accepts one ask_your_docs.llm block",
    )
    parser.add_argument("--block", type=Path, required=True, help="the block, as JSON")
    parser.add_argument("--role", required=True, help="baseline or candidate")
    parser.add_argument("--commit", required=True, help="the commit this product is")
    parser.add_argument(
        "--expect-product-under",
        type=Path,
        required=True,
        help="refuse to answer unless the imported pydocs_mcp lives under this directory",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover — the child's entry point
    raise SystemExit(run_child())
