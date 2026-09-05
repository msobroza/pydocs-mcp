"""R2 pin: TOOL_DOCS / SERVER_INSTRUCTIONS imports stay function-local.

The benchmarks-side description-overlay mechanism
(``benchmarks/src/pydocs_eval/optimize/_overlay_server.py``) re-binds
``pydocs_mcp.application.tool_docs`` MODULE attributes before the server /
CLI reads them. That only works because every consumer imports the names
inside the function that uses them (import executes at call time, after the
overlay's re-bind) — a module-level ``from ... import TOOL_DOCS`` would
freeze the pre-overlay binding at import time and silently defeat the
mechanism. This test pins the property structurally via AST so a refactor
that hoists the import fails loudly.
"""

from __future__ import annotations

import ast
import inspect

import pytest

_TOOL_DOCS_MODULE = "pydocs_mcp.application.tool_docs"

# module -> function names allowed to import from tool_docs. These are the
# three read sites the overlay mechanism depends on staying call-time.
_ALLOWED_FUNCTIONS = {
    "pydocs_mcp.server": {"run", "_register_tools"},
    "pydocs_mcp.__main__": {"_build_parser"},
}


def _tool_docs_imports(module_name: str) -> list[tuple[str | None, ast.ImportFrom]]:
    """Every ``from pydocs_mcp.application.tool_docs import ...`` in the
    module source, paired with the name of its innermost enclosing function
    (``None`` = module level)."""
    import importlib

    module = importlib.import_module(module_name)
    tree = ast.parse(inspect.getsource(module))

    functions = [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    found: list[tuple[str | None, ast.ImportFrom]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module == _TOOL_DOCS_MODULE):
            continue
        enclosing = None
        for fn in functions:
            # innermost wins: keep the tightest containing range.
            if fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno) and (
                enclosing is None or fn.lineno >= enclosing.lineno
            ):
                enclosing = fn
        found.append((enclosing.name if enclosing else None, node))
    return found


@pytest.mark.parametrize("module_name", sorted(_ALLOWED_FUNCTIONS))
def test_tool_docs_imports_are_function_local(module_name: str) -> None:
    imports = _tool_docs_imports(module_name)
    assert imports, f"{module_name} no longer imports tool_docs — update _ALLOWED_FUNCTIONS"
    for enclosing, node in imports:
        assert enclosing is not None, (
            f"{module_name}:{node.lineno} imports tool_docs at MODULE level — "
            "this freezes the pre-overlay binding and breaks the description "
            "overlay (R2); move it inside the consuming function"
        )
        assert enclosing in _ALLOWED_FUNCTIONS[module_name], (
            f"{module_name}:{node.lineno} imports tool_docs inside {enclosing!r} — "
            "new read sites must stay function-local AND be added here"
        )


def test_server_reads_tool_docs_in_both_seam_functions() -> None:
    """``run`` (SERVER_INSTRUCTIONS) and ``_register_tools`` (TOOL_DOCS) each
    import at call time — both seams the overlay re-binds through."""
    enclosings = {fn for fn, _ in _tool_docs_imports("pydocs_mcp.server")}
    assert enclosings == {"run", "_register_tools"}


def test_cli_reads_tool_docs_in_build_parser() -> None:
    enclosings = {fn for fn, _ in _tool_docs_imports("pydocs_mcp.__main__")}
    assert enclosings == {"_build_parser"}
