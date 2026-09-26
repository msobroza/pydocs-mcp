"""Where a before/after arm's ``ask_your_docs.llm`` block comes from: ``--llm-block``.

Model settings — ``provider`` and every ``params.*`` key — are ARM-side by
contract. The product's eval binding refuses a block sourced from the serving
YAML (or from the environment) so that an arm is deterministic: what a run
measures must be decided by the run, not by whatever file the serve child
happens to be pointed at. A serving file that carried them therefore failed
EVERY rollout, which is what this module's two halves prevent:

- :func:`load_arm_llm_block` reads the operator's block file, refuses a ``model``
  key (``--model`` owns that, and both arms share it) and validates the rest
  through the product's own ``LlmConnectionConfig`` — so a typo fails at plan
  time, before a worktree, a serve child or a token.
- :func:`refuse_file_sourced_model_settings` makes the binding's own refusal
  happen at plan time too, by asking it the same question a rollout asks.

Both raise :class:`~pydocs_eval.campaign.before_after.MeasurementPlanError`, the
command's "the operator fixes the input, not a traceback" channel.

One question is asked of the block at run time rather than plan time:
:func:`block_turns_thinking_off`, which the outcome taxonomy's starved-reply rule
reads (``trajectory.ask_outcome.run_evidence``).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from pydocs_eval.campaign.before_after import ArmLlmBlock, MeasurementPlanError

# The one key the block may NOT carry: the chat model is a run-level flag both
# arms share, folded over the block by the harness's launch tier.
_MODEL_KEY = "model"

# What a block file may hold, for the error messages (the product model owns the
# real schema; this is the operator-facing summary of it).
_BLOCK_SHAPE = "base_url, auth, provider, params, parallel_tool_calls"

# Placeholders for the plan-time probe below: this settings object is never run,
# and the block source is decided by ``harness.llm`` and ``pydocs_config`` alone.
_PROBE_UNUSED = ""

# Mirrors ``pydocs_mcp.retrieval.config.ask_your_docs_params_models.ThinkingLevel.OFF``,
# the ``params.thinking`` value that turns thinking off. Mirrored and not imported:
# an arm reads its block while an OLDER product may be the one importable, and a
# product that predates the thinking control has no such enum. The parity test
# pins the spelling against the product that has it.
ASK_THINKING_OFF = "off"


def block_turns_thinking_off(settings: Mapping[str, object] | None) -> bool:
    """Whether a pinned ``ask_your_docs.llm`` block turns the model's thinking off.

    ``False`` is what a YAML 1.1 loader makes of a bare ``thinking: off``, and the
    product accepts both spellings. No block, or no ``params.thinking``, leaves
    the endpoint's default — thinking allowed.

    Example:
        >>> block_turns_thinking_off({"params": {"thinking": "off"}})
        True
    """
    params = (settings or {}).get("params")
    thinking = params.get("thinking") if isinstance(params, Mapping) else None
    return thinking is False or thinking == ASK_THINKING_OFF


def load_arm_llm_block(path: Path) -> ArmLlmBlock:
    """Read and validate one arm's ``ask_your_docs.llm`` block from YAML or JSON.

    Example:
        >>> block = load_arm_llm_block(Path("arm_llm.yaml"))  # doctest: +SKIP
        >>> block.settings["params"]["top_p"]  # doctest: +SKIP
        0.95

    Raises:
        MeasurementPlanError: unreadable, not a mapping, carrying ``model``, or
            rejected by the product's ``LlmConnectionConfig``.
    """
    settings = _read_mapping(path)
    _refuse_block_model(settings, path)
    _refuse_invalid_block(settings, path)
    return ArmLlmBlock(source=str(path), settings=settings)


def refuse_file_sourced_model_settings(config_path: Path) -> None:
    """Refuse, at PLAN time, a serving file carrying settings only an arm may set.

    This is the ROLLOUT's own question — ``connection_block_for_binding`` on an
    arm that pinned no block — so a config which would raise on every rollout
    raises here instead, once, with the ``--llm-block`` pointer attached. Without
    it the refusal surfaced only as a raised rollout, and the run halted at zero
    answered tasks with no cause recorded anywhere.

    Raises:
        MeasurementPlanError: the file sets ``ask_your_docs.llm.params.*`` or a
            non-default ``provider``.
    """
    from pydocs_mcp.harness.ask_your_docs.binding import AskYourDocsRunnerSettings
    from pydocs_mcp.harness.ask_your_docs.binding_llm_block import (
        ArmParamsSourceError,
        connection_block_for_binding,
    )

    probe = AskYourDocsRunnerSettings(
        workspace=_PROBE_UNUSED,
        model=_PROBE_UNUSED,
        trace_root=_PROBE_UNUSED,
        pydocs_config=str(config_path),
    )
    try:
        connection_block_for_binding(probe)
    except ArmParamsSourceError as exc:
        raise MeasurementPlanError(
            f"{exc}; for this command that block is a file passed to --llm-block "
            f"(it reaches both arms identically), not a key in --config {config_path}"
        ) from exc


def _read_mapping(path: Path) -> dict[str, Any]:
    """The block file's top-level mapping — YAML, which also reads JSON."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MeasurementPlanError(f"--llm-block {path}: cannot be read ({exc})") from exc
    if not isinstance(raw, dict):
        got = "an empty file" if raw is None else f"a {type(raw).__name__}"
        raise MeasurementPlanError(
            f"--llm-block {path}: got {got}, expected an ask_your_docs.llm block ({_BLOCK_SHAPE})"
        )
    return raw


def _refuse_block_model(settings: dict[str, Any], path: Path) -> None:
    """``model`` belongs to ``--model``: one value, both arms, one place."""
    if _MODEL_KEY in settings:
        raise MeasurementPlanError(
            f"--llm-block {path} sets {_MODEL_KEY}: {settings[_MODEL_KEY]!r}, but the chat "
            f"model comes from --model (both arms share it); expected only {_BLOCK_SHAPE}"
        )


def _refuse_invalid_block(settings: dict[str, Any], path: Path) -> None:
    """Validate through the product model, so a typo costs a plan, not a run."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    try:
        # pydantic's ValidationError IS a ValueError, so no pydantic import is needed.
        LlmConnectionConfig.model_validate({**settings, _MODEL_KEY: None})
    except ValueError as exc:
        raise MeasurementPlanError(
            f"--llm-block {path} is not a valid ask_your_docs.llm block ({_BLOCK_SHAPE}): {exc}"
        ) from exc


__all__ = (
    "ASK_THINKING_OFF",
    "block_turns_thinking_off",
    "load_arm_llm_block",
    "refuse_file_sourced_model_settings",
)
