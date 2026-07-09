"""Agent-track value objects + run config (spec §D15).

Frozen slotted value objects for the paired agent-efficiency harness: what one
arm produced (``RunMetrics``), how the blind judge scored a pair
(``JudgeScore``), the per-arm CLI knobs (``ArmConfig``), the whole-run
guardrail config (``AgentTrackConfig``), and one admitted task
(``PairResult`` — the no-half-pairs rule is enforced in ``__post_init__``).

``AgentRunResult`` is the raw parse view the subprocess adapter builds before
deriving a ``RunMetrics``; it is kept separate so the parse layer (per-event
stream + final result JSON) has no scoring semantics baked in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Single source of truth for every guardrail / default (§"Default values").
# Bumping a default touches one line here, not scattered literals.
_DEFAULT_MODEL = "claude-sonnet-5"
_DEFAULT_MAX_TURNS = 40
_DEFAULT_JUDGE_MODEL = _DEFAULT_MODEL  # same pinned family as the arms by default
_DEFAULT_MAX_TASKS = 48
_DEFAULT_MAX_USD = 25.0
_DEFAULT_TASK_TIMEOUT_SECONDS = 900.0
_DEFAULT_RNG_SEED = 0  # slice-6 contract: one fixed seed for deterministic comparisons
_DEFAULT_OUTPUT_DIR = Path("~/.cache/pydocs-mcp/agent-track").expanduser()


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """What one arm produced on one task: the scoring-relevant view merged
    from the CLI's per-event stream and final result JSON. ``answer`` is the
    final text the judge scores; the rest are the efficiency deltas the report
    aggregates at answer-quality parity."""

    cost_usd: float
    wall_seconds: float
    turns: int
    tool_calls: int
    distinct_files_read: int
    cache_read_tokens: int
    cache_write_tokens: int
    answer: str


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Raw parse view of one CLI run before scoring semantics are applied.

    The subprocess adapter fills this from ``parse_stream_events`` +
    ``parse_result_json`` and derives a ``RunMetrics`` from it. Kept distinct
    from ``RunMetrics`` so the parse layer carries no judge/report concerns.
    """

    answer: str
    cost_usd: float
    wall_seconds: float
    turns: int
    tool_calls: int
    files_read: tuple[str, ...] = ()
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class JudgeScore:
    """The blind judge's verdict for one arm's answer. Five 0–10 rubric
    dimensions plus their ``mean``; ``reasoning`` is the judge's free-text
    justification; ``blind_label_map`` records which shuffled label (A/B) the
    arm was shown as, so a scored pair is auditable back to its arms."""

    correctness: float
    completeness: float
    relevance: float
    clarity: float
    reasoning: str
    mean: float
    blind_label_map: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArmConfig:
    """One arm's CLI knobs. ``mcp`` is the arm's defining difference between the
    two measured arms: the bare arm runs with file tools only; the indexed arm
    attaches the pydocs-mcp MCP server. ``no_tools`` is a THIRD profile the blind
    judge uses — an empty tool surface (``--allowedTools ""``) so it scores on the
    two answers + gold alone and cannot go exploring the filesystem. ``no_tools``
    takes precedence over ``mcp`` (a tool-less arm has no MCP either)."""

    name: str
    model: str = _DEFAULT_MODEL
    max_turns: int = _DEFAULT_MAX_TURNS
    mcp: bool = False
    no_tools: bool = False

    def __post_init__(self) -> None:
        # A tool-less arm attaching an MCP server is contradictory — the empty
        # tool surface is the whole point of the judge arm. Fail loud at the
        # boundary rather than silently emit a surface that grants MCP tools.
        if self.no_tools and self.mcp:
            raise ValueError(
                f"arm {self.name!r} sets both no_tools and mcp: a tool-less arm "
                "cannot attach an MCP server (no_tools takes precedence)"
            )


def _default_arms() -> tuple[ArmConfig, ArmConfig]:
    # Two-arm harness (spec §D15): identical model/turns, split only on ``mcp``.
    return (
        ArmConfig(name="bare", mcp=False),
        ArmConfig(name="indexed", mcp=True),
    )


@dataclass(frozen=True, slots=True)
class AgentTrackConfig:
    """Whole-run guardrail config. ``max_tasks`` / ``max_usd`` /
    ``task_timeout_seconds`` bound spend and time; ``rng_seed`` fixes
    judge-label randomization and report bootstrap so optimization
    comparisons are as deterministic as the harness allows (slice-6
    contract)."""

    arms: tuple[ArmConfig, ArmConfig] = field(default_factory=_default_arms)
    judge_model: str = _DEFAULT_JUDGE_MODEL
    max_tasks: int = _DEFAULT_MAX_TASKS
    max_usd: float = _DEFAULT_MAX_USD
    task_timeout_seconds: float = _DEFAULT_TASK_TIMEOUT_SECONDS
    rng_seed: int = _DEFAULT_RNG_SEED
    output_dir: Path = _DEFAULT_OUTPUT_DIR


@dataclass(frozen=True, slots=True)
class PairResult:
    """One admitted task: both arms' metrics plus the judge's per-arm scores.

    The no-half-pairs rule (spec §D15) lives in the type — a task counts only
    when both arms completed inside budget, so construction with a missing arm
    is a caller bug and fails loud at the boundary rather than silently
    skewing the paired aggregation.
    """

    task_id: str
    qa_type: str
    bare: RunMetrics | None
    indexed: RunMetrics | None
    judge: JudgeScore | None

    def __post_init__(self) -> None:
        if self.bare is None or self.indexed is None:
            raise ValueError(
                "PairResult requires both arms: no half-pairs are admitted "
                f"(task_id={self.task_id!r}, bare={self.bare!r}, "
                f"indexed={self.indexed!r})"
            )
