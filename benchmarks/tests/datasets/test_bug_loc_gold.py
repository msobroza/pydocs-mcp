"""Pin the bug-localization gold derivation — pure functions, no loader state.

Both corpora must land on the same notion of "gold file" from two different raw
shapes, so the diff reader, the JSON reader and the shared test-path predicate
are pinned here rather than only through a dataset object.

Hermetic: stdlib only, no ``pydocs_mcp`` import.
"""

from __future__ import annotations

import pytest

from pydocs_eval.datasets._bug_loc_gold import (
    BugLocGoldError,
    is_test_path,
    non_test_paths,
    paths_from_changed_files,
    paths_from_unified_diff,
)


# --- The test-path predicate ---------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_router.py",
        "test/helpers.py",
        "lib/tests/streamlit/caching_test.py",
        "src/pkg/test_module.py",
        "src/pkg/conftest.py",
        "Testing/Suite/run.py",
        "testing/logging/test_reporting.py",
    ],
)
def test_test_scaffolding_is_recognized(path: str) -> None:
    assert is_test_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "src/pkg/mod.py",
        # ``latest`` and ``contest`` merely CONTAIN "test" — segment equality,
        # not substring matching, is what keeps them product code.
        "src/latest/router.py",
        "src/contest/scoring.py",
        # ``protest.py`` starts with "p", not the ``test_`` prefix.
        "src/pkg/protest.py",
        "docs/releases/2.2.1.txt",
        "setup.cfg",
    ],
)
def test_product_code_is_not_mistaken_for_tests(path: str) -> None:
    assert is_test_path(path) is False


def test_non_test_paths_preserves_order_and_deduplicates() -> None:
    paths = ("b.py", "tests/t.py", "a.py", "b.py")
    assert non_test_paths(paths) == ("b.py", "a.py")


# --- Unified-diff reading -------------------------------------------------


def test_a_single_file_modify_yields_one_path() -> None:
    patch = "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-a\n+b\n"
    assert paths_from_unified_diff(patch) == ("pkg/mod.py",)


def test_a_multi_file_patch_yields_paths_in_first_appearance_order() -> None:
    patch = (
        "diff --git a/z.py b/z.py\n--- a/z.py\n+++ b/z.py\n@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-c\n+d\n"
    )
    assert paths_from_unified_diff(patch) == ("z.py", "a.py")


def test_a_delete_uses_the_pre_image_path() -> None:
    # ``+++ /dev/null`` carries no path; the removed file is what changed.
    patch = "--- a/pkg/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n"
    assert paths_from_unified_diff(patch) == ("pkg/gone.py",)


def test_an_add_uses_the_post_image_path() -> None:
    patch = "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1 @@\n+a\n"
    assert paths_from_unified_diff(patch) == ("pkg/new.py",)


def test_a_path_with_spaces_survives_the_image_header() -> None:
    # THE reason the reader parses ``---``/``+++`` rather than ``diff --git``:
    # the git line is ambiguous for a spaced path, the image header is not.
    patch = "--- a/Project Euler/Problem 01/sol2.py\n+++ b/Project Euler/Problem 01/sol2.py\n"
    assert paths_from_unified_diff(patch) == ("Project Euler/Problem 01/sol2.py",)


def test_a_timestamp_suffix_is_stripped() -> None:
    patch = "--- a/pkg/mod.py\t2024-01-01 00:00:00\n+++ b/pkg/mod.py\t2024-01-02 00:00:00\n"
    assert paths_from_unified_diff(patch) == ("pkg/mod.py",)


def test_diff_body_lines_that_look_like_headers_are_not_promoted() -> None:
    # A patch that edits a patch fixture: the nested headers are BODY lines
    # (prefixed by ``+``/``-``/space) and must not become gold paths.
    patch = (
        "--- a/tests/data/sample.patch\n"
        "+++ b/tests/data/sample.patch\n"
        "@@ -1,1 +1,1 @@\n"
        "---- a/decoy.py\n"
        "++++ b/decoy.py\n"
    )
    assert paths_from_unified_diff(patch) == ("tests/data/sample.patch",)


@pytest.mark.parametrize(
    ("patch", "phantom"),
    [
        # An ADDED line whose content starts with ``++ `` is byte-identical to
        # a post-image header; a removed line starting ``-- `` likewise. A line
        # scanner promoted both to gold paths, which inflates n_gt and caps the
        # instance's map@k / gold_recall below 1.0 for a perfect retrieval.
        (
            "--- a/docs/guide.rst\n+++ b/docs/guide.rst\n"
            "@@ -1,1 +1,2 @@\n context\n+++ see the release notes\n",
            "see the release notes",
        ),
        (
            "--- a/CHANGES.rst\n+++ b/CHANGES.rst\n"
            "@@ -1,1 +1,1 @@\n-- deprecated in 2.0\n+++ removed in 3.0\n",
            "removed in 3.0",
        ),
    ],
)
def test_body_text_shaped_like_an_image_header_is_never_a_gold_path(
    patch: str, phantom: str
) -> None:
    paths = paths_from_unified_diff(patch)
    assert phantom not in paths
    assert len(paths) == 1


def test_a_hunkless_rename_yields_both_paths() -> None:
    # git emits ``similarity index 100%`` renames with NO ---/+++ headers at
    # all, so a line scanner dropped BOTH the pre- and the post-fix path.
    patch = (
        "diff --git a/doc/symilar.rst b/doc/additional_tools/symilar/index.rst\n"
        "similarity index 100%\n"
        "rename from doc/symilar.rst\n"
        "rename to doc/additional_tools/symilar/index.rst\n"
    )
    assert set(paths_from_unified_diff(patch)) == {
        "doc/symilar.rst",
        "doc/additional_tools/symilar/index.rst",
    }


def test_an_empty_new_file_still_yields_its_path() -> None:
    # A new EMPTY file carries no hunks and no image headers — the shape a
    # line scanner silently dropped (real: ansible's added ``__init__.py``).
    patch = (
        "diff --git a/lib/ansible/x/__init__.py b/lib/ansible/x/__init__.py\n"
        "new file mode 100644\n"
        "index 00000000000000..e69de29bb2d1d6\n"
    )
    assert paths_from_unified_diff(patch) == ("lib/ansible/x/__init__.py",)


@pytest.mark.parametrize("patch", ["", "no headers here at all\n"])
def test_a_patch_naming_no_file_raises_with_the_offending_value(patch: str) -> None:
    with pytest.raises(BugLocGoldError) as excinfo:
        paths_from_unified_diff(patch)
    assert "unified diff" in str(excinfo.value)


def test_a_malformed_patch_raises_a_gold_error_not_a_parser_error() -> None:
    # The loaders catch BugLocGoldError to count a drop; a raw
    # UnidiffParseError would escape that handler and abort the whole sweep.
    with pytest.raises(BugLocGoldError):
        paths_from_unified_diff("--- a/x.py\n+++ b/x.py\n@@ -1,9 +1,9 @@\n-a\n")


def test_a_c_quoted_path_raises_rather_than_being_mis_parsed() -> None:
    # Decoding git's quoting is out of scope; silently keeping the quotes
    # would produce a gold path that can never match a real source path.
    with pytest.raises(BugLocGoldError) as excinfo:
        paths_from_unified_diff('--- a/x.py\n+++ "b/odd\\tname.py"\n')
    assert "C-quoted" in str(excinfo.value)


# --- changed_files list-literal reading -----------------------------------


def test_the_releases_python_repr_spelling_decodes_to_paths() -> None:
    # THE regression: every row of the pinned lca-bug-loc revision spells
    # changed_files as a PYTHON REPR — single quotes — so a json.loads-only
    # reader raised on 50/50 rows and the dataset minted ZERO tasks under a
    # merely INFO-level drop log. This is the byte string of record
    # ``thealgorithms/python/295/289``.
    assert paths_from_changed_files("['Project Euler/Problem 01/sol2.py']") == (
        "Project Euler/Problem 01/sol2.py",
    )


def test_a_json_encoded_list_still_decodes_to_paths() -> None:
    # Kept accepted so a release that switches to real JSON needs no edit.
    assert paths_from_changed_files('["a.py", "b/c d.py"]') == ("a.py", "b/c d.py")


def test_an_apostrophe_bearing_path_survives_the_python_repr_spelling() -> None:
    assert paths_from_changed_files('["it\'s/a.py"]') == ("it's/a.py",)


@pytest.mark.parametrize(
    "encoded",
    ["[not json", '{"a": 1}', '["a.py", 3]', '["a.py", ""]', "['a.py', 3]"],
)
def test_an_undecodable_changed_files_raises_with_the_offending_value(encoded: str) -> None:
    with pytest.raises(BugLocGoldError) as excinfo:
        paths_from_changed_files(encoded)
    assert encoded in str(excinfo.value)


def test_an_empty_list_decodes_cleanly_and_is_the_loaders_problem() -> None:
    # ``[]`` is well-formed, so this reader returns it unchanged; rejecting a
    # vacuous gold belongs to the loader's one empty-gold guard, not to two
    # readers that would each have to remember.
    assert paths_from_changed_files("[]") == ()


def test_a_non_string_changed_files_names_its_type() -> None:
    with pytest.raises(BugLocGoldError) as excinfo:
        paths_from_changed_files(["a.py"])
    assert "list" in str(excinfo.value)
