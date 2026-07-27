"""Missing-extra guard shared by harness entry points (product side).

Extracted from the ask-your-docs launcher: any harness whose runtime ships
behind an optional extra probes its module stack at startup and exits with
the exact install hint instead of a deep ImportError. The eval-side guard
(`benchmarks/.../ask_binding.py`) is deliberately NOT this function — it
probes a different module set including ``pydocs_mcp`` itself, so it can
never import from here (spec §4.2).
"""

from __future__ import annotations

from importlib.util import find_spec


def require_extra_modules(modules: tuple[str, ...], *, extra: str, command: str) -> None:
    """Exit with the install hint when any module of the optional stack is absent.

    Example::

        require_extra_modules(
            ("streamlit", "langgraph"),
            extra="harness-ask-your-docs",
            command="harness-ask-your-docs",
        )
    """
    missing = [m for m in modules if find_spec(m) is None]
    if missing:
        raise SystemExit(
            f"{command} needs the optional agent stack (missing: {', '.join(missing)}). "
            f"Install it with:  pip install 'pydocs-mcp[{extra}]'"
        )
