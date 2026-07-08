"""AstMemberExtractor — static AST parsing via Rust ``parse_py_file``.

Safe on untrusted dependencies — never executes package code. Used for
both project source and the static path for dependencies.

No per-module cap lives on this class:
:class:`~pydocs_mcp.extraction.config.MembersConfig` exposes
``members_per_module_cap`` but enforcement is the ingestion pipeline's
responsibility (downstream stage, out of scope). The extractor emits
every member it parses; upstream code truncates.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.deps import normalize_package_name

# Back-compat alias — the canonical implementation lives in
# extraction/config.py next to _EXCLUDED_DIRS. Kept as a local name
# so existing imports + tests don't break; new code should import
# ``path_under_excluded`` directly from extraction.config.
from pydocs_mcp.extraction.config import path_under_excluded as _path_under_excluded
from pydocs_mcp.extraction.strategies._dep_helpers import (
    find_installed_distribution,
    find_site_packages_root,
)
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    ModuleMember,
    ModuleMemberFilterField,
)


def _module_from_rel_path(rel: str) -> str:
    """Convert a root-relative ``.py`` path to a dotted module name.

    Strips a trailing ``__init__`` PATH SEGMENT (not substring) — mirrors
    the chunker's ``_module_from_path`` (extraction/strategies/chunkers/
    ast_python.py) so member and chunk sides agree on module identity for
    the same file. The previous ``rel.replace(".__init__", "")`` matched
    the substring anywhere in the dotted path: ``pkg/__init__x.py`` (a
    filename that merely starts with ``__init__``, not the real package
    marker) became ``pkg.__init__x`` -> ``pkgx`` (prefix silently glued to
    the next real module), and a root-level ``__init__.py`` produced the
    bare literal ``__init__`` since it has no leading '.' to match.
    """
    parts = rel.replace(os.sep, ".").removesuffix(".py").split(".")
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@dataclass(frozen=True, slots=True)
class AstMemberExtractor:
    async def extract_from_project(
        self,
        project_dir: Path,
    ) -> tuple[ModuleMember, ...]:
        return await asyncio.to_thread(self._parse_dir, project_dir, PROJECT_PACKAGE_NAME)

    async def extract_from_dependency(
        self,
        dep_name: str,
    ) -> tuple[ModuleMember, ...]:
        return await asyncio.to_thread(self._dep_sync, dep_name)

    def _dep_sync(self, dep_name: str) -> tuple[ModuleMember, ...]:
        """Sync body for dependency extraction — reusable by
        :class:`InspectMemberExtractor` fallback without re-entering an
        event loop."""
        dist = find_installed_distribution(dep_name)
        if dist is None:
            return ()
        py_files = [str(dist.locate_file(f)) for f in (dist.files or []) if str(f).endswith(".py")]
        if not py_files:
            return ()
        root_str = find_site_packages_root(py_files[0])
        package_name = normalize_package_name(dep_name)
        return self._parse_files(package_name, py_files, Path(root_str))

    def _parse_dir(self, root: Path, package: str) -> tuple[ModuleMember, ...]:
        from pydocs_mcp._fast import walk_py_files

        # walk_py_files (both the Rust impl and the Python fallback) has its
        # own hardcoded SKIP_DIRS that doesn't track the canonical Python-side
        # ``_EXCLUDED_DIRS`` policy. They diverge on .hg / .svn / target /
        # site-packages / .coverage / .cache. Post-filter via the canonical
        # helper so the member side sees the SAME exclusion set as the
        # chunker side — without this, a checked-in ``vendor/site-packages``
        # directory leaks into the symbol index even though chunker discovery
        # skips it.
        candidates = walk_py_files(str(root))
        py_files = [p for p in candidates if not _path_under_excluded(p)]
        return self._parse_files(package, py_files, root)

    def _parse_files(
        self,
        package: str,
        paths: list[str],
        root: Path,
    ) -> tuple[ModuleMember, ...]:
        # Deferred import so test-time module-level imports of this file don't
        # pull in the Rust native module when not strictly needed.
        from pydocs_mcp._fast import parse_py_file, read_files_parallel

        members: list[ModuleMember] = []
        for filepath, source in read_files_parallel(paths):
            if not source:
                continue
            try:
                rel = os.path.relpath(filepath, str(root))
            except ValueError:
                continue
            module = _module_from_rel_path(rel)
            for symbol in parse_py_file(source):
                members.append(
                    ModuleMember(
                        metadata={
                            ModuleMemberFilterField.PACKAGE.value: package,
                            ModuleMemberFilterField.MODULE.value: module,
                            ModuleMemberFilterField.NAME.value: symbol.name,
                            ModuleMemberFilterField.KIND.value: symbol.kind,
                            "signature": symbol.signature,
                            "return_annotation": "",
                            "parameters": (),
                            "docstring": symbol.docstring,
                        }
                    )
                )
        return tuple(members)


__all__ = ("AstMemberExtractor", "_path_under_excluded")
