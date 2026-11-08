"""One arm's settings overlay: ``--baseline-settings`` / ``--candidate-settings``.

Two arms on ONE commit that differ by their ``ask_your_docs`` settings are how
a same-commit variant is measured (the seed variant, a forced turn cap, a
guidance candidate). Each arm may name an overlay file; this module reads it,
splits off the eval-only keys and validates the rest, all at plan time — the
:mod:`before_after_llm_block` precedent, because a typo found by a rollout has
already cost a worktree, a serve child and a token.

The file holds an ``ask_your_docs`` mapping. Product keys — ``architecture``,
``max_agent_turns``, ``seed_search_with_question``, ``llm`` — are validated by
the PRODUCT's ``AskYourDocsConfig`` (``extra="forbid"``), so it alone decides
what an overlay may set: ``guidance_task`` is refused until the product grows
that knob (issue #391), with no eval-side list to forget. Two keys are the
eval's own and never reach it:

- ``guidance_sections`` — a skill document, parsed and validated here with the
  product loader, because the binding re-validates the WHOLE section set on
  every rollout (``_write_candidate_skill``).
- ``task_scaffold`` — how the question is rendered: ``citing`` (the default)
  or ``bare``, the question alone, as the chat page sends it (issue #386).

A ``model`` key, top-level or under ``llm``, is refused: ``--model`` owns it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.task_rendering import DEFAULT_TASK_SCAFFOLD, TaskScaffold

GUIDANCE_SECTIONS_KEY = "guidance_sections"
TASK_SCAFFOLD_KEY = "task_scaffold"
_EVAL_ONLY_KEYS = (GUIDANCE_SECTIONS_KEY, TASK_SCAFFOLD_KEY)
_MODEL_KEY = "model"
_LLM_KEY = "llm"

# What an overlay file may hold, for the error messages and the --help text.
ARM_OVERLAY_SHAPE = (
    "an ask_your_docs mapping: architecture, max_agent_turns, seed_search_with_question, "
    "llm, plus the eval-only guidance_sections (a skill file) and task_scaffold"
)


@dataclass(frozen=True, slots=True)
class ArmOverlay:
    """One arm's validated overlay file: its product keys and its eval-only keys."""

    source: str
    product: Mapping[str, object] = field(default_factory=dict)
    guidance_sections_path: str | None = None
    guidance_sections: Mapping[str, str] = field(default_factory=dict)
    task_scaffold: TaskScaffold = DEFAULT_TASK_SCAFFOLD


def load_arm_overlay(path: Path, *, flag: str) -> ArmOverlay:
    """Read and validate one arm's overlay; ``flag`` names it in every refusal.

    Example:
        >>> load_arm_overlay(Path("seed_on.yaml"), flag="--candidate-settings")  # doctest: +SKIP
        ArmOverlay(source='seed_on.yaml', product={'seed_search_with_question': True}, ...)

    Raises:
        MeasurementPlanError: unreadable, not a mapping, carrying ``model``,
            rejected by the product config, or naming an unusable scaffold or
            guidance file.
    """
    origin = f"{flag} {path}"
    raw = _read_mapping(path, origin)
    product = {key: value for key, value in raw.items() if key not in _EVAL_ONLY_KEYS}
    _refuse_model_keys(product, origin)
    _refuse_invalid_product_keys(product, origin)
    guidance_path = _guidance_path(raw.get(GUIDANCE_SECTIONS_KEY), path)
    return ArmOverlay(
        source=str(path),
        product=product,
        guidance_sections_path=None if guidance_path is None else str(guidance_path),
        guidance_sections=_guidance_sections(guidance_path, origin),
        task_scaffold=_task_scaffold(raw.get(TASK_SCAFFOLD_KEY, DEFAULT_TASK_SCAFFOLD), origin),
    )


def _read_mapping(path: Path, origin: str) -> dict[str, Any]:
    """The overlay's top-level mapping — YAML, which also reads JSON."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MeasurementPlanError(f"{origin}: cannot be read ({exc})") from exc
    if not isinstance(raw, dict):
        got = "an empty file" if raw is None else f"a {type(raw).__name__}"
        raise MeasurementPlanError(f"{origin}: got {got}, expected {ARM_OVERLAY_SHAPE}")
    return raw


def _refuse_model_keys(product: Mapping[str, object], origin: str) -> None:
    """``model`` belongs to ``--model``: one chat model, both arms, one place."""
    llm = product.get(_LLM_KEY)
    for where, block in (("", product), ("llm.", llm if isinstance(llm, Mapping) else {})):
        if _MODEL_KEY in block:
            raise MeasurementPlanError(
                f"{origin} sets {where}{_MODEL_KEY}: {block[_MODEL_KEY]!r}, but the chat "
                f"model comes from --model (both arms share it); expected {ARM_OVERLAY_SHAPE}"
            )


def _refuse_invalid_product_keys(product: Mapping[str, object], origin: str) -> None:
    """Validate through the product model, so a typo costs a plan, not a run."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

    try:
        # pydantic's ValidationError IS a ValueError, so no pydantic import is needed.
        AskYourDocsConfig.model_validate(dict(product))
    except ValueError as exc:
        raise MeasurementPlanError(
            f"{origin} is not a valid overlay ({ARM_OVERLAY_SHAPE}); the product's "
            f"ask_your_docs config refuses it: {' '.join(str(exc).split())}"
        ) from exc


def _task_scaffold(value: object, origin: str) -> TaskScaffold:
    """The overlay's scaffold, any ``TaskScaffold`` (each has its own template); else a refusal."""
    try:
        return TaskScaffold(value)
    except ValueError:
        accepted = ", ".join(str(scaffold) for scaffold in TaskScaffold)
        raise MeasurementPlanError(
            f"{origin}: {TASK_SCAFFOLD_KEY} {value!r} is not a scaffold a before/after arm "
            f"renders; expected one of: {accepted}"
        ) from None


def _guidance_path(value: object, overlay: Path) -> Path | None:
    """The skill file the overlay names, read relative to the overlay's own directory."""
    if value is None:
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else overlay.parent / path


def _guidance_sections(path: Path | None, origin: str) -> dict[str, str]:
    """The skill document's sections, validated by the product loader; ``{}`` for none."""
    if path is None:
        return {}
    from pydocs_mcp.application.description_source import parse_sections
    from pydocs_mcp.harness.core.skill_artifact_loader import (
        SKILL_ARTIFACT_HEADERS,
        parse_skill_artifact,
    )

    try:
        text = path.read_text(encoding="utf-8")
        parse_skill_artifact(text, origin=str(path))
        return parse_sections(text, allowed=SKILL_ARTIFACT_HEADERS)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise MeasurementPlanError(
            f"{origin}: {GUIDANCE_SECTIONS_KEY} {path} is not a usable skill document "
            f"(expected every section of {', '.join(SKILL_ARTIFACT_HEADERS)}): {exc}"
        ) from exc


__all__ = (
    "ARM_OVERLAY_SHAPE",
    "GUIDANCE_SECTIONS_KEY",
    "TASK_SCAFFOLD_KEY",
    "ArmOverlay",
    "load_arm_overlay",
)
