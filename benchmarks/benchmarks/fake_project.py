"""Generates the fake_project tree and its requirements.txt programmatically.

The fake project is deterministic so benchmark runs are comparable.
The FAKE_REQUIREMENTS list drives which packages get indexed.
"""
from __future__ import annotations

import shutil
from pathlib import Path

# Packages that will be listed in the fake project's requirements.txt.
# Keep this small — each package adds indexing latency to the benchmark.
FAKE_REQUIREMENTS: list[str] = [
    "requests",
    "pandas",
    "numpy",
]

# Source code lives next to this module in the repo.
_STATIC_ROOT = Path(__file__).parent.parent / "fake_project"


def generate_fake_project(dest: Path) -> Path:
    """Copy the static fake_project tree to *dest* and write requirements.txt.

    Args:
        dest: Directory to create (will be overwritten if it exists).

    Returns:
        Path to the generated project root.
    """
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_STATIC_ROOT, dest)

    req_path = dest / "requirements.txt"
    req_path.write_text("\n".join(FAKE_REQUIREMENTS) + "\n")
    return dest
