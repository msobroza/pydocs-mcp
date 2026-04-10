"""Resolve project dependencies from pyproject.toml or requirements.txt."""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("pydocs-mcp")


def resolve(project_dir: Path) -> list[str]:
    """Find and parse the dependency file. pyproject.toml has priority."""
    toml = project_dir / "pyproject.toml"
    if toml.exists():
        deps = _parse_toml(toml)
        if deps:
            log.info("pyproject.toml → %d deps", len(deps))
            return deps

    for name in ("requirements.txt", "requirements/base.txt", "requirements/prod.txt"):
        req = project_dir / name
        if req.exists():
            deps = _parse_requirements(req)
            log.info("%s → %d deps", name, len(deps))
            return deps

    log.warning("No dependency file found in %s", project_dir)
    return []


def normalize(raw: str) -> str:
    """Normalize a package name: strip version, lowercase, replace - with _."""
    return re.split(r"[>=<!\[;]", raw, maxsplit=1)[0].strip().lower().replace("-", "_")


def _parse_toml(path: Path) -> list[str]:
    deps, inside = [], False
    for line in path.read_text("utf-8").splitlines():
        s = line.strip()
        if re.match(r"^dependencies\s*=\s*\[", s):
            inside = True
            for m in re.finditer(r'"([^"]+)"', s):
                deps.append(normalize(m.group(1)))
            if s.endswith("]"):
                return deps
            continue
        if inside:
            if s.startswith("]"):
                return deps
            m = re.search(r'"([^"]+)"', s)
            if m:
                deps.append(normalize(m.group(1)))
    return deps


def _parse_requirements(path: Path) -> list[str]:
    deps = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line[0] in ("#", "-"):
            continue
        name = re.split(r"[>=<!\[;#\s]", line, maxsplit=1)[0]
        if name:
            deps.append(normalize(name))
    return deps
