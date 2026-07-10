"""The ``critique_refine`` optimizer — an LLM critique-and-rewrite loop (spec §D4).

Each trial: build a critique prompt from the current best artifact's rendered
text plus a summary of its last ``FitnessReport.components``, ask a
``CritiqueClient`` for a single full-replacement rewrite in one fenced block,
extract the block, and run the artifact's OWN ``validate()`` firewall on it. A
candidate that fails ``validate()`` is recorded as a ``Trial`` with its
violations and dropped WITHOUT ever calling fitness — the constraint firewall
(spec §D2/§D3) means an invalid rewrite costs nothing to score. A valid
candidate is scored on the ``train`` split and keep-best keeps the highest
scorer (the seed is the initial best, so a search that never beats it returns
the seed).

Acceptance is deliberately left ``False`` here: the orchestrator
(``run_optimization``) owns the held-out D4 acceptance gate — this optimizer
only proposes a best-on-train candidate. Everything expensive is injected behind
the ``CritiqueClient`` Protocol, so the whole loop runs offline with the scripted
``FakeCritiqueClient`` (no subprocess, no live LLM — slice-6 contract). The real
client, ``ClaudeCliCritiqueClient``, reuses the binding's ``AgentRunner``
one-shot tool-less arm — the SAME spend path and answer parser as the blind
judge, so no second LLM client stack is introduced.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_eval.optimize._agent_track_binding import AgentRunner, ArmConfig
from pydocs_eval.optimize._types import (
    FitnessReport,
    OptimizationBudget,
    OptimizationResult,
    Provenance,
    Trial,
)
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.protocols import FitnessFunction, OptimizableArtifact
from pydocs_eval.optimize.registries import optimizer_registry

# WHY: single source of truth for the critique arm's one-turn tool-less profile —
# it reads the current artifact + its component summary and returns a rewrite; it
# must not go exploring, exactly like the blind judge arm (``RealJudge``).
_CRITIQUE_MAX_TURNS = 1
_CRITIQUE_ARM_NAME = "critique"

# The optimizer name recorded in synthesized provenance when the caller hands a
# bare seed (the orchestrator overrides provenance from its own ``SeedView``).
_OPTIMIZER_NAME = "critique_refine"

# Pulls the FIRST fenced block out of a reply. Language tag (```md) optional; the
# body is everything up to the closing fence. A reply with no fence falls back to
# its whole text (``extract_rewrite``), so a terse client still round-trips.
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class CritiqueReply:
    """One critique client completion: the reply text and what it cost (spec §D4)."""

    text: str
    cost_usd: float


@runtime_checkable
class CritiqueClient(Protocol):
    """A one-shot completion seam: prompt in, ``CritiqueReply`` out (spec §D4)."""

    async def complete(self, prompt: str) -> CritiqueReply: ...


@dataclass
class FakeCritiqueClient:
    """Scripted ``CritiqueClient`` double for offline optimizer tests + dry-run.

    Returns ``replies`` in order, one per ``complete`` call; running past the end
    is a scripting bug and raises so a test that under-scripts fails loud rather
    than hanging. Exported for the dry-run preflight, which constructs
    ``critique_refine`` with an empty script (it never actually completes).
    """

    replies: list[CritiqueReply]
    _cursor: int = 0

    async def complete(self, prompt: str) -> CritiqueReply:
        """Return the next scripted reply, ignoring ``prompt`` (scripted double)."""
        _ = prompt
        if self._cursor >= len(self.replies):
            raise AssertionError(
                f"FakeCritiqueClient exhausted: {len(self.replies)} reply(ies) scripted"
            )
        reply = self.replies[self._cursor]
        self._cursor += 1
        return reply


@dataclass(frozen=True, slots=True)
class ClaudeCliCritiqueClient:
    """Real ``CritiqueClient`` backed by a one-shot, tool-less ``AgentRunner`` arm.

    Reuses the SAME subprocess adapter and answer field as the blind judge
    (``RealJudge``): a single-turn, no-tools arm reads the prompt and returns the
    rewrite as its answer, and its cost is the arm's ``cost_usd``. No second LLM
    client stack is introduced — the binding's ``AgentRunner`` is the only spend
    path. A timed-out arm (runner returns ``None``) yields an empty, zero-cost
    reply; the optimizer's ``validate()`` firewall then discards the empty rewrite.
    """

    runner: AgentRunner
    model: str
    cwd: Path

    async def complete(self, prompt: str) -> CritiqueReply:
        """Run the one-shot critique arm and return its answer as a reply."""
        arm = ArmConfig(
            name=_CRITIQUE_ARM_NAME,
            model=self.model,
            max_turns=_CRITIQUE_MAX_TURNS,
            no_tools=True,
        )
        metrics = await self.runner.run(arm, prompt=prompt, cwd=self.cwd, mcp_config=None)
        if metrics is None:
            return CritiqueReply(text="", cost_usd=0.0)
        return CritiqueReply(text=metrics.answer, cost_usd=metrics.cost_usd)


@optimizer_registry.register("critique_refine")
@dataclass(frozen=True, slots=True)
class CritiqueRefineOptimizer:
    """Critique-and-rewrite optimizer with a ``validate()`` firewall (spec §D4)."""

    client: CritiqueClient
    fitness: FitnessFunction
    name: str = _OPTIMIZER_NAME

    async def optimize(
        self,
        seed: object,
        ladder: FitnessLadder,
        budget: OptimizationBudget,
    ) -> OptimizationResult:
        """Run up to ``budget.max_trials`` critique rounds; return the best-on-train.

        ``seed`` is either a bare ``OptimizableArtifact`` (the standalone path the
        optimizer tests drive) or the orchestrator's ``SeedView`` (which carries
        the artifact on ``.seed`` plus the run provenance). The initial best is the
        seed scored once; each round asks the client for a rewrite, firewalls it
        through ``validate()``, and — only when valid — scores it and keeps the
        higher of (best, candidate). Acceptance stays ``False``: the orchestrator
        owns the held-out D4 gate.
        """
        artifact = _seed_artifact(seed)
        best = _Best(artifact=artifact, report=await self._score(artifact))
        trials: list[Trial] = []
        for _ in range(budget.max_trials):
            best = await self._one_round(artifact, best, trials)
        return _result(best, trials, _provenance(seed, best.artifact))

    async def _one_round(
        self, seed: OptimizableArtifact, best: _Best, trials: list[Trial]
    ) -> _Best:
        """One critique round: rewrite → firewall → (score + keep-best) or discard."""
        reply = await self.client.complete(_critique_prompt(best))
        candidate = seed.with_content(_extract_rewrite(reply.text))
        violations = candidate.validate()
        if violations:
            # Constraint firewall (spec §D2/§D3): an invalid rewrite is recorded
            # and dropped WITHOUT paying to score it.
            trials.append(_violation_trial(candidate, reply.cost_usd, violations))
            return best
        report = await self._score(candidate)
        trials.append(_scored_trial(candidate, report))
        return (
            _Best(artifact=candidate, report=report) if report.score > best.report.score else best
        )

    async def _score(self, artifact: OptimizableArtifact) -> FitnessReport:
        # Train-split scoring only — the optimizer never touches holdout (the
        # orchestrator's train firewall enforces this even for the real fitness).
        return await self.fitness.evaluate(artifact, split="train")


@dataclass(frozen=True, slots=True)
class _Best:
    """The running best candidate + its last fitness report (keep-best state)."""

    artifact: OptimizableArtifact
    report: FitnessReport


def _seed_artifact(seed: object) -> OptimizableArtifact:
    """Unwrap the artifact whether ``seed`` is bare or an orchestrator ``SeedView``."""
    # The orchestrator hands a ``SeedView`` (``.seed`` is the artifact); the
    # standalone optimizer tests hand the artifact directly. ``getattr`` bridges
    # both without importing the orchestrator (which would be a forward cycle).
    return getattr(seed, "seed", seed)  # type: ignore[return-value]


def _critique_prompt(best: _Best) -> str:
    """Render the critique-and-rewrite instruction for the current best artifact."""
    return (
        "You are improving a text artifact used to configure a code-search agent.\n"
        "Here is the current version and how it scored on efficiency metrics.\n\n"
        f"--- CURRENT ARTIFACT ---\n{best.artifact.render()}\n--- END ARTIFACT ---\n\n"
        f"Fitness summary: {_components_summary(best.report.components)}\n\n"
        "Propose a better full-replacement version. Keep every required section and "
        "stay within the documented token budgets. Respond with the COMPLETE "
        "replacement document in ONE fenced code block and nothing else."
    )


def _components_summary(components: Mapping[str, float]) -> str:
    """A compact, deterministic one-line summary of the fitness components."""
    if not components:
        return "(no components recorded)"
    return ", ".join(f"{key}={value:.4g}" for key, value in sorted(components.items()))


def _extract_rewrite(text: str) -> str:
    """Return the first fenced block's body, or the whole reply when unfenced."""
    match = _FENCE_RE.search(text)
    if match is None:
        return text
    # WHY: render_delimited appends exactly one trailing newline per section, so
    # a fenced block that captured that newline before the closing fence would
    # otherwise inject a phantom blank line; strip the single trailing newline the
    # fence grammar adds, leaving the artifact's own content intact.
    body = match.group(1)
    return body[:-1] if body.endswith("\n") else body


def _violation_trial(
    candidate: OptimizableArtifact, cost_usd: float, violations: tuple[str, ...]
) -> Trial:
    """A trial for a firewalled candidate: no rung score, its violations recorded."""
    return Trial(
        fingerprint=candidate.fingerprint,
        rung_scores=(),
        cost_usd=cost_usd,
        violations=violations,
    )


def _scored_trial(candidate: OptimizableArtifact, report: FitnessReport) -> Trial:
    """A trial for a valid candidate: its single train-rung score, no violations."""
    return Trial(
        fingerprint=candidate.fingerprint,
        rung_scores=(report.score,),
        cost_usd=report.cost_usd,
        violations=(),
    )


def _result(best: _Best, trials: list[Trial], provenance: Provenance) -> OptimizationResult:
    """Assemble the optimizer's proposal; acceptance is the orchestrator's call."""
    return OptimizationResult(
        best=best.artifact,
        accepted=False,  # the orchestrator owns the held-out D4 acceptance gate
        trials=tuple(trials),
        total_usd=sum(trial.cost_usd for trial in trials),
        provenance=provenance,
    )


def _provenance(seed: object, best: OptimizableArtifact) -> Provenance:
    """Reuse the orchestrator's provenance, or synthesize one for a bare seed."""
    existing = getattr(seed, "provenance", None)
    if existing is not None:
        return existing
    return Provenance(
        seed_fingerprint=best.fingerprint,
        dataset_revision="",
        model_ids=(),
        optimizer=_OPTIMIZER_NAME,
    )
