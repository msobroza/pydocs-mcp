"""Unit tests for scripts/release_notes_from_changelog.py.

The script feeds ``gh release create`` in release.yml and release-eval.yml;
these tests pin the section boundaries and the exit-2 fallback contract.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType

import pytest

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "release_notes_from_changelog.py"
)

_CHANGELOG = """# Changelog

## [Unreleased]

### Added

- something not yet shipped

## [0.8.0] — 2026-09-15

### Added

- **Pointer table** behind a compatibility gate.

### Fixed

- The client's limit reaches the pipeline.

## [0.7.0] — 2026-09-12

### Added

- query_prefix knob.
"""


@pytest.fixture(scope="module")
def release_notes_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_notes_from_changelog", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extracts_only_the_requested_section(release_notes_module: ModuleType) -> None:
    body = release_notes_module.extract_changelog_section(_CHANGELOG, "0.8.0")

    assert body is not None
    assert body.startswith("### Added")
    assert "Pointer table" in body
    assert "The client's limit reaches the pipeline." in body
    assert "query_prefix" not in body
    assert "not yet shipped" not in body


def test_heading_date_suffix_is_dropped(release_notes_module: ModuleType) -> None:
    body = release_notes_module.extract_changelog_section(_CHANGELOG, "0.7.0")

    assert body == "### Added\n\n- query_prefix knob."


def test_last_section_runs_to_end_of_file(release_notes_module: ModuleType) -> None:
    body = release_notes_module.extract_changelog_section(_CHANGELOG, "0.7.0")

    assert body is not None
    assert body.endswith("query_prefix knob.")


def test_missing_version_returns_none(release_notes_module: ModuleType) -> None:
    assert release_notes_module.extract_changelog_section(_CHANGELOG, "0.6.0") is None


def test_blank_section_counts_as_missing(release_notes_module: ModuleType) -> None:
    text = "## [Unreleased]\n\n## [1.0.0] — 2026-01-01\n\n- shipped\n"

    assert release_notes_module.extract_changelog_section(text, "Unreleased") is None
    assert release_notes_module.extract_changelog_section(text, "1.0.0") == "- shipped"


def test_release_notes_append_compare_link(release_notes_module: ModuleType) -> None:
    notes = release_notes_module.build_release_notes(
        changelog_text=_CHANGELOG,
        version="0.8.0",
        repo="msobroza/pydocs-mcp",
        tag="v0.8.0",
        previous_tag="v0.7.0",
    )

    assert notes is not None
    assert notes.endswith(
        "**Full Changelog**: https://github.com/msobroza/pydocs-mcp/compare/v0.7.0...v0.8.0\n"
    )


def test_release_notes_without_previous_tag_omit_compare_link(
    release_notes_module: ModuleType,
) -> None:
    notes = release_notes_module.build_release_notes(
        changelog_text=_CHANGELOG,
        version="0.8.0",
        repo="msobroza/pydocs-mcp",
        tag="v0.8.0",
        previous_tag="",
    )

    assert notes is not None
    assert "Full Changelog" not in notes


def test_main_writes_notes_to_stdout(
    release_notes_module: ModuleType,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_CHANGELOG, encoding="utf-8")

    exit_code = release_notes_module.main(
        [
            "--changelog",
            str(changelog),
            "--version",
            "0.8.0",
            "--repo",
            "msobroza/pydocs-mcp",
            "--tag",
            "v0.8.0",
            "--previous-tag",
            "v0.7.0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.startswith("### Added")
    assert "compare/v0.7.0...v0.8.0" in captured.out


def test_main_exits_two_when_section_is_missing(
    release_notes_module: ModuleType,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_CHANGELOG, encoding="utf-8")

    exit_code = release_notes_module.main(
        [
            "--changelog",
            str(changelog),
            "--version",
            "0.6.0",
            "--repo",
            "msobroza/pydocs-mcp",
            "--tag",
            "v0.6.0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == release_notes_module.EXIT_NO_SECTION
    assert captured.out == ""
    assert "## [0.6.0]" in captured.err
