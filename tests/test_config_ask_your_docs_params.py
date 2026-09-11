"""The ``ask_your_docs.llm.params`` / ``.provider`` vocabulary (model-params v2 §4).

Core-suite tests: pydantic only, no [harness-ask-your-docs] extra needed.
"""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pydocs_mcp.retrieval.config.ask_your_docs_params_models import (
    ChatParamsConfig,
    ThinkingLevel,
    param_bounds,
)

_ALLOWED = ("thinking", "temperature", "max_tokens", "top_p", "seed")
_MAX_SEED = 2**63 - 1


def test_an_empty_block_dumps_nothing() -> None:
    """Absent = not sent (the model's default); the frozen model is shareable."""
    params = ChatParamsConfig()
    assert params.model_dump(exclude_none=True) == {}
    assert params == ChatParamsConfig.model_validate({})
    assert hash(params) == hash(ChatParamsConfig())


def test_the_vocabulary_is_exactly_five_keys() -> None:
    assert tuple(ChatParamsConfig.model_fields) == _ALLOWED


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", 0),
        ("temperature", 0.0),
        ("temperature", 2),
        ("temperature", 2.0),
        ("max_tokens", 1),
        ("top_p", 1.0),
        ("top_p", 1e-9),
        ("seed", 0),
        ("seed", _MAX_SEED),
    ],
)
def test_range_edges_pass(field: str, value: float) -> None:
    assert getattr(ChatParamsConfig.model_validate({field: value}), field) == value


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("temperature", 2.5, "got 2.5, expected a number in [0, 2]"),
        ("temperature", -0.1, "got -0.1, expected a number in [0, 2]"),
        ("max_tokens", 0, "got 0, expected an integer >= 1"),
        ("top_p", 0, "got 0, expected a number in (0, 1]"),
        ("top_p", 1.5, "got 1.5, expected a number in (0, 1]"),
        ("seed", -1, f"got -1, expected an integer in [0, {_MAX_SEED}]"),
        ("seed", _MAX_SEED + 1, f"got {_MAX_SEED + 1}, expected an integer in [0, {_MAX_SEED}]"),
    ],
)
def test_out_of_range_names_the_value_and_the_shape(field: str, value, expected: str) -> None:
    with pytest.raises(ValidationError, match=rf"{field}: {_escape(expected)}"):
        ChatParamsConfig.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value", "got"),
    [
        ("temperature", True, "got a bool"),
        ("temperature", "0.5", "got a str"),
        ("temperature", math.nan, "got nan"),
        ("temperature", math.inf, "got inf"),
        ("top_p", -math.inf, "got -inf"),
        ("max_tokens", False, "got a bool"),
        ("max_tokens", "4096", "got a str"),
        ("max_tokens", 4096.0, "got 4096.0"),
        ("seed", 7.5, "got 7.5"),
    ],
)
def test_bool_numeric_string_nan_inf_and_non_integers_are_rejected(
    field: str, value: object, got: str
) -> None:
    with pytest.raises(ValidationError, match=rf"{field}: {_escape(got)}, expected an? "):
        ChatParamsConfig.model_validate({field: value})


def test_a_string_value_is_never_echoed() -> None:
    """A secret pasted into a numeric field names its TYPE, never its text."""
    with pytest.raises(ValidationError) as excinfo:
        ChatParamsConfig.model_validate({"temperature": _PASTED_SECRET})
    assert _PASTED_SECRET not in str(excinfo.value)


def test_a_string_number_points_to_the_json_env_form() -> None:
    """Env leaves arrive as strings; the whole-params JSON form carries real numbers."""
    with pytest.raises(ValidationError, match=r"PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS='\{"):
        ChatParamsConfig.model_validate({"temperature": "0.5"})


def test_yaml_off_becomes_off_and_auto_is_the_absent_value() -> None:
    """YAML 1.1 loads a bare ``thinking: off`` as False; explicit auto == omitted."""
    assert ChatParamsConfig.model_validate({"thinking": False}).thinking is ThinkingLevel.OFF
    assert ChatParamsConfig.model_validate({"thinking": "off"}).thinking is ThinkingLevel.OFF
    assert ChatParamsConfig.model_validate({"thinking": "low"}).thinking is ThinkingLevel.LOW
    assert ChatParamsConfig.model_validate({"thinking": "auto"}) == ChatParamsConfig()
    assert ChatParamsConfig(thinking=ThinkingLevel.AUTO).model_dump(exclude_none=True) == {}


def test_yaml_true_is_ambiguous() -> None:
    with pytest.raises(
        ValidationError, match="thinking: true is ambiguous; use low, medium or high"
    ):
        ChatParamsConfig.model_validate({"thinking": True})


def test_an_unknown_thinking_level_lists_the_levels() -> None:
    with pytest.raises(ValidationError, match="auto, off, low, medium, high"):
        ChatParamsConfig.model_validate({"thinking": "xhigh"})


def test_an_unknown_key_lists_the_allowed_set_and_never_echoes_the_value() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ChatParamsConfig.model_validate({"api_key": _PASTED_SECRET})
    rendered = str(excinfo.value)
    assert "unknown key 'api_key'" in rendered
    assert "thinking, temperature, max_tokens, top_p, seed" in rendered
    assert _PASTED_SECRET not in rendered


_REFUSED = {
    "timeout": "not configurable in this iteration",
    "max_retries": "not configurable in this iteration",
    "extra_body": "never set raw; the chat factory builds any request body from params.thinking",
    "model_kwargs": "never set raw; the chat factory builds any request body from params.thinking",
    "reasoning": "never set raw; the chat factory builds any request body from params.thinking",
    "chat_template_kwargs": "never set raw; the chat factory builds any request body",
    "reasoning_effort": "use params.thinking",
    "max_completion_tokens": "use params.max_tokens",
    "frequency_penalty": "not configurable",
    "presence_penalty": "not configurable",
    "stop": "not configurable",
    "verbosity": "not configurable",
    "top_k": "not configurable",
    "min_p": "not configurable",
    "repetition_penalty": "not configurable",
}


@pytest.mark.parametrize(("key", "reason"), sorted(_REFUSED.items()))
def test_each_refused_key_gives_its_own_message(key: str, reason: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        ChatParamsConfig.model_validate({key: _PASTED_SECRET})
    rendered = str(excinfo.value)
    assert f"ask_your_docs.llm.params.{key}" in rendered and reason in rendered
    assert "thinking, temperature, max_tokens, top_p, seed" in rendered  # the expected shape
    assert _PASTED_SECRET not in rendered


def test_param_bounds_read_the_field_constraints() -> None:
    """One source of bounds: the widgets and the messages read pydantic's Field."""
    temperature = param_bounds("temperature")
    assert (temperature.minimum, temperature.maximum) == (0, 2)
    assert temperature.exclusive_minimum is False
    top_p = param_bounds("top_p")
    assert (top_p.minimum, top_p.maximum, top_p.exclusive_minimum) == (0, 1, True)
    assert param_bounds("max_tokens").maximum is None
    assert param_bounds("seed").maximum == _MAX_SEED


def test_param_bounds_refuses_a_non_numeric_name() -> None:
    with pytest.raises(ValueError, match="got 'thinking', expected one of"):
        param_bounds("thinking")


_PASTED_SECRET = "sk-live-params-0001"


def _escape(text: str) -> str:
    import re

    return re.escape(text)


def _commented_llm_template() -> str:
    """The ``# llm:`` template that follows ``llm: null`` in the shipped default config."""
    root = Path(__file__).resolve().parents[1]
    shipped = root / "python/pydocs_mcp/defaults/default_config.yaml"
    lines = shipped.read_text(encoding="utf-8").splitlines()
    commented = itertools.takewhile(
        lambda line: line.startswith("  #"), lines[lines.index("  llm: null") + 1 :]
    )
    return "\n".join(line.removeprefix("  # ") for line in commented)


def test_the_shipped_template_documents_every_field_of_the_typed_block() -> None:
    """The commented template is documentation that must stay TRUE: uncommenting it yields a
    valid ``ask_your_docs.llm`` block naming every field, ``provider`` and ``params`` included."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    block = yaml.safe_load(_commented_llm_template())["llm"]
    assert set(block) == set(LlmConnectionConfig.model_fields)
    assert set(block["params"]) == set(ChatParamsConfig.model_fields)
    LlmConnectionConfig.model_validate(block)  # every documented value is in range
