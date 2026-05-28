"""InspectMemberExtractor — live-import dependency extractor with AST fallback.

``extract_from_project`` ALWAYS delegates to the composed AST fallback —
spec §9.2 forbids importing the project-under-test.
``extract_from_dependency`` tries ``importlib.import_module`` via
``_extract_by_import``; any exception triggers a fallback to the AST
extractor with a debug log.

``members_per_module_cap`` is the inline DoS guard restored from
pre-refactor enforcement (/ultrareview F4) — a single huge module
can't dump 10K+ symbols into FTS. Defaults to the constant in
``_dep_helpers`` so unit tests instantiating the extractor without a
cap kwarg still inherit the production default; CLI wiring passes
the YAML-configured value.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.strategies._dep_helpers import (
    _extract_by_import,
    find_installed_distribution,
)
from pydocs_mcp.extraction.strategies.members.ast_extractor import AstMemberExtractor
from pydocs_mcp.models import ModuleMember

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True)
class InspectMemberExtractor:
    static_fallback: AstMemberExtractor
    depth: int = 1
    members_per_module_cap: int = 120
    signature_max_chars: int = 200
    docstring_max_chars: int = 1024

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
            record = _extract_by_import(
                dist, self.depth,
                members_per_module_cap=self.members_per_module_cap,
                signature_max_chars=self.signature_max_chars,
                docstring_max_chars=self.docstring_max_chars,
            )
            symbols = record.get("symbols", ())
            return tuple(symbols)
        except Exception as exc:
            log.debug(
                "inspect import failed for %s: %s — AST fallback", dep_name, exc,
            )
            # Re-enter the AST path directly (sync — we're already off-loop).
            return self.static_fallback._dep_sync(dep_name)


__all__ = ("InspectMemberExtractor",)
