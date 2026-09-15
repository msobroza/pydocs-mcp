"""Batch 2 of the pointer table at the tool-call boundary, on a REAL index.

The kinds this file pins are the ones whose follow-ups fan out over MANY
symbols (reference rows, impact rows) and the ones that render ONE symbol at a
chosen depth (the symbol card, the outline, a context skeleton block). Every
claim is driven through ``build_routers`` exactly as ``server.py`` and the CLI
build it:

- a listing whose row count reaches the batch threshold ends with ONE batch
  context call over its top rows, capped at the batch maximum and saying how
  many rows it left unnamed; a shorter listing gets a symbol card per row;
- a symbol card, an outline and a context skeleton block carry the rows the
  table defines for them;
- no response points at what it just rendered (CONTEXT.md "self-pointing");
- every call these responses advertise executes;
- clearing the rows in YAML takes the bundles away, so the table is the only
  switch.
"""

from __future__ import annotations

import ast
import asyncio
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from pydocs_mcp.application import mcp_inputs
from pydocs_mcp.application.mcp_inputs import ContextInput, ReferencesInput, SymbolInput
from pydocs_mcp.application.tool_response import (
    ContextEnvelope,
    ReferencesEnvelope,
    SymbolEnvelope,
    ToolResponse,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import _to_call_tool_result, build_routers
from tests._index_fixture import index_project_to_db

_PYPROJECT = """\
[project]
name = "batchproj"
version = "0.0.0"
dependencies = []
"""

_MOD_PY = '''\
"""The module every batch-pointer test reads."""


def target_fn() -> int:
    """The symbol the reference page is about."""
    return 1


def quiet_fn() -> int:
    """The symbol with a single caller."""
    return 2


class Base:
    """The base of the inheritance page."""


class Derived(Base):
    """The one subclass."""
'''

# Nine callers plus the module's own IMPORTS edge — ten rows, two past the
# shipped batch maximum of eight.
_CALLER_COUNT = 9
_CALLERS_PY = '"""Callers of the target."""\n\nfrom pkg.mod import target_fn\n\n\n' + "\n\n".join(
    f'def c{i}() -> int:\n    """Caller {i}."""\n    return target_fn()'
    for i in range(_CALLER_COUNT)
)

# One caller plus the module edge — two rows, one short of the threshold.
_TWO_PY = (
    '"""The one caller of the quiet symbol."""\n\nfrom pkg.mod import quiet_fn\n\n\n'
    'def d0() -> int:\n    """The only caller."""\n    return quiet_fn()\n'
)

# A table whose rows are all empty: the off-switch for the bundles.
_CLEARED_TABLE = """\
output:
  pointers:
    table:
      reference_row: { together: [] }
      impact_row: { together: [] }
      symbol_card: { together: [], then: [] }
      outline: { together: [] }
      context_skeleton_block: { together: [] }
"""

_TARGET = "pkg.mod.target_fn"
_QUIET = "pkg.mod.quiet_fn"

# The rows the ten-row listing names, in render order (resolved first, then by
# from_node_id), and the two it leaves over: the eighth caller, past the batch
# maximum, and the module-level import row, which a context call cannot address.
_TOP_ROWS = tuple(f"pkg.callers.c{i}" for i in range(8))
_UNNAMED_ROWS = 2


def _write_project(root: Path) -> Path:
    project = root / "batchproj"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD_PY)
    (pkg / "callers.py").write_text(_CALLERS_PY)
    (pkg / "two.py").write_text(_TWO_PY)
    return project


@dataclass(frozen=True, slots=True)
class _WiredBatch:
    """One real bundle plus a router per surface, per table."""

    db_path: Path
    mcp: ToolRouter
    cli: ToolRouter
    cleared_mcp: ToolRouter


@pytest.fixture
def wired(tmp_path: Path) -> _WiredBatch:
    db_path = index_project_to_db(_write_project(tmp_path), tmp_path / "batch.db")
    overlay = tmp_path / "cleared.yaml"
    overlay.write_text(_CLEARED_TABLE)
    shipped = AppConfig.load()
    cleared = AppConfig.load(explicit_path=overlay)
    return _WiredBatch(
        db_path=db_path,
        mcp=build_routers(shipped, db_path=db_path, surface="mcp")[0],
        cli=build_routers(shipped, db_path=db_path, surface="cli")[0],
        cleared_mcp=build_routers(cleared, db_path=db_path, surface="mcp")[0],
    )


def _refs(router: ToolRouter, target: str, direction: str = "callers") -> str:
    payload = ReferencesInput(target=target, direction=direction)
    return asyncio.run(router.get_references(payload)).text


def _symbol(router: ToolRouter, target: str, depth: str = "summary") -> ToolResponse:
    return asyncio.run(router.get_symbol(SymbolInput(target=target, depth=depth)))


def _context(router: ToolRouter, target: str) -> str:
    return asyncio.run(router.get_context(ContextInput(targets=[target]))).text


def _group_line(text: str, label: str) -> str:
    """The one line of ``text`` starting with ``label`` — one bundle group."""
    matches = [line for line in text.splitlines() if line.startswith(label)]
    assert matches, f"no {label!r} line in:\n{text}"
    return matches[0]


def _group_lines(text: str, label: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(label)]


def _mcp_batch_call(targets: tuple[str, ...]) -> str:
    return "get_context(targets=[" + ", ".join(f'"{t}"' for t in targets) + "])"


# ── reference rows: one batch call above the threshold ─────────────────────


def test_a_full_reference_page_ends_in_one_batch_context_call(wired: _WiredBatch) -> None:
    """Ten rows would be ten card calls; one context call shares one budget."""
    text = _refs(wired.mcp, _TARGET)
    assert _group_line(text, "Together:") == (
        f"Together: → {_mcp_batch_call(_TOP_ROWS)} ({_UNNAMED_ROWS} more rows not named)"
    )


def test_the_batch_context_call_renders_in_cli_form(wired: _WiredBatch) -> None:
    text = _refs(wired.cli, _TARGET)
    assert _group_line(text, "Together:") == (
        "Together: → pydocs-mcp context "
        + " ".join(_TOP_ROWS)
        + f" ({_UNNAMED_ROWS} more rows not named)"
    )


def test_a_reference_page_never_points_back_at_the_symbol_it_is_about(
    wired: _WiredBatch,
) -> None:
    """The page already answers every edge of the target; a follow-up aimed
    back at it would re-issue the call that produced the page."""
    text = _refs(wired.mcp, _TARGET)
    assert _TARGET not in _group_line(text, "Together:")


def test_a_short_reference_page_offers_a_symbol_card_per_row(wired: _WiredBatch) -> None:
    """Below the threshold there is no fan-out to collapse, so each row keeps
    the cheapest deepening call there is."""
    text = _refs(wired.mcp, _QUIET)
    assert _group_line(text, "Together:") == 'Together: → get_symbol(target="pkg.two.d0")'


def test_a_short_reference_page_renders_its_cards_in_cli_form(wired: _WiredBatch) -> None:
    text = _refs(wired.cli, _QUIET)
    assert _group_line(text, "Together:") == "Together: → pydocs-mcp symbol pkg.two.d0"


def test_a_module_row_contributes_no_follow_up_target(wired: _WiredBatch) -> None:
    """``pkg.two`` imports the target, so the page lists it as a row — but
    get_context packs a SYMBOL's closure and rejects a module outright, and one
    such target would fail the batch call for every other row."""
    text = _refs(wired.mcp, _QUIET)
    assert "- `pkg.two` → " in text
    assert 'pkg.two"' not in _group_line(text, "Together:")


def test_an_inheritance_page_offers_the_symbols_its_senses_introduce(
    wired: _WiredBatch,
) -> None:
    """The two-section page draws from the same reference row — one subclass is
    below the threshold, so it gets its card."""
    text = _refs(wired.mcp, "pkg.mod.Base", direction="inherits")
    assert "## Subclasses of `pkg.mod.Base`" in text
    assert _group_line(text, "Together:") == 'Together: → get_symbol(target="pkg.mod.Derived")'


def test_an_empty_reference_page_offers_nothing(wired: _WiredBatch) -> None:
    """No rows, no counterparts — a bundle would name calls about nothing."""
    text = _refs(wired.mcp, _TARGET, direction="callees")
    assert "No callees found." in text
    assert "Together:" not in text


# ── impact rows ────────────────────────────────────────────────────────────


def test_an_impact_page_ends_in_the_same_batch_context_call(wired: _WiredBatch) -> None:
    text = _refs(wired.mcp, _TARGET, direction="impact")
    assert _group_line(text, "Together:") == (
        f"Together: → {_mcp_batch_call(_TOP_ROWS)} ({_UNNAMED_ROWS} more rows not named)"
    )


def test_an_impact_page_renders_the_batch_call_in_cli_form(wired: _WiredBatch) -> None:
    text = _refs(wired.cli, _TARGET, direction="impact")
    assert _group_line(text, "Together:") == (
        "Together: → pydocs-mcp context "
        + " ".join(_TOP_ROWS)
        + f" ({_UNNAMED_ROWS} more rows not named)"
    )


# ── the symbol card ────────────────────────────────────────────────────────


def test_the_card_offers_outline_callers_and_context_together_then_the_source(
    wired: _WiredBatch,
) -> None:
    text = _symbol(wired.mcp, _TARGET).text
    assert _group_line(text, "Together:") == (
        f'Together: → get_symbol(target="{_TARGET}", depth="tree") '
        f'→ get_references(target="{_TARGET}", direction="callers") '
        f'→ get_context(targets=["{_TARGET}"])'
    )
    assert _group_line(text, "Then:") == f'Then: → get_symbol(target="{_TARGET}", depth="source")'


def test_the_card_renders_its_bundle_in_cli_form(wired: _WiredBatch) -> None:
    text = _symbol(wired.cli, _TARGET).text
    assert _group_line(text, "Together:") == (
        f"Together: → pydocs-mcp symbol {_TARGET} --depth tree "
        f"→ pydocs-mcp refs {_TARGET} --direction callers "
        f"→ pydocs-mcp context {_TARGET}"
    )
    assert _group_line(text, "Then:") == f"Then: → pydocs-mcp symbol {_TARGET} --depth source"


def test_a_capped_card_never_advertises_its_outline_twice(
    wired: _WiredBatch, tmp_path: Path
) -> None:
    """The cap already hands over the outline call inline; repeating it in the
    bundle is the self-pointing the table forbids."""
    overlay = tmp_path / "cap.yaml"
    overlay.write_text("symbol_card:\n  child_cap: 2\n")
    router = build_routers(
        AppConfig.load(explicit_path=overlay), db_path=wired.db_path, surface="mcp"
    )[0]
    text = _symbol(router, "pkg.callers").text
    outline_call = '→ get_symbol(target="pkg.callers", depth="tree")'
    assert text.count(outline_call) == 1
    assert outline_call not in _group_line(text, "Together:")


# ── the outline ────────────────────────────────────────────────────────────


def test_the_outline_offers_the_roots_source_and_its_callers(wired: _WiredBatch) -> None:
    text = _symbol(wired.mcp, "pkg.callers", depth="tree").text
    assert _group_line(text, "Together:") == (
        'Together: → get_symbol(target="pkg.callers", depth="source") '
        '→ get_references(target="pkg.callers", direction="callers")'
    )
    assert "Then:" not in text


def test_the_outline_renders_its_bundle_in_cli_form(wired: _WiredBatch) -> None:
    text = _symbol(wired.cli, "pkg.callers", depth="tree").text
    assert _group_line(text, "Together:") == (
        "Together: → pydocs-mcp symbol pkg.callers --depth source "
        "→ pydocs-mcp refs pkg.callers --direction callers"
    )


def test_a_cut_outline_keeps_its_recovery_pointers_and_its_bundle(
    wired: _WiredBatch, tmp_path: Path
) -> None:
    """The cut's recovery pointers name what was elided; the bundle names what
    comes after the structure — both belong in a cut outline."""
    overlay = tmp_path / "budget.yaml"
    overlay.write_text("symbol_outline:\n  token_budget: 60\n")
    router = build_routers(
        AppConfig.load(explicit_path=overlay), db_path=wired.db_path, surface="mcp"
    )[0]
    text = _symbol(router, "pkg.callers", depth="tree").text
    assert "nodes elided" in text
    assert _group_line(text, "Together:").startswith(
        'Together: → get_symbol(target="pkg.callers", depth="source")'
    )


# ── the source depth ───────────────────────────────────────────────────────


def test_the_source_depth_carries_no_bundle(wired: _WiredBatch) -> None:
    """The deepest indexed view has nothing to deepen into — only the ``read``
    continuation the line cap adds, and this body is not capped."""
    text = _symbol(wired.mcp, _TARGET, depth="source").text
    assert "Together:" not in text
    assert "Then:" not in text


# ── a context skeleton block ───────────────────────────────────────────────


def test_a_signature_only_skeleton_block_offers_its_source(wired: _WiredBatch) -> None:
    text = _context(wired.mcp, "pkg.callers.c0")
    assert _group_line(text, "Together:") == (
        'Together: → get_symbol(target="pkg.callers.c0", depth="source")'
    )


def test_a_skeleton_block_renders_its_source_call_in_cli_form(wired: _WiredBatch) -> None:
    text = _context(wired.cli, "pkg.callers.c0")
    assert _group_line(text, "Together:") == (
        "Together: → pydocs-mcp symbol pkg.callers.c0 --depth source"
    )


def test_a_skeleton_block_that_rendered_its_body_offers_nothing(wired: _WiredBatch) -> None:
    """The block already holds the source, so the source call is self-pointing."""
    text = _context(wired.mcp, "pkg.callers.c0")
    assert f'→ get_symbol(target="{_TARGET}", depth="source")' not in text


# ── both surfaces advertise the same calls ─────────────────────────────────


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda r: _refs(r, _TARGET), id="reference_batch"),
        pytest.param(lambda r: _refs(r, _QUIET), id="reference_cards"),
        pytest.param(lambda r: _refs(r, _TARGET, direction="impact"), id="impact"),
        pytest.param(lambda r: _symbol(r, _TARGET).text, id="card"),
        pytest.param(lambda r: _symbol(r, "pkg.callers", depth="tree").text, id="outline"),
        pytest.param(lambda r: _context(r, "pkg.callers.c0"), id="context_skeleton"),
    ],
)
def test_the_cli_and_mcp_surfaces_advertise_the_same_calls(wired: _WiredBatch, render) -> None:
    mcp_text, cli_text = render(wired.mcp), render(wired.cli)
    for label in ("Together:", "Then:"):
        mcp_lines, cli_lines = _group_lines(mcp_text, label), _group_lines(cli_text, label)
        assert len(mcp_lines) == len(cli_lines)
        for mcp_line, cli_line in zip(mcp_lines, cli_lines, strict=True):
            assert mcp_line.count(" → ") == cli_line.count(" → ")


# ── every advertised call executes ─────────────────────────────────────────

# A call stops at the paren that closes it, so the count a batch line ends with
# ("(2 more rows not named)") is never read as part of the call.
_MCP_CALL_RE = re.compile(r"→ (\w+\([^()\n]*\))")

_TOOL_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "get_symbol": (mcp_inputs.SymbolInput, SymbolEnvelope),
    "get_references": (mcp_inputs.ReferencesInput, ReferencesEnvelope),
    "get_context": (mcp_inputs.ContextInput, ContextEnvelope),
}


def _run_mcp_call(router: ToolRouter, call_src: str) -> None:
    """Validate input → await the router method → validate the output envelope."""
    call = ast.parse(call_src, mode="eval").body
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name), call_src
    kwargs: dict[str, Any] = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
    input_model, envelope = _TOOL_MODELS[call.func.id]
    response = asyncio.run(getattr(router, call.func.id)(input_model(**kwargs)))
    _to_call_tool_result(response, envelope)


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda r: _refs(r, _TARGET), id="reference_batch"),
        pytest.param(lambda r: _refs(r, _QUIET), id="reference_cards"),
        pytest.param(lambda r: _refs(r, _TARGET, direction="impact"), id="impact"),
        pytest.param(lambda r: _symbol(r, _TARGET).text, id="card"),
        pytest.param(lambda r: _symbol(r, "pkg.callers", depth="tree").text, id="outline"),
        pytest.param(lambda r: _context(r, "pkg.callers.c0"), id="context_skeleton"),
    ],
)
def test_every_call_these_responses_advertise_executes(wired: _WiredBatch, render) -> None:
    """A pointer is a ready-made call; one that raises would cost the agent the
    turn it was meant to save."""
    calls = _MCP_CALL_RE.findall(render(wired.mcp))
    assert calls
    for call_src in calls:
        _run_mcp_call(wired.mcp, call_src)


# ── the table is the only switch ───────────────────────────────────────────


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda r: _refs(r, _TARGET), id="reference_batch"),
        pytest.param(lambda r: _refs(r, _QUIET), id="reference_cards"),
        pytest.param(lambda r: _refs(r, _TARGET, direction="impact"), id="impact"),
        pytest.param(lambda r: _symbol(r, _TARGET).text, id="card"),
        pytest.param(lambda r: _symbol(r, "pkg.callers", depth="tree").text, id="outline"),
        pytest.param(lambda r: _context(r, "pkg.callers.c0"), id="context_skeleton"),
    ],
)
def test_clearing_a_row_in_yaml_takes_its_bundle_away(wired: _WiredBatch, render) -> None:
    text = render(wired.cleared_mcp)
    assert "Together:" not in text
    assert "Then:" not in text


# ── the CLI verbs print what the MCP tools return ──────────────────────────


def test_the_cli_refs_verb_prints_the_batch_call(
    wired: _WiredBatch, capsys: pytest.CaptureFixture
) -> None:
    from pydocs_mcp.__main__ import main

    argv = shlex.split(f"pydocs-mcp refs {_TARGET} --direction callers")
    with patch("sys.argv", argv + ["--db", str(wired.db_path)]):
        assert main() == 0
    assert "→ pydocs-mcp context pkg.callers.c0 " in capsys.readouterr().out
