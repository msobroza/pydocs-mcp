#!/usr/bin/env python3
"""Generate stdlib_qnames.json for AC #15 stdlib indexing.

Walks `sys.stdlib_module_names` + an explicit `builtins.*` allowlist to
produce a flat list of qnames representing CPython stdlib + builtins entry
points. The reference resolver consumes this list to flip `to_node_id` from
None to a resolved qname when a project's CALLS edge targets a stdlib /
builtins symbol (e.g., `os.path.join`, `asyncio.to_thread`, `len`,
`isinstance`).

Run:
    python scripts/generate_stdlib_qnames.py --output python/pydocs_mcp/defaults/stdlib_qnames.json

Sub-PR follow-up to #5c. Honors CLAUDE.md §"MCP API surface vs YAML
configuration" — the resulting JSON is shipped as a default; behavior is
toggled by the YAML key `reference_graph.resolver.include_stdlib`.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from pathlib import Path

# Explicit allowlist of builtins worth indexing. Built-in callables /
# classes that show up in real-world code with backtick-quoted references
# or are common CALLS / IMPORTS targets. Done via explicit allowlist (not
# `dir(builtins)`) because the latter over-captures internal names like
# `_`, `__build_class__`, exceptions, etc. that the resolver wouldn't help with.
_BUILTINS_ALLOWLIST: tuple[str, ...] = (
    # Type constructors / casts
    "bool", "int", "float", "str", "bytes", "bytearray", "complex",
    "list", "tuple", "set", "frozenset", "dict",
    "range", "slice", "type", "object", "memoryview",
    # I/O
    "print", "input", "open", "repr",
    # Iteration
    "iter", "next", "enumerate", "zip", "map", "filter", "sorted",
    "reversed", "any", "all", "sum", "min", "max", "len",
    # Reflection
    "isinstance", "issubclass", "callable", "hasattr", "getattr",
    "setattr", "delattr", "vars", "dir", "id", "hash",
    # Numbers
    "abs", "round", "divmod", "pow", "bin", "oct", "hex",
    "ascii", "chr", "ord", "format",
    # Misc
    "globals", "locals", "exec", "eval", "compile", "help",
    "staticmethod", "classmethod", "property", "super",
    "Exception", "BaseException", "TypeError", "ValueError",
    "KeyError", "IndexError", "AttributeError", "RuntimeError",
    "NotImplementedError", "FileNotFoundError", "PermissionError",
    "OSError", "IOError",
)


def _public(name: str) -> bool:
    """Skip underscore-prefixed names + dunder names except common ones."""
    return not name.startswith("_") or name in {"__init__", "__call__", "__enter__", "__exit__"}


def _is_implementation_module(name: str) -> bool:
    """A stdlib_module_names entry that exists only to back another module
    (e.g., posixpath / ntpath back os.path). We skip these at the top level
    so they appear only under their public name (`os.path.*`)."""
    return name in {"posixpath", "ntpath", "genericpath", "macpath"}


# Modules that have side effects on import (print, open a browser, start an
# event loop, etc.) — we still want to add them as qnames so the resolver
# can flag references to them, but we don't import them and don't walk
# their members.
_SIDE_EFFECT_MODULES: frozenset[str] = frozenset({
    "this",          # prints the Zen of Python
    "antigravity",   # opens a browser to xkcd
    "__hello__",     # prints "Hello world!"
    "__phello__",    # prints "Hello world!"
    "idlelib",       # starts IDLE-related globals
    "turtledemo",    # demo package with side-effecty imports
    "tkinter",       # opens a display connection on some platforms
})


def _module_qnames(module_name: str, *, visited: set[str] | None = None) -> set[str]:
    """Return qnames exported by ``module_name``.

    Includes:
      - the module itself
      - top-level public callables / classes (filtered by ``__module__`` to
        drop re-exports)
      - submodules attached as attributes (e.g., ``os.path``, ``json.decoder``)
        — recursively walked one level deep so common cases like
        ``os.path.join`` and ``email.mime.text.MIMEText`` resolve.

    ``visited`` short-circuits the recursion to avoid cycles + repeats.
    """
    if visited is None:
        visited = set()
    if module_name in visited:
        return set()
    visited.add(module_name)

    qnames: set[str] = {module_name}
    try:
        mod = importlib.import_module(module_name)
    except (ImportError, Exception):
        return qnames

    for name, obj in inspect.getmembers(mod):
        if not _public(name):
            continue

        # Recurse into attached submodules (one level). The check
        # `obj.__name__ == f"{module_name}.{name}"` filters out aliases
        # (e.g., `os.path` aliases `posixpath`, but its __name__ is
        # `posixpath` — we still want it under `os.path`).
        if inspect.ismodule(obj):
            sub_real = getattr(obj, "__name__", "") or ""
            sub_public = f"{module_name}.{name}"
            qnames.add(sub_public)
            # Pull the submodule's public members in under the *public*
            # path. Re-key __module__ matches against the real name.
            try:
                for sub_name, sub_obj in inspect.getmembers(obj):
                    if not _public(sub_name):
                        continue
                    if not (
                        inspect.isfunction(sub_obj)
                        or inspect.isclass(sub_obj)
                        or inspect.ismethod(sub_obj)
                    ):
                        continue
                    obj_mod = getattr(sub_obj, "__module__", None) or ""
                    if obj_mod == sub_real or obj_mod.startswith(sub_real + "."):
                        qnames.add(f"{sub_public}.{sub_name}")
            except Exception:
                pass
            continue

        if not (inspect.isfunction(obj) or inspect.isclass(obj) or inspect.ismethod(obj)):
            continue
        # __module__ filter: skip re-exports.
        obj_module = getattr(obj, "__module__", None) or ""
        if obj_module == module_name or obj_module.startswith(module_name + "."):
            qnames.add(f"{module_name}.{name}")
    return qnames


def _builtins_qnames() -> set[str]:
    """Return qnames for the explicit builtins allowlist.

    Two shapes are added per name:
      - `<name>` (bare) — covers `len(...)` calls captured as `to_name = "len"`
      - `builtins.<name>` — covers explicit `from builtins import len`-style
        references.
    """
    out: set[str] = set()
    import builtins
    for name in _BUILTINS_ALLOWLIST:
        if hasattr(builtins, name):
            out.add(name)
            out.add(f"builtins.{name}")
    return out


def generate(output_path: Path) -> int:
    """Generate the stdlib_qnames.json file. Returns the qname count."""
    qnames: set[str] = set()

    # Walk every stdlib module name (Python 3.10+).
    for module_name in sorted(sys.stdlib_module_names):
        if module_name.startswith("_"):
            continue  # skip private modules (_collections, _thread, etc.)
        if _is_implementation_module(module_name):
            continue  # posixpath etc. surface only as os.path.*
        if module_name in _SIDE_EFFECT_MODULES:
            # Add the bare module qname so references resolve, but don't
            # import + introspect — that would print, open a browser, etc.
            qnames.add(module_name)
            continue
        qnames.update(_module_qnames(module_name))

    # Add builtins.
    qnames.update(_builtins_qnames())

    sorted_qnames = sorted(qnames)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(
            {
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
                "count": len(sorted_qnames),
                "qnames": sorted_qnames,
            },
            f,
            indent=2,
        )
    return len(sorted_qnames)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent.parent
        / "python/pydocs_mcp/defaults/stdlib_qnames.json",
    )
    args = p.parse_args()
    count = generate(args.output)
    print(f"Wrote {count} qnames to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
