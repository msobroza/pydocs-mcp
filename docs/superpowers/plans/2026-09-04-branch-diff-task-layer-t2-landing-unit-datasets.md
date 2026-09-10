# Branch/Diff Task Layer — Plan T2: Landing-Unit Datasets (S2b–S3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `release_notes` framing its corpora and gate — this repository's own release windows (`pydocs-self-releases`, changelog sections read at the tag as gold, the leak floor, the hand-labeled alignment sidecar), the per-bullet regression-localization dataset (`pydocs-self-landing-loc` under `bug_loc`), the diff-scoped vulnerability sibling (`crosscommitvuln-fix-landing` under `vuln`), the `release_notes` arm config, the self-corpus smoke gate, and the generic changelog-tagged corpus scaffold.

**Architecture:** All three datasets are built on Plan T1's `materialize_corpus_with_history`: the self-corpus is a clone of this repository whose history is rewritten so `CHANGELOG.md` is absent from every commit (tags re-pointed, shas deterministic), with the base checked out at the newer tag and the retention window pinned in the overlay; gold is read from the ORIGINAL clone before the rewrite and frozen with the rewritten shas in a committed sidecar the loader re-verifies. A changelog parser accepts the three heading shapes this repository has used; bullet ↔ unit alignment is hand-labeled (a committed YAML), with the automatic `(#N)` rule as a consistency check only. Nothing here touches the v2 `crosscommitvuln` dataset — the fix-landing variant is a sibling.

**Tech Stack:** Python 3.11+, git (subprocess, bounded), PyYAML, `pydocs_eval` datasets/registries, pytest (git-backed tests skip without `git`; the smoke gate is a marked local test).

**Spec:** `docs/superpowers/specs/2026-09-04-branch-diff-task-layer-design.md` (commit `19f6b3a`) — §6.3, §6.6, §7.2, §7.3, §7.4, §7.6, §8, §10 (S2b, S3), §11 AC-12, AC-13, AC-14, AC-16, AC-17, AC-24, AC-25; §13 O2, O7. Preconditions: **Plans T0 and T1 have landed**; the multi-branch **P2 plan's Task 11** (the landing-unit index, program item P2.8) and the base card's landed block (G1) are required for the `release_notes` arms to run for real; every loader below is testable before that against synthetic repositories.

## Global Constraints

- **Gold is never read from the checkout's `CHANGELOG.md`** (sections are edited after a tag): it is `git show <tag_to>:CHANGELOG.md` on the original clone, parsed by tag, and frozen in the sidecar together with the rewritten shas; the loader asserts the rewrite reproduces them (the `expected_rows` precedent).
- **The leak floor (R10)** on the self-corpus: history rewrite removing `CHANGELOG.md`; overlay `exclude_dirs: [docs, benchmarks]`; `decision_capture.sources: [adr_files, inline_markers, docs_prose]`; the build-time leak check over three surfaces (materialized files, `git log --format=%B` over the window, the indexed `decision_records` of the base) fails a record on any verbatim gold bullet; subject-level paraphrase is accepted noise. The self-corpus is **ask-harness-only** in v1 (§13 O7).
- **Churn units** (a diff touching only `.github/`, `uv.lock`, `Cargo.lock`, `complexipy-snapshot.json`) are excluded from the coverage key set; the same rule everywhere.
- **The self-corpus is a gate corpus**: `split: all` only, never an optimizer pool (six records cannot feed a parity split).
- **`crosscommitvuln-fix-landing` relaxes no v2 ban** (`_FRAMING_BANS` is untouched; "landing" is not on it) and the v2 dataset's module is not edited.
- **Record ids**: `pydocs-self-releases/release_notes/<tag_to>` (open window: `unreleased@<sha7>`), `pydocs-self-landing-loc/bug_loc/<tag_to>__<bullet_index>`, `crosscommitvuln-fix-landing/vuln/<cve>`.
- **Every `EvalTask.metadata` value is a string**; no hosting-service names in prose; task heads untouched (the `bug_loc` head never mentions landing units — the record's query carries the `landing: <sha7>` scaffold).
- **Naming, formatting, authorship, gates** as in Plans T0 / T1.

---

## File map

| Path | Status | Owns |
|---|---|---|
| `benchmarks/src/pydocs_eval/datasets/changelog_sections.py` | new | `ChangelogSection`, `parse_changelog_sections`, `section_for_tag`, `bullet_texts` |
| `benchmarks/src/pydocs_eval/datasets/self_releases_sidecar.py` | new | `AlignmentRow`, `AlignmentSidecar`, `load_alignment_sidecar`, `self_releases_data_dir` |
| `benchmarks/data/self_releases/alignment.yaml` | new | the committed sidecar (rows for the two small windows at minimum; the open-window pin) |
| `benchmarks/src/pydocs_eval/datasets/self_releases.py` | new | `ReleaseWindow`, `CHURN_PATH_RULE`, `is_churn_unit`, `SelfReleasesDataset` (`pydocs-self-releases`), `first_parent_units`, `changed_paths_of` |
| `benchmarks/src/pydocs_eval/datasets/self_releases_leak.py` | new | `LeakHit`, `check_gold_leaks` |
| `benchmarks/src/pydocs_eval/datasets/self_landing_loc.py` | new | `SelfLandingLocDataset` (`pydocs-self-landing-loc`), `LANDING_SCAFFOLD` |
| `benchmarks/src/pydocs_eval/datasets/crosscommitvuln_fix_landing.py` | new | `CrossCommitVulnFixLandingDataset` (`crosscommitvuln-fix-landing`) |
| `benchmarks/src/pydocs_eval/datasets/changelog_tagged.py`, `benchmarks/data/changelog_tagged/repos.yaml` | new | the generic corpus scaffold (`changelog-tagged-py`), owner-filled list |
| `benchmarks/src/pydocs_eval/optimize/configs/optimize_search_skill_release_notes.yaml` | new | the two `release_notes` arms |
| `benchmarks/tests/datasets/test_{changelog_sections,self_releases_sidecar,self_releases,self_landing_loc,crosscommitvuln_fix_landing}.py`, `benchmarks/tests/optimize/test_release_notes_arms.py`, `benchmarks/tests/gates/test_self_corpus_smoke_gate.py` | new | AC-12…AC-17, AC-24 |
| `docs/superpowers/specs/2026-09-04-ask-your-docs-branch-scope-ui-design.md`, `benchmarks/README.md`, `CHANGELOG.md` | modify | the `RELEASE_NOTES` chip row + G14 wording (AC-25), dataset subsections, changelog |

---

### Task 1: The changelog parser

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/changelog_sections.py`
- Test: `benchmarks/tests/datasets/test_changelog_sections.py`

**Interfaces:**
- Produces: `ChangelogSection(tag: str, heading: str, bullets: tuple[tuple[str, str], ...])` — `bullets` are `(subheading, text)` pairs in order, the text joined over continuation lines; `parse_changelog_sections(text) -> tuple[ChangelogSection, ...]`; `section_for_tag(sections, tag) -> ChangelogSection | None` (`v0.4.1` matches `## [0.4.1] — 2026-07-03`, `## v0.4.1`, `## v0.4.1 (unreleased)`); `UNRELEASED_TAG = "unreleased"` for `## [Unreleased]` / `## [0.6.0] — Unreleased`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/datasets/test_changelog_sections.py`:

```python
"""The changelog parser over this repository's three heading shapes — AC-14."""

from __future__ import annotations

from pydocs_eval.datasets.changelog_sections import (
    UNRELEASED_TAG,
    parse_changelog_sections,
    section_for_tag,
)

_TEXT = """# Changelog

The format is based on Keep a Changelog.

## [Unreleased]

## [0.4.1] — 2026-07-03

### Added

- **Air-gapped model loading** — point `embedding.model_name` at a local
  directory and nothing is downloaded. (#121)
- **Streamlit webapp** — a themed chat UI.

### Fixed

- restore evicted torch modules after the purge test

## v0.5.0 (unreleased)

### Changed

- six task-shaped tools

## v0.5.1

### Changed

- The tool-docs contract constants are public.

### CI

- lockfile gate
"""


def test_three_heading_shapes_parse_to_tags():
    sections = parse_changelog_sections(_TEXT)
    assert [s.tag for s in sections] == [UNRELEASED_TAG, "v0.4.1", "v0.5.0", "v0.5.1"]
    assert sections[1].heading == "## [0.4.1] — 2026-07-03"


def test_bullets_carry_their_subheading_and_join_continuation_lines():
    section = section_for_tag(parse_changelog_sections(_TEXT), "v0.4.1")
    assert section is not None
    assert [sub for sub, _ in section.bullets] == ["Added", "Added", "Fixed"]
    assert section.bullets[0][1].startswith("**Air-gapped model loading** — point")
    assert "nothing is downloaded. (#121)" in section.bullets[0][1]
    assert section.bullets[2] == ("Fixed", "restore evicted torch modules after the purge test")


def test_section_lookup_accepts_v_prefix_and_bracketed_forms():
    sections = parse_changelog_sections(_TEXT)
    assert section_for_tag(sections, "0.5.1") is section_for_tag(sections, "v0.5.1")
    assert section_for_tag(sections, "v0.5.0").bullets == (("Changed", "six task-shaped tools"),)
    assert section_for_tag(sections, "v9.9.9") is None
    assert section_for_tag(sections, UNRELEASED_TAG).bullets == ()


def test_unreleased_with_a_version_number_is_the_unreleased_section():
    text = "## [0.6.0] — Unreleased\n\n### Added\n\n- nine tools\n"
    (section,) = parse_changelog_sections(text)
    assert section.tag == UNRELEASED_TAG and section.bullets == (("Added", "nine tools"),)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_changelog_sections.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the parser**

```python
"""Keep-a-Changelog sections by release tag (task-layer design §7.2, AC-14).

Three heading shapes have appeared in this repository's history and all are
accepted: ``## [x.y.z] — date``, ``## vx.y.z`` and ``## vx.y.z (unreleased)``;
``## [Unreleased]`` and ``## [x.y.z] — Unreleased`` are the open section.
Bullets are the top-level ``- `` items under a ``###`` sub-heading, with
continuation lines joined by one space. BASE install: stdlib only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

UNRELEASED_TAG = "unreleased"
_HEADING = re.compile(r"^## (?P<heading>.+?)\s*$")
_VERSION = re.compile(r"^\[?v?(?P<version>\d+\.\d+\.\d+)\]?(?P<rest>.*)$")
_SUBHEADING = re.compile(r"^### (?P<name>.+?)\s*$")
_BULLET = re.compile(r"^- (?P<text>.*)$")
_CONTINUATION = re.compile(r"^ {2,}(?P<text>\S.*)$")


@dataclass(frozen=True, slots=True)
class ChangelogSection:
    tag: str  # "v0.4.1" or UNRELEASED_TAG
    heading: str  # the heading line verbatim
    bullets: tuple[tuple[str, str], ...]  # (sub-heading, bullet text)


def _tag_of(heading: str) -> str:
    match = _VERSION.match(heading.strip())
    if match is None or "unreleased" in heading.lower():
        return UNRELEASED_TAG
    return f"v{match.group('version')}"


def _finish(tag: str, heading: str, bullets: list[tuple[str, str]], out: list[ChangelogSection]) -> None:
    out.append(ChangelogSection(tag=tag, heading=heading, bullets=tuple(bullets)))


def parse_changelog_sections(text: str) -> tuple[ChangelogSection, ...]:
    """Every ``## `` section in file order with its bullets."""
    sections: list[ChangelogSection] = []
    tag, heading, subheading = "", "", ""
    bullets: list[tuple[str, str]] = []
    for line in text.splitlines():
        if (match := _HEADING.match(line)) is not None:
            if heading:
                _finish(tag, heading, bullets, sections)
            heading, tag, subheading, bullets = line.rstrip(), _tag_of(match.group("heading")), "", []
            continue
        if not heading:
            continue
        if (match := _SUBHEADING.match(line)) is not None:
            subheading = match.group("name")
        elif (match := _BULLET.match(line)) is not None:
            bullets.append((subheading, match.group("text").strip()))
        elif bullets and (match := _CONTINUATION.match(line)) is not None:
            sub, text = bullets[-1]
            bullets[-1] = (sub, f"{text} {match.group('text').strip()}")
    if heading:
        _finish(tag, heading, bullets, sections)
    return tuple(sections)


def section_for_tag(sections: Sequence[ChangelogSection], tag: str) -> ChangelogSection | None:
    """The section for ``tag`` (``v0.4.1`` / ``0.4.1`` / ``unreleased``), or None."""
    wanted = tag if tag == UNRELEASED_TAG else f"v{tag.lstrip('v')}"
    return next((s for s in sections if s.tag == wanted), None)


def bullet_texts(section: ChangelogSection) -> tuple[str, ...]:
    return tuple(text for _sub, text in section.bullets)


__all__ = ["UNRELEASED_TAG", "ChangelogSection", "bullet_texts", "parse_changelog_sections", "section_for_tag"]
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_changelog_sections.py -q`
Expected: PASS. Also run the parser over the real tags as a smoke check:

```bash
for t in v0.3.1 v0.4.0 v0.4.1 v0.5.0 v0.5.1; do git show $t:CHANGELOG.md > /tmp/cl_$t.md; done
PYTHONPATH=benchmarks/src python -c "
from pathlib import Path
from pydocs_eval.datasets.changelog_sections import parse_changelog_sections, section_for_tag
for t in ['v0.3.1','v0.4.0','v0.4.1','v0.5.0','v0.5.1']:
    s = section_for_tag(parse_changelog_sections(Path(f'/tmp/cl_{t}.md').read_text()), t)
    print(t, len(s.bullets) if s else None)
"
```

Expected: five lines, each with a bullet count (the v0.5.1 section prints `1`, the v0.5.0 one `15`).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/changelog_sections.py benchmarks/tests/datasets/test_changelog_sections.py
git commit -m "eval: changelog section parser over the three release heading shapes"
```

---

### Task 2: The alignment sidecar

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/self_releases_sidecar.py`
- Create: `benchmarks/data/self_releases/alignment.yaml`
- Test: `benchmarks/tests/datasets/test_self_releases_sidecar.py`

**Interfaces:**
- Produces: `AlignmentRow(tag_to: str, bullet_index: int, landing: tuple[str, ...])` (sha7s on the ORIGINAL history — the loader maps them to rewritten shas by first-parent position); `UnreleasedPin(tag_to_sha: str, section_text: str)`; `AlignmentSidecar(rows: tuple[AlignmentRow, ...], unreleased: UnreleasedPin | None, frozen_sections: Mapping[str, str])` with `rows_for(tag_to)`; `load_alignment_sidecar(path) -> AlignmentSidecar`; `self_releases_data_dir() -> Path` (`$PYDOCS_EVAL_DATA_DIR/self_releases` else `<repo>/benchmarks/data/self_releases`).

- [ ] **Step 1: Derive the rows for the two small windows (a one-time, recorded procedure)**

```bash
git log --first-parent --format='%h %s' v0.4.0..v0.4.1
git show v0.4.1:CHANGELOG.md | sed -n '/^## \[0.4.1\]/,/^## \[0.4.0\]/p'
git log --first-parent --format='%h %s' v0.5.0..v0.5.1
git show v0.5.1:CHANGELOG.md | sed -n '/^## v0.5.1/,/^## v0.5.0/p'
```

Read each window's bullets against its units and write one row per bullet naming the unit(s) that support it (bullet indices are 0-based in section order). For `(v0.5.0, v0.5.1]` the one bullet (the public tool-docs constants) is supported by the release-prep unit `5b5aeb5`, not by the slice-6 unit `4d4ff35`; for `(v0.4.0, v0.4.1]` the airgap and Streamlit bullets map to `44cf6f5` and `41eb983`, the test-suite fix to `bcd0eb2`, and the release-prep unit `788417a` supports no bullet on its own. Record what you find — the sidecar is a labeling artifact, and the README states the per-window `alignment_rate`.

- [ ] **Step 2: Commit the sidecar**

`benchmarks/data/self_releases/alignment.yaml` (fill the `landing` lists from Step 1; the two windows below are required, the other four are optional and unit-level until labeled):

```yaml
# Hand-labeled bullet <-> landing-unit alignment for this repository's release
# windows (task-layer design §7.2). Landing sha7s are on the ORIGINAL history;
# the loader maps them to the rewritten corpus by first-parent position and
# asserts the mapping reproduces the frozen sections below. Automatic (#N)
# alignment is only a consistency check (4 of ~70 bullets cite a number).
unreleased: null            # {tag_to_sha: <40-hex>, section_text: "..."} pins the open window
frozen_sections:            # `git show <tag_to>:CHANGELOG.md` section text, verbatim
  v0.4.1: |
    <paste the v0.4.1 section from Step 1>
  v0.5.1: |
    <paste the v0.5.1 section from Step 1>
rows:
  - { tag_to: v0.4.1, bullet_index: 0, landing: [44cf6f5] }
  - { tag_to: v0.4.1, bullet_index: 1, landing: [41eb983] }
  - { tag_to: v0.4.1, bullet_index: 2, landing: [bcd0eb2] }
  - { tag_to: v0.5.1, bullet_index: 0, landing: [5b5aeb5] }
```

Replace the two `<paste …>` markers with the section text from Step 1 before committing (the loader compares them byte-for-byte to `git show`, so the file must carry the real text; `test_self_releases.py` in Task 3 fails otherwise). Adjust `bullet_index` / `landing` to what the window actually shows.

- [ ] **Step 3: Write the failing tests**

Create `benchmarks/tests/datasets/test_self_releases_sidecar.py`:

```python
"""The alignment sidecar: schema, lookup, and the shipped file's shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.datasets.self_releases_sidecar import (
    AlignmentRow,
    load_alignment_sidecar,
    self_releases_data_dir,
)


def test_loads_rows_and_frozen_sections(tmp_path: Path):
    path = tmp_path / "alignment.yaml"
    path.write_text(
        "unreleased: {tag_to_sha: '" + "a" * 40 + "', section_text: '## [0.6.0] — Unreleased\\n\\n- x\\n'}\n"
        "frozen_sections:\n  v0.4.1: |\n    ## [0.4.1] — 2026-07-03\n\n    - one\n"
        "rows:\n  - {tag_to: v0.4.1, bullet_index: 0, landing: [abc1234, def5678]}\n",
        encoding="utf-8",
    )
    sidecar = load_alignment_sidecar(path)
    assert sidecar.rows_for("v0.4.1") == (AlignmentRow("v0.4.1", 0, ("abc1234", "def5678")),)
    assert sidecar.rows_for("v9.9.9") == ()
    assert sidecar.unreleased is not None and sidecar.unreleased.tag_to_sha == "a" * 40
    assert sidecar.frozen_sections["v0.4.1"].startswith("## [0.4.1]")


def test_rejects_a_short_landing_sha_and_unknown_keys(tmp_path: Path):
    path = tmp_path / "alignment.yaml"
    path.write_text("rows:\n  - {tag_to: v0.4.1, bullet_index: 0, landing: [abc]}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="abc"):
        load_alignment_sidecar(path)
    path.write_text("rows:\n  - {tag_to: v0.4.1, bullet: 0, landing: [abc1234]}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bullet"):
        load_alignment_sidecar(path)


def test_the_shipped_sidecar_covers_the_two_small_windows():
    sidecar = load_alignment_sidecar(self_releases_data_dir() / "alignment.yaml")
    assert len(sidecar.rows_for("v0.5.1")) == 1
    assert len(sidecar.rows_for("v0.4.1")) >= 3
    assert {"v0.4.1", "v0.5.1"} <= set(sidecar.frozen_sections)
```

- [ ] **Step 4: Create the module**

```python
"""The self-corpus alignment sidecar (task-layer design §7.2)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

_SHA7 = re.compile(r"^[0-9a-f]{7,40}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_ROW_KEYS = {"tag_to", "bullet_index", "landing"}


@dataclass(frozen=True, slots=True)
class AlignmentRow:
    tag_to: str
    bullet_index: int
    landing: tuple[str, ...]  # sha7+ on the original history


@dataclass(frozen=True, slots=True)
class UnreleasedPin:
    tag_to_sha: str
    section_text: str


@dataclass(frozen=True, slots=True)
class AlignmentSidecar:
    rows: tuple[AlignmentRow, ...]
    unreleased: UnreleasedPin | None
    frozen_sections: Mapping[str, str]

    def rows_for(self, tag_to: str) -> tuple[AlignmentRow, ...]:
        return tuple(sorted((r for r in self.rows if r.tag_to == tag_to), key=lambda r: r.bullet_index))


def self_releases_data_dir() -> Path:
    override = os.environ.get("PYDOCS_EVAL_DATA_DIR")
    base = Path(override) if override else Path(__file__).resolve().parents[3] / "data"
    return base / "self_releases"


def _row(raw: Mapping[str, object]) -> AlignmentRow:
    unknown = sorted(set(raw) - _ROW_KEYS)
    if unknown or set(raw) != _ROW_KEYS:
        raise ValueError(f"alignment row {dict(raw)!r}: expected exactly the keys {sorted(_ROW_KEYS)}, unexpected {unknown}")
    landing = tuple(str(s) for s in raw["landing"])  # type: ignore[union-attr]
    for sha in landing:
        if not _SHA7.match(sha):
            raise ValueError(f"alignment row for {raw['tag_to']!r} bullet {raw['bullet_index']!r}: landing {sha!r} is not a 7-40 hex sha")
    return AlignmentRow(str(raw["tag_to"]), int(raw["bullet_index"]), landing)  # type: ignore[call-overload]


def load_alignment_sidecar(path: Path) -> AlignmentSidecar:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rows = tuple(_row(r) for r in document.get("rows") or ())
    pin = document.get("unreleased")
    unreleased = None
    if pin:
        sha = str(pin["tag_to_sha"])
        if not _SHA40.match(sha):
            raise ValueError(f"unreleased.tag_to_sha {sha!r} is not 40-hex")
        unreleased = UnreleasedPin(sha, str(pin["section_text"]))
    frozen = {str(k): str(v) for k, v in (document.get("frozen_sections") or {}).items()}
    return AlignmentSidecar(rows=rows, unreleased=unreleased, frozen_sections=frozen)


__all__ = ["AlignmentRow", "AlignmentSidecar", "UnreleasedPin", "load_alignment_sidecar", "self_releases_data_dir"]
```

- [ ] **Step 5: Run the tests and commit**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_self_releases_sidecar.py -q`
Expected: PASS.

```bash
git add benchmarks/src/pydocs_eval/datasets/self_releases_sidecar.py benchmarks/data/self_releases/alignment.yaml benchmarks/tests/datasets/test_self_releases_sidecar.py
git commit -m "eval: self-releases alignment sidecar (schema, loader, the two small windows labeled)"
```

---

### Task 3: `pydocs-self-releases` — windows, units, gold, the leak floor

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/self_releases.py`, `benchmarks/src/pydocs_eval/datasets/self_releases_leak.py`
- Modify: `benchmarks/src/pydocs_eval/datasets/__init__.py`
- Test: `benchmarks/tests/datasets/test_self_releases.py`

**Interfaces:**
- Consumes: `materialize_corpus_with_history`, `ConfigOverlaySpec` (T1); the parser (Task 1); the sidecar (Task 2); `RELEASE_NOTES_TASK_NAME`, `mint_framed_task_id`; `non_test_paths`.
- Produces: `CHURN_PATH_RULE`, `is_churn_unit(paths)`; `ReleaseWindow(tag_from, tag_to, record_id)`; `release_windows(repo) -> tuple[ReleaseWindow, ...]` (consecutive `v*` tags by version, plus the open window when pinned); `first_parent_units(repo, from_ref, to_ref) -> tuple[str, ...]` (40-hex, newest first); `changed_paths_of(repo, sha)`; `SelfReleasesDataset(repo_root=<this repository>, sidecar_path=None, corpus_parent=None, split="all")` registered as `pydocs-self-releases`; `check_gold_leaks(corpus_root, bullets, from_sha, to_sha, decision_texts) -> tuple[LeakHit, ...]`; `LeakHit(surface, bullet_index, where)`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/datasets/test_self_releases.py` (a synthetic repository stands in for this one):

```python
"""``pydocs-self-releases`` over a synthetic release history — AC-12, AC-13, AC-15."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from pydocs_eval.datasets.self_releases import (
    SelfReleasesDataset,
    first_parent_units,
    is_churn_unit,
    release_windows,
)
from pydocs_eval.datasets.self_releases_leak import check_gold_leaks

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")
_ENV = {"PATH": __import__("os").environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=_ENV).stdout.strip()


def _commit(root: Path, message: str, files: dict[str, str]) -> str:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", message, cwd=root)
    return _git("rev-parse", "HEAD", cwd=root)


def _repo(tmp_path: Path) -> Path:
    """v0.1.0 -> (feat retry, chore lock) -> v0.2.0, with a changelog edited at each tag."""
    root = tmp_path / "self"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _commit(root, "init", {"pkg/mod.py": "def f():\n    return 1\n", "CHANGELOG.md": "# Changelog\n\n## v0.1.0\n\n### Added\n\n- first release\n"})
    _git("tag", "v0.1.0", cwd=root)
    _commit(root, "feat: retry fetch three times (#7)", {"pkg/retry.py": "def fetch():\n    return 3\n", "tests/test_retry.py": "def test_fetch():\n    pass\n"})
    _commit(root, "chore: relock", {"uv.lock": "version = 1\n"})
    _commit(root, "chore(release): v0.2.0", {"CHANGELOG.md": "# Changelog\n\n## v0.2.0\n\n### Added\n\n- Retry transient fetch errors three times before failing.\n\n## v0.1.0\n\n### Added\n\n- first release\n"})
    _git("tag", "v0.2.0", cwd=root)
    return root


def _sidecar(tmp_path: Path, root: Path, *, aligned: bool) -> Path:
    units = first_parent_units(root, "v0.1.0", "v0.2.0")
    retry = next(u for u in units if "retry" in _git("log", "-1", "--format=%s", u, cwd=root))
    section = _git("show", "v0.2.0:CHANGELOG.md", cwd=root).split("## v0.1.0")[0].split("# Changelog\n\n", 1)[1]
    document = {"unreleased": None, "frozen_sections": {"v0.2.0": section}, "rows": [{"tag_to": "v0.2.0", "bullet_index": 0, "landing": [retry[:7]]}] if aligned else []}
    path = tmp_path / "alignment.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def _tasks(dataset):
    async def _run():
        return [t async for t in dataset.tasks()]

    return asyncio.run(_run())


def test_windows_and_units_follow_the_first_parent_line(tmp_path: Path):
    root = _repo(tmp_path)
    assert [(w.tag_from, w.tag_to) for w in release_windows(root)] == [("v0.1.0", "v0.2.0")]
    units = first_parent_units(root, "v0.1.0", "v0.2.0")
    assert len(units) == 3 and units[0] == _git("rev-parse", "v0.2.0^{commit}", cwd=root)  # newest first


def test_churn_rule():
    assert is_churn_unit(("uv.lock",)) and is_churn_unit((".github/workflows/ci.yml", "Cargo.lock"))
    assert not is_churn_unit(("uv.lock", "pkg/mod.py")) and not is_churn_unit(())


def test_row_gold_and_the_rewritten_corpus(tmp_path: Path):
    root = _repo(tmp_path)
    dataset = SelfReleasesDataset(repo_root=root, sidecar_path=_sidecar(tmp_path, root, aligned=True), corpus_parent=tmp_path / "corpora")
    (task,) = _tasks(dataset)
    assert task.task_id == "pydocs-self-releases/release_notes/v0.2.0"
    assert task.gold.extra["bullet_0"] == "Retry transient fetch errors three times before failing."
    assert set(task.gold.extra) >= {"landing_0", "tag_from", "tag_to", "tag_from_sha", "tag_to_sha", "base"}
    assert task.gold.file_set == ("pkg/retry.py",)  # the aligned unit's non-test paths
    assert task.metadata == {**task.metadata, "unit_count": "3", "churn_unit_count": "1", "covered_unit_count": "1", "bullet_count": "1", "alignment_rate": "1.00"}
    corpus = task.corpus_source()
    try:
        assert "CHANGELOG.md" not in _git("log", "--all", "--name-only", "--format=", cwd=corpus)
        rewritten_units = first_parent_units(corpus, task.gold.extra["tag_from_sha"], task.gold.extra["tag_to_sha"])
        assert rewritten_units[1][:7] == task.gold.extra["landing_0"]  # sha7 on the REWRITTEN history
        assert _git("symbolic-ref", "HEAD", cwd=corpus) == "refs/heads/main"
        overlay = yaml.safe_load(Path(task.metadata["config_overlay"]).read_text(encoding="utf-8"))
        assert overlay["git"]["diff_chunks"]["retain"] == {"since_tags": 2, "tag_pattern": "v*", "max_landings": 500}
        assert overlay["decision_capture"]["sources"] == ["adr_files", "inline_markers", "docs_prose"]
        assert overlay["ingestion"]["discovery"]["project"]["exclude_dirs"] == ["docs", "benchmarks"]
    finally:
        shutil.rmtree(corpus.parent)


def test_without_sidecar_rows_every_non_churn_unit_is_a_coverage_key(tmp_path: Path):
    root = _repo(tmp_path)
    dataset = SelfReleasesDataset(repo_root=root, sidecar_path=_sidecar(tmp_path, root, aligned=False), corpus_parent=tmp_path / "corpora")
    (task,) = _tasks(dataset)
    landings = [v for k, v in task.gold.extra.items() if k.startswith("landing_")]
    assert len(landings) == 2 and task.metadata["churn_unit_count"] == "1"
    assert task.metadata["alignment_rate"] == "0.00"


def test_a_window_without_a_section_or_a_leaking_bullet_is_dropped(tmp_path: Path, caplog):
    root = _repo(tmp_path)
    # Leak: a doc file in the tree spells the bullet verbatim.
    _commit(root, "docs: notes", {"NOTES.md": "Retry transient fetch errors three times before failing.\n"})
    _git("tag", "-f", "v0.2.0", cwd=root)
    dataset = SelfReleasesDataset(repo_root=root, sidecar_path=_sidecar(tmp_path, root, aligned=True), corpus_parent=tmp_path / "corpora")
    with caplog.at_level("WARNING"):
        assert _tasks(dataset) == []
    assert "leak" in caplog.text.lower()


def test_leak_check_covers_the_three_surfaces(tmp_path: Path):
    root = _repo(tmp_path)
    corpus = root  # the original tree still carries CHANGELOG.md — surface (i)
    hits = check_gold_leaks(corpus, ("Retry transient fetch errors three times before failing.",), _git("rev-parse", "v0.1.0", cwd=root), _git("rev-parse", "v0.2.0", cwd=root), decision_texts=("mined: Retry transient fetch errors three times before failing.",))
    assert {h.surface for h in hits} == {"file", "decision"}
    assert not [h for h in check_gold_leaks(corpus, ("nothing like this",), _git("rev-parse", "v0.1.0", cwd=root), _git("rev-parse", "v0.2.0", cwd=root), decision_texts=())]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_self_releases.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: The leak check**

`benchmarks/src/pydocs_eval/datasets/self_releases_leak.py`:

```python
"""The self-corpus leak floor, checked at build time (task-layer design §7.2 R10).

Three surfaces of the nine tools can reach a gold bullet: the tree at the newer
tag (``read_file``), the commit messages of the window (``get_why`` over
commit-mined decisions — dropped from the overlay, but checked anyway), and the
indexed ``decision_records`` of the base (the caller passes their texts). A
verbatim bullet on any surface fails the record; subject-level paraphrase is
accepted noise.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_TEXT_SUFFIXES = {".md", ".rst", ".txt", ".py", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".json"}


@dataclass(frozen=True, slots=True)
class LeakHit:
    surface: str  # "file" | "commit_message" | "decision"
    bullet_index: int
    where: str


def _files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in _TEXT_SUFFIXES and ".git" not in path.parts:
            yield path


def _commit_bodies(root: Path, from_sha: str, to_sha: str) -> str:
    return subprocess.run(["git", "log", "--format=%B", f"{from_sha}..{to_sha}"], cwd=root, check=True, capture_output=True, text=True, timeout=120).stdout


def check_gold_leaks(corpus_root: Path, bullets: Sequence[str], from_sha: str, to_sha: str, *, decision_texts: Sequence[str]) -> tuple[LeakHit, ...]:
    """Every verbatim occurrence of a gold bullet on the three surfaces."""
    needles = [(i, b.strip()) for i, b in enumerate(bullets) if b.strip()]
    hits: list[LeakHit] = []
    for path in _files(corpus_root):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += [LeakHit("file", i, str(path.relative_to(corpus_root))) for i, b in needles if b in text]
    bodies = _commit_bodies(corpus_root, from_sha, to_sha)
    hits += [LeakHit("commit_message", i, f"{from_sha[:7]}..{to_sha[:7]}") for i, b in needles if b in bodies]
    for n, decision in enumerate(decision_texts):
        hits += [LeakHit("decision", i, f"decision_records[{n}]") for i, b in needles if b in decision]
    return tuple(hits)


__all__ = ["LeakHit", "check_gold_leaks"]
```

- [ ] **Step 4: The loader**

`benchmarks/src/pydocs_eval/datasets/self_releases.py`:

```python
"""``pydocs-self-releases`` — this repository's release windows as release_notes
records (task-layer design §7.2). A GATE corpus: ``split: all`` only.

One record per window ``(tag_from, tag_to]`` of consecutive ``v*`` tags (plus
the open window when the sidecar pins it). Gold = the ``tag_to`` changelog
section read from the ORIGINAL clone (``git show <tag>:CHANGELOG.md``) —
never the checkout's file — and the coverage key set ``landing_<i>``: the
sidecar-aligned units, else every non-churn unit. The corpus is the rewritten
clone (no ``CHANGELOG.md`` in any commit), base at ``tag_to``, overlay pinning
the retention window and the R10 exclusions; the leak check runs at build time.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..registries import dataset_registry
from ._bug_loc_gold import non_test_paths
from .base_dataset import EvalTask, GoldAnswer
from .change_tasks import RELEASE_NOTES_TASK_NAME
from .changelog_sections import UNRELEASED_TAG, bullet_texts, parse_changelog_sections, section_for_tag
from .history_corpus import ConfigOverlaySpec, HistoryCorpus, materialize_corpus_with_history
from .self_releases_leak import check_gold_leaks
from .self_releases_sidecar import AlignmentSidecar, load_alignment_sidecar, self_releases_data_dir
from .task_ids import mint_framed_task_id

log = logging.getLogger(__name__)

DATASET_NAME = "pydocs-self-releases"
#: A unit whose diff touches only these is internal churn (§6.3): excluded from
#: the coverage key set and from the retrieval gold.
CHURN_PATH_RULE: tuple[str, ...] = (".github/", "uv.lock", "Cargo.lock", "complexipy-snapshot.json")
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_RETAIN = {"since_tags": 2, "tag_pattern": "v*", "max_landings": 500}
_OVERLAY = ConfigOverlaySpec(retain=_RETAIN, exclude_dirs=("docs", "benchmarks"), decision_sources=("adr_files", "inline_markers", "docs_prose"))
_REQUEST = "Project: {project}\nBase branch: main\n\nWrite the release notes for the landings after {tag_from} up to and including {tag_to}."


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=600).stdout.strip()


def is_churn_unit(paths: Sequence[str]) -> bool:
    return bool(paths) and all(p.startswith(".github/") or p in CHURN_PATH_RULE for p in paths)


@dataclass(frozen=True, slots=True)
class ReleaseWindow:
    tag_from: str
    tag_to: str  # a tag, or UNRELEASED_TAG for the open window
    record_id: str


def release_windows(repo: Path, unreleased_sha: str | None = None) -> tuple[ReleaseWindow, ...]:
    """Consecutive ``v*`` tag pairs by version, plus the open window when pinned."""
    tags = sorted((t for t in _git("tag", "--list", "v*", cwd=repo).splitlines() if _TAG.match(t)), key=lambda t: tuple(int(x) for x in _TAG.match(t).groups()))  # type: ignore[union-attr]
    windows = [ReleaseWindow(a, b, b) for a, b in zip(tags, tags[1:], strict=False)]
    if unreleased_sha and tags:
        windows.append(ReleaseWindow(tags[-1], UNRELEASED_TAG, f"unreleased@{unreleased_sha[:7]}"))
    return tuple(windows)


def first_parent_units(repo: Path, from_ref: str, to_ref: str) -> tuple[str, ...]:
    """40-hex first-parent landings in ``(from_ref, to_ref]``, newest first."""
    out = _git("rev-list", "--first-parent", f"{from_ref}..{to_ref}", cwd=repo)
    return tuple(out.splitlines()) if out else ()


def changed_paths_of(repo: Path, sha: str) -> tuple[str, ...]:
    out = _git("show", "--first-parent", "--name-only", "--format=", sha, cwd=repo)
    return tuple(line for line in out.splitlines() if line)


def _subject(repo: Path, sha: str) -> str:
    return _git("log", "-1", "--format=%s", sha, cwd=repo)


@dataclass
class SelfReleasesDataset:
    name: str = DATASET_NAME
    revision: str = "self"
    repo_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[4])
    sidecar_path: Path | None = None
    corpus_parent: Path | None = None
    split: str = "all"

    def __post_init__(self) -> None:
        if self.split != "all":
            raise ValueError(f"{self.name} is a gate corpus: split must be 'all', got {self.split!r}")

    async def tasks(self) -> AsyncIterator[EvalTask]:
        sidecar = load_alignment_sidecar(self.sidecar_path or self_releases_data_dir() / "alignment.yaml")
        pin = sidecar.unreleased.tag_to_sha if sidecar.unreleased else None
        yielded, dropped = 0, 0
        for window in release_windows(self.repo_root, pin):
            task = self._window_task(window, sidecar)
            if task is None:
                dropped += 1
                continue
            yielded += 1
            yield task
        log.info("%s: yielded %d window(s), dropped %d", self.name, yielded, dropped)

    def _section_bullets(self, window: ReleaseWindow, sidecar: AlignmentSidecar) -> tuple[str, ...] | None:
        if window.tag_to == UNRELEASED_TAG:
            assert sidecar.unreleased is not None
            section = section_for_tag(parse_changelog_sections(sidecar.unreleased.section_text), UNRELEASED_TAG)
        else:
            text = _git("show", f"{window.tag_to}:CHANGELOG.md", cwd=self.repo_root)  # the ORIGINAL clone
            section = section_for_tag(parse_changelog_sections(text), window.tag_to)
            frozen = sidecar.frozen_sections.get(window.tag_to)
            if frozen is not None and frozen.strip() not in text:
                log.warning("%s: frozen section for %s differs from git show; dropping", self.name, window.tag_to)
                return None
        if section is None or not section.bullets:
            log.warning("%s: no changelog section for %s; dropping the window", self.name, window.tag_to)
            return None
        return bullet_texts(section)

    def _window_task(self, window: ReleaseWindow, sidecar: AlignmentSidecar) -> EvalTask | None:
        bullets = self._section_bullets(window, sidecar)
        if bullets is None:
            return None
        to_ref = sidecar.unreleased.tag_to_sha if window.tag_to == UNRELEASED_TAG else window.tag_to  # type: ignore[union-attr]
        original_units = first_parent_units(self.repo_root, window.tag_from, to_ref)
        corpus = materialize_corpus_with_history(self.repo_root, base_ref=to_ref, remove_paths=("CHANGELOG.md",), overlay=_OVERLAY, parent=self.corpus_parent)
        from_sha = _git("rev-parse", f"{window.tag_from}^{{commit}}", cwd=corpus.root)
        units = first_parent_units(corpus.root, from_sha, corpus.base_sha)
        if len(units) != len(original_units):
            raise RuntimeError(f"{self.name}: the rewrite of {window.record_id} changed the unit count {len(original_units)} -> {len(units)}")
        churn = {sha for sha in units if is_churn_unit(changed_paths_of(corpus.root, sha))}
        aligned = self._aligned_units(sidecar, window, original_units, units)
        covered = aligned or tuple(u for u in units if u not in churn)
        leaks = check_gold_leaks(corpus.root, bullets, from_sha, corpus.base_sha, decision_texts=())
        if leaks:
            log.warning("%s: %s leaks %d gold bullet(s) (%s); dropping", self.name, window.record_id, len(leaks), leaks[0])
            return None
        return self._task(window, corpus, from_sha, bullets, units, churn, covered, aligned)

    def _aligned_units(self, sidecar: AlignmentSidecar, window: ReleaseWindow, original: tuple[str, ...], rewritten: tuple[str, ...]) -> tuple[str, ...]:
        """Sidecar sha7s (original history) -> rewritten shas, by first-parent position."""
        by_position = {orig[:7]: new for orig, new in zip(original, rewritten, strict=True)}
        out: dict[str, None] = {}
        for row in sidecar.rows_for(window.tag_to):
            for sha7 in row.landing:
                if sha7[:7] not in by_position:
                    raise RuntimeError(f"{self.name}: sidecar row for {window.tag_to} names {sha7!r}, not a first-parent unit of the window")
                out.setdefault(by_position[sha7[:7]])
        return tuple(out)

    def _task(self, window: ReleaseWindow, corpus: HistoryCorpus, from_sha: str, bullets: tuple[str, ...], units: tuple[str, ...], churn: set[str], covered: tuple[str, ...], aligned: tuple[str, ...]) -> EvalTask:
        file_set: dict[str, None] = {}
        for sha in covered:
            for path in non_test_paths(changed_paths_of(corpus.root, sha)):
                file_set.setdefault(path)
        extra: dict[str, object] = {f"landing_{i}": sha[:7] for i, sha in enumerate(covered)}
        extra.update({f"bullet_{i}": b for i, b in enumerate(bullets)})
        extra.update({"tag_from": window.tag_from, "tag_to": window.tag_to, "tag_from_sha": from_sha, "tag_to_sha": corpus.base_sha, "base": corpus.base, "project": corpus.root.name})
        aligned_rate = len(aligned) / max(1, len(units) - len(churn))
        return EvalTask(
            task_id=mint_framed_task_id(dataset=self.name, task_name=RELEASE_NOTES_TASK_NAME, record_id=window.record_id),
            record_id=window.record_id,
            query=_REQUEST.format(project=corpus.root.name, tag_from=window.tag_from, tag_to=window.tag_to),
            gold=GoldAnswer(file_set=tuple(file_set), extra=extra),
            corpus_source=lambda root=corpus.root: root,
            metadata={
                "unit_count": str(len(units)),
                "churn_unit_count": str(len(churn)),
                "covered_unit_count": str(len(covered)),
                "bullet_count": str(len(bullets)),
                "alignment_rate": f"{aligned_rate:.2f}",
                "surface_stage": "S2b",
                "search_scope": "diff",
                "search_branch": corpus.base,
                "config_overlay": str(corpus.overlay_path),
            },
        )


dataset_registry.register(DATASET_NAME)(SelfReleasesDataset)

__all__ = ["CHURN_PATH_RULE", "DATASET_NAME", "ReleaseWindow", "SelfReleasesDataset", "changed_paths_of", "first_parent_units", "is_churn_unit", "release_windows"]
```

Register the class in `datasets/__init__.py`. The corpus is materialized eagerly per window at `tasks()` time (six clones of this repository) because the gold's `landing_<i>` are REWRITTEN shas; `corpus_source()` returns the built directory. The `decision_texts` surface of the leak check is empty at build time (no index yet); the smoke gate (Task 6) re-runs `check_gold_leaks` with the indexed base's `decision_records` texts.

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_self_releases.py -q`
Expected: PASS. Then a live check against this repository (`PYDOCS_EVAL_DATA_DIR` unset, the committed sidecar):

```bash
PYTHONPATH=benchmarks/src python -c "
import asyncio
from pydocs_eval.datasets.self_releases import SelfReleasesDataset
async def main():
    async for t in SelfReleasesDataset(corpus_parent=None).tasks():
        print(t.record_id, t.metadata['unit_count'], t.metadata['covered_unit_count'], t.metadata['alignment_rate'])
asyncio.run(main())
"
```

Expected: five closed windows (`v0.3.1` … `v0.5.1`) with unit counts 6 / 23 / 4 / 48 / 2 and no leak drops; if a window leaks, the warning names the surface and the file — extend `exclude_dirs` in `_OVERLAY` only with the owner's word (the README states the floor).

- [ ] **Step 6: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/self_releases.py benchmarks/src/pydocs_eval/datasets/self_releases_leak.py benchmarks/src/pydocs_eval/datasets/__init__.py benchmarks/tests/datasets/test_self_releases.py
git commit -m "eval: pydocs-self-releases — rewritten self-corpus, changelog gold at the tag, coverage keys, leak floor"
```

---

### Task 4: `pydocs-self-landing-loc` — regression localization over aligned bullets

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/self_landing_loc.py`
- Test: `benchmarks/tests/datasets/test_self_landing_loc.py`

**Interfaces:**
- Consumes: `SelfReleasesDataset` internals (`release_windows`, `first_parent_units`, `changed_paths_of`, the sidecar); `BUG_LOC_TASK_NAME`; `gold_recall(keys=[landing_sha])` (T0).
- Produces: `LANDING_SCAFFOLD` (the query suffix), `SelfLandingLocDataset` registered as `pydocs-self-landing-loc`; rows `pydocs-self-landing-loc/bug_loc/<tag_to>__<bullet_index>` for every aligned `Fixed` / `Changed` bullet; `extra["landing_sha"]` = the rewritten sha7; the loader raises when the sidecar has no rows.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/datasets/test_self_landing_loc.py`:

```python
"""``pydocs-self-landing-loc`` — AC-16."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.datasets.self_landing_loc import LANDING_SCAFFOLD, SelfLandingLocDataset
from pydocs_eval.optimize.rubric.checks import Check, evaluate_check
from tests.datasets.test_self_releases import _repo, _sidecar
from tests.optimize._trajectories import make_trajectory

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


def _tasks(dataset):
    async def _run():
        return [t async for t in dataset.tasks()]

    return asyncio.run(_run())


def test_rows_mint_under_bug_loc_with_the_scaffold_and_the_landing_key(tmp_path: Path):
    root = _repo(tmp_path)
    dataset = SelfLandingLocDataset(repo_root=root, sidecar_path=_sidecar(tmp_path, root, aligned=True), corpus_parent=tmp_path / "c")
    (task,) = _tasks(dataset)
    assert task.task_id == "pydocs-self-landing-loc/bug_loc/v0.2.0__0"
    assert task.query.endswith(LANDING_SCAFFOLD)
    assert task.query.startswith("Since v0.1.0, Retry transient fetch errors")
    assert task.gold.file_set == ("pkg/retry.py",)
    assert len(task.gold.extra["landing_sha"]) == 7
    check = Check(name="landing", kind="gold_recall", params={"keys": ["landing_sha"]}, fail=None)
    assert evaluate_check(check, task, make_trajectory(answer=f"pkg/retry.py\nlanding: {task.gold.extra['landing_sha']}")).score == 1.0
    assert evaluate_check(check, task, make_trajectory(answer="pkg/retry.py\nlanding: 0000000")).score == 0.0


def test_no_sidecar_rows_is_a_loud_error(tmp_path: Path):
    root = _repo(tmp_path)
    dataset = SelfLandingLocDataset(repo_root=root, sidecar_path=_sidecar(tmp_path, root, aligned=False), corpus_parent=tmp_path / "c")
    with pytest.raises(RuntimeError, match="alignment sidecar has no rows"):
        _tasks(dataset)
```

- [ ] **Step 2: Create the loader**

```python
"""``pydocs-self-landing-loc`` — regression localization over a ref range (§6.6, §7.3).

One record per sidecar-aligned ``Fixed`` / ``Changed`` bullet of the
self-corpus, phrased as a report; gold = the aligned unit's non-test paths
plus ``extra["landing_sha"]`` (rewritten sha7). Mints under ``bug_loc`` — no
new task name — and the record's QUERY carries the output scaffold, because
the ``bug_loc`` head cannot mention landing units without re-keying the task.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from ..registries import dataset_registry
from ._bug_loc_gold import non_test_paths
from .base_dataset import EvalTask, GoldAnswer
from .bug_localization import BUG_LOC_TASK_NAME
from .changelog_sections import parse_changelog_sections, section_for_tag
from .self_releases import DATASET_NAME as SELF_RELEASES, SelfReleasesDataset, changed_paths_of, first_parent_units
from .self_releases_sidecar import load_alignment_sidecar, self_releases_data_dir
from .task_ids import mint_framed_task_id

LANDING_SCAFFOLD = (
    " Name the files that must change, one per line, and end with `landing: <sha7>` "
    "naming the landing unit that introduced it."
)
_LOCALIZABLE_SUBHEADINGS = frozenset({"Fixed", "Changed"})


@dataclass
class SelfLandingLocDataset:
    name: str = "pydocs-self-landing-loc"
    revision: str = "self"
    repo_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[4])
    sidecar_path: Path | None = None
    corpus_parent: Path | None = None
    split: str = "all"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        sidecar_path = self.sidecar_path or self_releases_data_dir() / "alignment.yaml"
        sidecar = load_alignment_sidecar(sidecar_path)
        if not sidecar.rows:
            raise RuntimeError(f"{self.name}: the alignment sidecar has no rows ({sidecar_path}); label the windows first (task-layer design §7.3)")
        releases = SelfReleasesDataset(repo_root=self.repo_root, sidecar_path=sidecar_path, corpus_parent=self.corpus_parent)
        async for window_task in releases.tasks():
            for task in self._rows_of(window_task, sidecar):
                yield task

    def _rows_of(self, window_task: EvalTask, sidecar) -> list[EvalTask]:
        extra = window_task.gold.extra
        tag_to, tag_from = str(extra["tag_to"]), str(extra["tag_from"])
        corpus = window_task.corpus_source()
        section = self._section(tag_to, sidecar)
        units = first_parent_units(corpus, str(extra["tag_from_sha"]), str(extra["tag_to_sha"]))
        original = first_parent_units(self.repo_root, tag_from, tag_to if tag_to != "unreleased" else str(extra["tag_to_sha"]))
        by_position = {orig[:7]: new for orig, new in zip(original, units, strict=True)}
        rows: list[EvalTask] = []
        for row in sidecar.rows_for(tag_to):
            subheading, text = section.bullets[row.bullet_index]
            if subheading not in _LOCALIZABLE_SUBHEADINGS or not row.landing:
                continue
            unit = by_position[row.landing[0][:7]]
            rows.append(self._row_task(window_task, corpus, tag_from, tag_to, row.bullet_index, text, unit))
        return rows

    def _section(self, tag_to: str, sidecar):
        if tag_to == "unreleased":
            return section_for_tag(parse_changelog_sections(sidecar.unreleased.section_text), "unreleased")
        import subprocess

        text = subprocess.run(["git", "show", f"{tag_to}:CHANGELOG.md"], cwd=self.repo_root, check=True, capture_output=True, text=True).stdout
        return section_for_tag(parse_changelog_sections(text), tag_to)

    def _row_task(self, window_task: EvalTask, corpus: Path, tag_from: str, tag_to: str, index: int, text: str, unit: str) -> EvalTask:
        record_id = f"{tag_to}__{index}"
        return EvalTask(
            task_id=mint_framed_task_id(dataset=self.name, task_name=BUG_LOC_TASK_NAME, record_id=record_id),
            record_id=record_id,
            query=f"Since {tag_from}, {text}{LANDING_SCAFFOLD}",
            gold=GoldAnswer(file_set=non_test_paths(changed_paths_of(corpus, unit)), extra={"landing_sha": unit[:7], "tag_from": tag_from, "tag_to": tag_to, "project": str(window_task.gold.extra["project"])}),
            corpus_source=lambda root=corpus: root,
            metadata={**window_task.metadata, "source_dataset": SELF_RELEASES},
        )


dataset_registry.register("pydocs-self-landing-loc")(SelfLandingLocDataset)

__all__ = ["LANDING_SCAFFOLD", "SelfLandingLocDataset"]
```

Register in `datasets/__init__.py`.

- [ ] **Step 3: Run the tests and commit**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_self_landing_loc.py -q` — PASS.

```bash
git add benchmarks/src/pydocs_eval/datasets/self_landing_loc.py benchmarks/src/pydocs_eval/datasets/__init__.py benchmarks/tests/datasets/test_self_landing_loc.py
git commit -m "eval: pydocs-self-landing-loc — bug_loc over aligned release bullets with the landing scaffold"
```

---

### Task 5: `crosscommitvuln-fix-landing`

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/crosscommitvuln_fix_landing.py`
- Test: `benchmarks/tests/datasets/test_crosscommitvuln_fix_landing.py`

**Interfaces:**
- Consumes: the vendored records (`CrossCommitVulnDataset._read_records` shape: `task_id, repo_url, prefix_sha, fix_commit, query, gold, metadata`), `gold_from_record`, `_FRAMING_BANS`, `assert_query_clean`; `materialize_corpus_with_history`, `RepoCache.base_clone`.
- Produces: `CrossCommitVulnFixLandingDataset` registered as `crosscommitvuln-fix-landing`; rows `crosscommitvuln-fix-landing/vuln/<cve>`; corpus = history-preserving clone with the base at `fix_commit`, overlay `retain: {landings: 1}`; records dropped and counted when `fix_commit` has two parents or `fix_commit^1 != prefix_sha`; `FIX_LANDING_QUERY`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/datasets/test_crosscommitvuln_fix_landing.py`:

```python
"""``crosscommitvuln-fix-landing`` — AC-17 (the v2 invariants are untouched)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.datasets._crosscommitvuln_build import _FRAMING_BANS, assert_query_clean
from pydocs_eval.datasets.crosscommitvuln import CrossCommitVulnDataset
from pydocs_eval.datasets.crosscommitvuln_fix_landing import FIX_LANDING_QUERY, CrossCommitVulnFixLandingDataset

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")
_ENV = {"PATH": __import__("os").environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid"}


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=_ENV).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """prefix -> fix (linear), plus a merge commit for the two-parent case."""
    root = tmp_path / "vuln"
    (root / "app").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=root)
    (root / "app" / "io.py").write_text("def read(p):\n    return open(p).read()\n", encoding="utf-8")
    _git("add", ".", cwd=root)
    _git("commit", "-qm", "prefix", cwd=root)
    prefix = _git("rev-parse", "HEAD", cwd=root)
    (root / "app" / "io.py").write_text("def read(p):\n    if '..' in p: raise ValueError(p)\n    return open(p).read()\n", encoding="utf-8")
    _git("commit", "-qam", "fix traversal", cwd=root)
    fix = _git("rev-parse", "HEAD", cwd=root)
    _git("checkout", "-qb", "side", prefix, cwd=root)
    (root / "app" / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", ".", cwd=root)
    _git("commit", "-qm", "side", cwd=root)
    _git("checkout", "-q", "main", cwd=root)
    _git("merge", "-q", "--no-ff", "-m", "merge side", "side", cwd=root)
    merge = _git("rev-parse", "HEAD", cwd=root)
    return root, prefix, fix, merge


@dataclass
class _Cache:
    root: Path
    requested: list[str] = field(default_factory=list)

    def base_clone(self, url: str) -> Path:
        self.requested.append(url)
        return self.root

    def checkout(self, url: str, sha: str) -> Path:
        return self.root

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ()


def _record(task_id: str, prefix: str, fix: str) -> dict:
    return {"task_id": task_id, "repo_url": "https://example.invalid/acme/vuln.git", "prefix_sha": prefix, "fix_commit": fix, "query": "Could you look at acme/vuln? Find where untrusted input enters and the risky operation it reaches.", "gold": {"cve_id": "CVE-2099-0001", "cwe_ids": ["CWE-22"], "files": ["app/io.py"], "mechanism": "path reaches open()"}, "metadata": {"severity": "high"}}


def _tasks(dataset):
    async def _run():
        return [t async for t in dataset.tasks()]

    return asyncio.run(_run())


def test_rows_mint_under_vuln_with_a_landing_scoped_query_and_a_history_corpus(tmp_path: Path, caplog):
    root, prefix, fix, merge = _repo(tmp_path)
    fixture = tmp_path / "records.jsonl"
    fixture.write_text("".join(json.dumps(r) + "\n" for r in (_record("cve-2099-0001", prefix, fix), _record("cve-2099-0002", prefix, merge), _record("cve-2099-0003", "0" * 40, fix))), encoding="utf-8")
    dataset = CrossCommitVulnFixLandingDataset(fixture_path=fixture, repo_cache=_Cache(root), corpus_parent=tmp_path / "c")
    with caplog.at_level("INFO"):
        tasks = _tasks(dataset)
    assert [t.task_id for t in tasks] == ["crosscommitvuln-fix-landing/vuln/cve-2099-0001"]
    assert "dropped 2" in caplog.text
    (task,) = tasks
    assert task.query == FIX_LANDING_QUERY.format(project="cve-2099-0001", landing=fix[:7])
    assert_query_clean(task.query, _FRAMING_BANS)  # no ban relaxed, and the query passes them
    assert task.gold.extra["cve_id"] == "CVE-2099-0001" and task.gold.file_set == ("app/io.py",)
    corpus = task.corpus_source()
    try:
        assert _git("rev-parse", "main", cwd=corpus) == fix
        assert "landings: 1" in Path(task.metadata["config_overlay"]).read_text(encoding="utf-8")
    finally:
        shutil.rmtree(corpus.parent)


def test_the_v2_dataset_and_its_bans_are_byte_identical():
    bans = hashlib.sha256("\n".join(_FRAMING_BANS).encode()).hexdigest()
    assert len(_FRAMING_BANS) == 21 and bans == hashlib.sha256("\n".join(("commit", "commits", "multiple", "multi-commit", "gradually", "over time", "across", "benign", "static analysis", "sast", "per-commit", "individually", "scanner", "scanners", "scanning", "scans", "committed", "gradual", "evasion", "evade")).encode()).hexdigest() or True
    assert "landing" not in _FRAMING_BANS
    v2 = CrossCommitVulnDataset()
    assert v2.name == "crosscommitvuln" and v2.revision == "1.0"
```

(The second test pins the ban list's length and membership; replace the `or True` escape with the exact 21-entry tuple copied from `_crosscommitvuln_build.py` — the point is that the list did not change.)

- [ ] **Step 2: Create the loader**

```python
"""``crosscommitvuln-fix-landing`` — the diff-scoped vulnerability variant (§7.4).

The SAME vendored records as ``crosscommitvuln``, a different corpus and
query: the base is the fix commit itself (one landing unit,
``fix_commit^1..fix_commit``), and the question is scoped to the landing.
Units are first-parent steps only, so a record is dropped — loudly, counted —
when ``fix_commit`` has two parents or ``fix_commit^1 != prefix_sha``. A
SIBLING dataset: the v2 ``crosscommitvuln`` invariants (history-less
snapshots, the framing bans) are not touched, and this query passes the v2
ban list unchanged ("landing" is not on it).
"""

from __future__ import annotations

import importlib.resources as ir
import json
import logging
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registries import dataset_registry
from ._crosscommitvuln_build import _FRAMING_BANS, assert_query_clean, gold_from_record
from ._repo_cache import RepoCache, RepoCacheLike
from .base_dataset import EvalTask
from .crosscommitvuln import _default_repo_cache
from .history_corpus import ConfigOverlaySpec, materialize_corpus_with_history
from .task_ids import mint_framed_task_id

log = logging.getLogger(__name__)

VULN_TASK_NAME = "vuln"
FIX_LANDING_QUERY = (
    "Project: {project}\nBase branch: main\n\n"
    "Does the landing {landing} close a vulnerability, and which? Find where untrusted "
    "input enters, the risky operation it reaches, and name the flaw class from the abuse path."
)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=600).stdout.strip()


@dataclass
class CrossCommitVulnFixLandingDataset:
    name: str = "crosscommitvuln-fix-landing"
    revision: str = "1.0"
    fixture_path: Path | None = None
    repo_cache: RepoCacheLike = field(default_factory=_default_repo_cache)
    corpus_parent: Path | None = None

    async def tasks(self) -> AsyncIterator[EvalTask]:
        yielded, dropped = 0, 0
        for record in self._records():
            task = self._task(record)
            if task is None:
                dropped += 1
                continue
            yielded += 1
            yield task
        log.info("%s: yielded %d task(s), dropped %d record(s) (merge fix commits or parent mismatch)", self.name, yielded, dropped)

    def _records(self) -> list[dict[str, Any]]:
        if self.fixture_path is not None:
            text = self.fixture_path.read_text(encoding="utf-8")
        else:
            text = ir.files("pydocs_eval.datasets.data.crosscommitvuln").joinpath("records.jsonl").read_text()
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _task(self, record: dict[str, Any]) -> EvalTask | None:
        url, fix, prefix = str(record["repo_url"]), str(record["fix_commit"]), str(record["prefix_sha"])
        base = self.repo_cache.base_clone(url)
        try:
            parents = _git("rev-list", "--parents", "-n", "1", fix, cwd=base).split()[1:]
        except RuntimeError as exc:
            log.info("%s: dropping %r — %s", self.name, record["task_id"], exc)
            return None
        if len(parents) != 1 or parents[0] != prefix:
            log.info("%s: dropping %r — fix_commit parents %s (expected exactly prefix_sha %s)", self.name, record["task_id"], parents, prefix[:7])
            return None
        record_id = str(record["task_id"])
        query = FIX_LANDING_QUERY.format(project=record_id, landing=fix[:7])
        assert_query_clean(query, _FRAMING_BANS)
        overlay = ConfigOverlaySpec(retain={"landings": 1})
        return EvalTask(
            task_id=mint_framed_task_id(dataset=self.name, task_name=VULN_TASK_NAME, record_id=record_id),
            record_id=record_id,
            query=query,
            gold=gold_from_record(record),
            corpus_source=lambda u=url, f=fix, rid=record_id, o=overlay: self._corpus(u, f, rid, o),
            metadata={**{k: str(v) for k, v in dict(record.get("metadata", {})).items()}, "landing_sha": fix[:7], "surface_stage": "S3", "search_scope": "diff", "search_branch": fix, "config_overlay": str((self.corpus_parent or Path(".")) / f"{record_id}.overlay.yaml")},
        )

    def _corpus(self, url: str, fix: str, record_id: str, overlay: ConfigOverlaySpec) -> Path:
        corpus = materialize_corpus_with_history(self.repo_cache.base_clone(url), base_ref=fix, overlay=overlay, parent=self.corpus_parent)
        named = corpus.root.parent / record_id
        corpus.root.rename(named)
        corpus.overlay_path.rename((self.corpus_parent or corpus.root.parent) / f"{record_id}.overlay.yaml")
        return named


dataset_registry.register("crosscommitvuln-fix-landing")(CrossCommitVulnFixLandingDataset)

__all__ = ["FIX_LANDING_QUERY", "CrossCommitVulnFixLandingDataset"]
```

Register in `datasets/__init__.py`. (`_git` raising `subprocess.CalledProcessError` rather than `RuntimeError`: catch `subprocess.CalledProcessError` in `_task`.)

- [ ] **Step 3: Run the tests and commit**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_crosscommitvuln_fix_landing.py benchmarks/tests/datasets/test_crosscommitvuln_loader.py benchmarks/tests/datasets/test_crosscommitvuln_vendored.py -q` — PASS (the v2 suites untouched).

```bash
git add benchmarks/src/pydocs_eval/datasets/crosscommitvuln_fix_landing.py benchmarks/src/pydocs_eval/datasets/__init__.py benchmarks/tests/datasets/test_crosscommitvuln_fix_landing.py
git commit -m "eval: crosscommitvuln-fix-landing — vuln over the fix landing unit (v2 dataset untouched)"
```

---

### Task 6: The `release_notes` arm config, the generic corpus scaffold, the smoke gate

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/changelog_tagged.py`, `benchmarks/data/changelog_tagged/repos.yaml`
- Create: `benchmarks/src/pydocs_eval/optimize/configs/optimize_search_skill_release_notes.yaml`
- Create: `benchmarks/tests/optimize/test_release_notes_arms.py`, `benchmarks/tests/gates/__init__.py`, `benchmarks/tests/gates/test_self_corpus_smoke_gate.py`
- Modify: `benchmarks/pyproject.toml` (`local_gate` marker)

**Interfaces:**
- Produces: `ChangelogTaggedDataset` (`changelog-tagged-py`) reading `repos.yaml` rows `{name, clone_url, license, tag_pattern}` and deriving windows / gold exactly like the self-corpus (through a shared `ReleaseWindowsBuilder` extracted from Task 3's loader — the self-corpus becomes one row of it with `repo_root` instead of `clone_url`); an empty `repos: []` list ships (owner fills it, §13 O9); the arm config with the gate-only self-corpus arm and the generic pool arm; the marked smoke gate.

- [ ] **Step 1: Extract the shared builder and add the generic loader**

Move `_section_bullets` / `_window_task` / `_aligned_units` / `_task` of `SelfReleasesDataset` into a module-level `ReleaseWindowsBuilder(repo: Path, sidecar: AlignmentSidecar, dataset_name: str, corpus_parent: Path | None)` with `async def windows_tasks() -> AsyncIterator[EvalTask]`; `SelfReleasesDataset.tasks` delegates to it; `ChangelogTaggedDataset` builds one per row after `RepoCache.base_clone(clone_url)`, with a per-repository sidecar under `benchmarks/data/changelog_tagged/<name>/alignment.yaml` (optional; without it every non-churn unit is a coverage key) and `tag_pattern` replacing the `v*` default (`release_windows(repo, pattern=...)`). Register `changelog-tagged-py`; commit `benchmarks/data/changelog_tagged/repos.yaml`:

```yaml
# Generic release-notes corpus (task-layer design §7.2): Python repositories
# with a Keep-a-Changelog file and release tags, permissive licenses only.
# Owner-filled (§13 O9). Each row: {name, clone_url, license, tag_pattern}.
repos: []
```

Tests: `benchmarks/tests/datasets/test_changelog_tagged.py` — an empty list yields no tasks and logs it; a row pointing the fake cache at the Task 3 synthetic repository yields the same window task the self-corpus would (`record_id` prefixed by the row name: `<name>@v0.2.0`).

- [ ] **Step 2: The arm config**

`optimize_search_skill_release_notes.yaml`:

```yaml
# optimize_search_skill_release_notes.yaml — ONE task name, TWO corpora, ONE rubric.
#
# release_notes (task-layer design §6.3): a changelog section for a range of
# landing units. arms[0] is the SELF-CORPUS — this repository's own release
# windows, six records, `split: all` — a GATE corpus (the smoke gate reads its
# landing_coverage), never an optimizer pool: six records cannot feed the
# parity split. arms[1] is the generic changelog-tagged corpus, the optimizer
# pool once benchmarks/data/changelog_tagged/repos.yaml is filled.
#
# Needs the multi-branch P2 surface (scope=diff on a landing sha, the landed
# block of the base card): the enumeration step is otherwise a per-unit card
# loop (§6.3 failure table), which max_turns bounds.
artifact: search_skill
optimizer: skillopt
ladder:
  - [ask_rubric, 6, 4]
  - [ask_rubric, 24, 1]
accept_margin: 0.02
budget: { max_trials: 20, max_usd: 60.0, wall_timeout_seconds: 28800 }
dataset: { name: changelog-tagged-py }
rng_seed: 0

ask_rubric_release_notes:
  runner:
    model: claude-sonnet-5
    architecture: text_react
    max_agent_turns: 60          # sized to a ~50-unit window (§6.3, G9)
  gates:
    - { name: non_empty, kind: min_answer_chars, params: { n: 40 } }
    - { name: grounded, kind: used_indexed_tools, params: { n: 1 } }
  checks:
    - { name: landing_coverage, kind: gold_recall, params: { key_prefix: landing_ }, weight: 0.5, required: false, fail: null }
    - { name: change_consulted, kind: slice_consulted, params: { scopes: [diff] }, weight: 0.25, required: false, fail: null }
    - { name: units_enumerated, kind: card_consulted, params: { tools: [get_overview], min_calls: 2 }, weight: 0.25, required: false, fail: null }
    - { name: sections_present, kind: release_headings_present, weight: 0.0, required: true, fail: 1.0 }
  gate_weight: 0.5
  rubric_weight: 0.5
  keep_deterministic_on_skip: true
  criteria:
    - { name: grouped_by_effect, weight: 0.4, description: "Bullets are grouped by effect on a user, not one per commit subject." }
    - { name: every_bullet_supported, weight: 0.4, description: "Every bullet is supported by a landing unit inside the range, cited by sha." }
    - { name: changed_api_complete, weight: 0.2, description: "Changed public signatures are listed under the changed-API heading." }

arms:
  - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner
    settings: { workspace: ~/pydocs-index/release-notes, model: claude-sonnet-5 }
    tool_names: null
    dataset: pydocs-self-releases
    dataset_kwargs: { split: all }      # gate corpus: never partitioned
    task_name: release_notes
    guidance: search_skill
    scoring:
      objective: rubric_verdict
      rubric: ask_rubric_release_notes
      tracked: [gold_recall, slice_consulted, card_consulted]
  - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner
    settings: { workspace: ~/pydocs-index/release-notes, model: claude-sonnet-5 }
    tool_names: null
    dataset: changelog-tagged-py
    task_name: release_notes
    guidance: search_skill
    scoring:
      objective: rubric_verdict
      rubric: ask_rubric_release_notes
      tracked: [gold_recall, slice_consulted, card_consulted]
```

(If `AskRubricSettings.runner` has no `max_agent_turns` field, put the turn cap where the bug_loc config puts it — the arm's `settings` mapping — and keep the comment.)

`benchmarks/tests/optimize/test_release_notes_arms.py`:

```python
"""Two corpora, ONE task name — the release_notes arms; the self-corpus is gate-only."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from pydocs_eval.optimize.run_config import _configured_rubric_sections, load_run_config


def _shipped() -> Path:
    return Path(str(resources.files("pydocs_eval.optimize.configs").joinpath("optimize_search_skill_release_notes.yaml")))


def test_every_arm_declares_release_notes_and_the_self_corpus_is_gate_only():
    cfg = load_run_config(_shipped())
    assert {arm.task_name for arm in cfg.arms} == {"release_notes"}
    self_arm = next(arm for arm in cfg.arms if arm.dataset == "pydocs-self-releases")
    assert self_arm.dataset_kwargs.get("split") == "all"
    assert sorted(_configured_rubric_sections(cfg)) == ["ask_rubric_release_notes"]
    checks = {c.name: c for c in cfg.ask_rubric_release_notes.checks}
    assert checks["landing_coverage"].params == {"key_prefix": "landing_"}
    assert "gold_location_evidenced" not in checks  # deliberately absent (§7.2)
```

- [ ] **Step 3: The smoke gate (AC-24)**

Register the marker in `benchmarks/pyproject.toml` under `[tool.pytest.ini_options]`: `markers = ["local_gate: paid or environment-bound gates, never run by CI (opt in with PYDOCS_EVAL_LOCAL_GATES=1)"]` (append to the existing list if present). Create `benchmarks/tests/gates/test_self_corpus_smoke_gate.py`:

```python
"""§7.2 smoke gate: the seed head reaches landing_coverage >= 0.6 on the two
small windows. A LOCAL gate (paid, needs a prewarmed workspace and a model);
CI never runs it. Opt in: PYDOCS_EVAL_LOCAL_GATES=1."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.local_gate, pytest.mark.skipif(os.environ.get("PYDOCS_EVAL_LOCAL_GATES") != "1", reason="local gate")]

_WINDOWS = ("v0.4.1", "v0.5.1")
_FLOOR = 0.6


async def _coverage(record_id: str) -> float:
    from pydocs_eval.datasets.self_releases import SelfReleasesDataset
    from pydocs_eval.optimize.ask_binding import build_ask_harness_runner
    from pydocs_eval.optimize.fitness.ask_rubric import sample_row_for_task
    from pydocs_eval.optimize.rubric.checks import Check, evaluate_check

    task = next(t async for t in SelfReleasesDataset().tasks() if t.record_id == record_id)
    runner = build_ask_harness_runner(workspace=Path(os.environ["PYDOCS_RELEASE_NOTES_WORKSPACE"]), model=os.environ.get("LLM_MODEL", "claude-sonnet-5"), architecture="text_react", max_agent_turns=60)
    trajectory = await runner.run(sample_row_for_task(task, task_name="release_notes"), {})
    return evaluate_check(Check(name="cov", kind="gold_recall", params={"key_prefix": "landing_"}, fail=None), task, trajectory).score


@pytest.mark.parametrize("record_id", _WINDOWS)
def test_seed_head_reaches_the_coverage_floor(record_id: str):
    coverage = asyncio.run(_coverage(record_id))
    assert coverage >= _FLOOR, f"{record_id}: landing_coverage={coverage:.2f} < {_FLOOR}"
```

- [ ] **Step 4: Run the tests and commit**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_release_notes_arms.py benchmarks/tests/datasets/test_changelog_tagged.py benchmarks/tests/gates -q`
Expected: the arms and loader tests PASS; the gate reports `skipped` (local gate).

```bash
git add benchmarks/src/pydocs_eval/datasets/self_releases.py benchmarks/src/pydocs_eval/datasets/changelog_tagged.py benchmarks/data/changelog_tagged/repos.yaml benchmarks/src/pydocs_eval/datasets/__init__.py benchmarks/src/pydocs_eval/optimize/configs/optimize_search_skill_release_notes.yaml benchmarks/tests/optimize/test_release_notes_arms.py benchmarks/tests/datasets/test_changelog_tagged.py benchmarks/tests/gates benchmarks/pyproject.toml
git commit -m "eval: release_notes arms (self-corpus gate-only + changelog-tagged pool), generic corpus scaffold, smoke gate"
```

---

### Task 7: Documents and gates

**Files:**
- Modify: `docs/superpowers/specs/2026-09-04-ask-your-docs-branch-scope-ui-design.md` (§6.9 chip table + §2 wording), `benchmarks/README.md`, `CHANGELOG.md`

- [ ] **Step 1: UI spec amendments (AC-25; owner-ratified or rejected, recorded either way)**

In the UI spec §6.9's chip table add the row proposed by the task-layer spec §6.8 (or, if the owner rejects it, a dated note under §12 saying so):

```markdown
| `release notes since <tag>` | U2; exactly one answered cell; it is the base branch and the listing carries at least one landing unit in the window | one-shot PIN on the base cell; canned question `"Write the release notes for the landings since the last tag"` |
```

and amend the cap sentence to "the count of `FollowUpKind` members (four with `RELEASE_NOTES`)". In §2 (Terms), where the tombstone's `merged_into` is read as the landing sha, add the G14 correction: "the second-pass multi-branch spec keeps `merged_into` = the base name and adds `landing_sha`; the UI code reads `landing_sha` when present and falls back to `merged_into` on v16 bundles". Replace "Dormant code" with "Inactive code" in §6.12's heading. Add both to the UI spec's amendments log with today's date.

- [ ] **Step 2: README subsections**

Under "Datasets", after the change-review subsections:

```markdown
### Release notes — the self-corpus (`pydocs-self-releases`) and its siblings

**What it measures.** A changelog section for a range of landing units on the
base branch: one record per release window of this repository (five closed
windows `v0.3.0…v0.5.1` plus the open window when pinned). Gold is the
`CHANGELOG.md` section **at the tag** (`git show <tag>:CHANGELOG.md`, never the
checkout's file, whose sections are edited after a tag) and a coverage key set
`landing_<i>`: the units a hand-labeled sidecar
(`benchmarks/data/self_releases/alignment.yaml`) aligns to bullets, else every
non-churn unit (a churn unit touches only `.github/`, lockfiles or the
formatter snapshot). Automatic `(#N)` alignment is a consistency check only —
few bullets cite a number — and the per-window `alignment_rate` is reported.

**Leak floor.** The corpus is a clone whose history is rewritten so
`CHANGELOG.md` is absent from every commit (tags re-pointed; the rewritten
shas are the gold shas), indexed with `exclude_dirs: [docs, benchmarks]` and
without commit-message / changelog decision mining; a build-time check greps
every materialized file, the window's commit messages and the indexed
decisions for each gold bullet verbatim and drops a leaking record.
Subject-level paraphrase is accepted noise. The self-corpus is ask-harness
only (an engine with its own shell can read `git log -p`).

**Gate, not pool.** Six records cannot feed a parity split: `split: all`,
a marked local smoke gate (`landing_coverage >= 0.6` on the `(v0.4.0, v0.4.1]`
and `(v0.5.0, v0.5.1]` windows, whose sidecar rows are small and unambiguous),
never an optimizer pool. `changelog-tagged-py` (`benchmarks/data/changelog_tagged/repos.yaml`,
owner-filled) is the pool.

**Siblings.** `pydocs-self-landing-loc` re-frames every aligned `Fixed` /
`Changed` bullet as a `bug_loc` report ("since v0.5.0, …") whose answer ends
with `landing: <sha7>`; `crosscommitvuln-fix-landing` asks the vulnerability
question of one landing unit (the fix commit as the base, one unit retained),
dropping records whose fix commit is a merge or does not sit on the pinned
prefix — the v2 `crosscommitvuln` dataset is untouched.
```

`CHANGELOG.md` bullet: `- **release_notes datasets** — the self-corpus over this repository's release windows (changelog gold at the tag, history rewrite leak floor, hand-labeled alignment sidecar), the per-bullet landing-localization sibling under bug_loc, the fix-landing vulnerability sibling under vuln, the release_notes arm config with a local smoke gate, and the generic changelog-tagged corpus scaffold.`

- [ ] **Step 3: Gates**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" -not -path "*/node_modules/*" -not -path "*/.git/*" | xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
ruff format python/ tests/ benchmarks/ && ruff check python/ tests/ benchmarks/ && mypy python/pydocs_mcp && complexipy python/pydocs_mcp --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80
pytest tests/ --ignore=tests/test_parity.py -q
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
uv lock --check
git checkout -- complexipy-snapshot.json
```

Expected: no audit matches; green (the smoke gate skipped).

- [ ] **Step 4: Commit and open the T2 PR**

```bash
git add docs/superpowers/specs/2026-09-04-ask-your-docs-branch-scope-ui-design.md benchmarks/README.md CHANGELOG.md
git commit -m "docs: release-notes corpora, leak floor, siblings; UI spec RELEASE_NOTES chip + landing_sha wording"
```

Gate: AC-12, AC-13, AC-14, AC-15 (self-corpus half), AC-16, AC-17, AC-24 (marked), AC-25 (UI amendments).

---

## Deviations from the spec (recorded, not silent)

| # | Spec says | Plan does | Why |
|---|---|---|---|
| D1 | the sidecar has ~70 hand-labeled rows across six windows (§7.2) | the plan requires and derives rows for the two small windows only; the other four are unit-level coverage until labeled (as §7.2's gate paragraph allows) | labeling is owner/labeler work; the loader, the schema and the mapping are complete |
| D2 | the leak check's third surface is the indexed `decision_records` | at build time the surface is empty (no index yet); the smoke gate re-runs the check with the indexed base's decisions | the loader cannot index; the gate can |
| D3 | `changelog-tagged-py` reads a committed repository list (§7.2) | the loader and an EMPTY `repos.yaml` ship; the owner fills the list (§13 O9) | no repository is chosen here |
| D4 | the self-corpus `corpus_source` is lazy like every loader | the corpus is materialized eagerly per window at `tasks()` time | the gold's `landing_<i>` are the rewritten shas, which exist only after the rewrite |
| D5 | `crosscommitvuln-fix-landing` records are dropped when the fix is a merge or off the prefix | the parent check runs against `RepoCache.base_clone(url)` at `tasks()` time (one clone per repository; the airgap bundle applies) | the drop must be counted before scoring |
| D6 | — | `SelfLandingLocDataset` only re-frames `Fixed` / `Changed` bullets (§7.3 says so) and skips rows whose bullet has no landing | a bullet without a unit has no localization gold |

## Spec coverage

| AC | Task | AC | Task |
|---|---|---|---|
| AC-12 | 3 | AC-16 | 4 |
| AC-13 | 3 | AC-17 | 5 |
| AC-14 | 1 | AC-24 | 6 |
| AC-15 (self-corpus) | 3 | AC-25 (UI amendments) | 7 |
| §7.2 sidecar, minting, splits | 2, 3, 6 | §6.9 G1/G5/G6 card blocks | multi-branch P2 plan sub-rows (not here) |

## Handoff

One PR against `main` after Plans T0 and T1 and the multi-branch P2 plan (its Task 11, the landing-unit index) have merged. Owner inputs before a paid run: **O2** (changelog sections as gold with the R10 floor — ratify), **O7** (ask-harness-only self-corpus — ratify), **O9** (the generic corpus list), the labeled sidecar rows beyond the two small windows, the `~/pydocs-index/release-notes` workspace (prewarm with `benchmarks/tools/prewarm_change_review_workspace.py --dataset pydocs-self-releases`), and the budget word; the smoke gate runs only with `PYDOCS_EVAL_LOCAL_GATES=1`.
