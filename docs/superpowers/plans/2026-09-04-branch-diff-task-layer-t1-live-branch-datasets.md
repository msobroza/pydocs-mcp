# Branch/Diff Task Layer — Plan T1: Live-Branch Datasets (S1–S2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `change_review` framing real change corpora on live branches — a history-preserving corpus mode, the pull-request-review datasets (`pr-review-py` and its doc-drift / description dimensions), the S1 shape of `swe-bench-verified-test-gap`, the `change_review` arm config with its pre-indexed workspace tool, the P2.7 `diff_search.yaml` retrieval gate, and scripted trajectory tests over a fake server that advertises the P2 surface.

**Architecture:** One new corpus function, `materialize_corpus_with_history`, produces a checkout with `.git`, the base as a local branch, synthetic branches applied from a record's diff, and a per-record `AppConfig` overlay (tracked branches, retention window, exclusions, decision sources); the retrieval track applies that overlay per task and sets a search scope through an opt-in system Protocol, the ask/external tracks read a workspace the prewarm tool indexed from the same corpora. Loaders are fixture-complete and hermetic; the pull-request corpus's release pin is an owner input (spec §13 O9) and the release path fails loudly until it is filled.

**Tech Stack:** Python 3.11+, git (subprocess, bounded), `pydocs_eval` datasets/registries/sweep, pydantic run config, unidiff, pytest (git-backed tests skip without a `git` binary). Eval tests: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`.

**Spec:** `docs/superpowers/specs/2026-09-04-branch-diff-task-layer-design.md` (commit `19f6b3a`) — §6.2, §6.4.1, §6.4.3, §6.4.4, §7.1, §7.5 (already landed in T0), §7.6, §8, §10 (S1, S2a), §11 AC-11, AC-11a, AC-15, AC-18…AC-22, AC-25 (the G4 amendment). Precondition: **Plan T0 has landed** (the five task names, `change_tasks.py`, the check kinds, the record-keyed split, the S0 dataset), and the multi-branch **P1 plan's Task 16** (the `branch` selector) plus **P2 plan's Task 7** (the `changed` / `diff` scope values) have landed for the S1 / S2a arms to run for real; every task below is testable before that against fakes and synthetic git repositories.

## Global Constraints

- **History-preserving corpora only for the datasets of this plan.** Every other loader keeps materializing history-less, byte-identical corpora; `materialize_corpus` is untouched.
- **The base is a local branch, never a detached HEAD**: `git checkout -B <base> <base_ref>` with no remote configured (a detached HEAD would be indexed as `detached-<sha7>`, not the base).
- **Synthetic branches are one commit on the base** (`git apply --index` + one commit whose subject is the record's title); head refs of the source pull request are never fetched.
- **No hosting-service names in prose** (README, docstrings, comments); the pull-request corpus is cited by academic citation and pinned by `ParquetPin` in code (spec §13 O9). Until the owner supplies the pin, the release path raises `PrReviewCorpusUnpinnedError` naming §13 O9 — a loud, tested failure, never a silent empty corpus.
- **No MCP surface change.** The search scope the retrieval track sets rides `SearchQuery`, not a tool parameter; the overlay rides YAML.
- **Per-task overlay = YAML layered over the sweep's config** (`apply_yaml_overlay`), never a second config source of defaults.
- **Task heads never name a branch, sha or tag** (R11) — a record's branch reaches the model through the prompt (`Project: <name>`, `Branch: <name>`), which task rendering owns.
- **Every `EvalTask.metadata` value is a string.**
- **git calls are bounded** (the `_repo_cache._git` timeout) and read-only on the shared cache: the history-preserving corpus is a fresh clone, never the cache worktree.
- **Naming, formatting, authorship, gates** as in Plan T0 (no `Co-Authored-By`; `ruff` / `mypy` / `complexipy` / `vulture` / product + eval suites / `uv lock --check`).

---

## File map

| Path | Status | Owns |
|---|---|---|
| `benchmarks/src/pydocs_eval/datasets/_repo_cache.py` | modify | `RepoCache.base_clone(url)` (public) |
| `benchmarks/src/pydocs_eval/datasets/history_corpus.py` | new | `HistoryCorpus`, `materialize_corpus_with_history`, `apply_diff_as_commit`, `write_config_overlay`, `ConfigOverlaySpec` |
| `benchmarks/src/pydocs_eval/_config_overlay.py` | new | `apply_yaml_overlay(config, path)` |
| `benchmarks/src/pydocs_eval/sweep.py`, `systems/base_system.py`, `systems/pydocs.py` | modify | per-task overlay; `HasSearchScope`; `metadata["search_scope"]` |
| `benchmarks/src/pydocs_eval/datasets/pr_review.py` | new | `PullRequestReviewRecord`, `PrReviewCorpusUnpinnedError`, `PrReviewPyDataset`, `PrReviewPyDocDriftDataset`, `PrReviewPyDescriptionDataset` |
| `benchmarks/src/pydocs_eval/datasets/change_review.py` | modify | `surface_stage` (S0 / S1), the S1 branch + overlay |
| `benchmarks/src/pydocs_eval/optimize/configs/optimize_search_skill_change_review.yaml` | new | the four `change_review` arms |
| `benchmarks/tools/prewarm_change_review_workspace.py` | new | index every record's corpus into one workspace |
| `benchmarks/configs/dense_diff.yaml`, `benchmarks/configs/diff_search_rrf.yaml`, `benchmarks/tools/p27_diff_search_gate.py` | new | the P2.7 gate |
| `benchmarks/tests/fixtures/pr_review_py_mini.jsonl`, `benchmarks/tests/fixtures/pr_review_corpus/` | new | fixture records + a tiny corpus |
| `benchmarks/tests/{datasets,optimize,agent_track,test_*.py}` | new | AC-11a, AC-15, AC-18…AC-22 |
| `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` | modify | the G4 amendment sentence (§6.5a) |
| `benchmarks/README.md`, `CHANGELOG.md` | modify | dataset subsections, the corpus mode, the gate |

---

### Task 1: History-preserving corpus materialization

**Files:**
- Modify: `benchmarks/src/pydocs_eval/datasets/_repo_cache.py` (public `base_clone`)
- Create: `benchmarks/src/pydocs_eval/datasets/history_corpus.py`
- Test: `benchmarks/tests/datasets/test_history_corpus.py`

**Interfaces:**
- Produces: `RepoCache.base_clone(url) -> Path`; `ConfigOverlaySpec(track: tuple[str, ...], retain: Mapping[str, object], exclude_dirs: tuple[str, ...], decision_sources: tuple[str, ...] | None, capture_kinds: tuple[str, ...] | None)`; `write_config_overlay(root, spec) -> Path`; `HistoryCorpus(root, overlay_path, base, base_sha, branches: Mapping[str, str])`; `materialize_corpus_with_history(source_repo, *, base_ref, base="main", branch_refs=(), remove_paths=(), overlay=ConfigOverlaySpec(), parent=None) -> HistoryCorpus`; `apply_diff_as_commit(root, *, base, branch, diff_text, subject) -> str` (the new commit's 40-hex sha).

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/datasets/test_history_corpus.py`:

```python
"""``materialize_corpus_with_history`` — AC-15 over a synthetic git repository."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from pydocs_eval.datasets.history_corpus import (
    ConfigOverlaySpec,
    apply_diff_as_commit,
    materialize_corpus_with_history,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env={**_ENV, "PATH": __import__("os").environ["PATH"]}).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    """main: c1 (pkg/mod.py + CHANGELOG.md) → c2 (edits both) tagged v0.1.0."""
    root = tmp_path / "src"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("## v0.0.1\n- first\n", encoding="utf-8")
    _git("add", ".", cwd=root)
    _git("commit", "-q", "-m", "c1", cwd=root)
    (root / "pkg" / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("## v0.1.0\n- secret bullet\n## v0.0.1\n- first\n", encoding="utf-8")
    _git("commit", "-qam", "c2", cwd=root)
    _git("tag", "v0.1.0", cwd=root)
    return root


def test_base_is_a_local_branch_with_no_remote(tmp_path: Path):
    src = _repo(tmp_path)
    corpus = materialize_corpus_with_history(src, base_ref="v0.1.0", base="main", parent=tmp_path)
    assert (corpus.root / ".git").exists()
    assert _git("symbolic-ref", "HEAD", cwd=corpus.root) == "refs/heads/main"
    assert _git("remote", cwd=corpus.root) == ""
    assert corpus.base_sha == _git("rev-parse", "v0.1.0^{commit}", cwd=src)
    assert (corpus.root / "pkg" / "mod.py").read_text() == "def f():\n    return 2\n"


def test_overlay_carries_the_indexing_keys(tmp_path: Path):
    src = _repo(tmp_path)
    spec = ConfigOverlaySpec(
        track=("change/x",),
        retain={"landings": 1},
        exclude_dirs=("docs", "benchmarks"),
        decision_sources=("adr_files", "inline_markers", "docs_prose"),
        capture_kinds=("calls", "imports", "inherits", "mentions"),
    )
    corpus = materialize_corpus_with_history(src, base_ref="main", overlay=spec, parent=tmp_path)
    overlay = yaml.safe_load(corpus.overlay_path.read_text(encoding="utf-8"))
    assert overlay["git"]["branches"]["track"] == ["change/x"]
    assert overlay["git"]["diff_chunks"]["retain"] == {"landings": 1}
    assert overlay["ingestion"]["discovery"]["project"]["exclude_dirs"] == ["docs", "benchmarks"]
    assert overlay["decision_capture"]["sources"] == ["adr_files", "inline_markers", "docs_prose"]
    assert overlay["reference_graph"]["capture"]["kinds"] == ["calls", "imports", "inherits", "mentions"]
    assert corpus.overlay_path.parent == corpus.root.parent  # beside, never inside, the corpus


def test_branch_refs_become_local_branches(tmp_path: Path):
    src = _repo(tmp_path)
    c1 = _git("rev-parse", "HEAD~1", cwd=src)
    corpus = materialize_corpus_with_history(src, base_ref="main", branch_refs=(("old/x", c1),), parent=tmp_path)
    assert corpus.branches == {"old/x": c1}
    assert _git("rev-parse", "old/x", cwd=corpus.root) == c1


def test_history_rewrite_removes_the_path_from_every_commit_and_repoints_tags(tmp_path: Path):
    src = _repo(tmp_path)
    corpus = materialize_corpus_with_history(src, base_ref="v0.1.0", remove_paths=("CHANGELOG.md",), parent=tmp_path)
    assert not (corpus.root / "CHANGELOG.md").exists()
    listing = _git("log", "--all", "--name-only", "--format=", cwd=corpus.root)
    assert "CHANGELOG.md" not in listing
    assert _git("rev-parse", "v0.1.0^{commit}", cwd=corpus.root) == corpus.base_sha
    assert corpus.base_sha != _git("rev-parse", "v0.1.0^{commit}", cwd=src)  # rewritten shas
    # Deterministic: a second rewrite of the same source yields the same shas.
    again = materialize_corpus_with_history(src, base_ref="v0.1.0", remove_paths=("CHANGELOG.md",), parent=tmp_path / "again")
    assert again.base_sha == corpus.base_sha


def test_apply_diff_as_commit_creates_a_one_commit_branch_on_the_base(tmp_path: Path):
    src = _repo(tmp_path)
    corpus = materialize_corpus_with_history(src, base_ref="main", parent=tmp_path)
    diff = (
        "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n"
        "@@ -1,2 +1,2 @@ def f():\n def f():\n-    return 2\n+    return 3\n"
    )
    sha = apply_diff_as_commit(corpus.root, base="main", branch="review/r1", diff_text=diff, subject="fix f")
    assert len(sha) == 40
    assert _git("rev-parse", "review/r1", cwd=corpus.root) == sha
    assert _git("rev-parse", "review/r1~1", cwd=corpus.root) == corpus.base_sha
    assert _git("symbolic-ref", "HEAD", cwd=corpus.root) == "refs/heads/main"  # back on the base
    assert (corpus.root / "pkg" / "mod.py").read_text() == "def f():\n    return 2\n"
    with pytest.raises(RuntimeError, match="does not apply"):
        apply_diff_as_commit(corpus.root, base="main", branch="review/r2", diff_text=diff.replace("return 2", "return 9"), subject="bad")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_history_corpus.py -q`
Expected: FAIL — `ModuleNotFoundError: history_corpus`.

- [ ] **Step 3: Expose the base clone**

In `benchmarks/src/pydocs_eval/datasets/_repo_cache.py`, add to `RepoCache` (after `checkout`):

```python
    def base_clone(self, url: str) -> Path:
        """The shared base clone for ``url`` (cloned once). Read it, never
        check anything out in it — the history-preserving corpus clones FROM
        it so the shared cache is never mutated."""
        return self._base_clone(url)
```

and the same method on `RepoCacheLike` (`def base_clone(self, url: str) -> Path: ...`).

- [ ] **Step 4: Create `history_corpus.py`**

```python
"""History-preserving corpus materialization (task-layer design §7.6).

Every other loader materializes history-less (``materialize_corpus``): no
``.git``, no commit signal. The branch / diff framings need the product to
build ``branches`` rows, merge-base pairs and landing units, so their
corpora are fresh CLONES with history, the base checked out as a LOCAL branch
(a detached HEAD would be indexed as ``detached-<sha7>``), no remote, plus a
per-record ``AppConfig`` overlay the indexer loads. Optional history rewrite
removes leak paths (the self-corpus's changelog) from every commit; git's
own filter with an index filter keeps commit metadata, so the rewritten shas
are deterministic across machines.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_GIT_TIMEOUT = 600  # seconds; a filter over a few hundred commits, bounded
_COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "pydocs-eval",
    "GIT_AUTHOR_EMAIL": "eval@example.invalid",
    "GIT_COMMITTER_NAME": "pydocs-eval",
    "GIT_COMMITTER_EMAIL": "eval@example.invalid",
    # Fixed dates keep a synthetic commit's sha deterministic across runs.
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "FILTER_BRANCH_SQUELCH_WARNING": "1",
}


def _git(*args: str, cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env={**os.environ, **_COMMIT_ENV},
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {exc.stderr.strip()[-500:]}") from exc
    return result.stdout.strip()


@dataclass(frozen=True, slots=True)
class ConfigOverlaySpec:
    """The indexing keys a record's overlay carries (task-layer design §8)."""

    track: tuple[str, ...] = ()
    retain: Mapping[str, object] = field(default_factory=dict)
    exclude_dirs: tuple[str, ...] = ()
    decision_sources: tuple[str, ...] | None = None
    capture_kinds: tuple[str, ...] | None = None

    def as_yaml_document(self) -> dict[str, object]:
        document: dict[str, object] = {}
        git: dict[str, object] = {}
        if self.track:
            git["branches"] = {"track": list(self.track)}
        if self.retain:
            git["diff_chunks"] = {"retain": dict(self.retain)}
        if git:
            document["git"] = git
        if self.exclude_dirs:
            document["ingestion"] = {"discovery": {"project": {"exclude_dirs": list(self.exclude_dirs)}}}
        if self.decision_sources is not None:
            document["decision_capture"] = {"sources": list(self.decision_sources)}
        if self.capture_kinds is not None:
            document["reference_graph"] = {"capture": {"kinds": list(self.capture_kinds)}}
        return document


@dataclass(frozen=True, slots=True)
class HistoryCorpus:
    root: Path  # the clone; ``.git`` present, ``base`` checked out
    overlay_path: Path  # AppConfig overlay YAML (beside the root, never inside it)
    base: str
    base_sha: str
    branches: Mapping[str, str]  # name -> sha of every synthetic / tracked branch


def write_config_overlay(root: Path, spec: ConfigOverlaySpec) -> Path:
    """Write ``spec`` as ``<root.name>.overlay.yaml`` next to ``root``."""
    path = root.parent / f"{root.name}.overlay.yaml"
    path.write_text(yaml.safe_dump(spec.as_yaml_document(), sort_keys=True), encoding="utf-8")
    return path


def _rewrite_history(root: Path, remove_paths: Sequence[str]) -> None:
    """Drop ``remove_paths`` from every commit; tags follow (``--tag-name-filter cat``)."""
    quoted = " ".join(f"'{p}'" for p in remove_paths)
    _git(
        "filter-branch",
        "-f",
        "--index-filter",
        f"git rm --cached --ignore-unmatch -q -r -- {quoted}",
        "--tag-name-filter",
        "cat",
        "--",
        "--all",
        cwd=root,
    )
    # The backup refs would keep the removed blobs reachable (and indexable
    # through ``git log --all``): delete them.
    for ref in _git("for-each-ref", "--format=%(refname)", "refs/original/", cwd=root).splitlines():
        _git("update-ref", "-d", ref, cwd=root)


def materialize_corpus_with_history(
    source_repo: Path,
    *,
    base_ref: str,
    base: str = "main",
    branch_refs: Sequence[tuple[str, str]] = (),
    remove_paths: Sequence[str] = (),
    overlay: ConfigOverlaySpec = ConfigOverlaySpec(),
    parent: Path | None = None,
) -> HistoryCorpus:
    """A fresh clone of ``source_repo`` with ``base`` at ``base_ref`` (local branch,
    no remote), ``branch_refs`` as local branches, ``remove_paths`` filtered out
    of every commit, and the overlay written beside it. The caller owns the
    directory (``shutil.rmtree`` when done)."""
    root = Path(tempfile.mkdtemp(prefix="history_", dir=parent)) / "corpus"
    _git("clone", "-q", "--no-hardlinks", str(source_repo), str(root), cwd=source_repo.parent)
    _git("remote", "remove", "origin", cwd=root)
    _git("checkout", "-q", "-B", base, f"{base_ref}^{{commit}}", cwd=root)
    for name, sha in branch_refs:
        _git("branch", "-f", name, sha, cwd=root)
    if remove_paths:
        _rewrite_history(root, remove_paths)
    branches = {name: _git("rev-parse", name, cwd=root) for name, _ in branch_refs}
    return HistoryCorpus(
        root=root,
        overlay_path=write_config_overlay(root, overlay),
        base=base,
        base_sha=_git("rev-parse", base, cwd=root),
        branches=branches,
    )


def apply_diff_as_commit(root: Path, *, base: str, branch: str, diff_text: str, subject: str) -> str:
    """Create ``branch`` as ONE commit on ``base`` carrying ``diff_text``; return its sha.

    The base is checked out again afterwards, so the corpus root stays on the
    base branch. A diff that does not apply raises with the offending branch
    name (never a half-applied tree: ``git apply --index`` is atomic per call).
    """
    patch = root.parent / f"{branch.replace('/', '__')}.patch"
    patch.write_text(diff_text, encoding="utf-8")
    _git("checkout", "-q", "-B", branch, base, cwd=root)
    try:
        _git("apply", "--index", str(patch), cwd=root)
    except RuntimeError as exc:
        _git("checkout", "-q", base, cwd=root)
        _git("branch", "-D", branch, cwd=root)
        raise RuntimeError(f"diff for branch {branch!r} does not apply on {base}: {exc}") from exc
    _git("commit", "-q", "--allow-empty", "-m", subject, cwd=root)
    sha = _git("rev-parse", "HEAD", cwd=root)
    _git("checkout", "-q", base, cwd=root)
    return sha


__all__ = [
    "ConfigOverlaySpec",
    "HistoryCorpus",
    "apply_diff_as_commit",
    "materialize_corpus_with_history",
    "write_config_overlay",
]
```

Note the `git clone` source: `RepoCache.base_clone(url)` is a full clone with every fetched ref; cloning it copies the objects once per record (disk cost stated in the README; the shared worktrees of `RepoCache.checkout` are untouched).

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_history_corpus.py benchmarks/tests/datasets/test_repo_cache.py -q`
Expected: PASS. (`git filter-branch` prints a deprecation warning to stderr on some versions; `FILTER_BRANCH_SQUELCH_WARNING=1` silences it; if the installed git lacks `filter-branch`, install `git-filter-repo` and replace `_rewrite_history`'s call with `git filter-repo --force --invert-paths --path <p>...` — same semantics, same determinism.)

- [ ] **Step 6: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/_repo_cache.py benchmarks/src/pydocs_eval/datasets/history_corpus.py benchmarks/tests/datasets/test_history_corpus.py
git commit -m "eval: history-preserving corpus materialization with per-record config overlays"
```

---

### Task 2: Per-task config overlay and search scope on the retrieval track

**Files:**
- Create: `benchmarks/src/pydocs_eval/_config_overlay.py`
- Modify: `benchmarks/src/pydocs_eval/sweep.py` (`_run_task`: overlay + scope), `benchmarks/src/pydocs_eval/systems/base_system.py` (`HasSearchScope`), `benchmarks/src/pydocs_eval/systems/pydocs.py` (`set_search_scope`, `search`)
- Test: `benchmarks/tests/test_config_overlay.py`, `benchmarks/tests/systems/test_search_scope.py`

**Interfaces:**
- Produces: `apply_yaml_overlay(config: AppConfig, path: Path) -> AppConfig` (deep-merge of the YAML document over `config.model_dump()`); `HasSearchScope` Protocol with `set_search_scope(scope: str, branch: str) -> None`; `PydocsMcpSystem.set_search_scope`; task metadata keys `config_overlay` (path) and `search_scope` / `search_branch`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/test_config_overlay.py`:

```python
"""``apply_yaml_overlay`` layers a per-task YAML over a loaded AppConfig."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pydocs_mcp")

from pydocs_eval._config_overlay import apply_yaml_overlay
from pydocs_mcp.retrieval.config.app_config import AppConfig


def test_overlay_wins_over_the_loaded_config_and_leaves_the_rest(tmp_path: Path):
    base = AppConfig.load()
    overlay = tmp_path / "o.yaml"
    overlay.write_text(
        "decision_capture:\n  sources: [adr_files]\ningestion:\n  discovery:\n    project:\n      exclude_dirs: [docs]\n",
        encoding="utf-8",
    )
    merged = apply_yaml_overlay(base, overlay)
    assert merged.decision_capture.sources == ["adr_files"]
    assert merged.ingestion.discovery.project.exclude_dirs == ["docs"]
    assert merged.embedding == base.embedding  # untouched sections survive
    assert merged.compute_ingestion_pipeline_hash() != base.compute_ingestion_pipeline_hash()


def test_an_unknown_key_fails_loud(tmp_path: Path):
    overlay = tmp_path / "o.yaml"
    overlay.write_text("decision_capture:\n  sorces: [adr_files]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sorces"):
        apply_yaml_overlay(AppConfig.load(), overlay)
```

Create `benchmarks/tests/systems/test_search_scope.py`:

```python
"""The retrieval track's opt-in search scope (a SearchQuery field, never a tool param)."""

from __future__ import annotations

import pytest

pytest.importorskip("pydocs_mcp")

from pydocs_eval.systems.base_system import HasSearchScope
from pydocs_eval.systems.pydocs import PydocsMcpSystem


def test_pydocs_system_accepts_a_search_scope():
    system = PydocsMcpSystem()
    assert isinstance(system, HasSearchScope)
    system.set_search_scope("diff", "review/r1")
    assert system.search_query_fields() == {"scope": "diff", "branch": "review/r1"}
    system.set_search_scope("", "")
    assert system.search_query_fields() == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/test_config_overlay.py benchmarks/tests/systems/test_search_scope.py -q`
Expected: FAIL — `ModuleNotFoundError: _config_overlay`; `ImportError: HasSearchScope`.

- [ ] **Step 3: Implement**

`benchmarks/src/pydocs_eval/_config_overlay.py`:

```python
"""Layer a per-task YAML overlay over an already-loaded ``AppConfig``.

``AppConfig.load`` takes ONE user layer. The branch / diff datasets need a
second, per-record layer (tracked branches, retention window, exclusions)
on top of whatever config the sweep runs — so the overlay is deep-merged
over the loaded model's dump and re-validated. ``pydantic-settings`` gives
init kwargs the highest priority, so nothing from env or files re-enters.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config.app_config import AppConfig


def _deep_merge(base: Mapping[str, object], overlay: Mapping[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def apply_yaml_overlay(config: AppConfig, path: Path) -> AppConfig:
    """``config`` with ``path``'s YAML document layered over it (unknown keys fail loud).

    Example:
        >>> apply_yaml_overlay(AppConfig.load(), Path("record.overlay.yaml"))  # doctest: +SKIP
    """
    try:
        from pydocs_mcp.retrieval.config.app_config import AppConfig as _AppConfig
    except ImportError as exc:
        raise_missing_retrieval_extra(exc)
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(document, Mapping):
        raise ValueError(f"overlay {path} must be a YAML mapping; got {type(document).__name__}")
    merged = _AppConfig.model_validate(_deep_merge(config.model_dump(), document))
    # The user-config path feeds the pipeline-path security allowlist; carry it over.
    object.__setattr__(merged, "_effective_user_config_path", config._user_config_path())
    return merged


__all__ = ["apply_yaml_overlay"]
```

If `AppConfig` uses `extra="ignore"` and the unknown-key test fails, validate the overlay's key paths against `config.model_dump()` before merging (raise `ValueError` naming the first key not present at its level).

`base_system.py` — after `HasLibrary`:

```python
@runtime_checkable
class HasSearchScope(Protocol):
    """Opt-in: a system that can scope a search to a branch slice.

    The retrieval track sets it from ``task.metadata["search_scope"]`` /
    ``["search_branch"]`` before ``search`` (the change-review datasets rank
    over a branch's DIFF slice). Rides the product's ``SearchQuery`` fields,
    never a tool parameter.
    """

    def set_search_scope(self, scope: str, branch: str) -> None: ...
```

`systems/pydocs.py` — add two fields/methods on `PydocsMcpSystem` (`_search_scope: str = ""`, `_search_branch: str = ""` as `field(default="", init=False)` if the class is a dataclass, else plain attributes set in `__init__`):

```python
    def set_search_scope(self, scope: str, branch: str) -> None:
        self._search_scope, self._search_branch = scope, branch

    def search_query_fields(self) -> dict[str, str]:
        """The extra ``SearchQuery`` fields a scoped search carries (none when unset).
        Field names follow the product's ``application/search_query.py``
        ``build_search_query`` (multi-branch P2 plan, Task 5)."""
        fields: dict[str, str] = {}
        if self._search_scope:
            fields["scope"] = self._search_scope
        if self._search_branch:
            fields["branch"] = self._search_branch
        return fields
```

and in `search`: `SearchQuery(terms=query, max_results=limit, **self.search_query_fields())`. Before the P2 code lands, `SearchQuery` has neither field: guard with `try/except TypeError` is NOT acceptable (silent); instead the fields are passed only when non-empty, so every existing sweep (no scope) is byte-identical, and a scoped sweep on a pre-P2 product fails loudly with the `TypeError` naming the field.

`sweep.py` `_run_task`, replace the `dir_ = …` line and the index call with:

```python
    dir_ = corpus_dir if corpus_dir is not None else task.corpus_source()
    task_config = _config_for_task(config, task)
    _maybe_set_search_scope(system, task.metadata)
    try:
        t0 = time.perf_counter()
        await system.index(dir_, task_config)
```

and add the two helpers:

```python
def _config_for_task(config: AppConfig, task: EvalTask) -> AppConfig:
    """The sweep config with the task's ``config_overlay`` (if any) layered over it."""
    overlay = task.metadata.get("config_overlay", "")
    if not overlay:
        return config
    from pydocs_eval._config_overlay import apply_yaml_overlay

    return apply_yaml_overlay(config, Path(overlay))


def _maybe_set_search_scope(system: object, metadata: Mapping[str, str]) -> None:
    """Opt-in scoped search (``HasSearchScope``); a no-op for every other system."""
    if isinstance(system, HasSearchScope):
        system.set_search_scope(metadata.get("search_scope", ""), metadata.get("search_branch", ""))
```

(import `HasSearchScope` next to `_maybe_set_library`; the bench cache key already folds the overlay through `config.compute_ingestion_pipeline_hash()` and the per-record corpus dir).

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/test_config_overlay.py benchmarks/tests/systems -q && PYTHONPATH=benchmarks/src pytest benchmarks/tests/test_task_rendering.py benchmarks/tests/metrics -q`
Expected: PASS (existing sweep tests unchanged: no task carries `config_overlay`).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/pydocs_eval/_config_overlay.py benchmarks/src/pydocs_eval/sweep.py benchmarks/src/pydocs_eval/systems/base_system.py benchmarks/src/pydocs_eval/systems/pydocs.py benchmarks/tests/test_config_overlay.py benchmarks/tests/systems/test_search_scope.py
git commit -m "eval: per-task config overlay + opt-in search scope on the retrieval track"
```

---

### Task 3: `pr-review-py` and its dimension datasets

**Files:**
- Create: `benchmarks/src/pydocs_eval/datasets/pr_review.py`
- Create: `benchmarks/tests/fixtures/pr_review_py_mini.jsonl`, `benchmarks/tests/fixtures/pr_review_corpus/` (a tiny git-less tree the fake cache serves; the git-backed test builds a real repo)
- Modify: `benchmarks/src/pydocs_eval/datasets/__init__.py`
- Test: `benchmarks/tests/datasets/test_pr_review.py`

**Interfaces:**
- Consumes: `materialize_corpus_with_history`, `apply_diff_as_commit`, `ConfigOverlaySpec` (Task 1); `RepoCache.base_clone` (Task 1); `enclosing_symbols`, `paths_from_unified_diff`, `non_test_paths` (T0); `ChangeReviewDimension`, `DIMENSION_METADATA_KEY`, `CHANGE_REVIEW_TASK_NAME` (T0).
- Produces: `PullRequestReviewRecord(record_id, repo_url, project, title, base_sha, head_sha, diff, description, findings: tuple[ReviewFinding, ...])`, `ReviewFinding(path, line, text, resolved_by_change: bool)`; `PrReviewCorpusUnpinnedError`; `PR_REVIEW_PY_PIN: ParquetPin | None` (None until §13 O9); datasets `pr-review-py` (BLAST_RADIUS), `pr-review-py-doc-drift` (DOC_DRIFT: rows whose diff touches a symbol mentioned by an indexed `.md`), `pr-review-py-description` (PR_DESCRIPTION: rows with a description body); the S1 prompt `render_change_review_prompt(project, branch, request)`.

- [ ] **Step 1: Fixture**

`benchmarks/tests/fixtures/pr_review_py_mini.jsonl` (three rows; the diff applies on the fixture repository the test builds):

```json
{"record_id": "acme__widgets__41", "repo_url": "https://example.invalid/acme/widgets.git", "title": "Retry transient fetch errors", "base_sha": "BASE", "head_sha": "HEAD", "diff": "diff --git a/widgets/fetch.py b/widgets/fetch.py\n--- a/widgets/fetch.py\n+++ b/widgets/fetch.py\n@@ -1,3 +1,4 @@ def fetch(url):\n def fetch(url):\n-    return get(url)\n+    for _ in range(3):\n+        return get(url)\n", "description": "Retries fetch three times. Callers of fetch() keep their signature.", "findings": [{"path": "widgets/fetch.py", "line": 3, "text": "the loop returns on the first iteration; no retry happens", "resolved_by_change": true}], "docs_mentioning": ["docs/fetch.md"]}
{"record_id": "acme__widgets__42", "repo_url": "https://example.invalid/acme/widgets.git", "title": "Rename helper", "base_sha": "BASE", "head_sha": "HEAD", "diff": "diff --git a/widgets/util.py b/widgets/util.py\n--- a/widgets/util.py\n+++ b/widgets/util.py\n@@ -1,2 +1,2 @@ def helper():\n-def helper():\n+def helper_v2():\n     return 1\n", "description": "", "findings": [{"path": "widgets/util.py", "line": 1, "text": "rename breaks fetch()'s import", "resolved_by_change": false}], "docs_mentioning": []}
{"record_id": "acme__widgets__43", "repo_url": "https://example.invalid/acme/widgets.git", "title": "No findings", "base_sha": "BASE", "head_sha": "HEAD", "diff": "diff --git a/widgets/util.py b/widgets/util.py\n--- a/widgets/util.py\n+++ b/widgets/util.py\n@@ -1,2 +1,3 @@ def helper():\n def helper():\n+    # noop\n     return 1\n", "description": "trivial", "findings": [], "docs_mentioning": []}
```

`BASE` / `HEAD` are placeholders the test rewrites to the fixture repository's real shas (the loader requires 40-hex; the test writes a temp copy of the fixture with the shas filled in).

- [ ] **Step 2: Write the failing tests**

Create `benchmarks/tests/datasets/test_pr_review.py`:

```python
"""``pr-review-py`` and its dimension datasets — AC-11a (fixture) + the unpinned release path."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.datasets.pr_review import (
    PR_REVIEW_PY_PIN,
    PrReviewCorpusUnpinnedError,
    PrReviewPyDataset,
    PrReviewPyDescriptionDataset,
    PrReviewPyDocDriftDataset,
    render_change_review_prompt,
)
from pydocs_eval.registries import dataset_registry

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")
_FIXTURE = Path(__file__).parents[1] / "fixtures" / "pr_review_py_mini.jsonl"


def _git(*args: str, cwd: Path) -> str:
    env = {"PATH": __import__("os").environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


def _source_repo(tmp_path: Path) -> Path:
    root = tmp_path / "widgets"
    (root / "widgets").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "widgets" / "fetch.py").write_text("def fetch(url):\n    return get(url)\n", encoding="utf-8")
    (root / "widgets" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "docs" / "fetch.md").write_text("# fetch\n`fetch` retries nothing.\n", encoding="utf-8")
    _git("init", "-q", "-b", "main", cwd=root)
    _git("add", ".", cwd=root)
    _git("commit", "-qm", "base", cwd=root)
    return root


@dataclass
class _FakeRepoCache:
    source: Path
    requested: list[str] = field(default_factory=list)

    def base_clone(self, url: str) -> Path:
        self.requested.append(url)
        return self.source

    def checkout(self, url: str, sha: str) -> Path:  # unused here
        return self.source

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ()


def _fixture_with_shas(tmp_path: Path, base_sha: str) -> Path:
    rows = [json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    path = tmp_path / "rows.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            row["base_sha"], row["head_sha"] = base_sha, "f" * 40
            fh.write(json.dumps(row) + "\n")
    return path


def _collect(dataset):
    async def _run():
        return [task async for task in dataset.tasks()]

    return asyncio.run(_run())


def test_registered_names():
    for name in ("pr-review-py", "pr-review-py-doc-drift", "pr-review-py-description"):
        assert name in dataset_registry.names()


def test_rows_carry_branch_shas_findings_and_the_synthetic_commit_applies(tmp_path: Path):
    source = _source_repo(tmp_path)
    base_sha = _git("rev-parse", "HEAD", cwd=source)
    cache = _FakeRepoCache(source)
    dataset = PrReviewPyDataset(fixture_path=_fixture_with_shas(tmp_path, base_sha), repo_cache=cache, corpus_parent=tmp_path / "corpora")
    tasks = {t.record_id: t for t in _collect(dataset)}
    assert set(tasks) == {"acme__widgets__41", "acme__widgets__42"}  # no findings → dropped
    task = tasks["acme__widgets__41"]
    assert task.task_id == "pr-review-py/change_review/acme__widgets__41"
    assert task.gold.file_set == ("widgets/fetch.py",)
    assert task.gold.extra["finding_0"].startswith("the loop returns")
    assert task.gold.extra["symbol_0"] == "fetch"
    assert task.gold.extra["base_sha"] == base_sha and len(task.gold.extra["head_sha"]) == 40
    assert task.gold.extra["branch"] == "review/acme__widgets__41"
    assert task.metadata["dimension"] == "blast_radius" and task.metadata["surface_stage"] == "S1"
    assert task.metadata["search_scope"] == "diff" and task.metadata["search_branch"] == "review/acme__widgets__41"
    corpus = task.corpus_source()
    try:
        assert (corpus / ".git").exists()
        assert _git("rev-parse", "review/acme__widgets__41~1", cwd=corpus) == base_sha
        assert _git("symbolic-ref", "HEAD", cwd=corpus) == "refs/heads/main"
        overlay = Path(task.metadata["config_overlay"])
        assert "review/acme__widgets__41" in overlay.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(corpus.parent)
    assert task.query == render_change_review_prompt("acme__widgets__41", "review/acme__widgets__41", "Review this change: what does it break, and which tests and docs did it miss?")


def test_dimension_datasets_filter_the_rows(tmp_path: Path):
    source = _source_repo(tmp_path)
    base_sha = _git("rev-parse", "HEAD", cwd=source)
    rows = _fixture_with_shas(tmp_path, base_sha)
    drift = _collect(PrReviewPyDocDriftDataset(fixture_path=rows, repo_cache=_FakeRepoCache(source), corpus_parent=tmp_path / "c1"))
    assert [t.record_id for t in drift] == ["acme__widgets__41"]
    assert drift[0].gold.file_set == ("docs/fetch.md",)
    assert drift[0].task_id.startswith("pr-review-py-doc-drift/change_review/")
    description = _collect(PrReviewPyDescriptionDataset(fixture_path=rows, repo_cache=_FakeRepoCache(source), corpus_parent=tmp_path / "c2"))
    assert [t.record_id for t in description] == ["acme__widgets__41", "acme__widgets__43"]
    assert description[0].metadata["dimension"] == "pr_description"
    assert description[0].gold.file_set == ("widgets/fetch.py",)  # the change set


def test_the_release_path_is_loud_until_the_corpus_is_pinned():
    if PR_REVIEW_PY_PIN is not None:
        pytest.skip("the corpus is pinned; the release path is covered by the row-count pin")
    with pytest.raises(PrReviewCorpusUnpinnedError, match="O9"):
        _collect(PrReviewPyDataset())
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_pr_review.py -q`
Expected: FAIL — `ModuleNotFoundError: pr_review`.

- [ ] **Step 4: Create the loader family**

`benchmarks/src/pydocs_eval/datasets/pr_review.py`:

```python
"""The ``change_review`` framing over a pull-request-review corpus (S1 shape).

One record = one merged pull request with reviewer comments anchored to file
and line (task-layer design §7.1). The corpus is a HISTORY-PRESERVING clone
at ``base_sha`` with the record's merged diff applied as ONE synthetic commit
on the branch ``review/<record_id>``; head refs are never fetched. Three
registered datasets mint over the same records: the base review
(``blast_radius``), the doc-drift rows (diff touches a symbol an indexed
``.md`` mentions) and the description rows (a non-empty merged description).

The release pin is an OWNER INPUT (spec §13 O9: one published corpus pinned by
academic citation, Python slice, revision and row count in code). Until it is
filled, the release path raises ``PrReviewCorpusUnpinnedError`` — loud, tested,
never a silent empty corpus. Fixture rows (``fixture_path``) drive every test.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registries import dataset_registry
from ..trajectory.gold_diff import enclosing_symbols
from ._bug_loc_gold import BugLocGoldError, non_test_paths, paths_from_unified_diff
from ._parquet import ParquetPin, download_parquet, read_parquet_rows
from ._repo_cache import RepoCache, RepoCacheLike
from .base_dataset import EvalTask, GoldAnswer
from .change_tasks import CHANGE_REVIEW_TASK_NAME, DIMENSION_METADATA_KEY, ChangeReviewDimension
from .history_corpus import ConfigOverlaySpec, apply_diff_as_commit, materialize_corpus_with_history
from .task_ids import mint_framed_task_id

log = logging.getLogger(__name__)

#: Filled from the owner's §13 O9 decision: dataset id, revision, files,
#: expected_rows. ``None`` means "not pinned" and the release path refuses.
PR_REVIEW_PY_PIN: ParquetPin | None = None
_COLUMNS = ["record_id", "repo_url", "title", "base_sha", "head_sha", "diff", "description", "findings", "docs_mentioning"]
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_REVIEW_REQUEST = "Review this change: what does it break, and which tests and docs did it miss?"
_DESCRIPTION_REQUEST = "Draft the description for this change: motivation, what changed, risk, how it was tested."
_S1_PROMPT = "Project: {project}\nBranch: {branch}\n\n{request}"


class PrReviewCorpusUnpinnedError(RuntimeError):
    """The pull-request-review corpus has no release pin yet (spec §13 O9)."""


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    path: str
    line: int
    text: str
    resolved_by_change: bool


@dataclass(frozen=True, slots=True)
class PullRequestReviewRecord:
    record_id: str
    repo_url: str
    title: str
    base_sha: str
    head_sha: str
    diff: str
    description: str
    findings: tuple[ReviewFinding, ...]
    docs_mentioning: tuple[str, ...]

    @property
    def branch(self) -> str:
        return f"review/{self.record_id}"

    @property
    def project(self) -> str:
        """The project name the corpus indexes under — the record id, so two
        pull requests of one repository never collide in a workspace."""
        return self.record_id


def render_change_review_prompt(project: str, branch: str, request: str) -> str:
    """The S1 query: project + branch (never in a head, R11) + the request."""
    return _S1_PROMPT.format(project=project, branch=branch, request=request)


def _record_from_row(row: dict[str, Any]) -> PullRequestReviewRecord:
    findings = tuple(
        ReviewFinding(str(f["path"]), int(f.get("line", 0)), str(f["text"]), bool(f.get("resolved_by_change", False)))
        for f in row.get("findings") or ()
    )
    record = PullRequestReviewRecord(
        record_id=str(row["record_id"]),
        repo_url=str(row["repo_url"]),
        title=str(row.get("title") or ""),
        base_sha=str(row["base_sha"]),
        head_sha=str(row["head_sha"]),
        diff=str(row.get("diff") or ""),
        description=str(row.get("description") or ""),
        findings=findings,
        docs_mentioning=tuple(str(d) for d in row.get("docs_mentioning") or ()),
    )
    for label, sha in (("base_sha", record.base_sha), ("head_sha", record.head_sha)):
        if not _SHA40.fullmatch(sha):
            raise BugLocGoldError(f"record {record.record_id!r} {label} {sha!r} is not 40-hex")
    if not record.diff.strip():
        raise BugLocGoldError(f"record {record.record_id!r} has an empty diff")
    return record


def _corpus_source(cache: RepoCacheLike, record: PullRequestReviewRecord, parent: Path | None, overlay: ConfigOverlaySpec) -> Callable[[], Path]:
    """A lazy factory: clone from the shared base clone, build the branch, write the overlay."""

    def _build() -> Path:
        corpus = materialize_corpus_with_history(
            cache.base_clone(record.repo_url), base_ref=record.base_sha, overlay=overlay, parent=parent
        )
        apply_diff_as_commit(corpus.root, base=corpus.base, branch=record.branch, diff_text=record.diff, subject=record.title or record.record_id)
        # The corpus root is named after the project so the bundle's project name is the record id.
        named = corpus.root.parent / record.project
        corpus.root.rename(named)
        corpus.overlay_path.rename(named.parent / f"{record.project}.overlay.yaml")
        return named

    return _build


def _overlay_for(record: PullRequestReviewRecord, dimension: ChangeReviewDimension) -> ConfigOverlaySpec:
    kinds = ("calls", "imports", "inherits", "mentions") if dimension is ChangeReviewDimension.DOC_DRIFT else None
    return ConfigOverlaySpec(track=(record.branch,), retain={"landings": 1}, capture_kinds=kinds)


def _symbols(diff: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for hunks in enclosing_symbols(diff).values():
        for _span, symbol in hunks:
            seen.setdefault(symbol)
    return tuple(seen)


def _base_extra(record: PullRequestReviewRecord) -> dict[str, object]:
    extra: dict[str, object] = {f"symbol_{i}": s for i, s in enumerate(_symbols(record.diff))}
    extra.update({f"finding_{i}": f.text for i, f in enumerate(record.findings)})
    extra.update({"base_sha": record.base_sha, "head_sha": record.head_sha, "branch": record.branch, "project": record.project})
    return extra


@dataclass
class _PrReviewBase:
    """Shared loader body; subclasses set the name, the dimension, the gold and the row filter."""

    name: str = "pr-review-py"
    revision: str = "unpinned"
    fixture_path: Path | None = None
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)
    cache_dir: Path | None = None
    corpus_parent: Path | None = None
    dimension: ChangeReviewDimension = ChangeReviewDimension.BLAST_RADIUS
    _rows_cache: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)

    async def tasks(self) -> AsyncIterator[EvalTask]:
        yielded, dropped = 0, 0
        for row in await self._rows():
            try:
                record = _record_from_row(row)
                task = self._task_for(record)
            except BugLocGoldError as exc:
                dropped += 1
                log.info("%s: dropping %r — %s", self.name, row.get("record_id"), exc)
                continue
            if task is None:
                continue  # not a row of this dimension
            yielded += 1
            yield task
        log.info("%s: yielded %d task(s), dropped %d record(s) with underivable gold", self.name, yielded, dropped)

    async def _rows(self) -> list[dict[str, Any]]:
        if self._rows_cache is not None:
            return self._rows_cache
        if self.fixture_path is not None:
            text = self.fixture_path.read_text(encoding="utf-8")
            self._rows_cache = [json.loads(line) for line in text.splitlines() if line.strip()]
            return self._rows_cache
        if PR_REVIEW_PY_PIN is None:
            raise PrReviewCorpusUnpinnedError(
                "the pull-request-review corpus is not pinned: fill PR_REVIEW_PY_PIN from the "
                "owner's decision (task-layer design §13 O9 — dataset id, revision, files, "
                "expected_rows) or pass fixture_path="
            )
        pin = PR_REVIEW_PY_PIN
        self._rows_cache = await asyncio.to_thread(lambda: read_parquet_rows(download_parquet(pin, self.cache_dir), _COLUMNS, pin))
        return self._rows_cache

    def _task_for(self, record: PullRequestReviewRecord) -> EvalTask | None:
        gold = self._gold(record)
        if gold is None:
            return None
        return EvalTask(
            task_id=mint_framed_task_id(dataset=self.name, task_name=CHANGE_REVIEW_TASK_NAME, record_id=record.record_id),
            record_id=record.record_id,
            query=render_change_review_prompt(record.project, record.branch, self._request()),
            gold=gold,
            corpus_source=_corpus_source(self.repo_cache, record, self.corpus_parent, _overlay_for(record, self.dimension)),
            metadata={
                "repo": record.repo_url.rsplit("/", 1)[-1].removesuffix(".git"),
                DIMENSION_METADATA_KEY: self.dimension.value,
                "gold_file_count": str(len(gold.file_set)),
                "changed_file_count": str(len(non_test_paths(paths_from_unified_diff(record.diff)))),
                "surface_stage": "S1",
                "search_scope": "diff",
                "search_branch": record.branch,
                "config_overlay": str((self.corpus_parent or Path(".")) / f"{record.project}.overlay.yaml"),
            },
        )

    def _request(self) -> str:
        return _REVIEW_REQUEST

    def _gold(self, record: PullRequestReviewRecord) -> GoldAnswer | None:
        raise NotImplementedError


@dataset_registry.register("pr-review-py")
@dataclass
class PrReviewPyDataset(_PrReviewBase):
    """Blast-radius review: gold = the paths carrying review findings."""

    def _gold(self, record: PullRequestReviewRecord) -> GoldAnswer | None:
        paths: dict[str, None] = {}
        for finding in record.findings:
            paths.setdefault(finding.path)
        if not paths:
            raise BugLocGoldError(f"record {record.record_id!r} carries no findings")
        return GoldAnswer(file_set=tuple(paths), extra=_base_extra(record))


@dataset_registry.register("pr-review-py-doc-drift")
@dataclass
class PrReviewPyDocDriftDataset(_PrReviewBase):
    """Doc drift: the rows whose diff touches a symbol an indexed ``.md`` mentions."""

    name: str = "pr-review-py-doc-drift"
    dimension: ChangeReviewDimension = ChangeReviewDimension.DOC_DRIFT

    def _gold(self, record: PullRequestReviewRecord) -> GoldAnswer | None:
        if not record.docs_mentioning:
            return None
        return GoldAnswer(file_set=tuple(record.docs_mentioning), extra=_base_extra(record))


@dataset_registry.register("pr-review-py-description")
@dataclass
class PrReviewPyDescriptionDataset(_PrReviewBase):
    """PR description: rows with a merged description; gold = the change set (judge-dominant)."""

    name: str = "pr-review-py-description"
    dimension: ChangeReviewDimension = ChangeReviewDimension.PR_DESCRIPTION

    def _request(self) -> str:
        return _DESCRIPTION_REQUEST

    def _gold(self, record: PullRequestReviewRecord) -> GoldAnswer | None:
        if not record.description.strip():
            return None
        extra = _base_extra(record)
        extra["description"] = record.description
        return GoldAnswer(file_set=non_test_paths(paths_from_unified_diff(record.diff)), extra=extra)


__all__ = [
    "PR_REVIEW_PY_PIN",
    "PrReviewCorpusUnpinnedError",
    "PrReviewPyDataset",
    "PrReviewPyDescriptionDataset",
    "PrReviewPyDocDriftDataset",
    "PullRequestReviewRecord",
    "ReviewFinding",
    "render_change_review_prompt",
]
```

Register the three classes in `datasets/__init__.py` (import + `__all__`). The `config_overlay` metadata path is where `_corpus_source` renames the overlay to, so the sweep finds it after `corpus_source()` ran (the sweep calls `corpus_source()` before `_config_for_task`; `corpus_parent` must therefore be set on the production path — the arm's dataset kwargs pass `corpus_parent=<workspace>/corpora`, and `RepoCache`'s default root is used when it is `None`: set `self.corpus_parent = self.corpus_parent or Path(self.repo_cache.root) / "history"` in a `__post_init__` on the base — `RepoCache.root` exists; a fake cache without `root` keeps `None`).

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_pr_review.py benchmarks/tests/test_registry_population.py -q`
Expected: PASS (plus the pre-existing registry-population failures, if any, unchanged).

- [ ] **Step 6: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/pr_review.py benchmarks/src/pydocs_eval/datasets/__init__.py benchmarks/tests/fixtures/pr_review_py_mini.jsonl benchmarks/tests/datasets/test_pr_review.py
git commit -m "eval: pr-review-py + doc-drift + description datasets over history-preserving corpora (release pin owner-gated)"
```

---

### Task 4: `swe-bench-verified-test-gap` — the S1 shape

**Files:**
- Modify: `benchmarks/src/pydocs_eval/datasets/change_review.py`
- Test: `benchmarks/tests/datasets/test_change_review_test_gap.py`

**Interfaces:**
- Produces: `SweBenchVerifiedTestGapDataset(surface_stage: SurfaceStage = SurfaceStage.S0, corpus_parent=None)`; `SurfaceStage {S0, S1}` (`StrEnum`); on S1 the corpus is history-preserving with branch `change/<instance_id>` carrying `patch`, `extra["branch"]`, `metadata["search_scope"] = "changed"`, `metadata["search_branch"]`, `metadata["config_overlay"]`, and the prompt carries the project + branch instead of the diff.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/datasets/test_change_review_test_gap.py`:

```python
def test_s1_shape_builds_a_synthetic_branch_and_drops_the_diff_from_the_prompt(tmp_path):
    import shutil as _shutil
    import subprocess

    from pydocs_eval.datasets.change_review import SurfaceStage

    if _shutil.which("git") is None:
        pytest.skip("git binary required")
    source = tmp_path / "astropy"
    (source / "astropy" / "modeling").mkdir(parents=True)
    (source / "astropy" / "modeling" / "separable.py").write_text(
        "def _cstack(left, right):\n    if left:\n        cright = _coord_matrix(right, 'right', noutp)\n    else:\n        cright = np.zeros((noutp, right.shape[1]))\n        cright[-right.shape[0]:, -right.shape[1]:] = 1\n\n    return np.hstack([cleft, cright])\n\n",
        encoding="utf-8",
    )
    env = {"PATH": __import__("os").environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True, env=env)
    subprocess.run(["git", "add", "."], cwd=source, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=source, check=True, env=env)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True, env=env).stdout.strip()

    class _Cache(_FakeRepoCache):
        def base_clone(self, url: str) -> Path:
            return source

    rows = [json.loads(l) for l in _GAP_FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows[0]["base_commit"] = base_sha
    fixture = tmp_path / "rows.jsonl"
    fixture.write_text("".join(json.dumps(r) + "\n" for r in rows[:1]), encoding="utf-8")
    dataset = SweBenchVerifiedTestGapDataset(fixture_path=fixture, repo_cache=_Cache(), surface_stage=SurfaceStage.S1, corpus_parent=tmp_path / "corpora")

    async def _collect():
        return [t async for t in dataset.tasks()]

    (task,) = asyncio.run(_collect())
    assert task.gold.extra["branch"] == "change/astropy__astropy-12907"
    assert task.metadata["surface_stage"] == "S1" and task.metadata["search_scope"] == "changed"
    assert "unified diff" not in task.query and "Branch: change/astropy__astropy-12907" in task.query
    corpus = task.corpus_source()
    try:
        head = subprocess.run(["git", "rev-parse", "change/astropy__astropy-12907~1"], cwd=corpus, check=True, capture_output=True, text=True, env=env).stdout.strip()
        assert head == base_sha
    finally:
        _shutil.rmtree(corpus.parent)
```

(add `import json` to the test module's imports).

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_change_review_test_gap.py -q -k s1_shape`
Expected: FAIL — `ImportError: SurfaceStage`.

- [ ] **Step 3: Add the stage switch**

In `change_review.py` add:

```python
class SurfaceStage(StrEnum):
    """Which server surface the dataset shapes itself for (task-layer §10)."""

    S0 = "S0"  # history-less corpus, diff in the prompt
    S1 = "S1"  # history-preserving corpus, synthetic branch carrying the patch


_S1_PROMPT = (
    "Project: {project}\nBranch: {branch}\n\n"
    "Review this change for test gaps: for every changed non-test symbol with no "
    "changed test, name the test file that should exercise it. Answer in fixed "
    "sections (summary, findings, blast radius, test gap)."
)
```

fields `surface_stage: SurfaceStage = SurfaceStage.S0` and `corpus_parent: Path | None = None` on the dataset, and in `_row_to_task` replace the `query=`, `corpus_source=` and the `extra` / `metadata` construction with a stage branch:

```python
        branch = f"change/{instance_id}" if self.surface_stage is SurfaceStage.S1 else ""
        extra = _gold_extra(patch, changed, base_commit)
        extra["branch"] = branch
        extra["project"] = instance_id if branch else ""
        metadata = {
            "repo": str(row["repo"]),
            DIMENSION_METADATA_KEY: ChangeReviewDimension.TEST_GAP.value,
            "gold_file_count": str(len(test_paths)),
            "changed_file_count": str(len(changed)),
            "version": str(row.get("version") or ""),
            "difficulty": str(row.get("difficulty") or ""),
            "surface_stage": self.surface_stage.value,
        }
        url = _GITHUB_URL.format(owner=owner, name=repo_name)
        if branch:
            metadata.update({"search_scope": "changed", "search_branch": branch, "config_overlay": str(self._overlay_path(instance_id))})
            query = _S1_PROMPT.format(project=instance_id, branch=branch)
            source = self._history_corpus_source(url, base_commit, instance_id, branch, patch)
        else:
            query = render_test_gap_prompt(str(row.get("problem_statement") or ""), patch)
            source = _corpus_source(self.repo_cache, url, base_commit)
        return EvalTask(task_id=..., record_id=instance_id, query=query, gold=GoldAnswer(file_set=test_paths, extra=extra), corpus_source=source, metadata=metadata)
```

with the two helpers on the class:

```python
    def _overlay_path(self, instance_id: str) -> Path:
        return (self.corpus_parent or Path(".")) / f"{instance_id}.overlay.yaml"

    def _history_corpus_source(self, url: str, base_commit: str, instance_id: str, branch: str, patch: str):
        def _build() -> Path:
            corpus = materialize_corpus_with_history(
                self.repo_cache.base_clone(url),
                base_ref=base_commit,
                overlay=ConfigOverlaySpec(track=(branch,), retain={"landings": 1}),
                parent=self.corpus_parent,
            )
            apply_diff_as_commit(corpus.root, base=corpus.base, branch=branch, diff_text=patch, subject=f"fix {instance_id}")
            named = corpus.root.parent / instance_id
            corpus.root.rename(named)
            corpus.overlay_path.rename(self._overlay_path(instance_id))
            return named

        return _build
```

(imports: `from enum import StrEnum`, `from .history_corpus import ConfigOverlaySpec, apply_diff_as_commit, materialize_corpus_with_history`; the `_FakeRepoCache` in the existing tests gains a `base_clone` method returning its corpus dir so S0 tests keep passing — S0 never calls it).

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/datasets/test_change_review_test_gap.py -q`
Expected: PASS (S0 tests byte-identical; S1 builds the branch).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/pydocs_eval/datasets/change_review.py benchmarks/tests/datasets/test_change_review_test_gap.py
git commit -m "eval: swe-bench-verified-test-gap S1 shape — synthetic change/<id> branch, scoped search, overlay"
```

---

### Task 5: The `change_review` arm config and the workspace prewarm tool

**Files:**
- Create: `benchmarks/src/pydocs_eval/optimize/configs/optimize_search_skill_change_review.yaml`
- Create: `benchmarks/tools/prewarm_change_review_workspace.py`
- Test: `benchmarks/tests/optimize/test_change_review_arms.py`

**Interfaces:**
- Consumes: the `ask_rubric_change_review` section (T0), the four datasets (Tasks 3–4), `known_task_names`, `load_run_config`, `resolve_arms`.
- Produces: the shipped config (four arms, one objective); the tool `python benchmarks/tools/prewarm_change_review_workspace.py --dataset <name> --workspace <dir> [--max-records N]` that materializes every record's corpus, indexes it with the record's overlay (`pydocs-mcp --config <overlay> index <corpus> --branch <branch>` on P1; `index <corpus>` on P0), and copies the bundle (`.db` + `.tq`) into `<workspace>/`.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/optimize/test_change_review_arms.py`:

```python
"""Four dimension datasets, ONE task name, ONE rubric — the change_review arms config."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from pydocs_eval.optimize.ask_binding import known_task_names
from pydocs_eval.optimize.run_config import _configured_rubric_sections, load_run_config

_CONFIG = "optimize_search_skill_change_review.yaml"


def _shipped(name: str) -> Path:
    return Path(str(resources.files("pydocs_eval.optimize.configs").joinpath(name)))


@pytest.fixture
def change_review():
    return load_run_config(_shipped(_CONFIG))


def test_every_arm_declares_change_review(change_review) -> None:
    assert "change_review" in known_task_names()
    assert {arm.task_name for arm in change_review.arms} == {"change_review"}
    assert [arm.dataset for arm in change_review.arms] == [
        "pr-review-py",
        "swe-bench-verified-test-gap",
        "pr-review-py-doc-drift",
        "pr-review-py-description",
    ]


def test_one_objective_bound_and_weights_keyed_on_dataset_prefixes(change_review) -> None:
    assert sorted(_configured_rubric_sections(change_review)) == ["ask_rubric_change_review"]
    section = change_review.ask_rubric_change_review
    checks = {c.name: c for c in section.checks}
    assert set(checks["findings_located"].weight_by_type) <= {arm.dataset for arm in change_review.arms}
    assert checks["sections_present"].required and checks["sections_present"].weight == 0.0
    assert checks["change_consulted"].kind == "slice_consulted"
    assert section.keep_deterministic_on_skip is True


def test_the_test_gap_arm_names_its_stage_in_dataset_kwargs(change_review) -> None:
    (gap,) = [arm for arm in change_review.arms if arm.dataset == "swe-bench-verified-test-gap"]
    assert gap.dataset_kwargs.get("surface_stage") == "S1"
```

(If `ArmCell` has no `dataset_kwargs` field, add it in `optimize/arms.py` as `dataset_kwargs: Mapping[str, object] = Field(default_factory=dict)` with the same `MappingProxyType` freeze the `settings` field uses, thread it into `dataset_registry.build(arm.dataset, **arm.dataset_kwargs)` in `arm_runtime.resolve_arms`, and fold it into `ArmCell.to_canonical()` so the arm hash covers it.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_change_review_arms.py -q`
Expected: FAIL — the config file does not exist.

- [ ] **Step 3: Write the config**

`benchmarks/src/pydocs_eval/optimize/configs/optimize_search_skill_change_review.yaml`:

```yaml
# optimize_search_skill_change_review.yaml — ONE task name, FOUR dimension
# datasets, ONE rubric.
#
# change_review (task-layer design §6.2): given one change — a live branch —
# and a request, produce a review in fixed sections (summary, findings, blast
# radius, test gap, doc drift). Every arm declares task_name: change_review,
# so all four read AND update the same harness-invariant TASK_HEAD section;
# what differs is the DIMENSION each dataset stamps (metadata["dimension"])
# and, through weight_by_type keyed on the DATASET prefix, how the
# deterministic layer is apportioned:
#
#   arms[0]  pr-review-py                (blast_radius)  gold = finding paths
#   arms[1]  swe-bench-verified-test-gap (test_gap)      gold = test_patch paths
#   arms[2]  pr-review-py-doc-drift      (doc_drift)     gold = doc paths
#   arms[3]  pr-review-py-description    (pr_description) judge-dominant
#
# surface_stage: S1 — the corpora are history-preserving clones with a
# synthetic branch; the runners read a workspace prepared by
# benchmarks/tools/prewarm_change_review_workspace.py (one bundle per record).
# change_consulted scores at full weight from S2a (the changed/diff scope
# values); on S1 the arm reads the branch as a whole and the check is
# apportioned to 0 per dataset below — a dated rubric change that moves
# rubric_config_hash, so S1 and S2a numbers are NOT pooled.
artifact: search_skill
optimizer: skillopt
ladder:
  - [ask_rubric, 6, 4]
  - [ask_rubric, 24, 1]
accept_margin: 0.02
budget: { max_trials: 40, max_usd: 80.0, wall_timeout_seconds: 28800 }
dataset: { name: pr-review-py }
rng_seed: 0

ask_rubric_change_review:
  runner:
    model: claude-sonnet-5
    architecture: text_react
  gates:
    - { name: non_empty, kind: min_answer_chars, params: { n: 40 } }
    - { name: grounded, kind: used_indexed_tools, params: { n: 1 } }
  checks:
    - name: findings_located
      kind: gold_recall
      params: { keys: [file_set] }
      weight: 0.3
      required: false
      fail: null
      weight_by_type: { swe-bench-verified-test-gap: 0.5, pr-review-py-description: 0.0 }
    - name: located_by_evidence
      kind: gold_location_evidenced
      weight: 0.3
      required: false
      fail: null
      weight_by_type: { pr-review-py-description: 0.0 }
    - name: change_consulted
      kind: slice_consulted
      params: { scopes: [changed, diff] }
      weight: 0.2
      required: false
      fail: null
      # surface_stage S1: no slice value is advertised yet.
      weight_by_type: { pr-review-py: 0.0, swe-bench-verified-test-gap: 0.0, pr-review-py-doc-drift: 0.0, pr-review-py-description: 0.0 }
    - name: graph_consulted
      kind: graph_consulted
      params: { directions: [impact, callers] }
      weight: 0.1
      required: false
      fail: null
      weight_by_type: { swe-bench-verified-test-gap: 0.0, pr-review-py-description: 0.0 }
    - name: findings_cited
      kind: answer_regex
      params: { pattern: "(?m)^.+ · [A-Za-z_][\\w.]* — " }
      weight: 0.0
      required: false
      fail: null
    - name: sections_present
      kind: review_headings_present
      params: { dimension: blast_radius }
      weight: 0.0
      required: true
      fail: 1.0
  gate_weight: 0.5
  rubric_weight: 0.5
  keep_deterministic_on_skip: true
  criteria:
    - { name: findings_real, weight: 0.3, description: "Findings are real and cited to a path and a symbol." }
    - { name: blast_radius_true, weight: 0.25, description: "The blast radius names true callers of the changed symbols." }
    - { name: test_gap_complete, weight: 0.25, description: "The test gap is complete: every changed symbol without a changed test is listed with the test file that should cover it." }
    - { name: review_not_tree, weight: 0.2, description: "Reviews the change, not the tree: no finding concerns code the change did not touch." }

arms:
  - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner
    settings: { workspace: ~/pydocs-index/change-review, model: claude-sonnet-5 }
    tool_names: null
    dataset: pr-review-py
    task_name: change_review
    guidance: search_skill
    scoring:
      objective: rubric_verdict
      rubric: ask_rubric_change_review
      tracked: [gold_recall, gold_location_evidenced, slice_consulted, graph_consulted]
  - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner
    settings: { workspace: ~/pydocs-index/change-review, model: claude-sonnet-5 }
    tool_names: null
    dataset: swe-bench-verified-test-gap
    dataset_kwargs: { surface_stage: S1 }
    task_name: change_review
    guidance: search_skill
    scoring:
      objective: rubric_verdict
      rubric: ask_rubric_change_review
      tracked: [gold_recall, gold_location_evidenced, slice_consulted]
  - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner
    settings: { workspace: ~/pydocs-index/change-review, model: claude-sonnet-5 }
    tool_names: null
    dataset: pr-review-py-doc-drift
    task_name: change_review
    guidance: search_skill
    scoring:
      objective: rubric_verdict
      rubric: ask_rubric_change_review
      tracked: [gold_recall, gold_location_evidenced]
  - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner
    settings: { workspace: ~/pydocs-index/change-review, model: claude-sonnet-5 }
    tool_names: null
    dataset: pr-review-py-description
    task_name: change_review
    guidance: search_skill
    scoring:
      objective: rubric_verdict
      rubric: ask_rubric_change_review
      tracked: [slice_consulted]
```

The `sections_present` gate carries `dimension: blast_radius`; the TEST_GAP and DOC_DRIFT dimensions require the same four headings plus DOC_DRIFT for doc drift — add a second gate row `sections_present_doc_drift` with `params: { dimension: doc_drift }`, `applies_to: [pr-review-py-doc-drift]`, and give `sections_present` `applies_to: [pr-review-py, swe-bench-verified-test-gap, pr-review-py-description]` so each dataset has exactly one heading gate (`validate_checks` requires one required applicable check per dataset; `used_indexed_tools` already satisfies it, so the split is about the RIGHT headings, not about passing validation). Also a `pr-review-py-description` row: `sections_present_description` with `dimension: pr_description`, `applies_to: [pr-review-py-description]` — and remove that dataset from `sections_present`'s `applies_to`.

- [ ] **Step 4: Write the prewarm tool**

`benchmarks/tools/prewarm_change_review_workspace.py`:

```python
#!/usr/bin/env python3
"""Index every record of a change_review dataset into ONE workspace directory.

The ask / external optimize paths read a pre-indexed workspace
(``settings.workspace``) and have no per-sample corpus (task-layer design
§7.6, run-contract engine amendment gap 1). This tool closes the gap
offline: for each record it materializes the history-preserving corpus
(``task.corpus_source()``), indexes it with the record's config overlay and
the tracked branch (``pydocs-mcp --config <overlay> index <corpus>``, plus
``--branch <name>`` once the multi-branch P1 CLI has landed), and copies the
bundle (``.db`` + ``.tq``) into ``--workspace``. Each corpus is named after
its record id, so two pull requests of one repository index under distinct
project names and the prompt's ``Project:`` line selects the right bundle.

Usage:
    python benchmarks/tools/prewarm_change_review_workspace.py \\
        --dataset pr-review-py --workspace ~/pydocs-index/change-review [--max-records 20]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from pydocs_eval.registries import dataset_registry


def _bundle_paths(corpus: Path) -> tuple[Path, Path]:
    from pydocs_mcp.db import cache_path_for_project

    db = cache_path_for_project(corpus)
    return db, db.with_suffix(".tq")


def _index(corpus: Path, overlay: Path, branch: str, *, with_branch_flag: bool) -> None:
    argv = [sys.executable, "-m", "pydocs_mcp", "--config", str(overlay), "index", str(corpus), "--skip-deps"]
    if with_branch_flag and branch:
        argv += ["--branch", branch]
    subprocess.run(argv, check=True, timeout=3600)


def _supports_branch_flag() -> bool:
    help_text = subprocess.run([sys.executable, "-m", "pydocs_mcp", "index", "--help"], capture_output=True, text=True, check=False).stdout
    return "--branch" in help_text


async def _prewarm(dataset_name: str, workspace: Path, max_records: int | None, dataset_kwargs: dict[str, object]) -> int:
    workspace.mkdir(parents=True, exist_ok=True)
    dataset = dataset_registry.build(dataset_name, corpus_parent=workspace / "corpora", **dataset_kwargs)
    with_branch = _supports_branch_flag()
    done = 0
    async for task in dataset.tasks():
        if max_records is not None and done >= max_records:
            break
        corpus = task.corpus_source()
        overlay = Path(task.metadata["config_overlay"])
        _index(corpus, overlay, task.metadata.get("search_branch", ""), with_branch_flag=with_branch)
        for source in _bundle_paths(corpus):
            if source.exists():
                shutil.copy2(source, workspace / source.name)
        done += 1
        print(f"indexed {task.record_id} -> {workspace}")
    print(f"prewarmed {done} record(s) of {dataset_name} into {workspace} (branch flag: {with_branch})")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--max-records", type=int, default=None)
    parser.add_argument("--surface-stage", default=None, help="swe-bench-verified-test-gap only: S0 | S1")
    args = parser.parse_args(argv)
    kwargs: dict[str, object] = {}
    if args.surface_stage:
        kwargs["surface_stage"] = args.surface_stage
    asyncio.run(_prewarm(args.dataset, args.workspace.expanduser(), args.max_records, kwargs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/optimize/test_change_review_arms.py benchmarks/tests/optimize/test_arms.py benchmarks/tests/optimize/test_cli_dry_run.py -q && PYTHONPATH=benchmarks/src python benchmarks/tools/prewarm_change_review_workspace.py --help`
Expected: PASS; the help text prints. A dry run of the config walks all four arms on scripted doubles: `PYTHONPATH=benchmarks/src python -m pydocs_eval.optimize --config benchmarks/src/pydocs_eval/optimize/configs/optimize_search_skill_change_review.yaml --dry-run` (the exact flag spelling is in `benchmarks/README.md` §"Optimize"), expected `$0.00` and the per-arm plan.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/src/pydocs_eval/optimize/configs/optimize_search_skill_change_review.yaml benchmarks/tools/prewarm_change_review_workspace.py benchmarks/tests/optimize/test_change_review_arms.py benchmarks/src/pydocs_eval/optimize/arms.py benchmarks/src/pydocs_eval/optimize/arm_runtime.py
git commit -m "eval: change_review arms config (four dimensions, one rubric) + workspace prewarm tool"
```

---

### Task 6: The P2.7 gate — `diff_search.yaml` against dense-only on `pr-review-py`

**Files:**
- Create: `benchmarks/configs/dense_diff.yaml`, `benchmarks/configs/diff_search_rrf.yaml`
- Create: `benchmarks/tools/p27_diff_search_gate.py`
- Test: `benchmarks/tests/test_p27_gate_configs.py`
- Modify: `benchmarks/README.md` (ladder entry)

**Interfaces:**
- Consumes: the product's `pipelines/diff_search.yaml` (multi-branch P2 plan Task 6) routed by `scope=diff`; the retrieval track's `search_scope` (Task 2); `run_sweep`.
- Produces: two sweep overlays and a gate script printing `hit@5`, `map@5`, `recall@10` per config with the README ladder's paired check.

- [ ] **Step 1: Write the failing test**

Create `benchmarks/tests/test_p27_gate_configs.py`:

```python
"""The P2.7 gate's two sweep overlays load and select distinct pipelines."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_CONFIGS = Path(__file__).parents[1] / "configs"


@pytest.mark.parametrize("name", ["dense_diff.yaml", "diff_search_rrf.yaml"])
def test_overlay_names_a_chunk_pipeline(name: str):
    document = yaml.safe_load((_CONFIGS / name).read_text(encoding="utf-8"))
    (entry,) = document["pipelines"]["chunk"]
    assert entry["default"] is True and entry["pipeline_path"].endswith(".yaml")


def test_the_two_overlays_differ():
    a = yaml.safe_load((_CONFIGS / "dense_diff.yaml").read_text(encoding="utf-8"))
    b = yaml.safe_load((_CONFIGS / "diff_search_rrf.yaml").read_text(encoding="utf-8"))
    assert a != b


def test_gate_script_parses():
    import importlib.util

    spec = importlib.util.spec_from_file_location("p27", Path(__file__).parents[1] / "tools" / "p27_diff_search_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    assert module.METRICS == ("hit@5", "map@5", "recall@10")
```

- [ ] **Step 2: Create the overlays and the script**

`benchmarks/configs/dense_diff.yaml`:

```yaml
# P2.7 control — dense-only retrieval over a branch's DIFF slice. The sweep
# sets scope=diff / branch=<record branch> from the task metadata
# (pydocs_eval.sweep._maybe_set_search_scope); this overlay only selects the
# dense pipeline so the slice is ranked by embeddings alone.
pipelines:
  chunk:
    - default: true
      pipeline_path: pipelines/exp_dense.yaml
```

`benchmarks/configs/diff_search_rrf.yaml`:

```yaml
# P2.7 candidate — the product's diff_search preset (BM25 ∥ dense, RRF-fused)
# over the same DIFF slice. Promotion follows benchmarks/README.md's ladder:
# beat dense_diff.yaml on pr-review-py dev beyond noise, confirm once on test.
pipelines:
  chunk:
    - default: true
      pipeline_path: pipelines/diff_search.yaml
```

`benchmarks/tools/p27_diff_search_gate.py`:

```python
#!/usr/bin/env python3
"""P2.7: does diff_search.yaml beat dense-only on the pr-review-py DIFF slice?

Runs the two overlays on ``--split dev`` (small_dev while iterating), prints
``hit@5`` / ``map@5`` / ``recall@10`` with bootstrap CIs per config, and the
per-task paired verdict the README ladder asks for. Promotion is recorded by
hand in benchmarks/README.md; this script never flips a shipped default.

Usage:
    PYTHONPATH=benchmarks/src python benchmarks/tools/p27_diff_search_gate.py --split dev
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pydocs_eval.sweep import run_sweep

METRICS = ("hit@5", "map@5", "recall@10")
CONTROL = Path("benchmarks/configs/dense_diff.yaml")
CANDIDATE = Path("benchmarks/configs/diff_search_rrf.yaml")


async def _gate(split: str, limit: int | None) -> int:
    results, ran = await run_sweep(
        systems=("pydocs",),
        config_paths=(CONTROL, CANDIDATE),
        dataset_name="pr-review-py",
        dataset_kwargs={"split": split},
        metric_specs=METRICS,
        limit=limit,
    )
    for key, metrics in results.items():
        cells = "  ".join(f"{m}={metrics[m][0]:.3f} [{metrics[m][1]:.3f}, {metrics[m][2]:.3f}]" for m in METRICS if m in metrics)
        print(f"{key[1]:<22} {cells}")
    control = results[("pydocs", CONTROL.stem)]
    candidate = results[("pydocs", CANDIDATE.stem)]
    beats = candidate["hit@5"][1] > control["hit@5"][2]  # non-overlapping CIs on the headline
    print(f"tasks={ran}  candidate beats control on hit@5 beyond noise: {beats}")
    return 0 if beats else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", default="dev", choices=("small_dev", "dev", "test"))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    return asyncio.run(_gate(args.split, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
```

(`run_sweep`'s keyword names follow `pydocs_eval.sweep.run_sweep`'s signature — `systems`, `config_paths`, `dataset_name`, `dataset_kwargs`, `metric_specs`, `limit` are the names `optimize/fitness/retrieval.py` already passes; match them exactly. `pr-review-py` takes `split` through `stratified_split` once Task 3's loader gains the `split` / `dev_fraction` / `seed` fields the other repo-backed loaders carry — add them in this task if absent: `split: str = "all"`, `dev_fraction: float = 0.7`, `seed: int = 0`, stratum = the repository name, sort key = the record id.)

- [ ] **Step 3: README ladder entry**

Append to the benchmarks README's "Sweep protocol" section:

```markdown
**Diff-slice retrieval (P2.7).** `benchmarks/configs/diff_search_rrf.yaml`
(the product's BM25 ∥ dense preset over a branch's diff hunks) is promoted
over `benchmarks/configs/dense_diff.yaml` only if it beats it on
`pr-review-py` `dev` beyond noise (`hit@5` headline, `map@5`, `recall@10`) and
confirms once on `test`; `benchmarks/tools/p27_diff_search_gate.py` runs the
comparison and prints the verdict. The slice is selected per task from the
dataset's metadata (`search_scope=diff`, `search_branch=<record branch>`),
never by a tool parameter.
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/test_p27_gate_configs.py -q`
Expected: PASS. The gate itself runs only against a P2 build (`diff_search.yaml` shipped); before that, `--split small_dev --limit 2` fails loudly at index time naming the missing pipeline path — expected.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/configs/dense_diff.yaml benchmarks/configs/diff_search_rrf.yaml benchmarks/tools/p27_diff_search_gate.py benchmarks/tests/test_p27_gate_configs.py benchmarks/README.md
git commit -m "eval: P2.7 diff-slice retrieval gate (diff_search_rrf vs dense_diff on pr-review-py)"
```

---

### Task 7: Scripted trajectory tests over a fake server advertising the P2 surface

**Files:**
- Create: `benchmarks/tests/agent_track/_fake_change_tools.py`
- Create: `benchmarks/tests/agent_track/test_change_review_trajectories.py`

**Interfaces:**
- Produces: `FakeChangeServer(branches: Mapping[str, ...], landing_shas: set[str], base: str, diff_pending: bool)` exposing nine LangChain tools whose `args_schema` advertises `branch` and the `changed` / `diff` scope values, recording every call, answering canned text, raising `InvalidArgumentError`-shaped errors for `get_references(branch=<sha>)`, and returning the `is being generated` suggestion while `diff_pending`; `write_server_events(trace_dir, calls)` mirroring the recorder's `tool_call` line shape; `ScriptedToolCallingLlm(steps)`.
- The tests drive the product's `build_agent(mcp_tools=fake_tools)` graph with the scripted LLM, then score `slice_consulted` / `graph_consulted` over the events the fake wrote.

- [ ] **Step 1: The fake server and the scripted model**

`benchmarks/tests/agent_track/_fake_change_tools.py`:

```python
"""A fake nine-tool server advertising the P2 surface, for trajectory tests.

Records calls, answers canned text, refuses ``get_references`` on a landing
sha (the multi-branch spec §6.5b rule), and returns the working-tree
``is being generated`` suggestion while ``diff_pending`` — so the scripted
trajectories of the change_review head can be asserted without a product
server or an LLM.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from tests.optimize._trajectories import make_trajectory  # noqa: F401 — re-export convenience

NINE = ("get_overview", "search_codebase", "get_symbol", "get_context", "get_references", "get_why", "grep", "glob", "read_file")
_SCOPE_ENUM = ["project", "deps", "all", "changed", "diff"]


@dataclass
class FakeChangeServer:
    base: str = "main"
    live_branches: tuple[str, ...] = ("main", "feature/retry")
    landing_shas: tuple[str, ...] = ()
    diff_pending: bool = False
    calls: list[dict[str, object]] = field(default_factory=list)

    def _schema(self, name: str) -> dict[str, object]:
        props: dict[str, object] = {"project": {"type": "string", "default": ""}, "branch": {"type": "string", "default": ""}}
        if name in ("search_codebase", "grep"):
            props["scope"] = {"type": "string", "enum": _SCOPE_ENUM, "default": "all" if name == "search_codebase" else "project"}
        if name == "get_references":
            props["direction"] = {"type": "string", "enum": ["callers", "callees", "inherits", "impact", "governed_by"], "default": "callers"}
        return {"type": "object", "properties": props}

    def _answer(self, name: str, args: Mapping[str, object]) -> str:
        branch = str(args.get("branch") or self.base)
        if name == "get_references" and branch in self.landing_shas:
            raise ValueError(f"branch={branch!r} is a landing unit; get_references answers on its base branch {self.base!r}")
        if name in ("search_codebase", "grep") and args.get("scope") == "diff" and self.diff_pending:
            self.diff_pending = False  # the lazy job "finishes" after one retry
            return "[index: current]\n(no results)\nsuggestion: the diff of feature/retry is being generated; retry shortly"
        if name == "search_codebase":
            return "[index: current]\n1. pkg/retry.py:12-30 pkg.retry.fetch — hunk\n"
        if name == "get_references":
            return f"[index: current]\nimpact of pkg.retry.fetch on {branch}: pkg.client.Client.get (caller)\n"
        if name == "get_overview":
            return "[index: current]\nbranch card: feature/retry (base main @abc1234) files changed: pkg/retry.py\n"
        return "[index: current]\n(ok)\n"

    def tools(self) -> list[StructuredTool]:
        made = []
        for name in NINE:

            def _run(_name: str = name, **kwargs: object) -> str:
                self.calls.append({"tool": _name, "args": dict(kwargs)})
                return self._answer(_name, kwargs)

            tool = StructuredTool.from_function(func=_run, name=name, description=f"fake {name}")
            tool.args_schema = self._schema(name)  # the adapter's dict shape
            made.append(tool)
        return made


def write_server_events(trace_dir: Path, calls: Sequence[Mapping[str, object]]) -> Path:
    """The recorder's ``tool_call`` line shape (see test_trajectory_evidence._tool_event)."""
    trace_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"_event": "tool_call", "seq": i + 1, "tool": call["tool"], "args": call["args"], "error": None, "hit_count": 1, "result_ids": [], "trajectory_id": "traj", "ts": 0.0})
        for i, call in enumerate(calls)
    ]
    (trace_dir / "server_events.jsonl").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return trace_dir


class ScriptedToolCallingLlm:
    """A minimal chat model double: replays AIMessages (tool calls, then the answer)."""

    def __init__(self, steps: Sequence[AIMessage]) -> None:
        self._steps = list(steps)

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs) -> AIMessage:
        return self._steps.pop(0)

    def invoke(self, messages, **kwargs) -> AIMessage:
        return self._steps.pop(0)


def tool_call(name: str, call_id: str, **args: object) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": dict(args), "id": call_id}])
```

- [ ] **Step 2: The trajectory tests**

`benchmarks/tests/agent_track/test_change_review_trajectories.py`:

```python
"""AC-18…AC-21 — the change_review trajectories the head prescribes, scripted
over a fake P2 server: slice first, graph second, base branch for units, one
retry on a pending diff. The model is scripted (the head cannot drive a fake),
so these pin the harness plumbing and the checks, not model behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize.rubric.checks import Check, evaluate_check
from tests.agent_track._fake_change_tools import (
    FakeChangeServer,
    ScriptedToolCallingLlm,
    tool_call,
    write_server_events,
)
from tests.optimize._trajectories import make_trajectory

_REVIEW = "## Summary\nretry loop\n## Findings\n- pkg/retry.py · fetch — returns on the first iteration\n## Blast radius\npkg.client.Client.get\n## Test gap\nnone\n"


def _run(server: FakeChangeServer, steps: list[AIMessage]) -> str:
    from pydocs_mcp.harness.ask_your_docs.architectures import agent_registry

    arch = agent_registry.get("text_react")()
    from pydocs_mcp.harness.ask_your_docs.architectures import AgentBuildContext
    from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

    graph = arch.build(
        AgentBuildContext(
            llm=ScriptedToolCallingLlm(steps),
            tools=server.tools(),
            prompt="scripted",
            capabilities=ModelCapabilities(multimodal=False, source="override"),
            config=AskYourDocsConfig(),
        )
    )
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage("review feature/retry")]}))
    return result["messages"][-1].content


def _task() -> EvalTask:
    return EvalTask("pr-review-py/change_review/r1", "q", GoldAnswer(file_set=("pkg/retry.py",)), lambda: Path("/dev/null"))


def test_ac18_live_branch_review_consults_the_diff_slice_then_impact(tmp_path: Path):
    server = FakeChangeServer()
    steps = [
        tool_call("get_overview", "c1", branch="feature/retry"),
        tool_call("search_codebase", "c2", query="retry", scope="diff", branch="feature/retry"),
        tool_call("get_references", "c3", target="pkg.retry.fetch", direction="impact", branch="feature/retry"),
        AIMessage(content=_REVIEW),
    ]
    answer = _run(server, steps)
    assert "pkg.client.Client.get" in answer
    scopes = [c["args"].get("scope") for c in server.calls if c["tool"] == "search_codebase"]
    assert scopes == ["diff"]
    trace = write_server_events(tmp_path / "traj", server.calls)
    trajectory = make_trajectory(answer=answer, trace_dir=trace)
    assert evaluate_check(Check(name="s", kind="slice_consulted", params={"scopes": ["changed", "diff"]}, fail=None), _task(), trajectory).score == 1.0
    assert evaluate_check(Check(name="g", kind="graph_consulted", params={"directions": ["impact"]}, fail=None), _task(), trajectory).score == 1.0


def test_ac19_landing_unit_review_walks_the_graph_on_the_base(tmp_path: Path):
    sha = "3e1a9c2" + "0" * 33
    server = FakeChangeServer(landing_shas=(sha,))
    steps = [
        tool_call("search_codebase", "c1", query="retry", scope="diff", branch=sha),
        tool_call("get_references", "c2", target="pkg.retry.fetch", direction="impact", branch="main"),
        AIMessage(content=_REVIEW),
    ]
    _run(server, steps)
    references = [c for c in server.calls if c["tool"] == "get_references"]
    assert references == [{"tool": "get_references", "args": {"target": "pkg.retry.fetch", "direction": "impact", "branch": "main"}}]
    # The fake refuses the sha — a scripted call to it surfaces as a tool error, never a silent answer.
    server_bad = FakeChangeServer(landing_shas=(sha,))
    bad = [tool_call("get_references", "c1", target="pkg.retry.fetch", direction="impact", branch=sha), AIMessage(content="gave up")]
    _run(server_bad, bad)
    assert server_bad.calls[0]["args"]["branch"] == sha


def test_ac21_pending_diff_is_retried_once_then_falls_back_to_changed(tmp_path: Path):
    server = FakeChangeServer(diff_pending=True)
    steps = [
        tool_call("search_codebase", "c1", query="retry", scope="diff", branch="feature/retry"),
        tool_call("search_codebase", "c2", query="retry", scope="diff", branch="feature/retry"),
        tool_call("search_codebase", "c3", query="retry", scope="changed", branch="feature/retry"),
        AIMessage(content=_REVIEW),
    ]
    _run(server, steps)
    scopes = [c["args"]["scope"] for c in server.calls if c["tool"] == "search_codebase"]
    assert scopes == ["diff", "diff", "changed"]
    assert scopes.count("diff") <= 2  # at most one retry


def test_ac20_release_notes_enumerates_once_then_reads_at_most_one_card_per_unit(tmp_path: Path):
    shas = tuple(f"{i}" * 40 for i in ("a", "b", "c"))
    server = FakeChangeServer(landing_shas=shas)
    steps = [tool_call("get_overview", "c0", branch="main")]
    steps += [tool_call("get_overview", f"c{i + 1}", branch=sha) for i, sha in enumerate(shas)]
    steps.append(AIMessage(content="## Fixed\n- retry (" + ", ".join(s[:7] for s in shas) + "; pkg/retry.py)\n"))
    answer = _run(server, steps)
    overview_calls = [c for c in server.calls if c["tool"] == "get_overview"]
    assert overview_calls[0]["args"] == {"branch": "main"}
    assert len(overview_calls) == 1 + len(shas)
    assert all(sha[:7] in answer for sha in shas)
    trace = write_server_events(tmp_path / "traj", server.calls)
    check = Check(name="units", kind="card_consulted", params={"tools": ["get_overview"], "min_calls": 2}, fail=None)
    assert evaluate_check(check, _task(), make_trajectory(answer=answer, trace_dir=trace)).score == 1.0
```

- [ ] **Step 3: Run the tests**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/agent_track/test_change_review_trajectories.py -q`
Expected: PASS. If `create_react_agent` (the `text_react` architecture) requires a `BaseChatModel` rather than the duck-typed double, subclass `tests/harness/ask_your_docs/_agent_fakes.py`'s `FakeLlm` with a `_generate` that returns the scripted `AIMessage` (including its `tool_calls`) instead.

- [ ] **Step 4: Commit**

```bash
git add benchmarks/tests/agent_track/_fake_change_tools.py benchmarks/tests/agent_track/test_change_review_trajectories.py
git commit -m "eval tests: scripted change_review / release_notes trajectories over a fake P2 server"
```

---

### Task 8: Documents and gates

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` (§6.5a — the G4 sentence)
- Modify: `benchmarks/README.md` (three subsections), `CHANGELOG.md`

- [ ] **Step 1: The G4 amendment (owner-ratified in the multi-branch program; recorded here)**

In the multi-branch spec §6.5a, after the sentence describing hunk chunks' titles, add: `A hunk chunk's ``qualified_name`` is the enclosing symbol's dotted name on the new side of the hunk (the ``symbol_labeler`` of P2 plan Task 4), so a hunk hit chains into ``get_references`` / ``get_symbol`` without a second lookup (task-layer design R7 / G4, amended 2026-09-04).` Add the dated line to the spec's Amendments log.

- [ ] **Step 2: README subsections**

Under "Datasets", after the test-gap subsection, add:

```markdown
### Change review — findings, doc drift, description (`pr-review-py` family)

**What it measures.** The blast-radius review of one merged change against its
base (`pr-review-py`: gold = the paths carrying reviewer findings), the
doc-drift dimension (`pr-review-py-doc-drift`: gold = the docs that mention a
changed symbol), and the description dimension (`pr-review-py-description`:
judge-dominant, gold = the change set). One record = one merged pull request
with reviewer comments anchored to file and line; the corpus is a
history-preserving clone at the base commit with the merged diff applied as
one synthetic commit on `review/<record_id>` — head refs are never fetched.

**Where the data comes from.** A published pull-request-review corpus,
Python slice, pinned by academic citation, revision and row count in the
loader (`PR_REVIEW_PY_PIN`). Until the owner fills the pin the release path
refuses loudly; the committed fixture drives every test.

**Corpus mode.** `materialize_corpus_with_history` (used only by the branch /
diff datasets) clones the shared base clone, checks the base out as a local
branch with no remote, applies the record's diff, and writes a per-record
config overlay (tracked branches, retention window, exclusions, decision
sources) that the sweep layers over its own config. Disk cost: one clone per
record (the shared worktree cache is untouched). The ask and external
optimize tracks read a workspace pre-indexed from the same corpora
(`benchmarks/tools/prewarm_change_review_workspace.py`), one bundle per record.
```

Extend the test-gap subsection's "Shape today" paragraph with the S1 sentence: `S1 (\`surface_stage: S1\`) is the same records over a history-preserving corpus with the fix patch on the synthetic branch \`change/<instance_id>\`; the arm config names the stage.`

`CHANGELOG.md` bullet (under the T0 entry): `- **change_review datasets on live branches** — history-preserving corpora with per-record config overlays, the pull-request-review family (findings / doc drift / description), the S1 test-gap shape, the change_review arm config with a workspace prewarm tool, the P2.7 diff-slice retrieval gate, and scripted trajectory tests over a fake server advertising the branch and slice surface.`

- [ ] **Step 3: Gates**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" -not -path "*/node_modules/*" -not -path "*/.git/*" | xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
grep -rniE "github|gitlab|bitbucket" benchmarks/src/pydocs_eval/datasets/pr_review.py benchmarks/README.md | grep -v "_GITHUB_URL\|github.com/{owner}" || true
ruff format python/ tests/ benchmarks/ && ruff check python/ tests/ benchmarks/ && mypy python/pydocs_mcp && complexipy python/pydocs_mcp --max-complexity-allowed 15 && vulture python/pydocs_mcp --min-confidence 80
pytest tests/ --ignore=tests/test_parity.py -q
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
uv lock --check
git checkout -- complexipy-snapshot.json
```

Expected: no audit matches; the hosting-service grep returns only the pre-existing clone-URL template of `bug_localization.py` (none in the new prose); every gate green.

- [ ] **Step 4: Commit and open the T1 PR**

```bash
git add docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md benchmarks/README.md CHANGELOG.md
git commit -m "docs: pr-review family, history-preserving corpus mode, P2.7 gate; G4 hunk qualified_name amendment"
```

Gate: AC-11 (S1), AC-11a (fixture; the release half once O9 is pinned), AC-15, AC-18…AC-21 (scripted), AC-22 (the gate script + README), AC-25 (G4).

---

## Deviations from the spec (recorded, not silent)

| # | Spec says | Plan does | Why |
|---|---|---|---|
| D1 | `pr-review-py` is a published corpus pinned by citation (§7.1, §13 O9) | the loader is fixture-complete and the release path raises `PrReviewCorpusUnpinnedError` until `PR_REVIEW_PY_PIN` is filled from O9 | the corpus choice is an owner decision; a loud refusal is the only honest default |
| D2 | `materialize_corpus_with_history(checkout, *, base_ref, branch_refs, retain_overlay, remove_paths)` (§7.6) | `materialize_corpus_with_history(source_repo, *, base_ref, base, branch_refs, remove_paths, overlay: ConfigOverlaySpec, parent)` cloning from `RepoCache.base_clone(url)`; synthetic branches through `apply_diff_as_commit` | cloning a shared worktree is fragile; the overlay is one typed record rather than four keyword arguments |
| D3 | the overlay carries `retain`, `exclude_dirs`, `decision_capture.sources`, `git.branches.track` | plus `reference_graph.capture.kinds` (doc drift's `mentions`) | §6.4.3 / G11 need it on the same overlay |
| D4 | AC-18…AC-21 run "on the seed head" | the model is scripted (`ScriptedToolCallingLlm`) and the events are written from the fake's recorded calls; the checks score those events | the seed head cannot drive a fake; the tests pin the plumbing and the checks, not model behavior |
| D5 | — | the retrieval track's slice selection rides `task.metadata["search_scope"]` / `["search_branch"]` and an opt-in `HasSearchScope` system Protocol | the sweep's `search(query, limit)` has no scope; a Protocol keeps every other system untouched (the `HasLibraryName` precedent) |
| D6 | — | `ArmCell.dataset_kwargs` (frozen, hashed) | the test-gap arm must name `surface_stage: S1`; the alternative — a second registered dataset name — would double the dataset without doubling the records |
| D7 | — | corpus roots are named after the record id so each record indexes under its own project name | two pull requests of one repository would otherwise collide in a workspace (`select_project` picks the newest) |

## Spec coverage

| AC | Task | AC | Task |
|---|---|---|---|
| AC-11 (S1) | 4 | AC-20 | 7 |
| AC-11a | 3 | AC-21 | 7 |
| AC-15 | 1, 2 | AC-22 | 6 |
| AC-18 | 7 | AC-25 (G4) | 8 |
| AC-19 | 7 | §7.1 arm configs / `weight_by_type` per prefix | 5 |

## Handoff

One PR against `main` after Plan T0 has merged; the S1 / S2a arms run for real once the multi-branch P1 contract PR (the `branch` selector) and the P2 contract PR (the `changed` / `diff` values) have landed. Owner inputs before a paid run: **O9** (the pull-request corpus pin), the `~/pydocs-index/change-review` workspace prewarmed with `benchmarks/tools/prewarm_change_review_workspace.py`, and the budget word (spend stays owner-gated: `--dry-run` walks every arm at $0.00).
