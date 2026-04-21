"""Pre-extracted indexer helpers for dependency discovery + inspect-mode extraction.

Why pre-extracted (plan §Coupling conventions, Task 12 rationale): sub-PR #5
Batch 2 introduces extraction strategies (``AstMemberExtractor``,
``InspectMemberExtractor``, ``DependencyFileDiscoverer``) that need these three
helpers. Living under ``pydocs_mcp.indexer`` would force ``extraction/*`` to
import from ``indexer`` — the very module sub-PR #5 ultimately replaces — so
we copy the helpers verbatim here *before* any Batch 2 consumer arrives. This
breaks the ``extraction/* -> pydocs_mcp.indexer`` import edge so that Task 29
can delete ``indexer.py`` cleanly.

The originals still live in ``indexer.py`` during the transition period; both
copies stay in sync until Task 29 deletes the old module.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import logging
from pathlib import Path

from pydocs_mcp.constants import MODULE_DOCSTRING_MAX
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import Chunk, ChunkFilterField

log = logging.getLogger("pydocs-mcp")

SKIP_IMPORT = frozenset({
    "setuptools", "pip", "wheel", "pkg_resources",
    "distutils", "_distutils_hack", "certifi",
})
IMPORT_ALIASES = {
    "pillow": "PIL", "scikit-learn": "sklearn", "python-dateutil": "dateutil",
    "pyyaml": "yaml", "beautifulsoup4": "bs4", "opencv-python": "cv2",
    "opencv-python-headless": "cv2", "attrs": "attr",
}


def find_installed_distribution(dep_name: str):
    """Locate the installed ``importlib.metadata`` distribution for *dep_name*.

    Returns ``None`` if no matching distribution is installed — the service
    treats that as a non-fatal skip rather than a hard failure, because
    declared-but-not-installed deps are common during local development.
    """
    target = normalize_package_name(dep_name)
    for dist in importlib.metadata.distributions():
        raw = dist.metadata["Name"]
        if not raw:
            continue
        if raw.lower().replace("-", "_") == target:
            return dist
    return None


def find_site_packages_root(any_file: str) -> str:
    """Walk up to find site-packages directory."""
    for parent in Path(any_file).parents:
        if parent.name in ("site-packages", "dist-packages"):
            return str(parent)
    return str(Path(any_file).parent.parent)


def _extract_by_import(dist, depth: int) -> dict:
    """Import module, extract API via inspect.getmembers.

    Deferred imports for ``_build_package_record`` / ``_append_doc_file_chunks``
    / ``_extract_members_by_import`` — those three helpers still live in
    ``pydocs_mcp.indexer`` during the Batch 2 transition; Task 29 rehomes them
    here. Keeping the imports local avoids paying the circular-import cost at
    module load time and matches the plan's Task 12 "pure copy; no behavioral
    change" directive.
    """
    # Deferred imports: see docstring. These three helpers rehome to this
    # module in Task 29 along with the rest of indexer.py's body.
    from pydocs_mcp.indexer import (
        _append_doc_file_chunks,
        _build_package_record,
        _extract_members_by_import,
    )

    name = dist.metadata["Name"].lower().replace("-", "_")
    version = dist.metadata["Version"] or "?"
    package_record = _build_package_record(dist, name, version)
    _append_doc_file_chunks(dist, name, package_record)

    if name not in SKIP_IMPORT:
        iname = IMPORT_ALIASES.get(name, name)
        try:
            module = importlib.import_module(iname)
            if module.__doc__ and len(module.__doc__.strip()) > 30:
                package_record["chunks"].append(
                    Chunk(
                        text=module.__doc__.strip()[:MODULE_DOCSTRING_MAX],
                        metadata={
                            ChunkFilterField.PACKAGE.value: name,
                            ChunkFilterField.TITLE.value: name,
                            ChunkFilterField.ORIGIN.value: "docstring",
                        },
                    )
                )
            package_record["symbols"] = _extract_members_by_import(
                module, iname, name, max_depth=depth,
            )
        except Exception as e:
            log.debug("Failed to import %s: %s", iname, e)

    return package_record


__all__ = (
    "IMPORT_ALIASES",
    "SKIP_IMPORT",
    "find_installed_distribution",
    "find_site_packages_root",
    "_extract_by_import",
)
