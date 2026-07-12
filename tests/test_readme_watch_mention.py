"""README + DOCUMENTATION.md pin the `--watch` documentation surface
(spec 2026-07-11-watch-default-install §3.9 / AC-10)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_mentions_watch_flag() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--watch" in readme, "README must mention the --watch CLI flag"


def test_readme_states_watcher_in_default_install() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "default install" in readme, "README must state the watcher ships in the default install"


def test_no_watch_extras_install_instruction_anywhere() -> None:
    """Promotion regression (spec 2026-07-11-watch-default-install §3.9 /
    AC-15a): neither user doc may instruct installing the watch extra —
    match BOTH quoting forms so the docs-audit spec's quoting lint (D10)
    can't mask a surviving instruction."""
    for doc in ("README.md", "DOCUMENTATION.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "pip install pydocs-mcp[watch]" not in text, f"unquoted watch install hint in {doc}"
        assert "pip install 'pydocs-mcp[watch]'" not in text, f"quoted watch install hint in {doc}"


def test_documentation_md_has_live_reindexing_subsection() -> None:
    doc = (ROOT / "DOCUMENTATION.md").read_text(encoding="utf-8")
    # Heading style: existing subsections use `## ` or `### `. We pin a
    # case-insensitive substring so a stylistic tweak (subsection level)
    # doesn't break the test.
    assert re.search(
        r"(?im)^#{2,4}\s+live re-?indexing",
        doc,
    ), "DOCUMENTATION.md missing the 'Live re-indexing' subsection"


def test_documentation_md_describes_yaml_knobs() -> None:
    doc = (ROOT / "DOCUMENTATION.md").read_text(encoding="utf-8")
    assert "serve.watch" in doc
    assert "debounce_ms" in doc
    assert "ignore_globs" in doc


def test_documentation_md_states_default_install() -> None:
    doc = (ROOT / "DOCUMENTATION.md").read_text(encoding="utf-8")
    assert "part of the default install" in doc, (
        "DOCUMENTATION.md's Live re-indexing Install subsection must state "
        "the watcher ships in the default install"
    )


def test_readme_does_not_introduce_pr_jargon() -> None:
    """Re-run the project-wide README jargon audit after edits — must stay clean."""
    import subprocess

    result = subprocess.run(
        [
            "bash",
            "-c",
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
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        f"README jargon violations introduced by --watch docs:\n{result.stdout}"
    )
