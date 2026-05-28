"""ModuleInspector — live importlib + inspect (spec §5.1).

Post-#5a-2: depends only on a ``uow_factory: Callable[[], UnitOfWork]``.
Reads the indexed-package row through ``uow.packages.get(...)`` inside
``async with self.uow_factory() as uow:`` — the UoW Protocol guarantees
``packages`` is valid inside the context (spec §14.2 of #5b spec).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.constants import LIVE_DOC_MAX, LIVE_SIGNATURE_MAX
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.storage.protocols import UnitOfWork

# Use \A...\Z (not ^...$) — Python's ``re`` treats ``$`` as matching before a
# trailing ``\n`` by default, which lets ``"foo\n"`` slip past a naive anchor
# and reach ``importlib.import_module`` downstream.
_SUBMODULE_RE = re.compile(r"\A([A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*)?\Z")

# Match the pre-PR server.py handler: cap inspection at 50 public members so
# inspecting giant modules (e.g. ``numpy``) can't bloat the MCP response.
_MAX_MEMBERS: int = 50

# Single source of truth for the narrowed-from-broad ``except`` clause used
# by both the ``inspect.getmembers`` site and the ``pkgutil.iter_modules``
# fallback site below. Originally widened to ``except Exception`` for
# byte-parity with the pre-PR server.py handler; the narrow tuple lets
# real bugs (ValueError, KeyError, TypeError, …) propagate so they surface
# in tests instead of being silently swallowed.
_BENIGN_INSPECT_EXCEPTIONS = (
    AttributeError,
    ImportError,
    OSError,
    RuntimeError,
)


def _validate_submodule(submodule: str) -> bool:
    """Return True if submodule is a safe dotted identifier (or empty)."""
    return bool(_SUBMODULE_RE.match(submodule))


@dataclass(frozen=True, slots=True)
class ModuleInspector:
    """Live-import a package/submodule and render its public API.

    Depends only on ``uow_factory`` — opens a UoW per ``inspect`` call to
    check the package is indexed before crossing the import boundary.
    """

    uow_factory: Callable[[], UnitOfWork]

    async def inspect(self, package: str, submodule: str = "") -> str:
        pkg_name = normalize_package_name(package)
        async with self.uow_factory() as uow:
            pkg = await uow.packages.get(pkg_name)
        if pkg is None:
            return f"'{package}' is not indexed. Use lookup(target='') to see available packages."
        if submodule and not _validate_submodule(submodule):
            return (
                f"Invalid submodule '{submodule}'. Use only letters, digits, underscores, and dots."
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
        except _BENIGN_INSPECT_EXCEPTIONS:
            # Custom-DSL libs raise these from ``inspect.getmembers``
            # (broken __getattr__, lazy import shims, FS-backed
            # descriptors, RuntimeError property guards).
            pass

        if not items and hasattr(mod, "__path__"):
            try:
                subs = [
                    s for _, s, _ in pkgutil.iter_modules(mod.__path__) if not s.startswith("_")
                ]
                return f"# {target}\nSubmodules: {', '.join(subs)}"
            except _BENIGN_INSPECT_EXCEPTIONS:
                # Same rationale as the getmembers site above — broken /
                # lazy / FS-backed packages raise these.
                pass

        return (f"# {target}\n\n" + "\n\n".join(items)) if items else f"No API in '{target}'."
