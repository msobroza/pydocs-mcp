"""The before/after measurement plan: what two arms would run, and what it costs.

One run of the before/after command answers one question — did a change to the
server's responses make the agent's calls more needed? — by driving the SAME
dataset split through the SAME ask-your-docs harness twice, changing only the
commit of the product the serve child imports. This module owns everything that
happens BEFORE any spend:

- resolving the two commits and reading each one's tool-description document,
- loading the split and counting its tasks,
- reading the endpoint, model and turn budget the run will use,
- turning all of that into an explicit, printable estimate.

The plan is what ``--confirm-spend`` gates. Printing it is free and offline
apart from reading the dataset's own cache, so an operator always sees the task
count, the two commits, the endpoint and the estimate before deciding.

**Every assumption is named and printed.** The in-process ask harness does not
observe spend (its endpoint is often a local or internal server), so no dollar
figure can be read back from a run: the estimate here is the only cost signal,
and the run books it against the ceiling. A wrong assumption must therefore be
visible, never buried.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# One text in, its token count out — the product's tokenizer in a real run, a
# stub in tests, so the plan never has to import a tokenizer itself.
TokenCounter = Callable[[str], int]

# The descriptions document whose size the report compares across commits. It
# is read out of each commit with ``git show``, so neither version has to be
# imported — two copies of one package cannot live in one interpreter.
DESCRIPTIONS_PATH = "python/pydocs_mcp/defaults/descriptions.md"

# A task-name split selector covering SEVERAL registered datasets. ``repo_qa``
# is the product-enumerated framing two corpora mint under (see
# ``datasets/repo_qa.py``), so naming it selects both; any other selector is
# read as a registered dataset name.
TASK_NAME_DATASETS: Mapping[str, tuple[str, ...]] = {"repo_qa": ("repoqa-qa", "swe-qa-questions")}

# Cost-model defaults. Each is an ASSUMPTION the plan prints; none is measured.
_DEFAULT_CALLS_PER_TURN = 2.0  # the parallel design invites small bursts
_DEFAULT_CONTEXT_TOKENS_PER_TURN = 4000  # conversation + tool results per turn
_DEFAULT_OUTPUT_TOKENS_PER_TURN = 400
_TOKENS_PER_PRICED_UNIT = 1_000_000

# The metric block the report carries, in the order it prints them. Named here
# so the plan can promise exactly what the report delivers.
REPORTED_METRICS: tuple[str, ...] = (
    "needless_call_rate (+ resurfacing, zero_yield, fan_out_where_batch, tool_mismatch)",
    "pointer_followed_rate",
    "parallel_calls_per_turn",
    "batch_vs_fanout_ratio",
    "tool_calls_to_first_gold",
    "description_tokens",
)


class MeasurementPlanError(Exception):
    """A plan input the operator must fix (a bad ref, split or config)."""


@dataclass(frozen=True, slots=True)
class CostModel:
    """The priced assumptions behind the estimate — never measured values."""

    calls_per_turn: float = _DEFAULT_CALLS_PER_TURN
    context_tokens_per_turn: int = _DEFAULT_CONTEXT_TOKENS_PER_TURN
    output_tokens_per_turn: int = _DEFAULT_OUTPUT_TOKENS_PER_TURN
    usd_per_1m_input: float = 0.0
    usd_per_1m_output: float = 0.0

    def usd(self, *, input_tokens: int, output_tokens: int) -> float:
        """Dollars for a token count; ``0.0`` when no price was supplied."""
        priced = input_tokens * self.usd_per_1m_input + output_tokens * self.usd_per_1m_output
        return priced / _TOKENS_PER_PRICED_UNIT


@dataclass(frozen=True, slots=True)
class CommitUnderTest:
    """One arm's commit, with the size of the description surface it ships."""

    role: str
    sha: str
    subject: str
    description_tokens: int


@dataclass(frozen=True, slots=True)
class MeasurementPlan:
    """Everything the run will do, and what it is estimated to cost."""

    split: str
    task_ids: tuple[str, ...]
    baseline: CommitUnderTest
    candidate: CommitUnderTest
    model: str
    endpoint: str
    workspace: Path
    max_agent_turns: int
    cost: CostModel

    @property
    def rollouts(self) -> int:
        """One rollout per task per arm — both arms run every task."""
        return len(self.task_ids) * 2

    @property
    def model_turns(self) -> int:
        """Upper bound: every rollout spends its whole turn budget."""
        return self.rollouts * self.max_agent_turns

    @property
    def tool_calls(self) -> int:
        """Estimated tool calls: every turn but the answering one calls tools."""
        per_rollout = (self.max_agent_turns - 1) * self.cost.calls_per_turn
        return int(self.rollouts * per_rollout)

    @property
    def input_tokens(self) -> int:
        """Both commits' description surfaces ride every turn of their own arm."""
        descriptions = self.baseline.description_tokens + self.candidate.description_tokens
        per_arm_turns = self.model_turns // 2
        context = self.model_turns * self.cost.context_tokens_per_turn
        return context + per_arm_turns * descriptions

    @property
    def output_tokens(self) -> int:
        return self.model_turns * self.cost.output_tokens_per_turn

    @property
    def estimated_usd(self) -> float:
        return self.cost.usd(input_tokens=self.input_tokens, output_tokens=self.output_tokens)

    @property
    def estimated_usd_per_rollout(self) -> float:
        """What the run books per rollout against the ceiling (see the runner)."""
        return self.estimated_usd / self.rollouts if self.rollouts else 0.0


def resolve_commit(repo: Path, role: str, ref: str) -> tuple[str, str]:
    """``(full sha, subject line)`` for ``ref``; a typed error when it resolves to nothing."""
    sha = _git(repo, "rev-parse", f"{ref}^{{commit}}", what=f"{role} commit {ref!r}")
    subject = _git(repo, "log", "-1", "--format=%s", sha, what=f"{role} subject of {sha}")
    return sha, subject


def read_descriptions(repo: Path, sha: str) -> str:
    """The tool-description document as of ``sha`` (never the working tree's)."""
    return _git(
        repo,
        "show",
        f"{sha}:{DESCRIPTIONS_PATH}",
        what=f"{DESCRIPTIONS_PATH} at {sha}",
        strip=False,
    )


def _git(repo: Path, *args: str, what: str, strip: bool = True) -> str:
    """Run one read-only git command in ``repo``; map failure to a typed error."""
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise MeasurementPlanError(
            f"could not read {what}: git {' '.join(args)} failed in {repo} "
            f"({completed.stderr.strip() or 'no stderr'})"
        )
    return completed.stdout.strip() if strip else completed.stdout


def dataset_names_for(selector: str) -> tuple[str, ...]:
    """The registered datasets a ``--split`` selector names.

    A task-name selector (``repo_qa``) spans the framing's corpora; anything
    else is one registered dataset name.
    """
    return TASK_NAME_DATASETS.get(selector, (selector,))


def parse_split(spec: str) -> tuple[str, str]:
    """``"repo_qa/dev"`` → ``("repo_qa", "dev")``; a typed error otherwise."""
    selector, separator, split = spec.partition("/")
    if not separator or not selector or not split:
        raise MeasurementPlanError(
            f"--split must be <dataset-or-task-name>/<split>, got {spec!r} "
            "(for example repo_qa/dev or repoqa-qa/dev)"
        )
    return selector, split


def render_plan(plan: MeasurementPlan) -> str:
    """The plan-only output: what would run, and what it is estimated to cost."""
    return "\n".join(
        [
            "before/after measurement plan (NOTHING HAS BEEN SPENT)",
            "",
            *_plan_scope_lines(plan),
            "",
            *_plan_estimate_lines(plan),
            "",
            "metrics reported for both arms:",
            *(f"  - {name}" for name in REPORTED_METRICS),
            "",
            "re-run with --confirm-spend to execute both arms.",
        ]
    )


def _plan_scope_lines(plan: MeasurementPlan) -> list[str]:
    """What the run covers: split, arms, endpoint, budget."""
    return [
        f"split:      {plan.split} — {len(plan.task_ids)} task(s)",
        f"baseline:   {_commit_line(plan.baseline)}",
        f"candidate:  {_commit_line(plan.candidate)}",
        f"model:      {plan.model} @ {plan.endpoint}",
        f"workspace:  {plan.workspace}",
        f"turns:      {plan.max_agent_turns} agent turn(s) per task (the harness budget)",
    ]


def _commit_line(commit: CommitUnderTest) -> str:
    return f"{commit.sha[:12]}  {commit.subject}  ({commit.description_tokens} description tokens)"


def _plan_estimate_lines(plan: MeasurementPlan) -> list[str]:
    """The estimate and, beneath it, every assumption it rests on."""
    return [
        f"estimate:   {plan.rollouts} rollout(s), up to {plan.model_turns} model turn(s), "
        f"~{plan.tool_calls} tool call(s)",
        f"            ~{plan.input_tokens} input + ~{plan.output_tokens} output tokens",
        f"            ~${plan.estimated_usd:.2f}{_price_note(plan.cost)}",
        "assumptions (none of these is measured):",
        f"  - every rollout spends its full {plan.max_agent_turns}-turn budget",
        f"  - {plan.cost.calls_per_turn} tool call(s) per tool-calling turn",
        f"  - {plan.cost.context_tokens_per_turn} context tokens per turn, plus that "
        "arm's description surface",
        f"  - {plan.cost.output_tokens_per_turn} output tokens per turn",
    ]


def _price_note(cost: CostModel) -> str:
    """Say so when the dollar figure is zero because no price was supplied."""
    if cost.usd_per_1m_input or cost.usd_per_1m_output:
        return f" at ${cost.usd_per_1m_input}/1M input, ${cost.usd_per_1m_output}/1M output"
    return " — no price given; pass --usd-per-1m-input / --usd-per-1m-output"


def build_plan(
    *,
    repo: Path,
    baseline_ref: str,
    candidate_ref: str,
    split_spec: str,
    task_ids: Sequence[str],
    model: str,
    endpoint: str,
    workspace: Path,
    max_agent_turns: int,
    cost: CostModel,
    count_tokens: TokenCounter,
) -> MeasurementPlan:
    """Assemble the plan from resolved inputs (no dataset or network access here).

    ``count_tokens`` is injected so the plan stays testable without a tokenizer
    and so the caller decides which encoding the description counts use.
    """
    return MeasurementPlan(
        split=split_spec,
        task_ids=tuple(task_ids),
        baseline=_commit_under_test(repo, "baseline", baseline_ref, count_tokens),
        candidate=_commit_under_test(repo, "candidate", candidate_ref, count_tokens),
        model=model,
        endpoint=endpoint,
        workspace=workspace,
        max_agent_turns=max_agent_turns,
        cost=cost,
    )


def _commit_under_test(
    repo: Path, role: str, ref: str, count_tokens: TokenCounter
) -> CommitUnderTest:
    sha, subject = resolve_commit(repo, role, ref)
    return CommitUnderTest(
        role=role,
        sha=sha,
        subject=subject,
        description_tokens=count_tokens(read_descriptions(repo, sha)),
    )
