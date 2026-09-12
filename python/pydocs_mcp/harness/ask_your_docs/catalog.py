"""Application layer for reading a workspace of bundles (the project catalog).

``CatalogService`` scans a directory of pre-built ``*.db`` bundles through the
read-only :class:`~pydocs_mcp.harness.ask_your_docs.bundle.SqliteBundleReader` (no SQL
here, no migrate/rebuild path). ``workspace_catalog`` / ``render_catalog`` are
thin module-level wrappers the agent prompt uses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.bundle import BundleReader, IndexedBranch, SqliteBundleReader
from pydocs_mcp.models import BranchStatus


@dataclass(frozen=True, slots=True)
class WorkspaceBranchListing:
    """Every indexed project's ``branches`` rows, newest bundle per project.

    ``bundle_stems`` are the ``{project}_{slug}`` filename stems, the second
    form the server's ``project=`` selector accepts (multirepo.select_project).
    """

    projects: Mapping[str, tuple[IndexedBranch, ...]]
    bundle_stems: frozenset[str] = frozenset()

    @property
    def has_projects(self) -> bool:
        return bool(self.projects)

    @property
    def project_count(self) -> int:
        return len(self.projects)

    @property
    def project_names(self) -> tuple[str, ...]:
        return tuple(self.projects)

    def knows_project(self, name: str) -> bool:
        return name in self.projects or name in self.bundle_stems

    def rows(self, project: str) -> tuple[IndexedBranch, ...]:
        return self.projects.get(project, ())

    def pickable(self, project: str) -> tuple[IndexedBranch, ...]:
        """Live rows only — what the pickers and the catalog line list."""
        return tuple(
            r
            for r in self.rows(project)
            if r.status is BranchStatus.ACTIVE and not r.is_landing_unit
        )

    def merged(self, project: str) -> tuple[IndexedBranch, ...]:
        """Tombstones whose landing sha is known (the U2 "merged" group)."""
        return tuple(
            r
            for r in self.rows(project)
            if r.status in (BranchStatus.MERGED, BranchStatus.DELETED) and r.merged_into
        )

    def default_row(self, project: str) -> IndexedBranch | None:
        rows = self.rows(project)
        return next((r for r in rows if r.is_default), rows[0] if rows else None)

    def row(self, project: str, branch: str) -> IndexedBranch | None:
        return next((r for r in self.rows(project) if r.name == branch), None)

    def has_branch(self, project: str, branch: str) -> bool:
        if not project:  # a union request: any loaded project
            return any(self.row(name, branch) is not None for name in self.projects)
        return self.row(project, branch) is not None

    def head_sha(self, project: str, branch: str) -> str:
        row = self.row(project, branch)
        return row.head_sha if row else ""


EMPTY_BRANCH_LISTING = WorkspaceBranchListing(projects={})


@dataclass(frozen=True, slots=True)
class CatalogService:
    """Read a workspace of bundles: list projects, resolve a project's bundle.

    ``reader_factory`` builds a :class:`BundleReader` for one bundle path
    (defaults to :class:`SqliteBundleReader`); inject a fake to test offline.
    """

    workspace: str
    reader_factory: Callable[[Path], BundleReader] = field(default=SqliteBundleReader)

    def _bundles(self) -> list[Path]:
        return sorted(Path(self.workspace).expanduser().glob("*.db"))

    def projects(self) -> dict[str, list[str]]:
        """Map each project to its dependency packages (own code excluded). On
        duplicate names the newest bundle wins (mirrors the server routing)."""
        best: dict[str, tuple[float, list[str]]] = {}
        for db in self._bundles():
            reader = self.reader_factory(db)
            name = reader.project_name()
            indexed_at = reader.indexed_at()
            if name not in best or indexed_at > best[name][0]:
                best[name] = (indexed_at, reader.packages())
        return {name: packages for name, (_, packages) in sorted(best.items())}

    def branch_listing(self) -> WorkspaceBranchListing:
        """Every project's branch rows (newest bundle wins) plus the bundle stems."""
        best: dict[str, tuple[float, tuple[IndexedBranch, ...]]] = {}
        stems: set[str] = set()
        for db in self._bundles():
            reader = self.reader_factory(db)
            name, indexed_at = reader.project_name(), reader.indexed_at()
            stems.add(db.stem)
            if name not in best or indexed_at > best[name][0]:
                best[name] = (indexed_at, reader.branches())
        projects = {name: rows for name, (_, rows) in sorted(best.items())}
        return WorkspaceBranchListing(projects=projects, bundle_stems=frozenset(stems))

    def bundle_path(self, project: str) -> Path | None:
        """The ``.db`` whose project identity matches ``project``, or None."""
        for db in self._bundles():
            if self.reader_factory(db).project_name() == project:
                return db
        return None


def workspace_catalog(workspace: str) -> dict[str, list[str]]:
    """Project -> dependency packages for the whole workspace (agent prompt)."""
    return CatalogService(workspace).projects()


def workspace_branch_listing(workspace: str) -> WorkspaceBranchListing:
    """Project -> branch rows for the whole workspace (panel, popover, footer, prompt)."""
    return CatalogService(workspace).branch_listing()


def _branch_segment(
    project: str, branches: WorkspaceBranchListing | None, show_merged: bool
) -> str:
    """``branches: main (default), feature/x — `` or ``""`` (nothing to list)."""
    if branches is None:
        return ""
    names = [f"{r.name} (default)" if r.is_default else r.name for r in branches.pickable(project)]
    if show_merged:
        default = branches.default_row(project)
        for r in branches.merged(project):
            # merged_into is the LANDING SHA, never a branch name (multi-branch §6.8a).
            base = r.base_name or (default.name if default else "base")
            names.append(f"{r.name} (merged into {base} @{str(r.merged_into)[:7]})")
    return f"branches: {', '.join(names)} — " if names else ""


def _catalog_line(
    name: str, packages: list[str], branches: WorkspaceBranchListing | None, show_merged: bool
) -> str:
    packages_text = (
        f"dependency packages: {', '.join(packages)}"
        if packages
        else "own code only (no dependency packages indexed)"
    )
    return f"- {name} — {_branch_segment(name, branches, show_merged)}{packages_text}"


def render_catalog(
    catalog: dict[str, list[str]],
    branches: WorkspaceBranchListing | None = None,
    *,
    show_merged: bool = False,
) -> str:
    """One line per project, naming the exact project=/branch=/package= values.

    ``branches=None`` renders today's bytes; the branch segment is inserted
    between the project name and the package segment and lists pickable rows
    only; ``show_merged`` appends the landed-branch tombstone markers.
    """
    return "\n".join(
        _catalog_line(name, packages, branches, show_merged) for name, packages in catalog.items()
    )
