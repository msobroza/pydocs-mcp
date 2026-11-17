"""The ``Members (N): …`` line on class and module search hits (issue #393).

``search.output.members_line`` ships ``false``: with it off, ``search_codebase``
must answer exactly what the code before the knob answered — ``text``,
``items`` and the whole ``meta`` block, on the single-bundle path and on the
union of two bundles. ``tests/fixtures/goldens/search_members_line_off_answers.json``
records those answers; this module's golden writer produced it against the
parent commit (main at 9b9de5a3), before the knob existed.

Regenerate (only when an answer is meant to change, and from the code whose
answers are the reference)::

    PYDOCS_WRITE_MEMBERS_LINE_GOLDEN=1 uv run --no-sync pytest \\
        tests/integration/test_search_members_line.py -k write_golden
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db
from tests._score_tolerance import scores_within_float_noise

_GOLDEN_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "goldens"
    / "search_members_line_off_answers.json"
)
_GOLDEN_ENV = "PYDOCS_WRITE_MEMBERS_LINE_GOLDEN"

# A class with three methods (the cap overlay names two of them), its module,
# a module-level function, and a markdown guide whose root carries intro text:
# every hit shape the Members line must either render or leave alone.
_CART_PY = '''\
"""Shopping cart model."""


class Cart:
    """Holds the line items of one order."""

    def add(self, sku: str) -> None:
        """Add one item to the cart."""

    def remove(self, sku: str) -> None:
        """Remove one item from the cart."""

    def total(self) -> int:
        """Sum the cart's line items."""
        return 0


def checkout(cart: Cart) -> int:
    """Charge the cart total at checkout."""
    return cart.total()
'''

_GUIDE_MD = """\
How to check out a cart.

# Checkout

Call checkout with a cart to charge its total.

# Refunds

Remove the item from the cart before checkout.
"""

# The second bundle of the union: one class, one method, its module.
_BOOK_PY = '''\
"""Bookkeeping entries for cart totals."""


class Book:
    """An append-only list of cart entries."""

    def post(self, amount: int) -> None:
        """Post one cart total to the book."""
'''


def _write_shop(root: Path) -> Path:
    (root / "shop").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "cart.py").write_text(_CART_PY, encoding="utf-8")
    (root / "docs" / "guide.md").write_text(_GUIDE_MD, encoding="utf-8")
    return root


def _write_ledger(root: Path) -> Path:
    (root / "ledger").mkdir(parents=True)
    (root / "ledger" / "__init__.py").write_text("", encoding="utf-8")
    (root / "ledger" / "book.py").write_text(_BOOK_PY, encoding="utf-8")
    return root


def _single_calls() -> dict[str, SearchInput]:
    # ``kind="any"`` (the default) also ranks member rows, whose spans resolve
    # through the same module trees the Members line reads.
    return {
        "single_any": SearchInput(query="cart"),
        "single_docs": SearchInput(query="cart checkout total", kind="docs"),
    }


def _union_calls() -> dict[str, SearchInput]:
    return {"union_any": SearchInput(query="cart book")}


def _search_answers(router: object, calls: dict[str, SearchInput]) -> dict[str, object]:
    answers = {}
    for name, payload in calls.items():
        response = asyncio.run(router.search_codebase(payload))  # type: ignore[attr-defined]
        answers[name] = {"text": response.text, "items": response.items, "meta": response.meta}
    return answers


def _all_answers(tmp_path: Path, config: AppConfig) -> dict[str, object]:
    """``text``, ``items`` and ``meta`` of every call, JSON-normalized, roots masked."""
    shop = _write_shop(tmp_path / "shop")
    ledger = _write_ledger(tmp_path / "ledger")
    shop_db = index_project_to_db(shop, tmp_path / "shop.db")
    ledger_db = index_project_to_db(ledger, tmp_path / "ledger.db")
    single, _ = build_routers(config, db_path=shop_db, surface="mcp")
    union, _ = build_routers(config, db_paths=[shop_db, ledger_db], surface="mcp")
    answers = {**_search_answers(single, _single_calls()), **_search_answers(union, _union_calls())}
    rendered = json.dumps(answers, sort_keys=True, default=str).replace(str(tmp_path), "<tmp>")
    return json.loads(rendered)


def _golden() -> dict[str, dict[str, object]]:
    return json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))


def test_search_answers_with_the_knob_off_are_byte_identical_to_the_parent_commit(
    tmp_path: Path,
) -> None:
    answers = _all_answers(tmp_path, AppConfig.load())
    golden = _golden()
    assert sorted(answers) == sorted(golden)
    for call, expected in golden.items():
        want = {**expected, "items": scores_within_float_noise(expected["items"])}
        assert answers[call] == want, call


# Each class or module hit's Members line, written out by hand from the fixture
# sources above (never rendered), keyed by the hit's qualified name. The guide's
# root is a module too; its headings' names carry no dotted prefix of it, so
# they are named in full — exactly as its card names them.
_MEMBERS_LINES = {
    "shop.cart.Cart": "Members (3): add, remove, total",
    "shop.cart": "Members (2): Cart, checkout",
    "docs.guide.md": "Members (2): docs.guide.md#checkout, docs.guide.md#refunds",
    "ledger.book.Book": "Members (1): post",
    "ledger.book": "Members (1): Book",
}
_MEMBERS_LINE_ON = "search:\n  output:\n    members_line: true\n"


def _header_qualified_name(line: str) -> str:
    """``## pkg.mod.X — path:1-9`` → ``pkg.mod.X``; ``""`` for any other line."""
    return line[3:].split(" — ", 1)[0] if line.startswith("## ") else ""


def _with_members_lines(text: str, lines: dict[str, str]) -> str:
    """``text`` with ``lines[qname]`` under the header of each hit it names."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        out.append(line)
        qualified_name = _header_qualified_name(line)
        if qualified_name in lines:
            out.append(lines[qualified_name] + "\n")
    return "".join(out)


def _overlay(tmp_path: Path, yaml_text: str) -> Path:
    path = tmp_path / "members-line.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return path


def test_the_overlay_adds_the_members_lines_and_moves_nothing_else(tmp_path: Path) -> None:
    """The experiment arm's overlay: every class or module hit, on both paths,
    gains its line under its header; ``items`` and ``meta`` stay the parent's."""
    config = AppConfig.load(explicit_path=_overlay(tmp_path, _MEMBERS_LINE_ON))
    answers = _all_answers(tmp_path, config)
    for call, expected in _golden().items():
        want = {
            "text": _with_members_lines(str(expected["text"]), _MEMBERS_LINES),
            "items": scores_within_float_noise(expected["items"]),
            "meta": expected["meta"],
        }
        assert answers[call] == want, call


def test_the_overlay_names_at_most_symbol_card_child_cap_members(tmp_path: Path) -> None:
    overlay = _MEMBERS_LINE_ON + "symbol_card:\n  child_cap: 2\n"
    config = AppConfig.load(explicit_path=_overlay(tmp_path, overlay))
    answers = _all_answers(tmp_path, config)
    capped = {**_MEMBERS_LINES, "shop.cart.Cart": "Members (3): add, remove, and 1 more"}
    expected = _golden()["single_docs"]["text"]
    assert answers["single_docs"]["text"] == _with_members_lines(str(expected), capped)


def _without_calls(text: str) -> list[str]:
    """Every line but the follow-up calls, whose form is each surface's own."""
    return [line for line in text.splitlines() if "→" not in line]


def test_the_cli_prints_the_members_lines_the_mcp_tool_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pydocs_mcp.__main__ import main

    shop_db = index_project_to_db(_write_shop(tmp_path / "shop"), tmp_path / "shop.db")
    overlay = _overlay(tmp_path, _MEMBERS_LINE_ON)
    router, _ = build_routers(
        AppConfig.load(explicit_path=overlay), db_paths=[shop_db], surface="mcp"
    )
    mcp = asyncio.run(router.search_codebase(SearchInput(query="cart", kind="docs"))).text
    capsys.readouterr()
    argv = ["--config", str(overlay), "search", "cart", "--kind", "docs", "--db", str(shop_db)]
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *argv])
    assert main() == 0
    cli = capsys.readouterr().out.removesuffix("\n")
    assert "\nMembers (3): add, remove, total\n" in cli
    assert len(cli.splitlines()) == len(mcp.splitlines())
    assert _without_calls(cli) == _without_calls(mcp)
    assert "→ pydocs-mcp " in cli and "→ pydocs-mcp " not in mcp


@pytest.mark.skipif(not os.environ.get(_GOLDEN_ENV), reason=f"set {_GOLDEN_ENV}=1 to rewrite")
def test_write_golden(tmp_path: Path) -> None:
    answers = _all_answers(tmp_path, AppConfig.load())
    _GOLDEN_PATH.write_text(json.dumps(answers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
