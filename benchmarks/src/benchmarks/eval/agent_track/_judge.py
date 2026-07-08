"""Blind two-answer judge for the agent track (spec §D15).

The paired harness measures efficiency deltas *at answer-quality parity*, so it
needs a quality signal that cannot be gamed by which arm produced an answer. The
judge is BLIND: ``build_judge_prompt`` shuffles the two arms' answers into
opaque ``Answer A`` / ``Answer B`` slots (arm names never appear in the prompt),
scores each against the gold answer on five 0-10 rubric dimensions, and
``parse_judge_reply`` maps the labels back to arm names — recording the shuffle
in each ``JudgeScore.blind_label_map`` so a verdict is auditable.

Determinism: the A/B shuffle comes from ``random.Random(rng_seed)`` only — no
global randomness — so a resumed run rebuilds the exact same blind prompt for a
task and the ledger stays consistent across restarts (slice-6 contract).

Seams (all pure / Protocol so the harness stays offline-testable):
- ``build_judge_prompt`` / ``parse_judge_reply`` are pure — fixture-tested with
  no subprocess.
- ``Judge`` is the Protocol the orchestrator depends on; ``RealJudge`` reuses
  ``ClaudeAgentRunner`` in a one-shot, tool-less arm (its cost counts into the
  run budget), and ``FakeJudge`` is the scripted double for downstream tests and
  external consumers (slice-6 contract).
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from benchmarks.eval.agent_track._runner import AgentRunner
from benchmarks.eval.agent_track._types import ArmConfig, JudgeScore, RunMetrics

# The two blind slots every answer is shuffled into. Two arms → two labels; the
# arm names (``bare`` / ``indexed``) never reach the judge, only these do.
_BLIND_LABELS = ("A", "B")

# The five 0-10 rubric dimensions, in the order they are averaged into the mean.
# Single source of truth: the prompt lists them, the parser reads exactly these,
# and the mean is over all five (an answer's overall score is the flat average).
_RUBRIC_DIMENSIONS = ("correctness", "completeness", "relevance", "clarity", "reasoning")

# The judge arm's one-shot config: same model family as the arms (chosen by the
# caller via ``RealJudge.judge_model``), a single turn, and NO tools/MCP — the
# judge reads the prompt and returns JSON, it must not go exploring. Its cost is
# folded into the run budget by the orchestrator.
_JUDGE_MAX_TURNS = 1

# The committed rubric prompt (spec §D15): five dimensions, 0-10, explicit
# "do not reward verbosity". Kept as the runtime source of truth here; the
# byte-identical copy at ``tests/.../fixtures/judge_rubric.md`` is the
# reviewer-facing artifact, and ``test_judge`` pins the two in sync.
_RUBRIC_TEXT = """\
You are a blind, impartial judge scoring two candidate answers to a question
about a code repository against a reference answer. You do NOT know which system
produced which answer, and you must not try to guess.

Score EACH answer against the reference answer on these five dimensions, each on
an integer 0-10 scale (0 = worst, 10 = best):

- correctness: Does the answer state facts that agree with the reference? Penalize
  claims that contradict the reference or are fabricated.
- completeness: Does the answer cover what the reference covers? Penalize missing
  key facts; do NOT reward padding.
- relevance: Does the answer address the question that was asked, without drifting
  into unrelated material?
- clarity: Is the answer easy to follow — well organized, unambiguous, concrete?
- reasoning: Where the answer explains HOW or WHY, is the explanation sound and
  supported by the cited files/lines?

Do not reward verbosity. A short, correct, well-cited answer must outscore a long,
padded, or hedging one. Judge only the substance against the reference.

Respond with a SINGLE JSON object and nothing else, with exactly this shape:

{
  "A": {"correctness": <int>, "completeness": <int>, "relevance": <int>, "clarity": <int>, "reasoning": <int>},
  "B": {"correctness": <int>, "completeness": <int>, "relevance": <int>, "clarity": <int>, "reasoning": <int>}
}
"""


def build_judge_prompt(
    *,
    question: str,
    gold: str,
    answers: dict[str, str],
    rng_seed: int,
) -> tuple[str, dict[str, str]]:
    """Render the blind judge prompt and the label→arm map for one task.

    The two arms' answers are shuffled into ``Answer A`` / ``Answer B`` slots
    with ``random.Random(rng_seed)`` (no global randomness — a resumed run
    rebuilds the identical prompt), so the judge never sees an arm name. Returns
    the prompt text plus ``{"A": <arm>, "B": <arm>}`` so ``parse_judge_reply``
    can map the verdict back.

    Raises ``ValueError`` unless ``answers`` has exactly two arms — the judge is
    a two-answer comparison, not an N-way one.

    Example:
        >>> prompt, label_map = build_judge_prompt(  # doctest: +SKIP
        ...     question="q?", gold="g", answers={"bare": "a1", "indexed": "a2"}, rng_seed=7
        ... )
        >>> set(label_map.values()) == {"bare", "indexed"}
        True
    """
    if len(answers) != len(_BLIND_LABELS):
        raise ValueError(
            "blind judge scores exactly two answers, got "
            f"{len(answers)} arm(s): {sorted(answers)!r}"
        )
    # Sort for a stable starting order, then shuffle deterministically — so the
    # shuffle depends only on ``rng_seed``, not on dict insertion order.
    arms = sorted(answers)
    random.Random(rng_seed).shuffle(arms)
    label_map = dict(zip(_BLIND_LABELS, arms, strict=True))
    body = "\n\n".join(f"Answer {label}:\n{answers[arm]}" for label, arm in label_map.items())
    prompt = f"{_RUBRIC_TEXT}\nQuestion:\n{question}\n\nReference answer:\n{gold}\n\n{body}\n"
    return prompt, label_map


def parse_judge_reply(
    text: str,
    *,
    label_map: dict[str, str],
) -> dict[str, JudgeScore] | None:
    """Map a judge reply back to arm-name-keyed ``JudgeScore``s, or ``None``.

    Tolerant: the reply may wrap the JSON object in prose, so the first ``{…}``
    block is extracted and parsed. Returns ``None`` (a judge failure the
    orchestrator handles, never a raise) when the block is missing, is not JSON,
    or omits a scored label — a paired verdict needs both labels.

    The mean is the flat average of the five 0-10 rubric dimensions; the
    per-arm shuffle is recorded in each score's ``blind_label_map`` for audit.

    Example:
        >>> parse_judge_reply(  # doctest: +SKIP
        ...     '{"A": {"correctness": 9, "completeness": 8, "relevance": 9,'
        ...     ' "clarity": 9, "reasoning": 8}, "B": {...}}',
        ...     label_map={"A": "indexed", "B": "bare"},
        ... )["indexed"].mean
        8.6
    """
    payload = _extract_json_object(text)
    if payload is None:
        return None
    scores: dict[str, JudgeScore] = {}
    for label, arm in label_map.items():
        dims = payload.get(label)
        score = _score_from_dims(dims, blind_label_map=label_map)
        if score is None:
            return None  # a label with no valid numeric dims → no paired verdict
        scores[arm] = score
    return scores


def _extract_json_object(text: str) -> dict[str, object] | None:
    # Pull the first balanced-looking ``{…}`` block out of surrounding prose and
    # parse it. Greedy to the last ``}`` so a nested object survives; a failed
    # decode degrades to ``None`` rather than raising.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match is None:
        return None
    try:
        decoded = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _score_from_dims(
    dims: object,
    *,
    blind_label_map: dict[str, str],
) -> JudgeScore | None:
    # Build a ``JudgeScore`` from one label's dimension dict. Every rubric
    # dimension must be numeric; a missing/non-numeric dimension makes the whole
    # verdict untrustworthy → ``None``. ``reasoning`` is a rubric SCORE here
    # (feeds the mean); ``JudgeScore.reasoning`` free-text is the judge's
    # optional ``notes``, defaulting to empty.
    if not isinstance(dims, dict):
        return None
    values: list[float] = []
    for dimension in _RUBRIC_DIMENSIONS:
        raw = dims.get(dimension)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            return None
        values.append(float(raw))
    notes = dims.get("notes", "")
    return JudgeScore(
        correctness=values[0],
        completeness=values[1],
        relevance=values[2],
        clarity=values[3],
        reasoning=str(notes) if notes else "",
        mean=sum(values) / len(values),
        blind_label_map=dict(blind_label_map),
    )


@runtime_checkable
class Judge(Protocol):
    """Score one task's two arm answers against the gold answer (spec §D15).

    Returns arm-name-keyed ``JudgeScore``s, or ``None`` on judge failure (a bad
    reply, a timed-out judge arm) — the orchestrator discards a task whose judge
    returned ``None``, never admitting an unscored pair. ``RealJudge`` and the
    scripted ``FakeJudge`` both satisfy this (slice-6 contract).
    """

    async def score(
        self,
        *,
        question: str,
        gold: str,
        answers: dict[str, str],
    ) -> dict[str, JudgeScore] | None: ...


@dataclass(frozen=True, slots=True)
class RealJudge:
    """Blind judge backed by a one-shot, tool-less ``ClaudeAgentRunner`` arm.

    Reuses the same subprocess adapter as the arms (``runner``) so the judge's
    spend is measured and counted into the run budget by the orchestrator. The
    judge arm has NO tools and a single turn — it reads the blind prompt and
    returns JSON, it must not go exploring. ``rng_seed`` fixes the A/B shuffle so
    a resumed run reproduces the identical blind prompt.
    """

    runner: AgentRunner
    judge_model: str
    rng_seed: int
    cwd: Path

    async def score(
        self,
        *,
        question: str,
        gold: str,
        answers: dict[str, str],
    ) -> dict[str, JudgeScore] | None:
        """Run the blind judge arm and parse its reply, or ``None`` on failure.

        A timed-out judge arm (runner returns ``None``) or a malformed reply
        both yield ``None`` — the orchestrator drops the pair rather than admit
        an unscored one.
        """
        prompt, label_map = build_judge_prompt(
            question=question, gold=gold, answers=answers, rng_seed=self.rng_seed
        )
        # no_tools=True yields --allowedTools "" — the judge is tool-less so it
        # scores on the two answers + gold alone, never the filesystem. max_turns=1
        # bounds it further; the empty tool surface is what actually enforces it.
        arm = ArmConfig(
            name="judge",
            model=self.judge_model,
            max_turns=_JUDGE_MAX_TURNS,
            no_tools=True,
        )
        metrics: RunMetrics | None = await self.runner.run(
            arm, prompt=prompt, cwd=self.cwd, mcp_config=None
        )
        if metrics is None:
            return None
        return parse_judge_reply(metrics.answer, label_map=label_map)


@dataclass(frozen=True, slots=True)
class FakeJudge:
    """Scripted ``Judge`` double for offline orchestrator tests and consumers.

    Returns the arm-name-keyed ``scores`` verbatim; ``scores=None`` scripts the
    judge-failure path so tests can drive the no-unscored-pair discard without a
    real subprocess (slice-6 contract).
    """

    scores: dict[str, JudgeScore] | None = field(default_factory=dict)

    async def score(
        self,
        *,
        question: str,
        gold: str,
        answers: dict[str, str],
    ) -> dict[str, JudgeScore] | None:
        """Return the scripted scores (or ``None``), ignoring the run inputs."""
        _ = (question, gold, answers)  # scripted double ignores the inputs
        return self.scores
