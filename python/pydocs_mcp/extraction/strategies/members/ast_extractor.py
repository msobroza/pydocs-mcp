"""AstMemberExtractor — static AST parsing via Rust ``parse_py_file``.

Safe on untrusted dependencies — never executes package code. Used for
both project source and the static path for dependencies.

Module ids come from ``extraction/strategies/python_module_id.py``, the
single home of the rule, with one rule per root kind. Project files use
``package_rooted_module_id``, the same rule as chunk, tree and reference
ids, so ``src/needle/x.py`` is ``needle.x``. Dependency files use
``import_root_module_id``, because under site-packages the relative path is
the import path.

Colliding project member ids are ACCEPTED, not a defect (owner decision OD-A,
spec 2026-09-10-member-module-ids-design §9). Package rooting can map two
files to one ``(package, module)`` pair — ``examples/a/app/main.py`` and
``examples/b/app/main.py`` both become ``app.main``, as do this repo's
``tests/`` and ``benchmarks/tests/`` — so one module then carries the members
of both files. Chunks and document trees already collide that way, and
``document_trees`` upserts on ``(package, module)``, so a colliding member
hit's span comes from whichever file's tree was stored last. Parity with the
chunk side is the point: the old relpath-only ids were unique but
unresolvable. A source-path guard needs a new per-member field and is left to
the chunker-side collision spec (OD-C).

No per-module cap lives on this class:
:class:`~pydocs_mcp.extraction.config.MembersConfig` exposes
``members_per_module_cap`` but enforcement is the ingestion pipeline's
responsibility (downstream stage, out of scope). The extractor emits
every member it parses; upstream code truncates.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.extraction.config import _EXCLUDED_DIRS

# Back-compat alias — the canonical implementation lives in
# extraction/config.py next to _EXCLUDED_DIRS. Kept as a local name
# so existing imports + tests don't break; new code should import
# ``path_under_excluded`` directly from extraction.config.
from pydocs_mcp.extraction.config import path_under_excluded as _path_under_excluded
from pydocs_mcp.extraction.strategies._dep_helpers import (
    find_installed_distribution,
    find_site_packages_root,
)
from pydocs_mcp.extraction.strategies.python_module_id import (
    import_root_module_id,
    package_rooted_module_id,
)
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    ModuleMember,
    ModuleMemberFilterField,
)
from pydocs_mcp.project_toml import (
    ProjectExcludes,
    load_project_excludes,
    merge_excludes,
)


class _ParsedSymbol(Protocol):
    """Shape of one ``parse_py_file`` result (Rust ``ParsedMember`` or its fallback)."""

    name: str
    kind: str
    signature: str
    docstring: str


def _member_from_symbol(package: str, module: str, symbol: _ParsedSymbol) -> ModuleMember:
    return ModuleMember(
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


def _parent_dir_excluded(filepath: str, root: Path, effective: ProjectExcludes) -> bool:
    """True iff ``filepath``'s PARENT DIRECTORY falls under ``effective``.

    Directories-only rule (spec §4): the match target is the file's parent
    directory relpath, never the file path itself. Matching the full file
    relpath would let an entry that collides with a file NAME (bare
    ``"conf.py"``, anchored ``"docs/conf.py"``) drop that file's symbols
    here while chunk discovery — which prunes only ``os.walk`` dirnames —
    kept its chunks: a silent chunk/member divergence. Relpath scoping also
    keeps ancestor components ABOVE the walk root out of reach, mirroring
    chunk discovery's in-walk pruning.
    """
    try:
        rel_parent = os.path.relpath(Path(filepath).parent, str(root))
    except ValueError:
        # Different drive on Windows — same give-up posture as _parse_files.
        return False
    rel_parent = rel_parent.replace("\\", "/")
    if rel_parent == ".":
        # File directly at the walk root — there is no directory to match.
        return False
    # matches() also covers bare names by contract; the explicit
    # _path_under_excluded call is kept for parity with spec §7.5's
    # canonical matcher — the redundancy is intentional.
    return _path_under_excluded(rel_parent, effective.names) or effective.matches(rel_parent)


@dataclass(frozen=True, slots=True)
class AstMemberExtractor:
    # Per-run loader (spec D3): read the indexed project's own
    # ``[tool.pydocs-mcp] exclude_dirs`` fresh on every project walk so
    # --watch reindexes pick up TOML edits without a restart. Injected
    # strategy so tests never touch the filesystem.
    excludes_loader: Callable[[Path], ProjectExcludes] = load_project_excludes
    # YAML ``extraction.discovery.project.exclude_dirs`` entries, wired at
    # the write-side composition root (storage/factories.py). The dependency
    # member path (_dep_sync) deliberately ignores BOTH fields — it lists
    # ``dist.files`` directly and has never applied the directory blocklist
    # (spec §2 non-goal).
    scope_exclude_dirs: tuple[str, ...] = ()

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
        # site-packages is a sys.path entry: the relative path IS the import
        # path, so namespace packages (google/cloud/...) keep every segment.
        return self._parse_files(
            package_name, py_files, Path(root_str), module_id_for=import_root_module_id
        )

    def _parse_dir(self, root: Path, package: str) -> tuple[ModuleMember, ...]:
        """Project members, named with ``package_rooted_module_id``.

        WHY that rule: the project dir is not a ``sys.path`` entry. A
        relpath-only id gave ``src/needle/x.py`` -> ``src.needle.x`` and
        maturin ``python/pkg/x.py`` -> ``python.pkg.x``, while chunk, tree and
        reference ids (the same rule, via the chunker) said ``needle.x`` /
        ``pkg.x``, so get_symbol could not resolve the member ids that search
        published (spec 2026-09-10-member-module-ids-design §1).
        """
        from pydocs_mcp._fast import walk_py_files

        # walk_py_files (both the Rust impl and the Python fallback) has its
        # own hardcoded SKIP_DIRS that doesn't track the canonical Python-side
        # exclusion policy. Post-filter against the EFFECTIVE project set —
        # hardcoded floor ∪ YAML project entries ∪ the project's own
        # pyproject excludes — so the member side sees the SAME exclusion
        # set as chunk discovery. Without this, a checked-in
        # ``vendor/site-packages`` (floor) or a user-excluded ``fixtures/``
        # leaks into the symbol index even though chunk discovery skips it.
        effective = merge_excludes(
            _EXCLUDED_DIRS,
            self.scope_exclude_dirs,
            self.excludes_loader(root),
        )
        candidates = walk_py_files(str(root))
        py_files = [p for p in candidates if not _parent_dir_excluded(p, root, effective)]
        return self._parse_files(package, py_files, root, module_id_for=package_rooted_module_id)

    def _parse_files(
        self,
        package: str,
        paths: list[str],
        root: Path,
        *,
        module_id_for: Callable[[str, Path], str],
    ) -> tuple[ModuleMember, ...]:
        """Parse ``paths`` into members, naming each file's module with ``module_id_for``.

        A ``ValueError`` from ``module_id_for`` (a Windows cross-drive relpath)
        skips that file rather than failing the whole package.
        """
        # Deferred import so test-time module-level imports of this file don't
        # pull in the Rust native module when not strictly needed.
        from pydocs_mcp._fast import parse_py_file, read_files_parallel

        members: list[ModuleMember] = []
        for filepath, source in read_files_parallel(paths):
            if not source:
                continue
            try:
                module = module_id_for(filepath, root)
            except ValueError:
                continue
            members.extend(_member_from_symbol(package, module, s) for s in parse_py_file(source))
        return tuple(members)


__all__ = ("AstMemberExtractor", "_path_under_excluded")
