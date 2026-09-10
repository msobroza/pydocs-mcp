"""Paired agent-efficiency harness (spec §D15).

Two-arm harness: the same headless agent answers SWE-QA-Pro questions with
bare file tools (arm A) vs with the pydocs-mcp MCP server attached (arm B);
a blind LLM judge scores answer quality; per-task-paired aggregation reports
cost / tool-call / file-read / token deltas at answer-quality parity.

Manual and expensive by design — never CI. Everything testable is pure or
Protocol-seamed; the one expensive path sits behind a subprocess adapter and
hard guardrails.

This ``__init__`` IS the package's public surface: downstream consumers
(``optimize/_agent_track_binding.py`` is the canonical one) import the names
below rather than reaching into the underscore submodules. The ``DEFAULT_*``
constants are the single-source run defaults other layers mirror.
"""

from __future__ import annotations

from pydocs_eval.agent_track._arm import (
    SKILL_BLOCK_CHANNEL,
    TASK_PROMPT_SUFFIX_CHANNEL,
    external_arm_hash,
)
from pydocs_eval.agent_track._command import task_prompt
from pydocs_eval.agent_track._guidance import (
    ExternalUndeliverableGuidanceError,
    deliverable_section_keys,
    fold_guidance,
)
from pydocs_eval.agent_track._injection import (
    INJECTED_CONTEXT_MARKER,
    assemble_prompt,
)
from pydocs_eval.agent_track._judge import FakeJudge, Judge
from pydocs_eval.agent_track._runner import AgentRunner, FakeAgentRunner
from pydocs_eval.agent_track._types import (
    DEFAULT_MAX_TURNS,
    DEFAULT_MODEL,
    DEFAULT_RNG_SEED,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    AgentTrackConfig,
    ArmConfig,
    JudgeScore,
    PairResult,
    RunMetrics,
)
from pydocs_eval.agent_track.orchestrator import run_agent_track

__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MODEL",
    "DEFAULT_RNG_SEED",
    "DEFAULT_TASK_TIMEOUT_SECONDS",
    "INJECTED_CONTEXT_MARKER",
    "SKILL_BLOCK_CHANNEL",
    "TASK_PROMPT_SUFFIX_CHANNEL",
    "AgentRunner",
    "AgentTrackConfig",
    "ArmConfig",
    "ExternalUndeliverableGuidanceError",
    "FakeAgentRunner",
    "FakeJudge",
    "Judge",
    "JudgeScore",
    "PairResult",
    "RunMetrics",
    "assemble_prompt",
    "deliverable_section_keys",
    "external_arm_hash",
    "fold_guidance",
    "run_agent_track",
    "task_prompt",
]
