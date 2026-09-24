"""``path_in_project_scope``: the project walk's admission policy over one manifest
entry (spec §6.3 step 1, #310) — a branch that is not checked out has no tree to
walk, so its ``ls_tree`` listing is filtered by the same rule instead."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery import (
    ProjectFileDiscoverer,
    path_in_project_scope,
)
from pydocs_mcp.project_toml import ProjectExcludes

_FILES = {
    "pkg/a.py": "a = 1\n",
    "pkg/B.MD": "# b\n",
    "pkg/c.pyc": "x",
    "node_modules/x.py": "x = 1\n",
    "vendored/y.py": "y = 1\n",
    "docs/gen/z.py": "z = 1\n",
    "docs/keep.py": "k = 1\n",
    "big.py": "#" * 64,
}


def _excludes() -> ProjectExcludes:
    return ProjectExcludes(names=frozenset({"vendored"}), anchored=frozenset({"docs/gen"}))


def _scope() -> DiscoveryScopeConfig:
    return DiscoveryScopeConfig(max_file_size_bytes=32)


def test_the_filter_admits_exactly_what_the_walk_finds(tmp_path: Path) -> None:
    for relative, text in _FILES.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(text, encoding="utf-8")
    discoverer = ProjectFileDiscoverer(scope=_scope(), excludes_loader=lambda root: _excludes())
    walked, _root, effective = discoverer.discover(tmp_path)
    admitted = {
        relative
        for relative, text in _FILES.items()
        if path_in_project_scope(relative, len(text.encode()), _scope(), effective)
    }
    assert admitted == {Path(p).relative_to(tmp_path).as_posix() for p in walked}
    assert admitted == {"pkg/a.py", "pkg/B.MD", "docs/keep.py"}


def test_an_oversized_entry_is_named_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    effective = ProjectExcludes(frozenset(), frozenset())
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        assert not path_in_project_scope("big.py", 64, _scope(), effective)
    assert "big.py" in caplog.text and "max_file_size_bytes=32" in caplog.text
