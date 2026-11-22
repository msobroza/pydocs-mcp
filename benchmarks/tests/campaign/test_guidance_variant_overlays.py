"""The guidance-task experiment's three variant overlays, each planned at $0 (issue #465).

One overlay per variant of the experiment (spec
``docs/superpowers/specs/2026-11-22-guidance-task-experiment-design.md``,
"Eval-side instruments ($0)" and the arm table under "The measurement"):

- (a) ``system_v4`` draft plus the candidate seed — decides the flip;
- (b) ``system_v3`` plus the candidate seed alone — diagnostic;
- (c) ``system_v3`` plus the placebo — diagnostic, length alone.

Each overlay holds the ``guidance_sections`` key and nothing else, so a variant
differs from the baseline only in the text it loads. What an operator depends
on: the header's own free plan command exits 0, prints each variant's digest
and diff, and starts no rollout.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

from pydocs_eval.campaign import before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_arm import ArmSettings, run_arm
from pydocs_eval.campaign.before_after_arm_launch import arm_settings_for
from pydocs_eval.campaign.before_after_arm_settings import (
    GUIDANCE_SECTIONS_KEY,
    ArmOverlay,
    load_arm_overlay,
)
from pydocs_eval.datasets.repo_qa import REPO_QA_TASK_NAME
from pydocs_eval.optimize.artifacts.ask_prompt import SYSTEM_PROMPT_KEY
from pydocs_eval.task_rendering import DEFAULT_TASK_SCAFFOLD

from ._fakes import FakeArmRun, FakeHarnessRunner, eval_task, git_repo_with_two_descriptions
from .test_before_after_reuse_baseline import write_finished_baseline

CaptureFixture = pytest.CaptureFixture[str]

_FLAG = "--candidate-settings"
_DIFF_HEADING = "diff (baseline -> candidate):"
_PLAN_MARKER = "# Plan (free"
_PLACEHOLDER = re.compile(r"<[^>]*>")
_PLACEHOLDER_TOKEN = "<placeholder>"
_COMMIT_FLAGS = ("--baseline", "--candidate")
_HEADER_PLAN_FLAGS = (
    *_COMMIT_FLAGS,
    "--candidate-settings",
    "--config",
    "--llm-block",
    "--split",
    "--workspace",
    "--model",
)
_PATH_FLAGS = ("--candidate-settings", "--config", "--llm-block")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VARIANT_DIR = _REPO_ROOT / "benchmarks" / "configs" / "guidance_task"
VARIANT_A = _VARIANT_DIR / "arm_variant_a_system_v4_draft.yaml"
VARIANT_B = _VARIANT_DIR / "arm_variant_b_candidate_seed.yaml"
VARIANT_C = _VARIANT_DIR / "arm_variant_c_placebo.yaml"
_VARIANTS = (VARIANT_A, VARIANT_B, VARIANT_C)
# The ladder's serving file and LLM block, reused unchanged (no copy is made).
_LADDER_SERVING = _REPO_ROOT / "benchmarks" / "configs" / "ask_openrouter_qwen3_4b.yaml"
_LADDER_LLM_BLOCK = _REPO_ROOT / "benchmarks" / "configs" / "ask_openrouter_qwen3_8_27b_llm.yaml"
# The keys an arm overlay could set but these must not: the variant is its text alone.
_REFUSED_KEYS = ("guidance_task", "architecture", "max_agent_turns", "llm", "task_scaffold")


def _overlay_yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --- the files ------------------------------------------------------------------


def test_exactly_three_overlays_exist_one_per_variant() -> None:
    assert sorted(_VARIANT_DIR.glob("*.yaml")) == sorted(_VARIANTS)


def test_each_overlays_only_key_is_guidance_sections() -> None:
    assert [list(_overlay_yaml(path)) for path in _VARIANTS] == [[GUIDANCE_SECTIONS_KEY]] * 3


# --- the overlay loader, at plan time -------------------------------------------


def _load_variant_overlay(path: Path) -> ArmOverlay:
    return load_arm_overlay(path, flag=_FLAG)


@pytest.mark.parametrize(
    ("path", "guidance_file"),
    [
        (VARIANT_A, "variant_a_system_v4_draft.md"),
        (VARIANT_B, "candidate_seed.md"),
        (VARIANT_C, "placebo_guidance.md"),
    ],
)
def test_each_overlay_loads_its_guidance_file_at_plan_time(path: Path, guidance_file: str) -> None:
    overlay = _load_variant_overlay(path)

    assert overlay.guidance_sections_path == str(_VARIANT_DIR / guidance_file)
    assert overlay.product == {}, "no product key: the variant is its text alone"
    assert overlay.task_scaffold is DEFAULT_TASK_SCAFFOLD


# --- the plan, through the verb's own entry point --------------------------------


def _header_plan_command(path: Path) -> dict[str, str]:
    """The free plan command the overlay's header writes out, as ``{flag: value}``.

    Strict on purpose: the command must be the verb plus exactly the flags of
    ``_HEADER_PLAN_FLAGS``, each with a value, so a dropped line continuation,
    an added flag or a bare switch fails here instead of planning something else.
    """
    header = path.read_text(encoding="utf-8").split(_PLAN_MARKER, 1)[1]
    lines = [line.removeprefix("#").strip() for line in header.splitlines()]
    start = next(i for i, line in enumerate(lines) if line.startswith("python -m "))
    command: list[str] = []
    for line in lines[start:]:
        command.append(line.removesuffix("\\").strip())
        if not line.endswith("\\"):
            break
    # A placeholder (`<experiment head sha>`) is one value, not three words.
    argv = shlex.split(_PLACEHOLDER.sub(_PLACEHOLDER_TOKEN, " ".join(command)))
    assert argv[:4] == ["python", "-m", "pydocs_eval.campaign", "before-after"], argv
    flags, values = argv[4::2], argv[5::2]
    assert sorted(flags) == sorted(_HEADER_PLAN_FLAGS), argv
    assert not any(value.startswith("--") for value in values), argv
    return dict(zip(flags, values, strict=True))


def _plan_argv_bound_to_fakes(path: Path, tmp_path: Path, repo: Path) -> list[str]:
    """The header's command with its placeholders and paths bound to the test's fakes."""
    bound: list[str] = []
    for flag, value in _header_plan_command(path).items():
        bound += [flag, _bound_value(flag, value, tmp_path)]
    return ["before-after", *bound, "--repo", str(repo), "--out", str(tmp_path / "out")]


def _bound_value(flag: str, value: str, tmp_path: Path) -> str:
    if value == _PLACEHOLDER_TOKEN:
        assert flag in _COMMIT_FLAGS, f"placeholder for {flag}: only the commits are left open"
        return "HEAD"
    if flag == "--workspace":
        return str(tmp_path / "ws")
    if flag in _PATH_FLAGS:
        return str(_REPO_ROOT / value)
    return value


def _plan(path: Path, tmp_path: Path, stub_command: FakeArmRun, capsys: CaptureFixture) -> str:
    """Run the header's command through ``main``; return the plan it printed."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(_plan_argv_bound_to_fakes(path, tmp_path, repo)) == 0

    assert stub_command.roles == [], "a plan starts no rollout"
    return capsys.readouterr().out


def _digests(printed: str) -> tuple[str, str]:
    """The (baseline, candidate) settings digests the plan printed."""
    found = re.findall(r"(baseline|candidate)\s+([0-9a-f]{64})\s", printed)
    assert [role for role, _ in found] == ["baseline", "candidate"], found
    return found[0][1], found[1][1]


def _diff_rows(printed: str) -> list[str]:
    """The rows under the plan's ``diff (baseline -> candidate):`` line."""
    lines = printed[printed.index(_DIFF_HEADING) :].splitlines()[1:]
    indent = len(lines[0]) - len(lines[0].lstrip())
    rows = []
    for line in lines:
        if len(line) - len(line.lstrip()) != indent:
            break
        rows.append(line.strip())
    return rows


@pytest.mark.parametrize("path", _VARIANTS)
def test_the_headers_plan_command_plans_against_the_fakes_and_runs_nothing(
    path: Path, tmp_path: Path, stub_command: FakeArmRun, capsys: CaptureFixture
) -> None:
    printed = _plan(path, tmp_path, stub_command, capsys)

    assert printed.startswith("before/after measurement plan (NOTHING HAS BEEN SPENT)")
    assert "split:      repoqa-qa/small_dev" in printed
    assert "model:      qwen/qwen3.8-27b" in printed
    assert printed.rstrip().endswith("re-run with --confirm-spend to execute both arms.")


@pytest.mark.parametrize("path", _VARIANTS)
def test_each_overlay_plans_with_its_own_digest_and_prints_the_guidance_difference(
    path: Path, tmp_path: Path, stub_command: FakeArmRun, capsys: CaptureFixture
) -> None:
    printed = _plan(path, tmp_path, stub_command, capsys)

    baseline, candidate = _digests(printed)
    assert baseline != candidate
    assert f"candidate  {candidate}  ({path})" in printed
    assert _diff_rows(printed) == [
        f"guidance_sections: (default) -> {_load_variant_overlay(path).guidance_sections_path}"
    ], "the guidance text is the only difference from the baseline"


def test_the_three_variants_plan_with_pairwise_distinct_digests_over_one_baseline(
    tmp_path: Path, stub_command: FakeArmRun, capsys: CaptureFixture
) -> None:
    planned = [
        _digests(_plan(path, tmp_path / path.stem, stub_command, capsys)) for path in _VARIANTS
    ]

    assert len({baseline for baseline, _ in planned}) == 1, "one baseline, reused by every arm"
    assert len({candidate for _, candidate in planned}) == 3


# --- what each arm hands its rollouts --------------------------------------------


def _planned_arm_settings(
    path: Path, tmp_path: Path, stub_command: FakeArmRun, monkeypatch: pytest.MonkeyPatch
) -> dict[str, ArmSettings]:
    """Both arms' settings, built from the plan the header's command produced."""
    plans: list[object] = []
    monkeypatch.setattr(before_after_command, "render_plan", lambda plan: plans.append(plan) or "")
    repo = git_repo_with_two_descriptions(tmp_path)
    assert main(_plan_argv_bound_to_fakes(path, tmp_path, repo)) == 0
    args = argparse.Namespace(max_usd=None, base_url=None, config=tmp_path / "serve.yaml")
    return {
        role: arm_settings_for(args, plans[0], role=role, arm_dir=tmp_path / role)  # type: ignore[arg-type]
        for role in ("baseline", "candidate")
    }


def _guidance_handed_to_rollouts(
    settings: ArmSettings, tmp_path: Path
) -> tuple[list[dict[str, str]], list[object]]:
    """The guidance and task name every rollout of the arm was handed."""
    runner = FakeHarnessRunner(tmp_path / f"traces-{settings.role}")
    tasks = (eval_task("t1"), eval_task("t2"))
    asyncio.run(run_arm(settings, tasks, make_runner=lambda s, w: runner))
    return runner.guidance, [sample["task_name"] for sample in runner.samples]


@pytest.mark.parametrize(("path", "sections"), [(VARIANT_A, 11), (VARIANT_B, 10), (VARIANT_C, 10)])
def test_each_variant_hands_every_rollout_its_sections_with_the_repo_qa_task_name(
    path: Path,
    sections: int,
    tmp_path: Path,
    stub_command: FakeArmRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arms = _planned_arm_settings(path, tmp_path, stub_command, monkeypatch)

    guidance, task_names = _guidance_handed_to_rollouts(arms["candidate"], tmp_path)

    assert [len(handed) for handed in guidance] == [sections, sections]
    assert guidance == [dict(_load_variant_overlay(path).guidance_sections)] * 2
    assert (SYSTEM_PROMPT_KEY in guidance[0]) is (path == VARIANT_A)
    assert task_names == [REPO_QA_TASK_NAME] * 2


def test_the_arm_without_an_overlay_still_hands_every_rollout_no_guidance(
    tmp_path: Path, stub_command: FakeArmRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    arms = _planned_arm_settings(VARIANT_A, tmp_path, stub_command, monkeypatch)

    guidance, _ = _guidance_handed_to_rollouts(arms["baseline"], tmp_path)

    assert guidance == [{}, {}]


# --- the variants' texts, as the loader hands them -------------------------------


def test_variant_c_costs_what_variant_b_costs_on_the_three_sections_repo_qa_delivers() -> None:
    from pydocs_mcp.harness.core.skill_artifact_loader import (
        BACKBONE_HEADER,
        harness_task_head_section_header,
        task_head_section_header,
    )

    delivered = (
        BACKBONE_HEADER,
        task_head_section_header(REPO_QA_TASK_NAME),
        harness_task_head_section_header("ask_your_docs", REPO_QA_TASK_NAME),
    )
    seed, placebo = (
        _load_variant_overlay(path).guidance_sections for path in (VARIANT_B, VARIANT_C)
    )

    assert [len(placebo[header]) for header in delivered] == [
        len(seed[header]) for header in delivered
    ]
    assert all(placebo[header] != seed[header] for header in delivered)


def test_variant_a_carries_variant_bs_ten_skill_sections_and_the_draft() -> None:
    draft, seed = (
        dict(_load_variant_overlay(path).guidance_sections) for path in (VARIANT_A, VARIANT_B)
    )

    assert draft.pop(SYSTEM_PROMPT_KEY).strip()
    assert draft == seed


@pytest.mark.parametrize("key", _REFUSED_KEYS)
def test_no_overlay_sets_a_key_beyond_the_guidance(key: str) -> None:
    assert all(key not in _overlay_yaml(path) for path in _VARIANTS)


# --- the headers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "variant", "role"),
    [
        (VARIANT_A, "variant (a)", "DECIDES the flip"),
        (VARIANT_B, "variant (b)", "carrying six rules twice"),
        (VARIANT_C, "variant (c)", "effect of length alone"),
    ],
)
def test_each_header_names_its_variant_and_what_it_is_for(
    path: Path, variant: str, role: str
) -> None:
    header = _collapsed_header(path)

    assert variant in header and role in header


@pytest.mark.parametrize("path", _VARIANTS)
def test_each_header_says_how_the_arms_fit_together(path: Path) -> None:
    header = _collapsed_header(path)

    assert "The baseline is run once and reused by every other arm (--reuse-baseline" in header
    assert "A/A replicate: a fifth arm" in header and "--allow-identical" in header
    assert "drop (c) first, then (b); never (a)" in header
    assert "$25 stop-and-ask" in header


@pytest.mark.parametrize("path", _VARIANTS)
def test_each_header_writes_the_free_plan_command_and_no_paid_one(path: Path) -> None:
    flags = _header_plan_command(path)

    assert flags["--candidate-settings"] == str(path.relative_to(_REPO_ROOT))
    assert flags["--split"] == "repoqa-qa/small_dev"
    assert flags["--model"] == "qwen/qwen3.8-27b"
    assert (_REPO_ROOT / flags["--config"]) == _LADDER_SERVING
    assert (_REPO_ROOT / flags["--llm-block"]) == _LADDER_LLM_BLOCK
    assert "--confirm-spend" not in flags, "no paid command line is written out"
    assert "The paid run adds --confirm-spend, and only after the owner's go" in (
        _collapsed_header(path)
    )


def test_the_serving_file_leaves_the_12_turn_budget_at_the_product_default() -> None:
    """The headers' 12-Turn budget is the serving file's, not an overlay key."""
    from pydocs_mcp.retrieval.config.app_config import AppConfig
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

    serving = AppConfig.load(explicit_path=_LADDER_SERVING)

    assert serving.ask_your_docs.max_agent_turns == AskYourDocsConfig().max_agent_turns
    assert serving.ask_your_docs.max_agent_turns == 12, "the spec's pinned 12-Turn budget"
    assert all("12-Turn budget" in _collapsed_header(path) for path in _VARIANTS)


def _collapsed_header(path: Path) -> str:
    """The comment header as one line of prose, so a re-wrap moves no assertion."""
    lines = path.read_text(encoding="utf-8").splitlines()
    comments = [line.removeprefix("#").strip() for line in lines if line.startswith("#")]
    return " ".join(comments)


@pytest.mark.parametrize("path", _VARIANTS)
def test_each_header_command_plans_against_a_reused_baseline_and_prices_one_arm(
    path: Path, tmp_path: Path, stub_command: FakeArmRun, capsys: CaptureFixture
) -> None:
    """The later arms' paid step: the header's command plus ``--reuse-baseline``."""
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = write_finished_baseline(
        tmp_path / "earlier" / "baseline", commit=_head_sha(repo), model="qwen/qwen3.8-27b"
    )
    argv = [*_plan_argv_bound_to_fakes(path, tmp_path, repo), "--reuse-baseline", str(reused)]

    assert main(argv) == 0

    printed = capsys.readouterr().out
    assert f"reused from {reused} (no baseline rollout)" in printed
    assert "estimate:   2 rollout(s)" in printed, "the candidate alone is priced"
    assert stub_command.roles == []


def _head_sha(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()
