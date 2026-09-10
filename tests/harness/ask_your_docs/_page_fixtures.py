"""The AppTest scaffolding every page test shares: the config writer, the env fixture, the seams.

One home rather than one copy per module (the sibling of ``_connection_fakes``): a page test
that seeds ``connection_bearer`` / ``connection_list_models`` / ``connection_transport`` and a
page test that spies on the caches want the SAME page, and a second copy of ``_page_env`` is
how one of them keeps a bearer registry the other cleared.

Import ``page_env`` into a test module to arm it — it is autouse, so importing is arming.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

from pydocs_mcp.harness.ask_your_docs.cli import LAUNCH_BASE_URL_ENV_VAR, LAUNCH_MODEL_ENV_VAR
from pydocs_mcp.harness.ask_your_docs.connection_dialog import KEY_OPEN
from pydocs_mcp.harness.ask_your_docs.llm_connection import clear_bearer_registry
from pydocs_mcp.harness.ask_your_docs.model_listing import clear_model_listing_cache
from pydocs_mcp.harness.ask_your_docs.multimodal import clear_detection_cache

from ._connection_fakes import FakeModelsEndpoint
from ._serve_session_fakes import FakeServeToolsOpener

MODEL_IDS = ("model-a", "model-b")
TOKEN_URL = "http://localhost:8899/access-token"
PAGE_LOGGER = "pydocs-mcp.harness.ask-your-docs"  # app.py's logger name
# The page is LOCATED, never imported: importing it would execute the Streamlit script in bare
# mode at collection (a wall of missing-ScriptRunContext warnings plus a stray connection_resolved
# record). AppTest re-executes the file itself on every run.
_APP_SPEC = importlib.util.find_spec("pydocs_mcp.harness.ask_your_docs.app")
APP_PATH = _APP_SPEC.origin if _APP_SPEC is not None else ""


def write_config(
    tmp_path: Path,
    *,
    base_url="https://llm.internal/v1",
    model=None,
    auth="token",
    vision="true",
    endpoint_probe=False,
) -> str:
    lines = ["ask_your_docs:", "  llm:", f"    base_url: {base_url}"]
    if model:
        lines.append(f"    model: {model}")
    if auth == "token":
        lines += ["    auth:", f"      token_url: {TOKEN_URL}"]
    elif auth == "env":
        lines += ["    auth:", "      api_key_env: LLM_KEY"]
    if vision is not None:
        lines.append(f"    vision: {vision}")
    if endpoint_probe:  # the opt-in rung 3: the ladder fetches the bearer at render
        lines += ["  multimodal:", "    detection:", "      endpoint_probe: true"]
    path = tmp_path / "pydocs.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def page_env(tmp_path: Path, monkeypatch):
    """A workspace, no inherited connection variables, and every page cache emptied."""
    (tmp_path / "ws").mkdir()
    monkeypatch.setenv("PYDOCS_WORKSPACE", str(tmp_path / "ws"))
    for var in (
        "PYDOCS_CONFIG",
        "OPENAI_BASE_URL",
        "LLM_MODEL",
        "OPENAI_API_KEY",
        "LLM_KEY",
        LAUNCH_BASE_URL_ENV_VAR,
        LAUNCH_MODEL_ENV_VAR,
    ):
        monkeypatch.delenv(var, raising=False)
    clear_bearer_registry()
    clear_model_listing_cache()
    clear_detection_cache()
    st.cache_resource.clear()  # the identity-keyed page caches persist across AppTest runs
    yield
    clear_bearer_registry()
    clear_model_listing_cache()
    clear_detection_cache()
    st.cache_resource.clear()


def page(**seeds) -> AppTest:
    """One AppTest over the real page, with its session-state seams pre-seeded.

    ``serve_tools_opener`` defaults to a named fake, so no page test spawns a serve child."""
    at = AppTest.from_file(APP_PATH, default_timeout=180)
    at.session_state["connection_list_models"] = seeds.pop(
        "listing", FakeModelsEndpoint(ids=MODEL_IDS)
    )
    at.session_state["serve_tools_opener"] = seeds.pop("serve_tools_opener", FakeServeToolsOpener())
    for key, value in seeds.items():
        at.session_state[key] = value
    return at


def status_line(at: AppTest) -> str:
    return next(c.value for c in at.caption if " · " in c.value and "vision:" in c.value)


def open_dialog(at: AppTest) -> AppTest:
    at.run()
    assert not at.exception, at.exception
    at.button(key=KEY_OPEN).click().run()
    assert not at.exception, at.exception
    return at
