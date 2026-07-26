"""harness/core/prompt_namespace — the generalized per-architecture resolver.

Exercised against the real ask-your-docs prompt package: any harness prompt
package + the shared core pool compose the same way (own dir wins, core pool
is the fallback, unknown names raise listing both searched locations).
"""

from __future__ import annotations

import pytest

pytest.importorskip("jinja2")

from pydocs_mcp.harness.core.prompt_namespace import HarnessPromptNamespace

_AYD_PACKAGE = "pydocs_mcp.harness.ask_your_docs.prompts"


def test_own_namespace_wins_then_core_pool() -> None:
    ns = HarnessPromptNamespace(_AYD_PACKAGE, "inline")
    assert ns.resolve_source("system_suffix_v1") == "inline"
    assert ns.resolve_source("system_v1") == "shared"
    assert "Image handling:" in ns.render("system_suffix_v1")


def test_absent_architecture_dir_is_pure_fallback() -> None:
    ns = HarnessPromptNamespace(_AYD_PACKAGE, "no_such_architecture")
    assert ns.resolve_source("system_v1") == "shared"
    assert ns.render("rewrite_v1", history="H", question="Q")


def test_unknown_template_raises_listing_both_locations() -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        HarnessPromptNamespace(_AYD_PACKAGE, "inline").render("nope_v1")
    msg = str(excinfo.value)
    assert "inline" in msg and "harness/core/prompts" in msg


def test_names_are_own_union_core_pool() -> None:
    names = HarnessPromptNamespace(_AYD_PACKAGE, "inline").names()
    assert "system_suffix_v1" in names and "system_v1" in names
