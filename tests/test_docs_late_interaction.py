"""Documentation-presence checks for the late-interaction feature."""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).parent.parent


def test_default_config_documents_late_interaction_block() -> None:
    text = (_repo_root() / "python/pydocs_mcp/defaults/default_config.yaml").read_text(
        encoding="utf-8"
    )
    assert "late_interaction" in text


def test_claude_md_lists_late_interaction_scorer() -> None:
    text = (_repo_root() / "CLAUDE.md").read_text(encoding="utf-8")
    assert "late_interaction_scorer" in text


def test_readme_no_internal_jargon() -> None:
    """Audit every README.md for forbidden internal-PR / sub-PR / task jargon.

    Pure-Python implementation so the test runs on Windows runners too —
    the original bash + find + xargs grep pipeline isn't portable.
    Mirrors the audit pattern from CLAUDE.md §"README files".
    """
    pattern = re.compile(
        r"PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+",
    )
    excluded_parts = {".venv", ".claude", "node_modules", ".git"}

    offenders: list[str] = []
    for readme in _repo_root().rglob("README.md"):
        if excluded_parts.intersection(readme.parts):
            continue
        rel = readme.relative_to(_repo_root())
        for lineno, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, "README jargon detected:\n" + "\n".join(offenders)
