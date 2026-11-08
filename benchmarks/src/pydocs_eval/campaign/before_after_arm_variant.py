"""What one arm runs under beyond its commit, and the digest that names it.

A plan's two arms each get an :class:`ArmVariant`: the arm defaults (the pinned
architecture, the serving file's turn budget, the shared ``--llm-block``, the
citing scaffold), with that arm's overlay (``before_after_arm_settings``)
applied on top. The variant is what an arm's settings record, what the plan
prints as a sorted diff, and — through :attr:`ArmVariant.digest` — what
``--reuse-baseline`` matches and ``--allow-identical`` guards.

**The digest folds effective settings, not file bytes.** Restating a default
(``seed_search_with_question: false``) or reordering keys moves nothing; the
guidance file folds by its TEXT, not its path. ``sent_settings_fingerprint``
(the product's per-rollout identity) is not reused: it is ``None`` for a block
without ``params``, and it never sees the guidance or the scaffold.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydocs_eval.campaign.before_after import (
    SHORT_SHA_CHARS,
    ArmLlmBlock,
    ArmRole,
    MeasurementPlanError,
    flat_settings,
)
from pydocs_eval.campaign.before_after_arm import ARM_ARCHITECTURE
from pydocs_eval.campaign.before_after_arm_settings import (
    GUIDANCE_SECTIONS_KEY,
    TASK_SCAFFOLD_KEY,
    ArmOverlay,
)
from pydocs_eval.task_rendering import DEFAULT_TASK_SCAFFOLD, TaskScaffold, task_scaffold_version

# The overlay keys an arm carries on fields of its own; every OTHER product key
# rides the runner settings' ``harness`` mapping (``harness_block``).
_ARCHITECTURE_KEY = "architecture"
_TURNS_KEY = "max_agent_turns"
_LLM_KEY = "llm"
_OWN_FIELD_KEYS = (_ARCHITECTURE_KEY, _TURNS_KEY, _LLM_KEY)

# How a plan line spells a key one arm leaves at its default.
_UNSET = "(default)"

# A diff row: the dotted key, the baseline's value, the candidate's (None = unset).
SettingChange = tuple[str, object, object]


@dataclass(frozen=True, slots=True)
class ArmVariant:
    """One arm's effective settings beyond its commit — the defaults plus its overlay."""

    max_agent_turns: int
    architecture: str = ARM_ARCHITECTURE
    llm_block: Mapping[str, object] | None = None
    # The overlay's other product keys AS WRITTEN, sent beside harness.llm: the
    # arm's own product decides their defaults, so an explicit key is always sent.
    harness_block: Mapping[str, object] = field(default_factory=dict)
    guidance_sections_path: str | None = None
    guidance_sections: Mapping[str, str] = field(default_factory=dict)
    task_scaffold: TaskScaffold = DEFAULT_TASK_SCAFFOLD
    # The overlay file, or None for an arm that names none.
    source: str | None = None

    @property
    def digest(self) -> str:
        """SHA-256 of the effective settings plus the scaffold's VERSION.

        Example:
            >>> len(ArmVariant(max_agent_turns=12).digest)
            64
        """
        canonical = json.dumps(self._digested(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _digested(self) -> dict[str, object]:
        return {
            _ARCHITECTURE_KEY: self.architecture,
            _TURNS_KEY: self.max_agent_turns,
            _LLM_KEY: dict(self.llm_block or {}),
            "harness": harness_keys_off_default(self.harness_block),
            GUIDANCE_SECTIONS_KEY: dict(self.guidance_sections),
            TASK_SCAFFOLD_KEY: task_scaffold_version(self.task_scaffold),
        }

    def flat(self) -> dict[str, object]:
        """The settings as the overlay spells them, flattened to dotted keys."""
        nested = {
            _ARCHITECTURE_KEY: self.architecture,
            _TURNS_KEY: self.max_agent_turns,
            _LLM_KEY: dict(self.llm_block or {}),
            **harness_keys_off_default(self.harness_block),
            GUIDANCE_SECTIONS_KEY: self.guidance_sections_path,
            TASK_SCAFFOLD_KEY: str(self.task_scaffold),
        }
        return {key: value for key, value in flat_settings(nested) if value is not None}

    def arm_settings_fields(self) -> dict[str, Any]:
        """The ``ArmSettings`` fields this variant decides, digest included."""
        return {
            "architecture": self.architecture,
            "max_agent_turns": self.max_agent_turns,
            "llm_block": None if self.llm_block is None else dict(self.llm_block),
            "harness_block": dict(self.harness_block),
            "guidance_sections_path": self.guidance_sections_path,
            "guidance_sections": dict(self.guidance_sections),
            "task_scaffold": self.task_scaffold,
            "settings_digest": self.digest,
        }


def variant_of(
    overlay: ArmOverlay | None, *, llm_block: ArmLlmBlock | None, serving_turns: int
) -> ArmVariant:
    """One arm's variant: the arm defaults with ``overlay`` applied on top.

    Raises:
        MeasurementPlanError: the ``--llm-block`` file merged under the
            overlay's ``llm`` is a block the product refuses.
    """
    if overlay is None:
        settings = None if llm_block is None else dict(llm_block.settings)
        return ArmVariant(max_agent_turns=serving_turns, llm_block=settings)
    product = overlay.product
    return ArmVariant(
        max_agent_turns=int(product.get(_TURNS_KEY, serving_turns)),  # type: ignore[call-overload]
        architecture=str(product.get(_ARCHITECTURE_KEY, ARM_ARCHITECTURE)),
        llm_block=_merged_llm(overlay, llm_block),
        harness_block={key: value for key, value in product.items() if key not in _OWN_FIELD_KEYS},
        guidance_sections_path=overlay.guidance_sections_path,
        guidance_sections=dict(overlay.guidance_sections),
        task_scaffold=overlay.task_scaffold,
        source=overlay.source,
    )


def harness_keys_off_default(keys: Mapping[str, object]) -> dict[str, object]:
    """The ``ask_your_docs`` keys whose validated value differs from the product default.

    WHY: restating a default changes no run, so it must move neither the digest
    nor the plan's diff. The runner is still SENT the key as written — the
    arm's own commit decides its default, which may not be this checkout's.

    Example:
        >>> harness_keys_off_default({"seed_search_with_question": False})
        {}
    """
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

    validated, default = AskYourDocsConfig.model_validate(dict(keys)), AskYourDocsConfig()
    return {
        key: value
        for key, value in keys.items()
        if getattr(validated, key) != getattr(default, key)
    }


def _merged_llm(overlay: ArmOverlay, llm_block: ArmLlmBlock | None) -> dict[str, object] | None:
    """The ``--llm-block`` file with the overlay's ``llm`` merged over it, re-validated."""
    base = None if llm_block is None else dict(llm_block.settings)
    over = overlay.product.get(_LLM_KEY)
    if not isinstance(over, Mapping):
        return base
    merged = _deep_merged(base or {}, over)
    _refuse_invalid_merged_llm(merged, overlay, llm_block)
    return merged


def _deep_merged(under: Mapping[str, object], over: Mapping[str, object]) -> dict[str, object]:
    """``over`` on top of ``under``, nested mappings merged key by key."""
    merged = dict(under)
    for key, value in over.items():
        below = merged.get(key)
        both_mappings = isinstance(below, Mapping) and isinstance(value, Mapping)
        merged[key] = _deep_merged(below, value) if both_mappings else value  # type: ignore[arg-type]
    return merged


def _refuse_invalid_merged_llm(
    merged: Mapping[str, object], overlay: ArmOverlay, llm_block: ArmLlmBlock | None
) -> None:
    """Each half validated alone; the merge must validate too, or the arm raises per rollout."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    try:
        LlmConnectionConfig.model_validate(dict(merged))
    except ValueError as exc:
        under = "no --llm-block" if llm_block is None else f"--llm-block {llm_block.source}"
        raise MeasurementPlanError(
            f"the llm of {overlay.source} merged over {under} is not a valid "
            f"ask_your_docs.llm block: {' '.join(str(exc).split())}"
        ) from exc


@dataclass(frozen=True, slots=True)
class ArmVariants:
    """Both arms' variants. ``overlaid`` = an overlay flag was given, so the plan prints them."""

    baseline: ArmVariant
    candidate: ArmVariant
    overlaid: bool = False

    def for_role(self, role: str) -> ArmVariant:
        return self.baseline if role == ArmRole.BASELINE else self.candidate

    @property
    def max_agent_turns(self) -> int:
        """The plan's budget line and its estimate: the larger arm's cap, an upper bound."""
        return max(self.baseline.max_agent_turns, self.candidate.max_agent_turns)

    def diff(self) -> tuple[SettingChange, ...]:
        """Every dotted key whose value differs between the arms, sorted."""
        before, after = self.baseline.flat(), self.candidate.flat()
        keys = sorted(set(before) | set(after))
        return tuple(
            (key, before.get(key), after.get(key))
            for key in keys
            if before.get(key) != after.get(key)
        )

    def plan_lines(self) -> list[str]:
        """Each arm's digest and source, then the sorted diff between them."""
        return [
            f"settings:   baseline   {self.baseline.digest}  ({_source(self.baseline)})",
            f"            candidate  {self.candidate.digest}  ({_source(self.candidate)})",
            "            diff (baseline -> candidate):",
            *_diff_lines(self.diff()),
        ]


def _source(variant: ArmVariant) -> str:
    return variant.source or "arm defaults"


def _diff_lines(diff: tuple[SettingChange, ...]) -> list[str]:
    if not diff:
        return ["              (none: the two arms run identical settings)"]
    return [
        f"              {key}: {_spelled(before)} -> {_spelled(after)}"
        for key, before, after in diff
    ]


def _spelled(value: object) -> str:
    return _UNSET if value is None else str(value)


def arm_variants(
    baseline: ArmOverlay | None,
    candidate: ArmOverlay | None,
    *,
    llm_block: ArmLlmBlock | None,
    serving_turns: int,
    overlaid: bool | None = None,
) -> ArmVariants:
    """Both arms' variants from their overlays (either may be None)."""
    return ArmVariants(
        baseline=variant_of(baseline, llm_block=llm_block, serving_turns=serving_turns),
        candidate=variant_of(candidate, llm_block=llm_block, serving_turns=serving_turns),
        overlaid=(baseline, candidate) != (None, None) if overlaid is None else overlaid,
    )


def refuse_identical_arms(
    baseline_sha: str, candidate_sha: str, variants: ArmVariants, *, allow_identical: bool
) -> None:
    """One commit under one setting measures only noise — refused unless it is the A/A arm.

    Raises:
        MeasurementPlanError: same sha, same digest, and no ``--allow-identical``.
    """
    digest = variants.baseline.digest
    if allow_identical or baseline_sha != candidate_sha or digest != variants.candidate.digest:
        return
    raise MeasurementPlanError(
        f"baseline and candidate are the same commit {baseline_sha[:SHORT_SHA_CHARS]} under "
        f"the same settings (digest {digest[:SHORT_SHA_CHARS]}), so the run would measure "
        "noise alone; pass --allow-identical for an A/A arm, or give one arm an overlay "
        "(--baseline-settings / --candidate-settings) that differs"
    )


__all__ = (
    "ArmVariant",
    "ArmVariants",
    "SettingChange",
    "arm_variants",
    "harness_keys_off_default",
    "refuse_identical_arms",
    "variant_of",
)
