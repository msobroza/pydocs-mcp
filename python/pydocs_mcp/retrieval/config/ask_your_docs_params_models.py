"""The ``ask_your_docs.llm.provider`` / ``.params`` vocabulary (model-params v2 §4).

Five keys, one real-unit vocabulary; an absent key is not sent (the model's own
default). Ranges live ONLY in the pydantic ``Field`` constraints below —
:func:`param_bounds` reads them back for the dialog widgets and for the error
messages, so a bound is never spelled twice (CLAUDE.md §Default values).

WHY every message names the value: ``error_redaction`` blanks each input under
``ask_your_docs.llm`` (a credential can land there), so the MESSAGE is the only
channel left. Numbers are echoed; any other type is named, never its text — a
secret pasted into a numeric field or at an unknown key never reaches stderr.

Example:
    >>> ChatParamsConfig.model_validate({"thinking": False}).thinking
    <ThinkingLevel.OFF: 'off'>
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, get_args

import annotated_types
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)

# auto = decide from base_url (and, in the dialog only, from the listing); v2 §2.
ProviderName = Literal["auto", "openai", "openrouter", "vllm", "litellm", "generic"]
_DEFAULT_PROVIDER: ProviderName = "auto"  # single source: the block field and the fold


class ThinkingLevel(StrEnum):
    """The one Thinking control; the wire mapping per provider lives in the harness."""

    AUTO = "auto"  # send nothing — stored as None, so explicit auto == an omitted key
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"  # also what an on/off family's "On" stores (D8)
    HIGH = "high"


_MAX_SEED = 2**63 - 1  # the int64 ceiling OpenAI-format servers accept for seed
_PARAMS_LOCATION = "ask_your_docs.llm.params"
_NUMERIC_PARAMS = ("temperature", "max_tokens", "top_p", "seed")
# WHY a hint: env leaves reach pydantic as strings, and a numeric string is refused.
_JSON_ENV_HINT = (
    "; from the environment, set numbers as JSON: "
    "PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS='{\"temperature\": 0.2}'"
)

_NOT_THIS_ITERATION = "is not configurable in this iteration (the client keeps its own bound)"
_NEVER_RAW = "is never set raw; the chat factory builds any request body from params.thinking"
_NOT_CONFIGURABLE = "is not configurable"
# Keys an operator plausibly reaches for, each refused with its own pointer (v2 §4; D2
# dropped the penalties; ``stop`` can truncate the ReAct agent's tool calls).
_REFUSED_PARAM_KEYS: Mapping[str, str] = MappingProxyType(
    {
        "timeout": _NOT_THIS_ITERATION,
        "max_retries": _NOT_THIS_ITERATION,
        "extra_body": _NEVER_RAW,
        "model_kwargs": _NEVER_RAW,
        "reasoning": _NEVER_RAW,
        "chat_template_kwargs": _NEVER_RAW,
        "reasoning_effort": "is refused: use params.thinking (auto, off, low, medium or high)",
        "max_completion_tokens": "is refused: use params.max_tokens (sent as that field)",
        **dict.fromkeys(
            (
                "frequency_penalty",
                "presence_penalty",
                "stop",
                "verbosity",
                "top_k",
                "min_p",
                "repetition_penalty",
            ),
            _NOT_CONFIGURABLE,
        ),
    }
)


class ChatParamsConfig(BaseModel):
    """``ask_your_docs.llm.params``: what the chat model is asked for; None = not sent."""

    # hide_input_in_errors: direct construction must not echo a stray key's value either.
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    thinking: ThinkingLevel | None = Field(default=None)
    temperature: float | None = Field(default=None, ge=0, le=2, strict=True, allow_inf_nan=False)
    max_tokens: int | None = Field(default=None, ge=1, strict=True)  # wire: max_completion_tokens
    top_p: float | None = Field(default=None, gt=0, le=1, strict=True, allow_inf_nan=False)
    seed: int | None = Field(default=None, ge=0, le=_MAX_SEED, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _refuse_keys_outside_the_vocabulary(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        problems = [_key_problem(key) for key in data if key not in cls.model_fields]
        if problems:
            raise ValueError("; ".join(problems))
        return data

    @field_validator("thinking", mode="before")
    @classmethod
    def _thinking_from_yaml(cls, value: Any) -> ThinkingLevel | None:
        if value is True:
            raise ValueError("thinking: true is ambiguous; use low, medium or high")
        if value is False:  # YAML 1.1 loads a bare `thinking: off` as False
            return ThinkingLevel.OFF
        if value is None or value == ThinkingLevel.AUTO:
            return None
        if value not in tuple(ThinkingLevel):
            levels = ", ".join(level.value for level in ThinkingLevel)
            raise ValueError(f"thinking: got {_described(value)}, expected one of {levels}")
        return ThinkingLevel(value)

    @field_validator(*_NUMERIC_PARAMS, mode="wrap")
    @classmethod
    def _number_in_bounds(
        cls, value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> Any:
        try:
            return handler(value)
        except ValidationError:
            raise ValueError(_number_message(str(info.field_name), value)) from None


@dataclass(frozen=True, slots=True)
class ParamBounds:
    """One numeric param's range, read off its ``Field`` — the widgets' min/max/step source."""

    minimum: float
    maximum: float | None  # None = no ceiling here (max_tokens: the model's own)
    exclusive_minimum: bool
    integer: bool


def param_bounds(name: str) -> ParamBounds:
    """The range pydantic enforces for ``name``, e.g. ``param_bounds("top_p").maximum == 1``."""
    if name not in _NUMERIC_PARAMS:
        raise ValueError(
            f"param_bounds: got {name!r}, expected one of {', '.join(_NUMERIC_PARAMS)}"
        )
    field = ChatParamsConfig.model_fields[name]
    minimum, maximum, exclusive = 0.0, None, False
    for constraint in field.metadata:
        if isinstance(constraint, annotated_types.Ge):
            minimum = constraint.ge  # type: ignore[assignment]  # our Fields bound with numbers
        elif isinstance(constraint, annotated_types.Gt):
            minimum, exclusive = constraint.gt, True  # type: ignore[assignment]
        elif isinstance(constraint, annotated_types.Le):
            maximum = constraint.le  # type: ignore[assignment]
    return ParamBounds(minimum, maximum, exclusive, integer=int in get_args(field.annotation))


def _expected_shape(name: str) -> str:
    """``a number in [0, 2]`` / ``a number in (0, 1]`` / ``an integer >= 1`` — from the Field."""
    bounds = param_bounds(name)
    kind = "an integer" if bounds.integer else "a number"
    if bounds.maximum is None:
        return f"{kind} {'>' if bounds.exclusive_minimum else '>='} {bounds.minimum}"
    opening = "(" if bounds.exclusive_minimum else "["
    return f"{kind} in {opening}{bounds.minimum}, {bounds.maximum}]"


def _number_message(name: str, value: Any) -> str:
    hint = _JSON_ENV_HINT if isinstance(value, str) else ""
    return f"{name}: got {_described(value)}, expected {_expected_shape(name)}{hint}"


def _described(value: Any) -> str:
    """Numbers verbatim; anything else by TYPE only (bool is an int subclass: named first)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return f"a {type(value).__name__}"
    return repr(value)


def _key_problem(key: object) -> str:
    """The refusal for one key outside the vocabulary: its own reason, and the allowed set."""
    allowed = ", ".join(ChatParamsConfig.model_fields)
    reason = _REFUSED_PARAM_KEYS.get(str(key))
    if reason is not None:
        return f"{_PARAMS_LOCATION}.{key} {reason}; expected keys: {allowed}"
    return f"{_PARAMS_LOCATION}: unknown key {key!r}; expected one of {allowed}"


__all__ = ("ChatParamsConfig", "ParamBounds", "ProviderName", "ThinkingLevel", "param_bounds")
