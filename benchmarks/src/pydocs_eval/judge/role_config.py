"""The settings every judge role shares: where it calls, with which key, how hard and how long.

A role block names ONE pinned model (``model``) and how it is called. ``model``
is empty by default so a role refuses to run, naming its key, until the
deployment YAML pins it (:func:`pinned_model`); the pinned ids live only there.

Example:
    >>> EscalationConfig().reasoning_effort
    <ReasoningEffort.XHIGH: 'xhigh'>
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from pydocs_eval.judge.judge_errors import JudgeConfigError

#: Every role calls OpenRouter; each client adds its own route to this base
#: (``/systemone``, ``/chat/completions``, ``/batches``).
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
#: The variable the bearer is read from, at call time — never a file.
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
#: Where every role's model is pinned; the refusal points the reader at it.
DEPLOYMENT_YAML = "benchmarks/configs/judge_openrouter.yaml"

#: The YAML key of each role's pinned model, as a refusal names it.
JEV_MODEL_KEY = "judge.jev.model"
ESCALATION_MODEL_KEY = "judge.escalation.model"
WRITER_MODEL_KEY = "reference_writer.model"
WRITER_FALLBACK_MODEL_KEY = "reference_writer.fallback_model"

_DEFAULT_RETRIES = 2
# A batch role waits on its whole batch within this; a synchronous role waits
# on one request.
_DEFAULT_BATCH_TIMEOUT_SECONDS = 600.0
_DEFAULT_ESCALATION_TIMEOUT_SECONDS = 120.0
# "The needle's body plus a few surrounding lines" (spec 9c): enough to show the
# enclosing class or decorator, too few to reach a neighbouring function.
_DEFAULT_CONTEXT_LINES = 5

# The alignment set's sizes and draw (spec 9d, Q51/Q58/Q59): a 10-item dual pilot,
# then about 100 items in pass 1 and 60 in pass 2, all inside the $15 sub-budget.
_DEFAULT_PILOT_ITEMS = 10
_DEFAULT_PASS_1_ITEMS = 100
_DEFAULT_PASS_2_ITEMS = 60
_DEFAULT_ALIGNMENT_SUB_BUDGET_USD = 15.0
# Jev's review band starts at 0.3/0.7 and is swept outward; a first-pass Jev
# probability in 0.2-0.8 makes an answer borderline.
_DEFAULT_BAND_START = (0.3, 0.7)
_DEFAULT_BORDERLINE_WINDOW = (0.2, 0.8)
_DEFAULT_PLANTED_INJECTIONS = 4
_DEFAULT_RANDOM_SHARE = 0.25
_DEFAULT_AGREED_AUDIT_SHARE = 0.10
_DEFAULT_AUDIT_GROWTH_THRESHOLD = 0.05
# Luna decides a question only at this much agreement with the alignment set,
# or at the labellers' own κ where that is lower (Q53, Round 5f).
_DEFAULT_AGREEMENT_FLOOR = 0.90
# In-band escalations stop here for the whole program (Q52).
_DEFAULT_SPEND_CAP_USD = 2.00
# The most one in-band answer can cost at the pinned escalation model's price.
# Luna lists $0.10/M in and $0.50/M out; an in-band request is under ~15k tokens
# in, and its labels plus reasoning at xhigh stay under ~60k out: about $0.032.
# An answer OpenRouter does not price (or a call that failed, which may still
# have been billed) is booked at this bound, and no call starts unless it still
# fits under the cap, so the cap holds even when a price is missing. Re-derive
# it whenever judge.escalation.model changes.
_DEFAULT_ANSWER_COST_BOUND_USD = 0.05
# Below this inter-labeller Cohen's kappa a question is ill-posed: its criteria
# are rewritten before any judge is trusted on it (spec 9d). The spec names no
# number; 0.40 is the top of Landis and Koch's "fair" band.
_DEFAULT_MIN_LABELLER_KAPPA = 0.40


def labeller_model_key(index: int) -> str:
    """The YAML key of the ``index``-th alignment labeller's pinned model.

    Example:
        >>> labeller_model_key(1)
        'judge.alignment.labellers[1].model'
    """
    return f"judge.alignment.labellers[{index}].model"


class ReasoningEffort(StrEnum):
    """The effort levels OpenRouter's ``reasoning_effort`` accepts, highest first."""

    MAX = "max"
    XHIGH = "xhigh"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    MINIMAL = "minimal"
    NONE = "none"


def pinned_model(key: str, model: str) -> str:
    """``model``, or a refusal naming ``key`` when the deployment left it empty.

    Example:
        >>> pinned_model("judge.escalation.model", "openai/gpt-6-luna")
        'openai/gpt-6-luna'
    """
    if not model.strip():
        raise JudgeConfigError(
            f"{key} is empty: pin the model in {DEPLOYMENT_YAML} before this role runs"
        )
    return model


class OpenRouterCallConfig(BaseModel):
    """What every role block holds: its pinned model and how it is called.

    ``timeout_seconds`` has no shared default: each role family declares its own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = ""
    endpoint: str = OPENROUTER_API_BASE
    api_key_env: str = OPENROUTER_API_KEY_ENV
    timeout_seconds: float = Field(gt=0)
    retries: int = Field(default=_DEFAULT_RETRIES, ge=0)


class ChatRoleConfig(OpenRouterCallConfig):
    """One chat-completion role: its call settings and the reasoning effort it asks for.

    A ``:batch`` model runs through OpenRouter's Batch API and waits on the
    whole batch within ``timeout_seconds``; any other model is called one
    request at a time, each within ``timeout_seconds``.
    """

    reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH
    timeout_seconds: float = Field(default=_DEFAULT_BATCH_TIMEOUT_SECONDS, gt=0)


class EscalationFloorScale(StrEnum):
    """What the escalation judge's per-question agreement is compared on.

    The floor is ``min(agreement_floor, κ_labellers)`` either way. Whether the
    judge's side of the comparison is its percent agreement (Q53 and Round 5f,
    the spec as written) or its own κ (one κ scale for both) is an open owner
    word on the turn-efficiency umbrella; this knob is where it is answered.
    Under ``KAPPA`` a question whose labellers' κ is below ``agreement_floor``
    asks the judge's κ to reach the labellers' own: reaching it is the floor,
    and a judge κ above it is still flagged in the report, never rewarded.
    """

    #: The judge's raw agreement rate with the alignment label (as written).
    PERCENT_AGREEMENT = "percent_agreement"
    #: The judge's Cohen's κ against the alignment label (the single-scale variant).
    KAPPA = "kappa"


class EscalationConfig(ChatRoleConfig):
    """``judge.escalation``: the in-band judge, at the highest effort its endpoint accepts.

    It is trusted on a question only at ``agreement_floor`` (see
    :class:`EscalationFloorScale`), and stops calling once ``spend_cap_usd`` is
    spent, booking an unpriced answer at ``answer_cost_bound_usd``.
    ``cache_dir`` empty means the derived default (``cache_root()/escalation``),
    where the spend ledger also lives.
    """

    reasoning_effort: ReasoningEffort = ReasoningEffort.XHIGH
    timeout_seconds: float = Field(default=_DEFAULT_ESCALATION_TIMEOUT_SECONDS, gt=0)
    agreement_floor: float = Field(default=_DEFAULT_AGREEMENT_FLOOR, ge=0.0, le=1.0)
    floor_scale: EscalationFloorScale = EscalationFloorScale.PERCENT_AGREEMENT
    spend_cap_usd: float = Field(default=_DEFAULT_SPEND_CAP_USD, ge=0.0)
    answer_cost_bound_usd: float = Field(default=_DEFAULT_ANSWER_COST_BOUND_USD, gt=0.0)
    cache_dir: str = ""


@dataclass(frozen=True, slots=True)
class ChatRole:
    """One chat-completion role, ready to call: its pinned model's key and its settings.

    Raises:
        JudgeConfigError: ``config.model`` is empty, named by ``model_key``.
    """

    model_key: str
    config: ChatRoleConfig

    def __post_init__(self) -> None:
        pinned_model(self.model_key, self.config.model)

    @property
    def model(self) -> str:
        return self.config.model


def _checked_window(value: tuple[float, float], name: str) -> tuple[float, float]:
    low, high = value
    if not 0.0 <= low <= high <= 1.0:
        raise ValueError(
            f"{name} = {list(value)!r}, expected [low, high] with 0 <= low <= high <= 1"
        )
    return value


class AlignmentPassSizes(BaseModel):
    """``judge.alignment.n_items``: how many items each alignment pass aims at."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pass_1: int = Field(default=_DEFAULT_PASS_1_ITEMS, ge=1)
    pass_2: int = Field(default=_DEFAULT_PASS_2_ITEMS, ge=1)


class AlignmentConfig(BaseModel):
    """``judge.alignment``: the two blind labellers, and how the alignment set is drawn and routed.

    ``label_file`` maps a dataset to the label file it is written to; a dataset
    not listed uses the vendored ``judge/data/alignment/<dataset>__<jev-model>.jsonl``.
    ``sub_budget_usd`` bounds the pilot and both passes together.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    labellers: tuple[ChatRoleConfig, ChatRoleConfig] = (ChatRoleConfig(), ChatRoleConfig())
    pilot_items: int = Field(default=_DEFAULT_PILOT_ITEMS, ge=1)
    n_items: AlignmentPassSizes = Field(default_factory=AlignmentPassSizes)
    sub_budget_usd: float = Field(default=_DEFAULT_ALIGNMENT_SUB_BUDGET_USD, ge=0.0)
    band_start: tuple[float, float] = _DEFAULT_BAND_START
    borderline_window: tuple[float, float] = _DEFAULT_BORDERLINE_WINDOW
    planted_injections: int = Field(default=_DEFAULT_PLANTED_INJECTIONS, ge=0)
    random_share: float = Field(default=_DEFAULT_RANDOM_SHARE, ge=0.0, le=1.0)
    agreed_audit_share: float = Field(default=_DEFAULT_AGREED_AUDIT_SHARE, ge=0.0, le=1.0)
    audit_growth_threshold: float = Field(default=_DEFAULT_AUDIT_GROWTH_THRESHOLD, gt=0.0, le=1.0)
    min_labeller_kappa: float = Field(default=_DEFAULT_MIN_LABELLER_KAPPA, ge=-1.0, le=1.0)
    label_file: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("band_start", "borderline_window")
    @classmethod
    def _ordered_window(
        cls, value: tuple[float, float], info: ValidationInfo
    ) -> tuple[float, float]:
        return _checked_window(value, f"judge.alignment.{info.field_name}")


class ReferenceWriterConfig(ChatRoleConfig):
    """``reference_writer``: the model that writes reference answers, and its fallback.

    ``fallback_model`` runs under this block's settings, only for a row whose
    ``model`` job failed: the Batch API answered it with an error, or its batch
    ended ``failed``, ``expired`` or ``cancelled``. A batch merely still running
    at ``timeout_seconds`` is collected later by id, never re-asked of the
    fallback. ``context_lines`` is how many lines the writer sees on each side
    of a repoqa needle.
    """

    fallback_model: str = ""
    context_lines: int = Field(default=_DEFAULT_CONTEXT_LINES, ge=0)


__all__ = (
    "DEPLOYMENT_YAML",
    "ESCALATION_MODEL_KEY",
    "JEV_MODEL_KEY",
    "OPENROUTER_API_BASE",
    "OPENROUTER_API_KEY_ENV",
    "WRITER_FALLBACK_MODEL_KEY",
    "WRITER_MODEL_KEY",
    "AlignmentConfig",
    "AlignmentPassSizes",
    "ChatRole",
    "ChatRoleConfig",
    "EscalationConfig",
    "EscalationFloorScale",
    "OpenRouterCallConfig",
    "ReasoningEffort",
    "ReferenceWriterConfig",
    "labeller_model_key",
    "pinned_model",
)
