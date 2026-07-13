"""Tests for pydocs_mcp.project_toml — the [tool.pydocs-mcp] schema owner.

Pins AC-1..AC-5 of the per-project directory-exclusion design
(docs/superpowers/specs/2026-07-13-toml-exclude-dirs-design.md §11):
entry normalization order (separators -> trailing slash -> classify),
fail-loud rejection of escaping entries, the silent-empty vs loud-warning
vs raise error posture of the loader, and byte-wise case-sensitive
matching for both entry kinds.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.project_toml import (
    EMPTY_PROJECT_EXCLUDES,
    ProjectExcludeConfigError,
    ProjectExcludes,
    load_project_excludes,
    split_exclude_entries,
)

# -- AC-1: split_exclude_entries classification + normalization order ----


def test_split_bare_name_goes_to_names():
    names, anchored = split_exclude_entries(["fixtures"])
    assert names == frozenset({"fixtures"})
    assert anchored == frozenset()


def test_split_multi_segment_goes_to_anchored():
    names, anchored = split_exclude_entries(["docs/generated"])
    assert names == frozenset()
    assert anchored == frozenset({"docs/generated"})


def test_split_trailing_slash_on_anchored_is_stripped():
    names, anchored = split_exclude_entries(["docs/generated/"])
    assert names == frozenset()
    assert anchored == frozenset({"docs/generated"})


def test_split_trailing_slash_single_segment_is_bare_name():
    """Strip-before-classify (spec §4): the trailing separator is removed
    BEFORE the '/' classification runs, so "fixtures/" can never be
    silently anchored to the walk root."""
    names, anchored = split_exclude_entries(["fixtures/"])
    assert names == frozenset({"fixtures"})
    assert anchored == frozenset()


def test_split_trailing_backslash_single_segment_is_bare_name():
    """Windows-authored "fixtures\\" normalizes to "fixtures/" first, then
    strips — same bare-name outcome as the POSIX spelling."""
    names, anchored = split_exclude_entries(["fixtures\\"])
    assert names == frozenset({"fixtures"})
    assert anchored == frozenset()


def test_split_backslash_separator_becomes_anchored_posix():
    names, anchored = split_exclude_entries(["a\\b"])
    assert names == frozenset()
    assert anchored == frozenset({"a/b"})


# -- AC-2: split_exclude_entries rejections (message carries the value) --


@pytest.mark.parametrize(
    "bad",
    [
        "/tmp/x",  # absolute — escapes the walk root
        "a/../b",  # .. segment — escapes the walk root
        "a//b",  # empty segment — a dead entry that can never match
        "..",
        ".",
        "",
    ],
)
def test_split_rejects_escaping_or_empty_entries(bad):
    with pytest.raises(ProjectExcludeConfigError) as excinfo:
        split_exclude_entries([bad])
    # CLAUDE.md error convention: the offending value rides in the message.
    assert repr(bad) in str(excinfo.value)


def test_split_rejects_non_string_entry():
    with pytest.raises(ProjectExcludeConfigError) as excinfo:
        split_exclude_entries(["ok", 42])  # type: ignore[list-item]
    assert "42" in str(excinfo.value)


def test_config_error_is_pydocs_mcp_error_and_value_error():
    """The exceptions.py contract: PydocsMCPError lineage for the
    catch-any handle, ValueError lineage for stdlib isinstance checks."""
    assert issubclass(ProjectExcludeConfigError, PydocsMCPError)
    assert issubclass(ProjectExcludeConfigError, ValueError)


# -- AC-3: loader is silently empty for the three normal-absence cases ---


def test_load_missing_pyproject_is_silently_empty(tmp_path, caplog):
    with caplog.at_level("WARNING", logger="pydocs_mcp.project_toml"):
        result = load_project_excludes(tmp_path)
    assert result == EMPTY_PROJECT_EXCLUDES
    assert caplog.records == []


def test_load_no_tool_table_is_silently_empty(tmp_path, caplog):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    with caplog.at_level("WARNING", logger="pydocs_mcp.project_toml"):
        result = load_project_excludes(tmp_path)
    assert result == EMPTY_PROJECT_EXCLUDES
    assert caplog.records == []


def test_load_table_without_key_is_silently_empty(tmp_path, caplog):
    (tmp_path / "pyproject.toml").write_text("[tool.pydocs-mcp]\nother = 1\n")
    with caplog.at_level("WARNING", logger="pydocs_mcp.project_toml"):
        result = load_project_excludes(tmp_path)
    assert result == EMPTY_PROJECT_EXCLUDES
    assert caplog.records == []


def test_empty_exclude_list_is_empty_and_silent(tmp_path, caplog):
    """An explicit-but-empty list is a valid declaration of "no excludes" —
    both surfaces treat it exactly like an absent key."""
    assert split_exclude_entries([]) == (frozenset(), frozenset())
    (tmp_path / "pyproject.toml").write_text("[tool.pydocs-mcp]\nexclude_dirs = []\n")
    with caplog.at_level("WARNING", logger="pydocs_mcp.project_toml"):
        result = load_project_excludes(tmp_path)
    assert result == EMPTY_PROJECT_EXCLUDES
    assert caplog.records == []


# -- AC-4: unparseable TOML warns loudly; wrong-typed key raises ---------


def test_load_unparseable_toml_warns_and_returns_empty(tmp_path, caplog):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.pydocs-mcp\nexclude_dirs = [")  # broken TOML
    with caplog.at_level("WARNING", logger="pydocs_mcp.project_toml"):
        result = load_project_excludes(tmp_path)
    assert result == EMPTY_PROJECT_EXCLUDES
    assert str(pyproject) in caplog.text
    assert "NOT applied" in caplog.text


def test_load_truncated_utf8_warns_and_returns_empty(tmp_path, caplog):
    """A half-saved file mid --watch can cut a multi-byte UTF-8 sequence:
    tomllib decodes BEFORE parsing and lets UnicodeDecodeError escape, so
    the loader must catch it alongside TOMLDecodeError (spec §8 row 2)."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes(b'[tool.pydocs-mcp]\nexclude_dirs = ["caf\xc3')
    with caplog.at_level("WARNING", logger="pydocs_mcp.project_toml"):
        result = load_project_excludes(tmp_path)
    assert result == EMPTY_PROJECT_EXCLUDES
    assert str(pyproject) in caplog.text
    assert "NOT applied" in caplog.text


def test_load_non_table_tool_pydocs_mcp_raises(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool]\npydocs-mcp = 5\n")
    with pytest.raises(ProjectExcludeConfigError) as excinfo:
        load_project_excludes(tmp_path)
    assert "must be a table" in str(excinfo.value)


def test_load_string_valued_exclude_dirs_raises(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.pydocs-mcp]\nexclude_dirs = "docs"\n')
    with pytest.raises(ProjectExcludeConfigError) as excinfo:
        load_project_excludes(tmp_path)
    assert "list of strings" in str(excinfo.value)


def test_load_int_element_raises_pydocs_mcp_error(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pydocs-mcp]\nexclude_dirs = [1]\n")
    # PydocsMCPError is the catch-any handle — the concrete class must sit
    # under it so index-run boundaries can catch by lineage.
    with pytest.raises(PydocsMCPError):
        load_project_excludes(tmp_path)


def test_load_happy_path_classifies_both_kinds(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pydocs-mcp]\nexclude_dirs = ["docs/generated", "fixtures"]\n'
    )
    result = load_project_excludes(tmp_path)
    assert result.names == frozenset({"fixtures"})
    assert result.anchored == frozenset({"docs/generated"})


# -- AC-5: ProjectExcludes.matches semantics ------------------------------


def _excludes(names=(), anchored=()):
    return ProjectExcludes(frozenset(names), frozenset(anchored))


def test_anchored_matches_itself_and_subtree():
    ex = _excludes(anchored=["docs/generated"])
    assert ex.matches("docs/generated")
    assert ex.matches("docs/generated/deep/file.md")


def test_anchored_does_not_match_leaf_name_sibling():
    """The worked-example discriminator (spec §4): tools/generated survives
    an anchored docs/generated entry."""
    ex = _excludes(anchored=["docs/generated"])
    assert not ex.matches("tools/generated/x.py")


def test_anchored_does_not_match_prefix_extension():
    ex = _excludes(anchored=["docs/generated"])
    assert not ex.matches("docs/generated2/x")


def test_bare_name_matches_any_component_any_depth():
    ex = _excludes(names=["fixtures"])
    assert ex.matches("fixtures/a.py")
    assert ex.matches("src/pkg/fixtures/a.py")


def test_matching_is_byte_wise_case_sensitive_both_kinds():
    """Spec §4: byte-wise regardless of platform case-folding — an on-disk
    Docs/Generated never matches docs/generated."""
    ex = _excludes(names=["fixtures"], anchored=["docs/generated"])
    assert not ex.matches("Docs/Generated/x.md")
    assert not ex.matches("src/Fixtures/a.py")


# -- merge_excludes + exclusion_fingerprint shared contracts --------------


def test_merge_is_pure_union_and_names_include_floor():
    from pydocs_mcp.project_toml import merge_excludes

    floor = frozenset({".git", ".venv"})
    loaded = _excludes(names=["fixtures"], anchored=["docs/generated"])
    merged = merge_excludes(floor, ["testdata", "a/b"], loaded)
    assert merged.names == frozenset({".git", ".venv", "fixtures", "testdata"})
    assert merged.anchored == frozenset({"docs/generated", "a/b"})


def test_fingerprint_none_iff_bare_floor():
    from pydocs_mcp.project_toml import exclusion_fingerprint

    floor = frozenset({".git", ".venv"})
    assert exclusion_fingerprint(_excludes(names=floor), floor) is None
    # Any anchored entry, or any name beyond the floor, folds.
    assert exclusion_fingerprint(_excludes(names=floor | {"x"}), floor) is not None
    assert exclusion_fingerprint(_excludes(names=floor, anchored=["a/b"]), floor) is not None


def test_fingerprint_is_deterministic_and_kind_tagged():
    """Sorted-within-kind, names before anchored, NUL-joined — and the
    kind tag keeps a name and an identical anchored string distinct."""
    from pydocs_mcp.project_toml import exclusion_fingerprint

    floor = frozenset()
    fp = exclusion_fingerprint(_excludes(names=["b", "a"], anchored=["c/d"]), floor)
    assert fp == "n:a\x00n:b\x00a:c/d"
    # Kind tag: the IDENTICAL string in the two sets must not collide.
    # Direct ProjectExcludes construction bypasses split_exclude_entries'
    # classification, so a slash-bearing string is legal in `names` here —
    # "n:c/d" vs "a:c/d" is exactly the spec §9 collision the tag prevents.
    only_name = exclusion_fingerprint(_excludes(names=["c/d"]), floor)
    only_anchored = exclusion_fingerprint(_excludes(anchored=["c/d"]), floor)
    assert only_name == "n:c/d"
    assert only_anchored == "a:c/d"
    assert only_name != only_anchored
