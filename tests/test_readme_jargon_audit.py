"""README jargon rule + audit grep (AC-29)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_pr_jargon_in_readmes() -> None:
    """The audit regex must match nothing in any tracked README.md."""
    result = subprocess.run(
        [
            "bash", "-c",
            "find . -name 'README.md' "
            "-not -path '*/.venv/*' "
            "-not -path '*/.claude/*' "
            "-not -path '*/node_modules/*' "
            "-not -path '*/.git/*' "
            "-not -path '*/.pytest_cache/*' "
            "| xargs grep -nE '"
            "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of"
            "|PR-[A-Z][0-9.]+'",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        f"README jargon violations:\n{result.stdout}"
    )


def test_claude_md_includes_pr_letter_pattern_in_jargon_rule() -> None:
    """CLAUDE.md's README-jargon section's regex catches PR-[A-Z]N.M."""
    claude_md = (ROOT / "CLAUDE.md").read_text()
    audit_block = claude_md.split("README files: no internal PR")[-1]
    audit_block = audit_block.split("Async Patterns")[0]
    assert "PR-[A-Z]" in audit_block
