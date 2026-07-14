"""Tests for the git/docs decision-mining sources (spec §D8).

Three sources here: ``commit_messages`` parses the framed ``git log`` dump the
capture stage passes in via ``ctx.git_log_text`` (NO subprocess in these tests —
the seam is a plain string); ``changelog`` splits CHANGELOG/CHANGES markdown on
headings and keyword-gates each entry; ``docs_prose`` reads a bounded set of
top-level prose files and keyword-gates each paragraph. The one subprocess in
the layer lives in ``_git.read_git_log`` and is exercised against a real tmp
repo built with ``git init`` (mirroring the freshness resolver's test style).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.extraction.decisions import CaptureContext
from pydocs_mcp.extraction.decisions._git import _normalize_log, read_git_log
from pydocs_mcp.extraction.decisions.sources import (
    ChangelogSource,
    CommitMessagesSource,
    DocsProseSource,
)
from pydocs_mcp.project_toml import ProjectExcludes
from pydocs_mcp.retrieval.config.models import DecisionCaptureConfig

# One qualifying commit ("migrate" → 1 keyword + a 2-line body ≥ threshold via a
# second keyword "replace") and one that scores nothing ("fix typo"). The framing
# is exactly what ``_git._normalize_log`` emits and ``commit_messages`` parses.
_LOG = (
    "commit aaaa1111\nauthor-date 1700000000\nsubject migrate vector store to sidecar\n"
    "body We replace the in-db blobs.\nRationale: row size.\nfiles pkg/db.py pkg/store.py\n==END==\n"
    "commit bbbb2222\nauthor-date 1700000100\nsubject fix typo\nbody \nfiles README.md\n==END==\n"
)


def _cfg(**overrides: object) -> DecisionCaptureConfig:
    return DecisionCaptureConfig(**overrides)  # type: ignore[arg-type]


def _ctx(*, project_root: Path | None = None, git_log_text: str = "") -> CaptureContext:
    """A source's whole input; ``trees`` empty here (git/docs sources ignore it)."""
    return CaptureContext(
        project_root=project_root or Path("/x"),
        trees=(),
        config=_cfg(),
        git_log_text=git_log_text,
    )


# ── commit_messages ────────────────────────────────────────────────────────


async def test_keyword_scored_commit_becomes_proposed_decision(tmp_path) -> None:
    # affected_files are filtered to paths that exist under the tree — create them.
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "db.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "store.py").write_text("y = 2\n")
    raws = await CommitMessagesSource().mine(_ctx(project_root=tmp_path, git_log_text=_LOG))
    assert len(raws) == 1  # "fix typo" filtered out
    assert raws[0].status == "proposed" and raws[0].confidence == 0.70
    assert raws[0].affected_files == ("pkg/db.py", "pkg/store.py")
    assert raws[0].evidence_date == 1700000000.0
    assert raws[0].evidence[0].locator == "aaaa1111"
    assert "migrate vector store to sidecar" in raws[0].evidence[0].text  # verbatim


async def test_one_keyword_needs_three_body_lines() -> None:
    # A single keyword hit ("adopt") with only a 2-line body must NOT qualify.
    thin = (
        "commit ccccdddd\nauthor-date 1700000200\nsubject adopt a new config layout\n"
        "body one line only.\nfiles pkg/config.py\n==END==\n"
    )
    assert await CommitMessagesSource().mine(_ctx(git_log_text=thin)) == ()
    # The same single keyword WITH a 3+ line body qualifies.
    fat = (
        "commit eeeeffff\nauthor-date 1700000300\nsubject adopt a new config layout\n"
        "body line one.\nline two.\nline three.\nfiles pkg/config.py\n==END==\n"
    )
    raws = await CommitMessagesSource().mine(_ctx(git_log_text=fat))
    assert len(raws) == 1
    assert raws[0].evidence[0].locator == "eeeeffff"


async def test_commit_messages_empty_log_yields_nothing() -> None:
    assert await CommitMessagesSource().mine(_ctx(git_log_text="")) == ()


# ── changelog ──────────────────────────────────────────────────────────────


async def test_changelog_entries_keyword_gated(tmp_path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## 1.2.0\n\nWe migrate the vector store to a sidecar and replace blobs.\n\n"
        "## 1.1.0\n\nFixed a typo in the README.\n"
    )
    raws = await ChangelogSource().mine(_ctx(project_root=tmp_path))
    assert len(raws) == 1  # only the keyword-bearing entry
    assert raws[0].status == "proposed" and raws[0].confidence == 0.70
    assert raws[0].evidence[0].locator == "CHANGELOG.md#1.2.0"
    assert "migrate the vector store" in raws[0].evidence[0].text


async def test_changelog_absent_yields_nothing(tmp_path) -> None:
    assert await ChangelogSource().mine(_ctx(project_root=tmp_path)) == ()


# ── docs_prose ─────────────────────────────────────────────────────────────


async def test_docs_prose_bounded_by_max_files_and_size(tmp_path, caplog) -> None:
    # 12 candidate files under docs/, max_files=10 → exactly 10 read; one oversize
    # file skipped; a drop count logged.
    docs = tmp_path / "docs"
    docs.mkdir()
    keyword_para = "We decided to migrate the store and replace the blobs here.\n"
    for i in range(12):
        (docs / f"note_{i:02d}.md").write_text(f"# Doc {i}\n\n{keyword_para}\n")
    # Make one of the first-10 files oversize so it is skipped for size.
    oversize = docs / "note_00.md"
    oversize.write_text("# Big\n\n" + ("migrate " * 40000))  # >50 KB
    cfg = _cfg(docs_prose={"max_files": 10, "max_kb_per_file": 50})  # type: ignore[dict-item]
    source = DocsProseSource()
    ctx = CaptureContext(project_root=tmp_path, trees=(), config=cfg)
    import logging

    with caplog.at_level(logging.INFO):
        raws = await source.mine(ctx)
    # 10 files considered, 1 oversize skipped → 9 mined.
    assert len(raws) == 9
    assert all(r.confidence == 0.60 and r.status == "proposed" for r in raws)
    # a drop count is logged (files beyond max_files + the oversize skip)
    assert any("docs_prose" in rec.getMessage() for rec in caplog.records)


async def test_docs_prose_keyword_gate_drops_non_decision_paragraphs(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "# Project\n\nJust a friendly greeting paragraph.\n\n"
        "We migrate to a new store and replace the old one.\n"
    )
    raws = await DocsProseSource().mine(_ctx(project_root=tmp_path))
    assert len(raws) == 1
    assert "migrate to a new store" in raws[0].evidence[0].text


# ── _git.read_git_log seam ─────────────────────────────────────────────────


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


@pytest.mark.skipif(not _git_available(), reason="git not installed")
def test_read_git_log_round_trips_real_repo(tmp_path) -> None:
    def _git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            capture_output=True,
            check=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@x",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@x",
                "PATH": _path_env(),
            },
        )

    _git("init")
    (tmp_path / "a.py").write_text("a = 1\n")
    _git("add", "a.py")
    _git("commit", "-m", "migrate to sidecar store")
    (tmp_path / "b.py").write_text("b = 2\n")
    _git("add", "b.py")
    _git("commit", "-m", "replace blobs with rows")

    log = read_git_log(tmp_path, max_commits=10, timeout_seconds=5.0)
    # Framed round-trip: both subjects present, per-commit files under a `files`
    # line, one `==END==` terminator per commit.
    assert "migrate to sidecar store" in log
    assert "replace blobs with rows" in log
    assert "files a.py" in log
    assert "files b.py" in log
    assert log.count("==END==") == 2


def test_read_git_log_no_repo_returns_empty(tmp_path) -> None:
    assert read_git_log(tmp_path, max_commits=10, timeout_seconds=5.0) == ""


# Exactly what ``git log --name-only`` emits for a commit built with three ``-m``
# flags (subject + two body paragraphs). git separates the paragraphs with a
# blank line AND separates the body from the file list with a blank run — so a
# naive "split on the first blank" drops the second paragraph into the files.
_MULTI_PARAGRAPH_STDOUT = (
    "commit aaaa1111\n"
    "author-date 1700000000\n"
    "subject migrate the vector store\n"
    "body First we replace the blobs.\n"
    "\n"
    "Then we remove the legacy path.\n"
    "\n"
    "\n"
    "a.py\n"
)


def test_normalize_log_keeps_multiparagraph_body_out_of_files() -> None:
    # Regression: the LAST blank run — not the first — is the body/files
    # separator. Both body paragraphs must survive; no body word may leak into
    # the `files` line (which must carry only the real path).
    framed = _normalize_log(_MULTI_PARAGRAPH_STDOUT)
    assert "body First we replace the blobs." in framed
    assert "Then we remove the legacy path." in framed
    assert "files a.py\n" in framed
    files_line = next(ln for ln in framed.splitlines() if ln.startswith("files "))
    assert files_line == "files a.py"  # no body words leaked in


async def test_multiparagraph_commit_body_survives_in_evidence(tmp_path) -> None:
    # End-to-end through the parser: the framed multi-paragraph record must mine
    # with its FULL body in the evidence text and only the real path in
    # affected_files — no body word masquerading as a touched file.
    (tmp_path / "a.py").write_text("a = 1\n")
    log = _normalize_log(_MULTI_PARAGRAPH_STDOUT)
    raws = await CommitMessagesSource().mine(_ctx(project_root=tmp_path, git_log_text=log))
    assert len(raws) == 1
    text = raws[0].evidence[0].text
    assert "First we replace the blobs." in text
    assert "Then we remove the legacy path." in text  # second paragraph survived
    assert raws[0].affected_files == ("a.py",)  # no body words became files


def _path_env() -> str:
    import os

    return os.environ.get("PATH", "")


# ── changelog / docs_prose × effective excludes (spec 7.8, AC-21) ────────────

_QUALIFYING = "We migrate the vector store to a sidecar and replace blobs.\n"


def _excluded_ctx(tmp_path: Path, excluded: ProjectExcludes) -> CaptureContext:
    return CaptureContext(project_root=tmp_path, trees=(), config=_cfg(), excluded=excluded)


async def test_changelog_skips_excluded_docs_dir(tmp_path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n## 1.2.0\n\n{_QUALIFYING}")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CHANGELOG.md").write_text(f"# Changelog\n\n## 9.9.9\n\n{_QUALIFYING}")
    excluded = ProjectExcludes(names=frozenset({"docs"}), anchored=frozenset())
    raws = await ChangelogSource().mine(_excluded_ctx(tmp_path, excluded))
    # bare "docs" silences docs/CHANGELOG.md; the root changelog still mines.
    assert [r.evidence[0].locator for r in raws] == ["CHANGELOG.md#1.2.0"]


async def test_changelog_anchored_entry_leaves_candidates_intact(tmp_path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n## 1.2.0\n\n{_QUALIFYING}")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CHANGELOG.md").write_text(f"# Changelog\n\n## 9.9.9\n\n{_QUALIFYING}")
    excluded = ProjectExcludes(names=frozenset(), anchored=frozenset({"docs/generated"}))
    raws = await ChangelogSource().mine(_excluded_ctx(tmp_path, excluded))
    assert len(raws) == 2  # neither candidate's parent dir matches the anchor


async def test_changelog_default_excluded_is_identity(tmp_path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n## 1.2.0\n\n{_QUALIFYING}")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CHANGELOG.md").write_text(f"# Changelog\n\n## 9.9.9\n\n{_QUALIFYING}")
    raws = await ChangelogSource().mine(_ctx(project_root=tmp_path))
    assert len(raws) == 2  # no excluded kwarg → byte-identical to today


async def test_docs_prose_skips_excluded_docs_glob(tmp_path) -> None:
    (tmp_path / "README.md").write_text(f"# Project\n\n{_QUALIFYING}")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text(f"# Design\n\n{_QUALIFYING}")
    excluded = ProjectExcludes(names=frozenset({"docs"}), anchored=frozenset())
    raws = await DocsProseSource().mine(_excluded_ctx(tmp_path, excluded))
    # bare "docs" silences docs/*.md; the root README.md still mines.
    assert len(raws) == 1
    assert raws[0].evidence[0].locator.startswith("README.md#")


async def test_docs_prose_anchored_entry_leaves_candidates_intact(tmp_path) -> None:
    (tmp_path / "README.md").write_text(f"# Project\n\n{_QUALIFYING}")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text(f"# Design\n\n{_QUALIFYING}")
    excluded = ProjectExcludes(names=frozenset(), anchored=frozenset({"docs/generated"}))
    raws = await DocsProseSource().mine(_excluded_ctx(tmp_path, excluded))
    assert len(raws) == 2


async def test_docs_prose_default_excluded_is_identity(tmp_path) -> None:
    (tmp_path / "README.md").write_text(f"# Project\n\n{_QUALIFYING}")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text(f"# Design\n\n{_QUALIFYING}")
    raws = await DocsProseSource().mine(_ctx(project_root=tmp_path))
    assert len(raws) == 2  # no excluded kwarg → byte-identical to today (AC-21)
