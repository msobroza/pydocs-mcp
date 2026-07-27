"""The external CLI track's arm identity — half of its ledger resume key.

The external harness resumes on ``(task_id, arm_hash)`` since the run-contract
design §8 deferred item landed: resuming on ``task_id`` alone would silently
reuse answers produced under a DIFFERENT scaffold, guidance, or arm surface —
exactly the reuse the single measurement bump exists to prevent.

BASE install by contract: stdlib + eval-local imports only, no ``pydocs_mcp``
(ADR 0009's 2026-07-27 floor), because ``pydocs-eval-agent-track`` is a base
console script. The delivery map is therefore declared HERE — this harness's
guidance channel is eval-side machinery (the fitness appends the candidate
skill to every arm's task prompt), so there is no product map to read.
"""

from __future__ import annotations

from pydocs_eval.agent_track._types import AgentTrackConfig, ArmConfig
from pydocs_eval.arm_identity import arm_fingerprint, delivery_map_hash
from pydocs_eval.task_rendering import TASK_SCAFFOLD_VERSION

# The dotted path naming which harness these rows came from — the external
# CLI runner, the counterpart of an ``arms:`` cell's ``runner:`` key.
EXTERNAL_RUNNER_PATH = "pydocs_eval.agent_track._runner:ClaudeAgentRunner"

# This harness's static section→channel delivery map (design §4): candidate
# guidance reaches BOTH arms as a suffix on the rendered task prompt
# (``PairedAgentFitness``'s skill-appending runner, byte-identical to
# ``task_prompt(question, skill=…)``). Changing WHERE guidance lands changes
# the arm, so this map's hash folds into every external arm hash.
EXTERNAL_DELIVERY_MAP = {"guidance": "task_prompt_suffix"}


def external_arm_hash(cfg: AgentTrackConfig, *, dataset: str, guidance_fingerprint: str) -> str:
    """Return the arm hash for one external-track run configuration.

    Applies the design §6 formula — canonical JSON of the cell, the guidance
    fingerprint, and the delivery-map hash — over this harness's own cell
    spelling: its per-arm CLI knobs, the corpus the arm answers over, and the
    run-level knobs that can move an answer (the task scaffold, the judge model
    and the RNG seed the blind judge shuffles with).

    Example:
        >>> external_arm_hash(
        ...     AgentTrackConfig(), dataset="swe-qa-pro", guidance_fingerprint="abc"
        ... )[:8]
        'f5b2649c'
    """
    return arm_fingerprint(
        cell=_external_cell(cfg, dataset=dataset),
        guidance_fingerprint=guidance_fingerprint,
        delivery_map_hash=delivery_map_hash(EXTERNAL_DELIVERY_MAP),
    )


def _external_cell(cfg: AgentTrackConfig, *, dataset: str) -> dict[str, object]:
    """The external harness's arm cell, canonicalized for hashing.

    Carries the four things that decide what an answer means: which runner,
    which arm surfaces, which corpus (the normative cell's ``dataset`` key —
    two datasets share the default ledger path, so omitting it would let one
    corpus's answers suppress another's), and the run-level ``settings``.

    ``settings`` folds ``TASK_SCAFFOLD_VERSION`` because the scaffold IS the
    instructions the answer was produced under — the §8 silent-reuse failure
    the widened resume key exists to prevent is precisely "the scaffold moved,
    the ledger did not". It is the same fold ``ask_binding_identity`` applies
    on the in-process path, so a scaffold edit re-keys both tracks together.

    Deliberately NOT the whole ``AgentTrackConfig``: budget guardrails
    (``max_tasks`` / ``max_usd``) and the output dir bound a RUN, they do not
    change what an arm measures, so folding them in would re-key the ledger on
    every budget tweak and force a needless re-spend.
    """
    return {
        "runner": EXTERNAL_RUNNER_PATH,
        "arms": [_arm_config_cell(arm) for arm in cfg.arms],
        "dataset": dataset,
        "settings": {
            "judge_model": cfg.judge_model,
            "rng_seed": cfg.rng_seed,
            "scaffold": TASK_SCAFFOLD_VERSION,
            "task_timeout_seconds": cfg.task_timeout_seconds,
        },
    }


def _arm_config_cell(arm: ArmConfig) -> dict[str, object]:
    """One ``ArmConfig`` as an order-stable mapping (the ``_arm_to_dict`` shape)."""
    return {
        "name": arm.name,
        "model": arm.model,
        "max_turns": arm.max_turns,
        "mcp": arm.mcp,
        "no_tools": arm.no_tools,
        "tools": list(arm.tools) if arm.tools is not None else None,
    }
