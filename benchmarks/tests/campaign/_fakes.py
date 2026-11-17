"""Named doubles and throwaway fixtures shared by the before/after campaign tests.

Mirrors the product suite's ``tests/_fakes.py``: a test double lives here, under
its own name, instead of being monkeypatched together at each call site.

The per-arm block probe is the one seam these tests cannot exercise for real —
the live probe checks a commit out into a git worktree and runs a child process
under it. :class:`FakeArmBlockProbe` answers the same question in memory, so a
plan test stays offline and instant while still proving WHICH arms were asked.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest, MeasurementPlan
from pydocs_eval.campaign.before_after_arm import ArmSummary, read_arm_settings, write_arm_summary
from pydocs_eval.campaign.before_after_arm_variant import ArmVariant
from pydocs_eval.campaign.before_after_block_probe import ArmBlockAcceptance, ArmBlockVerdict
from pydocs_eval.campaign.before_after_corpora import IndexIdentity
from pydocs_eval.campaign.before_after_serving import ServingBasis, ServingSettings
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.datasets.repo_qa import GOLD_SYMBOL_KEY
from pydocs_eval.trajectory.server_capture import SERVER_EVENTS_FILENAME
from pydocs_eval.trajectory.token_accounting import ASK_MODEL_USAGE_FILENAME

# The key that cost the 2026-09-15 run its baseline arm: the candidate knew it,
# the baseline predated it, and the block was validated against the candidate.
REJECTED_KEY = "harness.llm.parallel_tool_calls"

# What the ``stub_command`` plan is served under: a serving config the CLI tests
# never read, so its digest is a stand-in, and the endpoint and index it names.
# The endpoint rides as ``--base-url`` would, so every arm resolves the same one.
STUB_SERVING = ServingBasis(
    config_digest="stub-serving-config",
    cli_url="http://e",
    file_llm=None,
    index=IndexIdentity(embedder_model="m", embedder_dim=8, scope_id="scope"),
)


def stub_serving_settings(max_agent_turns: int) -> ServingSettings:
    """``_serving_settings``' answer for :data:`STUB_SERVING`, at a turn cap."""
    return ServingSettings(endpoint="http://e", max_agent_turns=max_agent_turns, basis=STUB_SERVING)


@contextlib.contextmanager
def no_worktree(repo: Path, sha: str, root: Path) -> Iterator[Path]:
    """``product_worktree`` without git — the arm child is stubbed out anyway."""
    yield root / sha[:12]


def write_finished_summary_from_settings(
    worktree: Path, *, arm_dir: Path, split: str, limit: int | None
) -> None:
    """Stand in for the arm child: the finished ``arm.json`` it writes from its settings,
    through the real writer, with both plan tasks excluded (no trace to measure)."""
    settings = read_arm_settings(arm_dir)
    summary = ArmSummary(
        role=settings.role,
        commit=settings.commit,
        model=settings.model,
        trace_root=settings.trace_root,
        tasks=[],
        estimated_usd=0.0,
        halt_reason="completed",
        excluded=2,
        max_agent_turns=settings.max_agent_turns,
        **settings.summary_variant_fields(),  # type: ignore[arg-type]
    )
    write_arm_summary(arm_dir, summary)


@dataclass
class FakeArmBlockProbe:
    """One arm's product on the block, without a worktree or a child process.

    Every arm accepts unless ``reject_role`` names one — which is the real
    failure shape: both arms are handed the same block and only ONE of them
    refuses it. ``asked`` records the variant each arm was shown, so a test can
    prove the probe ran per arm and saw that arm's own block and overlay.
    """

    reject_role: str | None = None
    refused_keys: tuple[str, ...] = (REJECTED_KEY,)
    asked: list[tuple[str, str, ArmVariant]] = field(default_factory=list)

    def __call__(
        self, repo: Path, commit: CommitUnderTest, variant: ArmVariant
    ) -> ArmBlockAcceptance:
        self.asked.append((commit.role, commit.sha, variant))
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


@dataclass
class FakeArmRun:
    """Stands in for one arm's whole child process; records that it was asked.

    Shared by every test that drives the ``before-after`` CLI: what those tests
    assert is WHICH arms the command decided to run, so the arm itself — a git
    checkout plus a child process plus an endpoint — is replaced wholesale.
    """

    roles: list[str] = field(default_factory=list)

    def __call__(self, args: object, plan: MeasurementPlan, role: str) -> ArmSummary:
        self.roles.append(role)
        return ArmSummary(
            role=role,
            commit=(plan.baseline if role == "baseline" else plan.candidate).sha,
            model=plan.model,
            trace_root="",
            tasks=[],
            estimated_usd=0.0,
            halt_reason="completed",
            excluded=0,
        )


@dataclass(frozen=True, slots=True)
class RecordedTrajectory:
    """What the product harness hands an arm: a finished run whose trace is ON DISK.

    ``budget_exhausted`` / ``timed_out`` are the flags issue #371 adds to the
    product ``Trajectory``; a test standing in for an older product leaves them
    at their defaults, which is exactly how an old product reads.
    """

    trajectory_id: str
    trace_dir: Path
    answer: str
    turns: int
    wall_seconds: float = 1.5
    tool_call_count: int = 1
    budget_exhausted: bool = False
    timed_out: bool = False

    def server_tool_calls(self) -> tuple[str, ...]:
        """The server-observed slice; only its LENGTH is read by an arm."""
        return tuple(f"call-{index}" for index in range(self.tool_call_count))


def recorded_trajectory(
    trace_root: Path,
    *,
    answer: str,
    turns: int,
    last_finish_reason: str | None = None,
    **flags: bool,
) -> RecordedTrajectory:
    """One real product trace under ``trace_root``, and the run that produced it.

    ``last_finish_reason`` writes a usage sidecar in issue #371's shape — the one
    carrying each reply's ``finish_reason`` — so starvation can be booked.
    """
    from tests.trajectory._ask_traces import write_ask_trajectory

    trace_dir = write_ask_trajectory(trace_root, calls=[("search_codebase", {"query": "q"}, 1)])
    if last_finish_reason is not None:
        write_usage_with_finish_reason(trace_dir, last_finish_reason)
    return RecordedTrajectory(
        trajectory_id=trace_dir.name, trace_dir=trace_dir, answer=answer, turns=turns, **flags
    )


def run_with_trace_file(
    trace_root: Path, task_id: str, *, answer: str = "an answer", turns: int = 2
) -> RecordedTrajectory:
    """A run whose events file exists, for tests that only need it BOOKED.

    Synchronous on purpose: a harness fake answers from inside the arm's running
    event loop, where :func:`recorded_trajectory`'s recorder cannot be driven.
    """
    trace_dir = trace_root / f"traj-{task_id}"
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / SERVER_EVENTS_FILENAME).touch()
    return RecordedTrajectory(
        trajectory_id=trace_dir.name, trace_dir=trace_dir, answer=answer, turns=turns
    )


def write_usage_with_finish_reason(trace_dir: Path, finish_reason: str) -> None:
    """A one-reply usage sidecar whose record carries issue #371's ``finish_reason``."""
    record = {"turn": 1, "message_id": "m1", "input_tokens": 10, "output_tokens": 16384}
    payload = {"schema_version": 2, "messages": [{**record, "finish_reason": finish_reason}]}
    (trace_dir / ASK_MODEL_USAGE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


@dataclass
class ScriptedArmRunner:
    """The arm's harness runner, answering each task id with a scripted run.

    A scripted exception is raised instead of returned, and a task id missing
    from ``runs`` raises too, the way a dead serve child does; ``seen`` records
    every attempt, so a test can tell a run booked ONCE from one the campaign
    retried.
    """

    runs: Mapping[str, object]
    seen: list[str] = field(default_factory=list)

    async def run(self, sample: Mapping[str, object], guidance: Mapping[str, str]) -> object:
        record_id = str(sample["record_id"])
        self.seen.append(record_id)
        scripted = self.runs.get(record_id, RuntimeError(f"no scripted run for {record_id!r}"))
        if isinstance(scripted, BaseException):
            raise scripted
        return scripted


@dataclass
class FakeHarnessRunner:
    """One trajectory per sample, its trace file on disk; no agent and no endpoint.

    ``samples`` and ``guidance`` record what each rollout was handed, so a test
    can prove which tasks ran and what guidance reached the runner.
    """

    trace_root: Path
    samples: list[Mapping[str, object]] = field(default_factory=list)
    guidance: list[Mapping[str, str]] = field(default_factory=list)

    async def run(self, sample: Mapping[str, object], guidance: Mapping[str, str]) -> object:
        self.samples.append(dict(sample))
        self.guidance.append(dict(guidance))
        return run_with_trace_file(self.trace_root, str(sample["record_id"]))


@dataclass
class RecordingAskRunnerBuild:
    """Stands in for ``build_ask_harness_runner``: records each build's keyword arguments.

    What an arm's runner is BUILT from — its architecture, its llm block, its
    harness keys — is the whole question, so no runner is returned at all.
    """

    calls: list[dict[str, object]] = field(default_factory=list)

    def __call__(self, **settings: object) -> object:
        self.calls.append(settings)
        return object()


def eval_task(task_id: str, gold: tuple[str, ...] = ("a.py",), symbol: str = "") -> EvalTask:
    """One split task, with a gold file set and a corpus nothing reads.

    A ``symbol`` makes it a repoqa-qa needle: one file and the function it holds.
    """
    return EvalTask(
        task_id=task_id,
        query="where is the router?",
        gold=GoldAnswer(file_set=gold, extra={GOLD_SYMBOL_KEY: symbol} if symbol else {}),
        corpus_source=lambda: Path("/corpus"),
    )


#: The tasks every ``before-after-compare`` fixture arm answered.
COMPARE_TASK_IDS = tuple(f"t{index}" for index in range(10))


@dataclass
class FakeSplitTasks:
    """``load_split_tasks`` without a dataset: each task id, with the gold files it names.

    A test mutates ``gold_by_task`` to shape the split its arms answered (the
    ``before-after`` and compare verbs score stored answers against this gold),
    and ``symbol_by_task`` to give a task the function a repoqa-qa needle names.
    """

    gold_by_task: dict[str, tuple[str, ...]]
    symbol_by_task: dict[str, str] = field(default_factory=dict)
    #: The ``draw`` each load was asked for, in call order (None = the slice as-is).
    draws: list[object] = field(default_factory=list)

    async def __call__(
        self, split: str, *, limit: int | None = None, draw: object = None
    ) -> tuple[EvalTask, ...]:
        self.draws.append(draw)
        tasks = tuple(
            eval_task(task_id, gold, self.symbol_by_task.get(task_id, ""))
            for task_id, gold in self.gold_by_task.items()
        )
        return tasks[:limit] if limit else tasks


def before_after_argv(tmp_path: Path, repo: Path, *extra: str) -> list[str]:
    """The ``before-after`` argv a CLI test runs, minus whatever it is testing."""
    return [
        "before-after",
        "--baseline",
        "HEAD~1",
        "--candidate",
        "HEAD",
        "--config",
        str(tmp_path / "serve.yaml"),
        "--split",
        "repoqa-qa/dev",
        "--workspace",
        str(tmp_path / "ws"),
        "--model",
        "test-model",
        "--repo",
        str(repo),
        "--out",
        str(tmp_path / "out"),
        *extra,
    ]
