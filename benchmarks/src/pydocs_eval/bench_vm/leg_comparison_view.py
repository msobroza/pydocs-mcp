"""What ``compare`` prints on stdout: where it read, each leg's aggregates, and a verdict line.

Each identical aggregate is listed with its value, and each wall-clock one
with both sides' values, so the output is the acceptance evidence itself.
The problems go to stderr, one ``got …, expected …`` line each
(``local_reference_cli``); the verdict line only counts them.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydocs_eval.bench_vm.leg_comparison import LegVerdict, RunComparison

# The run tag naming the product commit (``sweep_support``: git, else the image's variable).
_COMMIT_TAG = "git_sha"


def render_run_comparison(comparison: RunComparison) -> str:
    lines = [f"run:   {comparison.run_results_dir}", f"local: {comparison.local_results_dir}"]
    for leg in comparison.legs:
        lines += _leg_lines(leg)
    lines.append(_verdict_line(comparison))
    return "\n".join(lines)


def _leg_lines(leg: LegVerdict) -> list[str]:
    return [
        f"leg {leg.key}",
        f"  run file:   {leg.run.path} ({leg.run.task_records} task records)",
        f"  local file: {leg.local.path}",
        f"  run tags:   {_tags_text(leg.run.tags)}",
        f"  local tags: {_tags_text(leg.local.tags)}",
        *_commit_note(leg),
        *(f"  identical: {name} = {value!r}" for name, value in leg.pairing.identical),
        *(
            f"  wall-clock, not compared: {name}: run {run!r}, local {local!r}"
            for name, run, local in leg.pairing.wall_clock
        ),
    ]


def _tags_text(tags: Mapping[str, str]) -> str:
    return " ".join(f"{name}={value}" for name, value in sorted(tags.items())) or "(none)"


def _commit_note(leg: LegVerdict) -> list[str]:
    # The comparison is only about the resume when both sides ran one commit.
    if leg.run.tags.get(_COMMIT_TAG) == leg.local.tags.get(_COMMIT_TAG):
        return []
    return [
        "  note: the two sides ran different commits; the local reference should run "
        "the run's commit"
    ]


def _verdict_line(comparison: RunComparison) -> str:
    if not comparison.identical:
        problems = len(comparison.failures)
        return f"not identical: {problems} problem{'' if problems == 1 else 's'}, listed on stderr"
    identical = sum(len(leg.pairing.identical) for leg in comparison.legs)
    wall_clock = sum(len(leg.pairing.wall_clock) for leg in comparison.legs)
    legs = len(comparison.legs)
    return (
        f"identical: {identical} aggregates bit-identical across {legs} "
        f"leg{'' if legs == 1 else 's'}; {wall_clock} wall-clock aggregates reported, not compared"
    )
