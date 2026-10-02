"""What ``write-references`` prints: its plan before any spend, and what a run did.

Example:
    >>> print(render_report(report))  # doctest: +SKIP
    written: 20 (writer 20, fallback 0)
    listed for the owner: 0
    ...
"""

from __future__ import annotations

from collections.abc import Iterator

from pydocs_eval.campaign.reference_writer_run import ReferenceRunReport, ReferenceWriterPlan
from pydocs_eval.judge.reference_writer import ReferenceGap, ReferenceGapKind
from pydocs_eval.judge.role_config import ReferenceWriterConfig

# A rough English-text ratio: the plan prints an order of magnitude, not a bill.
_CHARS_PER_TOKEN = 4
# What the owner does about a listed task, by why it was listed.
_NEXT_STEP = {
    ReferenceGapKind.STILL_RUNNING: "re-run this command to collect it by id",
    ReferenceGapKind.NOT_SUBMITTED: (
        "the submit may have been accepted anyway: check GET /api/v1/batches for it "
        "before re-running, or it is paid for twice"
    ),
    ReferenceGapKind.FAILED_CHECK: "read the reason; re-running asks it again",
    ReferenceGapKind.BOTH_MODELS_FAILED: "read both reasons; re-running asks it again",
}


def render_plan(split: str, plan: ReferenceWriterPlan, writer: ReferenceWriterConfig) -> str:
    """The plan as the owner reads it before any spend.

    Example:
        >>> print(render_plan("repoqa-qa/small_dev", plan, writer))  # doctest: +SKIP
        write-references repoqa-qa/small_dev
          tasks: 20 (stored already: 0, to write: 20)
        ...
    """
    collect = ", ".join(f"{b.batch_id} ({len(b.rows)} rows)" for b in plan.to_collect) or "none"
    tokens = plan.prompt_chars // _CHARS_PER_TOKEN
    return "\n".join(
        [
            f"write-references {split}",
            f"  tasks: {len(plan.tasks)} (stored already: {len(plan.stored)}, "
            f"to write: {len(plan.sources)})",
            f"  writer: {writer.model} at {writer.reasoning_effort.value}; fallback "
            f"{writer.fallback_model}, only for a row whose writer job failed",
            f"  each batch waited on {writer.timeout_seconds:g} s, then collected by a re-run; "
            f"{writer.retries} regenerations per row; {writer.context_lines} lines around a needle",
            f"  first prompts: {plan.prompt_chars} chars (about {tokens} tokens), "
            "plus each answer's reasoning and output",
            f"  batches to collect first: {collect}",
            f"  batches to delete first: {', '.join(plan.to_delete) or 'none'}",
            f"  rows file: {plan.out}",
            f"  journal: {plan.journal.path}",
        ]
    )


def render_report(report: ReferenceRunReport) -> str:
    """What the run did: rows kept, tasks listed for the owner, spend, batches touched.

    Example:
        >>> print(render_report(report))  # doctest: +SKIP
        written: 20 (writer 20, fallback 0)
        ...
    """
    result = report.result
    fallback = sum(1 for row in result.rows if row.fallback_reason)
    lines = [
        f"written: {len(result.rows)} (writer {len(result.rows) - fallback}, fallback {fallback})",
        f"listed for the owner: {len(result.gaps)}",
        *(line for gap in result.gaps for line in _gap_lines(gap)),
        f"cost reported by OpenRouter: ${result.cost_usd:.4f} "
        f"({result.uncosted_answers} answers unpriced)",
        f"batches deleted: {', '.join(report.deleted) or 'none'}",
        *(f"  - not deleted {f.batch_id}: {f.reason}" for f in report.not_deleted),
    ]
    if result.kept_open:
        lines.append(f"kept open for the tasks another run covers: {', '.join(result.kept_open)}")
    if report.lost:
        lines.append(f"lost upstream (tasks asked again): {', '.join(report.lost)}")
    return "\n".join(lines)


def _gap_lines(gap: ReferenceGap) -> Iterator[str]:
    batch = f" batch {gap.batch_id}" if gap.batch_id else ""
    yield f"  - {gap.task_id} [{gap.kind.value}]{batch}: {gap.reason}"
    yield f"    next: {_NEXT_STEP[gap.kind]}"


__all__ = ("render_plan", "render_report")
