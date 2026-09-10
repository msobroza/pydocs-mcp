"""serve_connection × langchain-mcp-adapters: the adapter carries the env map to a REAL child.

Extras-only (``[harness-ask-your-docs]``): the core half lives in ``test_serve_spawn.py``.
The brace-pattern pin reads a PRIVATE adapter attribute on purpose (like
``test_sdk_pins.py``): ``serve_child_env`` mirrors it, so a bump that changes it goes red.
"""

from __future__ import annotations

import logging
import sys
import traceback
from typing import Any

import pytest

pytest.importorskip("langchain_mcp_adapters")

from langchain_mcp_adapters import sessions
from langchain_mcp_adapters.client import MultiServerMCPClient

from pydocs_mcp.harness.ask_your_docs.serve_spawn import serve_connection
from pydocs_mcp.harness.core.serve_child_env import _ADAPTER_EXPANDED_REF

from ._stdio_probe import ENV_PRESENCE_SERVER

_PROBE_CMD = [sys.executable, str(ENV_PRESENCE_SERVER)]
_BRACE_SAMPLES = ("plain", "${X}", "a${NOPE}b", "${}", "$X", "${unterminated", "{X}", "$${Y}z")


def _tool_text(result: Any) -> str:
    """A tool result as text: a bare str, or a list of ``{"type": "text", "text": ...}`` blocks."""
    if isinstance(result, str):
        return result
    return "".join(block["text"] for block in result if block.get("type") == "text")


async def test_adapter_delivers_parent_variable_to_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AYD_ENV_PROBE_SENTINEL", "1")
    client = MultiServerMCPClient({"pydocs": serve_connection("/ws", pydocs_cmd=_PROBE_CMD)})
    (tool,) = [t for t in await client.get_tools() if t.name == "env_presence"]
    assert _tool_text(await tool.ainvoke({"name": "AYD_ENV_PROBE_SENTINEL"})) == "present"


@pytest.mark.parametrize("sample", _BRACE_SAMPLES)
def test_adapter_brace_pattern_matches_ours(sample: str) -> None:
    ours = bool(_ADAPTER_EXPANDED_REF.search(sample))
    assert bool(sessions._BRACED_VAR_RE.search(sample)) == ours


async def test_adapter_startup_failure_leaks_no_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """G8, adapter half: a child that dies before the handshake names no secret anywhere."""
    sentinel = "sk-g8-sentinel-9c1e"
    monkeypatch.setenv("AYD_G8_SENTINEL_KEY", sentinel)
    caplog.set_level(logging.DEBUG)
    dying = [sys.executable, "-c", "import sys; sys.exit(3)"]
    client = MultiServerMCPClient({"pydocs": serve_connection("/ws", pydocs_cmd=dying)})
    with pytest.raises(Exception) as excinfo:  # the type is not the contract (see test_serve_spawn)
        await client.get_tools()
    exc = excinfo.value
    rendered = (str(exc), repr(exc), "".join(traceback.format_exception(exc)), caplog.text)
    assert all(sentinel not in text for text in rendered)
