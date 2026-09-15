"""The ``ask_your_docs.scope`` config block (UI spec
2026-09-04-ask-your-docs-branch-scope-ui-design §6.2, §7 — AC-22).

Core-suite tests — pydantic only, no [harness-ask-your-docs] extra needed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig


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


def test_scope_defaults_yaml_matches_pydantic_defaults() -> None:
    """AC-22: the shipped YAML scope block equals ScopeDefaultsConfig()."""
    assert AppConfig.load().ask_your_docs.scope == ScopeDefaultsConfig()


def test_scope_branch_default_is_a_closed_vocabulary() -> None:
    with pytest.raises(ValidationError):
        ScopeDefaultsConfig(branch_default="main")


def test_scope_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        ScopeDefaultsConfig(branches="main")


def test_scope_max_cells_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__MAX_CELLS", "2")
    assert AppConfig.load().ask_your_docs.scope.max_cells == 2


def test_scope_rejects_a_slice_with_dependencies_only() -> None:
    """E11 at config load: deps and changed/diff are disjoint server slices."""
    with pytest.raises(ValidationError) as excinfo:
        ScopeDefaultsConfig(slice="diff_hunks", code="deps")
    assert "diff_hunks" in str(excinfo.value) and "deps" in str(excinfo.value)


def test_scope_config_lives_in_its_own_module_and_keeps_its_import_path() -> None:
    """Split out for ``ask_your_docs_models.py``'s line budget; the old path re-exports it."""
    from pydocs_mcp.retrieval.config import ask_your_docs_models
    from pydocs_mcp.retrieval.config.ask_your_docs_scope_models import ScopeDefaultsConfig

    assert ask_your_docs_models.ScopeDefaultsConfig is ScopeDefaultsConfig
    assert "ScopeDefaultsConfig" in ask_your_docs_models.__all__
    assert ask_your_docs_models.AskYourDocsConfig().scope == ScopeDefaultsConfig()


def test_scope_tokens_and_footer_hint_default_on() -> None:
    """AC-22 (D14): the two D14 keys default to true in BOTH sources; `is`, not `==`,
    so an int 1 from a sloppy YAML layer cannot pass (the bool-coercion trap)."""
    from pydocs_mcp.retrieval.config.ask_your_docs_scope_models import ScopeDefaultsConfig

    scope = AppConfig.load().ask_your_docs.scope
    assert scope.tokens_enabled is True and scope.footer_hint is True  # the YAML source
    bare = ScopeDefaultsConfig()  # the Field source — the shipped YAML would mask a flip here
    assert bare.tokens_enabled is True and bare.footer_hint is True
    assert ScopeDefaultsConfig(tokens_enabled=False, footer_hint=False).tokens_enabled is False


def test_scope_tokens_enabled_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__SCOPE__TOKENS_ENABLED", "false")
    scope = AppConfig.load().ask_your_docs.scope
    assert scope.tokens_enabled is False
    assert scope.footer_hint is True  # the sibling key is untouched by the override


def test_default_yaml_ships_exactly_nine_scope_keys() -> None:
    """The exact sorted key list — a count would pass with one key renamed."""
    shipped = _shipped_default_config()
    assert sorted(shipped["ask_your_docs"]["scope"]) == [
        "branch_default",
        "branch_name",
        "code",
        "footer_hint",
        "max_cells",
        "package",
        "project",
        "slice",
        "tokens_enabled",
    ]
