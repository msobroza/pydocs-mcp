"""Dependency resolution: find and parse all pyproject.toml and requirements files."""
from __future__ import annotations

import os
import re

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
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in-place so os.walk won't descend into skipped directories
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if fname == "pyproject.toml" or (
                fname.startswith("requirements") and fname.endswith(".txt")
            ):
                found.append(os.path.join(dirpath, fname))
    return found


def parse_pyproject_dependencies(path: str) -> list[str]:
    """Extract dependency names from a pyproject.toml file path.

    Returns normalised package names from [project] dependencies.
    Falls back to regex when tomllib is unavailable (Python < 3.11).
    """
    try:
        import tomllib  # type: ignore[import]
        with open(path, "rb") as f:
            data = tomllib.load(f)
        deps = data.get("project", {}).get("dependencies", [])
        return [normalize_package_name(d) for d in deps if d.strip()]
    except Exception:
        pass
    # Regex fallback for Python < 3.11
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        m = re.search(r'\[project\].*?dependencies\s*=\s*\[(.*?)\]', text, re.S)
        if not m:
            return []
        return [normalize_package_name(item) for item in re.findall(r'"([^"]+)"', m.group(1))]
    except Exception:
        return []


def parse_requirements_file(path: str) -> list[str]:
    """Extract dependency names from a requirements*.txt file.

    Skips blank lines, comments, and flag lines (-r, -c, -e).
    """
    result: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                result.append(normalize_package_name(line))
    except Exception:
        pass
    return result


def discover_declared_dependencies(project_dir: str) -> list[str]:
    """Return sorted, deduplicated dependency names found anywhere under project_dir.

    Scans all pyproject.toml and requirements*.txt in the entire directory tree,
    skipping virtualenvs and build artefacts. Version specifiers and extras are stripped.
    """
    all_deps: set[str] = set()
    for path in list_dependency_manifest_files(project_dir):
        fname = os.path.basename(path)
        if fname == "pyproject.toml":
            all_deps.update(parse_pyproject_dependencies(path))
        else:
            all_deps.update(parse_requirements_file(path))
    all_deps.discard("")
    return sorted(all_deps)
