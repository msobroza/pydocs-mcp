"""Gold derivation for the file-level bug-localization corpora — pure functions.

The two ``bug_loc`` loaders start from different raw shapes — SWE-bench Verified
ships a unified ``patch`` diff, Long Code Arena ships a stringified Python list
of ``changed_files`` — and must land on the SAME gold: the repo-relative paths
of the non-test files a fix touches (arXiv:2607.11046 §4.2). Both derivations
plus the shared test-path predicate live here, free of any loader state, so the
parsing is testable without a dataset object and the two corpora cannot drift
into two different notions of "gold file".

The diff reader delegates to ``unidiff.PatchSet`` — the same base dependency
``trajectory/gold_diff.py`` already parses SWE gold patches with, and the same
library the upstream SWE-bench harness uses. WHY not a hand-rolled
``---``/``+++`` scan: git emits file sections that carry NO image header at all
(a hunkless ``similarity index 100%`` rename, an empty new file, a binary
section), and a line scanner drops those gold files silently; conversely an
ADDED line whose content starts with ``++ `` is byte-identical to an image
header, so a scanner promotes diff BODY text to a phantom gold path — which
inflates ``n_gt`` and caps that instance's ``map@k``/``gold_recall`` below 1.0.
``PatchSet`` parses structurally and has neither failure mode.

A row whose gold cannot be derived is never silently coerced: the readers raise
:class:`BugLocGoldError` carrying the offending value, and the loaders turn that
into a counted, logged drop (the no-silent-caps rule).
"""

from __future__ import annotations

import ast
import json
import posixpath

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

_DEV_NULL = "/dev/null"
_IMAGE_PATH_PREFIXES = ("a/", "b/")

# Path segments and basename shapes that mark a file as test scaffolding. The
# paper's gold is the files that must CHANGE to fix the bug; SWE-bench keeps
# its tests in a separate ``test_patch`` by construction, but neither corpus
# guarantees the fix patch itself is test-free.
_TEST_DIR_SEGMENTS = frozenset({"test", "tests", "testing", "_test", "_tests"})
_TEST_BASENAME_PREFIX = "test_"
_TEST_STEM_SUFFIX = "_test"
_TEST_BASENAMES = frozenset({"conftest.py"})


class BugLocGoldError(ValueError):
    """A bug-localization record whose gold file set cannot be derived."""


def is_test_path(path: str) -> bool:
    """True iff ``path`` looks like test scaffolding rather than product code.

    Three signals, any of which is decisive: a ``test``/``tests``-style
    directory segment anywhere in the path, a ``test_*`` basename, or a
    ``*_test.<ext>`` stem. Case-insensitive because real repos ship ``Tests/``.

    KNOWN over-reach, measured and accepted: a few projects SHIP a public
    package under such a segment (``django/test/client.py``,
    ``sympy/testing/runtests.py``), and this predicate strips them. Measured on
    both pinned releases it never fires that way — 0/623 gold paths stripped
    across swe-bench-verified-loc's 500 rows, and on lca-bug-loc the derived
    non-test gold size matches the release's own
    ``changed_files_without_tests_count`` on 50/50 rows. An allow-list of
    shipped testing packages is the fix if a pin bump ever changes that; do NOT
    add one speculatively, because every entry is a per-repo judgement call.

    Example:
        >>> is_test_path("tests/unit/test_router.py")
        True
        >>> is_test_path("src/pkg/latest/router.py")
        False
    """
    parts = [segment.lower() for segment in path.split("/") if segment]
    if not parts:
        return False
    if any(segment in _TEST_DIR_SEGMENTS for segment in parts[:-1]):
        return True
    basename = parts[-1]
    if basename in _TEST_BASENAMES or basename.startswith(_TEST_BASENAME_PREFIX):
        return True
    stem, _dot, _ext = basename.rpartition(".")
    return bool(stem) and stem.endswith(_TEST_STEM_SUFFIX)


def non_test_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """``paths`` minus the test scaffolding, order-preserving and de-duplicated."""
    kept: dict[str, None] = {}
    for path in paths:
        if not is_test_path(path):
            kept.setdefault(path)
    return tuple(kept)


def paths_from_unified_diff(patch: str) -> tuple[str, ...]:
    """Repo-relative paths a unified diff touches, in first-appearance order.

    One file section contributes its POST-image path (the file after the fix),
    plus its PRE-image path when the section is a delete or a rename — the
    ADR 0011 gold rule ``trajectory/gold_diff.modified_files`` already applies
    to SWE gold patches, restated here only because ``bug_loc`` needs a
    deterministic ORDER where that helper returns a ``frozenset``.

    Raises:
        BugLocGoldError: the patch names no file, cannot be parsed as a unified
            diff, or carries git's C-quoted path spelling (``+++ "b/odd\\tname"``),
            which is not decoded — the message carries the offending value.

    Example:
        >>> paths_from_unified_diff("--- a/pkg/mod.py\\n+++ b/pkg/mod.py\\n")
        ('pkg/mod.py',)
    """
    touched: dict[str, None] = {}
    for section in _parse_patch(patch):
        for path in _section_paths(section):
            touched.setdefault(path)
    if not touched:
        raise BugLocGoldError(
            f"no file section found in patch {patch[:120]!r}, expected a unified "
            "diff naming at least one file"
        )
    return tuple(touched)


def _parse_patch(patch: str) -> PatchSet:
    """``PatchSet(patch)``, with a parse failure restated as a gold error."""
    try:
        return PatchSet(patch or "")
    except UnidiffParseError as exc:
        raise BugLocGoldError(
            f"patch {patch[:120]!r} is not a parseable unified diff: {exc}"
        ) from exc


def _section_paths(section: object) -> tuple[str, ...]:
    """Gold paths one diff file section contributes, post-image first.

    A modify/add contributes its target; a delete contributes its source; a
    rename contributes BOTH (the fix moved code the localizer must name, and
    the pre-fix path is where a reader of the bug report would look).
    """
    source = str(getattr(section, "source_file", _DEV_NULL))
    target = str(getattr(section, "target_file", _DEV_NULL))
    is_rename = bool(getattr(section, "is_rename", False))
    paths = [target] if target != _DEV_NULL else []
    if source != _DEV_NULL and (is_rename or target == _DEV_NULL):
        paths.append(source)
    return tuple(_strip_image_prefix(path) for path in paths)


def _strip_image_prefix(raw: str) -> str:
    """One diff image path minus its git ``a/`` / ``b/`` prefix."""
    if raw.startswith('"'):
        raise BugLocGoldError(
            f"diff header path {raw!r} uses git's C-quoted spelling, which this "
            "reader does not decode; expected a plain ``a/<path>`` / ``b/<path>``"
        )
    for image_prefix in _IMAGE_PATH_PREFIXES:
        if raw.startswith(image_prefix):
            return posixpath.normpath(raw[len(image_prefix) :])
    return posixpath.normpath(raw)


def paths_from_changed_files(encoded: object) -> tuple[str, ...]:
    """Decode Long Code Arena's ``changed_files`` — a list stringified inside a
    string column.

    The column's dtype is ``string``, not ``list``, and the release spells the
    list as a PYTHON REPR, not JSON: the value arrives as
    ``"['Project Euler/Problem 01/sol2.py']"`` — single quotes, verified on all
    50 rows of the pinned ``lca-bug-loc`` revision, where ``json.loads`` fails
    on 50/50 and ``ast.literal_eval`` succeeds on 50/50. Both spellings are
    accepted so a future release that switches to real JSON keeps working.
    Paths are already repo-relative and may contain spaces, so nothing here
    splits on whitespace.

    Raises:
        BugLocGoldError: the value is not a string, decodes under neither
            spelling, or is not a list of non-empty strings — the message
            carries the offending value.

    Example:
        >>> paths_from_changed_files("['src/a.py', 'src/b.py']")
        ('src/a.py', 'src/b.py')
        >>> paths_from_changed_files('["src/a.py"]')
        ('src/a.py',)
    """
    if not isinstance(encoded, str):
        raise BugLocGoldError(
            f"changed_files must be a stringified list, got {type(encoded).__name__} {encoded!r}"
        )
    decoded = _decode_list_literal(encoded)
    if not isinstance(decoded, list) or not all(isinstance(path, str) and path for path in decoded):
        raise BugLocGoldError(
            f"changed_files {encoded!r} decoded to {decoded!r}, expected a list of "
            "non-empty repo-relative path strings"
        )
    return tuple(decoded)


def _decode_list_literal(encoded: str) -> object:
    """Parse a Python-repr list, falling back to JSON; both are value-only.

    ``ast.literal_eval`` evaluates literals ONLY (no names, no calls), so it is
    safe on untrusted dataset text in a way ``eval`` would not be.
    """
    try:
        return ast.literal_eval(encoded)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        pass
    try:
        return json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise BugLocGoldError(
            f"changed_files {encoded!r} decodes as neither a Python list literal nor "
            f"JSON ({exc}); expected a list of repo-relative paths"
        ) from exc


__all__ = [
    "BugLocGoldError",
    "is_test_path",
    "non_test_paths",
    "paths_from_changed_files",
    "paths_from_unified_diff",
]
