"""``report-alignment``: what an alignment label file says, and the two paid reads made on it.

By default it reads the label file and prints, at $0, the labellers' κ per
question, which way their disagreements go, the owner's queue, the audit and,
once the escalation judge's agreement pass is in the file, its trust per
question — its agreement against ``min(agreement_floor, κ_labellers)``, its κ
beside the labellers' (flagged when above), and its agreement with each
labeller alone. Two parts call OpenRouter and need ``--confirm-spend``:

- ``--escalation-pass`` has the escalation judge (Luna) label every item both
  labellers answered, once, and stores its answers in the label file (the
  agreement pass: its own ledger line, outside the in-band $2 cap);
- ``--sweep`` asks Jev for its raw probabilities on every item (cached by
  input hash, so a re-run is free) and prints, per question, each review band
  from ``band_start`` outward and each ``completeness`` confidence gate, with
  Jev's agreement against the share it decides alone.

Exit status: 0 on a report, 2 when the input is refused or OpenRouter refuses.

Usage:
    python -m pydocs_eval.campaign report-alignment --labels FILE \\
        [--escalation-pass] [--sweep] [--confirm-spend]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import httpx

from pydocs_eval.campaign.alignment_report_text import SweepTable, render_alignment_report
from pydocs_eval.campaign.judge_openers import JevOpener, open_openrouter_jev
from pydocs_eval.judge.alignment_label_file import (
    AlignmentLabelFileError,
    read_alignment_labels,
    write_alignment_labels,
)
from pydocs_eval.judge.alignment_labelling import label_with_escalation_judge
from pydocs_eval.judge.alignment_request import alignment_plan
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, QuestionKey
from pydocs_eval.judge.chat_wire import ChatCompleter
from pydocs_eval.judge.config import JudgeConfig, load_judge_deployment
from pydocs_eval.judge.jev_scoring import calibration_answers
from pydocs_eval.judge.judge_errors import (
    JudgeConfigError,
    JudgeModelMismatchError,
    JudgeRequestError,
)
from pydocs_eval.judge.openrouter_chat import OpenRouterChatClient
from pydocs_eval.judge.openrouter_http import bearer_from_env
from pydocs_eval.judge.role_config import DEPLOYMENT_YAML
from pydocs_eval.judge.roles import escalation_role, jev_model
from pydocs_eval.judge.threshold_sweep import (
    SweepRow,
    sweep_noul_bands,
    sweep_pairs,
    sweep_score_gates,
)

_EXIT_REPORTED = 0
_EXIT_REFUSED = 2
_INPUT_ERRORS = (JudgeConfigError, AlignmentLabelFileError, ValueError, OSError)
_SPEND_ERRORS = (JudgeRequestError, JudgeModelMismatchError)

#: Opens the pinned escalation judge for its agreement pass.
EscalationOpener = Callable[[JudgeConfig], AbstractContextManager[ChatCompleter]]


def add_report_alignment_command(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``report-alignment`` on the campaign CLI's subparser."""
    parser = sub.add_parser(
        "report-alignment",
        help="report an alignment label file; --escalation-pass / --sweep spend with --confirm-spend",
    )
    parser.add_argument("--labels", type=Path, required=True, help="the alignment label file")
    parser.add_argument(
        "--deployment", type=Path, default=Path(DEPLOYMENT_YAML), help="the judge deployment YAML"
    )
    parser.add_argument(
        "--escalation-pass",
        action="store_true",
        help="have the escalation judge label every labelled item once (stored in the file)",
    )
    parser.add_argument(
        "--sweep", action="store_true", help="sweep Jev's bands and gates against the labels"
    )
    parser.add_argument(
        "--confirm-spend", action="store_true", help="allow the paid parts: calls OpenRouter"
    )
    parser.set_defaults(func=cmd_report_alignment)


def cmd_report_alignment(
    args: argparse.Namespace,
    *,
    open_escalation: EscalationOpener | None = None,
    open_jev: JevOpener | None = None,
) -> int:
    """Print the report; with ``--confirm-spend``, run the agreement pass or the sweep first."""
    try:
        judge = load_judge_deployment(args.deployment).judge
        rows = _read_rows(args)
        _refuse_unconfirmed_spend(args, judge)
    except _INPUT_ERRORS as exc:
        print(f"report-alignment: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    try:
        if args.escalation_pass:
            opener = open_escalation or _open_openrouter_escalation_judge
            rows = _escalation_pass(args.labels, rows, judge, opener)
        sweep = _sweep(rows, judge, open_jev or open_openrouter_jev) if args.sweep else None
    except _SPEND_ERRORS as exc:
        print(f"report-alignment: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    print(render_alignment_report(rows, judge, sweep=sweep))
    return _EXIT_REPORTED


def _read_rows(args: argparse.Namespace) -> tuple[AlignmentLabelRow, ...]:
    if not args.labels.exists():
        raise ValueError(f"--labels {args.labels} does not exist, expected an alignment label file")
    return read_alignment_labels(args.labels)


def _refuse_unconfirmed_spend(args: argparse.Namespace, judge: JudgeConfig) -> None:
    paid = [
        flag
        for flag, on in (("--escalation-pass", args.escalation_pass), ("--sweep", args.sweep))
        if on
    ]
    if paid and not args.confirm_spend:
        raise ValueError(f"{', '.join(paid)} calls OpenRouter: it needs --confirm-spend")
    if args.escalation_pass:
        bearer_from_env(escalation_role(judge).config.api_key_env)
    if args.sweep:
        jev_model(judge)
        bearer_from_env(judge.jev.api_key_env)


def _escalation_pass(
    labels: Path,
    rows: Sequence[AlignmentLabelRow],
    judge: JudgeConfig,
    open_escalation: EscalationOpener,
) -> tuple[AlignmentLabelRow, ...]:
    """The escalation judge's answers on every labelled item, stored in the label file."""
    model = escalation_role(judge).model
    with open_escalation(judge) as client:
        judged = label_with_escalation_judge(rows, client, model=model, jev=judge.jev)
    write_alignment_labels(labels, judged)
    return judged


def _sweep(
    rows: Sequence[AlignmentLabelRow], judge: JudgeConfig, open_jev: JevOpener
) -> SweepTable:
    """Jev's raw answers on every item, swept against the labels that stand."""
    with open_jev(judge) as jev_judge:
        answers = {
            row.item_id: calibration_answers(
                alignment_plan(row.context, jev=judge.jev), judge=jev_judge, config=judge
            )
            for row in rows
        }
    pairs = sweep_pairs(rows, answers)
    table: dict[QuestionKey, Sequence[SweepRow]] = {}
    for key, nouls in pairs.nouls.items():
        table[key] = sweep_noul_bands(nouls, band_start=judge.alignment.band_start)
    for key, scores in pairs.scores.items():
        table[key] = sweep_score_gates(scores)
    return table


@contextmanager
def _open_openrouter_escalation_judge(judge: JudgeConfig) -> Iterator[ChatCompleter]:
    """The pinned escalation judge on OpenRouter, for its agreement pass."""
    with httpx.Client() as http:
        yield OpenRouterChatClient(escalation_role(judge), http)


__all__ = ("add_report_alignment_command", "cmd_report_alignment")
