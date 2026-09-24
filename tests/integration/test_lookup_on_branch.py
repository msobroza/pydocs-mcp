"""#313 end to end on a real git repository: the symbol tools answer from the selected branch.

``main`` holds ``alpha`` and ``beta`` (``beta`` calls ``alpha``; ``run`` calls
``beta``); ``feature/x`` prepends ``gamma`` (``gamma`` calls ``alpha``; ``run``
calls ``gamma``) and rewrites ``beta`` so it no longer calls ``alpha``. Both
branches are indexed into one bundle through ``pydocs-mcp index`` itself, then
read through the real composition root:

- a symbol that exists only on ``feature/x`` resolves there and is unknown on
  ``main`` — without the miss message offering the other branch's name (AC1);
- impact, callers and the context closure follow the selected branch's edges
  only (AC2);
- the overview names the branch it describes and counts only its members (AC3);
- ``depth="source"`` renders the selected branch's text at its own span;
- a request naming no branch reads the served branch — no member, symbol or
  decision of the other branch leaks into it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    OverviewInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.db import cache_path_for_project
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._git_sandbox import isolate_git_config, requires_git, run_git
from tests.application._router_fakes import with_branch

pytestmark = requires_git

MAIN, FEATURE = "main", "feature/x"
_ALPHA = '\n\ndef alpha(row: str) -> int:\n    """Alpha counts a row."""\n    return len(row)\n'
_BETA_MAIN = (
    '\n\ndef beta(row: str) -> int:\n    """Beta scales a row."""\n    return 2 * alpha(row)\n'
)
_BETA_FEATURE = (
    '\n\ndef beta(row: str) -> int:\n    """Beta triples a row."""\n    return 3 * len(row)\n'
)
_GAMMA = (
    '\n\ndef gamma(row: str) -> int:\n    """Gamma exists only on the feature branch."""\n'
    "    return 3 * alpha(row)\n"
)
_RUN = '"""Main."""\nfrom app.core import {name}\n\n\ndef run() -> int:\n    """Run it."""\n    return {name}("x")\n'
_ADR = (
    "# 1. Keep rows in memory\n\n## Status\n\nAccepted\n\n## Decision\n\nWe keep rows in memory.\n"
)


@pytest.fixture(autouse=True)
def _sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


def _two_branch_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "docs" / "adr" / "0001-memory.md").write_text(_ADR, encoding="utf-8")
    core, main = root / "app" / "core.py", root / "app" / "main.py"
    core.write_text('"""Core."""' + _ALPHA + _BETA_MAIN, encoding="utf-8")
    main.write_text(_RUN.format(name="beta"), encoding="utf-8")
    run_git(root, "init", "-q", "-b", MAIN)
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "init")
    run_git(root, "switch", "-q", "-c", FEATURE)
    core.write_text('"""Core."""' + _GAMMA + _ALPHA + _BETA_FEATURE, encoding="utf-8")
    main.write_text(_RUN.format(name="gamma"), encoding="utf-8")
    run_git(root, "commit", "-q", "-am", "gamma replaces beta in run")
    run_git(root, "switch", "-q", MAIN)
    return root


def _cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *argv])
    return _cli_main()


@pytest.fixture
def router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    root = _two_branch_project(tmp_path)
    flags = ("--no-inspect", "--skip-deps")
    assert _cli(monkeypatch, "index", str(root), *flags) == 0
    assert _cli(monkeypatch, "index", str(root), *flags, "--branch", FEATURE) == 0
    db = cache_path_for_project(root.resolve())
    built, _services = build_routers(AppConfig.load(), db_path=db, surface="mcp")
    return built


def _call(router: Any, method: str, payload: Any, branch: str = "") -> Any:
    selected = with_branch(payload, branch) if branch else payload
    return asyncio.run(getattr(router, method)(selected))


def _names(items: tuple[dict[str, Any], ...], key: str = "qualified_name") -> set[str]:
    return {str(item[key]) for item in items}


def test_a_symbol_only_on_the_branch_resolves_there_and_is_unknown_on_main(router: Any) -> None:
    gamma = SymbolInput(target="app.core.gamma")
    on_feature = _call(router, "get_symbol", gamma, FEATURE)
    assert on_feature.meta["branch"] == FEATURE
    assert "Gamma exists only on the feature branch" in on_feature.text
    assert _names(on_feature.items) == {"app.core.gamma"}
    with pytest.raises(NotFoundError) as caught:
        _call(router, "get_symbol", gamma)
    # The miss names nothing of feature/x: the closest-name candidates are main's.
    assert "gamma" not in str(caught.value).split("not found", 1)[1]
    with pytest.raises(NotFoundError):
        _call(router, "get_symbol", gamma, MAIN)


def test_the_source_depth_renders_the_selected_branch_text_at_its_own_span(router: Any) -> None:
    beta = SymbolInput(target="app.core.beta", depth="source")
    on_main = _call(router, "get_symbol", beta)
    on_feature = _call(router, "get_symbol", beta, FEATURE)
    assert "2 * alpha(row)" in on_main.text and "3 * len(row)" not in on_main.text
    assert "3 * len(row)" in on_feature.text and "2 * alpha(row)" not in on_feature.text
    alpha = SymbolInput(target="app.core.alpha", depth="source")
    # alpha is ONE chunk row on both branches; gamma pushes it down on feature/x.
    main_row = _call(router, "get_symbol", alpha).items[0]
    feature_row = _call(router, "get_symbol", alpha, FEATURE).items[0]
    assert (main_row["start_line"], feature_row["start_line"]) == (3, 8)
    gamma = SymbolInput(target="app.core.gamma", depth="source")
    assert "3 * alpha(row)" in _call(router, "get_symbol", gamma, FEATURE).text
    with pytest.raises(NotFoundError):
        _call(router, "get_symbol", gamma)


def test_impact_on_a_branch_follows_that_branch_edges_only(router: Any) -> None:
    impact = ReferencesInput(target="app.core.alpha", direction="impact")
    on_main = _call(router, "get_references", impact).text
    on_feature = _call(router, "get_references", impact, FEATURE).text
    assert "`app.core.beta`" in on_main and "`app.core.gamma`" not in on_main
    assert "`app.core.gamma`" in on_feature and "`app.core.beta`" not in on_feature
    assert "`app.main.run`" in on_main and "`app.main.run`" in on_feature


def test_callers_and_the_context_closure_are_branch_facts(router: Any) -> None:
    callers = ReferencesInput(target="app.core.alpha", direction="callers")
    on_main = _call(router, "get_references", callers)
    on_feature = _call(router, "get_references", callers, FEATURE)
    assert _names(on_main.items, "from_qualified_name") == {"app.core.beta"}
    assert _names(on_feature.items, "from_qualified_name") == {"app.core.gamma"}
    # The caller row's span is the selected branch's tree node.
    (gamma_row,) = on_feature.items
    assert (gamma_row["start_line"], gamma_row["end_line"]) == (3, 5)
    context = ContextInput(targets=["app.main.run"])
    assert "app.core.beta" in _call(router, "get_context", context).text
    on_feature_context = _call(router, "get_context", context, FEATURE).text
    assert "app.core.gamma" in on_feature_context and "app.core.beta" not in on_feature_context
    # The packed text is the selected branch's row: beta has one per branch,
    # and the focus renders its signature and docstring.
    beta = ContextInput(targets=["app.core.beta"])
    packed_on_main = _call(router, "get_context", beta).text
    packed_on_feature = _call(router, "get_context", beta, FEATURE).text
    assert "Beta scales a row" in packed_on_main and "Beta triples" not in packed_on_main
    assert "Beta triples a row" in packed_on_feature and "Beta scales" not in packed_on_feature


def _overview_title(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("# Overview"))


def test_the_overview_names_the_branch_it_describes(router: Any) -> None:
    on_main = _call(router, "get_overview", OverviewInput())
    on_feature = _call(router, "get_overview", OverviewInput(), FEATURE)
    assert _overview_title(on_main.text) == "# Overview — __project__ · branch main"
    assert _overview_title(on_feature.text) == "# Overview — __project__ · branch feature/x"
    # Symbols are the branch's own members: main has alpha, beta, run.
    assert "· 3 symbols ·" in on_main.text and "· 4 symbols ·" in on_feature.text


def test_a_request_naming_no_branch_never_lists_the_other_branch_members(router: Any) -> None:
    package_card = _call(router, "get_symbol", SymbolInput(target="__project__")).text
    assert "gamma" not in package_card and "alpha" in package_card
    by_name = SearchInput(query="gamma", kind="api")
    assert _call(router, "search_codebase", by_name).items == ()
    assert _names(_call(router, "search_codebase", by_name, FEATURE).items) == {"app.core.gamma"}


def test_a_member_hit_carries_the_selected_branch_span(router: Any) -> None:
    """#312 left member-hit spans on the served branch's tree; they now come
    from the branch the search pinned."""
    alpha = SearchInput(query="alpha", kind="api")
    (on_main,) = _call(router, "search_codebase", alpha).items
    (on_feature,) = _call(router, "search_codebase", alpha, FEATURE).items
    assert (on_main["start_line"], on_feature["start_line"]) == (3, 8)


def test_decisions_are_read_on_the_selected_branch(router: Any) -> None:
    why = WhyInput(query="keep rows in memory")
    on_main = _call(router, "get_why", why)
    assert [item["title"] for item in on_main.items] == ["Keep rows in memory"]
    # O10: decision mining per branch is P2, so feature/x carries no decision
    # rows in P1 — it must not answer with main's.
    assert _call(router, "get_why", why, FEATURE).items == ()
