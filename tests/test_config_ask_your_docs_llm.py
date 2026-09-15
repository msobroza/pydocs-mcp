"""The ``ask_your_docs.llm`` connection block (LLM-connection design §5.1 — AC-21, AC-22)
plus its ``provider`` / ``params`` integration through AppConfig (model-params v2 §4).

Core-suite tests — pydantic only, no [harness-ask-your-docs] extra needed. The params
*vocabulary* itself is pinned by ``test_config_ask_your_docs_params.py``; what lives here
is how a params block reaches AppConfig and survives the auth-secret redaction.
"""

from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig


def _shipped_default_config() -> dict[str, Any]:
    """The shipped defaults YAML, parsed. The presence tests below read it directly so
    that deleting a block fails loudly instead of being masked by the Field defaults."""
    root = Path(__file__).resolve().parents[1]
    return yaml.safe_load(
        (root / "python/pydocs_mcp/defaults/default_config.yaml").read_text(encoding="utf-8")
    )


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Hermeticity (repo convention): clears PYDOCS_* env vars and chdirs away
    from any cwd config. (A user-level ~/.config/pydocs-mcp/config.yaml is NOT
    isolated — the same hole as every sibling config-test fixture.)"""
    for var in list(os.environ):
        if var.startswith("PYDOCS_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


# ── ask_your_docs.llm (LLM-connection design §5.1 — AC-21, AC-22) ──


def test_llm_block_absent_by_default() -> None:
    """AC-21: no block ⇒ today's behavior; the dated default flip is pinned."""
    cfg = AppConfig.load().ask_your_docs
    assert cfg.llm is None
    assert cfg.multimodal.preferred_architecture == "inline"


def test_llm_block_parses_token_service_and_vision_model(tmp_path) -> None:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import VisionModelConfig

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        "    auth:\n"
        "      token_url: http://localhost:8899/access-token\n"
        "    token_field: access_token\n"
        "    renew_on_status: [401, 403]\n"
        "    vision:\n"
        "      model: vision-b\n",
        encoding="utf-8",
    )
    llm = AppConfig.load(explicit_path=overlay).ask_your_docs.llm
    assert llm is not None
    assert llm.base_url == "http://llm.internal/v1"
    assert llm.model is None
    assert llm.auth is not None and llm.auth.token_url == "http://localhost:8899/access-token"
    assert llm.auth.api_key_env is None
    assert llm.token_field == "access_token"
    assert llm.renew_on_status == (401, 403)
    assert llm.vision == VisionModelConfig(model="vision-b")


def test_llm_vision_true_false_and_api_key_env(tmp_path) -> None:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    assert LlmConnectionConfig(vision=True).vision is True
    assert LlmConnectionConfig(vision=False).vision is False
    external = LlmConnectionConfig.model_validate({"auth": {"api_key_env": "LLM_KEY"}})
    assert external.base_url is None  # D2: an external key on the vendor default endpoint
    assert external.auth is not None and external.auth.api_key_env == "LLM_KEY"
    assert external.renew_on_status == (401, 403, 407)


def test_llm_auth_needs_exactly_one_source() -> None:
    """E8: both or neither of token_url / api_key_env is rejected, naming what was given."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmAuthConfig

    with pytest.raises(ValidationError, match="expected exactly one of token_url / api_key_env"):
        LlmAuthConfig(token_url="http://localhost:8899/access-token", api_key_env="LLM_KEY")
    with pytest.raises(ValidationError, match="got neither"):
        LlmAuthConfig()


def test_llm_token_url_needs_base_url() -> None:
    """E14: a token service authenticates one internal endpoint — the block must name it."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    with pytest.raises(ValidationError, match="token_url needs base_url; got null"):
        LlmConnectionConfig.model_validate({"auth": {"token_url": "http://localhost:8899/t"}})


# A secret in the LAST query parameter: pydantic elides the MIDDLE of a long
# input repr, so a trailing value survives verbatim in an un-redacted error.
# Keep every credential literal in a module constant — never inline one in a
# test body: ``_rendered`` formats the traceback, which prints the offending
# frame's SOURCE line, and an inlined literal would fail the assertion by
# appearing there rather than in the error pydantic built.
_AUTH_SECRET = "sk-live-9f8e7d6c5b4a"
_TOKEN_URL_WITH_SECRET = f"http://llm.internal/token?api_key={_AUTH_SECRET}"
_USERINFO_SECRET = "s3cr3tpw"
_QUERY_SECRET = "v4lue"
_TOKEN_URL_WITH_USERINFO = f"http://user:{_USERINFO_SECRET}@host:8899/t?k={_QUERY_SECRET}"


def _rendered(excinfo) -> str:
    """Both surfaces a startup ValidationError reaches stderr / the logs through."""
    error = excinfo.value
    frames = traceback.format_exception(type(error), error, error.__traceback__)
    return str(error) + "".join(frames)


def test_llm_token_url_rejects_credentials_in_userinfo_or_query() -> None:
    """E16 path 1 (direct construction): the message shows the stripped form only."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmAuthConfig

    with pytest.raises(ValidationError, match="must not carry credentials") as excinfo:
        LlmAuthConfig(token_url=_TOKEN_URL_WITH_USERINFO)
    rendered = _rendered(excinfo)
    assert _USERINFO_SECRET not in rendered and _QUERY_SECRET not in rendered


def test_llm_auth_secret_redacted_through_connection_model_validate() -> None:
    """E16 path 2: LlmConnectionConfig is outermost, so it — not the nested auth
    model — decides whether pydantic echoes the auth mapping."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    with pytest.raises(ValidationError) as excinfo:
        LlmConnectionConfig.model_validate(
            {
                "base_url": "http://llm.internal/v1",
                "auth": {"token_url": _TOKEN_URL_WITH_SECRET},
            }
        )
    assert _AUTH_SECRET not in _rendered(excinfo)


def test_llm_auth_secret_redacted_through_yaml_overlay(tmp_path) -> None:
    """E16 path 3: the CLI / server startup path — this error goes to stderr."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        "    auth:\n"
        f"      token_url: {_TOKEN_URL_WITH_SECRET}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load(explicit_path=overlay)
    assert _AUTH_SECRET not in _rendered(excinfo)


def test_llm_auth_secret_redacted_through_env_layer(monkeypatch) -> None:
    """E16 path 4: the PYDOCS_ env layer feeds the same block."""
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__LLM__BASE_URL", "http://llm.internal/v1")
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__LLM__AUTH__TOKEN_URL", _TOKEN_URL_WITH_SECRET)
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load()
    assert _AUTH_SECRET not in _rendered(excinfo)


def test_auth_redaction_spares_other_config_errors(tmp_path) -> None:
    """The redaction stays narrow: a sibling block's error still names the offending
    value (CLAUDE.md — errors carry the offending value and the expected shape)."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        "    auth:\n"
        f"      token_url: {_TOKEN_URL_WITH_SECRET}\n"
        "  images:\n"
        "    max_per_turn: 99\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load(explicit_path=overlay)
    rendered = _rendered(excinfo)
    assert _AUTH_SECRET not in rendered
    assert "input_value=99" in rendered


def test_llm_stray_key_secret_redacted_through_yaml_overlay(tmp_path) -> None:
    """A secret pasted at a key the block does not define — the plausible operator
    mistake, since the design forbids secrets in YAML at all, so a stray key is
    exactly where one turns up. The ``extra_forbidden`` error sits at
    ``ask_your_docs.llm.api_key``, OUTSIDE ``…llm.auth``: only a redaction scoped to
    the whole ``llm`` subtree keeps it out of the startup error."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        f"    api_key: {_AUTH_SECRET}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load(explicit_path=overlay)
    assert _AUTH_SECRET not in _rendered(excinfo)


def test_llm_scalar_in_place_of_the_block_is_redacted(tmp_path) -> None:
    """The same mistake one level up: a bare token pasted at ``ask_your_docs.llm``
    raises ``model_type`` AT the block location, not under it."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("ask_your_docs:\n  llm: " + _AUTH_SECRET + "\n", encoding="utf-8")
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load(explicit_path=overlay)
    assert _AUTH_SECRET not in _rendered(excinfo)


def test_llm_block_errors_name_the_value_without_the_input_echo(tmp_path) -> None:
    """The widened scope costs every field in the block pydantic's ``input_value=``
    echo, so the validator MESSAGE must carry the offending value and the expected
    shape on its own (CLAUDE.md §Coding Rules)."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        "    renew_on_status: [999]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load(explicit_path=overlay)
    rendered = _rendered(excinfo)
    assert "got 999, expected a subset of [401, 403, 407]" in rendered
    assert "input_value=[999]" not in rendered  # blanked by the widened redaction


def test_llm_renew_on_status_must_be_renewable() -> None:
    """E17: only 401 / 403 / 407 may renew; 200 and 503 are rejected with the allowed set."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    for bad in (200, 503):
        with pytest.raises(
            ValidationError, match=rf"got {bad}, expected a subset of \[401, 403, 407\]"
        ):
            LlmConnectionConfig(renew_on_status=(bad,))
    assert LlmConnectionConfig(renew_on_status=(401, 407)).renew_on_status == (401, 407)


def test_llm_rejects_unknown_keys_and_empty_vision_model() -> None:
    """E7 + the vision.model shape."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import (
        LlmAuthConfig,
        LlmConnectionConfig,
    )

    with pytest.raises(ValidationError):
        LlmConnectionConfig.model_validate({"profiles": []})
    with pytest.raises(ValidationError):
        LlmAuthConfig.model_validate({"api_key_env": "K", "allow_cleartext": True})
    with pytest.raises(ValidationError):
        LlmConnectionConfig.model_validate({"vision": {"model": ""}})


def test_llm_vision_env_overlay(monkeypatch) -> None:
    """AC-22: PYDOCS_ASK_YOUR_DOCS__LLM__VISION=true reaches the block through the env layer."""
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__LLM__VISION", "true")
    llm = AppConfig.load().ask_your_docs.llm
    assert llm is not None and llm.vision is True


def test_default_yaml_ships_llm_null_and_the_flipped_default() -> None:
    """AC-21 YAML half: the shipped block carries `llm: null` and the flipped
    `preferred_architecture: inline`."""
    shipped = _shipped_default_config()
    block = shipped["ask_your_docs"]
    assert "llm" in block and block["llm"] is None
    assert block["multimodal"]["preferred_architecture"] == "inline"


_UNREBUILDABLE_SECRET = "sk-live-unrebuildable-0001"


def test_redaction_never_falls_back_to_the_unredacted_error() -> None:
    """The rebuild is not total: ``from_exception_data`` accepts pydantic's own error types
    only, so a line raised with a CUSTOM code comes back as a plain string it refuses. The
    fallback must still blank the input — handing the original error back (or letting the
    rebuild's own exception carry it as ``__context__``) would print the credential E16 exists
    to hide."""
    from pydantic_core import PydanticCustomError

    from pydocs_mcp.retrieval.config.error_redaction import (
        redact_secret_inputs,
        redacting_secret_inputs,
    )

    original = ValidationError.from_exception_data(
        "AppConfig",
        [
            {
                "type": PydanticCustomError("bespoke_code", "the endpoint is not reachable"),
                "loc": ("ask_your_docs", "llm", "auth", "token_url"),
                "input": _UNREBUILDABLE_SECRET,
            }
        ],
    )
    assert _UNREBUILDABLE_SECRET in str(original)  # the leak this guards against
    redacted = redact_secret_inputs(original)
    assert _UNREBUILDABLE_SECRET not in str(redacted)
    assert "the endpoint is not reachable" in str(redacted)  # the message survives
    assert "'token_url'" in str(redacted)  # and so does WHERE it came from
    assert [detail["loc"] for detail in redacted.errors()] == [()]  # the messages-only fallback

    def _raise() -> None:
        with redacting_secret_inputs():
            raise original

    with pytest.raises(ValidationError) as excinfo:
        _raise()
    assert _UNREBUILDABLE_SECRET not in _rendered(excinfo)


# ── ask_your_docs.llm.provider / .params (model-params v2 §4) ──


def test_llm_block_without_params_keeps_todays_semantics(tmp_path) -> None:
    """Adding the fields moves nothing: no key = provider auto + an empty params set."""
    from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ChatParamsConfig

    assert AppConfig.load().ask_your_docs.llm is None  # default_config.yaml's llm: null holds
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("ask_your_docs:\n  llm:\n    base_url: http://llm.internal/v1\n")
    llm = AppConfig.load(explicit_path=overlay).ask_your_docs.llm
    assert llm is not None and llm.provider == "auto" and llm.params == ChatParamsConfig()


def test_llm_params_and_provider_parse_from_yaml(tmp_path) -> None:
    """A bare YAML ``thinking: off`` (loaded as False) still means off."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://localhost:8000/v1\n"
        "    provider: vllm\n"
        "    params:\n"
        "      thinking: off\n"
        "      temperature: 0.6\n"
        "      max_tokens: 4096\n",
        encoding="utf-8",
    )
    llm = AppConfig.load(explicit_path=overlay).ask_your_docs.llm
    assert llm is not None and llm.provider == "vllm"
    assert llm.params.model_dump(exclude_none=True) == {
        "thinking": "off",
        "temperature": 0.6,
        "max_tokens": 4096,
    }


def test_llm_provider_is_a_closed_set() -> None:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    with pytest.raises(ValidationError):
        LlmConnectionConfig.model_validate({"provider": "ollama"})


def test_llm_params_thinking_env_overlay(monkeypatch) -> None:
    """The existing PYDOCS_ASK_YOUR_DOCS__LLM__* env tier reaches params too."""
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS__THINKING", "low")
    llm = AppConfig.load().ask_your_docs.llm
    assert llm is not None and llm.params.thinking == "low"


def test_llm_params_numbers_env_overlay_as_json(monkeypatch) -> None:
    """Env leaves are strings, so numbers ride the whole-params JSON form."""
    monkeypatch.setenv(
        "PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS", '{"temperature": 0.2, "max_tokens": 512}'
    )
    llm = AppConfig.load().ask_your_docs.llm
    assert llm is not None and (llm.params.temperature, llm.params.max_tokens) == (0.2, 512)


def test_a_params_error_survives_redaction_while_the_auth_secret_does_not(tmp_path) -> None:
    """Params values are not secrets: their message (value + shape) reaches stderr, while a
    sibling auth token in the same block stays blanked."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        "    auth:\n"
        f"      token_url: {_TOKEN_URL_WITH_SECRET}\n"
        "    params:\n"
        "      temperature: 2.5\n"
        "      reasoning_effort: high\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load(explicit_path=overlay)
    rendered = _rendered(excinfo)
    assert _AUTH_SECRET not in rendered
    assert "<redacted>" in rendered
    assert "ask_your_docs.llm.params.reasoning_effort is refused: use params.thinking" in rendered


def test_a_params_range_error_survives_redaction(tmp_path) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        "    auth:\n"
        f"      token_url: {_TOKEN_URL_WITH_SECRET}\n"
        "    params:\n"
        "      temperature: 2.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load(explicit_path=overlay)
    rendered = _rendered(excinfo)
    assert "temperature: got 2.5, expected a number in [0, 2]" in rendered
    assert _AUTH_SECRET not in rendered
    assert "input_value=2.5" not in rendered  # the input itself is still never echoed
