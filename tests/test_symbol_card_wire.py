"""Wire-level checks for the symbol card at ``get_symbol(depth="summary")``.

Indexes a tiny project through ``tests/_index_fixture`` and drives the frozen
tool end to end — MCP through ``ToolRouter``, CLI through a CLI-surface router
built from the same bundle — so the card, its items[] rows and its cap are
asserted on a REAL index rather than on a hand-built tree.

The summary depth used to render the same nested PageIndex JSON the tree depth
renders (ADR 0023 Evidence). It now renders the **symbol card**: signature,
first doc line, and the names of the immediate children under the YAML card cap
(``symbol_card.child_cap``). The tree depth is deliberately untouched here — it
becomes the budgeted outline in its own change.
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
name = "cardproj"
version = "0.0.0"
dependencies = []
"""

_MOD_PY = '''\
"""The module every card test reads."""


class Alpha:
    """A documented class."""

    ATTR = 1

    def run(self) -> int:
        """Run it."""
        return 1

    @staticmethod
    def stat() -> int:
        return 2


class Bare:
    ATTR = 2


def beta() -> int:
    """A documented function."""
    return 1
'''

# 25 top-level functions — five past the shipped card cap of 20.
_WIDE_MEMBERS = 25
_WIDE_PY = '"""A wide module."""\n\n\n' + "\n\n".join(
    f"def f{i:02d}() -> int:\n    return {i}" for i in range(_WIDE_MEMBERS)
)


def _write_project(root: Path) -> Path:
    project = root / "cardproj"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD_PY)
    (pkg / "wide.py").write_text(_WIDE_PY)
    return project


@dataclass(frozen=True, slots=True)
class _WiredCards:
    """One real bundle plus a router per surface (pointers differ by surface)."""

    db_path: Path
    mcp_router: ToolRouter
    cli_router: ToolRouter


@pytest.fixture
def wired(tmp_path: Path) -> _WiredCards:
    project = _write_project(tmp_path)
    db_path = index_project_to_db(project, tmp_path / "card.db")
    config = AppConfig.load()
    mcp_router, _m = build_routers(config, db_path=db_path, surface="mcp")
    cli_router, _c = build_routers(config, db_path=db_path, surface="cli")
    return _WiredCards(db_path=db_path, mcp_router=mcp_router, cli_router=cli_router)


def _symbol(router: ToolRouter, target: str, depth: str = "summary") -> ToolResponse:
    return asyncio.run(router.get_symbol(SymbolInput(target=target, depth=depth)))


def _validated(response: ToolResponse) -> SymbolEnvelope:
    """Validate exactly as ``server.py`` does, then re-read the typed envelope."""
    _to_call_tool_result(response, SymbolEnvelope)
    return SymbolEnvelope.model_validate(response.structured())


def _members_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(("Members (", "No members")))


# ── the class card ─────────────────────────────────────────────────────────


def test_class_card_renders_signature_doc_line_and_child_names(wired: _WiredCards) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.mod.Alpha"))
    lines = envelope.text.splitlines()
    header = next(line for line in lines if line.startswith("class Alpha"))
    assert header.startswith("class Alpha · pkg.mod.Alpha · pkg/mod.py:")
    assert "A documented class." in lines
    assert _members_line(envelope.text) == "Members (2): run, stat"
    # The card is text, never the PageIndex JSON the summary depth used to send.
    assert '"nodes"' not in envelope.text


def test_class_card_items_carry_the_target_and_its_listed_children(wired: _WiredCards) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.mod.Alpha"))
    assert [i.qualified_name for i in envelope.items] == [
        "pkg.mod.Alpha",
        "pkg.mod.Alpha.run",
        "pkg.mod.Alpha.stat",
    ]
    assert [i.kind for i in envelope.items] == ["class", "method", "method"]
    assert all(i.path == "pkg/mod.py" for i in envelope.items)
    assert all(i.start_line is not None and i.end_line is not None for i in envelope.items)


def test_a_childless_undocumented_symbol_says_so(wired: _WiredCards) -> None:
    """A definitive "nothing inside" answer spares the agent a follow-up call."""
    envelope = _validated(_symbol(wired.mcp_router, "pkg.mod.Bare"))
    assert _members_line(envelope.text) == "No members."
    assert [i.qualified_name for i in envelope.items] == ["pkg.mod.Bare"]


# ── the module card ────────────────────────────────────────────────────────


def test_module_card_renders_its_doc_line_and_top_level_members(wired: _WiredCards) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.mod"))
    assert envelope.text.splitlines()[-1].startswith("Members (3): ")
    assert "A wide module." not in envelope.text
    assert "The module every card test reads." in envelope.text.splitlines()
    assert _members_line(envelope.text) == "Members (3): Alpha, Bare, beta"
    assert [i.qualified_name for i in envelope.items] == [
        "pkg.mod",
        "pkg.mod.Alpha",
        "pkg.mod.Bare",
        "pkg.mod.beta",
    ]


# ── the capped module ──────────────────────────────────────────────────────


def test_capped_module_card_ends_in_and_n_more_plus_an_outline_pointer(
    wired: _WiredCards,
) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.wide"))
    cap = AppConfig.load().symbol_card.child_cap
    listed = [f"f{i:02d}" for i in range(cap)]
    assert _members_line(envelope.text) == (
        f"Members ({_WIDE_MEMBERS}): " + ", ".join(listed) + f", and {_WIDE_MEMBERS - cap} more"
    )
    assert '→ get_symbol(target="pkg.wide", depth="tree")' in envelope.text
    assert envelope.meta.truncated is True


def test_capped_module_card_items_are_exactly_the_names_it_listed(wired: _WiredCards) -> None:
    envelope = _validated(_symbol(wired.mcp_router, "pkg.wide"))
    cap = AppConfig.load().symbol_card.child_cap
    assert [i.qualified_name for i in envelope.items] == ["pkg.wide"] + [
        f"pkg.wide.f{i:02d}" for i in range(cap)
    ]


def test_the_card_cap_is_a_yaml_knob(wired: _WiredCards, tmp_path: Path) -> None:
    """``symbol_card.child_cap`` is YAML-tunable — never an MCP parameter."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("symbol_card:\n  child_cap: 3\n")
    config = AppConfig.load(explicit_path=overlay)
    router, _s = build_routers(config, db_path=wired.db_path, surface="mcp")
    text = _symbol(router, "pkg.wide").text
    assert _members_line(text) == f"Members ({_WIDE_MEMBERS}): f00, f01, f02, and 22 more"


# ── both surfaces, and the untouched tree depth ────────────────────────────


def _without_pointer_lines(text: str) -> str:
    """The card minus its resolved follow-up calls — the surface-shared part."""
    return "\n".join(line for line in strip_pointers(text).splitlines() if not line.startswith("→"))


@pytest.mark.parametrize("target", ["pkg.mod.Alpha", "pkg.mod", "pkg.wide"])
def test_cli_and_mcp_render_the_same_card(wired: _WiredCards, target: str) -> None:
    mcp = _symbol(wired.mcp_router, target)
    cli = _symbol(wired.cli_router, target)
    assert _without_pointer_lines(cli.text) == _without_pointer_lines(mcp.text)
    assert cli.items == mcp.items


def test_the_outline_pointer_renders_in_each_surfaces_own_syntax(wired: _WiredCards) -> None:
    assert "→ pydocs-mcp symbol pkg.wide --depth tree" in _symbol(wired.cli_router, "pkg.wide").text
    assert (
        '→ get_symbol(target="pkg.wide", depth="tree")'
        in _symbol(wired.mcp_router, "pkg.wide").text
    )


def test_the_cli_verb_prints_the_card(wired: _WiredCards, capsys: pytest.CaptureFixture) -> None:
    from pydocs_mcp.__main__ import main

    argv = shlex.split("pydocs-mcp symbol pkg.mod.Alpha --depth summary")
    with patch("sys.argv", argv + ["--db", str(wired.db_path)]):
        assert main() == 0
    out = capsys.readouterr().out
    assert "Members (2): run, stat" in out
    assert '"nodes"' not in out


def test_the_two_cheap_depths_are_complementary(wired: _WiredCards) -> None:
    """The card names its children; the outline places them in the tree. Neither
    is the PageIndex JSON both depths used to send (``tests/test_outline_wire``
    pins the outline itself)."""
    card = _validated(_symbol(wired.mcp_router, "pkg.mod.Alpha"))
    outline = _validated(_symbol(wired.mcp_router, "pkg.mod.Alpha", depth="tree"))
    assert _members_line(card.text) == "Members (2): run, stat"
    assert [line for line in outline.text.splitlines() if line.startswith("  method")] == [
        "  method pkg.mod.Alpha.run · 9-11",
        "  method pkg.mod.Alpha.stat · 14-15",
    ]
    assert '"nodes"' not in card.text and '"nodes"' not in outline.text
    # Same node set both ways: the card must not hide what the outline shows.
    assert [i.qualified_name for i in outline.items] == [i.qualified_name for i in card.items]
