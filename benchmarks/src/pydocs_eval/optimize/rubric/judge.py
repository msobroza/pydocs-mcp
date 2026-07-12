"""Configurable rubric judge — one-shot prompt + strict parse-or-discard (spec §3.4.3).

``ConfigurableRubricJudge`` renders a judge prompt from the configured
criteria and reuses the agent-track judging machinery: a one-shot, tool-less
agent-CLI arm whose cost counts into the run budget. Parsing is strict —
any missing, non-numeric, or out-of-range criterion discards the sample
(never admitted partially scored). ``FakeRubricJudge`` is the scripted
offline double every test and ``--dry-run`` uses.

Unlike the paired-track blind judge, this one scores a SINGLE transcript
against criteria — no A/B shuffle is needed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_eval.optimize._agent_track_binding import AgentRunner, ArmConfig
from pydocs_eval.optimize.rubric.model import RubricCriterion

# WHY 1: the judge reads the rendered prompt and returns JSON — it must not
# go exploring. The empty tool surface (no_tools=True) is what actually
# enforces it; the single turn bounds it further (the RealJudge precedent).
_RUBRIC_JUDGE_MAX_TURNS = 1


@dataclass(frozen=True, slots=True)
class RubricVerdict:
    """One judge call's outcome: per-criterion scores or a discard reason."""

    scores: Mapping[str, float] | None
    cost_usd: float
    discard_reason: str | None = None


@runtime_checkable
class RubricJudge(Protocol):
    """Scores one answer against the configured criteria (spec §3.4.3)."""

    async def score(
        self,
        *,
        question: str,
        answer: str,
        criteria: tuple[RubricCriterion, ...],
    ) -> RubricVerdict: ...


def build_rubric_prompt(
    *, question: str, answer: str, criteria: tuple[RubricCriterion, ...]
) -> str:
    """Render the one-shot judge prompt from the configured criteria.

    Example:
        >>> c = (RubricCriterion("correctness", 1.0, "Factually correct."),)
        >>> "score 0-10" in build_rubric_prompt(question="q", answer="a", criteria=c)
        True
    """
    criteria_block = "\n".join(f"- {c.name}: {c.description}" for c in criteria)
    keys = ", ".join(f'"{c.name}": <0-10>' for c in criteria)
    return (
        "You are a strict evaluator. Score the answer on each criterion, "
        "score 0-10 each; do not reward verbosity.\n\n"
        f"Criteria:\n{criteria_block}\n\n"
        f"Question:\n{question}\n\n"
        f"Answer:\n{answer}\n\n"
        f"Reply with ONLY a JSON object: {{{keys}}}\n"
    )


def parse_rubric_reply(
    text: str, *, criteria: tuple[RubricCriterion, ...]
) -> dict[str, float] | None:
    """Extract per-criterion 0-10 scores from a judge reply, or ``None``.

    Strict parse-or-discard: the first ``{…}`` block is extracted from any
    surrounding prose; every configured criterion must be present, numeric,
    and within 0-10, or the whole reply is rejected — a partial score is
    never admitted (spec AC-10).
    """
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    scores: dict[str, float] = {}
    for criterion in criteria:
        value = payload.get(criterion.name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        if not 0 <= float(value) <= 10:
            return None
        scores[criterion.name] = float(value)
    return scores


@dataclass(frozen=True, slots=True)
class ConfigurableRubricJudge:
    """Rubric judge backed by a one-shot, tool-less agent-CLI arm.

    Reuses the same subprocess adapter as the arms (``runner``) so the
    judge's spend is measured and counted into the run budget — the RealJudge
    shape from the paired track, minus the blind A/B shuffle.
    """

    runner: AgentRunner
    judge_model: str
    cwd: Path

    async def score(
        self,
        *,
        question: str,
        answer: str,
        criteria: tuple[RubricCriterion, ...],
    ) -> RubricVerdict:
        """Run the judge arm and strictly parse its reply (spec AC-10).

        A timed-out arm or a malformed reply both yield a discard verdict
        with a reason — the fitness writes the discard line and excludes the
        sample, never admitting it unscored.
        """
        prompt = build_rubric_prompt(question=question, answer=answer, criteria=criteria)
        arm = ArmConfig(
            name="rubric_judge",
            model=self.judge_model,
            max_turns=_RUBRIC_JUDGE_MAX_TURNS,
            no_tools=True,
        )
        metrics = await self.runner.run(arm, prompt=prompt, cwd=self.cwd, mcp_config=None)
        if metrics is None:
            return RubricVerdict(scores=None, cost_usd=0.0, discard_reason="judge arm timed out")
        scores = parse_rubric_reply(metrics.answer, criteria=criteria)
        if scores is None:
            return RubricVerdict(
                scores=None,
                cost_usd=metrics.cost_usd,
                discard_reason="judge reply missing or invalid criterion scores",
            )
        return RubricVerdict(scores=scores, cost_usd=metrics.cost_usd)


@dataclass(slots=True)
class FakeRubricJudge:
    """Scripted offline double: canned per-question scores + a call counter.

    ``scripted`` maps a question to its criterion scores; an unscripted
    question discards (mirroring the real judge's failure path). ``calls``
    lets tests prove the gate short-circuit and per-sample resume never
    invoke the judge (spec AC-9, AC-11).
    """

    scripted: Mapping[str, Mapping[str, float]]
    cost_per_call: float = 0.0
    calls: int = field(default=0, init=False)

    async def score(
        self,
        *,
        question: str,
        answer: str,
        criteria: tuple[RubricCriterion, ...],
    ) -> RubricVerdict:
        _ = (answer, criteria)
        self.calls += 1
        scores = self.scripted.get(question)
        if scores is None:
            return RubricVerdict(
                scores=None, cost_usd=self.cost_per_call, discard_reason="unscripted question"
            )
        return RubricVerdict(scores=dict(scores), cost_usd=self.cost_per_call)
