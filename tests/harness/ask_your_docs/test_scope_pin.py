"""``scope_pin.pinned_args`` — the corpus pin the interceptor forces onto every tool call.

Pulled out of ``agent._intercept`` so the pin is a pure function the activity trace can
reuse to show "actually sent" arguments. Core deps only: the module must not import
langchain (a subprocess check pins that).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from pydocs_mcp.harness.ask_your_docs.scope_pin import pinned_args

_ALL = {"project": "", "package": "", "code": "all"}
_FULL = {"project": "backend", "package": "fastapi", "code": "deps"}


@pytest.mark.parametrize(
    ("tool", "scope", "expected_extra"),
    [
        ("search_codebase", _FULL, {"project": "backend", "package": "fastapi", "scope": "deps"}),
        ("get_overview", _FULL, {"project": "backend", "package": "fastapi"}),
        ("get_symbol", _FULL, {"project": "backend"}),
        ("grep", _FULL, {"project": "backend"}),
        ("search_codebase", _ALL, {}),
        ("search_codebase", {}, {}),
        ("search_codebase", {"code": "project"}, {"scope": "project"}),
        ("get_references", {"package": "fastapi", "code": "deps"}, {}),
    ],
)
def test_pinned_args_matches_the_interceptor_table(
    tool: str, scope: dict[str, str], expected_extra: dict[str, str]
) -> None:
    proposed = {"query": "q"}
    assert pinned_args(tool, proposed, scope) == {**proposed, **expected_extra}


def test_pin_overrides_what_the_model_proposed() -> None:
    proposed = {"query": "q", "project": "model-picked", "scope": "all"}
    assert pinned_args("search_codebase", proposed, _FULL)["project"] == "backend"
    assert pinned_args("search_codebase", proposed, _FULL)["scope"] == "deps"


def test_pinned_args_never_mutates_the_proposed_args() -> None:
    proposed = {"query": "q"}
    pinned_args("search_codebase", proposed, _FULL)
    assert proposed == {"query": "q"}


def test_scope_pin_module_stays_langchain_free() -> None:
    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.scope_pin\n"
        "assert not any(m.startswith('langchain') for m in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
