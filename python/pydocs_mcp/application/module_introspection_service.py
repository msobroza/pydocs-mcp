"""ModuleIntrospectionService — live importlib + inspect (spec §5.1).

Factored out of ``server.py::inspect_module`` so the MCP handler is a thin
adapter over a Protocol-only service. The live-import + ``inspect`` logic is
CPU-bound and synchronous — we offload it to a worker thread via
``asyncio.to_thread`` so the FastMCP event loop is never blocked while an
``__init__.py`` side-effect-imports half a framework.

Byte-parity with the pre-PR handler output is a hard requirement (AC #8) —
the ``_inspect_target`` body below is a verbatim move of the post-import
branch in ``server.py``.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import re
from dataclasses import dataclass

from pydocs_mcp.constants import LIVE_DOC_MAX, LIVE_SIGNATURE_MAX
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.storage.protocols import PackageStore

_SUBMODULE_RE = re.compile(r"^([A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*)?$")

# Match the pre-PR server.py handler: cap inspection at 50 public members so
# inspecting giant modules (e.g. ``numpy``) can't bloat the MCP response.
_MAX_MEMBERS: int = 50


def _validate_submodule(submodule: str) -> bool:
    """Return True if submodule is a safe dotted identifier (or empty)."""
    return bool(_SUBMODULE_RE.match(submodule))


@dataclass(frozen=True, slots=True)
class ModuleIntrospectionService:
    """Live-import a package/submodule and render its public API.

    Depends only on ``PackageStore`` — the service first checks the indexed
    package exists (so we never import arbitrary modules the user didn't
    previously index), then delegates to ``asyncio.to_thread`` for the
    synchronous ``importlib`` + ``inspect`` work.
    """

    package_store: PackageStore

    async def inspect(self, package: str, submodule: str = "") -> str:
        pkg_name = normalize_package_name(package)
        if await self.package_store.get(pkg_name) is None:
            return (
                f"'{package}' is not indexed. "
                "Use list_packages() to see available packages."
            )
        if submodule and not _validate_submodule(submodule):
            return (
                f"Invalid submodule '{submodule}'. "
                "Use only letters, digits, underscores, and dots."
            )
        target = pkg_name + (f".{submodule}" if submodule else "")
        # Offload to a worker thread: importlib + inspect may block on disk
        # I/O (first-time imports) and on user code side effects.
        return await asyncio.to_thread(self._inspect_target, target)

    def _inspect_target(self, target: str) -> str:
        try:
            mod = importlib.import_module(target)
        except ImportError:
            return f"Cannot import '{target}'."

        items = []
        try:
            for name, obj in inspect.getmembers(mod):
                if name.startswith("_"):
                    continue
                if not (inspect.isfunction(obj) or inspect.isclass(obj)):
                    continue
                try:
                    sig = str(inspect.signature(obj))[:LIVE_SIGNATURE_MAX]
                except (ValueError, TypeError):
                    sig = "(...)"
                doc = (inspect.getdoc(obj) or "").split("\n")[0][:LIVE_DOC_MAX]
                kind = "class" if inspect.isclass(obj) else "def"
                items.append(f"{kind} {name}{sig}\n    {doc}")
                if len(items) >= _MAX_MEMBERS:
                    break
        except Exception:  # noqa: BLE001 -- AC #8 byte-parity: pre-PR server.py swallowed broadly so custom-DSL libs that raise unusual types during inspect.getmembers don't crash the handler
            pass

        if not items and hasattr(mod, "__path__"):
            try:
                subs = [
                    s for _, s, _ in pkgutil.iter_modules(mod.__path__)
                    if not s.startswith("_")
                ]
                return f"# {target}\nSubmodules: {', '.join(subs)}"
            except Exception:  # noqa: BLE001 -- AC #8 byte-parity: pre-PR server.py swallowed broadly on pkgutil.iter_modules failures
                pass

        return (
            f"# {target}\n\n" + "\n\n".join(items)
        ) if items else f"No API in '{target}'."
