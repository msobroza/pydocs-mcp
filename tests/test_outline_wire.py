"""Wire-level checks for the budgeted outline at ``get_symbol(depth="tree")``.

Indexes a tiny project through ``tests/_index_fixture`` and drives the frozen
tool end to end — MCP through ``ToolRouter``, CLI through a CLI-surface router
built from the same bundle — so the outline, its items[] rows, its level cut
and its footer are asserted on a REAL index rather than on a hand-built tree.

The tree depth used to render the nested PageIndex JSON, unbounded (ADR 0008
measured one module at 5,825 tokens). It now renders the **outline**: one
compact line per node, fitted to ``symbol_outline.token_budget`` by level cut,
with the ``levels L of D shown, N nodes elided`` footer and recovery pointers
at the largest elided subtrees (ADR 0023 (b), (f)).

Sibling of ``tests/test_symbol_card_wire.py``, which pins the other cheap depth.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from pydocs_mcp.application.formatting import strip_pointers
from pydocs_mcp.application.mcp_inputs import SymbolInput
from pydocs_mcp.application.tool_response import SymbolEnvelope, ToolResponse
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import _to_call_tool_result, build_routers
from tests._index_fixture import index_project_to_db

_PYPROJECT = """\
[project]
name = "outlineproj"
version = "0.0.0"
dependencies = []
"""

_MOD_PY = '''\
"""The small module every outline test reads."""


class Alpha:
    """A documented class."""

    ATTR = 1

    def run(self) -> int:
        """Run it."""
        return 1

    @staticmethod
    def stat() -> int:
        return 2


def beta() -> int:
    """A documented function."""
    return 1
'''

# A module whose whole outline cannot fit the shipped 2048-token budget: 60
# classes × 5 methods = 361 nodes over 3 levels.
_BIG_CLASSES = 60
_BIG_METHODS = 5
_BIG_ELIDED = _BIG_CLASSES * _BIG_METHODS


def _big_class(index: int) -> str:
    methods = "\n".join(
        f"    def m{m}(self) -> int:\n        return {m}" for m in range(_BIG_METHODS)
    )
    return f"class C{index:02d}:\n{methods}"


_BIG_PY = (
    '"""A big module."""\n\n\n'
    + "\n\n\n".join(_big_class(index) for index in range(_BIG_CLASSES))
    + "\n"
)


def _write_project(root: Path) -> Path:
    project = root / "outlineproj"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD_PY)
    (pkg / "big.py").write_text(_BIG_PY)
    return project


@dataclass(frozen=True, slots=True)
class _WiredOutlines:
    """One real bundle plus a router per surface (pointers differ by surface)."""

    db_path: Path
    mcp_router: ToolRouter
    cli_router: ToolRouter


@pytest.fixture(scope="module")
def wired(tmp_path_factory: pytest.TempPathFactory) -> _WiredOutlines:
    # Module-scoped: indexing the 361-node module once keeps the file fast.
    tmp_path = tmp_path_factory.mktemp("outline")
    project = _write_project(tmp_path)
    db_path = index_project_to_db(project, tmp_path / "outline.db")
    config = AppConfig.load()
    mcp_router, _m = build_routers(config, db_path=db_path, surface="mcp")
    cli_router, _c = build_routers(config, db_path=db_path, surface="cli")
    return _WiredOutlines(db_path=db_path, mcp_router=mcp_router, cli_router=cli_router)


def _symbol(router: ToolRouter, target: str, depth: str = "tree") -> ToolResponse:
    return asyncio.run(router.get_symbol(SymbolInput(target=target, depth=depth)))


def _validated(response: ToolResponse) -> SymbolEnvelope:
    """Validate exactly as ``server.py`` does, then re-read the typed envelope."""
    _to_call_tool_result(response, SymbolEnvelope)
    return SymbolEnvelope.model_validate(response.structured())


def _body_lines(text: str) -> list[str]:
    """The outline's node lines alone — envelope frame, cut footer, pointers out."""
    skipped = ("[index:", "[⚠", "[truncated:", "- ", "levels ", "Together:", "Then:")
    return [
        line
        for line in text.splitlines()
        if line.strip() and not line.startswith(skipped) and not line.lstrip().startswith("→")
    ]


def _footer(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("levels "))


# ── the small module renders whole ─────────────────────────────────────────


def test_a_small_module_renders_its_whole_outline_as_compact_text(wired: _WiredOutlines) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.mod"))
    assert _body_lines(envelope.text) == [
        "module pkg.mod · pkg/mod.py:1-20",
        "  class pkg.mod.Alpha · 4-15",
        "    method pkg.mod.Alpha.run · 9-11",
        "    method pkg.mod.Alpha.stat · 14-15",
        "  function pkg.mod.beta · 18-20",
    ]
    # The outline is text: the nested JSON both cheap depths used to send is gone.
    assert '"nodes"' not in envelope.text


def test_a_small_module_is_not_marked_truncated(wired: _WiredOutlines) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.mod"))
    assert envelope.meta.truncated is False
    assert "levels " not in envelope.text


def test_the_outline_items_mirror_its_lines(wired: _WiredOutlines) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.mod"))
    assert [item.qualified_name for item in envelope.items] == [
        "pkg.mod",
        "pkg.mod.Alpha",
        "pkg.mod.Alpha.run",
        "pkg.mod.Alpha.stat",
        "pkg.mod.beta",
    ]
    assert [item.kind for item in envelope.items] == [
        "module",
        "class",
        "method",
        "method",
        "function",
    ]
    assert all(item.path == "pkg/mod.py" for item in envelope.items)


# ── the class target ───────────────────────────────────────────────────────


def test_a_class_target_outlines_the_class_and_its_methods(wired: _WiredOutlines) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.mod.Alpha"))
    lines = _body_lines(envelope.text)
    assert lines[0].startswith("class pkg.mod.Alpha · pkg/mod.py:")
    assert [line.strip().split(" · ")[0] for line in lines[1:]] == [
        "method pkg.mod.Alpha.run",
        "method pkg.mod.Alpha.stat",
    ]
    assert [item.qualified_name for item in envelope.items] == [
        "pkg.mod.Alpha",
        "pkg.mod.Alpha.run",
        "pkg.mod.Alpha.stat",
    ]


def test_a_childless_symbol_outlines_to_one_line(wired: _WiredOutlines) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.mod.beta"))
    assert len(_body_lines(envelope.text)) == 1
    assert [item.qualified_name for item in envelope.items] == ["pkg.mod.beta"]


# ── the level cut ──────────────────────────────────────────────────────────


def test_a_module_over_the_budget_is_cut_to_whole_levels(wired: _WiredOutlines) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.big"))
    lines = _body_lines(envelope.text)
    assert lines[0].startswith("module pkg.big · pkg/big.py:")
    # The method level is gone; every class survives, none of them indented deeper.
    assert len(lines) == 1 + _BIG_CLASSES
    assert all(line.startswith("  class pkg.big.C") for line in lines[1:])


def test_a_cut_outline_ends_in_the_footer_naming_the_cut(wired: _WiredOutlines) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.big"))
    assert _footer(envelope.text) == f"levels 2 of 3 shown, {_BIG_ELIDED} nodes elided"
    assert envelope.meta.truncated is True


def test_a_cut_outline_offers_recovery_pointers_at_the_largest_elided_subtrees(
    wired: _WiredOutlines,
) -> None:
    text = _symbol(wired.mcp_router, "pkg.big").text
    pointers = [line.strip() for line in text.splitlines() if line.lstrip().startswith("→")]
    count = AppConfig.load().symbol_outline.recovery_pointer_count
    # Every class elided the same five methods, so the tie falls to document order.
    assert pointers == [
        f'→ get_symbol(target="pkg.big.C{index:02d}", depth="tree")' for index in range(count)
    ]


def test_a_cut_outline_prunes_its_items_to_the_nodes_the_text_shows(
    wired: _WiredOutlines,
) -> None:
    """The one place items[] is narrowed to the text (ADR 0023, vs ADR 0010/0011)."""
    envelope = _validated(_symbol(wired.mcp_router, "pkg.big"))
    assert [item.qualified_name for item in envelope.items] == ["pkg.big"] + [
        f"pkg.big.C{index:02d}" for index in range(_BIG_CLASSES)
    ]
    assert not any(".m" in item.qualified_name for item in envelope.items)


def test_the_cut_body_is_far_smaller_than_the_tree_it_stands_for(wired: _WiredOutlines) -> None:
    """The point of the budget: the cheap-looking depth stays cheap."""
    from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens

    budget = AppConfig.load().symbol_outline.token_budget
    assert count_tokens(_symbol(wired.mcp_router, "pkg.big").text, "") <= budget


# ── the YAML knobs ─────────────────────────────────────────────────────────


def _router_with(wired: _WiredOutlines, tmp_path: Path, overlay_yaml: str) -> ToolRouter:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(overlay_yaml)
    config = AppConfig.load(explicit_path=overlay)
    router, _s = build_routers(config, db_path=wired.db_path, surface="mcp")
    return router


def test_a_budget_too_small_for_one_level_trims_children_per_parent(
    wired: _WiredOutlines, tmp_path: Path
) -> None:
    router = _router_with(wired, tmp_path, "symbol_outline:\n  token_budget: 120\n")
    text = _symbol(router, "pkg.big").text
    lines = _body_lines(text)
    assert lines[0].startswith("module pkg.big · ")
    assert lines[-1].strip().startswith("and ")
    assert lines[-1].strip().endswith(" more")
    assert _footer(text).startswith("levels 2 of 3 shown, ")


def test_the_token_budget_is_a_yaml_knob(wired: _WiredOutlines, tmp_path: Path) -> None:
    """``symbol_outline.token_budget: 0`` is the documented off switch."""
    router = _router_with(wired, tmp_path, "symbol_outline:\n  token_budget: 0\n")
    envelope = _validated(_symbol(router, "pkg.big"))
    assert len(envelope.items) == 1 + _BIG_CLASSES + _BIG_ELIDED
    assert envelope.meta.truncated is False


def test_the_recovery_pointer_count_is_a_yaml_knob(wired: _WiredOutlines, tmp_path: Path) -> None:
    router = _router_with(wired, tmp_path, "symbol_outline:\n  recovery_pointer_count: 1\n")
    text = _symbol(router, "pkg.big").text
    assert [line for line in text.splitlines() if line.lstrip().startswith("→")] == [
        '→ get_symbol(target="pkg.big.C00", depth="tree")'
    ]


# ── both surfaces ──────────────────────────────────────────────────────────


def _without_pointer_lines(text: str) -> str:
    """The outline minus its resolved follow-up calls — the surface-shared part.

    Both the cut's own recovery pointers (own-line ``→``) and the closing bundle
    (``Together:`` / ``Then:``) render per surface, so parity is asserted on what
    is left plus, separately, on the call COUNT of each bundle line.
    """
    dropped = ("→", "Together:", "Then:")
    return "\n".join(
        line for line in strip_pointers(text).splitlines() if not line.startswith(dropped)
    )


@pytest.mark.parametrize("target", ["pkg.mod", "pkg.mod.Alpha", "pkg.big"])
def test_cli_and_mcp_render_the_same_outline(wired: _WiredOutlines, target: str) -> None:
    mcp = _symbol(wired.mcp_router, target)
    cli = _symbol(wired.cli_router, target)
    assert _without_pointer_lines(cli.text) == _without_pointer_lines(mcp.text)
    assert cli.items == mcp.items


def test_the_recovery_pointers_render_in_each_surfaces_own_syntax(
    wired: _WiredOutlines,
) -> None:
    assert (
        "→ pydocs-mcp symbol pkg.big.C00 --depth tree" in _symbol(wired.cli_router, "pkg.big").text
    )
    assert (
        '→ get_symbol(target="pkg.big.C00", depth="tree")'
        in _symbol(wired.mcp_router, "pkg.big").text
    )


def test_the_cli_verb_prints_the_outline_and_its_footer(
    wired: _WiredOutlines, capsys: pytest.CaptureFixture
) -> None:
    from pydocs_mcp.__main__ import main

    argv = shlex.split("pydocs-mcp symbol pkg.big --depth tree")
    with patch("sys.argv", argv + ["--db", str(wired.db_path)]):
        assert main() == 0
    out = capsys.readouterr().out
    assert "module pkg.big · pkg/big.py:" in out
    assert f"levels 2 of 3 shown, {_BIG_ELIDED} nodes elided" in out
    assert '"nodes"' not in out
