"""What ``compare`` prints on stdout: the files it read, each leg's aggregates, and a verdict line.

Every leg file on each side is listed first, paired or not, with the run's
task records, the points a relaunch resumed it from, and whether the leg has
its final block, so a pull taken mid-run shows how far the run got. Each
identical aggregate is then listed with its value, and each wall-clock one
with both sides' values, so the output is the acceptance evidence itself.
The problems go to stderr, one ``got …, expected …`` line each
(``local_reference_cli``); the verdict line only counts them.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydocs_eval.bench_vm.leg_comparison import (
    ComparedSide,
    LegFileReading,
    LegVerdict,
    RunComparison,
)


def render_run_comparison(comparison: RunComparison) -> str:
    """The stdout report of one ``compare``: both sides' files, every paired leg, the verdict."""
    lines = [
        f"run:   {comparison.run.results_dir}",
        *_file_lines(comparison.run),
        f"local: {comparison.local.results_dir}",
        *_file_lines(comparison.local),
    ]
    for leg in comparison.legs:
        lines += _leg_lines(leg)
    lines.append(_verdict_line(comparison))
    return "\n".join(lines)


def _file_lines(side: ComparedSide) -> list[str]:
    if not side.readings:
        return ["  (no leg files)"]
    return [_file_line(side, reading) for reading in side.readings]


def _file_line(side: ComparedSide, reading: LegFileReading) -> str:
    leg = "no run_start header" if reading.key is None else f"leg {reading.key}"
    progress = ""
    if side.role.lists_task_records:
        progress += f", {reading.task_record_count} task records"
    if reading.resumed_after:
        progress += f", resumed after {', '.join(str(n) for n in reading.resumed_after)}"
    block = "final aggregate block" if reading.block else "no final aggregate block yet"
    return f"  {reading.path.relative_to(side.results_dir)}: {leg}{progress}, {block}"


def _leg_lines(leg: LegVerdict) -> list[str]:
    return [
        f"leg {leg.key}",
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
    if leg.ran_one_commit:
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
