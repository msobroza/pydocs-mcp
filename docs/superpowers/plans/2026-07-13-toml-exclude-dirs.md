# Per-Project Directory Exclusion (TOML + YAML) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a project exclude additional directories from pydocs-mcp indexation via `[tool.pydocs-mcp] exclude_dirs` in its own pyproject.toml plus additive YAML `extraction.discovery.{project,dependency}.exclude_dirs`, layered over the non-removable hardcoded floor.

**Architecture:** A new `project_toml.py` module owns the TOML schema, entry validation, merge, and fingerprint helpers; discoverers/member-extractor/decision-mining/manifest-walk consume one per-run effective set (injected loader strategy, state-carried for the content-hash fold); the watcher derives best-effort ignore globs. Spec: docs/superpowers/specs/2026-07-13-toml-exclude-dirs-design.md (D1-D10, AC-1..AC-26).

**Tech Stack:** Python 3.11+, pydantic v2, stdlib tomllib, pytest, watchdog; no Rust changes.

---

### Task 1: `project_toml.py` — schema owner (spec §7.1; AC-1..AC-5)

Create the new module `python/pydocs_mcp/project_toml.py` — a peer of `deps.py` / `multirepo.py` and the single owner of the `[tool.pydocs-mcp]` pyproject schema. It ships the `ProjectExcludes` value object, the shared normalizer/validator `split_exclude_entries` (decision D5 — both the TOML loader and the YAML field validator funnel through it), the loader `load_project_excludes`, the pure union helper `merge_excludes`, the content-hash helper `exclusion_fingerprint`, and the fail-loud `ProjectExcludeConfigError`.

Constraints that shape the code (do not deviate):

- The module imports ONLY stdlib + `pydocs_mcp.exceptions` — never anything from `extraction/` — so `extraction/config.py` (Task 2) can import it without a cycle. The root exception it subclasses is `PydocsMCPError` (`python/pydocs_mcp/exceptions.py:15`); per that module's rule (`exceptions.py:7-9`, "concrete exception classes live in their respective modules"), `ProjectExcludeConfigError` is defined here, and per the multiple-inheritance precedent documented at `exceptions.py:17-24` it also inherits `ValueError`.
- Normalization order (spec §4, load-bearing): backslash → `/` FIRST, strip trailing `/` SECOND, classify by remaining `/` LAST. Consequence pinned by AC-1: `"fixtures/"` and `"fixtures\"` are BARE names, never anchored.
- Matching (spec §4): byte-wise case-sensitive; `relpath` is a walk-root-relative POSIX DIRECTORY path; anchored entries match the directory itself and anything beneath; names match any path component.
- Error posture (spec §8): missing file / missing table / missing key → empty, silent. Unparseable TOML → `log.warning` naming the file + "NOT applied", return empty. Declared-but-wrong-typed `exclude_dirs` → raise `ProjectExcludeConfigError`.
- `exclusion_fingerprint` returns `None` iff `excludes.names == floor and not excludes.anchored` (the conditional no-fold case, spec §9.2); otherwise `"\0".join` of kind-tagged sorted entries — all `"n:<name>"` sorted, then all `"a:<path>"` sorted.

**Files:**

- Create: `python/pydocs_mcp/project_toml.py`
- Test: `tests/test_project_toml.py` (new)

- [ ] **Step 1: Write the failing test file `tests/test_project_toml.py` (AC-1 and AC-2 — the normalizer)**

Create `tests/test_project_toml.py` with exactly this content (module docstring + AC-1/AC-2 tests first; the remaining ACs are appended in Steps 4 and 7 so each red/green cycle stays small):

```python
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
```

- [ ] **Step 2: Run the test — expect FAIL on import**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
pytest tests/test_project_toml.py -v
```

Expected output: collection error, `ModuleNotFoundError: No module named 'pydocs_mcp.project_toml'`.

- [ ] **Step 3: Write the complete module `python/pydocs_mcp/project_toml.py`**

Create the file with exactly this content (the FULL module — later tasks in this plan never edit it again):

```python
"""Owner of the indexed project's ``[tool.pydocs-mcp]`` pyproject schema.

A peer of :mod:`pydocs_mcp.deps` / :mod:`pydocs_mcp.multirepo`. Reads
per-project directory exclusions (``[tool.pydocs-mcp] exclude_dirs``) and
ships the shared entry normalizer used by BOTH configuration surfaces —
the TOML loader here and the YAML ``exclude_dirs`` field validator on
:class:`~pydocs_mcp.extraction.config.DiscoveryScopeConfig` funnel through
:func:`split_exclude_entries`, so the two surfaces can never drift
(design decision D5).

Import discipline: stdlib + :mod:`pydocs_mcp.exceptions` ONLY. In
particular nothing from ``extraction/`` — ``extraction/config.py``
imports this module, and any back-import would cycle.

Usage::

    excludes = load_project_excludes(Path("/repo"))
    excludes.matches("docs/generated")   # True if excluded
"""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.exceptions import PydocsMCPError

logger = logging.getLogger(__name__)

_EXCLUDE_KEY = "exclude_dirs"
_TOOL_TABLE = "pydocs-mcp"


class ProjectExcludeConfigError(PydocsMCPError, ValueError):
    """A declared exclude list is malformed (wrong type, escaping entry).

    Raised — never swallowed — because a declared-but-broken config is
    user intent gone wrong (spec §8): silently ignoring it would index
    everything the user asked to exclude. ValueError lineage preserves
    stdlib isinstance checks, per the exceptions.py precedent.
    """


@dataclass(frozen=True, slots=True)
class ProjectExcludes:
    """User-declared exclusion entries, pre-classified (spec §4).

    ``names`` are bare directory names matched against ANY path component
    at any depth; ``anchored`` are normalized POSIX relpaths matched as
    walk-root-anchored subtrees.
    """

    names: frozenset[str]
    anchored: frozenset[str]

    def matches(self, relpath: str) -> bool:
        """True iff ``relpath`` falls under any entry.

        ``relpath`` is walk-root-relative with POSIX separators, and a
        DIRECTORY path per the directories-only rule of spec §4 — callers
        pass a file's parent directory, never the file path. Anchored
        entries match the directory itself and anything beneath it; names
        match any path component. Byte-wise case-sensitive on every
        platform (spec §4) — no casefolding, ever.
        """
        path = relpath.replace("\\", "/")
        if any(part in self.names for part in path.split("/")):
            return True
        return any(
            path == entry or path.startswith(entry + "/") for entry in self.anchored
        )


EMPTY_PROJECT_EXCLUDES = ProjectExcludes(frozenset(), frozenset())
"""The no-excludes value — default for every optional excludes parameter."""


def split_exclude_entries(
    entries: Sequence[str],
) -> tuple[frozenset[str], frozenset[str]]:
    """Normalize + classify exclude entries into ``(names, anchored)``.

    The shared validator for BOTH surfaces (decision D5). Normalization
    order is load-bearing (spec §4): backslashes -> ``/`` FIRST, trailing
    ``/`` stripped SECOND, classification by remaining ``/`` LAST — so
    ``"fixtures/"`` and ``"fixtures\\"`` are bare names, never anchored.

    Raises :class:`ProjectExcludeConfigError` for non-string, absolute,
    ``..``/``.``-segment, or empty entries — each escapes the walk root
    or is meaningless, and the message carries the offending value.

    Example::

        split_exclude_entries(["fixtures", "docs/generated/"])
        # (frozenset({"fixtures"}), frozenset({"docs/generated"}))
    """
    names: set[str] = set()
    anchored: set[str] = set()
    for raw in entries:
        if not isinstance(raw, str):
            raise ProjectExcludeConfigError(
                f"exclude_dirs entries must be strings; got {raw!r} "
                f"({type(raw).__name__}) — expected a directory name like "
                f'"fixtures" or an anchored path like "docs/generated"'
            )
        entry = raw.replace("\\", "/")
        if entry.startswith("/"):
            raise ProjectExcludeConfigError(
                f"exclude_dirs entry {raw!r} is absolute; entries must be "
                f"walk-root-relative directory names or paths"
            )
        entry = entry.rstrip("/")
        if not entry:
            raise ProjectExcludeConfigError(
                f"exclude_dirs entry {raw!r} is empty after normalization; "
                f"entries must name a directory"
            )
        if any(segment in {".", ".."} for segment in entry.split("/")):
            raise ProjectExcludeConfigError(
                f"exclude_dirs entry {raw!r} contains a '.' or '..' segment; "
                f"entries must stay inside the walk root"
            )
        if "/" in entry:
            anchored.add(entry)
        else:
            names.add(entry)
    return frozenset(names), frozenset(anchored)


def load_project_excludes(project_root: Path) -> ProjectExcludes:
    """Read ``[tool.pydocs-mcp] exclude_dirs`` from the project's pyproject.

    Error posture (spec §8): missing file / missing table / missing key ->
    empty and SILENT (the normal case for virtually every project);
    unparseable TOML -> loud warning, empty result (the floor still
    protects the dangerous directories, and an index run must not die on
    a half-saved TOML mid ``--watch``); declared-but-wrong-typed
    ``exclude_dirs`` -> :class:`ProjectExcludeConfigError`.

    Example::

        load_project_excludes(Path("/repo")).matches("fixtures")
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return EMPTY_PROJECT_EXCLUDES
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError:
        logger.warning(
            "unparseable TOML in %s: project excludes NOT applied "
            "(hardcoded floor + YAML excludes remain in effect)",
            pyproject,
        )
        return EMPTY_PROJECT_EXCLUDES
    tool = data.get("tool")
    table = tool.get(_TOOL_TABLE) if isinstance(tool, dict) else None
    if table is None:
        return EMPTY_PROJECT_EXCLUDES
    if not isinstance(table, dict):
        raise ProjectExcludeConfigError(
            f"[tool.{_TOOL_TABLE}] in {pyproject} must be a table; got {table!r}"
        )
    entries = table.get(_EXCLUDE_KEY)
    if entries is None:
        return EMPTY_PROJECT_EXCLUDES
    if not isinstance(entries, list):
        raise ProjectExcludeConfigError(
            f"[tool.{_TOOL_TABLE}] {_EXCLUDE_KEY} in {pyproject} must be a "
            f"list of strings; got {entries!r}"
        )
    names, anchored = split_exclude_entries(entries)
    return ProjectExcludes(names=names, anchored=anchored)


def merge_excludes(
    floor: frozenset[str],
    scope_entries: Sequence[str],
    loaded: ProjectExcludes,
) -> ProjectExcludes:
    """Pure union of the three sources (spec §3.3) — nothing subtracts.

    ``floor`` is the hardcoded ``_EXCLUDED_DIRS`` set (callers pass it in;
    this module never imports extraction/), ``scope_entries`` the raw YAML
    list for the walk's scope, ``loaded`` the pre-classified TOML result.
    The returned ``names`` always includes the floor.

    Example::

        merge_excludes(_EXCLUDED_DIRS, cfg.exclude_dirs, loader(root))
    """
    scope_names, scope_anchored = split_exclude_entries(scope_entries)
    return ProjectExcludes(
        names=floor | scope_names | loaded.names,
        anchored=scope_anchored | loaded.anchored,
    )


def exclusion_fingerprint(
    excludes: ProjectExcludes, floor: frozenset[str]
) -> str | None:
    """Normalized fingerprint of the effective set for the content-hash fold.

    ``None`` iff the effective set equals the bare floor (no user excludes,
    no anchored entries) — the conditional no-fold case of spec §9.2 that
    keeps every pre-upgrade stored hash valid. Otherwise a deterministic
    string: all bare names sorted and tagged ``n:``, then all anchored
    paths sorted and tagged ``a:``, NUL-joined — the kind tag prevents the
    same string appearing in both sets from colliding.

    Example::

        exclusion_fingerprint(effective, _EXCLUDED_DIRS)  # None or "a:...\\x00n:..."
    """
    if excludes.names == floor and not excludes.anchored:
        return None
    tagged = [f"n:{name}" for name in sorted(excludes.names)]
    tagged += [f"a:{path}" for path in sorted(excludes.anchored)]
    return "\0".join(tagged)
```

- [ ] **Step 4: Run the AC-1/AC-2 tests — expect PASS**

```bash
pytest tests/test_project_toml.py -v
```

Expected: all 13 tests pass (`test_split_bare_name_goes_to_names` ... `test_config_error_is_pydocs_mcp_error_and_value_error`).

- [ ] **Step 5: Append the AC-3/AC-4 loader tests (red)**

Append to `tests/test_project_toml.py`:

```python
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


# -- AC-4: unparseable TOML warns loudly; wrong-typed key raises ---------


def test_load_unparseable_toml_warns_and_returns_empty(tmp_path, caplog):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.pydocs-mcp\nexclude_dirs = [")  # broken TOML
    with caplog.at_level("WARNING", logger="pydocs_mcp.project_toml"):
        result = load_project_excludes(tmp_path)
    assert result == EMPTY_PROJECT_EXCLUDES
    assert str(pyproject) in caplog.text
    assert "NOT applied" in caplog.text


def test_load_string_valued_exclude_dirs_raises(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pydocs-mcp]\nexclude_dirs = "docs"\n'
    )
    with pytest.raises(ProjectExcludeConfigError) as excinfo:
        load_project_excludes(tmp_path)
    assert "list of strings" in str(excinfo.value)


def test_load_int_element_raises_pydocs_mcp_error(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pydocs-mcp]\nexclude_dirs = [1]\n"
    )
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
```

- [ ] **Step 6: Run — expect PASS immediately (implementation already complete from Step 3)**

```bash
pytest tests/test_project_toml.py -v
```

Expected: all 20 tests pass. If `test_load_unparseable_toml_warns_and_returns_empty` fails on the caplog assertion, the warning message drifted from Step 3 — fix the message, not the test.

- [ ] **Step 7: Append the AC-5 matching tests plus merge/fingerprint contract tests (red-check, then green)**

Append to `tests/test_project_toml.py`:

```python
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
    assert (
        exclusion_fingerprint(_excludes(names=floor, anchored=["a/b"]), floor)
        is not None
    )


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
```

Run:

```bash
pytest tests/test_project_toml.py -v
```

Expected: all 28 tests pass. (These pin behavior the Step 3 module already implements; a failure here means Step 3 drifted from the shared contract — fix the module.)

- [ ] **Step 8: Run the surrounding gates for the new files**

```bash
ruff format --check python/pydocs_mcp/project_toml.py tests/test_project_toml.py
ruff check python/pydocs_mcp/project_toml.py tests/test_project_toml.py
mypy python/pydocs_mcp
pytest tests/ -q -x --ignore=tests/test_parity.py
```

Expected: ruff/mypy clean; full suite green (the new module is additive — nothing imports it yet, so no existing test can move).

- [ ] **Step 9: Commit**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
git add python/pydocs_mcp/project_toml.py tests/test_project_toml.py
git commit -m "feat: project_toml.py owns [tool.pydocs-mcp] exclude_dirs (AC-1..AC-5)"
```

(Authorship policy: plain `git commit -m`, no `Co-Authored-By` trailer, no `--author`.)

---

### Task 2: YAML `exclude_dirs` field + D1 docstring amendments in `config.py` (spec §7.2, D1; AC-14, AC-15)

Declare `exclude_dirs` on `DiscoveryScopeConfig` (`python/pydocs_mcp/extraction/config.py:126`) with a `@field_validator` that delegates to `split_exclude_entries` and re-raises as `ValueError` (pydantic wraps it into the usual startup `ValidationError` — the `include_extensions` precedent at `extraction/config.py:143-153`). Amend the three #6b policy docstrings that currently say "not user-configurable" (decision D1: the FLOOR is hardcoded; user exclusions are additive-only). Rework `tests/extraction/test_config.py`: the old rejection test `test_discovery_scope_config_forbids_exclude_dirs` (`test_config.py:72-80`) inverts into declared-field + `AppConfig.load` success (AC-14) and invalid-entry `ValidationError` tests (AC-15).

Both scopes get the field for free through the existing `DiscoveryConfig.project` / `DiscoveryConfig.dependency` split (`extraction/config.py:156-162`). `_EXCLUDED_DIRS` and `path_under_excluded` are code-unchanged — only docstrings move.

**Files:**

- Modify: `python/pydocs_mcp/extraction/config.py` (module docstring lines 10-18; `_EXCLUDED_DIRS` attribute docstring lines 59-62; `DiscoveryScopeConfig` class body lines 126-153)
- Test: `tests/extraction/test_config.py` (replace lines 72-80; amend the module docstring lines 1-14 and the cross-reference in `test_excluded_dirs_is_module_level_frozenset`, lines 62-69)

- [ ] **Step 1: Replace the old rejection test with the failing AC-14/AC-15 tests**

In `tests/extraction/test_config.py`, DELETE the function `test_discovery_scope_config_forbids_exclude_dirs` (lines 72-80, quoted here so you delete the right one):

```python
def test_discovery_scope_config_forbids_exclude_dirs():
    """Spec AC #6b guardrail: ``DiscoveryScopeConfig.model_fields`` must NOT
    contain ``exclude_dirs``. Attempting to set it via YAML / init hits
    Pydantic ``extra="forbid"`` and raises :class:`ValidationError`."""
    assert "exclude_dirs" not in DiscoveryScopeConfig.model_fields, (
        "exclude_dirs must not be a declared field — blocklist is hardcoded"
    )
    with pytest.raises(ValidationError, match="exclude_dirs"):
        DiscoveryScopeConfig(exclude_dirs=["my_secret_dir"])
```

and insert in its place:

```python
def test_discovery_scope_config_declares_exclude_dirs():
    """AC-14, inverting the old #6b rejection test: ``exclude_dirs`` IS a
    declared field (decision D1 — the FLOOR stays hardcoded in
    ``_EXCLUDED_DIRS``; user entries are additive-only), defaulting to []."""
    assert "exclude_dirs" in DiscoveryScopeConfig.model_fields
    assert DiscoveryScopeConfig().exclude_dirs == []
    cfg = DiscoveryScopeConfig(exclude_dirs=["fixtures", "docs/generated"])
    assert cfg.exclude_dirs == ["fixtures", "docs/generated"]


def test_exclude_dirs_loads_through_app_config(tmp_path):
    """AC-14 end of the wire: a YAML overlay setting
    ``extraction.discovery.project.exclude_dirs`` loads through
    ``AppConfig.load`` — no more ``extra="forbid"`` rejection for this key."""
    from pydocs_mcp.retrieval.config import AppConfig

    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        'extraction:\n  discovery:\n    project:\n      exclude_dirs: ["fixtures"]\n'
    )
    config = AppConfig.load(explicit_path=overlay)
    assert config.extraction.discovery.project.exclude_dirs == ["fixtures"]
    assert config.extraction.discovery.dependency.exclude_dirs == []


@pytest.mark.parametrize("bad_entry", ["/tmp/abs", "a/../b", ""])
def test_exclude_dirs_invalid_entry_rejected_at_load(tmp_path, bad_entry):
    """AC-15: escaping / empty entries fail at ``AppConfig.load`` with a
    ``ValidationError`` naming the field — the shared
    ``split_exclude_entries`` rules (D5), surfaced through pydantic."""
    from pydocs_mcp.retrieval.config import AppConfig

    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        f'extraction:\n  discovery:\n    project:\n      exclude_dirs: ["{bad_entry}"]\n'
    )
    with pytest.raises(ValidationError, match="exclude_dirs"):
        AppConfig.load(explicit_path=overlay)


def test_exclude_dirs_floor_duplicate_is_allowed():
    """Spec §8: listing a floor entry (".git") is a harmless no-op under
    union semantics — allowed, never an error."""
    cfg = DiscoveryScopeConfig(exclude_dirs=[".git"])
    assert cfg.exclude_dirs == [".git"]
```

- [ ] **Step 2: Update the two stale docstrings in the same test file**

Replace the test-module docstring (lines 1-14) with:

```python
"""Tests for ExtractionConfig + the directory-exclusion floor (spec §11.1).

Invariants:
- All defaults load without a YAML file.
- Extension allowlist is narrowable (``[".py"]`` works).
- Extension allowlist cannot be widened (``[".rst"]`` raises).
- ``_EXCLUDED_DIRS`` is a module-level ``frozenset`` — the hardcoded,
  non-removable FLOOR (decision #6b as amended by the 2026-07-13
  exclude-dirs design: user ``exclude_dirs`` entries are additive-only).
- :class:`DiscoveryScopeConfig` declares ``exclude_dirs`` (AC-14) and
  validates entries via the shared ``split_exclude_entries`` (AC-15).
- All models use ``extra="forbid"`` — stray keys raise.
- ``by_extension`` validator catches unsupported extensions.
- ``ExtractionConfig`` round-trips via ``model_dump`` + re-construction.
"""
```

And replace the docstring of `test_excluded_dirs_is_module_level_frozenset` (its body is unchanged):

```python
def test_excluded_dirs_is_module_level_frozenset():
    """``_EXCLUDED_DIRS`` is a frozenset at module scope — the FLOOR of
    decision #6b as amended: user surfaces can only ADD exclusions on top
    (see ``test_discovery_scope_config_declares_exclude_dirs``); nothing
    can remove an entry from it at runtime or via YAML."""
    assert isinstance(_EXCLUDED_DIRS, frozenset)
    # Common noisy / secret-bearing directories.
    for d in (".git", ".venv", "site-packages", "node_modules", "__pycache__"):
        assert d in _EXCLUDED_DIRS, f"{d!r} must be blocklisted (security / index-bloat invariant)"
```

- [ ] **Step 3: Run the reworked tests — expect the three new ones to FAIL**

```bash
pytest tests/extraction/test_config.py -v
```

Expected failures:
- `test_discovery_scope_config_declares_exclude_dirs` — `AssertionError` on `"exclude_dirs" in DiscoveryScopeConfig.model_fields` (pydantic still forbids the key).
- `test_exclude_dirs_loads_through_app_config` — `ValidationError: ... exclude_dirs ... Extra inputs are not permitted`.
- `test_exclude_dirs_floor_duplicate_is_allowed` — same `ValidationError`.
- `test_exclude_dirs_invalid_entry_rejected_at_load` — passes for the wrong reason at this point (extra-forbid also raises `ValidationError` matching "exclude_dirs"); it becomes meaningful once the field exists. All other existing tests stay green.

- [ ] **Step 4: Implement — add the field + validator and amend the three docstrings in `python/pydocs_mcp/extraction/config.py`**

Four edits. First, add the import after the existing pydantic import block (current lines 21-25):

```python
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pydocs_mcp.project_toml import ProjectExcludeConfigError, split_exclude_entries
```

(`project_toml` imports only stdlib + `pydocs_mcp.exceptions` — no cycle back into `extraction/`.)

Second, replace the module docstring's policy paragraph (current lines 10-18, beginning `Policy (decision #6b):` and ending `(spec AC #6b).`) with:

```python
Policy (decision #6b, amended by the 2026-07-13 exclude-dirs design): the
**extension allowlist** is narrowable via YAML (``include_extensions``);
the **directory-exclusion FLOOR** is HARDCODED in :data:`_EXCLUDED_DIRS`
and non-removable — un-excluded ``.git`` / ``.venv`` / ``site-packages``
would leak secrets, balloon the FTS index, and break inspect-mode imports.
User exclusions are additive-only: ``exclude_dirs`` on
:class:`DiscoveryScopeConfig` (server YAML) and the indexed project's
``[tool.pydocs-mcp] exclude_dirs`` (see :mod:`pydocs_mcp.project_toml`)
union OVER the floor; no surface, and no syntax, can shrink it.
```

Third, replace the `_EXCLUDED_DIRS` attribute docstring (current lines 59-62):

```python
"""The hardcoded, non-removable directory-exclusion FLOOR (spec decision
#6b as amended): user surfaces — YAML ``exclude_dirs`` on
:class:`DiscoveryScopeConfig` and the project's ``[tool.pydocs-mcp]
exclude_dirs`` — can only ADD exclusions on top of this set."""
```

Fourth, replace the whole `DiscoveryScopeConfig` class (current lines 126-153) with this final version:

```python
class DiscoveryScopeConfig(BaseModel):
    """Per-context discovery scope — project vs dependency.

    ``exclude_dirs`` entries are ADDITIVE over the hardcoded
    :data:`_EXCLUDED_DIRS` floor (decision #6b as amended): bare names
    match any path component at any depth; entries containing ``/`` are
    walk-root-anchored subtree paths. No entry can shrink the floor.
    ``include_extensions`` remains narrow-only; ``extra="forbid"`` still
    catches genuinely unknown keys at load time.
    """

    model_config = ConfigDict(extra="forbid")

    include_extensions: list[str] = Field(default_factory=lambda: [".py", ".md", ".ipynb"])
    # 1MB, not 500KB: a real 561KB module (mlc_llm dispatch table) was
    # silently skipped under the old cap, imposing an unwinnable recall
    # ceiling on every retrieval method (PAGEINDEX_DIVS.md F3).
    max_file_size_bytes: int = 1_000_000
    exclude_dirs: list[str] = Field(default_factory=list)

    @field_validator("include_extensions")
    @classmethod
    def _enforce_allowlist(cls, v: list[str]) -> list[str]:
        bad = set(v) - ALLOWED_EXTENSIONS
        if bad:
            raise ValueError(
                f"extraction.discovery.*.include_extensions: unsupported "
                f"extensions {sorted(bad)}; must be subset of "
                f"{sorted(ALLOWED_EXTENSIONS)}"
            )
        return v

    @field_validator("exclude_dirs")
    @classmethod
    def _validate_exclude_dirs(cls, v: list[str]) -> list[str]:
        # Delegate to the shared normalizer (design D5) so the TOML and
        # YAML surfaces can never drift; re-raise as ValueError so pydantic
        # wraps it into the usual startup ValidationError.
        try:
            split_exclude_entries(v)
        except ProjectExcludeConfigError as exc:
            raise ValueError(
                f"extraction.discovery.*.exclude_dirs: {exc}"
            ) from exc
        return v
```

(The validator validates and returns the raw list rather than storing the classified split — the discoverers re-split via `merge_excludes` per run, keeping the config model a plain round-trippable YAML mirror; `test_model_dump_round_trips` at `tests/extraction/test_config.py:127` depends on that.)

- [ ] **Step 5: Run the reworked test file — expect PASS**

```bash
pytest tests/extraction/test_config.py -v
```

Expected: all tests pass, including `test_model_dump_round_trips` and `test_every_model_forbids_extras` (the field is declared, so `extra="forbid"` now rejects only genuinely unknown keys — `bogus=1` still raises).

- [ ] **Step 6: Run the affected existing suites and gates**

```bash
pytest tests/test_project_toml.py tests/extraction/ -q
ruff format --check python/pydocs_mcp/extraction/config.py tests/extraction/test_config.py
ruff check python/ tests/
mypy python/pydocs_mcp
pytest tests/ -q --ignore=tests/test_parity.py
```

Expected: everything green. If anything under `tests/` greps for the deleted test name or asserts the absence of `exclude_dirs`, this run surfaces it — the only known referencer was the docstring updated in Step 2 (`rg -n "forbids_exclude_dirs" tests/ python/` should return nothing).

- [ ] **Step 7: Commit**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
git add python/pydocs_mcp/extraction/config.py tests/extraction/test_config.py
git commit -m "feat: declare additive exclude_dirs on DiscoveryScopeConfig (D1 amendment, AC-14/AC-15)"
```

(Authorship policy: plain `git commit -m`, no trailers, no `--author`.)

---

### Task 3: ProjectFileDiscoverer exclusion pruning (spec §7.3; AC-6..AC-10)

**Prerequisite:** Tasks 1–2 have landed `python/pydocs_mcp/project_toml.py` (`ProjectExcludes`, `EMPTY_PROJECT_EXCLUDES`, `split_exclude_entries`, `load_project_excludes`, `merge_excludes`, `ProjectExcludeConfigError`) and the `DiscoveryScopeConfig.exclude_dirs` field. Do not start this task until `pytest tests/test_project_toml.py -q` and `pytest tests/extraction/test_config.py -q` are green.

`ProjectFileDiscoverer` gains a per-run injected `excludes_loader` and prunes `os.walk` against the effective set `_EXCLUDED_DIRS ∪ YAML project.exclude_dirs ∪ loader(target)`. The return shape stays `(paths, root)` in THIS task (the 3-tuple lands in Task 5). This task also carries the spec D1 docstring amendments for the three discovery-side files.

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/discovery/project.py` (whole file — docstring lines 1-16, `discover` lines 32-46)
- Modify: `python/pydocs_mcp/extraction/strategies/discovery/__init__.py` (package docstring lines 12-16)
- Modify: `python/pydocs_mcp/extraction/strategies/discovery/_shared.py` (module docstring lines 1-8 only — no code change here)
- Test: `tests/extraction/test_discovery.py` (extend; also amend its module docstring lines 13-15)

- [ ] **Step 1: Write the failing tests (AC-6..AC-10).**

  In `tests/extraction/test_discovery.py`, first extend the import block (currently lines 18-30). The final import block is:

  ```python
  from __future__ import annotations

  import logging
  from dataclasses import dataclass
  from pathlib import Path, PurePosixPath

  import pytest

  from pydocs_mcp.extraction.config import DiscoveryScopeConfig
  from pydocs_mcp.extraction.strategies.discovery import (
      DependencyFileDiscoverer,
      ProjectFileDiscoverer,
  )
  from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes
  ```

  Then replace the last paragraph of the test module's docstring (lines 13-15, `"Decision #6b: directory blocklist cannot be widened/narrowed — it's a module\nconstant. These tests pin that invariant by asserting presence of common\nblocklist entries in output filtering."`) with:

  ```python
  Decision #6b (amended 2026-07-13): the directory-blocklist FLOOR is a module
  constant and non-removable; user exclusions are additive-only. These tests pin
  both halves — the floor always prunes, and YAML/pyproject entries prune MORE.
  ```

  Append this new section at the end of the `ProjectFileDiscoverer` block (after `test_project_nested_dirs_walked`, before the `DependencyFileDiscoverer` section at line 142):

  ```python
  # ── Per-project exclusion pruning (spec 2026-07-13 §7.3, AC-6..AC-10) ─────


  def _build_worked_example_tree(tmp_path: Path) -> None:
      """The §4 worked-example tree from the exclude-dirs spec (no pyproject
      on disk — each test supplies excludes via the injected loader/scope)."""
      (tmp_path / "docs" / "generated").mkdir(parents=True)
      (tmp_path / "docs" / "generated" / "api.md").write_text("# api\n")
      (tmp_path / "docs" / "guide.md").write_text("# guide\n")
      (tmp_path / "src" / "myproj" / "fixtures").mkdir(parents=True)
      (tmp_path / "src" / "myproj" / "core.py").write_text("x = 1\n")
      (tmp_path / "src" / "myproj" / "fixtures" / "sample.py").write_text("y = 2\n")
      (tmp_path / "fixtures").mkdir()
      (tmp_path / "fixtures" / "data.md").write_text("# data\n")
      (tmp_path / "tools" / "generated").mkdir(parents=True)
      (tmp_path / "tools" / "generated" / "gen.py").write_text("z = 3\n")
      (tmp_path / ".venv").mkdir()
      (tmp_path / ".venv" / "secret.py").write_text("s = 4\n")


  def _rel_paths(paths: list[str], root: Path) -> set[str]:
      return {Path(p).relative_to(root).as_posix() for p in paths}


  def test_project_empty_excludes_output_identical_to_floor_only(tmp_path: Path) -> None:
      """AC-6 regression: with exclude_dirs empty on both surfaces the output
      is byte-identical to floor-only pruning — same sorted paths whether the
      loader is the real default (no pyproject on disk → empty) or an
      injected empty fake."""
      _build_worked_example_tree(tmp_path)

      default_disc = ProjectFileDiscoverer(scope=DiscoveryScopeConfig())
      injected_disc = ProjectFileDiscoverer(
          scope=DiscoveryScopeConfig(),
          excludes_loader=lambda root: EMPTY_PROJECT_EXCLUDES,
      )

      default_out = default_disc.discover(tmp_path)
      injected_out = injected_disc.discover(tmp_path)

      assert default_out == injected_out
      paths = default_out[0]
      assert paths == sorted(paths)
      assert _rel_paths(paths, tmp_path) == {
          "docs/generated/api.md",
          "docs/guide.md",
          "src/myproj/core.py",
          "src/myproj/fixtures/sample.py",
          "fixtures/data.md",
          "tools/generated/gen.py",
      }


  def test_project_bare_name_entry_prunes_every_depth(tmp_path: Path) -> None:
      """AC-7: bare "fixtures" prunes BOTH occurrences — root-level and the
      nested src/myproj/fixtures — any path component, any depth (§4)."""
      _build_worked_example_tree(tmp_path)
      disc = ProjectFileDiscoverer(
          scope=DiscoveryScopeConfig(),
          excludes_loader=lambda root: ProjectExcludes(
              names=frozenset({"fixtures"}), anchored=frozenset()
          ),
      )
      paths, _ = disc.discover(tmp_path)
      rels = _rel_paths(paths, tmp_path)
      assert "fixtures/data.md" not in rels
      assert "src/myproj/fixtures/sample.py" not in rels
      assert "src/myproj/core.py" in rels
      assert "docs/guide.md" in rels


  def test_project_anchored_entry_prunes_only_its_own_path(tmp_path: Path) -> None:
      """AC-8: anchored "docs/generated" removes docs/generated/** while the
      leaf-name sibling tools/generated/** survives (§4 worked example)."""
      _build_worked_example_tree(tmp_path)
      disc = ProjectFileDiscoverer(
          scope=DiscoveryScopeConfig(),
          excludes_loader=lambda root: ProjectExcludes(
              names=frozenset(), anchored=frozenset({"docs/generated"})
          ),
      )
      paths, _ = disc.discover(tmp_path)
      rels = _rel_paths(paths, tmp_path)
      assert "docs/generated/api.md" not in rels
      assert "docs/guide.md" in rels
      assert "tools/generated/gen.py" in rels


  def test_project_floor_survives_user_excludes_and_duplicates_are_noop(
      tmp_path: Path,
  ) -> None:
      """AC-9: the floor still prunes with user excludes set (.venv contents
      never discovered) and a user entry duplicating a floor name (".git")
      is a harmless no-op, not an error."""
      _build_worked_example_tree(tmp_path)
      (tmp_path / ".git").mkdir()
      (tmp_path / ".git" / "hook.py").write_text("h = 1\n")

      disc = ProjectFileDiscoverer(
          scope=DiscoveryScopeConfig(),
          excludes_loader=lambda root: ProjectExcludes(
              names=frozenset({".git", "fixtures"}), anchored=frozenset()
          ),
      )
      paths, _ = disc.discover(tmp_path)
      rels = _rel_paths(paths, tmp_path)
      assert not any(r.startswith(".venv/") for r in rels)
      assert not any(r.startswith(".git/") for r in rels)
      assert not any("fixtures" in r.split("/") for r in rels)
      assert "src/myproj/core.py" in rels


  def test_project_yaml_and_pyproject_surfaces_merge(tmp_path: Path) -> None:
      """AC-10: YAML scope entries and pyproject entries UNION — each surface
      excludes a different directory and both are gone. The pyproject side
      arrives via the injected fake loader, proving the D3 injection seam
      (called once per run, with the walk root)."""
      _build_worked_example_tree(tmp_path)
      calls: list[Path] = []

      def fake_loader(root: Path) -> ProjectExcludes:
          calls.append(root)
          return ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())

      disc = ProjectFileDiscoverer(
          scope=DiscoveryScopeConfig(exclude_dirs=["docs/generated"]),
          excludes_loader=fake_loader,
      )
      paths, _ = disc.discover(tmp_path)
      rels = _rel_paths(paths, tmp_path)

      assert calls == [tmp_path]
      assert "fixtures/data.md" not in rels          # pyproject surface
      assert "src/myproj/fixtures/sample.py" not in rels
      assert "docs/generated/api.md" not in rels     # YAML surface
      assert "docs/guide.md" in rels
      assert "tools/generated/gen.py" in rels
  ```

- [ ] **Step 2: Run the new tests — expect FAIL.**

  From the repo root `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a`:

  ```bash
  pytest tests/extraction/test_discovery.py -v
  ```

  Expected: the 5 new tests FAIL with `TypeError: ProjectFileDiscoverer.__init__() got an unexpected keyword argument 'excludes_loader'` (the frozen slotted dataclass has no such field yet). Every pre-existing test in the file still PASSES.

- [ ] **Step 3: Implement — rewrite `python/pydocs_mcp/extraction/strategies/discovery/project.py`.**

  Full final file content (this includes the D1 docstring amendment — the old "users cannot widen or shrink the directory blocklist" wording dies here):

  ```python
  """ProjectFileDiscoverer — walks a project directory.

  Prunes directories in-place during ``os.walk`` so excluded subtrees are
  never descended into. Filters files by ``scope.include_extensions`` and
  skips anything larger than ``scope.max_file_size_bytes``. Output paths
  are sorted for deterministic downstream hashing.

  The pruning set is the EFFECTIVE exclusion set (spec decision #6b, as
  amended 2026-07-13): the hardcoded
  :data:`~pydocs_mcp.extraction.config._EXCLUDED_DIRS` FLOOR — never
  removable, because un-excluded ``.git`` / ``.venv`` / ``site-packages``
  would leak secrets, balloon the FTS index, and break inspect-mode
  imports — unioned with the two ADDITIVE user surfaces: YAML
  ``extraction.discovery.project.exclude_dirs`` and the indexed project's
  own ``[tool.pydocs-mcp] exclude_dirs``. Users can exclude MORE than the
  floor, never less.

  The pyproject surface is read PER RUN through the injected
  ``excludes_loader`` — not captured at composition time — so a
  ``--watch``-triggered reindex applies fresh ``pyproject.toml`` exclude
  edits without a server restart (spec D3).
  """

  from __future__ import annotations

  import os
  from collections.abc import Callable
  from dataclasses import dataclass
  from pathlib import Path

  from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, DiscoveryScopeConfig
  from pydocs_mcp.extraction.strategies.discovery._shared import _within_size_budget
  from pydocs_mcp.project_toml import (
      ProjectExcludes,
      load_project_excludes,
      merge_excludes,
  )


  def _dir_survives(
      root: Path,
      dirpath: str,
      name: str,
      effective: ProjectExcludes,
  ) -> bool:
      """True iff the candidate directory is NOT excluded.

      Bare names are an O(1) frozenset test on the leaf name. Anchored
      entries need the walk-root-relative POSIX path of the candidate
      directory — computed only when anchored entries exist, keeping the
      no-excludes path byte-identical to floor-only pruning. Pruning at
      the directory level means excluded subtrees are never descended,
      so per-file checks never happen (spec §7.3).
      """
      if name in effective.names:
          return False
      if not effective.anchored:
          return True
      rel = (Path(dirpath) / name).relative_to(root).as_posix()
      return not effective.matches(rel)


  @dataclass(frozen=True, slots=True)
  class ProjectFileDiscoverer:
      scope: DiscoveryScopeConfig
      # Injected strategy, not a startup capture: every discover() run
      # re-reads the project's pyproject.toml so --watch reindexes pick up
      # [tool.pydocs-mcp] exclude_dirs edits without a restart (spec D3).
      excludes_loader: Callable[[Path], ProjectExcludes] = load_project_excludes

      def discover(self, target: Path) -> tuple[list[str], Path]:
          root = Path(target)
          effective = merge_excludes(
              _EXCLUDED_DIRS,
              self.scope.exclude_dirs,
              self.excludes_loader(root),
          )
          paths: list[str] = []
          for dirpath, dirnames, filenames in os.walk(root):
              # Prune in-place so os.walk skips excluded subtrees entirely.
              dirnames[:] = [
                  d for d in dirnames if _dir_survives(root, dirpath, d, effective)
              ]
              for name in filenames:
                  ext = Path(name).suffix.lower()
                  if ext not in self.scope.include_extensions:
                      continue
                  full = str(Path(dirpath) / name)
                  if not _within_size_budget(full, self.scope.max_file_size_bytes):
                      continue
                  paths.append(full)
          paths.sort()
          return paths, root


  __all__ = ("ProjectFileDiscoverer",)
  ```

- [ ] **Step 4: Amend the D1 docstrings in the two sibling files.**

  In `python/pydocs_mcp/extraction/strategies/discovery/__init__.py`, replace the paragraph at lines 12-16 (`"Both consult the HARDCODED ... import from :mod:`.base_discoverer`."`) so the full final docstring reads:

  ```python
  """File discoverers — one file per strategy.

  Two concrete implementations of the
  :class:`~pydocs_mcp.extraction.protocols.ProjectFileDiscoverer` /
  :class:`~pydocs_mcp.extraction.protocols.DependencyFileDiscoverer`
  Protocols:

  - :mod:`.project` — :class:`ProjectFileDiscoverer` (walks a project dir)
  - :mod:`.dependency` — :class:`DependencyFileDiscoverer` (lists files
    shipped by an installed dependency distribution)

  Both prune against the EFFECTIVE exclusion set: the hardcoded
  :data:`~pydocs_mcp.extraction.config._EXCLUDED_DIRS` floor
  (non-removable) unioned with the additive user surfaces — YAML
  ``extraction.discovery.*.exclude_dirs`` on both scopes; the project
  walk additionally honors the indexed project's own
  ``[tool.pydocs-mcp] exclude_dirs`` (spec decision #6b as amended
  2026-07-13: additive-only, the floor never shrinks). The Protocol
  types share names with the concrete classes; consumers that need the
  structural Protocol import from :mod:`.base_discoverer`.
  """
  ```

  In `python/pydocs_mcp/extraction/strategies/discovery/_shared.py`, replace the module docstring (lines 1-8) with:

  ```python
  """Helpers shared by both file discoverers.

  Both ``ProjectFileDiscoverer`` and ``DependencyFileDiscoverer`` filter
  out files larger than ``scope.max_file_size_bytes`` and skip files
  inside the effective exclusion set (hardcoded floor + configured
  additions). These helpers live here so the two implementations stay
  byte-identical on their pruning policy.
  """
  ```

  No code in `_shared.py` changes in this task.

- [ ] **Step 5: Run the tests — expect PASS.**

  ```bash
  pytest tests/extraction/test_discovery.py -v
  ```

  Expected: all tests pass, including the 5 new ones.

- [ ] **Step 6: Run the affected neighboring suites.**

  ```bash
  pytest tests/extraction/ -q
  pytest tests/test_project_toml.py tests/extraction/test_config.py -q
  ```

  Expected: all pass (nothing else consumes the discoverer's constructor shape; the return shape is unchanged in this task).

- [ ] **Step 7: Commit.**

  ```bash
  git add python/pydocs_mcp/extraction/strategies/discovery/project.py \
          python/pydocs_mcp/extraction/strategies/discovery/__init__.py \
          python/pydocs_mcp/extraction/strategies/discovery/_shared.py \
          tests/extraction/test_discovery.py
  git commit -m "feat(discovery): ProjectFileDiscoverer prunes the effective exclusion set (floor + YAML + pyproject)"
  ```

---

### Task 4: DependencyFileDiscoverer YAML excludes (spec §7.4; AC-11)

The dependency walk honors floor ∪ YAML `dependency.exclude_dirs` only. Two checks, both directories-only for USER entries (spec §4: an entry colliding with a file name is a uniform no-op): the FLOOR keeps today's full-relpath `_in_excluded_dir` → `path_under_excluded` framing untouched (byte-compat regression, AC-11 — a wheel-shipped file literally named `.git` stays excluded exactly as today); user BARE names match only the file's **parent-directory** components (basename stripped first — a bare `"conf.py"` entry never drops a shipped `conf.py` file); anchored entries match each `dist.files` relpath with its first path component stripped AND its basename stripped (the stripped **parent-directory** path is what's matched). The class gains NO loader field — a dependency's own `pyproject.toml` is never consulted (spec D4). Return shape stays `(paths, root)` in THIS task.

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/discovery/dependency.py` (whole file — docstring lines 1-10, `discover` lines 32-50)
- Modify: `python/pydocs_mcp/extraction/strategies/discovery/_shared.py` (`_in_excluded_dir`, lines 49-58 — gains the `excluded` parameter)
- Test: `tests/extraction/test_discovery.py` (extend the `DependencyFileDiscoverer` section, reusing `_FakeDist` / `_make_fake_dist` at lines 145-181)

- [ ] **Step 1: Write the failing tests (AC-11).**

  Add `import dataclasses` to the import block of `tests/extraction/test_discovery.py` (below `import logging`). Then append at the end of the `DependencyFileDiscoverer` section (after `test_dependency_empty_files_returns_default_root`, before the `# ── Protocol conformance` marker):

  ```python
  # ── YAML dependency.exclude_dirs (spec 2026-07-13 §7.4, AC-11) ────────────


  def test_dependency_yaml_excludes_bare_and_anchored(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """AC-11: user bare names prune at any depth via PARENT-directory
      components (never a file's own basename — directories-only, §4);
      anchored entries match with the FIRST path component stripped, so one
      entry applies uniformly under every top-level component (§4). A flat
      single-component relpath (six.py) survives every anchored entry; a
      distribution with two top-level components has the anchored entry
      applied under both; a bare entry colliding with a shipped FILE name
      (conf.py) excludes nothing."""
      dist = _make_fake_dist(
          tmp_path,
          (
              "foo/mod.py",
              "foo/tests/test_mod.py",     # bare "tests" — pruned
              "foo/docs/examples/ex.md",   # anchored "docs/examples" — pruned
              "foo/docs/examples/deep/d.md",  # beneath the anchor — pruned
              "foo/docs/guide.md",         # docs itself NOT excluded — kept
              "bar/docs/examples/ex2.md",  # second top-level component — pruned
              "six.py",                    # flat module — survives (§4 edge)
              "foo/conf.py",               # bare "conf.py" is a FILE-name
                                           # collision — kept (§4 no-op rule)
          ),
      )
      monkeypatch.setattr(
          "pydocs_mcp.extraction.strategies.discovery.dependency.find_installed_distribution",
          lambda name: dist,
      )
      monkeypatch.setattr(
          "pydocs_mcp.extraction.strategies.discovery.dependency.find_site_packages_root",
          lambda p: str(tmp_path / "site-packages"),
      )

      scope = DiscoveryScopeConfig(exclude_dirs=["tests", "docs/examples", "conf.py"])
      disc = DependencyFileDiscoverer(scope=scope)
      paths, _ = disc.discover("foo")

      sp = tmp_path / "site-packages"
      rels = {Path(p).relative_to(sp).as_posix() for p in paths}
      assert rels == {"foo/mod.py", "foo/docs/guide.md", "six.py", "foo/conf.py"}


  def test_dependency_never_reads_project_toml(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """AC-11 / D4: the dependency walk NEVER consults the pyproject loader
      — a dependency's own TOML is an untrusted-input channel pointed at
      index composition. Pinned two ways: the module-level loader raises if
      called, and the dataclass structurally has no excludes_loader field."""

      def _boom(root: Path) -> ProjectExcludes:
          raise AssertionError(
              "dependency walk must never call load_project_excludes"
          )

      monkeypatch.setattr("pydocs_mcp.project_toml.load_project_excludes", _boom)
      dist = _make_fake_dist(tmp_path, ("foo/mod.py",))
      monkeypatch.setattr(
          "pydocs_mcp.extraction.strategies.discovery.dependency.find_installed_distribution",
          lambda name: dist,
      )
      monkeypatch.setattr(
          "pydocs_mcp.extraction.strategies.discovery.dependency.find_site_packages_root",
          lambda p: str(tmp_path / "site-packages"),
      )

      disc = DependencyFileDiscoverer(
          scope=DiscoveryScopeConfig(exclude_dirs=["tests"]),
      )
      paths, _ = disc.discover("foo")

      assert [Path(p).name for p in paths] == ["mod.py"]
      field_names = {f.name for f in dataclasses.fields(DependencyFileDiscoverer)}
      assert "excludes_loader" not in field_names
  ```

- [ ] **Step 2: Run the new tests — expect FAIL.**

  ```bash
  pytest tests/extraction/test_discovery.py -v -k "yaml_excludes or never_reads"
  ```

  Expected: `test_dependency_yaml_excludes_bare_and_anchored` FAILS with an assertion mismatch — the actual set still contains `foo/tests/test_mod.py`, `foo/docs/examples/ex.md`, `foo/docs/examples/deep/d.md`, and `bar/docs/examples/ex2.md` because `scope.exclude_dirs` is currently ignored. `test_dependency_never_reads_project_toml` PASSES already (it is a regression guard pinning D4 both before and after the change — that is intentional).

- [ ] **Step 3: Implement — update `_shared.py` and rewrite `dependency.py`.**

  In `python/pydocs_mcp/extraction/strategies/discovery/_shared.py`, change the import at line 15 and the `_in_excluded_dir` function (lines 49-58). Final versions of both:

  ```python
  from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, path_under_excluded
  ```

  ```python
  def _in_excluded_dir(
      relpath: str,
      excluded: frozenset[str] = _EXCLUDED_DIRS,
  ) -> bool:
      """True iff any path component of ``relpath`` is in ``excluded``.

      Delegates to :func:`pydocs_mcp.extraction.config.path_under_excluded`
      so this module and the members extractor enforce the same policy
      with the same splitting rules (M2). ``excluded`` defaults to the
      hardcoded floor (checked against the FULL file relpath — today's
      framing, unchanged); the dependency walk's user-entry check passes
      the effective union set (floor ∪ YAML ``dependency.exclude_dirs``
      bare names) with a PARENT-directory relpath, so user entries stay
      directories-only (§4). Guards against dependency wheels that ship
      vestigial ``.git`` or ``__pycache__`` directories (rare but real —
      spec §11.1).
      """
      return path_under_excluded(relpath, excluded)
  ```

  Full final content of `python/pydocs_mcp/extraction/strategies/discovery/dependency.py`:

  ```python
  """DependencyFileDiscoverer — lists files shipped by an installed dependency.

  Returns ``(paths, site_packages_root)``; a missing distribution
  (declared-but-not-installed) returns ``([], Path("."))`` — the
  :class:`~pydocs_mcp.application.ProjectIndexer` treats that as a
  non-fatal skip. Applies the same extension + size filters as
  :class:`ProjectFileDiscoverer`, plus the EFFECTIVE dependency-scope
  exclusion set: the hardcoded ``_EXCLUDED_DIRS`` floor (a wheel can ship
  bundled ``.git/`` / ``__pycache__`` / ``node_modules`` directories and
  they must never leak into the FTS index) unioned with YAML
  ``extraction.discovery.dependency.exclude_dirs``.

  A dependency's own ``pyproject.toml`` is NEVER consulted (spec D4 —
  an untrusted-input channel pointed at index composition), so this
  class has no ``excludes_loader`` field. User entries are
  directories-only (§4): bare names match PARENT-directory components
  (a bare entry colliding with a shipped file name, e.g. ``"conf.py"``,
  excludes nothing); anchored YAML entries match each ``dist.files``
  relpath with its FIRST path component stripped (§4) — one entry like
  ``"docs/examples"`` excludes ``<top-level>/docs/examples/**``
  uniformly across distributions, and a flat single-component relpath
  (``six.py``) never matches an anchored entry. The FLOOR keeps today's
  full-relpath framing (byte-compat with the pre-feature walk).
  """

  from __future__ import annotations

  from dataclasses import dataclass
  from pathlib import Path

  from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, DiscoveryScopeConfig
  from pydocs_mcp.extraction.strategies._dep_helpers import (
      find_installed_distribution,
      find_site_packages_root,
  )
  from pydocs_mcp.extraction.strategies.discovery._shared import (
      _in_excluded_dir,
      _within_size_budget,
  )
  from pydocs_mcp.project_toml import (
      EMPTY_PROJECT_EXCLUDES,
      ProjectExcludes,
      merge_excludes,
  )


  def _bare_dep_dir_match(rel_str: str, effective: ProjectExcludes) -> bool:
      """True iff a PARENT-directory component of ``rel_str`` is an
      effective bare-name entry.

      The file's own basename is stripped BEFORE matching (directories-only
      rule, §4): a bare user entry colliding with a shipped FILE name (e.g.
      ``"conf.py"``) excludes nothing. The floor keeps its legacy
      full-relpath framing in ``discover`` — this check carries only the
      user-visible semantics, so passing the union set (which includes the
      floor) is harmless: floor names on parent components were already
      caught by the floor check.
      """
      parent = "/".join(rel_str.replace("\\", "/").split("/")[:-1])
      return bool(parent) and _in_excluded_dir(parent, effective.names)


  def _anchored_dep_match(rel_str: str, effective: ProjectExcludes) -> bool:
      """True iff the file's PARENT directory, after stripping the relpath's
      first component, falls under an anchored entry (§4).

      The strip is a relpath rule, not a package-directory rule: a
      distribution with several top-level components has the entry applied
      under each of them, and a single-component relpath (flat module a la
      ``six.py``) has nothing left after stripping and never matches. The
      parent directory — not the full file relpath — is matched, per the
      directories-only rule (§4): an entry colliding with a file name is a
      uniform no-op on both walks.
      """
      if not effective.anchored:
          return False
      parts = rel_str.replace("\\", "/").split("/")
      if len(parts) < 2:
          return False
      return effective.matches("/".join(parts[1:-1]))


  @dataclass(frozen=True, slots=True)
  class DependencyFileDiscoverer:
      scope: DiscoveryScopeConfig

      def discover(self, target: str) -> tuple[list[str], Path]:
          # Floor ∪ YAML dependency.exclude_dirs only — no TOML loader
          # (spec D4); EMPTY_PROJECT_EXCLUDES stands in for the absent
          # pyproject surface.
          effective = merge_excludes(
              _EXCLUDED_DIRS,
              self.scope.exclude_dirs,
              EMPTY_PROJECT_EXCLUDES,
          )
          dist = find_installed_distribution(target)
          if dist is None:
              return [], Path()
          paths: list[str] = []
          for f in dist.files or []:
              rel_str = str(f)
              # Floor: full-relpath framing, byte-identical to today's walk
              # when no user entries are configured (AC-11 regression).
              if _in_excluded_dir(rel_str):
                  continue
              # User entries: directories-only (§4) — bare names match
              # parent-dir components, anchored entries match with the
              # first component stripped.
              if _bare_dep_dir_match(rel_str, effective):
                  continue
              if _anchored_dep_match(rel_str, effective):
                  continue
              ext = Path(rel_str).suffix.lower()
              if ext not in self.scope.include_extensions:
                  continue
              full = str(dist.locate_file(f))
              if not _within_size_budget(full, self.scope.max_file_size_bytes):
                  continue
              paths.append(full)
          paths.sort()
          root = Path(find_site_packages_root(paths[0])) if paths else Path()
          return paths, root


  __all__ = ("DependencyFileDiscoverer",)
  ```

- [ ] **Step 4: Run the tests — expect PASS.**

  ```bash
  pytest tests/extraction/test_discovery.py -v
  ```

  Expected: all tests pass — the two new ones plus every pre-existing dependency test (the floor-only tests are unchanged because the floor check IS today's `_in_excluded_dir(rel_str)` verbatim; with empty `exclude_dirs`, `effective.names == _EXCLUDED_DIRS` so `_bare_dep_dir_match` can only re-flag parent components the floor check already dropped, and `effective.anchored` is empty so `_anchored_dep_match` is a constant `False` — the walk is byte-identical to today's).

- [ ] **Step 5: Run the affected neighboring suites.**

  ```bash
  pytest tests/extraction/ -q
  ```

  Expected: all pass.

- [ ] **Step 6: Commit.**

  ```bash
  git add python/pydocs_mcp/extraction/strategies/discovery/dependency.py \
          python/pydocs_mcp/extraction/strategies/discovery/_shared.py \
          tests/extraction/test_discovery.py
  git commit -m "feat(discovery): DependencyFileDiscoverer honors YAML dependency.exclude_dirs (union bare names + first-component-stripped anchored)"
  ```

---

### Task 5: Protocol 3-tuple + `FileBundle.effective_excludes` + `FileDiscoveryStage` persistence (spec §7.3 last bullet, §7.7, §9.1; groundwork for AC-23)

Both discoverers return the effective set they pruned against as a third return element; the Protocols at `extraction/protocols.py:61-71` grow to match; `FileBundle` gains `effective_excludes: ProjectExcludes = EMPTY_PROJECT_EXCLUDES`; `FileDiscoveryStage.run` persists the third element there. This is the single per-run derivation point downstream stages (`ContentHashStage`, `MineDecisionsStage`) read from — they never re-invoke the loader.

Every caller of `.discover(` was enumerated by `grep -rn "\.discover(" python/ tests/ benchmarks/`. The complete caller set to fix (verify with the same grep before starting — Tasks 3-4 added more sites in `tests/extraction/test_discovery.py`):

1. `python/pydocs_mcp/extraction/pipeline/stages/file_discovery.py:41-42` (via `_discover` → `run`)
2. `tests/extraction/test_discovery.py` — every `... = disc.discover(...)` unpack site (16 pre-existing at lines 44, 57, 80, 92, 104, 112, 124, 137, 187, 218, 251, 275, 304, 321, 375, 392 + the sites added by Tasks 3-4)
3. `tests/extraction/test_stages.py` — the `_FakeProjectDiscoverer` / `_FakeDepDiscoverer` fakes (lines 67-94) and their canned results (lines 100-102, 125)
4. `tests/extraction/pipeline/test_stages_use_bundles.py` — fake results at lines 66-67
5. `tests/extraction/test_protocols.py` — fake `discover` bodies at lines 60-65 and 75-80

There are no production callers outside `file_discovery.py` and no benchmark callers.

**Files:**
- Modify: `python/pydocs_mcp/extraction/protocols.py` (lines 60-71)
- Modify: `python/pydocs_mcp/extraction/pipeline/ingestion.py` (`FileBundle`, lines 51-69)
- Modify: `python/pydocs_mcp/extraction/pipeline/stages/file_discovery.py` (lines 34-42 + imports)
- Modify: `python/pydocs_mcp/extraction/strategies/discovery/project.py` (`discover` signature + return)
- Modify: `python/pydocs_mcp/extraction/strategies/discovery/dependency.py` (`discover` signature + returns)
- Test: `tests/extraction/pipeline/test_stages_use_bundles.py` (new stage test + fake updates)
- Test: `tests/extraction/test_discovery.py`, `tests/extraction/test_stages.py`, `tests/extraction/test_protocols.py` (tuple-unpack updates)

- [ ] **Step 1: Write the failing stage test.**

  In `tests/extraction/pipeline/test_stages_use_bundles.py`, add to the import block (after the existing `pydocs_mcp` imports):

  ```python
  from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes
  ```

  Then insert immediately after `test_file_discovery_writes_to_files_bundle` (line 77):

  ```python
  @pytest.mark.asyncio
  async def test_file_discovery_persists_effective_excludes(tmp_path: Path) -> None:
      """FileDiscoveryStage stores the discoverer's third return element on
      state.files.effective_excludes — the single per-run derivation point
      (spec D10): ContentHashStage folds its fingerprint and
      MineDecisionsStage fills CaptureContext.excluded from here, never
      re-invoking the TOML loader mid-run."""
      effective = ProjectExcludes(
          names=frozenset({"fixtures"}),
          anchored=frozenset({"docs/generated"}),
      )
      project_disc = _FakeProjectDiscoverer(result=([], tmp_path, effective))
      dep_disc = _FakeDepDiscoverer(result=([], Path(), EMPTY_PROJECT_EXCLUDES))
      stage = FileDiscoveryStage(
          project_discoverer=project_disc,  # type: ignore[arg-type]
          dep_discoverer=dep_disc,  # type: ignore[arg-type]
      )

      project_state = IngestionState(
          files=FileBundle(target=tmp_path, target_kind=TargetKind.PROJECT),
      )
      out = await stage.run(project_state)
      assert out.files.effective_excludes == effective

      dep_state = IngestionState(
          files=FileBundle(target="foo", target_kind=TargetKind.DEPENDENCY),
      )
      dep_out = await stage.run(dep_state)
      assert dep_out.files.effective_excludes == EMPTY_PROJECT_EXCLUDES


  def test_file_bundle_effective_excludes_defaults_empty() -> None:
      """A directly-constructed FileBundle behaves exactly as today — the
      field defaults to EMPTY_PROJECT_EXCLUDES (spec §7.7)."""
      bundle = FileBundle()
      assert bundle.effective_excludes == EMPTY_PROJECT_EXCLUDES
  ```

- [ ] **Step 2: Run the new tests — expect FAIL.**

  ```bash
  pytest tests/extraction/pipeline/test_stages_use_bundles.py -v -k "effective_excludes"
  ```

  Expected: `test_file_discovery_persists_effective_excludes` FAILS with `ValueError: too many values to unpack (expected 2)` (the stage's `run` unpacks the fake's 3-tuple into `paths, root`); `test_file_bundle_effective_excludes_defaults_empty` FAILS with `AttributeError: 'FileBundle' object has no attribute 'effective_excludes'`.

- [ ] **Step 3: Add the field to `FileBundle`.**

  In `python/pydocs_mcp/extraction/pipeline/ingestion.py`, add a runtime import after the existing imports (line 33, before the `if TYPE_CHECKING:` block — `project_toml` imports only stdlib + `pydocs_mcp.exceptions`, so no cycle):

  ```python
  from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes
  ```

  Replace the `FileBundle` class (lines 51-69) with:

  ```python
  @dataclass(frozen=True, slots=True)
  class FileBundle:
      """Discovery + file-read outputs: where to read from, what was read.

      Wraps the (``target``, ``target_kind``, ``package_name``, ``root``,
      ``paths``, ``file_contents``, ``content_hash``,
      ``effective_excludes``) tuple populated by
      :class:`FileDiscoveryStage`, :class:`FileReadStage`, and
      :class:`ContentHashStage`. Splitting the state into bundles keeps
      stage signatures honest about the slice they touch and stops
      :class:`IngestionState` from growing into a god object.
      """

      target: Path | str = field(default_factory=lambda: Path())
      target_kind: TargetKind = TargetKind.PROJECT
      package_name: str = ""
      root: Path = field(default_factory=lambda: Path())
      paths: tuple[str, ...] = ()
      file_contents: tuple[tuple[str, str], ...] = ()
      content_hash: str = ""
      # The exclusion set the discovery walk ACTUALLY pruned against —
      # the single per-run derivation point (spec D10). ContentHashStage
      # folds its fingerprint and MineDecisionsStage fills
      # CaptureContext.excluded from here; neither re-invokes the TOML
      # loader, so a mid---watch pyproject save between stages can never
      # fingerprint a set the walk did not use.
      effective_excludes: ProjectExcludes = EMPTY_PROJECT_EXCLUDES
  ```

- [ ] **Step 4: Grow both discoverers' return to the 3-tuple.**

  In `python/pydocs_mcp/extraction/strategies/discovery/project.py`, the final `discover` (only the signature and last line change from Task 3):

  ```python
      def discover(self, target: Path) -> tuple[list[str], Path, ProjectExcludes]:
          root = Path(target)
          effective = merge_excludes(
              _EXCLUDED_DIRS,
              self.scope.exclude_dirs,
              self.excludes_loader(root),
          )
          paths: list[str] = []
          for dirpath, dirnames, filenames in os.walk(root):
              # Prune in-place so os.walk skips excluded subtrees entirely.
              dirnames[:] = [
                  d for d in dirnames if _dir_survives(root, dirpath, d, effective)
              ]
              for name in filenames:
                  ext = Path(name).suffix.lower()
                  if ext not in self.scope.include_extensions:
                      continue
                  full = str(Path(dirpath) / name)
                  if not _within_size_budget(full, self.scope.max_file_size_bytes):
                      continue
                  paths.append(full)
          paths.sort()
          return paths, root, effective
  ```

  Also amend the first docstring paragraph of `project.py` to note the new element — replace `"Output paths are sorted for deterministic downstream hashing."` with `"Output paths are sorted for deterministic downstream hashing; the effective exclusion set the walk pruned against is returned as the third element so downstream stages fold/consume the exact set used (spec D10)."`

  In `python/pydocs_mcp/extraction/strategies/discovery/dependency.py`, the final `discover` (signature and the two `return` lines change from Task 4):

  ```python
      def discover(self, target: str) -> tuple[list[str], Path, ProjectExcludes]:
          # Floor ∪ YAML dependency.exclude_dirs only — no TOML loader
          # (spec D4); EMPTY_PROJECT_EXCLUDES stands in for the absent
          # pyproject surface. Returned even on the missing-dist path so
          # the per-scope hash fold (spec §9.1) always sees the set this
          # scope would have used.
          effective = merge_excludes(
              _EXCLUDED_DIRS,
              self.scope.exclude_dirs,
              EMPTY_PROJECT_EXCLUDES,
          )
          dist = find_installed_distribution(target)
          if dist is None:
              return [], Path(), effective
          paths: list[str] = []
          for f in dist.files or []:
              rel_str = str(f)
              # Floor: full-relpath framing, byte-identical to today's walk
              # when no user entries are configured (AC-11 regression).
              if _in_excluded_dir(rel_str):
                  continue
              # User entries: directories-only (§4) — bare names match
              # parent-dir components, anchored entries match with the
              # first component stripped.
              if _bare_dep_dir_match(rel_str, effective):
                  continue
              if _anchored_dep_match(rel_str, effective):
                  continue
              ext = Path(rel_str).suffix.lower()
              if ext not in self.scope.include_extensions:
                  continue
              full = str(dist.locate_file(f))
              if not _within_size_budget(full, self.scope.max_file_size_bytes):
                  continue
              paths.append(full)
          paths.sort()
          root = Path(find_site_packages_root(paths[0])) if paths else Path()
          return paths, root, effective
  ```

  Also amend `dependency.py`'s docstring first sentence: replace ``Returns ``(paths, site_packages_root)``;`` with ``Returns ``(paths, site_packages_root, effective_excludes)``;``.

- [ ] **Step 5: Update the Protocols.**

  In `python/pydocs_mcp/extraction/protocols.py`, add `ProjectExcludes` to the `TYPE_CHECKING` block (lines 33-34):

  ```python
  if TYPE_CHECKING:
      from pydocs_mcp.extraction.model import DocumentNode
      from pydocs_mcp.project_toml import ProjectExcludes
  ```

  Replace the two discoverer Protocols (lines 60-71) with:

  ```python
  @runtime_checkable
  class ProjectFileDiscoverer(Protocol):
      """Yields ``(paths, root, effective_excludes)`` for a project directory
      target. The third element is the exclusion set the walk pruned
      against — floor ∪ YAML ``project.exclude_dirs`` ∪ the project's own
      ``[tool.pydocs-mcp] exclude_dirs`` — carried on the state bundle so
      downstream stages never re-derive it (spec D10)."""

      def discover(self, target: Path) -> tuple[list[str], Path, ProjectExcludes]: ...


  @runtime_checkable
  class DependencyFileDiscoverer(Protocol):
      """Yields ``(paths, root, effective_excludes)`` for an installed
      dependency by name. The third element is floor ∪ YAML
      ``dependency.exclude_dirs`` only — a dependency's own
      ``pyproject.toml`` is never read (spec D4)."""

      def discover(self, target: str) -> tuple[list[str], Path, ProjectExcludes]: ...
  ```

- [ ] **Step 6: Persist the third element in `FileDiscoveryStage`.**

  In `python/pydocs_mcp/extraction/pipeline/stages/file_discovery.py`, add `ProjectExcludes` to the `TYPE_CHECKING` block (lines 20-24):

  ```python
  if TYPE_CHECKING:
      from pydocs_mcp.extraction.strategies.discovery import (
          DependencyFileDiscoverer,
          ProjectFileDiscoverer,
      )
      from pydocs_mcp.project_toml import ProjectExcludes
  ```

  Replace `run` and `_discover` (lines 34-42) with:

  ```python
      async def run(self, state: IngestionState) -> IngestionState:
          paths, root, effective = await asyncio.to_thread(self._discover, state)
          new_files = replace(
              state.files,
              paths=tuple(paths),
              root=root,
              effective_excludes=effective,
          )
          return replace(state, files=new_files)

      def _discover(
          self, state: IngestionState
      ) -> tuple[list[str], Path, ProjectExcludes]:
          if state.files.target_kind is TargetKind.PROJECT:
              return self.project_discoverer.discover(Path(str(state.files.target)))
          return self.dep_discoverer.discover(str(state.files.target))
  ```

  Also update the stage's module docstring first line (line 1) to `"""FileDiscoveryStage — fills ``state.files.paths`` + ``state.files.root`` + ``state.files.effective_excludes``.`

- [ ] **Step 7: Run the new stage tests — expect the two new tests to PASS, existing 2-tuple fakes to FAIL.**

  ```bash
  pytest tests/extraction/pipeline/test_stages_use_bundles.py tests/extraction/test_stages.py -v
  ```

  Expected: the two Step-1 tests PASS; `test_file_discovery_writes_to_files_bundle`, `test_file_discovery_branches_on_project_target`, and `test_file_discovery_branches_on_dependency_target` FAIL with `ValueError: not enough values to unpack (expected 3, got 2)` — their fakes still return 2-tuples. That is the fix list for Step 8.

- [ ] **Step 8: Fix every remaining caller (the enumerated set above).**

  **(a) `tests/extraction/test_stages.py`** — add to the import block:

  ```python
  from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes
  ```

  Replace the two fakes (lines 67-94) with:

  ```python
  @dataclass
  class _FakeProjectDiscoverer:
      """Records which discover() call was invoked; returns a canned
      (paths, root, effective_excludes)."""

      calls: list = None
      result: tuple = ((), Path(), EMPTY_PROJECT_EXCLUDES)

      def __post_init__(self) -> None:
          if self.calls is None:
              object.__setattr__(self, "calls", [])

      def discover(self, target: Path) -> tuple[list[str], Path, ProjectExcludes]:
          self.calls.append(("project", target))
          return self.result


  @dataclass
  class _FakeDepDiscoverer:
      calls: list = None
      result: tuple = ((), Path(), EMPTY_PROJECT_EXCLUDES)

      def __post_init__(self) -> None:
          if self.calls is None:
              object.__setattr__(self, "calls", [])

      def discover(self, target: str) -> tuple[list[str], Path, ProjectExcludes]:
          self.calls.append(("dep", target))
          return self.result
  ```

  Update the two canned results: line 100-102 becomes `project_disc = _FakeProjectDiscoverer(result=([str(tmp_path / "a.py")], tmp_path, EMPTY_PROJECT_EXCLUDES))`; line 125 becomes `dep_disc = _FakeDepDiscoverer(result=(["/pkgs/foo/mod.py"], Path("/pkgs"), EMPTY_PROJECT_EXCLUDES))`.

  **(b) `tests/extraction/pipeline/test_stages_use_bundles.py`** — update the canned results in `test_file_discovery_writes_to_files_bundle` (lines 66-67):

  ```python
      project_disc = _FakeProjectDiscoverer(result=([str(f1)], tmp_path, EMPTY_PROJECT_EXCLUDES))
      dep_disc = _FakeDepDiscoverer(result=([], Path("/unused"), EMPTY_PROJECT_EXCLUDES))
  ```

  **(c) `tests/extraction/test_protocols.py`** — update the two fake `discover` bodies (lines 60-65 and 75-80; `@runtime_checkable` checks method presence only, so these keep passing either way — updating keeps the fakes honest about the contract). Add the import `from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes` and rewrite:

  ```python
  def test_project_file_discoverer_runtime_checkable_accepts_discover():
      class _FakeProjectDiscoverer:
          def discover(self, target: Path) -> tuple[list[str], Path, ProjectExcludes]:
              return [], target, EMPTY_PROJECT_EXCLUDES

      assert isinstance(_FakeProjectDiscoverer(), ProjectFileDiscoverer)
  ```

  ```python
  def test_dependency_file_discoverer_runtime_checkable_accepts_discover():
      class _FakeDepDiscoverer:
          def discover(self, target: str) -> tuple[list[str], Path, ProjectExcludes]:
              return [], Path(), EMPTY_PROJECT_EXCLUDES

      assert isinstance(_FakeDepDiscoverer(), DependencyFileDiscoverer)
  ```

  **(d) `tests/extraction/test_discovery.py`** — mechanical unpack updates at every `disc.discover(` site (pre-existing lines 44, 57, 80, 92, 104, 112, 124, 137, 187, 218, 251, 275, 304, 321, 375, 392 plus the Task-3/4 additions; re-grep to be exhaustive: `grep -n "disc.discover(" tests/extraction/test_discovery.py`):

  - `paths, root = disc.discover(...)` → `paths, root, _ = disc.discover(...)`
  - `paths, _ = disc.discover(...)` → `paths, _, _ = disc.discover(...)`
  - `_, root = disc.discover(...)` → `_, root, _ = disc.discover(...)`
  - In Task 3's `test_project_empty_excludes_output_identical_to_floor_only`, the `default_out == injected_out` comparison and `default_out[0]` indexing need no change (the 3-tuples compare element-wise; equal effective sets compare equal).

  Then add two pins for the third element at the end of the file (before the `# ── Protocol conformance` section works too; placement at end of the exclusion sections is fine). These need `_EXCLUDED_DIRS` — extend the config import to `from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, DiscoveryScopeConfig`:

  ```python
  def test_project_discover_returns_effective_excludes(tmp_path: Path) -> None:
      """§7.3 last bullet: the third return element is the exact set the walk
      pruned against — floor ∪ scope entries ∪ loader output."""
      disc = ProjectFileDiscoverer(
          scope=DiscoveryScopeConfig(exclude_dirs=["docs/generated"]),
          excludes_loader=lambda root: ProjectExcludes(
              names=frozenset({"fixtures"}), anchored=frozenset()
          ),
      )
      _, _, effective = disc.discover(tmp_path)
      assert "fixtures" in effective.names
      assert _EXCLUDED_DIRS <= effective.names
      assert effective.anchored == frozenset({"docs/generated"})


  def test_dependency_discover_returns_effective_excludes_even_when_missing() -> None:
      """§7.4 last bullet: the dependency scope returns floor ∪ YAML only —
      even on the missing-distribution path, so the per-scope hash fold
      (spec §9.1) always sees the set this scope would have used."""
      disc = DependencyFileDiscoverer(
          scope=DiscoveryScopeConfig(exclude_dirs=["tests"]),
      )
      paths, root, effective = disc.discover("definitely-not-a-real-pkg-2026-xyz")
      assert paths == []
      assert root == Path()
      assert "tests" in effective.names
      assert _EXCLUDED_DIRS <= effective.names
      assert effective.anchored == frozenset()
  ```

- [ ] **Step 9: Run the full affected suites — expect PASS.**

  ```bash
  pytest tests/extraction/ -v
  pytest tests/ -q
  ```

  Expected: everything green. If any `ValueError: not enough values to unpack (expected 3, got 2)` remains, a caller was missed — re-run `grep -rn "\.discover(" python/ tests/ benchmarks/` and fix the site.

- [ ] **Step 10: Run the lint/type gates on the touched surface.**

  ```bash
  ruff format --check python/ tests/ benchmarks/
  ruff check python/ tests/ benchmarks/
  mypy python/pydocs_mcp
  ```

  Expected: clean. (mypy sees the Protocol/impl signatures agree on the 3-tuple; the `TYPE_CHECKING`-only `ProjectExcludes` imports in `protocols.py` / `file_discovery.py` resolve under `from __future__ import annotations`.)

- [ ] **Step 11: Commit.**

  ```bash
  git add python/pydocs_mcp/extraction/protocols.py \
          python/pydocs_mcp/extraction/pipeline/ingestion.py \
          python/pydocs_mcp/extraction/pipeline/stages/file_discovery.py \
          python/pydocs_mcp/extraction/strategies/discovery/project.py \
          python/pydocs_mcp/extraction/strategies/discovery/dependency.py \
          tests/extraction/test_discovery.py \
          tests/extraction/test_stages.py \
          tests/extraction/test_protocols.py \
          tests/extraction/pipeline/test_stages_use_bundles.py
  git commit -m "feat(extraction): discoverers return the effective exclusion set; FileDiscoveryStage persists FileBundle.effective_excludes"
  ```

---

### Task 6: AstMemberExtractor effective-set post-filter + factories wiring

Implements spec §7.5 and §7.7 (AC-12, AC-13). The member-side project walk (`AstMemberExtractor._parse_dir`) currently post-filters `walk_py_files` output against the hardcoded floor only (`python/pydocs_mcp/extraction/strategies/members/ast_extractor.py:96-97`). This task makes it post-filter against the full effective project set — `merge_excludes(_EXCLUDED_DIRS, self.scope_exclude_dirs, self.excludes_loader(root))` — matching each candidate file's **parent-directory** relpath (the directories-only rule of spec §4), and wires the YAML project-scope entries into the extractor at the write-side composition root (`python/pydocs_mcp/storage/factories.py:592`). The dependency member path (`_dep_sync`) is deliberately untouched (spec §2 non-goal; AC-13 pins byte-identity).

**Depends on:** the shared `project_toml.py` module from the earlier tasks — specifically `ProjectExcludes` (with `.matches`), `EMPTY_PROJECT_EXCLUDES`, `load_project_excludes`, and `merge_excludes(floor, scope_entries, loaded)` (whose result's `.names` includes the floor), plus the `DiscoveryScopeConfig.exclude_dirs` field. Do not start this task until those exist.

**Files:**
- Modify: `python/pydocs_mcp/extraction/strategies/members/ast_extractor.py` (fields on the dataclass at line 57; `_parse_dir` at lines 85-98; imports at lines 13-35; new module-level helper)
- Modify: `python/pydocs_mcp/storage/factories.py` (line 592, inside `build_project_indexer`)
- Test: `tests/extraction/test_members.py` (append at end of file, currently 586 lines)
- Test: `tests/storage/test_build_project_indexer.py` (append after line 124)

- [ ] **Step 1: Write the failing extractor tests**

  Append the following block to the END of `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/tests/extraction/test_members.py` (after `test_path_under_excluded_egg_info_as_component_not_substring`, the last test in the file — it starts at line 573; the file currently ends at line 586). Also add one import line to the top-of-file import block, directly below the existing `from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField` (line 31):

  ```python
  from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes
  ```

  Appended tests (complete code — the file already imports `asyncio`, `pytest`, `Path`, `AstMemberExtractor`, `InspectMemberExtractor`, and the `_FakeDist` helper used below):

  ```python
  # -- Effective-set post-filter (per-project exclude_dirs, spec §7.5) ----------


  def _empty_loader(root: Path) -> ProjectExcludes:
      """Injected stand-in for load_project_excludes — no pyproject read."""
      return EMPTY_PROJECT_EXCLUDES


  def _fixed_loader(excludes: ProjectExcludes):
      """Loader returning a canned ProjectExcludes regardless of root — the
      injection seam of spec D3, so no filesystem pyproject.toml is needed."""

      def _load(root: Path) -> ProjectExcludes:
          return excludes

      return _load


  def _make_exclude_tree(tmp_path: Path) -> Path:
      """Worked-example tree of spec §4: bare 'fixtures' occurs at two depths,
      anchored 'docs/generated' has a leaf-name-colliding sibling under
      tools/, and a floor dir (site-packages) is present so floor + user
      excludes can be asserted in one walk."""
      (tmp_path / "kept.py").write_text("def kept(): pass\n", encoding="utf-8")
      fixtures = tmp_path / "fixtures"
      fixtures.mkdir()
      (fixtures / "sample.py").write_text("def fixture_fn(): pass\n", encoding="utf-8")
      nested = tmp_path / "src" / "pkg" / "fixtures"
      nested.mkdir(parents=True)
      (nested / "deep.py").write_text("def deep_fixture_fn(): pass\n", encoding="utf-8")
      generated = tmp_path / "docs" / "generated"
      generated.mkdir(parents=True)
      (generated / "gen.py").write_text("def generated_fn(): pass\n", encoding="utf-8")
      sibling = tmp_path / "tools" / "generated"
      sibling.mkdir(parents=True)
      (sibling / "gen2.py").write_text("def sibling_fn(): pass\n", encoding="utf-8")
      vendored = tmp_path / "site-packages" / "leaky"
      vendored.mkdir(parents=True)
      (vendored / "__init__.py").write_text("def floor_leaked(): pass\n", encoding="utf-8")
      return tmp_path


  async def _member_names(extractor: AstMemberExtractor, root: Path) -> set[str]:
      members = await extractor.extract_from_project(root)
      return {m.metadata[ModuleMemberFilterField.NAME.value] for m in members}


  @pytest.mark.asyncio
  async def test_ast_project_bare_exclude_prunes_all_depths(tmp_path: Path) -> None:
      """AC-12 (TOML surface, bare name): 'fixtures' from the injected loader
      prunes BOTH occurrences (root-level and src/pkg/fixtures); the floor
      still applies alongside user excludes."""
      _make_exclude_tree(tmp_path)
      extractor = AstMemberExtractor(
          excludes_loader=_fixed_loader(
              ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())
          ),
      )
      names = await _member_names(extractor, tmp_path)
      assert "kept" in names
      assert "fixture_fn" not in names, "root-level fixtures/ leaked into member index"
      assert "deep_fixture_fn" not in names, "nested src/pkg/fixtures/ leaked — bare names match ANY depth"
      assert "floor_leaked" not in names, "floor (_EXCLUDED_DIRS) must still apply with user excludes set"


  @pytest.mark.asyncio
  async def test_ast_project_anchored_exclude_prunes_only_its_path(tmp_path: Path) -> None:
      """AC-12 (TOML surface, anchored): 'docs/generated' prunes exactly that
      subtree; the leaf-name-colliding sibling tools/generated survives."""
      _make_exclude_tree(tmp_path)
      extractor = AstMemberExtractor(
          excludes_loader=_fixed_loader(
              ProjectExcludes(names=frozenset(), anchored=frozenset({"docs/generated"}))
          ),
      )
      names = await _member_names(extractor, tmp_path)
      assert "generated_fn" not in names
      assert "sibling_fn" in names, "anchored entry over-matched a same-leaf-name sibling (spec §4)"


  @pytest.mark.asyncio
  async def test_ast_project_scope_exclude_dirs_yaml_surface(tmp_path: Path) -> None:
      """AC-12 (YAML surface): scope_exclude_dirs entries flow through
      merge_excludes' split/classify path — bare and anchored both honored
      with an empty pyproject loader."""
      _make_exclude_tree(tmp_path)
      extractor = AstMemberExtractor(
          excludes_loader=_empty_loader,
          scope_exclude_dirs=("fixtures", "docs/generated"),
      )
      names = await _member_names(extractor, tmp_path)
      assert "kept" in names
      assert "fixture_fn" not in names
      assert "deep_fixture_fn" not in names
      assert "generated_fn" not in names
      assert "sibling_fn" in names


  @pytest.mark.asyncio
  async def test_ast_project_toml_and_yaml_surfaces_merge(tmp_path: Path) -> None:
      """AC-12 (§3.3 union): each surface excludes a DIFFERENT directory —
      both are gone after the merge."""
      _make_exclude_tree(tmp_path)
      extractor = AstMemberExtractor(
          excludes_loader=_fixed_loader(
              ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())
          ),
          scope_exclude_dirs=("docs/generated",),
      )
      names = await _member_names(extractor, tmp_path)
      assert "fixture_fn" not in names
      assert "deep_fixture_fn" not in names
      assert "generated_fn" not in names
      assert "kept" in names
      assert "sibling_fn" in names


  @pytest.mark.asyncio
  async def test_ast_project_filename_collision_entry_is_noop(tmp_path: Path) -> None:
      """Directories-only rule (spec §4 / AC-19 groundwork): an entry that
      collides with a FILE name — bare 'conf.py' or anchored 'docs/conf.py'
      where docs/conf.py is a file — must be a no-op on the member walk,
      because matching targets the file's PARENT DIRECTORY relpath."""
      (tmp_path / "kept.py").write_text("def kept(): pass\n", encoding="utf-8")
      docs = tmp_path / "docs"
      docs.mkdir()
      (docs / "conf.py").write_text("def conf_fn(): pass\n", encoding="utf-8")
      extractor = AstMemberExtractor(
          excludes_loader=_fixed_loader(
              ProjectExcludes(
                  names=frozenset({"conf.py"}),
                  anchored=frozenset({"docs/conf.py"}),
              )
          ),
      )
      names = await _member_names(extractor, tmp_path)
      assert "kept" in names
      assert "conf_fn" in names, (
          "file-name-colliding exclude entry dropped the file's symbols — the "
          "member post-filter matched the file path instead of its parent dir"
      )


  @pytest.mark.asyncio
  async def test_ast_project_default_construction_unchanged(tmp_path: Path) -> None:
      """Regression: a default-constructed extractor on a tree with NO
      pyproject.toml applies the floor only — every user-space directory
      still indexes (spec §7.3 byte-compat posture, member side)."""
      _make_exclude_tree(tmp_path)
      names = await _member_names(AstMemberExtractor(), tmp_path)
      assert {"kept", "fixture_fn", "deep_fixture_fn", "generated_fn", "sibling_fn"} <= names
      assert "floor_leaked" not in names


  @pytest.mark.asyncio
  async def test_inspect_project_delegation_inherits_excludes(tmp_path: Path) -> None:
      """AC-12 (inspect mode): extract_from_project ALWAYS delegates to the
      composed AstMemberExtractor (inspect_extractor.py:42-47), so inspect-mode
      project indexing inherits the effective-set filter verbatim."""
      _make_exclude_tree(tmp_path)
      ast_extractor = AstMemberExtractor(
          excludes_loader=_fixed_loader(
              ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())
          ),
      )
      inspect_extractor = InspectMemberExtractor(static_fallback=ast_extractor)

      ast_members = await ast_extractor.extract_from_project(tmp_path)
      inspect_members = await inspect_extractor.extract_from_project(tmp_path)

      assert ast_members == inspect_members
      names = {m.metadata[ModuleMemberFilterField.NAME.value] for m in inspect_members}
      assert "fixture_fn" not in names


  @pytest.mark.asyncio
  async def test_ast_dependency_path_ignores_configured_excludes(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """AC-13: extract_from_dependency is byte-identical regardless of
      configured excludes — even entries that WOULD match the dependency's own
      package directory — and NEVER consults the pyproject loader."""
      sp = tmp_path / "sp"
      (sp / "foo").mkdir(parents=True)
      # ``encoding="utf-8"`` — see ``simple_project`` fixture for rationale.
      (sp / "foo" / "__init__.py").write_text(_SIMPLE_MODULE, encoding="utf-8")
      dist = _FakeDist(site_packages=sp, rel_files=("foo/__init__.py",))
      monkeypatch.setattr(
          "pydocs_mcp.extraction.strategies.members.ast_extractor.find_installed_distribution",
          lambda name: dist,
      )
      monkeypatch.setattr(
          "pydocs_mcp.extraction.strategies.members.ast_extractor.find_site_packages_root",
          lambda p: str(sp),
      )

      def _boom_loader(root: Path) -> ProjectExcludes:
          raise AssertionError("dependency member path must never call the pyproject loader")

      configured = AstMemberExtractor(excludes_loader=_boom_loader, scope_exclude_dirs=("foo",))
      default = AstMemberExtractor()

      configured_members = await configured.extract_from_dependency("foo")
      default_members = await default.extract_from_dependency("foo")

      assert configured_members == default_members
      assert len(configured_members) > 0
  ```

- [ ] **Step 2: Run the new tests — expect FAIL**

  ```bash
  cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
  pytest tests/extraction/test_members.py -v -k "exclude or delegation_inherits or ignores_configured or default_construction_unchanged"
  ```

  Expected: every new test ERRORS/FAILS with `TypeError: AstMemberExtractor.__init__() got an unexpected keyword argument 'excludes_loader'` (or `'scope_exclude_dirs'`); `test_ast_project_default_construction_unchanged` PASSES (default path unchanged today). If instead you get `ImportError` on `pydocs_mcp.project_toml`, the earlier tasks have not landed — stop and finish them first.

- [ ] **Step 3: Implement the effective-set post-filter**

  Replace the entire contents of `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/python/pydocs_mcp/extraction/strategies/members/ast_extractor.py` with (only the imports, the dataclass fields, `_parse_dir`, and the new `_parent_dir_excluded` helper change; `_module_from_rel_path`, `_dep_sync`, `_parse_files`, and `__all__` are verbatim today's code):

  ```python
  """AstMemberExtractor — static AST parsing via Rust ``parse_py_file``.

  Safe on untrusted dependencies — never executes package code. Used for
  both project source and the static path for dependencies.

  No per-module cap lives on this class:
  :class:`~pydocs_mcp.extraction.config.MembersConfig` exposes
  ``members_per_module_cap`` but enforcement is the ingestion pipeline's
  responsibility (downstream stage, out of scope). The extractor emits
  every member it parses; upstream code truncates.
  """

  from __future__ import annotations

  import asyncio
  import os
  from collections.abc import Callable
  from dataclasses import dataclass
  from pathlib import Path

  from pydocs_mcp.deps import normalize_package_name
  from pydocs_mcp.extraction.config import _EXCLUDED_DIRS

  # Back-compat alias — the canonical implementation lives in
  # extraction/config.py next to _EXCLUDED_DIRS. Kept as a local name
  # so existing imports + tests don't break; new code should import
  # ``path_under_excluded`` directly from extraction.config.
  from pydocs_mcp.extraction.config import path_under_excluded as _path_under_excluded
  from pydocs_mcp.extraction.strategies._dep_helpers import (
      find_installed_distribution,
      find_site_packages_root,
  )
  from pydocs_mcp.models import (
      PROJECT_PACKAGE_NAME,
      ModuleMember,
      ModuleMemberFilterField,
  )
  from pydocs_mcp.project_toml import (
      ProjectExcludes,
      load_project_excludes,
      merge_excludes,
  )


  def _module_from_rel_path(rel: str) -> str:
      """Convert a root-relative ``.py`` path to a dotted module name.

      Strips a trailing ``__init__`` PATH SEGMENT (not substring) — mirrors
      the chunker's ``_module_from_path`` (extraction/strategies/chunkers/
      ast_python.py) so member and chunk sides agree on module identity for
      the same file. The previous ``rel.replace(".__init__", "")`` matched
      the substring anywhere in the dotted path: ``pkg/__init__x.py`` (a
      filename that merely starts with ``__init__``, not the real package
      marker) became ``pkg.__init__x`` -> ``pkgx`` (prefix silently glued to
      the next real module), and a root-level ``__init__.py`` produced the
      bare literal ``__init__`` since it has no leading '.' to match.
      """
      parts = rel.replace(os.sep, ".").removesuffix(".py").split(".")
      if parts and parts[-1] == "__init__":
          parts.pop()
      return ".".join(parts)


  def _parent_dir_excluded(filepath: str, root: Path, effective: ProjectExcludes) -> bool:
      """True iff ``filepath``'s PARENT DIRECTORY falls under ``effective``.

      Directories-only rule (spec §4): the match target is the file's parent
      directory relpath, never the file path itself. Matching the full file
      relpath would let an entry that collides with a file NAME (bare
      ``"conf.py"``, anchored ``"docs/conf.py"``) drop that file's symbols
      here while chunk discovery — which prunes only ``os.walk`` dirnames —
      kept its chunks: a silent chunk/member divergence. Relpath scoping also
      keeps ancestor components ABOVE the walk root out of reach, mirroring
      chunk discovery's in-walk pruning.
      """
      try:
          rel_parent = os.path.relpath(os.path.dirname(filepath), str(root))
      except ValueError:
          # Different drive on Windows — same give-up posture as _parse_files.
          return False
      rel_parent = rel_parent.replace("\\", "/")
      if rel_parent == ".":
          # File directly at the walk root — there is no directory to match.
          return False
      # Bare names (floor folded into ``effective.names`` by merge_excludes)
      # go through the canonical component matcher; anchored entries through
      # ProjectExcludes.matches (spec §7.5).
      return _path_under_excluded(rel_parent, effective.names) or effective.matches(rel_parent)


  @dataclass(frozen=True, slots=True)
  class AstMemberExtractor:
      # Per-run loader (spec D3): read the indexed project's own
      # ``[tool.pydocs-mcp] exclude_dirs`` fresh on every project walk so
      # --watch reindexes pick up TOML edits without a restart. Injected
      # strategy so tests never touch the filesystem.
      excludes_loader: Callable[[Path], ProjectExcludes] = load_project_excludes
      # YAML ``extraction.discovery.project.exclude_dirs`` entries, wired at
      # the write-side composition root (storage/factories.py). The dependency
      # member path (_dep_sync) deliberately ignores BOTH fields — it lists
      # ``dist.files`` directly and has never applied the directory blocklist
      # (spec §2 non-goal).
      scope_exclude_dirs: tuple[str, ...] = ()

      async def extract_from_project(
          self,
          project_dir: Path,
      ) -> tuple[ModuleMember, ...]:
          return await asyncio.to_thread(self._parse_dir, project_dir, PROJECT_PACKAGE_NAME)

      async def extract_from_dependency(
          self,
          dep_name: str,
      ) -> tuple[ModuleMember, ...]:
          return await asyncio.to_thread(self._dep_sync, dep_name)

      def _dep_sync(self, dep_name: str) -> tuple[ModuleMember, ...]:
          """Sync body for dependency extraction — reusable by
          :class:`InspectMemberExtractor` fallback without re-entering an
          event loop."""
          dist = find_installed_distribution(dep_name)
          if dist is None:
              return ()
          py_files = [str(dist.locate_file(f)) for f in (dist.files or []) if str(f).endswith(".py")]
          if not py_files:
              return ()
          root_str = find_site_packages_root(py_files[0])
          package_name = normalize_package_name(dep_name)
          return self._parse_files(package_name, py_files, Path(root_str))

      def _parse_dir(self, root: Path, package: str) -> tuple[ModuleMember, ...]:
          from pydocs_mcp._fast import walk_py_files

          # walk_py_files (both the Rust impl and the Python fallback) has its
          # own hardcoded SKIP_DIRS that doesn't track the canonical Python-side
          # exclusion policy. Post-filter against the EFFECTIVE project set —
          # hardcoded floor ∪ YAML project entries ∪ the project's own
          # pyproject excludes — so the member side sees the SAME exclusion
          # set as chunk discovery. Without this, a checked-in
          # ``vendor/site-packages`` (floor) or a user-excluded ``fixtures/``
          # leaks into the symbol index even though chunk discovery skips it.
          effective = merge_excludes(
              _EXCLUDED_DIRS,
              self.scope_exclude_dirs,
              self.excludes_loader(root),
          )
          candidates = walk_py_files(str(root))
          py_files = [p for p in candidates if not _parent_dir_excluded(p, root, effective)]
          return self._parse_files(package, py_files, root)

      def _parse_files(
          self,
          package: str,
          paths: list[str],
          root: Path,
      ) -> tuple[ModuleMember, ...]:
          # Deferred import so test-time module-level imports of this file don't
          # pull in the Rust native module when not strictly needed.
          from pydocs_mcp._fast import parse_py_file, read_files_parallel

          members: list[ModuleMember] = []
          for filepath, source in read_files_parallel(paths):
              if not source:
                  continue
              try:
                  rel = os.path.relpath(filepath, str(root))
              except ValueError:
                  continue
              module = _module_from_rel_path(rel)
              for symbol in parse_py_file(source):
                  members.append(
                      ModuleMember(
                          metadata={
                              ModuleMemberFilterField.PACKAGE.value: package,
                              ModuleMemberFilterField.MODULE.value: module,
                              ModuleMemberFilterField.NAME.value: symbol.name,
                              ModuleMemberFilterField.KIND.value: symbol.kind,
                              "signature": symbol.signature,
                              "return_annotation": "",
                              "parameters": (),
                              "docstring": symbol.docstring,
                          }
                      )
                  )
          return tuple(members)


  __all__ = ("AstMemberExtractor", "_path_under_excluded")
  ```

  Notes on the shape:
  - `Callable` default `load_project_excludes` on a `slots=True` dataclass is safe — slotted dataclasses move defaults into `__init__`, so the function is never a class-attribute descriptor.
  - `effective.matches(rel_parent)` also covers bare names by contract; the explicit `_path_under_excluded(..., effective.names)` call is kept because spec §7.5 names it as the canonical bare-name matcher (parity with the chunk-side pruning code and the existing `_path_under_excluded` unit tests). The redundancy is O(path components) and intentional.

- [ ] **Step 4: Run the new tests — expect PASS**

  ```bash
  pytest tests/extraction/test_members.py -v
  ```

  Expected: all tests in the file pass (the 8 new ones plus every pre-existing test — `test_ast_project_skips_excluded_dirs_post_walk` (F14) must still pass, now flowing through the merged floor).

- [ ] **Step 5: Run the affected existing suite**

  `tests/test_disable_rust_consumer_binding.py` calls `_parse_dir` directly with a default-constructed extractor (line 115-116) — it must still pass (the default loader silently returns empty excludes on a pyproject-less tmp dir).

  ```bash
  pytest tests/extraction/test_members.py tests/test_disable_rust_consumer_binding.py -q
  ```

  Expected: `.. all passed`, zero failures.

- [ ] **Step 6: Commit the extractor change**

  ```bash
  git add python/pydocs_mcp/extraction/strategies/members/ast_extractor.py tests/extraction/test_members.py
  git commit -m "feat(members): post-filter project member walk by the effective exclude set"
  ```

- [ ] **Step 7: Write the failing composition-root wiring test**

  Append to `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/tests/storage/test_build_project_indexer.py` (after `test_maintenance_callables_run_against_the_db`, line 124). The file's autouse `_offline_factories` fixture already stubs the embedder + LLM client, and the `db_path` fixture creates the schema:

  ```python
  def test_member_extractor_wired_with_yaml_project_excludes(
      db_path: Path,
      tmp_path: Path,
  ) -> None:
      """Spec §7.7 wiring pin (AC-26 groundwork): YAML
      ``extraction.discovery.project.exclude_dirs`` must reach
      AstMemberExtractor.scope_exclude_dirs through the REAL composition
      root — in both the static path and (via static_fallback) inspect mode.
      An implementation that adds the field but forgets the factories.py
      wiring passes the in-test-injection tests and silently never applies
      YAML excludes to member extraction."""
      from pydocs_mcp.storage.factories import build_project_indexer

      overlay = tmp_path / "overlay.yaml"
      overlay.write_text(
          "extraction:\n"
          "  discovery:\n"
          "    project:\n"
          '      exclude_dirs: ["fixtures"]\n',
          encoding="utf-8",
      )
      config = AppConfig.load(explicit_path=overlay)

      static_bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
      assert static_bundle.orchestrator.member_extractor.scope_exclude_dirs == ("fixtures",)

      inspect_bundle = build_project_indexer(config, db_path, use_inspect=True, inspect_depth=None)
      assert inspect_bundle.orchestrator.member_extractor.static_fallback.scope_exclude_dirs == (
          "fixtures",
      )
  ```

- [ ] **Step 8: Run the wiring test — expect FAIL**

  ```bash
  pytest tests/storage/test_build_project_indexer.py::test_member_extractor_wired_with_yaml_project_excludes -v
  ```

  Expected: `AssertionError: assert () == ('fixtures',)` — the field exists (Step 3) but the factory still constructs `AstMemberExtractor()` bare.

- [ ] **Step 9: Wire the factory**

  In `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/python/pydocs_mcp/storage/factories.py`, inside `build_project_indexer` (defined at line 501), replace the member-extractor wiring block at lines 592-605. Current code:

  ```python
      ast_member = AstMemberExtractor()
      members_cfg = config.extraction.members
      depth = inspect_depth if inspect_depth is not None else members_cfg.inspect_depth
      member_extractor = (
          InspectMemberExtractor(
              static_fallback=ast_member,
              depth=depth,
              members_per_module_cap=members_cfg.members_per_module_cap,
              signature_max_chars=members_cfg.signature_max_chars,
              docstring_max_chars=members_cfg.docstring_max_chars,
          )
          if use_inspect
          else ast_member
      )
  ```

  Full modified form of the block:

  ```python
      # YAML project-scope excludes ride on the extractor so member extraction
      # applies the same effective set as chunk discovery — forgetting this
      # wiring is the silent chunk/member divergence spec §7.5 exists to
      # prevent. The pyproject surface needs no wiring here: the extractor's
      # excludes_loader default reads it per run (spec D3, --watch freshness).
      ast_member = AstMemberExtractor(
          scope_exclude_dirs=tuple(config.extraction.discovery.project.exclude_dirs),
      )
      members_cfg = config.extraction.members
      depth = inspect_depth if inspect_depth is not None else members_cfg.inspect_depth
      member_extractor = (
          InspectMemberExtractor(
              static_fallback=ast_member,
              depth=depth,
              members_per_module_cap=members_cfg.members_per_module_cap,
              signature_max_chars=members_cfg.signature_max_chars,
              docstring_max_chars=members_cfg.docstring_max_chars,
          )
          if use_inspect
          else ast_member
      )
  ```

  Nothing else in `build_project_indexer` changes (`config` is already in scope as the first parameter; `AstMemberExtractor` is already imported at line 531 via the deferred `from pydocs_mcp.extraction import ...`).

- [ ] **Step 10: Run the wiring test — expect PASS, then the affected suites**

  ```bash
  pytest tests/storage/test_build_project_indexer.py -v
  pytest tests/extraction/test_members.py tests/storage/ -q
  ```

  Expected: all pass, including the pre-existing bundle-shape/depth tests (the overlay-free tests construct `AstMemberExtractor` with `scope_exclude_dirs=()` — default YAML `exclude_dirs` is `[]`, so `tuple([]) == ()`).

- [ ] **Step 11: Lint/type gates for the touched files, then commit**

  ```bash
  ruff format --check python/pydocs_mcp/extraction/strategies/members/ast_extractor.py python/pydocs_mcp/storage/factories.py tests/extraction/test_members.py tests/storage/test_build_project_indexer.py
  ruff check python/ tests/
  mypy python/pydocs_mcp
  git add python/pydocs_mcp/storage/factories.py tests/storage/test_build_project_indexer.py
  git commit -m "feat(factories): wire YAML project exclude_dirs into AstMemberExtractor"
  ```

  Expected: `ruff format --check` prints nothing (exit 0), `ruff check` reports `All checks passed!`, `mypy` reports `Success: no issues found`.

### Task 7: ContentHashStage conditional fingerprint fold

Implements spec §9, §9.1, §9.2 and decision D10 at the unit level (the end-to-end AC-24 case lands with the e2e task; this task is its groundwork). `ContentHashStage` (`python/pydocs_mcp/extraction/pipeline/stages/content_hash.py:23-35`) currently hashes only the discovered path list via `hash_files`. After this task it folds `exclusion_fingerprint(state.files.effective_excludes, _EXCLUDED_DIRS)` into the hash input **only when that fingerprint is not `None`**; when it is `None` (effective set == bare floor — spec §9.2's conditional no-fold case) the produced `content_hash` is byte-identical to today's pure `hash_files` framing, so every pre-upgrade stored hash keeps matching.

**Depends on:** `project_toml.py`'s `exclusion_fingerprint(excludes, floor) -> str | None` and `EMPTY_PROJECT_EXCLUDES`, and `FileBundle.effective_excludes: ProjectExcludes = EMPTY_PROJECT_EXCLUDES` filled by `FileDiscoveryStage.run` (both from earlier tasks).

**Design constraint discovered in the current code:** `tests/test_disable_rust_consumer_binding.py:37-72` constructs a `FileBundle` **directly** (no discovery stage ran, so `effective_excludes` is `EMPTY_PROJECT_EXCLUDES`) and asserts `content_hash == "sentinel-hash"` — i.e. the stage output equals `hash_files`' return verbatim. `exclusion_fingerprint(EMPTY_PROJECT_EXCLUDES, _EXCLUDED_DIRS)` is NOT `None` (`frozenset() != _EXCLUDED_DIRS`), so the stage must additionally treat `EMPTY_PROJECT_EXCLUDES` — the "discovery never supplied a set" sentinel — as no-fold. Production runs always carry at least the floor (that is what the discoverers return), so this guard only affects directly-constructed states.

**Fold mechanism:** `hash_files`' input framing is owned by the Rust/fallback parity pair (`src/lib.rs:198` / `_fallback.py:58`) and cannot grow a parameter (spec D7 — no Rust change). So the fold wraps the base digest: `md5(base_digest + "\x00" + fingerprint)[:16]`, matching the fallback's non-cryptographic-fingerprint posture and the 16-hex-char digest width. When the fingerprint is `None`, the base digest is returned untouched — byte-identical to today.

**Files:**
- Modify: `python/pydocs_mcp/extraction/pipeline/stages/content_hash.py` (`run` at lines 23-26, `_hash` at lines 28-35, imports at lines 8-15)
- Test: `tests/extraction/test_stages.py` (extend the `── ContentHashStage ──` section, lines 360-385; extend the config import at line 24)

- [ ] **Step 1: Write the failing fold tests**

  In `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/tests/extraction/test_stages.py`:

  Change line 24 from

  ```python
  from pydocs_mcp.extraction.config import ChunkingConfig, ExtractionConfig
  ```

  to

  ```python
  from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, ChunkingConfig, ExtractionConfig
  ```

  and add below it:

  ```python
  from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes, merge_excludes
  ```

  Then insert the following AFTER `test_content_hash_produces_stable_string` (line 385), before the `── PackageBuildStage ──` divider:

  ```python
  # ── ContentHashStage — conditional exclusion-fingerprint fold (spec §9.2) ──


  _FLOOR_ONLY = ProjectExcludes(names=_EXCLUDED_DIRS, anchored=frozenset())


  def _raw_hash_files(paths: list[str]) -> str:
      """Today's framing: hash_files output normalized exactly as the stage
      normalizes it (str passthrough / bytes → hex) — the pre-upgrade value
      every stored packages.content_hash was written with."""
      from pydocs_mcp._fast import hash_files

      result = hash_files(paths)
      return result if isinstance(result, str) else result.hex()


  def _hash_state(tmp_path: Path, f: Path, excludes: ProjectExcludes) -> IngestionState:
      return IngestionState(
          files=FileBundle(
              target=tmp_path,
              target_kind=TargetKind.PROJECT,
              paths=(str(f),),
              effective_excludes=excludes,
          ),
      )


  @pytest.mark.asyncio
  async def test_content_hash_floor_only_is_byte_identical_to_unfolded(tmp_path: Path) -> None:
      """AC-24(a) groundwork: an effective set equal to the bare floor folds
      NOTHING — the hash equals the pure hash_files framing, so an index
      written before the fold existed skips as cached on the first
      post-upgrade run."""
      f = tmp_path / "a.py"
      f.write_text("x = 1\n")

      out = await ContentHashStage().run(_hash_state(tmp_path, f, _FLOOR_ONLY))

      assert out.files.content_hash == _raw_hash_files([str(f)])


  @pytest.mark.asyncio
  async def test_content_hash_empty_sentinel_is_unfolded(tmp_path: Path) -> None:
      """A directly-constructed FileBundle (discovery never ran) carries
      EMPTY_PROJECT_EXCLUDES — the 'no set supplied' sentinel must hash
      exactly like the floor-only case, never fold an empty fingerprint
      (pins tests/test_disable_rust_consumer_binding.py's verbatim-output
      contract)."""
      f = tmp_path / "a.py"
      f.write_text("x = 1\n")
      state = IngestionState(
          files=FileBundle(target=tmp_path, target_kind=TargetKind.PROJECT, paths=(str(f),)),
      )

      out = await ContentHashStage().run(state)

      assert out.files.content_hash == _raw_hash_files([str(f)])


  @pytest.mark.asyncio
  async def test_content_hash_misses_when_exclude_added_paths_unchanged(tmp_path: Path) -> None:
      """AC-24(b) groundwork / §9's necessity argument: adding a user exclude
      must move the hash even when the discovered path list is UNCHANGED
      (member-only directories contribute zero chunk paths — a path-only
      hash would skip as cached and strand their symbols). Both entry kinds
      must move it."""
      f = tmp_path / "a.py"
      f.write_text("x = 1\n")
      stage = ContentHashStage()

      baseline = await stage.run(_hash_state(tmp_path, f, _FLOOR_ONLY))
      with_name = await stage.run(
          _hash_state(
              tmp_path,
              f,
              ProjectExcludes(names=_EXCLUDED_DIRS | {"fixtures"}, anchored=frozenset()),
          )
      )
      with_anchor = await stage.run(
          _hash_state(
              tmp_path,
              f,
              ProjectExcludes(names=_EXCLUDED_DIRS, anchored=frozenset({"docs/generated"})),
          )
      )

      assert with_name.files.content_hash != baseline.files.content_hash
      assert with_anchor.files.content_hash != baseline.files.content_hash


  @pytest.mark.asyncio
  async def test_content_hash_distinct_nonempty_sets_yield_distinct_hashes(
      tmp_path: Path,
  ) -> None:
      """Transitions between two DISTINCT non-empty exclude sets always miss
      (§9.2); the kind-tagged fingerprint keeps a bare name and an anchored
      entry with the same text from colliding."""
      f = tmp_path / "a.py"
      f.write_text("x = 1\n")
      stage = ContentHashStage()

      fixtures_named = await stage.run(
          _hash_state(
              tmp_path,
              f,
              ProjectExcludes(names=_EXCLUDED_DIRS | {"fixtures"}, anchored=frozenset()),
          )
      )
      docs_named = await stage.run(
          _hash_state(
              tmp_path,
              f,
              ProjectExcludes(names=_EXCLUDED_DIRS | {"docs"}, anchored=frozenset()),
          )
      )
      docs_anchored = await stage.run(
          _hash_state(
              tmp_path,
              f,
              ProjectExcludes(names=_EXCLUDED_DIRS, anchored=frozenset({"docs"})),
          )
      )

      assert fixtures_named.files.content_hash != docs_named.files.content_hash
      assert docs_named.files.content_hash != docs_anchored.files.content_hash


  @pytest.mark.asyncio
  async def test_content_hash_floor_duplicate_entries_hash_like_floor_only(
      tmp_path: Path,
  ) -> None:
      """AC-24(d) groundwork / §3.3 no-op rule: entries that only duplicate
      floor names leave the effective set equal to the floor — no fold, no
      spurious cache miss; still byte-identical to the unfolded framing."""
      f = tmp_path / "a.py"
      f.write_text("x = 1\n")
      dup_only = merge_excludes(_EXCLUDED_DIRS, (".git", "venv"), EMPTY_PROJECT_EXCLUDES)

      out = await ContentHashStage().run(_hash_state(tmp_path, f, dup_only))

      assert out.files.content_hash == _raw_hash_files([str(f)])
  ```

- [ ] **Step 2: Run the new tests — expect the two fold tests to FAIL**

  ```bash
  cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
  pytest tests/extraction/test_stages.py -v -k content_hash
  ```

  Expected: `test_content_hash_floor_only_is_byte_identical_to_unfolded`, `test_content_hash_empty_sentinel_is_unfolded`, and `test_content_hash_floor_duplicate_entries_hash_like_floor_only` PASS trivially (today's stage never folds anything); `test_content_hash_misses_when_exclude_added_paths_unchanged` and `test_content_hash_distinct_nonempty_sets_yield_distinct_hashes` FAIL with `AssertionError` on `!=` (all hashes are currently equal because the stage ignores `effective_excludes`).

- [ ] **Step 3: Implement the conditional fold**

  Replace the entire contents of `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/python/pydocs_mcp/extraction/pipeline/stages/content_hash.py` with:

  ```python
  """ContentHashStage — fills ``state.files.content_hash``, the package-level hash.

  The package hash drives whole-package cache invalidation. Per-node
  ``DocumentNode.content_hash`` values are computed inside each chunker
  and ride on the trees instead — they don't flow through state.
  """

  from __future__ import annotations

  import asyncio
  import hashlib
  from dataclasses import dataclass, replace
  from typing import Any

  from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
  from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
  from pydocs_mcp.extraction.serialization import stage_registry
  from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, exclusion_fingerprint


  @stage_registry.register("content_hash")
  @dataclass(frozen=True, slots=True)
  class ContentHashStage:
      name: str = "content_hash"

      async def run(self, state: IngestionState) -> IngestionState:
          excludes = state.files.effective_excludes
          # Fold the fingerprint of the set the SAME run's discovery walk
          # actually pruned against (state-carried, never re-derived — spec
          # D10: a mid---watch pyproject save landing between the two stages
          # must not fold a set the walk didn't use). EMPTY_PROJECT_EXCLUDES
          # is the "discovery never supplied a set" sentinel (directly
          # constructed states in tests / legacy callers): folding its empty
          # fingerprint would silently change every such hash, so it is
          # no-fold like the floor-only case — which exclusion_fingerprint
          # itself collapses to None (spec §9.2: the conditional fold keeps
          # every pre-upgrade stored hash valid).
          fingerprint = (
              None
              if excludes == EMPTY_PROJECT_EXCLUDES
              else exclusion_fingerprint(excludes, _EXCLUDED_DIRS)
          )
          h = await asyncio.to_thread(self._hash, list(state.files.paths), fingerprint)
          new_files = replace(state.files, content_hash=h)
          return replace(state, files=new_files)

      def _hash(self, paths: list[str], fingerprint: str | None) -> str:
          # Deferred so _fast's native/fallback choice is resolved lazily.
          from pydocs_mcp._fast import hash_files

          result = hash_files(paths)
          # hash_files may return str (fallback) or bytes (some native builds).
          # Normalize so downstream consumers see a stable str regardless.
          base = result if isinstance(result, str) else result.hex()
          if fingerprint is None:
              # No user excludes → byte-identical to the historical framing
              # (spec §9.2 — upgrade is free for exclude-less deployments).
              return base
          # Fold via digest-of-digest: hash_files' input framing is owned by
          # the Rust/fallback parity pair and cannot grow a parameter (spec
          # D7 — no Rust change), so the fingerprint wraps the base digest
          # instead of entering it. md5 matches the fallback's
          # non-cryptographic cache-fingerprint posture; [:16] matches the
          # base digest width.
          folded = hashlib.md5(
              f"{base}\x00{fingerprint}".encode(),
              usedforsecurity=False,
          )
          return folded.hexdigest()[:16]

      @classmethod
      def from_dict(cls, data: dict, context: Any) -> ContentHashStage:
          return cls()

      def to_dict(self) -> dict:
          return {"type": "content_hash"}


  __all__ = ("ContentHashStage",)
  ```

- [ ] **Step 4: Run the fold tests — expect PASS**

  ```bash
  pytest tests/extraction/test_stages.py -v -k content_hash
  ```

  Expected: all 6 `content_hash` tests pass (the 5 new ones plus the pre-existing `test_content_hash_produces_stable_string`).

- [ ] **Step 5: Run the affected existing suites**

  The verbatim-output contract of the disable-rust test and the stage-registry round-trip tests both touch this stage:

  ```bash
  pytest tests/extraction/test_stages.py tests/test_disable_rust_consumer_binding.py tests/extraction/test_factories.py -q
  ```

  Expected: all pass. In particular `test_content_hash_stage_uses_fallback_after_disable_rust` still asserts `content_hash == "sentinel-hash"` — the `EMPTY_PROJECT_EXCLUDES` sentinel guard is what keeps it green.

- [ ] **Step 6: Lint/type gates for the touched files, then commit**

  ```bash
  ruff format --check python/pydocs_mcp/extraction/pipeline/stages/content_hash.py tests/extraction/test_stages.py
  ruff check python/ tests/
  mypy python/pydocs_mcp
  git add python/pydocs_mcp/extraction/pipeline/stages/content_hash.py tests/extraction/test_stages.py
  git commit -m "feat(cache): conditionally fold the exclusion fingerprint into the package content hash"
  ```

  Expected: `ruff format --check` prints nothing (exit 0), `ruff check` reports `All checks passed!`, `mypy` reports `Success: no issues found`.

---

### Task 8: Decision-mining sources honor `CaptureContext.excluded` (spec §7.8, D8; AC-21)

**Prerequisites:** `python/pydocs_mcp/project_toml.py` exists with `ProjectExcludes`, `EMPTY_PROJECT_EXCLUDES` (Task 1), and `FileBundle` already carries `effective_excludes: ProjectExcludes = EMPTY_PROJECT_EXCLUDES` filled by `FileDiscoveryStage` (earlier task). If executing tasks out of order, land those first.

Three decision-mining sources read project files directly from `ctx.project_root`, bypassing `ProjectFileDiscoverer`: `adr_files` (`_adr_paths`, `python/pydocs_mcp/extraction/decisions/sources/adr_files.py:61-68`), `docs_prose` (`_candidate_files`, `docs_prose.py:67-73`), and `changelog` (`_changelog_paths`, `changelog.py:62-67`). Each filters its candidates by the candidate file's **parent-directory** relpath against the effective project set (the §4 directories-only rule). `inline_markers` (mines already-filtered trees) and `commit_messages` (reads git history) are untouched. The set arrives on `CaptureContext.excluded`, filled by `MineDecisionsStage._build_context` from `state.files.effective_excludes` — decision mining performs NO TOML read of its own.

All three source globs/probes are non-recursive against fixed conventional directories (`docs/adr`, `docs`, etc.), so "filter each candidate by its parent-directory relpath" collapses to "check the conventional directory's relpath once, before globbing" — the parent of every `docs/adr/*.md` candidate IS `docs/adr`.

**Files:**
- Modify: `python/pydocs_mcp/extraction/decisions/_types.py` (CaptureContext at :56-63)
- Modify: `python/pydocs_mcp/extraction/pipeline/stages/decisions/mine_decisions.py` (`_build_context` at :60-74)
- Modify: `python/pydocs_mcp/extraction/decisions/sources/adr_files.py` (`mine` :51-58, `_adr_paths` :61-68)
- Modify: `python/pydocs_mcp/extraction/decisions/sources/docs_prose.py` (`mine` :50-64, `_candidate_files` :67-73)
- Modify: `python/pydocs_mcp/extraction/decisions/sources/changelog.py` (`mine` :50-59, `_changelog_paths` :62-70)
- Test: `tests/extraction/test_decision_sources_markers_adr.py`
- Test: `tests/extraction/test_decision_sources_git_docs.py`
- Test: `tests/extraction/pipeline/test_decision_stages.py`

- [ ] **Step 1: Write the failing `adr_files` tests.** Append to `tests/extraction/test_decision_sources_markers_adr.py` (this file's conventions: bare `async def test_*` — `asyncio_mode = "auto"` in pyproject.toml:193 — a module-level `_cfg()` helper, `tmp_path` fixtures). Add one import at the top of the file, next to the existing `pydocs_mcp` imports (after line 22, `from pydocs_mcp.retrieval.config.models import DecisionCaptureConfig`):

```python
from pydocs_mcp.project_toml import ProjectExcludes
```

Then append at the end of the file:

```python
# ── adr_files × effective excludes (spec 7.8, AC-21) ─────────────────────────


def _two_adr_dirs(tmp_path) -> None:
    """One ADR under docs/adr/ and one under the root-level adr/ convention."""
    docs_adr = tmp_path / "docs" / "adr"
    docs_adr.mkdir(parents=True)
    (docs_adr / "0001-docs-side.md").write_text(
        "# 1. Docs-side decision\n\nStatus: Accepted\n\n## Decision\nSQLite.\n"
    )
    root_adr = tmp_path / "adr"
    root_adr.mkdir()
    (root_adr / "0001-root-side.md").write_text(
        "# 1. Root-side decision\n\nStatus: Accepted\n\n## Decision\nFTS5.\n"
    )


async def test_adr_source_skips_excluded_parent_dirs(tmp_path) -> None:
    _two_adr_dirs(tmp_path)
    excluded = ProjectExcludes(names=frozenset({"docs"}), anchored=frozenset())
    raws = await AdrFilesSource().mine(
        CaptureContext(project_root=tmp_path, trees=(), config=_cfg(), excluded=excluded)
    )
    # bare "docs" silences docs/adr; the root-level adr/ fixture still mines.
    assert [r.title for r in raws] == ["Root-side decision"]


async def test_adr_source_anchored_entry_leaves_other_candidates(tmp_path) -> None:
    _two_adr_dirs(tmp_path)
    excluded = ProjectExcludes(names=frozenset(), anchored=frozenset({"docs/generated"}))
    raws = await AdrFilesSource().mine(
        CaptureContext(project_root=tmp_path, trees=(), config=_cfg(), excluded=excluded)
    )
    # anchored "docs/generated" matches neither docs/adr nor adr — all mine.
    assert sorted(r.title for r in raws) == ["Docs-side decision", "Root-side decision"]


async def test_adr_source_default_excluded_is_identity(tmp_path) -> None:
    _two_adr_dirs(tmp_path)
    raws = await AdrFilesSource().mine(
        CaptureContext(project_root=tmp_path, trees=(), config=_cfg())
    )
    # A directly-constructed context (no excluded kwarg) behaves exactly as today.
    assert sorted(r.title for r in raws) == ["Docs-side decision", "Root-side decision"]
```

- [ ] **Step 2: Run the new tests — expect FAIL on the missing field.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
pytest tests/extraction/test_decision_sources_markers_adr.py -v
```

Expected: the two tests passing `excluded=` fail with `TypeError: CaptureContext.__init__() got an unexpected keyword argument 'excluded'`; `test_adr_source_default_excluded_is_identity` passes (it exercises today's behavior). All pre-existing tests in the file still pass.

- [ ] **Step 3: Add the `excluded` field to `CaptureContext`.** In `python/pydocs_mcp/extraction/decisions/_types.py`, add one import after line 19 (`from pydocs_mcp.storage.decision_record import DecisionEvidence`) — `project_toml` imports only stdlib + `pydocs_mcp.exceptions`, so no cycle:

```python
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes
```

Replace the `CaptureContext` dataclass (currently lines 56-63) with:

```python
@dataclass(frozen=True, slots=True)
class CaptureContext:
    """Everything a source may read; sources never touch the DB or network."""

    project_root: Path
    trees: tuple[DocumentNode, ...]
    config: DecisionCaptureConfig
    git_log_text: str = ""  # a later slice fills this; "" = no git history
    # Effective project exclusion set (floor ∪ YAML ∪ TOML), threaded from the
    # SAME run's discovery walk via ``state.files.effective_excludes`` — never
    # re-derived here, so a mid-watch pyproject.toml save cannot make mining
    # and discovery disagree (spec D8). The empty default keeps every
    # directly-constructed context behaving exactly as before this field.
    excluded: ProjectExcludes = EMPTY_PROJECT_EXCLUDES
```

Run again: `pytest tests/extraction/test_decision_sources_markers_adr.py -v`. Expected: `test_adr_source_skips_excluded_parent_dirs` now fails with `AssertionError` (both titles mined — the source still ignores `excluded`); the anchored and default tests pass.

- [ ] **Step 4: Implement the `adr_files` filter.** In `python/pydocs_mcp/extraction/decisions/sources/adr_files.py`, add one import after line 23 (the close of the `pydocs_mcp.extraction.decisions._types` import block):

```python
from pydocs_mcp.project_toml import ProjectExcludes
```

Replace `mine` (lines 51-58) and `_adr_paths` (lines 61-68) with:

```python
    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]:
        tree_qnames = _collect_tree_qnames(ctx)
        raws: list[RawDecision] = []
        for path in _adr_paths(ctx.project_root, ctx.excluded):
            raw = _parse_adr(path, ctx.project_root, tree_qnames)
            if raw is not None:
                raws.append(raw)
        return tuple(raws)
```

```python
def _adr_paths(project_root: Path, excluded: ProjectExcludes) -> list[Path]:
    """Every ``*.md`` under a conventional, non-excluded ADR directory, sorted.

    The glob is non-recursive, so each candidate file's parent directory IS
    ``rel`` — checking the directory once here is the directories-only rule
    (spec §4) applied to this source's candidates.
    """
    paths: list[Path] = []
    for rel in _ADR_DIRS:
        if excluded.matches(rel):
            continue
        base = project_root / rel
        if base.is_dir():
            paths.extend(sorted(base.glob("*.md")))
    return paths
```

Run: `pytest tests/extraction/test_decision_sources_markers_adr.py -v` — expect ALL tests in the file to PASS.

- [ ] **Step 5: Write the failing `changelog` + `docs_prose` tests.** Append to `tests/extraction/test_decision_sources_git_docs.py` (conventions: bare `async def test_*`, module `_ctx()` helper — the new tests construct `CaptureContext` directly because `_ctx` has no `excluded` parameter and the shared helper should not churn). Add one import after line 26 (`from pydocs_mcp.retrieval.config.models import DecisionCaptureConfig`):

```python
from pydocs_mcp.project_toml import ProjectExcludes
```

Append at the end of the file:

```python
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
```

Run: `pytest tests/extraction/test_decision_sources_git_docs.py -v`. Expected FAIL: `test_changelog_skips_excluded_docs_dir` and `test_docs_prose_skips_excluded_docs_glob` fail with `AssertionError` (two raws mined instead of one — the sources ignore `excluded`); the anchored/default tests pass.

- [ ] **Step 6: Implement the `changelog` filter.** In `python/pydocs_mcp/extraction/decisions/sources/changelog.py`, add one import after line 22 (`from pydocs_mcp.extraction.decisions.sources.commit_messages import qualifies`):

```python
from pydocs_mcp.project_toml import ProjectExcludes
```

Replace `mine` (lines 50-59) and `_changelog_paths` (lines 62-70) with:

```python
    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]:
        raws: list[RawDecision] = []
        for path in _changelog_paths(ctx.project_root, ctx.excluded):
            locator_base = _rel_locator(path, ctx.project_root)
            body = path.read_text(encoding="utf-8", errors="replace")
            for entry in _split_entries(body):
                raw = _entry_to_raw(entry, locator_base)
                if raw is not None:
                    raws.append(raw)
        return tuple(raws)
```

```python
def _changelog_paths(project_root: Path, excluded: ProjectExcludes) -> list[Path]:
    """Existing changelog files at the root and under non-excluded dirs, sorted.

    ``rel_dir`` is each candidate file's parent directory (the directories-only
    rule, spec §4); ``"."`` is the walk root itself and is never excludable.
    """
    paths: list[Path] = []
    for rel_dir in _CHANGELOG_DIRS:
        if rel_dir != "." and excluded.matches(rel_dir):
            continue
        for name in _CHANGELOG_NAMES:
            candidate = project_root / rel_dir / name
            if candidate.is_file():
                paths.append(candidate)
    return sorted(set(paths))
```

- [ ] **Step 7: Implement the `docs_prose` filter.** In `python/pydocs_mcp/extraction/decisions/sources/docs_prose.py`, add one import after line 27 (`from pydocs_mcp.extraction.decisions.sources.commit_messages import qualifies`):

```python
from pydocs_mcp.project_toml import ProjectExcludes
```

In `mine` (line 53), change the candidate call — the full final `mine`:

```python
    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]:
        cfg = ctx.config.docs_prose
        tree_qnames = _collect_tree_qnames(ctx)
        candidates = _candidate_files(ctx.project_root, ctx.excluded)
        selected, over_cap = candidates[: cfg.max_files], candidates[cfg.max_files :]
        raws: list[RawDecision] = []
        skipped_size = 0
        for path in selected:
            body = _read_within_cap(path, cfg.max_kb_per_file)
            if body is None:
                skipped_size += 1
                continue
            raws.extend(_mine_file(body, path, ctx.project_root, tree_qnames))
        _log_drops(len(over_cap), skipped_size)
        return tuple(raws)
```

Replace `_candidate_files` (lines 67-73) with:

```python
def _candidate_files(project_root: Path, excluded: ProjectExcludes) -> list[Path]:
    """Root prose files that exist, then ``docs/*.md`` sorted — deterministic order.

    Root files sit directly at the walk root (no excludable parent directory);
    the ``docs/`` glob is non-recursive, so its files' parent directory IS
    ``docs`` — one check drops the whole glob (directories-only rule, spec §4).
    """
    files = [project_root / name for name in _ROOT_DOCS if (project_root / name).is_file()]
    docs_dir = project_root / _DOCS_GLOB_DIR
    if docs_dir.is_dir() and not excluded.matches(_DOCS_GLOB_DIR):
        files.extend(sorted(docs_dir.glob("*.md")))
    return files
```

Run: `pytest tests/extraction/test_decision_sources_git_docs.py -v` — expect ALL tests in the file to PASS.

- [ ] **Step 8: Write the failing `MineDecisionsStage._build_context` tests.** Append to `tests/extraction/pipeline/test_decision_stages.py` (conventions: bare `async def test_*`, the module's `_state()` and `_cfg()` helpers; the new tests construct `IngestionState` directly because `_state()` does not accept `effective_excludes`). Add one import after line 36 (`from pydocs_mcp.models import ChunkOrigin`):

```python
from pydocs_mcp.project_toml import ProjectExcludes
```

Append at the end of the file:

```python
# ── MineDecisionsStage × effective excludes (spec 7.8, D8; AC-21) ───────────


def _excluded_state(root: Path, excludes: ProjectExcludes) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target=root,
            target_kind=TargetKind.PROJECT,
            package_name="__project__",
            root=root,
            effective_excludes=excludes,
        ),
        chunks=ChunkBundle(trees=()),
    )


async def test_build_context_carries_effective_excludes(tmp_path: Path) -> None:
    excludes = ProjectExcludes(names=frozenset({"docs"}), anchored=frozenset())
    stage = MineDecisionsStage(config=_cfg())
    ctx = await stage._build_context(_excluded_state(tmp_path, excludes), tmp_path)
    # The stage threads the SAME object the discovery walk pruned against —
    # no re-derivation, no second TOML read (spec 9.1).
    assert ctx.excluded is excludes


async def test_mine_run_skips_adr_files_under_excluded_dir(tmp_path: Path) -> None:
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-x.md").write_text("# 1. Use SQLite\n\nStatus: Accepted\n\n## Decision\nYes.\n")
    excludes = ProjectExcludes(names=frozenset({"docs"}), anchored=frozenset())
    out = await MineDecisionsStage(config=_cfg(sources=["adr_files"])).run(
        _excluded_state(tmp_path, excludes)
    )
    # An excluded ADR directory yields no decision records at all (AC-21) —
    # so nothing downstream (get_why / search --kind decision / GOVERNS) can
    # resurface it.
    assert out.decisions == ()


async def test_mine_run_default_bundle_still_mines_adr_files(tmp_path: Path) -> None:
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-x.md").write_text("# 1. Use SQLite\n\nStatus: Accepted\n\n## Decision\nYes.\n")
    out = await MineDecisionsStage(config=_cfg(sources=["adr_files"])).run(
        _state(trees=(), root=tmp_path)
    )
    assert len(out.decisions) == 1  # empty-default bundle → identical to today
```

Run: `pytest tests/extraction/pipeline/test_decision_stages.py -v`. Expected FAIL: `test_build_context_carries_effective_excludes` with `AssertionError` (ctx.excluded is the module default `EMPTY_PROJECT_EXCLUDES`, not the injected object) and `test_mine_run_skips_adr_files_under_excluded_dir` with `AssertionError` (one decision mined); the default-bundle test passes.

- [ ] **Step 9: Fill `excluded` in `_build_context`.** In `python/pydocs_mcp/extraction/pipeline/stages/decisions/mine_decisions.py`, replace `_build_context` (lines 60-74) with:

```python
    async def _build_context(self, state: IngestionState, root: Path) -> CaptureContext:
        """Read the bounded git log ONCE, then bundle the source input."""
        git_cfg = self.config.commit_messages
        git_log_text = await asyncio.to_thread(
            read_git_log,
            root,
            max_commits=git_cfg.max_commits,
            timeout_seconds=git_cfg.timeout_seconds,
        )
        return CaptureContext(
            project_root=root,
            trees=state.chunks.trees,
            config=self.config,
            git_log_text=git_log_text,
            # The exact set the same run's discovery walk pruned against
            # (spec D8/9.1) — never re-derived, never a second TOML read.
            excluded=state.files.effective_excludes,
        )
```

No import changes needed (`CaptureContext` is already imported). Run: `pytest tests/extraction/pipeline/test_decision_stages.py -v` — expect ALL tests to PASS.

- [ ] **Step 10: Run the affected existing suites.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
pytest tests/extraction/ tests/application/test_capture_decisions_persistence.py \
       tests/application/test_decision_service.py tests/application/test_decision_wiring.py -q
ruff check python/pydocs_mcp/extraction/decisions/ python/pydocs_mcp/extraction/pipeline/ tests/
ruff format --check python/ tests/ benchmarks/
mypy python/pydocs_mcp
```

Expected: all green. If `ruff format --check` flags the new code, run `ruff format python/ tests/` and re-check.

- [ ] **Step 11: Commit.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
git add python/pydocs_mcp/extraction/decisions/_types.py \
        python/pydocs_mcp/extraction/decisions/sources/adr_files.py \
        python/pydocs_mcp/extraction/decisions/sources/docs_prose.py \
        python/pydocs_mcp/extraction/decisions/sources/changelog.py \
        python/pydocs_mcp/extraction/pipeline/stages/decisions/mine_decisions.py \
        tests/extraction/test_decision_sources_markers_adr.py \
        tests/extraction/test_decision_sources_git_docs.py \
        tests/extraction/pipeline/test_decision_stages.py
git commit -m "feat(decisions): filter file-reading decision sources by the effective exclusion set"
```

(No `Co-Authored-By` trailer, no `--author` — repo authorship policy.)

Note: the full index-level half of AC-21 (a tmp project whose `pyproject.toml` excludes its ADR directory yields zero `decision_records` — nothing for `get_why` / `search --kind decision` / `governed_by` to surface) rides with Task 11 as `test_ac21_pyproject_excluded_adr_dir_yields_no_decision_records` in `tests/extraction/test_end_to_end_excludes.py` (Task 11 Step 15) — it needs the whole discovery→state→mine chain wired, which is complete only after every component task lands.

---

### Task 9: `deps.py` manifest walk + `StaticDependencyResolver` excludes (spec §7.9, D9; AC-22)

**Prerequisites:** `python/pydocs_mcp/project_toml.py` exists with `ProjectExcludes`, `EMPTY_PROJECT_EXCLUDES`, `load_project_excludes`, `merge_excludes` (Task 1), and `DiscoveryScopeConfig.exclude_dirs` exists (YAML-field task) — the `factories.py` wiring in Step 7 reads it.

`list_dependency_manifest_files` (`python/pydocs_mcp/deps.py:49-65`) walks the whole project tree pruning only the hardcoded `_SKIP_DIRS` (`deps.py:13-30`), and feeds `discover_declared_dependencies` (`deps.py:166-180`). A manifest inside a user-excluded directory (fixture projects routinely carry fixture `pyproject.toml`s) would otherwise still contribute packages to the dependency index. `StaticDependencyResolver` (`python/pydocs_mcp/extraction/strategies/dependencies.py`) gains the same two fields as `ProjectFileDiscoverer` — `excludes_loader` + `scope_exclude_dirs` — computes floor ∪ YAML-project ∪ TOML per `resolve()` call (the per-run posture of D3; nothing fingerprints this set, so a fresh per-call read is safe), and passes it down.

Production construction sites of `StaticDependencyResolver(` (verified by grep): exactly one — `python/pydocs_mcp/storage/factories.py:609`. The two test-side sites (`tests/integration/test_self_index_resolution_rate.py:112`, `tests/extraction/test_end_to_end.py:132`) keep defaults and need no edit (the default loader silently returns empty excludes for any project without a `[tool.pydocs-mcp]` table, per §8 row 1).

**Files:**
- Modify: `python/pydocs_mcp/deps.py` (`list_dependency_manifest_files` :49-65, `discover_declared_dependencies` :166-180)
- Modify: `python/pydocs_mcp/extraction/strategies/dependencies.py` (whole `StaticDependencyResolver`)
- Modify: `python/pydocs_mcp/storage/factories.py` (:609)
- Test: `tests/test_deps.py`
- Test: `tests/extraction/test_dependencies.py`

- [ ] **Step 1: Write the failing manifest-walk tests.** In `tests/test_deps.py` (conventions: plain classes of sync tests, `tmp_path`-based fixtures, `os.sep`-aware path assertions), replace the import block (lines 1-5) with:

```python
"""Tests for recursive dependency resolution in deps.py."""

import os
import pytest
from pydocs_mcp.deps import discover_declared_dependencies, list_dependency_manifest_files
from pydocs_mcp.project_toml import ProjectExcludes
```

Append at the end of the file:

```python
@pytest.fixture
def excluded_manifest_tree(tmp_path):
    """Root manifest plus manifests inside candidate-excluded directories.

    Layout exercises every AC-22 case: a root-level ``fixtures/`` (bare-name
    target), a nested ``services/fixtures/`` (anchored target), and a sibling
    ``other/fixtures/`` (must survive the anchored entry).
    """
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi"]\n')
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "pyproject.toml").write_text('[project]\ndependencies = ["leaky_dep"]\n')
    nested = tmp_path / "services" / "fixtures"
    nested.mkdir(parents=True)
    (nested / "requirements.txt").write_text("nested_leak\n")
    sibling = tmp_path / "other" / "fixtures"
    sibling.mkdir(parents=True)
    (sibling / "requirements.txt").write_text("sibling_dep\n")
    return tmp_path


class TestManifestWalkExcludes:
    def test_empty_excludes_keeps_todays_output(self, excluded_manifest_tree):
        default = list_dependency_manifest_files(str(excluded_manifest_tree))
        explicit = list_dependency_manifest_files(
            str(excluded_manifest_tree), ProjectExcludes(frozenset(), frozenset())
        )
        assert sorted(default) == sorted(explicit)
        assert any("fixtures" in p.split(os.sep) for p in default)  # regression

    def test_bare_name_prunes_matching_dirs_at_every_depth(self, excluded_manifest_tree):
        excludes = ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())
        found = list_dependency_manifest_files(str(excluded_manifest_tree), excludes)
        assert not any("fixtures" in p.split(os.sep) for p in found)
        assert any(os.path.basename(p) == "pyproject.toml" for p in found)  # root kept

    def test_anchored_entry_prunes_only_its_own_path(self, excluded_manifest_tree):
        excludes = ProjectExcludes(names=frozenset(), anchored=frozenset({"services/fixtures"}))
        found = list_dependency_manifest_files(str(excluded_manifest_tree), excludes)
        rels = [os.path.relpath(p, str(excluded_manifest_tree)) for p in found]
        assert os.path.join("services", "fixtures", "requirements.txt") not in rels
        assert os.path.join("other", "fixtures", "requirements.txt") in rels  # sibling survives
        assert os.path.join("fixtures", "pyproject.toml") in rels  # root-level fixtures survives

    def test_discover_passthrough_drops_deps_from_excluded_manifests(
        self, excluded_manifest_tree
    ):
        excludes = ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())
        deps = discover_declared_dependencies(str(excluded_manifest_tree), excludes)
        assert "leaky_dep" not in deps
        assert "nested_leak" not in deps
        assert "sibling_dep" not in deps
        assert "fastapi" in deps  # the root manifest still contributes
```

- [ ] **Step 2: Run the new tests — expect FAIL on the missing parameter.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
pytest tests/test_deps.py -v
```

Expected: the three tests passing a second argument fail with `TypeError: list_dependency_manifest_files() takes 1 positional argument but 2 were given` (and `discover_declared_dependencies() takes 1 positional argument but 2 were given` for the passthrough test); `test_empty_excludes_keeps_todays_output` also fails on the two-arg call. All pre-existing tests in the file still pass.

- [ ] **Step 3: Implement the `deps.py` changes.** In `python/pydocs_mcp/deps.py`, add one import after line 8 (`from pathlib import Path`) — `project_toml` is a stdlib-only peer, no cycle:

```python
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes
```

Replace `list_dependency_manifest_files` (lines 49-65) with — note the pruning matches the project walk (spec §7.3): bare names via the union frozenset, anchored via `matches` on the walk-root-relative directory path:

```python
def list_dependency_manifest_files(
    root: str,
    excludes: ProjectExcludes = EMPTY_PROJECT_EXCLUDES,
) -> list[str]:
    """Recursively find all pyproject.toml and requirements*.txt under root.

    Prunes _SKIP_DIRS plus the caller-supplied effective exclusion set, so
    virtualenvs, build artefacts, and user-excluded directories are never
    descended into — a manifest inside an excluded directory contributes no
    packages to the dependency index (spec D9).
    """
    found: list[str] = []
    skip_names = _SKIP_DIRS | excludes.names
    root_path = Path(root)
    # os.walk is the right API for in-place dirnames pruning; Path.rglob has no
    # equivalent skip-subtree mechanism (would descend into .venv/ etc).
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root_path).as_posix()
        # Prune in-place so os.walk won't descend into skipped directories:
        # bare names via O(1) frozenset membership, anchored entries via the
        # walk-root-relative POSIX directory path (spec §4 semantics).
        dirnames[:] = [
            d
            for d in dirnames
            if d not in skip_names and not excludes.matches(_child_relpath(rel_dir, d))
        ]
        for fname in filenames:
            if fname == "pyproject.toml" or (
                fname.startswith("requirements") and fname.endswith(".txt")
            ):
                found.append(str(Path(dirpath) / fname))
    return found


def _child_relpath(rel_dir: str, name: str) -> str:
    """Walk-root-relative POSIX path of a child directory; the root's own
    rel_dir is ``"."`` (never itself excludable)."""
    return name if rel_dir == "." else f"{rel_dir}/{name}"
```

Replace `discover_declared_dependencies` (lines 166-180) with:

```python
def discover_declared_dependencies(
    project_dir: str,
    excludes: ProjectExcludes = EMPTY_PROJECT_EXCLUDES,
) -> list[str]:
    """Return sorted, deduplicated dependency names found anywhere under project_dir.

    Scans all pyproject.toml and requirements*.txt in the entire directory tree,
    skipping virtualenvs, build artefacts, and the caller's effective exclusion
    set (spec D9). Version specifiers and extras are stripped.
    """
    all_deps: set[str] = set()
    for path in list_dependency_manifest_files(project_dir, excludes):
        fname = Path(path).name
        if fname == "pyproject.toml":
            all_deps.update(parse_pyproject_dependencies(path))
        else:
            all_deps.update(parse_requirements_file(path))
    all_deps.discard("")
    return sorted(all_deps)
```

Run: `pytest tests/test_deps.py tests/test_deps_extended.py tests/test_deps_optional_groups.py tests/test_deps_pep440_specs.py tests/test_deps_project_scripts.py -v` — expect ALL to PASS.

- [ ] **Step 4: Write the failing resolver tests.** Append to `tests/extraction/test_dependencies.py` (conventions: explicit `@pytest.mark.asyncio` on async tests, `tmp_path: Path` annotations). Add one import after line 22 (`from pydocs_mcp.extraction.strategies.dependencies import StaticDependencyResolver`):

```python
from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes
```

Append at the end of the file:

```python
# ── excludes_loader + scope_exclude_dirs (spec 7.9, D9; AC-22) ───────────────


def _tree_with_fixture_manifest(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests"]\n'
    )
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "pyproject.toml").write_text('[project]\ndependencies = ["leaky_dep"]\n')
    return tmp_path


@pytest.mark.asyncio
async def test_resolver_applies_fake_loader_excludes(tmp_path: Path) -> None:
    """A manifest inside a TOML-excluded directory contributes no packages."""
    root = _tree_with_fixture_manifest(tmp_path)

    def fake_loader(_root: Path) -> ProjectExcludes:
        return ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())

    names = await StaticDependencyResolver(excludes_loader=fake_loader).resolve(root)
    assert "leaky_dep" not in names
    assert "requests" in names


@pytest.mark.asyncio
async def test_resolver_applies_scope_exclude_dirs(tmp_path: Path) -> None:
    """YAML project-scope entries reach the manifest walk without a TOML read."""
    root = _tree_with_fixture_manifest(tmp_path)

    def empty_loader(_root: Path) -> ProjectExcludes:
        return EMPTY_PROJECT_EXCLUDES

    resolver = StaticDependencyResolver(
        excludes_loader=empty_loader, scope_exclude_dirs=("fixtures",)
    )
    names = await resolver.resolve(root)
    assert "leaky_dep" not in names
    assert "requests" in names


@pytest.mark.asyncio
async def test_resolver_default_construction_unchanged(tmp_path: Path) -> None:
    """No [tool.pydocs-mcp] table + no scope entries → today's behavior."""
    root = _tree_with_fixture_manifest(tmp_path)
    names = await StaticDependencyResolver().resolve(root)
    assert "leaky_dep" in names
    assert "requests" in names
```

Run: `pytest tests/extraction/test_dependencies.py -v`. Expected FAIL: the first two new tests with `TypeError: StaticDependencyResolver.__init__() got an unexpected keyword argument 'excludes_loader'`; `test_resolver_default_construction_unchanged` passes.

- [ ] **Step 5: Implement the resolver fields.** Replace the entire contents of `python/pydocs_mcp/extraction/strategies/dependencies.py` with:

```python
"""Static dependency resolver — wraps ``deps.discover_declared_dependencies`` (spec §10).

:class:`StaticDependencyResolver` is the only :class:`DependencyResolver`
strategy shipped. Today's :mod:`pydocs_mcp.deps` is already clean — pure
functions, no I/O beyond file reads — so we wrap rather than rewrite it.
Alternative strategies (poetry.lock / pdm.lock / uv resolution / graph-aware
dependency walking) are future work.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.project_toml import (
    ProjectExcludes,
    load_project_excludes,
    merge_excludes,
)


@dataclass(frozen=True, slots=True)
class StaticDependencyResolver:
    """Implements the :class:`DependencyResolver` Protocol via
    :func:`pydocs_mcp.deps.discover_declared_dependencies`.

    Scans ``pyproject.toml`` + ``requirements*.txt`` anywhere under
    ``project_dir``; returns sorted, normalized, de-duplicated names as a
    tuple (the Protocol's return type).

    ``excludes_loader`` + ``scope_exclude_dirs`` mirror the project
    discoverer: the effective project set (floor ∪ YAML project entries ∪
    pyproject TOML) is computed fresh per ``resolve`` call — the per-run read
    posture that keeps ``--watch`` edits live — and passed down so a manifest
    inside an excluded directory contributes no packages (spec D9). A fresh
    per-call TOML read is safe here: nothing fingerprints this set, so there
    is no intra-run coupling to preserve.
    """

    excludes_loader: Callable[[Path], ProjectExcludes] = load_project_excludes
    scope_exclude_dirs: tuple[str, ...] = ()

    async def resolve(self, project_dir: Path) -> tuple[str, ...]:
        return await asyncio.to_thread(self._resolve_sync, project_dir)

    def _resolve_sync(self, project_dir: Path) -> tuple[str, ...]:
        # Deferred import keeps the module load graph small for test-time
        # imports; :mod:`deps` pulls in ``tomllib`` which is fine but
        # unnecessary at class-definition time.
        from pydocs_mcp.deps import discover_declared_dependencies

        effective = merge_excludes(
            _EXCLUDED_DIRS,
            self.scope_exclude_dirs,
            self.excludes_loader(project_dir),
        )
        return tuple(discover_declared_dependencies(str(project_dir), effective))


__all__ = ("StaticDependencyResolver",)
```

(The `_EXCLUDED_DIRS` floor is passed into `merge_excludes` per the shared contract — the resolver's manifest walk therefore prunes the floor names on top of `deps._SKIP_DIRS`; both are pure-union additions, never subtractions. The old module docstring's "sub-PR #5" wording dies with this rewrite per the README/comments jargon policy.)

Run: `pytest tests/extraction/test_dependencies.py -v` — expect ALL tests to PASS, including the pre-existing frozen/slotted and Protocol-surface tests (fields don't change either invariant: `FrozenInstanceError` is an `AttributeError` subclass, and `resolve`'s signature is untouched).

- [ ] **Step 6: Wire the composition root.** In `python/pydocs_mcp/storage/factories.py`, line 609, inside the `ProjectIndexer(...)` construction, replace:

```python
        dependency_resolver=StaticDependencyResolver(),
```

with:

```python
        dependency_resolver=StaticDependencyResolver(
            # YAML project-scope entries reach the manifest walk (spec 7.9);
            # the TOML loader default stands (per-resolve read, D3 posture).
            scope_exclude_dirs=tuple(config.extraction.discovery.project.exclude_dirs)
        ),
```

`config` is already in scope at that call site (used at `factories.py:564` / `:570` / `:578`). This is the only construction site in `python/` (grep `StaticDependencyResolver(` — the other hits are tests that keep defaults, plus `benchmarks/src/pydocs_eval/systems/pydocs.py:288` and its `build/lib` copy, which construct it bare and correctly keep the new fields' defaults — no edit needed there).

- [ ] **Step 7: Run the affected existing suites.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
pytest tests/test_deps.py tests/test_deps_extended.py tests/test_deps_optional_groups.py \
       tests/test_deps_pep440_specs.py tests/test_deps_project_scripts.py \
       tests/extraction/test_dependencies.py tests/extraction/test_end_to_end.py -q
ruff check python/pydocs_mcp/deps.py python/pydocs_mcp/extraction/strategies/dependencies.py \
       python/pydocs_mcp/storage/factories.py tests/
ruff format --check python/ tests/ benchmarks/
mypy python/pydocs_mcp
```

Expected: all green. If `ruff format --check` flags the new code, run `ruff format python/ tests/` and re-check.

- [ ] **Step 8: Commit.**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
git add python/pydocs_mcp/deps.py \
        python/pydocs_mcp/extraction/strategies/dependencies.py \
        python/pydocs_mcp/storage/factories.py \
        tests/test_deps.py \
        tests/extraction/test_dependencies.py
git commit -m "feat(deps): prune user-excluded directories from dependency-manifest discovery"
```

(No `Co-Authored-By` trailer, no `--author` — repo authorship policy.)

---

### Task 10: Watcher derived globs + re-derivation + config-error resilience

Implements spec §7.6, decision D6, and the two watch rows of §8. Three pieces:

1. `FileWatcher` gains a `derived_globs_provider: Callable[[], tuple[str, ...]]` field (default returns `()`) consulted by `_matches` **on every call**, in addition to the static `ignore_globs` tuple, plus a pure module-level helper `derive_exclude_globs(excludes, project_root)` that translates a `ProjectExcludes` into watchdog globs: bare name → `"<project_root>/**/<name>/**"`, anchored → `"<project_root>/<path>/**"` (root-anchored, spec §7.6).
2. `_build_watcher_and_callback` (`python/pydocs_mcp/__main__.py:525`) derives the globs at startup (best-effort — a loader `ProjectExcludeConfigError` degrades to YAML-derived entries only, with a warning), wires them through the provider, and **re-derives + swaps the provider's backing value after every successful reindex** (D6 shrink direction, AC-25).
3. The `_on_change` callback catches `ProjectExcludeConfigError` explicitly — logs, skips the cycle, keeps the watcher alive (§8 watch row, AC-20).

**Depends on:** `python/pydocs_mcp/project_toml.py` (`ProjectExcludes`, `EMPTY_PROJECT_EXCLUDES`, `load_project_excludes`, `merge_excludes`, `ProjectExcludeConfigError`) and the `DiscoveryScopeConfig.exclude_dirs` field — both land in earlier tasks of this plan. Do not start Task 10 until those are committed.

**Two spec-reading resolutions this task bakes in (why the tests look the way they do):**

- **Derived globs land in `derived_globs_provider`, NOT in the static `ignore_globs` tuple** — otherwise the post-reindex swap of AC-25 is impossible on a frozen dataclass. Spec AC-16 has been amended to say exactly this ("land in the watcher's derived-glob provider ... consulted by `_matches` alongside the configured `ignore_globs`"), matching §7.6's separate-suffix rule. The observable behavior AC-16 pins (excluded-dir events filtered, root `pyproject.toml` still matching) is asserted directly.
- **`fnmatch` has no globstar:** `fnmatch.translate` turns `**` into `.*.*` ≡ `.*`, so the contract-pinned bare-name glob `"<root>/**/fixtures/**"` requires a literal `/fixtures/` **after** some path segment — it matches `<root>/src/fixtures/a.py` but NOT a top-level `<root>/fixtures/a.py` (no `/fixtures/` substring remains after `<root>/`). That top-level miss is the D6-sanctioned best-effort gap, now stated explicitly in spec §7.6 and AC-25 (both amended to use nested example paths): the event still fires, the reindex runs, discovery excludes the directory, and the cached no-change pass costs <100ms. Tests therefore use **nested** occurrences (`src/fixtures/...`) to exercise suppression; do NOT "fix" this by emitting a second glob form — the glob shape is a shared cross-section contract.
- **"Re-derive after every manifest-triggered reindex":** the `on_change` callback has signature `Callable[[], Awaitable[None]]` (`serve/watcher.py:125`) — it receives no trigger paths, so it cannot distinguish manifest-triggered from source-triggered reindexes. We re-derive after **every successful** reindex: a strict superset that is idempotent when the exclude surfaces didn't change (the loader re-reads the same TOML, `merge_excludes` yields the same set, the same tuple is swapped in).

**Files:**

- Modify: `python/pydocs_mcp/serve/watcher.py` (imports at lines 20-28; new module helpers after `_load_watchdog` ends at line 62; `FileWatcher` fields at lines 75-82; `_matches` at lines 102-121)
- Modify: `python/pydocs_mcp/__main__.py` (TYPE_CHECKING block at lines 36-38; new `_derive_watch_globs` helper before `_build_watcher_and_callback`; `_build_watcher_and_callback` at lines 525-568; caller at line 597 in `_run_watch_loop`; caller at line 637 in `_run_watch_only`)
- Test: `tests/test_watcher.py` (append)
- Test: `tests/test_main_cli_watch.py` (append)

---

#### Cycle 1 — `FileWatcher.derived_globs_provider` + `derive_exclude_globs`

- [ ] **Step 1: Write the failing watcher-unit tests (AC-16 derivation shape + provider seam).**

  Append to `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/tests/test_watcher.py` (after `test_index_db_wal_mode_enabled_for_concurrent_reindex`, the last test in the file). These follow the file's conventions: function-local imports, `tmp_path` fixture, `observer_factory=lambda: object()` for `_matches`-only tests (no event loop needed):

  ```python
  def test_derive_exclude_globs_bare_and_anchored(tmp_path: Path) -> None:
      """AC-16 (derivation shape, spec §7.6): a bare name derives a glob
      anchored BENEATH the project root — `<root>/**/<name>/**`, never the
      unanchored `**/<name>/**` house style of the configured YAML defaults —
      and an anchored entry derives `<root>/<path>/**`. Root-anchoring is
      load-bearing: an unanchored bare-name glob would match ancestor
      components of the project root's own path (§7.6 collision case)."""
      from pydocs_mcp.project_toml import ProjectExcludes
      from pydocs_mcp.serve.watcher import derive_exclude_globs

      excludes = ProjectExcludes(
          names=frozenset({"fixtures"}),
          anchored=frozenset({"docs/generated"}),
      )
      globs = derive_exclude_globs(excludes, tmp_path)

      assert f"{tmp_path}/**/fixtures/**" in globs
      assert f"{tmp_path}/docs/generated/**" in globs
      assert len(globs) == 2
      assert "**/fixtures/**" not in globs


  def test_derive_exclude_globs_empty_set_yields_no_globs(tmp_path: Path) -> None:
      """No user excludes -> no derived globs (the floor is covered by the
      operator-owned configured defaults, never re-derived here)."""
      from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES
      from pydocs_mcp.serve.watcher import derive_exclude_globs

      assert derive_exclude_globs(EMPTY_PROJECT_EXCLUDES, tmp_path) == ()


  def test_derived_globs_provider_defaults_to_empty_tuple(tmp_path: Path) -> None:
      """Constructing FileWatcher without the new field is byte-identical to
      today's filtering — the default provider returns ()."""
      from pydocs_mcp.serve.watcher import FileWatcher

      fw = FileWatcher(
          root=tmp_path,
          extensions=(".py",),
          ignore_globs=(),
          debounce_ms=10,
          observer_factory=lambda: object(),
      )
      assert fw.derived_globs_provider() == ()
      assert fw._matches(tmp_path / "src" / "fixtures" / "a.py") is True


  def test_matches_consults_derived_globs_provider(tmp_path: Path) -> None:
      """AC-16 (filtering): a path inside a derived-excluded dir does not match
      `_matches`; the project root's own pyproject.toml still does (manifest
      rule — no `<name>` component between root and the file); a manifest
      INSIDE the excluded dir does not (manifests are subordinate to ignore
      globs, `_is_dependency_manifest` docstring, serve/watcher.py)."""
      from pydocs_mcp.serve.watcher import FileWatcher

      fw = FileWatcher(
          root=tmp_path,
          extensions=(".py",),
          ignore_globs=(),
          debounce_ms=10,
          observer_factory=lambda: object(),
          derived_globs_provider=lambda: (f"{tmp_path}/**/fixtures/**",),
      )
      # WHY nested (src/fixtures/, not top-level fixtures/): fnmatch has no
      # globstar — `**` degrades to `*` — so the derived glob needs a literal
      # "/fixtures/" after some segment below the root. The top-level miss is
      # D6's sanctioned best-effort gap (discovery owns correctness).
      assert fw._matches(tmp_path / "src" / "fixtures" / "a.py") is False
      assert fw._matches(tmp_path / "src" / "fixtures" / "pyproject.toml") is False
      assert fw._matches(tmp_path / "pyproject.toml") is True
      assert fw._matches(tmp_path / "src" / "app.py") is True


  def test_matches_rereads_derived_globs_provider_every_call(tmp_path: Path) -> None:
      """The provider is consulted per `_matches` call — never cached at
      construction — the seam AC-25's post-reindex swap relies on."""
      from pydocs_mcp.serve.watcher import FileWatcher

      backing: list[tuple[str, ...]] = [(f"{tmp_path}/**/fixtures/**",)]
      fw = FileWatcher(
          root=tmp_path,
          extensions=(".py",),
          ignore_globs=(),
          debounce_ms=10,
          observer_factory=lambda: object(),
          derived_globs_provider=lambda: backing[0],
      )
      event = tmp_path / "src" / "fixtures" / "x.py"
      assert fw._matches(event) is False
      backing[0] = ()
      assert fw._matches(event) is True
  ```

- [ ] **Step 2: Run the new tests — expect FAIL.**

  ```bash
  cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
  pytest tests/test_watcher.py -v -k "derive_exclude_globs or derived_globs_provider or rereads_derived"
  ```

  Expected: 5 failures/errors. The two `derive_exclude_globs` tests error with `ImportError: cannot import name 'derive_exclude_globs' from 'pydocs_mcp.serve.watcher'`; `test_derived_globs_provider_defaults_to_empty_tuple` fails with `AttributeError: 'FileWatcher' object has no attribute 'derived_globs_provider'`; the two constructor-injection tests error with `TypeError: FileWatcher.__init__() got an unexpected keyword argument 'derived_globs_provider'`.

- [ ] **Step 3: Implement the watcher changes.**

  In `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/python/pydocs_mcp/serve/watcher.py`:

  **(a)** Add one import. The current import block (lines 20-28) is:

  ```python
  from __future__ import annotations

  import asyncio
  import contextlib
  import fnmatch
  import logging
  from collections.abc import Awaitable, Callable
  from dataclasses import dataclass, field
  from pathlib import Path
  ```

  Append after the `pathlib` line (project_toml imports only stdlib + `pydocs_mcp.exceptions`, so this preserves the module's documented cheap-import property and creates no cycle):

  ```python
  from pydocs_mcp.project_toml import ProjectExcludes
  ```

  **(b)** Add two module-level functions immediately after `_load_watchdog` (which ends at line 62, before the `FileWatcher` class):

  ```python
  def _no_derived_globs() -> tuple[str, ...]:
      """Default ``derived_globs_provider`` — no user-exclude globs derived."""
      return ()


  def derive_exclude_globs(excludes: ProjectExcludes, project_root: Path) -> tuple[str, ...]:
      """Translate user exclusion entries into watchdog ignore globs (spec §7.6).

      Bare names become ``<project_root>/**/<name>/**`` and anchored entries
      become ``<project_root>/<path>/**``. Both are prefixed with the absolute
      project root because ``FileWatcher._matches`` fnmatches the FULL absolute
      path string: an unanchored ``**/<name>/**`` would match ancestor
      components of the project root's own path (a project at
      ``/home/user/docs/myproj`` excluding ``"docs"`` would silence every
      event under the root, including the root ``pyproject.toml`` — no event,
      no reindex, ever). Root-anchoring puts the wildcard segment strictly
      below the root.

      Best-effort churn suppression only (spec decision D6): fnmatch's ``*``
      is not a globstar, so a bare-name glob misses a top-level occurrence
      (``<root>/<name>/...`` has no ``/<name>/`` after a below-root segment).
      That miss costs one cheap cached reindex per event — discovery owns
      correctness, never this derivation.

      Sorted for deterministic output (frozenset iteration order varies).
      """
      root = str(project_root)
      bare = tuple(f"{root}/**/{name}/**" for name in sorted(excludes.names))
      anchored = tuple(f"{root}/{path}/**" for path in sorted(excludes.anchored))
      return bare + anchored
  ```

  **(c)** Add the field to `FileWatcher`. The current field block (lines 75-82) is:

  ```python
      root: Path
      extensions: tuple[str, ...]
      ignore_globs: tuple[str, ...]
      debounce_ms: int
      # Allows tests to inject a `FakeObserver` without touching watchdog.
      # Production callers leave it None → constructor resolves the real
      # `watchdog.observers.Observer` lazily.
      observer_factory: Callable[[], object] | None = field(default=None)
  ```

  Append after the `observer_factory` line:

  ```python
      # WHY a provider callable, not a second globs tuple: derived (user-
      # exclude) globs must be swappable after a manifest-triggered reindex
      # (spec D6 shrink direction, AC-25) while this dataclass is frozen.
      # `_matches` re-reads the provider on every event; the composition
      # root (`_build_watcher_and_callback`) swaps the backing value.
      # Same injected-callable pattern as `observer_factory`.
      derived_globs_provider: Callable[[], tuple[str, ...]] = field(default=_no_derived_globs)
  ```

  **(d)** Replace `_matches` (lines 102-121) with this full final version — only the last two lines of the body and one docstring sentence change:

  ```python
      def _matches(self, path: Path) -> bool:
          """Pure-function event filter — returns True iff the path is
          a candidate for triggering a reindex.

          Extensions are compared case-insensitively (path.suffix.lower())
          so editors that save as `Setup.PY` on case-insensitive filesystems
          (macOS APFS / Windows NTFS by default) still trigger reindex.
          Defaults in WatchConfig are lowercase by convention.

          Dependency manifests (`pyproject.toml` / `requirements*.txt`) always
          match regardless of `extensions`, so adding a package to them retriggers
          indexing and the new dependency gets picked up.

          Returns False for: non-watched extensions that aren't a manifest, paths
          matching any `ignore_globs` pattern OR any glob currently returned by
          `derived_globs_provider` (user-exclude suppression, spec §7.6).
          """
          if path.suffix.lower() not in self.extensions and not _is_dependency_manifest(path.name):
              return False
          path_str = str(path)
          patterns = self.ignore_globs + self.derived_globs_provider()
          return not any(fnmatch.fnmatch(path_str, pattern) for pattern in patterns)
  ```

- [ ] **Step 4: Run the new tests — expect PASS.**

  ```bash
  pytest tests/test_watcher.py -v -k "derive_exclude_globs or derived_globs_provider or rereads_derived"
  ```

  Expected: `5 passed`.

- [ ] **Step 5: Run the full watcher suite (regressions — existing constructions omit the new field and must be unaffected).**

  ```bash
  pytest tests/test_watcher.py tests/test_watcher_manifests.py tests/test_watcher_dispatch_contract.py -v
  ```

  Expected: all pass, 0 failures.

- [ ] **Step 6: Commit cycle 1.**

  ```bash
  git add python/pydocs_mcp/serve/watcher.py tests/test_watcher.py
  git commit -m "feat(watch): FileWatcher derived_globs_provider seam + exclude-glob derivation helper"
  ```

---

#### Cycle 2 — startup derivation, post-reindex re-derivation, config-error resilience in `_build_watcher_and_callback`

- [ ] **Step 7: Write the failing wiring tests (AC-16 collision, AC-20, AC-25).**

  Append to `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/tests/test_main_cli_watch.py` (after `test_serve_watch_help_has_no_extras_hint`, the last test). Conventions from the same file: inline `argparse.Namespace`, `monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)` so construction never touches real watchdog, `caplog.at_level(..., logger="pydocs-mcp")`:

  ```python
  def _watch_args(root) -> "argparse.Namespace":
      """Namespace shape `_build_watcher_and_callback` reads (mirrors the
      existing tests in this file; force=False so nothing masks the
      no-force-propagation contract tested elsewhere)."""
      import argparse

      return argparse.Namespace(
          project=str(root),
          verbose=False,
          watch=True,
          force=False,
          cache_dir=None,
          no_inspect=True,
          config=None,
      )


  def test_build_watcher_derives_root_anchored_globs_ancestor_collision(
      tmp_path, monkeypatch
  ) -> None:
      """AC-16: derived globs are anchored at the project root, so a project
      that itself lives UNDER a directory named like a bare exclude
      (`<tmp>/docs/myproj`, exclude `"docs"`) keeps its root pyproject.toml
      visible to the watcher — an unanchored `**/docs/**` would match the
      root's own ancestor path and permanently silence the watcher (§7.6)."""
      from pydocs_mcp.__main__ import _build_watcher_and_callback
      from pydocs_mcp.project_toml import ProjectExcludes
      from pydocs_mcp.retrieval.config.models import WatchConfig
      from pydocs_mcp.serve import watcher as watcher_mod
      from tests._fakes import FakeObserver

      monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)

      root = tmp_path / "docs" / "myproj"
      root.mkdir(parents=True)

      def _fake_loader(_project):
          return ProjectExcludes(names=frozenset({"docs"}), anchored=frozenset())

      watcher, _on_change = _build_watcher_and_callback(
          _watch_args(root), WatchConfig(), excludes_loader=_fake_loader
      )

      # Compare against watcher.root (the resolved project path), not the raw
      # tmp_path string — `_project_and_db` resolves symlinked tmp dirs.
      assert f"{watcher.root}/**/docs/**" in watcher.derived_globs_provider()
      # Ancestor collision: the root pyproject.toml — whose absolute path
      # contains /docs/ ABOVE the project root — still matches (manifest rule).
      assert watcher._matches(watcher.root / "pyproject.toml") is True
      # A nested excluded occurrence is suppressed.
      assert watcher._matches(watcher.root / "src" / "docs" / "guide.md") is False
      # Configured YAML globs land verbatim; derivation never touches them.
      assert watcher.ignore_globs == tuple(WatchConfig().ignore_globs)


  def test_build_watcher_derives_globs_from_yaml_scope_entries(
      tmp_path, monkeypatch
  ) -> None:
      """AC-16: YAML `extraction.discovery.project.exclude_dirs` entries reach
      the derived globs (bare AND anchored forms) with no pyproject excludes —
      both user surfaces feed the same derivation (§7.6)."""
      from pydocs_mcp.__main__ import _build_watcher_and_callback
      from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES
      from pydocs_mcp.retrieval.config.models import WatchConfig
      from pydocs_mcp.serve import watcher as watcher_mod
      from tests._fakes import FakeObserver

      monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)

      watcher, _on_change = _build_watcher_and_callback(
          _watch_args(tmp_path),
          WatchConfig(),
          project_exclude_dirs=("fixtures", "docs/generated"),
          excludes_loader=lambda _p: EMPTY_PROJECT_EXCLUDES,
      )

      derived = watcher.derived_globs_provider()
      assert f"{watcher.root}/**/fixtures/**" in derived
      assert f"{watcher.root}/docs/generated/**" in derived


  async def test_on_change_catches_exclude_config_error_and_recovers(
      tmp_path, monkeypatch, caplog
  ) -> None:
      """AC-20 (§8 watch row): a watch-triggered reindex raising
      ProjectExcludeConfigError is logged and swallowed — the watcher callback
      returns normally and keeps working — and the NEXT (valid) manifest edit
      triggers a reindex whose fresh excludes are applied to the derived
      globs. Startup derivation with a raising loader is best-effort: warn,
      construct the watcher with no derived globs."""
      from pydocs_mcp.__main__ import _build_watcher_and_callback
      from pydocs_mcp.project_toml import ProjectExcludeConfigError, ProjectExcludes
      from pydocs_mcp.retrieval.config.models import WatchConfig
      from pydocs_mcp.serve import watcher as watcher_mod
      from tests._fakes import FakeObserver

      monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)

      loader_valid = [False]

      def _flip_loader(_project):
          if not loader_valid[0]:
              raise ProjectExcludeConfigError(
                  "exclude_dirs must be a list of strings, got 'docs'"
              )
          return ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())

      reindex_raises = [True]
      calls: list[None] = []

      async def _flaky_run_indexing(_args) -> None:
          calls.append(None)
          if reindex_raises[0]:
              raise ProjectExcludeConfigError(
                  "exclude_dirs must be a list of strings, got 'docs'"
              )

      monkeypatch.setattr("pydocs_mcp.__main__._run_indexing", _flaky_run_indexing)

      with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
          watcher, on_change = _build_watcher_and_callback(
              _watch_args(tmp_path), WatchConfig(), excludes_loader=_flip_loader
          )
      # Startup: best-effort — warning logged, watcher up, no derived globs.
      assert any("exclude config invalid" in r.getMessage() for r in caplog.records)
      assert watcher.derived_globs_provider() == ()

      caplog.clear()
      with caplog.at_level(logging.ERROR, logger="pydocs-mcp"):
          await on_change()  # mid-edit save: must NOT raise
      errors = [r for r in caplog.records if r.levelno == logging.ERROR]
      assert len(errors) == 1
      assert "exclude config invalid" in errors[0].getMessage()
      assert "skipping this reindex cycle" in errors[0].getMessage()
      assert watcher.derived_globs_provider() == ()  # failed cycle: no swap

      # The user finishes the edit: next manifest event reindexes + applies it.
      loader_valid[0] = True
      reindex_raises[0] = False
      await on_change()
      assert len(calls) == 2, "callback must keep reindexing after a config error"
      assert f"{watcher.root}/**/fixtures/**" in watcher.derived_globs_provider()


  async def test_derived_globs_rederive_after_reindex_shrink_direction(
      tmp_path, monkeypatch
  ) -> None:
      """AC-25 (D6 shrink direction): with `"fixtures"` excluded at startup an
      event inside it is filtered; after a manifest-triggered reindex whose
      fresh effective set is empty, the provider is swapped and the SAME event
      matches — edits inside the re-included directory fire reindexes again
      without a restart. Configured YAML ignore_globs unchanged throughout."""
      from pydocs_mcp.__main__ import _build_watcher_and_callback
      from pydocs_mcp.project_toml import EMPTY_PROJECT_EXCLUDES, ProjectExcludes
      from pydocs_mcp.retrieval.config.models import WatchConfig
      from pydocs_mcp.serve import watcher as watcher_mod
      from tests._fakes import FakeObserver

      monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)

      excludes_cell = [ProjectExcludes(names=frozenset({"fixtures"}), anchored=frozenset())]

      async def _noop_run_indexing(_args) -> None:
          return None

      monkeypatch.setattr("pydocs_mcp.__main__._run_indexing", _noop_run_indexing)

      watcher, on_change = _build_watcher_and_callback(
          _watch_args(tmp_path),
          WatchConfig(),
          excludes_loader=lambda _p: excludes_cell[0],
      )

      configured_before = watcher.ignore_globs
      event = watcher.root / "src" / "fixtures" / "x.py"
      assert watcher._matches(event) is False  # startup-derived glob suppresses

      # User removes the exclude entry; the manifest edit triggers a reindex.
      excludes_cell[0] = EMPTY_PROJECT_EXCLUDES
      await on_change()

      assert watcher.derived_globs_provider() == ()
      assert watcher._matches(event) is True  # re-included dir fires again
      assert watcher.ignore_globs == configured_before  # only the derived suffix refreshed
  ```

  Note: `logging` and `pytest` are already imported at the top of this file (lines 5, 7); the async tests run without a marker exactly like the file's existing `async def` tests.

- [ ] **Step 8: Run the new tests — expect FAIL.**

  ```bash
  pytest tests/test_main_cli_watch.py -v -k "derives_root_anchored or derives_globs_from_yaml or catches_exclude_config or rederive_after_reindex"
  ```

  Expected: 4 errors, each `TypeError: _build_watcher_and_callback() got an unexpected keyword argument 'excludes_loader'` (or `'project_exclude_dirs'` for the YAML-entries test).

- [ ] **Step 9: Implement the `__main__.py` wiring.**

  In `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a/python/pydocs_mcp/__main__.py`:

  **(a)** Extend the TYPE_CHECKING block (lines 36-38). Current:

  ```python
  if TYPE_CHECKING:
      from pydocs_mcp.retrieval.config import AppConfig, WatchConfig
      from pydocs_mcp.serve.watcher import FileWatcher
  ```

  Final:

  ```python
  if TYPE_CHECKING:
      from pydocs_mcp.project_toml import ProjectExcludes
      from pydocs_mcp.retrieval.config import AppConfig, WatchConfig
      from pydocs_mcp.serve.watcher import FileWatcher
  ```

  **(b)** Add a module-level helper immediately BEFORE `_build_watcher_and_callback` (line 525). Function-local imports match the file's house style:

  ```python
  def _derive_watch_globs(
      project: Path,
      scope_entries: tuple[str, ...],
      loader: Callable[[Path], ProjectExcludes],
  ) -> tuple[str, ...]:
      """Best-effort watchdog ignore globs from the user exclusion surfaces.

      Churn suppression only (spec decision D6) — discovery owns correctness,
      so a failed or partial derivation degrades to extra cheap cached reindex
      cycles, never to wrong index content. The `_EXCLUDED_DIRS` floor is
      deliberately NOT folded in (empty-floor merge): floor directories are
      already covered by the operator-owned configured `ignore_globs`
      defaults, and re-deriving them would only duplicate patterns.
      """
      from pydocs_mcp.project_toml import (
          EMPTY_PROJECT_EXCLUDES,
          ProjectExcludeConfigError,
          merge_excludes,
      )
      from pydocs_mcp.serve.watcher import derive_exclude_globs

      try:
          loaded = loader(project)
      except ProjectExcludeConfigError as exc:
          # Spec §8 (watcher glob-derivation row): warn and derive from the
          # YAML entries only — the reindex path fails loud on its own.
          log.warning(
              "watch: project exclude config invalid (%s); "
              "ignore globs derived from YAML entries only",
              exc,
          )
          loaded = EMPTY_PROJECT_EXCLUDES
      effective = merge_excludes(frozenset(), scope_entries, loaded)
      return derive_exclude_globs(effective, project)
  ```

  **(c)** Replace `_build_watcher_and_callback` (lines 525-568) with this full final version:

  ```python
  def _build_watcher_and_callback(
      args: argparse.Namespace,
      watch_cfg: WatchConfig,
      *,
      project_exclude_dirs: tuple[str, ...] = (),
      excludes_loader: Callable[[Path], ProjectExcludes] | None = None,
  ) -> tuple[FileWatcher, Callable[[], Awaitable[None]]]:
      """Build the ``FileWatcher`` + ``on_change`` callback shared by
      ``serve --watch`` and the standalone ``watch`` subcommand.

      Single source of truth for watcher construction so the two modes can
      only differ in whether they ALSO run an MCP server. Lifted out of
      ``_run_watch_loop`` to keep the two consumers in sync — bug-fixes
      or YAML-knob additions land here and reach both modes automatically.

      ``project_exclude_dirs`` carries the YAML project-scope entries
      (``extraction.discovery.project.exclude_dirs``); ``excludes_loader``
      is the pyproject-excludes loader seam (default: the real
      ``load_project_excludes``) so tests inject fakes without touching the
      filesystem.
      """
      from pydocs_mcp.project_toml import ProjectExcludeConfigError, load_project_excludes
      from pydocs_mcp.serve.watcher import FileWatcher

      loader = excludes_loader if excludes_loader is not None else load_project_excludes
      project, _db = _project_and_db(args)

      # One-element list so the `_on_change` closure below can swap the
      # derived suffix after each reindex (spec D6 shrink direction, AC-25)
      # while the watcher re-reads it through `derived_globs_provider` on
      # every event. The configured `ignore_globs` tuple stays operator-owned
      # and static — only the derived suffix ever refreshes.
      derived_globs: list[tuple[str, ...]] = [
          _derive_watch_globs(project, project_exclude_dirs, loader)
      ]
      watcher = FileWatcher(
          root=project,
          extensions=tuple(watch_cfg.extensions),
          ignore_globs=tuple(watch_cfg.ignore_globs),
          debounce_ms=watch_cfg.debounce_ms,
          derived_globs_provider=lambda: derived_globs[0],
      )

      # File-change reindexes must NEVER inherit --force: force wipes the
      # whole cache (SQLite + .tq via IndexingService.clear_all) and re-embeds
      # project + dependencies — what the user asked for on the INITIAL pass,
      # catastrophic on every save (and in serve --watch mode, queries during
      # the re-embed window would hit an empty index). Copy the namespace so
      # the caller-driven initial pass keeps its force semantics.
      watch_args = argparse.Namespace(**vars(args))
      watch_args.force = False

      async def _on_change() -> None:
          # Reindex via the same Phase 1 helper used at startup. Cache
          # makes the no-change case <100ms (spec §2).
          try:
              await _run_indexing(watch_args)
          except ProjectExcludeConfigError as exc:
              # WHY: a half-saved pyproject.toml can PARSE with a wrong-typed
              # value (`exclude_dirs = "docs"` before the brackets land).
              # Killing the serve process on a keystroke race would be worse
              # than the misconfiguration — log, skip this cycle, and let the
              # very next save retry (spec §8, watch row). Detection stays
              # loud; only delivery is softened.
              log.error(
                  "watch: project exclude config invalid; "
                  "skipping this reindex cycle: %s",
                  exc,
              )
              return
          except Exception as exc:
              # WHY: a reindex failure during the watch loop should NOT
              # take down the consumer (MCP server in --watch mode; the
              # whole process in standalone watch mode). Log + keep
              # serving stale data instead.
              log.error("watch: reindex failed: %s", exc)
              return
          # WHY re-derive after EVERY successful reindex (not only manifest-
          # triggered ones — this callback receives no trigger paths, and
          # re-deriving an unchanged set is idempotent): startup-only
          # derivation fails the shrink direction. Removing an exclude entry
          # re-includes the directory on the manifest-triggered reindex, but
          # a stale startup glob would then swallow every subsequent event
          # inside it — no event, no reindex, a silently stale subtree until
          # restart (spec D6, AC-25).
          derived_globs[0] = _derive_watch_globs(project, project_exclude_dirs, loader)

      return watcher, _on_change
  ```

  **(d)** Update the caller in `_run_watch_loop`. Current (line 597):

  ```python
      watcher, on_change = _build_watcher_and_callback(args, watch_cfg)
  ```

  Final (`config` is already in scope from `AppConfig.load` at line 594):

  ```python
      watcher, on_change = _build_watcher_and_callback(
          args,
          watch_cfg,
          project_exclude_dirs=tuple(config.extraction.discovery.project.exclude_dirs),
      )
  ```

  **(e)** Update the caller in `_run_watch_only`. Current (line 637):

  ```python
      watcher, on_change = _build_watcher_and_callback(args, watch_cfg)
  ```

  Final (`config` in scope from line 634):

  ```python
      watcher, on_change = _build_watcher_and_callback(
          args,
          watch_cfg,
          project_exclude_dirs=tuple(config.extraction.discovery.project.exclude_dirs),
      )
  ```

- [ ] **Step 10: Run the new tests — expect PASS.**

  ```bash
  pytest tests/test_main_cli_watch.py -v -k "derives_root_anchored or derives_globs_from_yaml or catches_exclude_config or rederive_after_reindex"
  ```

  Expected: `4 passed`.

- [ ] **Step 11: Run the affected existing suites (both watch consumers + the force/isolation regressions that call `_build_watcher_and_callback` with the OLD two-argument shape — the new keyword-only params must default them to unchanged behavior).**

  ```bash
  pytest tests/test_main_cli_watch.py tests/test_main_cli_watch_force.py \
         tests/test_main_cli_watch_only.py tests/test_main_cli_serve_watch_enabled.py \
         tests/test_main_cli.py tests/test_watcher.py tests/test_watcher_manifests.py \
         tests/test_watcher_dispatch_contract.py -v
  ```

  Expected: all pass. Watch specifically for `test_on_change_isolates_reindex_failure` (its RuntimeError must still route to the generic `except Exception` branch producing exactly 2 `watch: reindex failed` ERROR records) and the two `test_run_watch_loop_cancels_*` tests (they drive the real `_run_watch_loop` caller edit through `AppConfig.load`, so they also verify `config.extraction.discovery.project.exclude_dirs` resolves — i.e. the earlier config task actually landed).

- [ ] **Step 12: Run the lint/type gates on the touched files, then commit.**

  ```bash
  ruff format python/pydocs_mcp/serve/watcher.py python/pydocs_mcp/__main__.py \
              tests/test_watcher.py tests/test_main_cli_watch.py
  ruff check python/ tests/
  mypy python/pydocs_mcp
  ```

  Expected: format makes no changes (or re-run the two pytest commands if it does), 0 ruff errors, 0 mypy errors.

  ```bash
  git add python/pydocs_mcp/serve/watcher.py python/pydocs_mcp/__main__.py \
          tests/test_watcher.py tests/test_main_cli_watch.py
  git commit -m "feat(watch): derive user-exclude ignore globs, re-derive after each reindex, survive exclude config errors"
  ```

  (`tests/test_watcher.py` is in the `git add` list because the mutating `ruff format` above may have reformatted it after Cycle 1's commit — leaving it out would strand those reformats uncommitted until the Task 13 catch-all. If it is unchanged, `git add` on it is a no-op.)

---

### Task 11: End-to-end acceptance suite (AC-17, AC-18, AC-19, AC-21 end-to-end clause, AC-23, AC-24, AC-26)

**Files:**
- Create: `tests/extraction/test_end_to_end_excludes.py` (peer of `tests/extraction/test_end_to_end.py`, per spec §11 "extend `tests/extraction/test_end_to_end.py` or peer" — a separate file keeps the existing e2e file under the size cap and gives the exclusion suite one grep-able home)
- Test: `tests/extraction/test_end_to_end_excludes.py`

This task writes NO production code. Every test here drives the REAL write path — `build_project_indexer` (the production composition root at `python/pydocs_mcp/storage/factories.py:501`) over a tmp project, with only `MockEmbedder` / `FakeLlmClient` substituted (the exact monkeypatch seam `tests/storage/test_build_project_indexer.py:20-34` already uses). These are acceptance pins over behavior implemented in Tasks 1–10, so the expected outcome of each run step is **PASS**. If any test FAILS, the defect is in the earlier task's component (each step names the suspect file) — fix it there, never by weakening the test.

Conventions this file follows (read them in the real neighbors before starting):
- `tests/extraction/test_end_to_end.py:84-136` — tmp-project fixture + service wiring shape.
- `tests/storage/test_build_project_indexer.py:20-41` — the `_offline_factories` autouse monkeypatch fixture and `db_path` fixture.
- `pyproject.toml:193` sets `asyncio_mode = "auto"`, so `async def test_*` needs no `@pytest.mark.asyncio` decorator.

- [ ] **Step 1: Create the test file with fixtures and helpers**

Create `tests/extraction/test_end_to_end_excludes.py` with this exact content:

```python
"""End-to-end acceptance suite for per-project directory exclusion.

Drives the REAL write-side composition root (``build_project_indexer``,
``storage/factories.py``) over tmp projects that declare
``[tool.pydocs-mcp] exclude_dirs`` in their own ``pyproject.toml`` and/or
``extraction.discovery.*.exclude_dirs`` in a YAML overlay. Pins the
spec's end-to-end acceptance criteria: exclusion of chunks AND symbols
(AC-17), widen-and-reindex removal including the member-only-content
fingerprint case (AC-18), file-name-collision no-op parity (AC-19),
zero decision records from a TOML-excluded ADR directory (AC-21,
end-to-end clause), dependency-cache isolation in both directions
(AC-23), the four conditional-fold behaviors (AC-24), and the YAML
surface through the real composition root (AC-26).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.application.indexing_service import IndexingStats
from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig


# ── Offline seam (same monkeypatch pattern as tests/storage/test_build_project_indexer.py) ──


@pytest.fixture(autouse=True)
def _offline_factories(monkeypatch):
    """MockEmbedder + FakeLlmClient so the composition root never downloads
    ONNX weights or touches the OpenAI network (the factory resolves both
    lazily via deferred imports — the documented monkeypatch seam)."""
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.retrieval import llm_clients as _llm_clients
    from tests._fakes import FakeLlmClient, MockEmbedder

    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: MockEmbedder())
    monkeypatch.setattr(
        _llm_clients,
        "build_llm_client",
        lambda cfg: FakeLlmClient(responses={}),
    )


# ── Fixture-project builders ─────────────────────────────────────────────

_CORE_PY = '"""Core module."""\n\n\ndef core_fn():\n    """Core."""\n    return 1\n'
_SAMPLE_PY = '"""Fixture sample."""\n\n\ndef sample_fn():\n    """Sample."""\n    return 2\n'
_GEN_PY = '"""Generated tool."""\n\n\ndef gen_fn():\n    """Gen."""\n    return 3\n'
_GUIDE_MD = "# Guide\n\nReal documentation.\n"
_API_MD = "# Generated API\n\nAuto-generated noise.\n"
_DATA_MD = "# Fixture data\n\nSynthetic content.\n"


def _write_pyproject(
    root: Path,
    *,
    exclude_dirs: list[str] | None = None,
    dependencies: tuple[str, ...] = (),
) -> None:
    """(Re)write the project's own pyproject.toml — the TOML surface."""
    deps = ", ".join(f'"{d}"' for d in dependencies)
    text = f'[project]\nname = "e2e-excl"\nversion = "0.1.0"\ndependencies = [{deps}]\n'
    if exclude_dirs is not None:
        entries = ", ".join(f'"{e}"' for e in exclude_dirs)
        text += f"\n[tool.pydocs-mcp]\nexclude_dirs = [{entries}]\n"
    (root / "pyproject.toml").write_text(text, encoding="utf-8")


def _make_worked_example_tree(root: Path) -> None:
    """The spec §4 worked-example tree (minus .venv — the floor is pinned
    by the discoverer unit tests, not re-proven here)."""
    (root / "docs" / "generated").mkdir(parents=True)
    (root / "docs" / "generated" / "api.md").write_text(_API_MD, encoding="utf-8")
    (root / "docs" / "guide.md").write_text(_GUIDE_MD, encoding="utf-8")
    (root / "src" / "myproj" / "fixtures").mkdir(parents=True)
    (root / "src" / "myproj" / "core.py").write_text(_CORE_PY, encoding="utf-8")
    (root / "src" / "myproj" / "fixtures" / "sample.py").write_text(_SAMPLE_PY, encoding="utf-8")
    (root / "fixtures").mkdir()
    (root / "fixtures" / "data.md").write_text(_DATA_MD, encoding="utf-8")
    (root / "tools" / "generated").mkdir(parents=True)
    (root / "tools" / "generated" / "gen.py").write_text(_GEN_PY, encoding="utf-8")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh SQLite DB file — schema materialised up front."""
    p = tmp_path / "e2e_excl.db"
    open_index_database(p).close()
    return p


# ── Indexing through the REAL composition root ───────────────────────────


async def _index_run(
    project: Path,
    db: Path,
    config: AppConfig | None = None,
    *,
    include_deps: bool = False,
) -> IndexingStats:
    """One full index pass via build_project_indexer — a fresh bundle per
    run, exactly like a fresh CLI invocation (the per-run TOML loader of
    spec §5 is exercised for real)."""
    from pydocs_mcp.storage.factories import build_project_indexer

    bundle = build_project_indexer(
        config or AppConfig.load(),
        db,
        use_inspect=False,
        inspect_depth=None,
    )
    return await bundle.orchestrator.index_project(
        project,
        force=False,
        include_project_source=True,
        include_dependencies=include_deps,
        workers=1,
    )


# ── SQLite observation helpers ───────────────────────────────────────────


def _rows(db: Path, sql: str, *params: object) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _chunk_modules(db: Path) -> set[str]:
    return {r[0] for r in _rows(db, "SELECT module FROM chunks WHERE package = '__project__'")}


def _member_modules(db: Path) -> set[str]:
    return {
        r[0] for r in _rows(db, "SELECT module FROM module_members WHERE package = '__project__'")
    }


def _package_hash(db: Path, name: str = "__project__") -> str:
    rows = _rows(db, "SELECT content_hash FROM packages WHERE name = ?", name)
    assert rows, f"no packages row for {name!r}"
    return rows[0][0]


def _has_component(modules: set[str], component: str) -> bool:
    """True iff any dotted module id carries *component* as a path segment
    (module ids dot-join directory components; doc files keep their
    extension as a trailing segment, e.g. 'fixtures.data.md')."""
    return any(component in m.split(".") for m in modules)
```

- [ ] **Step 2: Sanity-run the empty module**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
pytest tests/extraction/test_end_to_end_excludes.py -v
```

Expected output: `no tests ran` (exit code 5) with zero collection errors. A collection error here means an import path in the helpers is wrong — fix before proceeding.

- [ ] **Step 3: AC-17 — pyproject-driven exclusion removes chunks AND symbols**

Append to `tests/extraction/test_end_to_end_excludes.py`:

```python
# ── AC-17: pyproject excludes remove chunks and symbols ──────────────────


async def test_ac17_pyproject_excludes_remove_chunks_and_symbols(
    tmp_path: Path, db_path: Path
) -> None:
    """Index a project whose OWN pyproject.toml declares exclude_dirs;
    neither chunks nor ModuleMember rows exist for the excluded
    directories, while every sibling survives — including the
    leaf-name-collision sibling tools/generated/ (spec §4)."""
    _make_worked_example_tree(tmp_path)
    _write_pyproject(tmp_path, exclude_dirs=["docs/generated", "fixtures"])

    stats = await _index_run(tmp_path, db_path)
    assert stats.project_indexed is True

    chunks = _chunk_modules(db_path)
    members = _member_modules(db_path)

    # Bare "fixtures" prunes BOTH occurrences (root-level and nested).
    assert not _has_component(chunks, "fixtures"), f"fixtures chunks leaked: {chunks}"
    assert not _has_component(members, "fixtures"), f"fixtures symbols leaked: {members}"
    # Anchored "docs/generated" prunes exactly its own subtree...
    assert not any(m.startswith("docs.generated.") for m in chunks)
    # ...while the sibling with the same leaf name survives on BOTH tables.
    assert "tools.generated.gen" in chunks
    assert "tools.generated.gen" in members
    # Untouched content is fully present on both tables.
    assert "docs.guide.md" in chunks
    assert "src.myproj.core" in chunks
    assert "src.myproj.core" in members
```

- [ ] **Step 4: Run AC-17**

```bash
pytest tests/extraction/test_end_to_end_excludes.py::test_ac17_pyproject_excludes_remove_chunks_and_symbols -v
```

Expected: `1 passed`. If it FAILS on the chunk assertions, the suspect is `ProjectFileDiscoverer` (`python/pydocs_mcp/extraction/strategies/discovery/project.py`) or the default `excludes_loader` wiring; if it fails only on the member assertions, the suspect is `AstMemberExtractor._parse_dir` (`python/pydocs_mcp/extraction/strategies/members/ast_extractor.py`).

- [ ] **Step 5: AC-19 — file-name-collision entries are a uniform no-op**

Append:

```python
# ── AC-19: directories-only rule — filename collisions are a no-op ───────


async def test_ac19_filename_collision_is_uniform_noop(tmp_path: Path, db_path: Path) -> None:
    """An entry colliding with a FILE name (anchored 'docs/conf.py' where
    docs/conf.py is a file, and the bare filename 'conf.py') excludes
    nothing on EITHER walk: the file's chunks and its symbols are both
    present — never one without the other (spec §4 directories-only rule,
    §7.5 chunk/member divergence guard)."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "conf.py").write_text(
        '"""Sphinx-style conf."""\n\n\ndef setup(app):\n    """Setup."""\n    return app\n',
        encoding="utf-8",
    )
    (tmp_path / "docs" / "guide.md").write_text(_GUIDE_MD, encoding="utf-8")
    _write_pyproject(tmp_path, exclude_dirs=["docs/conf.py", "conf.py"])

    stats = await _index_run(tmp_path, db_path)
    assert stats.project_indexed is True

    chunks = _chunk_modules(db_path)
    members = _member_modules(db_path)
    # Parity: the collided file keeps chunks AND symbols.
    assert "docs.conf" in chunks, f"chunk walk dropped docs/conf.py: {chunks}"
    assert "docs.conf" in members, f"member post-filter dropped docs/conf.py: {members}"
    # The rest of docs/ is untouched (the anchored entry excluded nothing).
    assert "docs.guide.md" in chunks
```

- [ ] **Step 6: Run AC-19**

```bash
pytest tests/extraction/test_end_to_end_excludes.py::test_ac19_filename_collision_is_uniform_noop -v
```

Expected: `1 passed`. A failure where `docs.conf` is missing from `members` but present in `chunks` means `AstMemberExtractor._parse_dir` is matching the FULL file relpath instead of the parent-directory relpath — exactly the divergence spec §7.5 forbids.

- [ ] **Step 7: AC-18 — widen-and-reindex removes rows atomically (two cases)**

Append:

```python
# ── AC-18: widen the excludes → reindex removes rows, SQLite + .tq coherent ──


async def test_ac18_widen_and_reindex_removes_previously_indexed_rows(
    tmp_path: Path, db_path: Path
) -> None:
    """Widening exclude_dirs between two runs misses the package hash,
    skips the cached path, and atomically removes the newly-excluded
    directory's chunks AND symbols; the .tq sidecar stays coherent with
    SQLite (observed via the bundle's integrity sweep)."""
    from pydocs_mcp.storage.factories import build_project_indexer

    _make_worked_example_tree(tmp_path)
    _write_pyproject(tmp_path)  # no excludes yet

    stats1 = await _index_run(tmp_path, db_path)
    assert stats1.project_indexed is True
    hash_before = _package_hash(db_path)
    assert _has_component(_chunk_modules(db_path), "fixtures")
    assert _has_component(_member_modules(db_path), "fixtures")

    # Widen: exclude fixtures. Only pyproject.toml changes — it is not a
    # discovered file (.toml is outside include_extensions), so the path
    # set moves ONLY because discovery now prunes fixtures/.
    _write_pyproject(tmp_path, exclude_dirs=["fixtures"])
    stats2 = await _index_run(tmp_path, db_path)
    assert stats2.project_indexed is True, "cached-skip path must NOT be taken"
    assert _package_hash(db_path) != hash_before

    assert not _has_component(_chunk_modules(db_path), "fixtures"), "chunk rows orphaned"
    assert not _has_component(_member_modules(db_path), "fixtures"), "member rows orphaned"
    # Vector-count coherence: the integrity sweep compares chunks(embedded=1)
    # against the .tq sidecar and returns [] iff nothing is orphaned/stranded.
    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=False, inspect_depth=None)
    assert await bundle.check_integrity() == []


async def test_ac18_member_only_directory_still_misses_via_fingerprint(
    tmp_path: Path, db_path: Path
) -> None:
    """The §9 fingerprint-fold case: the newly-excluded directory holds
    ONLY member-producing, chunk-invisible content (a .py above
    max_file_size_bytes, default 1_000_000). Excluding it leaves the
    chunk-discovered path set unchanged — a path-only hash would hit the
    cache and strand the symbols. The exclusion fingerprint must force
    the miss and the symbols must vanish."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(_CORE_PY, encoding="utf-8")
    (tmp_path / "bigonly").mkdir()
    # > 1 MB of valid Python: one real symbol + comment padding. The size
    # budget applies to chunk discovery only; walk_py_files (members) has
    # no budget, so this file yields a ModuleMember but zero chunks.
    big_src = '"""Big module."""\n\n\ndef big_fn():\n    """Big."""\n    return 1\n\n' + (
        "# pad\n" * 170_000
    )
    (tmp_path / "bigonly" / "big.py").write_text(big_src, encoding="utf-8")
    _write_pyproject(tmp_path)

    stats1 = await _index_run(tmp_path, db_path)
    assert stats1.project_indexed is True
    hash_before = _package_hash(db_path)
    # Precondition the case depends on: members yes, chunks no.
    assert "bigonly.big" in _member_modules(db_path)
    assert "bigonly.big" not in _chunk_modules(db_path)

    _write_pyproject(tmp_path, exclude_dirs=["bigonly"])
    stats2 = await _index_run(tmp_path, db_path)
    assert stats2.project_indexed is True, (
        "path set unchanged but exclusion set widened — the fingerprint "
        "fold must force a hash miss (spec §9, Goal 6)"
    )
    assert _package_hash(db_path) != hash_before
    assert "bigonly.big" not in _member_modules(db_path), "member-only symbols survived"
```

- [ ] **Step 8: Run AC-18**

```bash
pytest tests/extraction/test_end_to_end_excludes.py -v -k ac18
```

Expected: `2 passed`. The second test takes a few seconds (it writes and AST-parses a ~1 MB file — that is the point of the fixture). If `test_ac18_member_only_directory_still_misses_via_fingerprint` fails with `stats2.project_indexed is False`, the `ContentHashStage` fold (`python/pydocs_mcp/extraction/pipeline/stages/content_hash.py`) is not folding `state.files.effective_excludes` — the exact hole spec §9 documents.

- [ ] **Step 9: AC-23 — dependency-cache isolation in both directions**

Append. The declared dependency is `iniconfig` — a tiny, pure-Python transitive dependency of pytest itself, so it is guaranteed installed in any environment that can run this suite:

```python
# ── AC-23: per-scope fingerprint — project edits never invalidate dep caches ──


def _write_overlay(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


async def test_ac23_dependency_cache_isolation_both_directions(
    tmp_path: Path, db_path: Path
) -> None:
    """Editing PROJECT excludes (TOML, then YAML) re-extracts the project
    but leaves every dependency on the cached-skip path (stats.cached);
    editing YAML DEPENDENCY excludes misses the dependency hashes without
    touching the project's (spec §9.1, decision D10)."""
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "app.py").write_text(_CORE_PY, encoding="utf-8")
    (proj / "fixtures").mkdir()
    (proj / "fixtures" / "data.md").write_text(_DATA_MD, encoding="utf-8")
    _write_pyproject(proj, dependencies=("iniconfig",))

    # Run A — baseline: project + iniconfig both extracted.
    stats_a = await _index_run(proj, db_path, include_deps=True)
    assert stats_a.project_indexed is True
    assert stats_a.indexed == 1 and stats_a.failed == 0
    dep_hash_a = _package_hash(db_path, "iniconfig")

    # Run B — TOML direction: edit ONLY the project's own exclude_dirs.
    _write_pyproject(proj, exclude_dirs=["fixtures"], dependencies=("iniconfig",))
    stats_b = await _index_run(proj, db_path, include_deps=True)
    assert stats_b.project_indexed is True, "project hash must miss"
    assert stats_b.cached == 1 and stats_b.indexed == 0, (
        "iniconfig must take the cached-skip path — project TOML excludes "
        "must never reach the dependency fold"
    )
    assert _package_hash(db_path, "iniconfig") == dep_hash_a

    # Run C — YAML project direction: add a YAML project exclude on top.
    overlay_c = _write_overlay(
        tmp_path / "overlay_c.yaml",
        "extraction:\n  discovery:\n    project:\n      exclude_dirs: [\"docs\"]\n",
    )
    stats_c = await _index_run(
        proj, db_path, AppConfig.load(explicit_path=overlay_c), include_deps=True
    )
    assert stats_c.project_indexed is True, "project fold changed → miss"
    assert stats_c.cached == 1 and stats_c.indexed == 0
    assert _package_hash(db_path, "iniconfig") == dep_hash_a

    # Run D — dependency direction: SAME project excludes as run C, plus a
    # dependency-scope exclude. Project skips; the dependency misses.
    overlay_d = _write_overlay(
        tmp_path / "overlay_d.yaml",
        "extraction:\n  discovery:\n"
        "    project:\n      exclude_dirs: [\"docs\"]\n"
        "    dependency:\n      exclude_dirs: [\"tests\"]\n",
    )
    stats_d = await _index_run(
        proj, db_path, AppConfig.load(explicit_path=overlay_d), include_deps=True
    )
    assert stats_d.project_indexed is False, (
        "dependency-scope excludes must never reach the project fold"
    )
    assert stats_d.indexed == 1 and stats_d.cached == 0, "dependency fold changed → miss"
    assert _package_hash(db_path, "iniconfig") != dep_hash_a
```

- [ ] **Step 10: Run AC-23**

```bash
pytest tests/extraction/test_end_to_end_excludes.py::test_ac23_dependency_cache_isolation_both_directions -v
```

Expected: `1 passed` (this is the slowest test in the file — it runs four full index passes including a real installed distribution). If runs B/C fail with `stats.cached == 0`, `DependencyFileDiscoverer.discover` is returning a union that includes project-scope entries — the per-scope rule of spec §9.1 is broken. If run D fails with `stats_d.project_indexed is True`, the project fold is (wrongly) including dependency-scope YAML.

- [ ] **Step 11: AC-24 — the four conditional-fold behaviors**

Append:

```python
# ── AC-24: upgrade compatibility — the fold is conditional (spec §9.2) ────


async def test_ac24_conditional_fold_all_four_behaviors(tmp_path: Path, db_path: Path) -> None:
    """(a) No user excludes → the stored hash equals TODAY'S framing, pure
    hash_files(paths) — a pre-upgrade index skips as cached; (b) first
    user exclude → miss; (c) removing the last exclude → miss AND the
    hash returns to the unfolded value of (a); (d) a floor-duplicate-only
    list ('.git') → same hash as (a), no spurious miss."""
    from pydocs_mcp._fast import hash_files
    from pydocs_mcp.extraction.config import DiscoveryScopeConfig
    from pydocs_mcp.extraction.strategies.discovery import ProjectFileDiscoverer

    _make_worked_example_tree(tmp_path)
    _write_pyproject(tmp_path)

    # (a) baseline: byte-identical to the pre-change framing.
    stats_a = await _index_run(tmp_path, db_path)
    assert stats_a.project_indexed is True
    hash_a = _package_hash(db_path)
    paths, _root, _effective = ProjectFileDiscoverer(scope=DiscoveryScopeConfig()).discover(
        tmp_path
    )
    raw = hash_files(list(paths))
    expected_unfolded = raw if isinstance(raw, str) else raw.hex()
    assert hash_a == expected_unfolded, (
        "no-excludes hash must equal pure hash_files(paths) so every "
        "pre-upgrade stored hash keeps matching (spec §9.2)"
    )

    # (b) adding the first user exclude → miss.
    _write_pyproject(tmp_path, exclude_dirs=["fixtures"])
    stats_b = await _index_run(tmp_path, db_path)
    assert stats_b.project_indexed is True
    hash_b = _package_hash(db_path)
    assert hash_b != hash_a

    # (c) removing the last user exclude → miss, hash returns to (a).
    _write_pyproject(tmp_path)
    stats_c = await _index_run(tmp_path, db_path)
    assert stats_c.project_indexed is True
    assert _package_hash(db_path) == hash_a, "fold must drop out entirely"

    # (d) floor duplicates only → effective set == floor → no fold, no miss.
    _write_pyproject(tmp_path, exclude_dirs=[".git"])
    stats_d = await _index_run(tmp_path, db_path)
    assert stats_d.project_indexed is False, "floor-duplicate entry caused a spurious miss"
    assert _package_hash(db_path) == hash_a
```

- [ ] **Step 12: Run AC-24**

```bash
pytest tests/extraction/test_end_to_end_excludes.py::test_ac24_conditional_fold_all_four_behaviors -v
```

Expected: `1 passed`. If (a) fails, `ContentHashStage` is folding unconditionally (or changed the `hash_files` framing) — the upgrade-cost bug spec §9.2 exists to prevent. If (d) fails with a spurious miss, `exclusion_fingerprint` is not returning `None` when `excludes.names == floor and not excludes.anchored`.

- [ ] **Step 13: AC-26 — YAML surface through the REAL composition root**

Append:

```python
# ── AC-26: YAML exclude_dirs reaches BOTH walks via storage/factories.py ──


async def test_ac26_yaml_excludes_through_real_composition_root(
    tmp_path: Path, db_path: Path
) -> None:
    """Load extraction.discovery.project.exclude_dirs from a YAML overlay
    through the real AppConfig.load + build_project_indexer — NO in-test
    construction of AstMemberExtractor or the discoverers — and assert
    chunks AND ModuleMember rows from fixtures/ are both absent. Pins the
    factories.py wiring of scope_exclude_dirs (spec §7.7): forgetting it
    passes the unit tests (field injected in-test) while YAML excludes
    silently never reach member extraction."""
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "app.py").write_text(_CORE_PY, encoding="utf-8")
    (proj / "fixtures").mkdir()
    (proj / "fixtures" / "sample.py").write_text(_SAMPLE_PY, encoding="utf-8")
    (proj / "fixtures" / "data.md").write_text(_DATA_MD, encoding="utf-8")
    _write_pyproject(proj)  # NO TOML excludes — YAML is the only surface here.

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "extraction:\n  discovery:\n    project:\n      exclude_dirs: [\"fixtures\"]\n",
        encoding="utf-8",
    )
    stats = await _index_run(proj, db_path, AppConfig.load(explicit_path=overlay))
    assert stats.project_indexed is True

    chunks = _chunk_modules(db_path)
    members = _member_modules(db_path)
    assert not _has_component(chunks, "fixtures"), (
        f"YAML excludes never reached chunk discovery: {chunks}"
    )
    assert not _has_component(members, "fixtures"), (
        f"YAML excludes never reached member extraction "
        f"(scope_exclude_dirs unwired in storage/factories.py): {members}"
    )
    # The non-excluded module survives on both tables.
    assert "src.app" in chunks
    assert "src.app" in members
```

- [ ] **Step 14: Run AC-26**

```bash
pytest tests/extraction/test_end_to_end_excludes.py::test_ac26_yaml_excludes_through_real_composition_root -v
```

Expected: `1 passed`. A failure ONLY on the `members` assertion is the exact defect this AC exists to catch: `storage/factories.py` still constructs `AstMemberExtractor()` bare instead of `AstMemberExtractor(scope_exclude_dirs=tuple(config.extraction.discovery.project.exclude_dirs))`.

- [ ] **Step 15: AC-21 (end-to-end clause) — a TOML-excluded ADR directory yields zero decision records**

This is the index-level half of AC-21 that Task 8's stage-level tests deliberately do NOT cover: it drives the real `pyproject.toml` → `ProjectFileDiscoverer` → `FileBundle.effective_excludes` → `MineDecisionsStage` chain through `build_project_indexer` and observes the `decision_records` table. Append:

```python
# ── AC-21 (end-to-end clause): excluded ADR dir → zero decision records ───

_ADR_MD = "# 1. Use SQLite\n\nStatus: Accepted\n\n## Decision\nYes.\n"


async def test_ac21_pyproject_excluded_adr_dir_yields_no_decision_records(
    tmp_path: Path,
) -> None:
    """Index a tmp project whose OWN pyproject.toml excludes its ADR
    directory: zero decision_records sourced from it. get_why and
    ``search --kind decision`` hydrate exclusively from decision_records
    (via origin='decision_record' chunks), and
    get_references(direction="governed_by") traverses kind='governs'
    edges in node_references — so zero rows on all three tables, observed
    at the single storage layer every one of those read surfaces
    consumes, IS the "surface nothing from it" guarantee (spec AC-21).
    A control project (same tree, no exclude) proves the fixture mines
    for real — the default shipped config already enables the adr_files
    source, so no decision_capture overlay is needed."""

    def _make(root: Path, *, exclude: bool) -> Path:
        (root / "docs" / "adr").mkdir(parents=True)
        (root / "docs" / "adr" / "0001-use-sqlite.md").write_text(
            _ADR_MD, encoding="utf-8"
        )
        (root / "src").mkdir()
        (root / "src" / "core.py").write_text(_CORE_PY, encoding="utf-8")
        _write_pyproject(root, exclude_dirs=["docs"] if exclude else None)
        return root

    # Control: without the exclude, the ADR mines (>0 records) — otherwise
    # the zero-assertions below would be vacuously green.
    control = _make(tmp_path / "control", exclude=False)
    control_db = tmp_path / "control.db"
    open_index_database(control_db).close()
    stats = await _index_run(control, control_db)
    assert stats.project_indexed is True
    n_adr = _rows(
        control_db,
        "SELECT COUNT(*) FROM decision_records WHERE source = 'adr_files'",
    )[0][0]
    assert n_adr >= 1, "control fixture failed to mine — AC-21 pin would be vacuous"

    # Excluded: the same tree with [tool.pydocs-mcp] exclude_dirs = ["docs"].
    # In this fixture EVERY decision record comes from adr_files (no git
    # repo, no CHANGELOG, no README/docs-glob prose, no inline markers), so
    # total-count zero is the strongest observable.
    excl = _make(tmp_path / "excl", exclude=True)
    excl_db = tmp_path / "excl.db"
    open_index_database(excl_db).close()
    stats = await _index_run(excl, excl_db)
    assert stats.project_indexed is True
    assert _rows(excl_db, "SELECT COUNT(*) FROM decision_records")[0][0] == 0
    # Nothing for get_why / `search --kind decision` to hydrate...
    assert (
        _rows(
            excl_db,
            "SELECT COUNT(*) FROM chunks WHERE origin = 'decision_record'",
        )[0][0]
        == 0
    )
    # ...and nothing for get_references(direction="governed_by") to traverse.
    assert (
        _rows(
            excl_db,
            "SELECT COUNT(*) FROM node_references WHERE kind = 'governs'",
        )[0][0]
        == 0
    )
```

- [ ] **Step 16: Run AC-21**

```bash
pytest tests/extraction/test_end_to_end_excludes.py::test_ac21_pyproject_excluded_adr_dir_yields_no_decision_records -v
```

Expected: `1 passed`. If the control half fails (`n_adr == 0`), the fixture or the shipped `decision_capture` defaults changed — fix the fixture, never delete the control. If the excluded half fails with leftover `decision_records`, the suspect is the Task 8 chain: `MineDecisionsStage._build_context` not filling `excluded` from `state.files.effective_excludes` (`python/pydocs_mcp/extraction/pipeline/stages/decisions/mine_decisions.py`) or `adr_files._adr_paths` not consulting `ctx.excluded` (`python/pydocs_mcp/extraction/decisions/sources/adr_files.py`).

- [ ] **Step 17: Run the whole file plus the neighboring e2e and composition-root suites**

```bash
pytest tests/extraction/test_end_to_end_excludes.py tests/extraction/test_end_to_end.py tests/storage/test_build_project_indexer.py -v
```

Expected: all tests pass (8 new + the pre-existing e2e/factory tests), no skips other than any pre-existing ones. Then the broader affected suites:

```bash
pytest tests/extraction/ tests/test_deps.py tests/test_watcher.py -q
```

Expected: all pass.

- [ ] **Step 18: Commit**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
git add tests/extraction/test_end_to_end_excludes.py
git commit -m "test(e2e): acceptance suite for exclude_dirs — AC-17/18/19/21/23/24/26"
```

---

### Task 12: Documentation (DOCUMENTATION.md, README.md, default_config.yaml)

**Files:**
- Modify: `DOCUMENTATION.md` (insert a new `## Excluding directories from indexing` section immediately before `## Two-level cache`, currently line 536)
- Modify: `README.md` (insert a new `### Exclude directories from indexing` subsection between the `### Live re-indexing` subsection — ends at the line 152 link — and `### Multi-repo search (optional)`, currently line 154)
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (lines 31-34 stale policy comment + lines 45-51 discovery block)

No production code, so no TDD cycle — the verification steps are the README audit grep, a config-load sanity test, and the config test suite.

- [ ] **Step 1: Add the DOCUMENTATION.md section**

In `DOCUMENTATION.md`, find the heading `## Two-level cache` (line 536; the paragraph after it begins "Each project gets a `.db`") and insert the following complete section immediately BEFORE it (the outer fence below is 4 backticks so the inner ```` ```toml ````/```` ```yaml ````/tree-diagram fences nest cleanly — in the actual DOCUMENTATION.md they are normal top-level 3-backtick fences, exactly as shown):

````markdown
## Excluding directories from indexing

Some directories are pure retrieval noise — generated docs, test fixtures,
vendored examples, in-repo evaluation datasets. You can exclude *additional*
directories from indexing via two additive surfaces. Both layer on top of the
built-in, non-removable floor (`.git`, `.venv`, `__pycache__`, `node_modules`,
`site-packages`, …): entries can only exclude MORE — there is no syntax, on
any surface, that re-includes a floor directory, and listing a floor name
(e.g. `".git"`) is a harmless no-op.

**Surface 1 — the indexed project's own `pyproject.toml`** (travels with the
repo; every developer indexing the project gets the same exclusions; applies
to the project walk only, never to dependencies):

```toml
# pyproject.toml at the root of the project being indexed
[tool.pydocs-mcp]
exclude_dirs = ["docs/generated", "fixtures"]
```

**Surface 2 — server YAML** (per-deployment; the only way to shape
dependency walks — a dependency's own `pyproject.toml` is never consulted):

```yaml
# pydocs-mcp.yaml (or any overlay loaded via --config)
extraction:
  discovery:
    project:
      exclude_dirs: ["fixtures"]          # additive over the floor + TOML
    dependency:
      exclude_dirs: ["tests", "examples"] # additive over the floor
```

### Matching rules

Each entry is one of two kinds, classified by whether it contains a `/`
(after normalization: backslashes become `/`, then a trailing `/` is
stripped — so `"fixtures/"` is the bare name `"fixtures"`, never anchored):

1. **Bare name** (no `/`) — matches that directory name as any path
   component at any depth, exactly like the built-in floor entries.
2. **Anchored path** (contains `/`) — a relative path anchored at the walk
   root; excludes exactly that directory and its subtree, nothing else. For
   dependency walks the first path component of each shipped-file path is
   stripped before matching, so one entry applies uniformly across
   dependencies.

No globs — `*`, `?`, `[` are literal characters. Entries name directories,
never files (an entry colliding with a file name is a no-op). Matching is
byte-wise case-sensitive on every platform.

With `exclude_dirs = ["docs/generated", "fixtures"]`:

```
myproj/                          (project root)
├── docs/
│   ├── generated/               ✗ excluded — anchored "docs/generated"
│   │   └── api.md               ✗   (whole subtree gone)
│   └── guide.md                 ✓ indexed
├── src/
│   └── myproj/
│       ├── core.py              ✓ indexed
│       └── fixtures/            ✗ excluded — bare name matches at any depth
│           └── sample.py        ✗
├── fixtures/                    ✗ excluded — bare name matches here too
│   └── data.md                  ✗
├── tools/
│   └── generated/               ✓ indexed — anchored "docs/generated" does
│       └── gen.py               ✓   NOT match a same-named sibling
└── .venv/                       ✗ excluded — built-in floor, as always
```

### Reach and freshness

An excluded directory contributes **no chunks, no symbols, no decision
records (`get_why`), and no dependency manifests** — every write-side reader
honors the same effective set. Under `--watch`, edits to
`[tool.pydocs-mcp] exclude_dirs` are picked up on the next reindex without a
server restart (`pyproject.toml` is always a reindex trigger), and widening
the list removes the newly-excluded directories' chunks, symbols, and dense
vectors from the index atomically. YAML `exclude_dirs`, like every other
YAML tunable, resolves at startup and needs a restart to change.
````

- [ ] **Step 2: Verify the DOCUMENTATION.md anchor renders**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
grep -n "^## Excluding directories from indexing" DOCUMENTATION.md
grep -n "^## Two-level cache" DOCUMENTATION.md
```

Expected: the first grep prints one line whose number is smaller than the second grep's — the new section sits directly before `## Two-level cache`.

- [ ] **Step 3: Add the README.md recipe subsection**

In `README.md`, find the end of the `### Live re-indexing` subsection — the paragraph ending with `(see\n[DOCUMENTATION.md](DOCUMENTATION.md#live-re-indexing)).` (line 152) — and insert the following BETWEEN that paragraph and the `### Multi-repo search (optional)` heading:

````markdown
### Exclude directories from indexing

Keep generated docs, test fixtures, or vendored trees out of search results.
Declare additional exclusions in your project's own `pyproject.toml` — they
travel with the repo:

```toml
[tool.pydocs-mcp]
exclude_dirs = ["docs/generated", "fixtures"]
```

Bare names (`"fixtures"`) match at any depth; paths (`"docs/generated"`)
match only that directory. Entries are additive over the built-in floor
(`.git`, `.venv`, …) — you can exclude more, never less. A server-side YAML
equivalent covers both project and dependency walks
(`extraction.discovery.*.exclude_dirs`); see
[DOCUMENTATION.md](DOCUMENTATION.md#excluding-directories-from-indexing).
````

Note: the outer fence above is 4 backticks so the inner ` ```toml ` fence nests cleanly — in the actual README it is a normal top-level 3-backtick fence, exactly as shown.

- [ ] **Step 4: Run the README audit grep (jargon gate — mandatory before merge)**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
```

Expected: **no output** (exit code 1 from grep). Any match is a policy violation — replace the offending text with a capability/file reference before proceeding.

- [ ] **Step 5: Update `python/pydocs_mcp/defaults/default_config.yaml`**

Two edits. First, the stale policy comment at lines 31-34. Replace:

```yaml
extraction:
  # Directory blocklist is hardcoded in extraction/config.py::_EXCLUDED_DIRS —
  # not configurable via YAML by design (security: un-excluded .git/.venv/
  # site-packages would leak secrets). Extension allowlist IS narrowable.
```

with:

```yaml
extraction:
  # Directory-exclusion FLOOR is hardcoded in extraction/config.py::
  # _EXCLUDED_DIRS and non-removable (security: un-excluded .git/.venv/
  # site-packages would leak secrets). User exclude_dirs (below, and the
  # indexed project's own [tool.pydocs-mcp]) are ADDITIVE-ONLY over it.
```

Second, the discovery block at lines 45-51. Replace:

```yaml
  discovery:
    project:
      include_extensions: [".py", ".md", ".ipynb"]
      max_file_size_bytes: 1000000
    dependency:
      include_extensions: [".py", ".md", ".ipynb"]
      max_file_size_bytes: 1000000
```

with:

```yaml
  discovery:
    project:
      include_extensions: [".py", ".md", ".ipynb"]
      max_file_size_bytes: 1000000
      # Additive over the built-in floor; bare names match at any depth,
      # entries containing "/" anchor at the project root.
      # e.g. exclude_dirs: ["docs/generated", "fixtures"]
      exclude_dirs: []
    dependency:
      include_extensions: [".py", ".md", ".ipynb"]
      max_file_size_bytes: 1000000
      # Additive over the built-in floor; applies to every dependency walk
      # (a dependency's own pyproject.toml is never consulted).
      # e.g. exclude_dirs: ["tests", "examples"]
      exclude_dirs: []
```

- [ ] **Step 6: Verify the shipped defaults still load and match the pydantic defaults**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
python -c "
from pydocs_mcp.retrieval.config import AppConfig
c = AppConfig.load()
assert c.extraction.discovery.project.exclude_dirs == []
assert c.extraction.discovery.dependency.exclude_dirs == []
print('defaults OK')
"
```

Expected output: `defaults OK`. Then the config suites that pin defaults:

```bash
pytest tests/extraction/test_config.py tests/test_default_config_serve_watch.py tests/test_config_pipeline_hash.py -q
```

Expected: all pass. A `test_config_pipeline_hash` failure would mean the YAML edit accidentally changed the ingestion-hash input — the discovery block is not part of it, so investigate before touching anything else.

- [ ] **Step 7: Commit**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a
git add DOCUMENTATION.md README.md python/pydocs_mcp/defaults/default_config.yaml
git commit -m "docs: exclude_dirs user docs (DOCUMENTATION.md section, README recipe, default_config.yaml keys)"
```

---

### Task 13: Full CI gate — run and fix-forward

**Files:**
- Modify: none planned — only fix-forward edits if a gate fails. `complexipy-snapshot.json` must be restored from HEAD and never committed.

Run every gate from the repo root `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/dreamy-joliot-f7830a`. Fix-forward means: a gate failure gets the smallest compliant fix in the file the gate names, then re-run that gate, then continue. Commit fix-forwards per gate (messages below).

- [ ] **Step 1: ruff format**

```bash
ruff format --check python/ tests/ benchmarks/
```

Expected output: `N files already formatted` (exit 0). If it lists would-be-reformatted files, run `ruff format python/ tests/ benchmarks/`, re-run the check, and fold the result into a `style: ruff format` commit.

- [ ] **Step 2: ruff lint**

```bash
ruff check python/ tests/ benchmarks/
```

Expected output: `All checks passed!` (exit 0). Fix any finding in place (no `# noqa` unless the rule is a documented false positive in this repo) and re-run.

- [ ] **Step 3: mypy**

```bash
mypy python/pydocs_mcp
```

Expected output: `Success: no issues found in N source files` (exit 0). Likely failure surface for this feature: the `Callable[[Path], ProjectExcludes]` field defaults and the 3-tuple discoverer Protocol returns — fix annotations at the definition site, never with `# type: ignore` on the caller.

- [ ] **Step 4: complexipy (snapshot-rewrite trap)**

```bash
complexipy python/pydocs_mcp --max-complexity-allowed 15
git checkout -- complexipy-snapshot.json
```

Expected: exit 0 (no function above 15). **The local run rewrites `complexipy-snapshot.json` in place — the `git checkout --` line is mandatory immediately after, and the file must NEVER appear in any commit of this feature** (`git status` must show it clean before every `git add`). If a new function exceeds 15, refactor it (extract guard clauses / helpers per the repo's ≤2-nesting rule) rather than raising the threshold.

- [ ] **Step 5: vulture**

```bash
vulture python/pydocs_mcp --min-confidence 80
```

Expected: no output (exit 0). A hit on a newly-added symbol usually means an `__all__` export or a YAML-registry decorator path vulture can't see — prefer wiring the symbol properly over whitelisting.

- [ ] **Step 6: pytest with coverage floor**

```bash
pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90
```

Expected: all tests pass and the run ends with `Required test coverage of 90% reached.` (exit 0). If coverage falls below 90%, the uncovered lines will be in the new modules (`project_toml.py`, the watcher re-derivation, the error-posture branches) — add the missing unit tests in the task-owned test files (`tests/test_project_toml.py`, `tests/test_watcher.py`), not throwaway ones.

- [ ] **Step 7: lockfile check (STOP condition)**

```bash
uv lock --check
```

Expected: exit 0 (`Resolved N packages` / no changes needed) — this feature adds zero dependencies (`tomllib` is stdlib). **If this gate fails, STOP: do not relock, do not edit `uv.lock` or `pyproject.toml` dependencies. Report the failure** — a lockfile drift here means something outside this feature's scope changed and needs the user's decision.

- [ ] **Step 7b: dependency audit (pip-audit — STOP condition, same posture as Step 7)**

```bash
uv export --frozen --no-emit-project --no-group docs --format requirements-txt > requirements-audit.txt
.venv/bin/pip-audit --strict --local
rm -f requirements-audit.txt
```

Expected: pip-audit exits 0 with no known vulnerabilities. This is the CI pair documented in `.github/workflows/ci.yml` / CLAUDE.md; CI runs `uvx pip-audit --strict --requirement requirements-audit.txt`, but the requirement-mode run SIGABRTs under the local sandbox — `.venv/bin/pip-audit --strict --local` over the frozen venv is the repo's documented local equivalent. `requirements-audit.txt` is a scratch artifact — never commit it. **If pip-audit reports a vulnerability, STOP and report it** — this feature adds zero dependencies, so any finding is pre-existing drift outside this feature's scope and needs the user's decision (do not bump or pin anything to silence it).

- [ ] **Step 8: verify clean tree and commit any fix-forwards**

```bash
git status --porcelain
```

Expected: empty (all fix-forwards already committed per gate). If any fix-forward edits are still unstaged, verify `complexipy-snapshot.json` is NOT among them, then:

```bash
git add python/ tests/ benchmarks/
git commit -m "fix: CI gate fix-forwards for exclude_dirs (ruff/mypy/coverage)"
```

Then re-run the full gate list (Steps 1-7b) one final time top to bottom — every gate must pass consecutively on the final tree.
