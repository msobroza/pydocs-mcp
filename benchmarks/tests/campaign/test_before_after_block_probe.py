"""campaign/before-after — each arm's OWN product decides whether the block is valid.

The failure these tests pin: ``--llm-block`` is validated once, against the
product of the checkout the command runs from — the candidate's. On 2026-09-15
the block carried ``parallel_tool_calls``, a key the candidate had and the
baseline (``440973eb``) did not, and the baseline's ``AskYourDocsRunnerSettings``
forbids extras. The plan passed, the run started, and every baseline rollout
raised at runner-build time: 54 attempts booked at the plan's assumed cost, zero
answered tasks, while the candidate arm spent real money at the endpoint.

So the plan now asks BOTH products first. Nothing here spends and nothing here
reaches an endpoint: the plan-side tests inject ``FakeArmBlockProbe`` and the
child-side tests build a runner object that is thrown away unused.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import (
    CommitUnderTest,
    CostModel,
    MeasurementPlanError,
    build_plan,
    render_plan,
)
from pydocs_eval.campaign.before_after_block_probe import (
    CHILD_MODULE,
    ArmBlockAcceptance,
    ArmBlockVerdict,
    acceptance_from_json,
    child_command,
    probe_arm_block,
    refused_keys,
    run_child,
    verdict_here,
)
from pydocs_eval.campaign.before_after_corpora import TaskWorkspaces
from pydocs_eval.campaign.before_after_llm_block import load_arm_llm_block
from pydocs_eval.campaign.before_after_product import arm_environment

from ._fakes import (
    REJECTED_KEY,
    FakeArmBlockProbe,
    git_repo_with_two_descriptions,
    init_git_repo,
    run_git,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LLM_BLOCK = _REPO_ROOT / "benchmarks" / "configs" / "ask_openrouter_qwen3_8_27b_llm.yaml"

# The child builds the real ask runner, which needs the harness extra installed.
_needs_ask_extra = pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="the ask harness extra is not installed, so no product runner can be built",
)


# --- the plan refuses a block one arm does not know ------------------------


def test_a_block_key_one_arm_rejects_is_refused_at_plan_time(tmp_path: Path) -> None:
    """The plan must name the arm, the commit, the key and the fix — before spending."""
    repo = git_repo_with_two_descriptions(tmp_path)
    probe = FakeArmBlockProbe(reject_role="baseline")

    with pytest.raises(MeasurementPlanError) as refused:
        _plan_pinning_the_shipped_block(repo, tmp_path, probe)

    message = str(refused.value)
    assert "baseline" in message and REJECTED_KEY in message
    assert str(_LLM_BLOCK) in message
    assert "remove the key from the block, or pick a baseline that knows it" in message


def test_a_block_both_arms_accept_prints_one_line_per_arm(tmp_path: Path) -> None:
    """An operator sees each arm's own verdict under the block it judges."""
    repo = git_repo_with_two_descriptions(tmp_path)

    plan = _plan_pinning_the_shipped_block(repo, tmp_path, FakeArmBlockProbe())

    printed = render_plan(plan)
    assert f"{plan.baseline.sha[:12]}: accepts the block" in printed
    assert f"{plan.candidate.sha[:12]}: accepts the block" in printed
    assert printed.index("llm block:") < printed.index("accepts the block")


def test_every_arm_is_asked_about_the_byte_identical_block(tmp_path: Path) -> None:
    """Two arms asked about two different blocks would measure two experiments."""
    repo = git_repo_with_two_descriptions(tmp_path)
    probe = FakeArmBlockProbe()

    plan = _plan_pinning_the_shipped_block(repo, tmp_path, probe)

    assert [role for role, _sha, _block in probe.asked] == ["baseline", "candidate"]
    assert [sha for _role, sha, _block in probe.asked] == [plan.baseline.sha, plan.candidate.sha]
    assert probe.asked[0][2] == probe.asked[1][2] == dict(plan.llm_block.settings)


def test_a_plan_that_pins_no_block_asks_no_arm(tmp_path: Path) -> None:
    """Nothing to validate, nothing to check out: the free plan stays free."""
    repo = git_repo_with_two_descriptions(tmp_path)
    probe = FakeArmBlockProbe()

    plan = _build_plan(repo, tmp_path, probe_block=probe)

    assert probe.asked == []
    assert plan.arm_blocks == ()


def test_a_pinned_block_with_no_probe_wired_is_refused_loudly(tmp_path: Path) -> None:
    """The default must never mean "unchecked" — it means "nobody wired the probe"."""
    repo = git_repo_with_two_descriptions(tmp_path)

    with pytest.raises(MeasurementPlanError, match="no arm probe was injected"):
        _build_plan(repo, tmp_path, llm_block=load_arm_llm_block(_LLM_BLOCK))


# --- the child: THIS product's own verdict ---------------------------------


@_needs_ask_extra
def test_this_product_rejects_a_block_key_it_does_not_know() -> None:
    """The settings model forbids extras — which is exactly what an old arm does."""
    acceptance = verdict_here({"nonsense_knob": 1}, role="baseline", sha="a" * 40)

    assert acceptance.rejected
    assert acceptance.keys == ("harness.llm.nonsense_knob",)
    assert "Extra inputs are not permitted" in acceptance.detail
    # One line: the refusal has to read as one sentence in a plan and a log.
    assert "\n" not in acceptance.detail


@_needs_ask_extra
def test_this_product_accepts_the_shipped_block_file() -> None:
    """The block the recorded run sends must validate against the product it ships with."""
    acceptance = verdict_here(load_arm_llm_block(_LLM_BLOCK).settings, role="candidate", sha="b")

    assert acceptance.verdict is ArmBlockVerdict.ACCEPTS
    assert acceptance.keys == ()


@_needs_ask_extra
def test_the_child_prints_exactly_one_json_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The child's whole contract: assert the product, print one verdict, exit 0."""
    block_file = tmp_path / "block.json"
    block_file.write_text(json.dumps({"nonsense_knob": 1}), encoding="utf-8")

    code = run_child(_child_argv(block_file, product=_installed_product_root(), sha="c" * 40))

    assert code == 0
    acceptance = acceptance_from_json(capsys.readouterr().out)
    assert acceptance.rejected and acceptance.role == "baseline"


def test_the_child_refuses_a_product_the_path_did_not_switch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A shadowing install would answer for the WRONG commit, so it answers not at all."""
    block_file = tmp_path / "block.json"
    block_file.write_text("{}", encoding="utf-8")

    code = run_child(_child_argv(block_file, product=tmp_path / "not-the-product", sha="d"))

    assert code == 2
    assert "shadowing" in capsys.readouterr().err


def test_refused_keys_names_nothing_when_the_error_is_not_pydantics() -> None:
    """A plain ValueError blames no key; the detail then carries the whole message."""
    assert refused_keys(ValueError("no keys here")) == ()


# --- parent and child, wired ----------------------------------------------


def test_the_child_runs_under_the_arms_own_product(tmp_path: Path) -> None:
    """The probe's whole point: ask THIS commit, with its worktree first on the path."""
    worktree = tmp_path / "src-abcdef"
    command = child_command(
        worktree, commit=_commit("baseline", "e" * 40), block_file=tmp_path / "b"
    )

    assert command[1:3] == ["-m", CHILD_MODULE]
    assert command[command.index("--expect-product-under") + 1] == str(worktree)
    assert command[command.index("--commit") + 1] == "e" * 40
    path = arm_environment(worktree)["PYTHONPATH"].split(os.pathsep)
    assert path[0] == str(worktree / "python")


def test_an_acceptance_round_trips_through_the_childs_json() -> None:
    """Parent and child agree on one wire, so a verdict survives the process boundary."""
    acceptance = ArmBlockAcceptance(
        role="baseline",
        sha="f" * 40,
        verdict=ArmBlockVerdict.REJECTS,
        detail="Extra inputs are not permitted",
        keys=(REJECTED_KEY,),
    )

    assert acceptance_from_json(acceptance.to_json()) == acceptance


def test_a_child_that_prints_something_else_is_a_plan_error() -> None:
    """A verdict that cannot be read is not a "no" — it is a broken probe."""
    with pytest.raises(MeasurementPlanError, match="expected one JSON verdict"):
        acceptance_from_json("Traceback (most recent call last):")


def test_a_worktree_that_cannot_answer_is_a_plan_error_not_a_verdict(tmp_path: Path) -> None:
    """A commit whose product cannot even be imported must not read as acceptance."""
    repo = _git_repo_with_a_stub_product(tmp_path)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    with pytest.raises(MeasurementPlanError, match="probe child exited"):
        probe_arm_block(repo, _commit("baseline", head), {"base_url": "http://endpoint/v1"})


# --- helpers ---------------------------------------------------------------


def _commit(role: str, sha: str) -> CommitUnderTest:
    return CommitUnderTest(role=role, sha=sha, subject="a commit", description_tokens=0)


def _child_argv(block_file: Path, *, product: Path, sha: str) -> list[str]:
    return [
        "--block",
        str(block_file),
        "--role",
        "baseline",
        "--commit",
        sha,
        "--expect-product-under",
        str(product),
    ]


def _installed_product_root() -> Path:
    """The directory this interpreter's ``pydocs_mcp`` lives under."""
    import pydocs_mcp

    return Path(pydocs_mcp.__file__).resolve().parents[1]


def _plan_pinning_the_shipped_block(repo: Path, tmp_path: Path, probe: FakeArmBlockProbe):
    return _build_plan(repo, tmp_path, llm_block=load_arm_llm_block(_LLM_BLOCK), probe_block=probe)


def _build_plan(repo: Path, tmp_path: Path, **overrides: object):
    """The plan, with a tokenizer and a corpus layout that need nothing installed."""
    return build_plan(
        repo=repo,
        baseline_ref="HEAD~1",
        candidate_ref="HEAD",
        split_spec="repoqa-qa/dev",
        task_ids=("t1",),
        model="qwen/qwen3.8-27b",
        endpoint="http://endpoint/v1",
        workspace=tmp_path,
        max_agent_turns=3,
        cost=CostModel(),
        count_tokens=len,
        task_workspaces=TaskWorkspaces(root=tmp_path, shared_workspace=tmp_path),
        **overrides,  # type: ignore[arg-type]
    )


def _git_repo_with_a_stub_product(tmp_path: Path) -> Path:
    """A repo whose ``python/pydocs_mcp`` is an empty package — importable, useless.

    Enough for the child's product assertion to pass and for building the arm's
    runner under it to fail, which is the "the probe could not answer" path.
    """
    repo = tmp_path / "stub-repo"
    package = repo / "python" / "pydocs_mcp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    init_git_repo(repo)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "a stub product")
    return repo
