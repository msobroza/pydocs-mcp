"""harness/core/serve_child_env — the environment policy for OUR serve child.

Core deps only: every test injects ``environ=`` so the developer's shell never
leaks in, and reads the one names-only JSON log line through caplog.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from pydocs_mcp.harness.core import serve_child_env as policy
from pydocs_mcp.harness.core.serve_child_env import (
    clear_serve_child_env_log_memo,
    serve_child_env,
)
from pydocs_mcp.observability.trace_env import (
    TRACE_DIR_ENV_VAR,
    TRACE_ENABLED_ENV_VAR,
    TRACE_TRAJECTORY_ID_ENV_VAR,
    trace_subprocess_env,
)
from pydocs_mcp.retrieval.config.app_config import AppConfig

_LOGGER = "pydocs-mcp.harness.serve-child-env"
_EVENT = "serve_child_env_withheld"


@pytest.fixture(autouse=True)
def _fresh_log_memo(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    clear_serve_child_env_log_memo()
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    yield
    clear_serve_child_env_log_memo()


def _withheld_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _LOGGER and _EVENT in r.getMessage()]


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in _withheld_records(caplog) if r.levelno >= logging.WARNING]


def test_child_env_inherits_parent_variables() -> None:
    environ = {
        "OPENROUTER_API_KEY": "k-dummy",
        "TMPDIR": "/var/folders/x/T/",
        "HTTPS_PROXY": "http://proxy:3128",
        "SSL_CERT_FILE": "/etc/ca.pem",
        "SYSTEMROOT": "C:\\Windows",
        "TEMP": "C:\\Temp",
    }
    assert serve_child_env(environ=environ) == environ


def test_overlay_wins_over_inherited_value() -> None:
    assert serve_child_env({"A": "new"}, environ={"A": "old"})["A"] == "new"


def test_overlay_is_the_only_spelling_of_its_names() -> None:
    env = serve_child_env({TRACE_DIR_ENV_VAR: "/fresh"}, environ={"pydocs_trace__dir": "/stale"})
    spellings = [name for name in env if name.upper() == TRACE_DIR_ENV_VAR]
    assert spellings == [TRACE_DIR_ENV_VAR] and env[TRACE_DIR_ENV_VAR] == "/fresh"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PYDOCS_TRACE__ENABLED", "trace-sentinel-a"),
        ("pydocs_trace__enabled", "trace-sentinel-b"),
        ("Pydocs_Trace__Trajectory_Id", "trace-sentinel-c"),
        ("PYDOCS_TRACE", '{"trajectory_id": "trace-sentinel-json"}'),
    ],
)
def test_inherited_trace_section_is_withheld_in_every_spelling(
    caplog: pytest.LogCaptureFixture, name: str, value: str
) -> None:
    assert name not in serve_child_env(environ={name: value, "KEEP": "1"})
    (record,) = _warnings(caplog)
    assert name in record.getMessage() and value not in caplog.text


def test_explicit_trace_overlay_is_delivered_exactly() -> None:
    overlay = trace_subprocess_env(Path("/t"), "id1")
    stale = {
        TRACE_ENABLED_ENV_VAR: "false",
        TRACE_DIR_ENV_VAR: "/stale",
        TRACE_TRAJECTORY_ID_ENV_VAR: "old-id",
    }
    env = serve_child_env(overlay, environ=stale)
    for name in (TRACE_ENABLED_ENV_VAR, TRACE_DIR_ENV_VAR, TRACE_TRAJECTORY_ID_ENV_VAR):
        assert env[name] == overlay[name]


def test_braced_values_are_dropped_and_logged_by_name_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    environ = {"WEIRD": "a${NOPE}b", "PASSISH": "x${HOME}y", "KEEP": "1"}
    env = serve_child_env(environ=environ)
    assert "WEIRD" not in env and "PASSISH" not in env and env["KEEP"] == "1"
    (record,) = _warnings(caplog)
    payload = json.loads(record.getMessage())
    assert payload["event"] == _EVENT
    assert payload["withheld"]["adapter_expanded_ref"] == ["PASSISH", "WEIRD"]
    assert "a${NOPE}b" not in caplog.text and "x${HOME}y" not in caplog.text


def test_shell_functions_are_dropped_quietly(caplog: pytest.LogCaptureFixture) -> None:
    environ = {"BASH_FUNC_foo%%": "() { echo ${x}; }", "OLDFN": "() { :; }", "KEEP": "1"}
    env = serve_child_env(environ=environ)
    assert "BASH_FUNC_foo%%" not in env and "OLDFN" not in env and env["KEEP"] == "1"
    assert _warnings(caplog) == []


def test_nothing_withheld_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    serve_child_env({"B": "2"}, environ={"A": "1"})
    assert _withheld_records(caplog) == []


def test_warning_fires_once_per_distinct_withheld_set(caplog: pytest.LogCaptureFixture) -> None:
    serve_child_env(environ={"WEIRD": "${X}"})
    serve_child_env(environ={"WEIRD": "${X}"})
    assert len(_warnings(caplog)) == 1
    serve_child_env(environ={"OTHER": "${Y}"})
    assert len(_warnings(caplog)) == 2


def test_result_is_a_fresh_dict_and_parent_is_untouched() -> None:
    environ = {"A": "1", "WEIRD": "${X}", "PYDOCS_TRACE__DIR": "/d"}
    before = dict(environ)
    os_before = dict(os.environ)
    env = serve_child_env({"B": "2"}, environ=environ, seal_config_tier=True)
    default_env = serve_child_env({"B": "2"})
    assert environ == before and env is not environ
    assert default_env is not os.environ and dict(os.environ) == os_before


@pytest.mark.parametrize(
    "name",
    [
        "PYDOCS_SEARCH__TOP_K",
        "pydocs_pipelines__x",
        "PYDOCS_SERVE__DESCRIPTIONS_PATH",
        "PYDOCS_EMBEDDING__BASE_URL",
        "PYDOCS_CONFIG_PATH",
        "PYDOCS_ASK_YOUR_DOCS",
    ],
)
def test_sealed_tier_withholds_config_overlays_case_insensitively(name: str) -> None:
    environ = {name: "overlay-value"}
    assert name not in serve_child_env(environ=environ, seal_config_tier=True)
    assert serve_child_env(environ=environ)[name] == "overlay-value"


def test_sealed_tier_keeps_cache_dir_and_credentials(caplog: pytest.LogCaptureFixture) -> None:
    kept = {
        "PYDOCS_CACHE_DIR": "/sandbox/.pydocs-mcp",
        "OPENAI_API_KEY": "k1",
        "OPENROUTER_API_KEY": "k2",
        "TMPDIR": "/tmp/x",
    }
    env = serve_child_env(environ={**kept, "PYDOCS_SEARCH__TOP_K": "3"}, seal_config_tier=True)
    assert {name: env[name] for name in kept} == kept
    (record,) = _warnings(caplog)
    assert "PYDOCS_CACHE_DIR" not in caplog.text
    assert json.loads(record.getMessage())["sealed_config_tier"] is True


def test_sealed_tier_withholds_endpoint_variables(caplog: pytest.LogCaptureFixture) -> None:
    environ = {"OPENAI_BASE_URL": "http://shell-gw/v1", "LLM_MODEL": "shell-model"}
    sealed = serve_child_env(environ=environ, seal_config_tier=True)
    assert "OPENAI_BASE_URL" not in sealed and "LLM_MODEL" not in sealed
    (record,) = _warnings(caplog)
    payload = json.loads(record.getMessage())
    assert payload["withheld"] == {"endpoint_tier_sealed": ["LLM_MODEL", "OPENAI_BASE_URL"]}
    assert "shell-gw" not in caplog.text and "shell-model" not in caplog.text
    assert serve_child_env(environ=environ) == environ


def test_config_prefix_matches_appconfig_env_prefix() -> None:
    assert AppConfig.model_config["env_prefix"] == policy._CONFIG_TIER_PREFIX
    assert policy._TRACE_SECTION_ENV_VAR == "PYDOCS_TRACE"


def test_query_prefix_env_withheld_only_by_the_sealed_tier() -> None:
    # embedding.query_prefix via env reaches inheriting ask-your-docs children,
    # but the sealed eval/harness binding must set it in the child's --config.
    environ = {"PYDOCS_EMBEDDING__QUERY_PREFIX": "Instruct: x\nQuery:", "PYDOCS_CACHE_DIR": "/c"}
    sealed = serve_child_env(environ=environ, seal_config_tier=True)
    assert "PYDOCS_EMBEDDING__QUERY_PREFIX" not in sealed
    assert sealed["PYDOCS_CACHE_DIR"] == "/c"
    inherited = serve_child_env(environ=environ)
    assert inherited["PYDOCS_EMBEDDING__QUERY_PREFIX"] == "Instruct: x\nQuery:"
