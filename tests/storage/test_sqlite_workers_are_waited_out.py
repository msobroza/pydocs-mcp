"""Every worker on a shared SQLite connection goes through ``sqlite_to_thread``.

The PR #399 CI segfault came from ONE repository method, but every method that
hands a shared connection to ``asyncio.to_thread`` had the same exposure: a
cancelled caller releases the connection while the thread still steps it. The
behaviour is pinned in ``test_sqlite_worker_cancellation.py``; this guard keeps
the NEXT method from reintroducing the shape, by reading the source itself.

Two places hold a shared connection: the body of ``async with _maybe_acquire(...)``
(a per-call connection, or a unit of work's held one), and the plumbing that
opens, commits, rolls back and closes it. A worker that opens its OWN connection
inside the thread (the cross-link store, the factories' index probes) races
nothing and keeps ``asyncio.to_thread``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import pydocs_mcp

_PACKAGE = Path(pydocs_mcp.__file__).parent

# The plumbing that opens, commits, rolls back and closes a shared connection.
_CONNECTION_PLUMBING = (
    "retrieval/pipeline/connection.py",
    "storage/sqlite/transaction.py",
    "storage/sqlite/uow.py",
)


def _is_asyncio_to_thread(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    target = node.func
    return (
        target.attr == "to_thread"
        and isinstance(target.value, ast.Name)
        and target.value.id == "asyncio"
    )


def _is_maybe_acquire_block(node: ast.AST) -> bool:
    if not isinstance(node, ast.AsyncWith):
        return False
    calls = [item.context_expr for item in node.items if isinstance(item.context_expr, ast.Call)]
    return any(getattr(call.func, "id", None) == "_maybe_acquire" for call in calls)


def _to_thread_lines(node: ast.AST) -> list[int]:
    return sorted(call.lineno for call in ast.walk(node) if _is_asyncio_to_thread(call))


def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_maybe_acquire_block_hands_its_connection_to_asyncio_to_thread() -> None:
    offenders = [
        f"{path.relative_to(_PACKAGE)}:{line}"
        for path in sorted(_PACKAGE.rglob("*.py"))
        for block in ast.walk(_parsed(path))
        if _is_maybe_acquire_block(block)
        for line in _to_thread_lines(block)
    ]

    assert offenders == [], "use sqlite_to_thread on a shared connection: " + ", ".join(offenders)


@pytest.mark.parametrize("module", _CONNECTION_PLUMBING)
def test_the_connection_plumbing_never_uses_asyncio_to_thread(module: str) -> None:
    lines = _to_thread_lines(_parsed(_PACKAGE / module))

    assert lines == [], f"{module} lines {lines}: use sqlite_to_thread"


def test_the_guard_sees_the_blocks_it_polices() -> None:
    """A guard that matched nothing would pass vacuously; the repositories have many."""
    blocks = [
        block
        for path in sorted((_PACKAGE / "storage").rglob("*.py"))
        for block in ast.walk(_parsed(path))
        if _is_maybe_acquire_block(block)
    ]

    assert len(blocks) >= 50
