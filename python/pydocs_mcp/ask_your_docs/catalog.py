"""Application layer for reading a workspace of bundles (the project catalog).

``CatalogService`` scans a directory of pre-built ``*.db`` bundles through the
read-only :class:`~pydocs_mcp.ask_your_docs.bundle.SqliteBundleReader` (no SQL
here, no migrate/rebuild path). ``workspace_catalog`` / ``render_catalog`` are
thin module-level wrappers the agent prompt uses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.ask_your_docs.bundle import BundleReader, SqliteBundleReader


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

    def bundle_path(self, project: str) -> Path | None:
        """The ``.db`` whose project identity matches ``project``, or None."""
        for db in self._bundles():
            if self.reader_factory(db).project_name() == project:
                return db
        return None


def workspace_catalog(workspace: str) -> dict[str, list[str]]:
    """Project -> dependency packages for the whole workspace (agent prompt)."""
    return CatalogService(workspace).projects()


def render_catalog(catalog: dict[str, list[str]]) -> str:
    """One line per project, naming the exact project=/package= filter values."""
    return "\n".join(
        f"- {name} — dependency packages: {', '.join(packages)}"
        if packages
        else f"- {name} — own code only (no dependency packages indexed)"
        for name, packages in catalog.items()
    )
