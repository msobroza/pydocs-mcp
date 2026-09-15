#!/usr/bin/env python3
"""Render GitHub Release notes from a Keep-a-Changelog file.

Both release workflows call this after the PyPI publish: the notes for tag
``v0.8.0`` are the body of the ``## [0.8.0]`` section of ``CHANGELOG.md``,
followed by GitHub's compare link to the previous tag. The heading's date
suffix (``## [0.8.0] — 2026-09-15``) is dropped.

Usage::

    python scripts/release_notes_from_changelog.py \\
        --changelog CHANGELOG.md --version 0.8.0 \\
        --repo msobroza/pydocs-mcp --tag v0.8.0 --previous-tag v0.7.0 > notes.md

Exit status ``2`` means the changelog has no non-empty section for that
version; the workflow then falls back to ``gh release create --generate-notes``
instead of publishing a Release with empty notes.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

EXIT_NO_SECTION = 2

# A Keep-a-Changelog release heading: "## [0.8.0] — 2026-09-15" or "## [Unreleased]".
_SECTION_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]", re.MULTILINE)


def extract_changelog_section(changelog_text: str, version: str) -> str | None:
    """Return the body under ``## [<version>]``, or ``None`` when absent or blank.

    >>> extract_changelog_section("## [1.0] — d\\n- a\\n\\n## [0.9]\\n- b\\n", "1.0")
    '- a'
    """
    headings = list(_SECTION_HEADING.finditer(changelog_text))
    for index, heading in enumerate(headings):
        if heading.group("version") != version:
            continue
        next_start = headings[index + 1].start() if index + 1 < len(headings) else None
        section = changelog_text[heading.end() : next_start]
        body = section.partition("\n")[2].strip()
        return body or None
    return None


def compare_link(repo: str, previous_tag: str, tag: str) -> str:
    """The "Full Changelog" compare line the hand-written releases carried."""
    return f"**Full Changelog**: https://github.com/{repo}/compare/{previous_tag}...{tag}"


def build_release_notes(
    *,
    changelog_text: str,
    version: str,
    repo: str,
    tag: str,
    previous_tag: str,
) -> str | None:
    """Changelog section plus compare link, or ``None`` when there is no section."""
    section = extract_changelog_section(changelog_text, version)
    if section is None:
        return None
    parts = [section]
    if previous_tag:
        parts.append(compare_link(repo, previous_tag, tag))
    return "\n\n".join(parts) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--changelog", type=pathlib.Path, required=True)
    parser.add_argument("--version", required=True, help="e.g. 0.8.0 (no tag prefix)")
    parser.add_argument("--repo", required=True, help="owner/name, for the compare link")
    parser.add_argument("--tag", required=True, help="the release tag, e.g. v0.8.0")
    parser.add_argument(
        "--previous-tag",
        default="",
        help="previous release tag; empty omits the compare link",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    notes = build_release_notes(
        changelog_text=args.changelog.read_text(encoding="utf-8"),
        version=args.version,
        repo=args.repo,
        tag=args.tag,
        previous_tag=args.previous_tag,
    )
    if notes is None:
        print(
            f"no non-empty '## [{args.version}]' section in {args.changelog}; "
            "expected a Keep-a-Changelog release heading for this version",
            file=sys.stderr,
        )
        return EXIT_NO_SECTION
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
