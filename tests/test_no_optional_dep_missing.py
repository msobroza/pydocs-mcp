"""AC-13: OptionalDepMissing class no longer exists in the codebase."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# This test file itself mentions the symbol by design — exclude it from the
# grep so the assertion measures only production code + other tests.
_THIS_FILE = Path(__file__).name


def test_grep_finds_no_optional_dep_missing() -> None:
    # --include=*.py restricts the scan to source files (skips stale
    # .pyc bytecode in __pycache__/ from earlier test runs).
    # --exclude skips this very file, which mentions the symbol by design.
    result = subprocess.run(
        [
            "grep",
            "-rn",
            "--include=*.py",
            "--exclude",
            _THIS_FILE,
            "OptionalDepMissing",
            "python/",
            "tests/",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    # grep exits 1 on no match
    assert result.returncode != 0, f"OptionalDepMissing references still present:\n{result.stdout}"
