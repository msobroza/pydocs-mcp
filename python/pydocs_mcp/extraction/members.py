"""Member extractors — ``AstMemberExtractor`` + ``InspectMemberExtractor`` (spec §9).

Both implement sub-PR #4's :class:`~pydocs_mcp.application.MemberExtractor`
Protocol. :class:`AstMemberExtractor` is static-only (never imports code) and
safe on untrusted packages. :class:`InspectMemberExtractor` delegates project
source to AST (spec §9.2 — we never import the project-under-test) and uses
``importlib.import_module`` for dependencies with an AST fallback on any
exception.

Imports live in :mod:`pydocs_mcp.extraction._dep_helpers` (NOT in
:mod:`pydocs_mcp.indexer`) — the ``extraction/*`` package must never take a
hard dependency on the module sub-PR #5 ultimately deletes (plan §Coupling
conventions, spec §3b).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.extraction._dep_helpers import (
    _extract_by_import,
    find_installed_distribution,
    find_site_packages_root,
)
from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True)
class AstMemberExtractor:
    """Static AST parsing via the Rust ``parse_py_file`` (with Python fallback).

    Safe on untrusted dependencies — never executes package code. Used for
    both project source and the static path for dependencies.

    No per-module cap lives on this class: :class:`~pydocs_mcp.extraction.config.MembersConfig`
    exposes ``members_per_module_cap`` but enforcement is the ingestion pipeline's
    responsibility (downstream stage, out of scope for Task 18). The extractor
    emits every member it parses; upstream code truncates.
    """

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]:
        return await asyncio.to_thread(self._parse_dir, project_dir, "__project__")

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]:
        return await asyncio.to_thread(self._dep_sync, dep_name)

    def _dep_sync(self, dep_name: str) -> tuple[ModuleMember, ...]:
        """Sync body for dependency extraction — reusable by
        :class:`InspectMemberExtractor` fallback without re-entering an
        event loop."""
        dist = find_installed_distribution(dep_name)
        if dist is None:
            return ()
        py_files = [
            str(dist.locate_file(f))
            for f in (dist.files or [])
            if str(f).endswith(".py")
        ]
        if not py_files:
            return ()
        root_str = find_site_packages_root(py_files[0])
        package_name = normalize_package_name(dep_name)
        return self._parse_files(package_name, py_files, Path(root_str))

    def _parse_dir(self, root: Path, package: str) -> tuple[ModuleMember, ...]:
        from pydocs_mcp._fast import walk_py_files
        py_files = walk_py_files(str(root))
        return self._parse_files(package, py_files, root)

    def _parse_files(
        self, package: str, paths: list[str], root: Path,
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
            module = (
                rel.replace(os.sep, ".")
                .removesuffix(".py")
                .replace(".__init__", "")
            )
            for symbol in parse_py_file(source):
                members.append(
                    ModuleMember(metadata={
                        ModuleMemberFilterField.PACKAGE.value: package,
                        ModuleMemberFilterField.MODULE.value: module,
                        ModuleMemberFilterField.NAME.value: symbol.name,
                        ModuleMemberFilterField.KIND.value: symbol.kind,
                        "signature": symbol.signature,
                        "return_annotation": "",
                        "parameters": (),
                        "docstring": symbol.docstring,
                    })
                )
        return tuple(members)


@dataclass(frozen=True, slots=True)
class InspectMemberExtractor:
    """Live-import dependency extractor; AST for projects (spec §9.2).

    ``extract_from_project`` ALWAYS delegates to the composed AST fallback —
    we never import the project-under-test. ``extract_from_dependency``
    tries ``importlib.import_module`` via ``_extract_by_import``; any
    exception triggers a fallback to the AST extractor with a debug log.
    """

    static_fallback: AstMemberExtractor
    depth: int = 1

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]:
        # spec §9.2: project source NEVER goes through live imports.
        return await self.static_fallback.extract_from_project(project_dir)

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]:
        return await asyncio.to_thread(self._inspect, dep_name)

    def _inspect(self, dep_name: str) -> tuple[ModuleMember, ...]:
        dist = find_installed_distribution(dep_name)
        if dist is None:
            # No installed distribution → empty tuple, no fallback (AST
            # would also find nothing). Matches spec §9 "non-fatal skip".
            return ()
        try:
            record = _extract_by_import(dist, self.depth)
            symbols = record.get("symbols", ())
            return tuple(symbols)
        except Exception as exc:  # noqa: BLE001 -- spec §9.2 fallback allowlist
            log.debug(
                "inspect import failed for %s: %s — AST fallback", dep_name, exc,
            )
            # Re-enter the AST path directly (sync — we're already off-loop).
            return self.static_fallback._dep_sync(dep_name)


__all__ = (
    "AstMemberExtractor",
    "InspectMemberExtractor",
)
