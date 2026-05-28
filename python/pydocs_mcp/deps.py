"""Dependency resolution: find and parse all pyproject.toml and requirements files."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories that never contain meaningful project dependencies
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".tox", ".eggs", "build", "dist", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "htmlcov", ".nox",
})


def normalize_package_name(raw: str) -> str:
    """Normalize a raw dependency string to a plain package name.

    Examples: 'FastAPI>=0.100' -> 'fastapi', 'scikit-learn[ml]' -> 'scikit_learn'
    """
    name = re.split(r"[><=!;\[\s(]", raw)[0]
    return name.strip().lower().replace("-", "_")


def list_dependency_manifest_files(root: str) -> list[str]:
    """Recursively find all pyproject.toml and requirements*.txt under root.

    Prunes _SKIP_DIRS so virtualenvs and build artefacts are never descended into.
    """
    found: list[str] = []
    # os.walk is the right API for in-place dirnames pruning; Path.rglob has no
    # equivalent skip-subtree mechanism (would descend into .venv/ etc).
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in-place so os.walk won't descend into skipped directories
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if fname == "pyproject.toml" or (
                fname.startswith("requirements") and fname.endswith(".txt")
            ):
                found.append(str(Path(dirpath) / fname))
    return found


def parse_pyproject_dependencies(path: str) -> list[str]:
    """Extract dependency names from a pyproject.toml file path.

    Returns normalised package names from [project] dependencies.
    Falls back to regex when tomllib is unavailable (Python < 3.11).
    """
    pyproject_path = Path(path)
    try:
        import tomllib
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        deps = data.get("project", {}).get("dependencies", [])
        return [normalize_package_name(d) for d in deps if d.strip()]
    except Exception:
        # Best-effort parsing: malformed pyproject.toml shouldn't crash discovery;
        # log at debug level so the failure isn't entirely silent for an operator.
        logger.debug("tomllib failed for %s; falling back to regex parse", path, exc_info=True)
    # Regex fallback for Python < 3.11
    try:
        with pyproject_path.open(encoding="utf-8", errors="ignore") as f:
            text = f.read()
        m = re.search(r'\[project\].*?dependencies\s*=\s*\[(.*?)\]', text, re.S)
        if not m:
            return []
        return [normalize_package_name(item) for item in re.findall(r'"([^"]+)"', m.group(1))]
    except Exception:
        logger.debug("regex fallback failed for %s", path, exc_info=True)
        return []


def parse_requirements_file(path: str) -> list[str]:
    """Extract dependency names from a requirements*.txt file.

    Skips blank lines, comments, and flag lines (-r, -c, -e).
    """
    result: list[str] = []
    try:
        with Path(path).open(encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                result.append(normalize_package_name(line))
    except Exception:
        # Best-effort: an unreadable requirements file shouldn't crash discovery.
        logger.debug("failed to parse requirements file %s", path, exc_info=True)
    return result


def discover_declared_dependencies(project_dir: str) -> list[str]:
    """Return sorted, deduplicated dependency names found anywhere under project_dir.

    Scans all pyproject.toml and requirements*.txt in the entire directory tree,
    skipping virtualenvs and build artefacts. Version specifiers and extras are stripped.
    """
    all_deps: set[str] = set()
    for path in list_dependency_manifest_files(project_dir):
        fname = Path(path).name
        if fname == "pyproject.toml":
            all_deps.update(parse_pyproject_dependencies(path))
        else:
            all_deps.update(parse_requirements_file(path))
    all_deps.discard("")
    return sorted(all_deps)
