"""What an arm records about the settings it ran under, and that older files still load.

An arm's ``arm_settings.json`` and ``arm.json`` both carry the architecture, the
task scaffold, the guidance file and the settings digest, because the report's
provenance, ``--reuse-baseline`` and the compare verb read them back. Files
written before those fields existed must still load — as "not recorded" in the
summary, and as the settings every arm then ran under in the settings file.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest, CostModel, MeasurementPlan
from pydocs_eval.campaign.before_after_arm import (
    ARM_ARCHITECTURE,
    ARM_SETTINGS_FILENAME,
    ARM_SUMMARY_FILENAME,
    ArmSettings,
    ArmSummary,
    read_arm_settings,
    read_arm_summary,
    run_arm,
    write_arm_settings,
)
from pydocs_eval.campaign.before_after_corpora import TaskWorkspaces
from pydocs_eval.campaign.before_after_measure import ArmMetrics, measure_arm
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.task_rendering import TaskScaffold

from ._fakes import FakeHarnessRunner, eval_task

_DIGEST = "d" * 64


def arm_settings(tmp_path: Path, **overrides: object) -> ArmSettings:
    fields: dict[str, object] = {
        "role": "candidate",
        "commit": "b" * 40,
        "workspace": str(tmp_path / "ws"),
        "model": "test-model",
        "trace_root": str(tmp_path / "traces"),
        "out_dir": str(tmp_path / "arm"),
        "max_agent_turns": 4,
        "estimated_usd_per_rollout": 0.25,
        "cost_ceiling_usd": 10.0,
    }
    return ArmSettings(**{**fields, **overrides})  # type: ignore[arg-type]


def test_the_settings_file_round_trips_the_variant_fields(tmp_path: Path) -> None:
    settings = arm_settings(
        tmp_path,
        architecture="auto",
        harness_block={"seed_search_with_question": True},
        guidance_sections_path="guidance.md",
        guidance_sections={"BACKBONE": "search first"},
        settings_digest=_DIGEST,
    )
    write_arm_settings(tmp_path, settings)

    loaded = read_arm_settings(tmp_path)

    assert loaded == settings
    assert loaded.task_scaffold is TaskScaffold.CITING


def test_a_settings_file_written_before_the_variant_fields_loads_as_the_pinned_arm(
    tmp_path: Path,
) -> None:
    """Every arm before variant arms ran the pinned architecture and the citing scaffold."""
    payload = asdict(arm_settings(tmp_path))
    for key in (
        "architecture",
        "harness_block",
        "guidance_sections_path",
        "guidance_sections",
        "task_scaffold",
        "settings_digest",
    ):
        payload.pop(key)
    (tmp_path / ARM_SETTINGS_FILENAME).write_text(json.dumps(payload), encoding="utf-8")

    loaded = read_arm_settings(tmp_path)

    assert loaded.architecture == ARM_ARCHITECTURE
    assert loaded.task_scaffold is TaskScaffold.CITING
    assert loaded.harness_block == {} and loaded.guidance_sections == {}
    assert loaded.settings_digest == ""


def test_the_arm_summary_carries_what_the_arm_ran_under(tmp_path: Path) -> None:
    settings = arm_settings(
        tmp_path,
        architecture="auto",
        guidance_sections_path="guidance.md",
        settings_digest=_DIGEST,
    )

    summary = asyncio.run(
        run_arm(
            settings,
            (eval_task("t1"),),
            make_runner=lambda s, workspace: FakeHarnessRunner(Path(s.trace_root)),
        )
    )

    assert read_arm_summary(Path(settings.out_dir)) == summary
    assert (summary.architecture, summary.task_scaffold) == ("auto", "citing")
    assert summary.guidance_sections_path == "guidance.md"
    assert summary.settings_digest == _DIGEST


def test_an_arm_summary_written_before_the_variant_fields_reads_as_unrecorded(
    tmp_path: Path,
) -> None:
    settings = replace(arm_settings(tmp_path), out_dir=str(tmp_path))
    asyncio.run(
        run_arm(
            settings,
            (eval_task("t1"),),
            make_runner=lambda s, workspace: FakeHarnessRunner(Path(s.trace_root)),
        )
    )
    payload = json.loads((tmp_path / ARM_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    for key in ("architecture", "task_scaffold", "guidance_sections_path", "settings_digest"):
        payload.pop(key)
    (tmp_path / ARM_SUMMARY_FILENAME).write_text(json.dumps(payload), encoding="utf-8")

    loaded = read_arm_summary(tmp_path)

    assert (loaded.architecture, loaded.task_scaffold, loaded.settings_digest) == ("", "", "")
    assert loaded.guidance_sections_path is None
    assert [task.task_id for task in loaded.tasks] == ["t1"]


# --- the report's provenance ------------------------------------------------


def _measured(role: str, **variant: object) -> ArmMetrics:
    summary = ArmSummary(
        role=role,
        commit="a" * 40,
        model="test-model",
        trace_root="",
        tasks=[],
        estimated_usd=0.0,
        halt_reason="completed",
        excluded=0,
        **variant,  # type: ignore[arg-type]
    )
    commit = CommitUnderTest(role=role, sha="a" * 40, subject="s", description_tokens=1)
    return measure_arm(summary, commit, workspace=Path("/ws"))


def _provenance(baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    plan = MeasurementPlan(
        split="repoqa-qa/dev",
        task_ids=(),
        baseline=baseline.commit,
        candidate=candidate.commit,
        model="test-model",
        endpoint="e",
        workspace=Path("/ws"),
        max_agent_turns=4,
        cost=CostModel(),
        task_workspaces=TaskWorkspaces(root=Path("/ws"), shared_workspace=Path("/ws")),
    )
    report = render_report(plan, [baseline, candidate])
    return report[: report.index("| Metric |")]


def test_the_report_provenance_names_each_arms_settings() -> None:
    baseline = _measured(
        "baseline", architecture="text_react", task_scaffold="citing", settings_digest=_DIGEST
    )
    candidate = _measured(
        "candidate",
        architecture="auto",
        task_scaffold="citing",
        guidance_sections_path="g.md",
        settings_digest="e" * 64,
    )

    text = _provenance(baseline, candidate)

    assert (
        f"- settings, baseline: architecture `text_react`, scaffold `citing`, guidance none, "
        f"digest `{_DIGEST}`" in text
    )
    assert "- settings, candidate: architecture `auto`, scaffold `citing`, guidance `g.md`" in text


def test_arms_written_before_settings_digests_print_no_settings_line() -> None:
    text = _provenance(_measured("baseline"), _measured("candidate"))

    assert "- settings," not in text
