"""The external CLI track's arm identity — half of its ledger resume key.

The external harness resumes on ``(task_id, arm_hash)`` since the run-contract
design §8 deferred item landed: resuming on ``task_id`` alone would silently
reuse answers produced under a DIFFERENT scaffold, guidance, or arm surface —
exactly the reuse the single measurement bump exists to prevent.

BASE install by contract: stdlib + eval-local imports only, no ``pydocs_mcp``
(ADR 0009's 2026-07-27 floor), because ``pydocs-eval-agent-track`` is a base
console script. The delivery map is therefore declared HERE — this harness's
guidance channel is eval-side machinery (``_guidance`` folds the candidate's
sections, ``_command`` carries the block on ``claude --append-system-prompt``),
so there is no product map to read.

Two channels can carry candidate text into a pass, and WHICH one ran is arm
state: the mapped ``append_system_prompt.skill_block`` above, and the legacy
free-form blob ``PairedAgentFitness`` appends to the task prompt
(``task_prompt_suffix``) — off the section map because it is not a section.
The same artifact delivered through different channels is a different arm, so
``external_arm_hash`` folds the channels the pass actually used.
"""

from __future__ import annotations

from pydocs_eval.agent_track._guidance import deliverable_section_keys
from pydocs_eval.agent_track._types import AgentTrackConfig, ArmConfig
from pydocs_eval.arm_identity import arm_fingerprint, delivery_map_hash
from pydocs_eval.task_rendering import TASK_SCAFFOLD_VERSION

# The dotted path naming which harness these rows came from — the external
# CLI runner, the counterpart of an ``arms:`` cell's ``runner:`` key.
EXTERNAL_RUNNER_PATH = "pydocs_eval.agent_track._runner:ClaudeAgentRunner"

# The channel the MAPPED sections ride: the folded skill block on ``claude
# --append-system-prompt`` (``_command`` owns the flag spelling, ``_guidance``
# the fold).
SKILL_BLOCK_CHANNEL = "append_system_prompt.skill_block"

# The channel the legacy free-form blob rides: appended to every arm's rendered
# task prompt (``PairedAgentFitness``'s skill-appending wrapper). Not a section,
# so not in the map below — but still a delivery, so still arm state.
TASK_PROMPT_SUFFIX_CHANNEL = "task_prompt_suffix"

# The literal placeholder standing in for a task name in the map's keys.
_TASK_NAME_PLACEHOLDER = "<task_name>"

# This harness's static section→channel delivery map (design §4). WHY PATTERN
# keys rather than the ask harness's enumerated ones: this module sits on the
# base-install floor (no ``pydocs_mcp``), so it cannot read the product's
# ``TASK_NAMES`` — and a hand-written copy would be a second spelling every
# task-name widening event must hand-edit in lockstep. The ``<task_name>``
# placeholder is therefore the map's documented convention: one key per TIER,
# not one per task. Which task's heads actually fold is arm state carried by
# ``AgentTrackConfig.task_name`` in the cell below. Changing WHERE guidance
# lands changes the arm, so this map's hash folds into every external arm hash.
#
# Built FROM ``deliverable_section_keys`` rather than re-spelling its tiers:
# the map is the hashed statement of what this harness delivers, so narrowing
# or widening the fold must move the map's hash by construction — a hand-kept
# duplicate would leave the hash still while the delivered text changed.
EXTERNAL_DELIVERY_MAP = {
    key: SKILL_BLOCK_CHANNEL for key in deliverable_section_keys(_TASK_NAME_PLACEHOLDER)
}


def external_arm_hash(
    cfg: AgentTrackConfig,
    *,
    dataset: str,
    guidance_fingerprint: str,
    guidance_channels: tuple[str, ...] = (),
) -> str:
    """Return the arm hash for one external-track run configuration.

    Applies the design §6 formula — canonical JSON of the cell, the guidance
    fingerprint, and the delivery-map hash — over this harness's own cell
    spelling: its per-arm CLI knobs, the corpus the arm answers over, and the
    run-level knobs that can move an answer (the task scaffold, the judge model
    and the RNG seed the blind judge shuffles with).

    ``guidance_channels`` names the channels this pass actually delivered on
    (``SKILL_BLOCK_CHANNEL`` / ``TASK_PROMPT_SUFFIX_CHANNEL``); the default
    ``()`` is the bare CLI's no-guidance state. It is folded because the static
    delivery map cannot express it: the same artifact appended to the task
    prompt and folded onto ``--append-system-prompt`` is the same fingerprint
    reaching the model two different ways — two arms, not one resumable one.

    Example:
        >>> external_arm_hash(
        ...     AgentTrackConfig(), dataset="swe-qa-pro", guidance_fingerprint="abc"
        ... )[:8]
        '0576f4de'
    """
    return arm_fingerprint(
        cell=_external_cell(cfg, dataset=dataset, guidance_channels=guidance_channels),
        guidance_fingerprint=guidance_fingerprint,
        delivery_map_hash=delivery_map_hash(EXTERNAL_DELIVERY_MAP),
    )


def _external_cell(
    cfg: AgentTrackConfig, *, dataset: str, guidance_channels: tuple[str, ...] = ()
) -> dict[str, object]:
    """The external harness's arm cell, canonicalized for hashing.

    Carries the five things that decide what an answer means: which runner,
    which arm surfaces, which corpus (the normative cell's ``dataset`` key —
    two datasets share the default ledger path, so omitting it would let one
    corpus's answers suppress another's), which channels carried the candidate
    text, and the run-level ``settings``.

    ``guidance_channels`` is HOW the text reached the agent, the one delivery
    fact the static ``EXTERNAL_DELIVERY_MAP`` cannot carry (the map is a
    constant; the channel used is per pass). Without it, a candidate appended
    to the task prompt and the SAME candidate folded onto
    ``--append-system-prompt`` mint one hash and resume each other's rows.

    ``settings`` folds ``task_name`` because it SELECTS which task head and
    harness task head of the candidate document reach the agent
    (``_guidance.deliverable_section_keys``): the same candidate delivered
    under two task names is two different texts, and the guidance fingerprint
    — the whole document — cannot tell them apart.

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
        "guidance_channels": list(guidance_channels),
        "settings": {
            "judge_model": cfg.judge_model,
            "rng_seed": cfg.rng_seed,
            "scaffold": TASK_SCAFFOLD_VERSION,
            "task_name": cfg.task_name,
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
