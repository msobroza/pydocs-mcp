"""Documentation-presence checks for the late-interaction feature."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).parent.parent


def test_default_config_documents_late_interaction_block() -> None:
    text = (_repo_root() / "python/pydocs_mcp/defaults/default_config.yaml").read_text()
    assert "late_interaction" in text


def test_claude_md_lists_late_interaction_scorer() -> None:
    text = (_repo_root() / "CLAUDE.md").read_text()
    assert "late_interaction_scorer" in text


def test_readme_no_internal_jargon() -> None:
    """Re-run the audit grep from CLAUDE.md §"README files"."""
    out = subprocess.run(
        ["bash", "-c",
         'find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" '
         '-not -path "*/node_modules/*" -not -path "*/.git/*" | xargs '
         'grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+" || true'],
        cwd=_repo_root(),
        capture_output=True, text=True,
    )
    assert out.stdout.strip() == "", f"README jargon detected: {out.stdout!r}"
