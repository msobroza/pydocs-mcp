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

**Every assumption is named and printed.** Nothing here is measured — the
estimate is what the operator approves BEFORE any spend, and the run books it
against the ceiling, so a wrong assumption must be visible, never buried.

Afterwards the report says what the run actually spent, priced with the same
``--usd-per-1m-*`` rates as this estimate (``CostModel.usd`` and the report's
measured dollars both call ``priced_usd``). A price the ENDPOINT quotes is
reported beside it when it quotes one; most endpoints this harness talks to
quote nothing, which is why the estimate remains the signal the gate trusts.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_eval.campaign.before_after_corpora import TaskWorkspaces
from pydocs_eval.trajectory.token_accounting import priced_usd

if TYPE_CHECKING:  # the probe module imports this one; only its NAME is needed here
    from pydocs_eval.campaign.before_after_block_probe import ArmBlockAcceptance

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

# How a plan line names a commit. One source: the arm lines and the per-arm
# block verdicts under them must abbreviate the same commit the same way.
SHORT_SHA_CHARS = 12

# Cost-model defaults. Each is an ASSUMPTION the plan prints; none is measured.
_DEFAULT_CALLS_PER_TURN = 2.0  # the parallel design invites small bursts
_DEFAULT_CONTEXT_TOKENS_PER_TURN = 4000  # conversation + tool results per turn
_DEFAULT_OUTPUT_TOKENS_PER_TURN = 400

# The metric block the report carries, in the order it prints them. Named here
# so the plan can promise exactly what the report delivers; it mirrors
# ``before_after_rows.REPORT_ROWS`` and the two move together.
REPORTED_METRICS: tuple[str, ...] = (
    "needless_call_rate (+ resurfacing, zero_yield, fan_out_where_batch, tool_mismatch)",
    "pointer_followed_rate",
    "parallel_calls_per_turn",
    "batch_vs_fanout_ratio",
    "gold_reached_rate",
    "tool_calls_to_first_gold",
    "trajectory (union) recall@1/5/10 over every reformulation",
    "best and first search call recall@1/5/10 + mrr",
    "search_calls, reformulations",
    "tool_calls_total, distinct_tools_used, tool_calls_used, used/total ratio",
    "tokens in / out / reasoning / cached (arm total and per-task mean with CI)",
    "estimated_usd (the price flags on measured tokens) and reported_usd (the endpoint's own)",
    "description_tokens",
)

# How the report compares the two arms — promised in the plan, because a number
# without its uncertainty cannot answer the question the run is paying for.
REPORTED_STATISTICS = (
    "each arm's mean with a 95% bootstrap CI, plus the PAIRED delta with its "
    "bootstrap CI and a one-sided p (Wilcoxon signed-rank; McNemar exact for "
    "the gold-reached rate), paired by task id"
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
        """Dollars for a token count; ``0.0`` when no price was supplied.

        Delegates so the plan's ESTIMATE and the report's MEASURED cost price a
        token identically — a second copy of the per-million arithmetic is how
        the two figures would start disagreeing.
        """
        return priced_usd(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd_per_1m_input=self.usd_per_1m_input,
            usd_per_1m_output=self.usd_per_1m_output,
        )


@dataclass(frozen=True, slots=True)
class ArmLlmBlock:
    """The ``ask_your_docs.llm`` block BOTH arms pin, and the file it came from.

    Model settings are ARM-side by contract: the eval binding refuses a block
    sourced from the serving file (so an arm stays deterministic), which is why
    ``--llm-block`` exists and why the serving YAML carries no ``llm:`` block.
    Both arms receive this one byte-identically — only the product commit differs.
    """

    source: str
    settings: Mapping[str, object]

    def plan_lines(self) -> list[str]:
        """The block as the plan prints it: one sorted dotted key per line."""
        return [
            f"llm block:  {self.source} (arm-side, byte-identical for both arms)",
            *(
                f"            {key}: {_yaml_scalar(value)}"
                for key, value in flat_settings(self.settings)
            ),
        ]


def _yaml_scalar(value: object) -> str:
    """Echo a block value in the operator's own vocabulary: ``null``, not ``None``."""
    return "null" if value is None else str(value)


def flat_settings(settings: Mapping[str, object]) -> Iterator[tuple[str, object]]:
    """Flatten a nested block into sorted ``dotted.key, value`` pairs.

    Printable and greppable: ``auth.api_key_env`` names an ENVIRONMENT VARIABLE,
    never a credential, so the whole block is safe to put in a plan and a log.

    Example:
        >>> list(flat_settings({"params": {"top_p": 0.95}}))
        [('params.top_p', 0.95)]
    """
    for key, value in sorted(settings.items()):
        if isinstance(value, Mapping):
            yield from ((f"{key}.{leaf}", item) for leaf, item in flat_settings(value))
        else:
            yield key, value


@dataclass(frozen=True, slots=True)
class CommitUnderTest:
    """One arm's commit, with the size of the description surface it ships."""

    role: str
    sha: str
    subject: str
    description_tokens: int


# One arm's commit and the block, in; THAT product's verdict on it, out. The
# real probe (``before_after_block_probe.probe_arm_block``) checks the commit
# out and asks its own settings model; injected like ``count_tokens`` so the
# free plan stays testable without git, a child process or a product install.
# The probe module imports THIS one, so the value object it returns is named
# here as a forward reference and the real probe is wired by the command — the
# composition root — rather than defaulted in.
ArmBlockProbe = Callable[[Path, "CommitUnderTest", Mapping[str, object]], "ArmBlockAcceptance"]


def refuse_an_unprobed_block(
    repo: Path, commit: CommitUnderTest, block: Mapping[str, object]
) -> ArmBlockAcceptance:
    """The default probe: loud, never silent. A pinned block MUST be checked.

    Null-object shaped (the product's ``NullTreeService`` precedent): a caller
    that pins no block never reaches it, and one that pins a block without
    wiring the real probe gets a named failure instead of an unchecked run.
    """
    raise MeasurementPlanError(
        f"--llm-block was pinned for {commit.role} {commit.sha[:SHORT_SHA_CHARS]} in "
        f"{repo}, carrying {sorted(block)}, but no arm probe was injected; "
        "build_plan(probe_block=...) takes before_after_block_probe.probe_arm_block"
    )


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
    task_workspaces: TaskWorkspaces
    # None = no --llm-block was given, so the arms send whatever the model defaults to.
    llm_block: ArmLlmBlock | None = None
    # One acceptance per arm when a block IS pinned: both products said yes.
    arm_blocks: tuple[ArmBlockAcceptance, ...] = ()

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
            f"reported as: {REPORTED_STATISTICS}",
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
        *_workspace_lines(plan),
        f"turns:      {plan.max_agent_turns} agent turn(s) per task (the harness budget)",
        *(plan.llm_block.plan_lines() if plan.llm_block is not None else []),
        *(acceptance.plan_line() for acceptance in plan.arm_blocks),
    ]


def _workspace_lines(plan: MeasurementPlan) -> list[str]:
    """What ``--workspace`` means for this split, and what each task will search.

    A task that names a corpus searches a bundle built for THAT corpus under the
    ``--workspace`` directory; a task that names none searches ``--workspace``
    itself, which is what every task did before per-corpus workspaces existed.
    """
    shared = len(plan.task_workspaces.shared_task_ids)
    return [
        f"workspace:  {plan.workspace} — the root the per-corpus workspaces are built "
        f"under; searched directly by the {shared} task(s) that name no corpus",
        *plan.task_workspaces.preflight_lines(),
    ]


def _commit_line(commit: CommitUnderTest) -> str:
    short = commit.sha[:SHORT_SHA_CHARS]
    return f"{short}  {commit.subject}  ({commit.description_tokens} description tokens)"


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
    task_workspaces: TaskWorkspaces,
    llm_block: ArmLlmBlock | None = None,
    probe_block: ArmBlockProbe = refuse_an_unprobed_block,
) -> MeasurementPlan:
    """Assemble the plan from resolved inputs (no dataset or network access here).

    ``count_tokens`` is injected so the plan stays testable without a tokenizer
    and so the caller decides which encoding the description counts use.
    ``probe_block`` is injected the same way, and for the same reason: asking
    each arm's product about the block means a worktree and a child process.

    Raises:
        MeasurementPlanError: either arm's product rejects the pinned block.
    """
    baseline = _commit_under_test(repo, "baseline", baseline_ref, count_tokens)
    candidate = _commit_under_test(repo, "candidate", candidate_ref, count_tokens)
    return MeasurementPlan(
        split=split_spec,
        task_ids=tuple(task_ids),
        baseline=baseline,
        candidate=candidate,
        model=model,
        endpoint=endpoint,
        workspace=workspace,
        max_agent_turns=max_agent_turns,
        cost=cost,
        task_workspaces=task_workspaces,
        llm_block=llm_block,
        arm_blocks=_accepted_by_both_arms(repo, (baseline, candidate), llm_block, probe_block),
    )


def _accepted_by_both_arms(
    repo: Path,
    commits: tuple[CommitUnderTest, ...],
    llm_block: ArmLlmBlock | None,
    probe_block: ArmBlockProbe,
) -> tuple[ArmBlockAcceptance, ...]:
    """Ask EACH arm's own product about the pinned block; refuse the plan on a no.

    WHY per arm and not once: ``load_arm_llm_block`` validates the block against
    the product of the checkout the command runs from — the CANDIDATE's. A key
    that commit added passes there and is then an extra the baseline's settings
    model forbids, so the baseline arm raises at runner-build time on every
    rollout, books each raise at the plan's assumed cost and halts at zero
    answered tasks — while the candidate arm spends real money beside it.
    """
    if llm_block is None:
        return ()
    accepted: list[ArmBlockAcceptance] = []
    for commit in commits:
        acceptance = probe_block(repo, commit, llm_block.settings)
        if acceptance.rejected:
            raise MeasurementPlanError(acceptance.refusal(llm_block.source))
        accepted.append(acceptance)
    return tuple(accepted)


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
