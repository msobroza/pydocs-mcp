# Multi-branch indexing — P0 foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the branch dimension into the bundle for the checked-out branch only — git port and adapters, schema v16, branch record + manifest + membership + extraction cache written on every project pass, project-scoped garbage collection, and `meta.branch` on every response — while every tool's text and `items[]` stay byte-identical.

**Architecture:** A `GitRepository` Protocol in the application layer with a `NullGitRepository` and a `SubprocessGitRepository` adapter under a new `pydocs_mcp/git/` package; four new SQLite tables behind three new store Protocols on the unit of work; the existing `reindex_package` transaction gains one guard clause (removal policy by package origin) and two calls into `application/branch_membership.py`; the freshness probe gains one closure that reads the default branch for the envelope. Today's extraction flow, readers, and header line are unchanged.

**Tech Stack:** Python 3.11+, sqlite3 (FTS5), pydantic v2 / pydantic-settings, watchdog (unchanged), `git` on PATH (optional at runtime), pytest with `asyncio_mode = "auto"`, ruff, mypy, complexipy, vulture.

**Spec:** `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` — P0 implements §6.1 (v16 only), §6.2 (P0 subset of the port), §6.3 steps 1 and 6 for the working-tree branch, §6.7 (`meta.branch` only), §6.13, §6.14, and the P0 row of §10. Read §6.14 before touching any file: it fixes where each piece lives.

## Global Constraints

- Run everything through `uv`: `uv run --no-sync pytest …`, `uv run --no-sync ruff check …`, `uv run --no-sync ruff format …`, `uv run --no-sync mypy python/pydocs_mcp`. Bare `pytest`/`ruff` are not on PATH.
- CI gate for every task's final step: `uv run --no-sync ruff format --check python/ tests/` AND `uv run --no-sync ruff check python/ tests/` (the formatter passing is a separate check from the linter), `uv run --no-sync mypy python/pydocs_mcp`, `uv run --no-sync complexipy python/pydocs_mcp --max-complexity-allowed 15`, `uv run --no-sync vulture python/pydocs_mcp --min-confidence 80`. Coverage floor 90% on `tests/` (`--ignore=tests/test_parity.py`).
- Line length 100 (`[tool.ruff] line-length = 100`). `from __future__ import annotations` at the top of every new module.
- Naming: plain English identifiers; closed vocabularies are `enum.StrEnum` with UPPER_SNAKE members and lowercase string values (the `models.py` precedent, e.g. `PackageOrigin.PROJECT == "project"`); never `Literal` aliases for closed vocabularies. Functions 4–20 lines, at most two indentation levels, files under 500 lines. Error messages carry the offending value and the expected shape.
- Application code depends on Protocols only (`storage/protocols.py`, `application/protocols.py`); never import `Sqlite*` or `subprocess` outside `storage/` and `git/`. Composition roots (`server.py`, `__main__.py`, `storage/factories.py`, and the new `git/factory.py`) are the only places that wire concretes. New services take a `uow_factory: Callable[[], UnitOfWork]`.
- Defaults live in exactly one place: module-level `_DEFAULT_X` constants or pydantic `Field(default=…)`; never repeat a literal.
- The MCP surface is frozen (`docs/tool-contracts.md`): P0 adds exactly one additive envelope field, `meta.branch`, declared on `MetaModel`. No input model changes; `tests/test_mcp_surface_freeze.py` is not edited. The registration golden IS regenerated (the `outputSchema` grows one property).
- Byte identity (spec AC-3): every tool's `text` and `items[]` are identical before and after this plan. The header line does not change in P0.
- Git commits: end the message with nothing extra — this repository does NOT use a Claude co-author trailer (owner rule). Commit after every task; never commit with a failing suite.
- `.git` is a non-removable excluded directory for discovery and stays so; the adapter only ever runs `git -C <root> …` with `GIT_OPTIONAL_LOCKS=0`, a timeout, and no repository writes.
- Model split (owner rule 2026-09-03): implementation subagents run on Opus 5 (`model: opus`); spec and plan edits stay with the main session.

---

## File structure

**Create**

| File | Responsibility |
|---|---|
| `python/pydocs_mcp/git/__init__.py` | package marker, re-exports `GitCommandError` |
| `python/pydocs_mcp/git/errors.py` | `GitCommandError` (concrete exceptions live next to the code that raises them) |
| `python/pydocs_mcp/git/refs.py` | plumbing-file readers moved out of `application/freshness.py` + `resolve_git_branch` |
| `python/pydocs_mcp/git/null_repository.py` | `NullGitRepository` — the Null Object for "no git" |
| `python/pydocs_mcp/git/subprocess_repository.py` | `SubprocessGitRepository` — bounded `git` subprocess adapter |
| `python/pydocs_mcp/git/factory.py` | `git_repository_factory(config)` creator function |
| `python/pydocs_mcp/retrieval/config/git_models.py` | `GitConfig` pydantic sub-model (`git:` YAML section) |
| `python/pydocs_mcp/storage/branch_records.py` | `BranchRecord`, `BranchFile`, `ChunkMembership`, `FileExtraction` value objects |
| `python/pydocs_mcp/storage/sqlite/branch_repository.py` | `SqliteBranchRepository` over `branches` + `branch_files` |
| `python/pydocs_mcp/storage/sqlite/branch_chunk_repository.py` | `SqliteBranchChunkRepository` over `branch_chunks` |
| `python/pydocs_mcp/storage/sqlite/file_extraction_repository.py` | `SqliteFileExtractionRepository` over `file_extractions` |
| `python/pydocs_mcp/application/branch_manifest.py` | `BranchManifest` value object, `BranchManifestBuilder` Protocol, `WorkingTreeManifestBuilder`, `NoBranchManifestBuilder` |
| `python/pydocs_mcp/application/branch_membership.py` | `write_branch_membership`, `write_file_extraction_cache`, `collect_project_garbage` — functions over an open `uow` |
| `python/pydocs_mcp/application/branch_listing.py` | `BranchSummary` + `list_branch_summaries(uow_factory)` for the CLI verb |
| `tests/test_models_branch_vocabulary.py`, `tests/test_git_refs.py`, `tests/test_git_subprocess_repository.py`, `tests/test_git_null_repository.py`, `tests/test_config_git_block.py`, `tests/test_db_schema_v16_migration.py`, `tests/storage/test_branch_repositories.py`, `tests/application/test_branch_manifest.py`, `tests/application/test_branch_membership.py`, `tests/application/test_meta_branch.py`, `tests/test_cli_branches.py`, `tests/integration/test_multi_branch_p0.py` | one test module per task |

**Modify**

| File | Change |
|---|---|
| `python/pydocs_mcp/models.py` | four `StrEnum`s + `NON_GIT_BRANCH_NAME` |
| `python/pydocs_mcp/application/freshness.py` | import the readers from `git/refs.py`; re-export `resolve_git_head`; `EnvelopeInfo.branch`; probe closure `read_default_branch` |
| `python/pydocs_mcp/application/protocols.py` | `GitRepository` Protocol; `ExtractionResult.discovered_paths` |
| `python/pydocs_mcp/extraction/pipeline/chunk_extractor.py` | populate `discovered_paths` |
| `python/pydocs_mcp/db.py` | `SCHEMA_VERSION = 16`, `_apply_v16_additions`, ladder, `_KNOWN_TABLES`, DDL |
| `python/pydocs_mcp/storage/protocols.py` | `BranchStore`, `BranchChunkStore`, `FileExtractionStore`; `ChunkStore.insert_returning_ids` / `delete_unreferenced_project_chunks`; `UnitOfWork` accessors |
| `python/pydocs_mcp/storage/sqlite/chunk_repository.py` | the two new `ChunkStore` methods |
| `python/pydocs_mcp/storage/sqlite/uow.py` | three repositories on the UoW; `delete_all` order |
| `python/pydocs_mcp/storage/composite_uow.py` | `_DISPATCH_ATTRS` |
| `python/pydocs_mcp/storage/sqlite/__init__.py` | export the three repositories |
| `python/pydocs_mcp/application/indexing_service.py` | `ChunkDiffOutcome`; `_diff_merge_chunks` stops deleting; `reindex_package(..., branch_manifest=None)`; `remove_package` cascade |
| `python/pydocs_mcp/application/project_indexer.py` | `manifest_builder` field, builds the manifest for the project package |
| `python/pydocs_mcp/storage/factories.py` | wire `WorkingTreeManifestBuilder`; `build_freshness_probe` closure |
| `python/pydocs_mcp/retrieval/config/app_config.py` | `git: GitConfig` field |
| `python/pydocs_mcp/defaults/default_config.yaml` | `git:` block |
| `python/pydocs_mcp/application/tool_response.py` | `MetaModel.branch` |
| `python/pydocs_mcp/application/envelope.py` | `_assemble_meta` writes `branch` |
| `python/pydocs_mcp/__main__.py` | `branches` subcommand |
| `tests/_fakes.py` | three in-memory stores; `FakeUnitOfWork` accessors; `make_fake_uow_factory` kwargs; `InMemoryChunkStore` new methods |
| `tests/fixtures/goldens/mcp_registration_surface.json` | regenerated |
| `tests/test_db_schema_v15_migration.py` | version literal → `SCHEMA_VERSION` |
| `docs/tool-contracts.md` | §2.4 `meta.branch` + §6 row 8 (gated on owner ratification, see Task 8) |
| `CHANGELOG.md`, `DOCUMENTATION.md` | entries |

---

### Task 1: Branch vocabulary, records, and the git error type

**Files:**
- Modify: `python/pydocs_mcp/models.py` (after `PROJECT_PACKAGE_NAME`, and after `class SearchScope`)
- Create: `python/pydocs_mcp/storage/branch_records.py`
- Create: `python/pydocs_mcp/git/__init__.py`, `python/pydocs_mcp/git/errors.py`
- Test: `tests/test_models_branch_vocabulary.py`

**Interfaces:**
- Produces: `NON_GIT_BRANCH_NAME: str`; enums `BranchStatus`, `BranchIndexSource`, `BranchSlice`, `FileChangeKind`; dataclasses `BranchRecord`, `BranchFile`, `ChunkMembership`, `FileExtraction`; `GitCommandError(argv, reason, stderr_tail="")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_branch_vocabulary.py
"""Branch-dimension vocabulary + records (spec §6.1, §6.14 item 4)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import (
    NON_GIT_BRANCH_NAME,
    BranchIndexSource,
    BranchSlice,
    BranchStatus,
    FileChangeKind,
)
from pydocs_mcp.storage.branch_records import (
    BranchFile,
    BranchRecord,
    ChunkMembership,
    FileExtraction,
)


def test_branch_vocabularies_are_str_enums_with_lowercase_values() -> None:
    assert BranchStatus.ACTIVE == "active"
    assert {s.value for s in BranchStatus} == {"active", "inactive", "merged", "deleted"}
    assert {s.value for s in BranchIndexSource} == {"working_tree", "git_objects"}
    assert {s.value for s in BranchSlice} == {"tree", "diff"}
    assert {k.value for k in FileChangeKind} == {
        "unchanged", "added", "modified", "renamed", "deleted",
    }


def test_non_git_sentinel_cannot_be_a_git_ref_name() -> None:
    # git check-ref-format forbids spaces, so no real branch can collide.
    assert " " in NON_GIT_BRANCH_NAME


def test_records_are_frozen_and_carry_defaults() -> None:
    rec = BranchRecord(
        name="main", head_sha="abc1234", source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p", indexed_at=1.0, last_used_at=1.0,
    )
    assert rec.status is BranchStatus.ACTIVE and rec.is_default is False
    with pytest.raises(FrozenInstanceError):
        rec.name = "other"  # type: ignore[misc]
    bf = BranchFile(branch="main", path="pkg/a.py", blob_sha="b1")
    assert bf.change_kind is FileChangeKind.UNCHANGED
    cm = ChunkMembership(branch="main", chunk_id=7, source_path="pkg/a.py")
    assert cm.slice is BranchSlice.TREE and cm.changed is False
    fe = FileExtraction(blob_sha="b1", path="pkg/a.py", pipeline_hash="p",
                        chunk_spans="[[7, 1, 3]]", created_at=1.0)
    assert fe.tree_json is None


def test_git_command_error_carries_argv_reason_and_stderr() -> None:
    err = GitCommandError(("git", "-C", "/p", "status"), "timeout after 30s", "fatal: x")
    assert isinstance(err, PydocsMCPError) and isinstance(err, RuntimeError)
    assert "status" in str(err) and "timeout after 30s" in str(err) and "fatal: x" in str(err)
    assert err.argv == ("git", "-C", "/p", "status")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest tests/test_models_branch_vocabulary.py -q`
Expected: FAIL — `ImportError: cannot import name 'NON_GIT_BRANCH_NAME'` (and `No module named 'pydocs_mcp.git'`).

- [ ] **Step 3: Add the vocabulary to `models.py`**

Right after the `PROJECT_PACKAGE_NAME = "__project__"` block:

```python
# Branch dimension (spec §6.1): non-git projects still get exactly one branch
# row so membership and the project-scoped GC behave uniformly. A space is
# illegal in a git ref name (git check-ref-format), so this sentinel can never
# collide with a real branch; the envelope renders it as ``meta.branch = null``.
NON_GIT_BRANCH_NAME = "no git"
```

Right after `class SearchScope(StrEnum): …`:

```python
class BranchStatus(StrEnum):
    """Lifecycle of one indexed branch (spec §6.8a). Soft state on the record."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    MERGED = "merged"
    DELETED = "deleted"


class BranchIndexSource(StrEnum):
    """Where a branch's files were read from when it was indexed (spec §6.3)."""

    WORKING_TREE = "working_tree"
    GIT_OBJECTS = "git_objects"


class BranchSlice(StrEnum):
    """Which slice a membership row belongs to: whole-symbol chunks or diff hunks."""

    TREE = "tree"
    DIFF = "diff"


class FileChangeKind(StrEnum):
    """How a manifest entry differs from the branch's base (spec §6.5)."""

    UNCHANGED = "unchanged"
    ADDED = "added"
    MODIFIED = "modified"
    RENAMED = "renamed"
    DELETED = "deleted"
```

- [ ] **Step 4: Create `storage/branch_records.py`**

```python
"""Branch-dimension value objects (spec §6.1): rows of ``branches``,
``branch_files``, ``branch_chunks`` and ``file_extractions``.

Immutable, like :class:`~pydocs_mcp.storage.node_reference.NodeReference`;
the SQLite repositories map them 1:1 onto their tables.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import BranchIndexSource, BranchSlice, BranchStatus, FileChangeKind


@dataclass(frozen=True, slots=True)
class BranchRecord:
    """One row of ``branches`` — identity, lifecycle, and freshness of a branch."""

    name: str
    head_sha: str
    source: BranchIndexSource
    pipeline_hash: str
    indexed_at: float
    last_used_at: float
    is_default: bool = False
    base_name: str | None = None
    merge_base_sha: str | None = None
    worktree_path: str | None = None
    status: BranchStatus = BranchStatus.ACTIVE
    merged_into: str | None = None
    retired_at: float | None = None
    purge_after: float | None = None
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class BranchFile:
    """One row of ``branch_files`` — the manifest entry for one project-relative path."""

    branch: str
    path: str
    blob_sha: str
    change_kind: FileChangeKind = FileChangeKind.UNCHANGED


@dataclass(frozen=True, slots=True)
class ChunkMembership:
    """One row of ``branch_chunks`` — a chunk's membership in a branch plus the
    per-branch span (spans live on membership, not on the shared chunk row)."""

    branch: str
    chunk_id: int
    source_path: str
    start_line: int | None = None
    end_line: int | None = None
    changed: bool = False
    slice: BranchSlice = BranchSlice.TREE


@dataclass(frozen=True, slots=True)
class FileExtraction:
    """One row of ``file_extractions`` — the blob-keyed extraction cache.

    ``chunk_spans`` is JSON ``[[chunk_id, start_line, end_line], ...]`` in file
    order. The tree / members / references columns stay ``None`` until P1
    populates and consumes them.
    """

    blob_sha: str
    path: str
    pipeline_hash: str
    chunk_spans: str
    created_at: float
    tree_json: str | None = None
    members_json: str | None = None
    references_json: str | None = None
```

- [ ] **Step 5: Create the `git` package with its error type**

`python/pydocs_mcp/git/errors.py`:

```python
"""The git adapter's error type — raised at the subprocess boundary only."""

from __future__ import annotations

from pydocs_mcp.exceptions import PydocsMCPError


class GitCommandError(PydocsMCPError, RuntimeError):
    """A ``git`` subprocess failed, timed out, or could not start.

    Raised only inside ``pydocs_mcp.git``; application code sees this type,
    never ``subprocess`` errors (spec §6.14 item 7). ``argv`` is the exact
    command, ``reason`` the failure class ("timeout after 30s", "exit 128",
    "binary not found"), ``stderr_tail`` the last lines git printed.
    """

    def __init__(self, argv: tuple[str, ...], reason: str, stderr_tail: str = "") -> None:
        self.argv = argv
        self.reason = reason
        self.stderr_tail = stderr_tail
        detail = f": {stderr_tail}" if stderr_tail else ""
        super().__init__(f"git command {' '.join(argv)!r} failed ({reason}){detail}")
```

`python/pydocs_mcp/git/__init__.py`:

```python
"""Git adapters (spec §6.2): plumbing readers and the subprocess repository.

Only this package may import ``subprocess``; everything else reaches git
through the :class:`~pydocs_mcp.application.protocols.GitRepository` Protocol.
"""

from pydocs_mcp.git.errors import GitCommandError

__all__ = ("GitCommandError",)
```

- [ ] **Step 6: Run the test and the gate**

Run: `uv run --no-sync pytest tests/test_models_branch_vocabulary.py tests/test_models.py tests/test_models_constants.py -q`
Expected: PASS.
Run: `uv run --no-sync ruff format python/pydocs_mcp/models.py python/pydocs_mcp/storage/branch_records.py python/pydocs_mcp/git tests/test_models_branch_vocabulary.py && uv run --no-sync ruff check python/ tests/ && uv run --no-sync mypy python/pydocs_mcp`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/models.py python/pydocs_mcp/storage/branch_records.py python/pydocs_mcp/git tests/test_models_branch_vocabulary.py
git commit -m "feat(branches): branch vocabulary enums, branch records, GitCommandError"
```

---

### Task 2: Git plumbing readers move to `git/refs.py`, plus `resolve_git_branch`

**Files:**
- Create: `python/pydocs_mcp/git/refs.py`
- Modify: `python/pydocs_mcp/application/freshness.py` (replace the five reader functions with imports; keep the public name)
- Test: `tests/test_git_refs.py`

**Interfaces:**
- Produces (all sync, no subprocess): `locate_gitdir(project_root) -> Path | None`, `refs_home(gitdir) -> Path`, `resolve_ref(gitdir, ref) -> str | None`, `resolve_git_head(project_root) -> str | None` (unchanged semantics), `resolve_git_branch(project_root) -> str | None` (short name under `refs/heads/`, `None` when detached or unresolvable).
- `pydocs_mcp.application.freshness.resolve_git_head` keeps working as an import path (`application/index_project.py` and tests use it).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_git_refs.py
"""Plumbing-file readers (spec §6.2): no subprocess, worktree-aware, degrade to None."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.git.refs import locate_gitdir, resolve_git_branch, resolve_git_head

_SHA = "8783c8c1111111111111111111111111111111aa"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo_with_loose_ref(root: Path, branch: str = "feature/x") -> None:
    _write(root / ".git" / "HEAD", f"ref: refs/heads/{branch}\n")
    _write(root / ".git" / "refs" / "heads" / branch, _SHA + "\n")


def test_resolve_git_branch_reads_symbolic_head(tmp_path: Path) -> None:
    _repo_with_loose_ref(tmp_path)
    assert resolve_git_branch(tmp_path) == "feature/x"
    assert resolve_git_head(tmp_path) == _SHA


def test_resolve_git_branch_is_none_when_detached(tmp_path: Path) -> None:
    _write(tmp_path / ".git" / "HEAD", _SHA + "\n")
    assert resolve_git_branch(tmp_path) is None
    assert resolve_git_head(tmp_path) == _SHA


def test_resolve_git_branch_is_none_without_repository(tmp_path: Path) -> None:
    assert resolve_git_branch(tmp_path) is None
    assert locate_gitdir(tmp_path) is None


def test_packed_ref_resolves_head(tmp_path: Path) -> None:
    _write(tmp_path / ".git" / "HEAD", "ref: refs/heads/main\n")
    _write(tmp_path / ".git" / "packed-refs", f"# pack-refs\n{_SHA} refs/heads/main\n")
    assert resolve_git_head(tmp_path) == _SHA
    assert resolve_git_branch(tmp_path) == "main"


def test_worktree_gitfile_delegates_refs_to_commondir(tmp_path: Path) -> None:
    main = tmp_path / "main"
    _repo_with_loose_ref(main, "main")
    wt = tmp_path / "wt"
    wt_gitdir = main / ".git" / "worktrees" / "wt"
    _write(wt / ".git", f"gitdir: {wt_gitdir}\n")
    _write(wt_gitdir / "HEAD", "ref: refs/heads/feature/y\n")
    _write(wt_gitdir / "commondir", "../..\n")
    _write(main / ".git" / "refs" / "heads" / "feature" / "y", _SHA + "\n")
    assert resolve_git_branch(wt) == "feature/y"
    assert resolve_git_head(wt) == _SHA


def test_freshness_module_still_exports_resolve_git_head() -> None:
    from pydocs_mcp.application.freshness import resolve_git_head as via_freshness

    assert via_freshness is resolve_git_head
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest tests/test_git_refs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.git.refs'`.

- [ ] **Step 3: Create `git/refs.py`** (the bodies of `_read_packed_refs`, `_locate_gitdir`, `_refs_home`, `_resolve_ref`, `resolve_git_head` are moved verbatim from `application/freshness.py`, renamed without the underscore; only `resolve_git_branch` is new)

```python
"""Git plumbing-file readers (spec §6.2) — no subprocess, safe on the request path.

Moved here from ``application/freshness.py`` so the git package owns every
git-format concern. Handles a ``.git`` directory, a worktree gitfile
(``gitdir:`` pointer + ``commondir`` delegation), loose refs, ``packed-refs``,
and detached HEAD. Any I/O error or unrecognized layout degrades to ``None``.
"""

from __future__ import annotations

from pathlib import Path

_HEADS_PREFIX = "refs/heads/"


def read_packed_refs(packed: Path, ref: str) -> str | None:
    for line in packed.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # '#' = header, '^' = peeled-tag annotation for the line above.
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if name == ref:
            return sha
    return None


def locate_gitdir(project_root: Path) -> Path | None:
    """Resolve ``.git`` to a gitdir — a directory, or a worktree gitfile pointer."""
    git = project_root / ".git"
    if git.is_dir():
        return git
    if not git.is_file():
        return None
    content = git.read_text(encoding="utf-8").strip()
    if not content.startswith("gitdir:"):
        return None
    gitdir = Path(content.split(":", 1)[1].strip())
    return gitdir if gitdir.is_absolute() else (project_root / gitdir).resolve()


def refs_home(gitdir: Path) -> Path:
    """Worktree gitdirs keep only HEAD locally; refs live under ``commondir``."""
    commondir_file = gitdir / "commondir"
    if not commondir_file.is_file():
        return gitdir
    common = Path(commondir_file.read_text(encoding="utf-8").strip())
    return common if common.is_absolute() else (gitdir / common).resolve()


def resolve_ref(gitdir: Path, ref: str) -> str | None:
    """Loose file first, then the refs home, then ``packed-refs``."""
    for candidate in (gitdir / ref, refs_home(gitdir) / ref):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip() or None
    packed = refs_home(gitdir) / "packed-refs"
    if packed.is_file():
        return read_packed_refs(packed, ref)
    return None


def _read_head(project_root: Path) -> str | None:
    """The raw ``HEAD`` line, or ``None`` for a non-repo / unreadable layout."""
    try:
        gitdir = locate_gitdir(project_root)
        if gitdir is None:
            return None
        return (gitdir / "HEAD").read_text(encoding="utf-8").strip() or None
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError on a corrupted plumbing file.
        return None


def resolve_git_head(project_root: Path) -> str | None:
    """Commit sha ``HEAD`` points at, or ``None`` when unresolvable."""
    head = _read_head(project_root)
    if head is None:
        return None
    if not head.startswith("ref:"):
        return head  # detached HEAD stores the raw sha
    try:
        gitdir = locate_gitdir(project_root)
        return resolve_ref(gitdir, head.split(":", 1)[1].strip()) if gitdir else None
    except (OSError, ValueError):
        return None


def resolve_git_branch(project_root: Path) -> str | None:
    """Short branch name ``HEAD`` points at; ``None`` when detached or unresolvable."""
    head = _read_head(project_root)
    if head is None or not head.startswith("ref:"):
        return None
    ref = head.split(":", 1)[1].strip()
    return ref[len(_HEADS_PREFIX) :] if ref.startswith(_HEADS_PREFIX) else ref


__all__ = (
    "locate_gitdir",
    "read_packed_refs",
    "refs_home",
    "resolve_git_branch",
    "resolve_git_head",
    "resolve_ref",
)
```

- [ ] **Step 4: Slim `application/freshness.py`**

Delete `_read_packed_refs`, `_locate_gitdir`, `_refs_home`, `_resolve_ref`, and `resolve_git_head` from the module. Replace them with one import right after the existing `from pydocs_mcp.storage.index_metadata import IndexMetadata` line, and add an `__all__` at the bottom (vulture would otherwise flag the re-export as an unused import):

```python
from pydocs_mcp.git.refs import resolve_git_head
```

```python
__all__ = ("EnvelopeInfo", "IndexFreshnessProbe", "resolve_git_head")
```

Update the module docstring's first paragraph to: `resolve_git_head` lives in `pydocs_mcp.git.refs` and is re-exported here for the existing import path. Then check nothing else imported the underscore names:

Run: `grep -rn "_locate_gitdir\|_refs_home\|_resolve_ref\|_read_packed_refs" python/ tests/`
Expected: no output. (If a test does import one, change that import to the public name in `pydocs_mcp.git.refs`.)

- [ ] **Step 5: Run the tests and the gate**

Run: `uv run --no-sync pytest tests/test_git_refs.py tests/application -q -k "freshness or envelope or index_project"`
Expected: PASS.
Run: `uv run --no-sync ruff format python/pydocs_mcp/git/refs.py python/pydocs_mcp/application/freshness.py tests/test_git_refs.py && uv run --no-sync ruff check python/ tests/ && uv run --no-sync mypy python/pydocs_mcp && uv run --no-sync vulture python/pydocs_mcp --min-confidence 80`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/git/refs.py python/pydocs_mcp/application/freshness.py tests/test_git_refs.py
git commit -m "refactor(git): move plumbing readers to git/refs.py, add resolve_git_branch"
```

---

### Task 3: The `GitRepository` port, its two adapters, the `git:` config block, and the creator function

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py` (append the Protocol)
- Create: `python/pydocs_mcp/git/null_repository.py`, `python/pydocs_mcp/git/subprocess_repository.py`, `python/pydocs_mcp/git/factory.py`
- Create: `python/pydocs_mcp/retrieval/config/git_models.py`
- Modify: `python/pydocs_mcp/retrieval/config/app_config.py` (import + one field), `python/pydocs_mcp/defaults/default_config.yaml` (append a `git:` block after the `serve:` block)
- Test: `tests/test_git_null_repository.py`, `tests/test_git_subprocess_repository.py`, `tests/test_config_git_block.py`

**Interfaces:**
- Produces: `GitRepository` Protocol with `current_branch() -> str | None`, `head_sha() -> str | None`, `index_manifest() -> tuple[tuple[str, str], ...]`, `hash_objects(paths: Sequence[str]) -> tuple[tuple[str, str], ...]`, `working_tree_changes() -> tuple[tuple[str, FileChangeKind], ...]`, `list_worktrees() -> tuple[tuple[str, str | None], ...]` (path, branch). All paths are project-relative POSIX strings except worktree paths, which are absolute.
- Produces: `NullGitRepository()`; `SubprocessGitRepository(project_root, binary="git", timeout_seconds=30.0)`; `git_repository_factory(config: GitConfig) -> Callable[[Path], GitRepository]`; `GitConfig` with `enabled: GitEnablement = AUTO`, `binary: str = "git"`, `timeout_seconds: float = 30.0`; `AppConfig.git`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_git_null_repository.py
"""NullGitRepository — the Null Object for projects without git (spec §6.11)."""

from __future__ import annotations

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.null_repository import NullGitRepository


def test_null_repository_conforms_and_answers_empty() -> None:
    repo = NullGitRepository()
    assert isinstance(repo, GitRepository)
    assert repo.current_branch() is None
    assert repo.head_sha() is None
    assert repo.index_manifest() == ()
    assert repo.hash_objects(["a.py"]) == ()
    assert repo.working_tree_changes() == ()
    assert repo.list_worktrees() == ()
```

```python
# tests/test_git_subprocess_repository.py
"""SubprocessGitRepository against a real repository (skipped without ``git``)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.models import FileChangeKind

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _git(root: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x", "HOME": str(root), "PATH": "/usr/bin:/bin"}
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, env=env).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_conforms_and_reads_branch_and_head(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    assert isinstance(git, GitRepository)
    assert git.current_branch() == "main"
    head = git.head_sha()
    assert head is not None and len(head) == 40


def test_index_manifest_lists_tracked_files_with_blob_ids(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    manifest = dict(git.index_manifest())
    assert set(manifest) == {"pkg/a.py"}
    assert len(manifest["pkg/a.py"]) == 40


def test_hash_objects_matches_git_for_untracked_file(repo: Path) -> None:
    (repo / "pkg" / "b.py").write_text("x = 1\n", encoding="utf-8")
    git = SubprocessGitRepository(project_root=repo)
    (path, blob), = git.hash_objects(["pkg/b.py"])
    assert path == "pkg/b.py"
    assert blob == _git(repo, "hash-object", "pkg/b.py").strip()


def test_working_tree_changes_reports_modified_and_untracked(repo: Path) -> None:
    (repo / "pkg" / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    (repo / "pkg" / "b.py").write_text("x = 1\n", encoding="utf-8")
    git = SubprocessGitRepository(project_root=repo)
    changes = dict(git.working_tree_changes())
    assert changes == {"pkg/a.py": FileChangeKind.MODIFIED, "pkg/b.py": FileChangeKind.ADDED}


def test_list_worktrees_includes_the_main_checkout(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    worktrees = git.list_worktrees()
    assert (str(repo.resolve()), "main") in worktrees


def test_failures_become_git_command_error(tmp_path: Path) -> None:
    git = SubprocessGitRepository(project_root=tmp_path)  # not a repository
    with pytest.raises(GitCommandError) as info:
        git.index_manifest()
    assert "ls-files" in str(info.value)


def test_missing_binary_becomes_git_command_error(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo, binary="/nonexistent/git")
    with pytest.raises(GitCommandError) as info:
        git.head_sha()
    assert "binary not found" in str(info.value)
```

```python
# tests/test_config_git_block.py
"""The ``git:`` AppConfig section (spec §6.9) and the creator function (§6.14 item 1)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from pydocs_mcp.git.factory import git_repository_factory
from pydocs_mcp.git.null_repository import NullGitRepository
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.git_models import GitConfig, GitEnablement


def test_defaults_are_auto_git_and_thirty_seconds() -> None:
    cfg = AppConfig.load().git
    assert cfg.enabled is GitEnablement.AUTO
    assert cfg.binary == "git"
    assert cfg.timeout_seconds == 30.0


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GitConfig(enabled="auto", binry="git")  # type: ignore[call-arg]


def test_factory_returns_null_when_disabled(tmp_path: Path) -> None:
    build = git_repository_factory(GitConfig(enabled=GitEnablement.OFF))
    assert isinstance(build(tmp_path), NullGitRepository)


def test_factory_returns_null_when_not_a_repository(tmp_path: Path) -> None:
    build = git_repository_factory(GitConfig())
    assert isinstance(build(tmp_path), NullGitRepository)


@pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")
def test_factory_returns_subprocess_adapter_for_a_repository(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    build = git_repository_factory(GitConfig())
    assert isinstance(build(tmp_path), SubprocessGitRepository)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --no-sync pytest tests/test_git_null_repository.py tests/test_git_subprocess_repository.py tests/test_config_git_block.py -q`
Expected: FAIL — `ImportError: cannot import name 'GitRepository'`.

- [ ] **Step 3: Add the Protocol to `application/protocols.py`**

Add `Sequence` to the `collections.abc` import and `FileChangeKind` to the `pydocs_mcp.models` import, then append at the end of the module:

```python
@runtime_checkable
class GitRepository(Protocol):
    """The git port (spec §6.2, P0 subset). Adapters live in ``pydocs_mcp.git``.

    Every path is project-relative POSIX (``pkg/a.py``) except worktree paths,
    which are absolute. Read-only: no method writes to the repository. Adapters
    raise :class:`~pydocs_mcp.git.errors.GitCommandError` on failure; the Null
    adapter answers empty / ``None`` and never raises.
    """

    def current_branch(self) -> str | None: ...

    def head_sha(self) -> str | None: ...

    def index_manifest(self) -> tuple[tuple[str, str], ...]:
        """``(path, blob_sha)`` for every tracked file, from git's own index."""
        ...

    def hash_objects(self, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
        """``(path, blob_sha)`` computed from the working-tree bytes of ``paths``."""
        ...

    def working_tree_changes(self) -> tuple[tuple[str, FileChangeKind], ...]:
        """Modified / added(untracked) / deleted paths versus the index."""
        ...

    def list_worktrees(self) -> tuple[tuple[str, str | None], ...]:
        """``(absolute_path, branch_or_None)`` for every worktree of the repository."""
        ...
```

- [ ] **Step 4: Create `git/null_repository.py`**

```python
"""NullGitRepository — the Null Object wired when git or the repository is absent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import FileChangeKind


@dataclass(frozen=True, slots=True)
class NullGitRepository:
    """Answers "nothing here" for every query and never raises (spec §6.11)."""

    def current_branch(self) -> str | None:
        return None

    def head_sha(self) -> str | None:
        return None

    def index_manifest(self) -> tuple[tuple[str, str], ...]:
        return ()

    def hash_objects(self, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
        return ()

    def working_tree_changes(self) -> tuple[tuple[str, FileChangeKind], ...]:
        return ()

    def list_worktrees(self) -> tuple[tuple[str, str | None], ...]:
        return ()
```

- [ ] **Step 5: Create `git/subprocess_repository.py`**

```python
"""SubprocessGitRepository — bounded, read-only ``git`` subprocess adapter (spec §6.2).

Every call is ``git -C <root> …`` with a timeout, ``GIT_OPTIONAL_LOCKS=0`` (no
``index.lock`` writes from status-like commands) and ``GIT_TERMINAL_PROMPT=0``
(never block on a credential prompt). Failures are translated to
:class:`GitCommandError` at this boundary (spec §6.14 item 7).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import FileChangeKind

_DEFAULT_TIMEOUT_SECONDS = 30.0
_STDERR_TAIL_CHARS = 400
# Porcelain v1 status codes → manifest change kind. Anything else (renames in
# the index, conflicts) reads as MODIFIED: the file's bytes must be re-hashed.
_STATUS_KINDS = {"??": FileChangeKind.ADDED, " D": FileChangeKind.DELETED,
                 "D ": FileChangeKind.DELETED}


@dataclass(frozen=True, slots=True)
class SubprocessGitRepository:
    project_root: Path
    binary: str = "git"
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def current_branch(self) -> str | None:
        out = self._run("symbolic-ref", "--quiet", "--short", "HEAD", allow_exit={1})
        return out.strip() or None

    def head_sha(self) -> str | None:
        out = self._run("rev-parse", "--verify", "--quiet", "HEAD", allow_exit={1})
        return out.strip() or None

    def index_manifest(self) -> tuple[tuple[str, str], ...]:
        # ``ls-files --stage``: "<mode> <blob> <stage>\t<path>" — git's own stat
        # cache answers without reading file bytes (spec §6.3 step 1).
        out = self._run("ls-files", "--stage", "-z")
        rows = []
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            rows.append((path, meta.split()[1]))
        return tuple(rows)

    def hash_objects(self, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
        if not paths:
            return ()
        out = self._run("hash-object", "--stdin-paths", stdin="\n".join(paths) + "\n")
        shas = out.split()
        if len(shas) != len(paths):
            raise GitCommandError(
                self._argv("hash-object", "--stdin-paths"),
                f"expected {len(paths)} blob ids, got {len(shas)}",
            )
        return tuple(zip(paths, shas, strict=True))

    def working_tree_changes(self) -> tuple[tuple[str, FileChangeKind], ...]:
        out = self._run("status", "--porcelain=v1", "-z", "--untracked-files=all",
                        "--no-renames")
        rows = []
        for entry in out.split("\0"):
            if len(entry) < 4:
                continue
            code, path = entry[:2], entry[3:]
            rows.append((path, _STATUS_KINDS.get(code, FileChangeKind.MODIFIED)))
        return tuple(rows)

    def list_worktrees(self) -> tuple[tuple[str, str | None], ...]:
        out = self._run("worktree", "list", "--porcelain")
        rows: list[tuple[str, str | None]] = []
        path: str | None = None
        for line in out.splitlines() + [""]:
            if line.startswith("worktree "):
                path = line[len("worktree ") :]
            elif line.startswith("branch ") and path is not None:
                rows.append((path, line[len("branch refs/heads/") :]))
                path = None
            elif line == "" and path is not None:
                rows.append((path, None))  # detached worktree
                path = None
        return tuple(rows)

    def _argv(self, *args: str) -> tuple[str, ...]:
        return (self.binary, "-C", str(self.project_root), *args)

    def _run(self, *args: str, stdin: str | None = None, allow_exit: set[int] = frozenset()) -> str:
        argv = self._argv(*args)
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
        try:
            proc = subprocess.run(  # noqa: S603 — argv is built from config + literals only
                argv, input=stdin, capture_output=True, text=True,
                timeout=self.timeout_seconds, env=env, check=False,
            )
        except FileNotFoundError as exc:
            raise GitCommandError(argv, "binary not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(argv, f"timeout after {self.timeout_seconds:g}s") from exc
        except OSError as exc:
            raise GitCommandError(argv, f"could not start: {exc}") from exc
        if proc.returncode != 0 and proc.returncode not in allow_exit:
            tail = proc.stderr.strip()[-_STDERR_TAIL_CHARS:]
            raise GitCommandError(argv, f"exit {proc.returncode}", tail)
        return proc.stdout
```

The `# noqa: S603` comment is required by the repository's lint config for every `subprocess.run` (the `extraction/decisions/_git.py` precedent).

- [ ] **Step 6: Create the config sub-model and mount it**

`python/pydocs_mcp/retrieval/config/git_models.py`:

```python
"""``git:`` configuration (spec §6.9, P0 subset: enablement, binary, timeout).

Deployment knobs, never MCP tool params (CLAUDE.md §"MCP API surface vs YAML
configuration"). P1 adds ``branches`` / ``ref_watch`` / ``remote`` here.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_GIT_BINARY = "git"
_DEFAULT_GIT_TIMEOUT_SECONDS = 30.0


class GitEnablement(StrEnum):
    """``auto``: on when a git binary and a repository are found; ``on`` / ``off``."""

    AUTO = "auto"
    ON = "on"
    OFF = "off"


class GitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: GitEnablement = GitEnablement.AUTO
    binary: str = Field(default=_DEFAULT_GIT_BINARY, min_length=1)
    timeout_seconds: float = Field(default=_DEFAULT_GIT_TIMEOUT_SECONDS, gt=0)
```

In `app_config.py`, add the import next to the other config imports and the field after `files: FilesConfig = …`:

```python
from pydocs_mcp.retrieval.config.git_models import GitConfig
```

```python
    # Git integration (spec §6.2/§6.9): enablement, binary, timeout. Per
    # CLAUDE.md §"MCP API surface vs YAML configuration": deployment knobs,
    # NOT MCP tool params — the nine task-shaped tools stay fixed.
    git: GitConfig = Field(default_factory=GitConfig)
```

In `defaults/default_config.yaml`, append right after the `serve:` block (before the `# Phase 2 trace capture` comment):

```yaml
# Git integration (spec 2026-09-03 multi-branch §6.9). `auto` turns the git
# port on when a `git` binary and a repository are found; `off` keeps today's
# git-free behavior. Read-only: the product never writes to the repository.
git:
  enabled: auto            # auto | on | off
  binary: git
  timeout_seconds: 30
```

- [ ] **Step 7: Create `git/factory.py`**

```python
"""Creator function for the git port (spec §6.14 item 1: creation separated from use)."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.null_repository import NullGitRepository
from pydocs_mcp.git.refs import locate_gitdir
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.retrieval.config.git_models import GitConfig, GitEnablement

log = logging.getLogger("pydocs-mcp")


def git_repository_factory(config: GitConfig) -> Callable[[Path], GitRepository]:
    """Bind the config once; the returned callable picks the adapter per project root."""

    def _build(project_root: Path) -> GitRepository:
        if config.enabled is GitEnablement.OFF:
            return NullGitRepository()
        missing = _unavailable_reason(config, project_root)
        if missing is None:
            return SubprocessGitRepository(
                project_root=project_root,
                binary=config.binary,
                timeout_seconds=config.timeout_seconds,
            )
        level = logging.WARNING if config.enabled is GitEnablement.ON else logging.INFO
        log.log(level, '{"event": "git_unavailable", "reason": "%s", "root": "%s"}',
                missing, project_root)
        return NullGitRepository()

    return _build


def _unavailable_reason(config: GitConfig, project_root: Path) -> str | None:
    if shutil.which(config.binary) is None:
        return f"binary {config.binary!r} not on PATH"
    if locate_gitdir(project_root) is None:
        return "not a git repository"
    return None
```

- [ ] **Step 8: Run the tests and the gate**

Run: `uv run --no-sync pytest tests/test_git_null_repository.py tests/test_git_subprocess_repository.py tests/test_config_git_block.py tests/test_config_pipeline_hash.py -q`
Expected: PASS (the pipeline-hash test proves `ingestion_pipeline_hash` is unchanged by the new section).
Run: `uv run --no-sync ruff format python/pydocs_mcp/git python/pydocs_mcp/retrieval/config python/pydocs_mcp/application/protocols.py tests/test_git_*.py tests/test_config_git_block.py && uv run --no-sync ruff check python/ tests/ && uv run --no-sync mypy python/pydocs_mcp && uv run --no-sync complexipy python/pydocs_mcp --max-complexity-allowed 15`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add python/pydocs_mcp/git python/pydocs_mcp/application/protocols.py python/pydocs_mcp/retrieval/config/git_models.py python/pydocs_mcp/retrieval/config/app_config.py python/pydocs_mcp/defaults/default_config.yaml tests/test_git_null_repository.py tests/test_git_subprocess_repository.py tests/test_config_git_block.py
git commit -m "feat(git): GitRepository port, Null + subprocess adapters, git: config block"
```

---

### Task 4: Schema v16 — four tables, the chunk hash index, and the migration

**Files:**
- Modify: `python/pydocs_mcp/db.py` (`SCHEMA_VERSION`, `_DDL`, `_KNOWN_TABLES`, `_apply_v16_additions`, `_migrate_in_place`, `open_index_database` docstring)
- Modify: `tests/test_db_schema_v15_migration.py` (version literals)
- Test: `tests/test_db_schema_v16_migration.py`

**Interfaces:**
- Produces: tables `branches`, `branch_files`, `branch_chunks`, `file_extractions` exactly as in the DDL below; index `ix_chunks_content_hash`; `SCHEMA_VERSION == 16`. The v15 → v16 step sets `packages.content_hash = NULL` for `__project__` only.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_schema_v16_migration.py
"""v16 migration — the branch dimension's tables (spec §6.1, P0).

Mirrors test_db_schema_v15_migration.py: build a v15 db on disk, reopen through
open_index_database, assert the four tables + the chunk hash index exist, that
rows survive, and that ONLY the project package's content_hash was cleared
(forcing one re-extraction that populates the new tables; chunk hashes are
unchanged so nothing re-embeds).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database

_NEW_TABLES = {"branches", "branch_files", "branch_chunks", "file_extractions"}

_V15_SCRIPT = """
    CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT, embedding_model TEXT);
    CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
        module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
        content_hash TEXT, qualified_name TEXT,
        embedded INTEGER NOT NULL DEFAULT 0, decision_id INTEGER,
        source_path TEXT, start_line INTEGER, end_line INTEGER);
    CREATE VIRTUAL TABLE chunks_fts USING fts5(title, text, package,
        content=chunks, content_rowid=id, tokenize='porter unicode61');
    CREATE TABLE module_members (id INTEGER PRIMARY KEY, package TEXT,
        module TEXT, name TEXT, kind TEXT, signature TEXT,
        return_annotation TEXT, parameters TEXT, docstring TEXT);
    CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1),
        project_name TEXT, project_root TEXT, embedding_provider TEXT,
        embedding_model TEXT, embedding_dim INTEGER,
        pipeline_hash TEXT, indexed_at REAL, git_head TEXT,
        activity_summary TEXT, overview_summary TEXT);
    CREATE TABLE decision_records (id INTEGER PRIMARY KEY, package TEXT NOT NULL,
        title TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL,
        confidence REAL NOT NULL, evidence TEXT NOT NULL,
        affected_files TEXT NOT NULL, affected_qnames TEXT NOT NULL,
        staleness_score REAL NOT NULL DEFAULT 0.0, superseded_by INTEGER,
        verification TEXT NOT NULL DEFAULT 'verbatim', structured TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL);
    INSERT INTO packages (name, content_hash, origin) VALUES ('__project__', 'h1', 'project');
    INSERT INTO packages (name, content_hash, origin) VALUES ('requests', 'h2', 'dependency');
    INSERT INTO chunks (package, title, text, content_hash, embedded)
        VALUES ('__project__', 't', 'body', 'c1', 1);
    PRAGMA user_version = 15;
"""


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def _v15_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_V15_SCRIPT)
    conn.commit()
    conn.close()


def test_schema_version_is_16() -> None:
    assert SCHEMA_VERSION == 16


def test_fresh_db_has_branch_tables_and_hash_index(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert _NEW_TABLES <= _tables(conn)
        assert "ix_chunks_content_hash" in _indexes(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_v15_db_upgrades_in_place_and_clears_only_the_project_hash(tmp_path: Path) -> None:
    db = tmp_path / "v15.db"
    _v15_db(db)
    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert _NEW_TABLES <= _tables(conn)
        hashes = dict(conn.execute("SELECT name, content_hash FROM packages"))
        assert hashes == {"__project__": None, "requests": "h2"}
        # chunks and their embedded flags survive: no re-embed is forced.
        assert conn.execute("SELECT content_hash, embedded FROM chunks").fetchone() == ("c1", 1)
    finally:
        conn.close()


def test_v16_stamped_db_missing_tables_is_repaired_on_open(tmp_path: Path) -> None:
    db = tmp_path / "drift.db"
    conn = sqlite3.connect(db)
    conn.executescript(_V15_SCRIPT.replace("PRAGMA user_version = 15;", "PRAGMA user_version = 16;"))
    conn.commit()
    conn.close()
    conn = open_index_database(db)
    try:
        assert _NEW_TABLES <= _tables(conn)
        # drift repair never clears content_hash — that is the version step's job
        assert conn.execute(
            "SELECT content_hash FROM packages WHERE name='__project__'"
        ).fetchone()[0] == "h1"
    finally:
        conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest tests/test_db_schema_v16_migration.py -q`
Expected: FAIL — `assert 15 == 16`.

- [ ] **Step 3: Bump the version and extend the DDL**

In `db.py`, change the constant and prepend a ledger paragraph:

```python
SCHEMA_VERSION = 16  # v16: additive — the branch dimension's tables (spec
# 2026-09-03 multi-branch §6.1, P0): branches / branch_files / branch_chunks /
# file_extractions + ix_chunks_content_hash. The upgrade clears
# packages.content_hash for __project__ ONLY, so the next index pass re-extracts
# the project once and populates the new tables; chunk content hashes are
# unchanged, so NO re-embed. Dependency packages are untouched.
# v15: additive — chunks.{source_path,start_line,end_line}
```

(keep the rest of the existing ledger below it). Append to `_DDL`, after the `index_metadata` table:

```sql
    CREATE TABLE branches (
        name            TEXT PRIMARY KEY,
        head_sha        TEXT NOT NULL,
        base_name       TEXT,
        merge_base_sha  TEXT,
        source          TEXT NOT NULL,
        worktree_path   TEXT,
        is_default      INTEGER NOT NULL DEFAULT 0,
        pipeline_hash   TEXT NOT NULL,
        indexed_at      REAL NOT NULL,
        last_used_at    REAL NOT NULL,
        status          TEXT NOT NULL DEFAULT 'active',
        merged_into     TEXT,
        retired_at      REAL,
        purge_after     REAL,
        pinned          INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE branch_files (
        branch      TEXT NOT NULL,
        path        TEXT NOT NULL,
        blob_sha    TEXT NOT NULL,
        change_kind TEXT NOT NULL DEFAULT 'unchanged',
        PRIMARY KEY (branch, path)
    );
    CREATE TABLE file_extractions (
        blob_sha        TEXT NOT NULL,
        path            TEXT NOT NULL,
        pipeline_hash   TEXT NOT NULL,
        chunk_spans     TEXT NOT NULL,
        tree_json       TEXT,
        members_json    TEXT,
        references_json TEXT,
        created_at      REAL NOT NULL,
        PRIMARY KEY (blob_sha, path, pipeline_hash)
    );
    CREATE TABLE branch_chunks (
        branch      TEXT NOT NULL,
        chunk_id    INTEGER NOT NULL,
        source_path TEXT NOT NULL,
        start_line  INTEGER,
        end_line    INTEGER,
        changed     INTEGER NOT NULL DEFAULT 0,
        slice       TEXT NOT NULL DEFAULT 'tree',
        PRIMARY KEY (branch, chunk_id)
    );
    CREATE INDEX ix_chunks_content_hash   ON chunks(content_hash);
    CREATE INDEX ix_branch_chunks_chunk   ON branch_chunks(chunk_id);
    CREATE INDEX ix_branch_chunks_changed ON branch_chunks(branch, changed);
    CREATE INDEX ix_branch_chunks_slice   ON branch_chunks(branch, slice);
```

Extend `_KNOWN_TABLES` with four entries after `"decision_records",  # new in v14`:

```python
    "branches",  # new in v16
    "branch_files",  # new in v16
    "branch_chunks",  # new in v16
    "file_extractions",  # new in v16
```

- [ ] **Step 4: Add the sweep and wire the ladder**

After `_apply_v15_additions`:

```python
def _apply_v16_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply the v16 shape — the branch dimension's tables.

    ``CREATE … IF NOT EXISTS`` keeps the sweep safe to re-run as a v16-on-open
    drift-recovery pass. The sweep never touches ``packages.content_hash``;
    only the 15 → 16 version step clears the project package's hash.
    """
    for statement in _V16_STATEMENTS:
        conn.execute(statement)


_V16_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS branches (name TEXT PRIMARY KEY, head_sha TEXT NOT NULL, "
    "base_name TEXT, merge_base_sha TEXT, source TEXT NOT NULL, worktree_path TEXT, "
    "is_default INTEGER NOT NULL DEFAULT 0, pipeline_hash TEXT NOT NULL, "
    "indexed_at REAL NOT NULL, last_used_at REAL NOT NULL, "
    "status TEXT NOT NULL DEFAULT 'active', merged_into TEXT, retired_at REAL, "
    "purge_after REAL, pinned INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS branch_files (branch TEXT NOT NULL, path TEXT NOT NULL, "
    "blob_sha TEXT NOT NULL, change_kind TEXT NOT NULL DEFAULT 'unchanged', "
    "PRIMARY KEY (branch, path))",
    "CREATE TABLE IF NOT EXISTS file_extractions (blob_sha TEXT NOT NULL, path TEXT NOT NULL, "
    "pipeline_hash TEXT NOT NULL, chunk_spans TEXT NOT NULL, tree_json TEXT, "
    "members_json TEXT, references_json TEXT, created_at REAL NOT NULL, "
    "PRIMARY KEY (blob_sha, path, pipeline_hash))",
    "CREATE TABLE IF NOT EXISTS branch_chunks (branch TEXT NOT NULL, chunk_id INTEGER NOT NULL, "
    "source_path TEXT NOT NULL, start_line INTEGER, end_line INTEGER, "
    "changed INTEGER NOT NULL DEFAULT 0, slice TEXT NOT NULL DEFAULT 'tree', "
    "PRIMARY KEY (branch, chunk_id))",
    "CREATE INDEX IF NOT EXISTS ix_chunks_content_hash ON chunks(content_hash)",
    "CREATE INDEX IF NOT EXISTS ix_branch_chunks_chunk ON branch_chunks(chunk_id)",
    "CREATE INDEX IF NOT EXISTS ix_branch_chunks_changed ON branch_chunks(branch, changed)",
    "CREATE INDEX IF NOT EXISTS ix_branch_chunks_slice ON branch_chunks(branch, slice)",
)
```

In `_migrate_in_place`: add `_apply_v16_additions(conn)` as the last sweep of EVERY existing branch (the `current == SCHEMA_VERSION` branch, the `(12, 13, 14)` branch, the `(9, 10, 11)` branch, and the `(2, 3, 4, 6, 7, 8)` branch), then insert a new branch for `15` between the `== SCHEMA_VERSION` branch and the `(12, 13, 14)` branch:

```python
    elif current == 15:
        # v15 → v16 — additive branch tables; clear the PROJECT package's
        # content_hash only, so the next pass re-extracts it once and fills
        # branches / branch_files / branch_chunks / file_extractions. Chunk
        # hashes are unchanged → no re-embed. Dependencies are not re-extracted.
        _apply_v16_additions(conn)
        conn.execute("UPDATE packages SET content_hash = NULL WHERE name = '__project__'")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

Change the `(12, 13, 14)` branch to `current in (12, 13, 14)` unchanged in membership but add the same project-only `content_hash` clear before its version stamp (those DBs also need the P0 pass to populate the tables). The `(9, 10, 11)` and `(2 … 8)` branches already clear/force re-extraction paths; add only the sweep call there.

- [ ] **Step 5: Fix the v15 test's literals**

In `tests/test_db_schema_v15_migration.py` replace `assert SCHEMA_VERSION == 15` with `assert SCHEMA_VERSION >= 15` and every `.fetchone()[0] == 15` with `.fetchone()[0] == SCHEMA_VERSION`. Then check the older migration tests:

Run: `grep -n "== 1[0-5]$\|== 1[0-5])" tests/test_db_schema_v*_migration.py tests/test_db.py`
Expected: no remaining literal version assertions (fix any hit the same way).

- [ ] **Step 6: Run the schema suite and the gate**

Run: `uv run --no-sync pytest tests/test_db_schema_v16_migration.py tests/test_db_schema_v15_migration.py tests/test_db_schema_v14_migration.py tests/test_db_schema_v13_migration.py tests/test_db_schema_v9_migration.py tests/test_db.py tests/test_db_schema_drift_recovery.py tests/db -q`
Expected: PASS.
Run: `uv run --no-sync ruff format python/pydocs_mcp/db.py tests/test_db_schema_v16_migration.py tests/test_db_schema_v15_migration.py && uv run --no-sync ruff check python/ tests/ && uv run --no-sync mypy python/pydocs_mcp && uv run --no-sync complexipy python/pydocs_mcp --max-complexity-allowed 15`
Expected: clean (if `_migrate_in_place` trips complexipy, move the per-branch sweep lists into a `_SWEEPS_BY_VERSION` tuple lookup — same behavior, table-driven).

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/db.py tests/test_db_schema_v16_migration.py tests/test_db_schema_v15_migration.py
git commit -m "feat(db): schema v16 — branches, branch_files, branch_chunks, file_extractions"
```

---

### Task 5: The three branch stores — Protocols, SQLite repositories, unit-of-work wiring, fakes

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` (three Protocols, two `ChunkStore` methods, three `UnitOfWork` accessors)
- Create: `python/pydocs_mcp/storage/sqlite/branch_repository.py`, `python/pydocs_mcp/storage/sqlite/branch_chunk_repository.py`, `python/pydocs_mcp/storage/sqlite/file_extraction_repository.py`
- Modify: `python/pydocs_mcp/storage/sqlite/chunk_repository.py`, `python/pydocs_mcp/storage/sqlite/uow.py`, `python/pydocs_mcp/storage/composite_uow.py` (`_DISPATCH_ATTRS`), `python/pydocs_mcp/storage/sqlite/__init__.py` (exports)
- Modify: `tests/_fakes.py`
- Test: `tests/storage/test_branch_repositories.py`

**Interfaces:**
- Produces Protocols (all `async`, all `@runtime_checkable`):
  - `BranchStore`: `upsert_branch(record: BranchRecord) -> None`, `get_branch(name) -> BranchRecord | None`, `list_branches() -> tuple[BranchRecord, ...]`, `default_branch_name() -> str | None`, `replace_files(branch, files: Sequence[BranchFile]) -> None`, `list_files(branch) -> tuple[BranchFile, ...]`, `count_files(branch) -> int`, `delete_branch(name) -> None`, `delete_all() -> None`.
  - `BranchChunkStore`: `replace_membership(branch, rows: Sequence[ChunkMembership]) -> None`, `list_membership(branch) -> tuple[ChunkMembership, ...]`, `count_for_branch(branch) -> int`, `delete_for_branch(branch) -> None`, `delete_all() -> None`.
  - `FileExtractionStore`: `upsert_many(rows: Sequence[FileExtraction]) -> None`, `get(blob_sha, path, pipeline_hash) -> FileExtraction | None`, `delete_unreferenced() -> int`, `delete_all() -> None`.
  - `ChunkStore` gains `insert_returning_ids(chunks: tuple[Chunk, ...]) -> tuple[int, ...]` and `delete_unreferenced_project_chunks() -> tuple[int, ...]` (ids deleted, for `vectors.remove_vectors`).
  - `UnitOfWork` gains `branches`, `branch_chunks`, `file_extractions`.
- Produces fakes: `InMemoryBranchStore`, `InMemoryBranchChunkStore`, `InMemoryFileExtractionStore`; `make_fake_uow_factory(..., branches=, branch_chunks=, file_extractions=)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_branch_repositories.py
"""Branch stores (spec §6.1): SQLite round-trips, Protocol conformance, fake parity."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchIndexSource, Chunk
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.storage.branch_records import (
    BranchFile,
    BranchRecord,
    ChunkMembership,
    FileExtraction,
)
from pydocs_mcp.storage.protocols import (
    BranchChunkStore,
    BranchStore,
    ChunkStore,
    FileExtractionStore,
    UnitOfWork,
)
from pydocs_mcp.storage.sqlite import (
    SqliteBranchChunkRepository,
    SqliteBranchRepository,
    SqliteChunkRepository,
    SqliteFileExtractionRepository,
    SqliteUnitOfWork,
)
from tests._fakes import (
    InMemoryBranchChunkStore,
    InMemoryBranchStore,
    InMemoryFileExtractionStore,
    make_fake_uow_factory,
)


def _record(name: str = "main", *, is_default: bool = True) -> BranchRecord:
    return BranchRecord(
        name=name, head_sha="a" * 40, source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p", indexed_at=10.0, last_used_at=10.0, is_default=is_default,
    )


@pytest.fixture
def uow_factory(tmp_path: Path):
    db = tmp_path / "b.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    return lambda: SqliteUnitOfWork(provider=provider)


def test_sqlite_repositories_conform(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    assert isinstance(SqliteBranchRepository(provider=provider), BranchStore)
    assert isinstance(SqliteBranchChunkRepository(provider=provider), BranchChunkStore)
    assert isinstance(SqliteFileExtractionRepository(provider=provider), FileExtractionStore)
    assert isinstance(SqliteChunkRepository(provider=provider), ChunkStore)
    assert isinstance(SqliteUnitOfWork(provider=provider), UnitOfWork)


def test_fakes_conform() -> None:
    assert isinstance(InMemoryBranchStore(), BranchStore)
    assert isinstance(InMemoryBranchChunkStore(), BranchChunkStore)
    assert isinstance(InMemoryFileExtractionStore(), FileExtractionStore)
    assert isinstance(make_fake_uow_factory()(), UnitOfWork)


@pytest.mark.parametrize("kind", ["sqlite", "fake"])
async def test_branch_and_files_round_trip(kind: str, uow_factory) -> None:
    factory = uow_factory if kind == "sqlite" else make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(_record())
        await uow.branches.replace_files("main", [
            BranchFile(branch="main", path="pkg/a.py", blob_sha="b1"),
            BranchFile(branch="main", path="pkg/b.py", blob_sha="b2"),
        ])
        await uow.commit()
    async with factory() as uow:
        assert await uow.branches.get_branch("main") == _record()
        assert await uow.branches.default_branch_name() == "main"
        assert await uow.branches.count_files("main") == 2
        assert {f.path for f in await uow.branches.list_files("main")} == {"pkg/a.py", "pkg/b.py"}
        # replace is a swap, not an append
        await uow.branches.replace_files("main", [BranchFile("main", "pkg/a.py", "b9")])
        assert [f.blob_sha for f in await uow.branches.list_files("main")] == ["b9"]
        await uow.branches.delete_branch("main")
        assert await uow.branches.get_branch("main") is None
        assert await uow.branches.count_files("main") == 0
        await uow.commit()


@pytest.mark.parametrize("kind", ["sqlite", "fake"])
async def test_membership_round_trip_and_project_gc(kind: str, uow_factory) -> None:
    factory = uow_factory if kind == "sqlite" else make_fake_uow_factory()
    kept = Chunk.from_test_inputs(package=PROJECT_PACKAGE_NAME, module="m", title="k", text="k")
    orphan = Chunk.from_test_inputs(package=PROJECT_PACKAGE_NAME, module="m", title="o", text="o")
    dep = Chunk.from_test_inputs(package="requests", module="r", title="d", text="d")
    async with factory() as uow:
        ids = await uow.chunks.insert_returning_ids((kept, orphan, dep))
        assert len(ids) == 3 and len(set(ids)) == 3
        await uow.branch_chunks.replace_membership("main", [
            ChunkMembership(branch="main", chunk_id=ids[0], source_path="m.py", start_line=1, end_line=2),
        ])
        deleted = await uow.chunks.delete_unreferenced_project_chunks()
        await uow.commit()
    assert deleted == (ids[1],)  # the orphan project chunk only; the dependency chunk survives
    async with factory() as uow:
        rows = await uow.branch_chunks.list_membership("main")
        assert [(r.chunk_id, r.start_line, r.end_line) for r in rows] == [(ids[0], 1, 2)]
        assert await uow.branch_chunks.count_for_branch("main") == 1
        assert await uow.chunks.count(filter={"package": "requests"}) == 1
        assert await uow.chunks.count(filter={"package": PROJECT_PACKAGE_NAME}) == 1


@pytest.mark.parametrize("kind", ["sqlite", "fake"])
async def test_file_extractions_upsert_get_and_unreferenced_delete(kind: str, uow_factory) -> None:
    factory = uow_factory if kind == "sqlite" else make_fake_uow_factory()
    live = FileExtraction("b1", "pkg/a.py", "p", "[[1, 1, 2]]", 5.0)
    stale = FileExtraction("b0", "pkg/a.py", "p", "[[9, 1, 2]]", 4.0)
    async with factory() as uow:
        await uow.file_extractions.upsert_many([live, stale])
        await uow.branches.upsert_branch(_record())
        await uow.branches.replace_files("main", [BranchFile("main", "pkg/a.py", "b1")])
        assert await uow.file_extractions.get("b1", "pkg/a.py", "p") == live
        assert await uow.file_extractions.delete_unreferenced() == 1
        assert await uow.file_extractions.get("b0", "pkg/a.py", "p") is None
        assert await uow.file_extractions.get("b1", "pkg/a.py", "p") == live
        await uow.commit()


async def test_delete_all_wipes_branch_tables(uow_factory) -> None:
    async with uow_factory() as uow:
        await uow.branches.upsert_branch(_record())
        await uow.branch_chunks.replace_membership("main", [ChunkMembership("main", 1, "m.py")])
        await uow.file_extractions.upsert_many([FileExtraction("b", "m.py", "p", "[]", 1.0)])
        await uow.delete_all()
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.branches.list_branches() == ()
        assert await uow.branch_chunks.count_for_branch("main") == 0
        assert await uow.file_extractions.get("b", "m.py", "p") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest tests/storage/test_branch_repositories.py -q`
Expected: FAIL — `ImportError: cannot import name 'BranchChunkStore' from 'pydocs_mcp.storage.protocols'`.

- [ ] **Step 3: Extend `storage/protocols.py`**

Add to the `TYPE_CHECKING` imports:

```python
    from pydocs_mcp.storage.branch_records import (
        BranchFile,
        BranchRecord,
        ChunkMembership,
        FileExtraction,
    )
```

Append to `class ChunkStore(Protocol)` (after `refresh_span_metadata`):

```python
    async def insert_returning_ids(self, chunks: tuple[Chunk, ...]) -> tuple[int, ...]:
        """Insert-only, returning the new row ids in input order (spec §6.3 step 4).

        Membership rows need the persisted id of every incoming chunk; pairing
        by hash after the fact (the ``_maybe_write_vectors`` tail-slice trick)
        is fragile, so the insert reports its ids.
        """
        ...

    async def delete_unreferenced_project_chunks(self) -> tuple[int, ...]:
        """Project-scoped GC (spec §6.1): delete ``__project__`` rows no
        ``branch_chunks`` row references; return their ids so the caller can
        drop the vectors. Dependency packages are never touched here."""
        ...
```

Append three Protocols before `class UnitOfWork(Protocol)`:

```python
@runtime_checkable
class BranchStore(Protocol):
    """``branches`` + ``branch_files`` — the branch record and its manifest (spec §6.1)."""

    async def upsert_branch(self, record: BranchRecord) -> None: ...
    async def get_branch(self, name: str) -> BranchRecord | None: ...
    async def list_branches(self) -> tuple[BranchRecord, ...]: ...
    async def default_branch_name(self) -> str | None: ...
    async def replace_files(self, branch: str, files: Sequence[BranchFile]) -> None: ...
    async def list_files(self, branch: str) -> tuple[BranchFile, ...]: ...
    async def count_files(self, branch: str) -> int: ...
    async def delete_branch(self, name: str) -> None:
        """Drop the record AND its manifest rows."""
        ...

    async def delete_all(self) -> None: ...


@runtime_checkable
class BranchChunkStore(Protocol):
    """``branch_chunks`` — membership with per-branch spans (spec §6.1)."""

    async def replace_membership(self, branch: str, rows: Sequence[ChunkMembership]) -> None:
        """Atomic swap: the branch's membership becomes exactly ``rows``."""
        ...

    async def list_membership(self, branch: str) -> tuple[ChunkMembership, ...]: ...
    async def count_for_branch(self, branch: str) -> int: ...
    async def delete_for_branch(self, branch: str) -> None: ...
    async def delete_all(self) -> None: ...


@runtime_checkable
class FileExtractionStore(Protocol):
    """``file_extractions`` — the blob-keyed extraction cache (spec §6.1)."""

    async def upsert_many(self, rows: Sequence[FileExtraction]) -> None: ...
    async def get(self, blob_sha: str, path: str, pipeline_hash: str) -> FileExtraction | None: ...
    async def delete_unreferenced(self) -> int:
        """Drop rows whose ``(blob_sha, path)`` no ``branch_files`` row references."""
        ...

    async def delete_all(self) -> None: ...
```

Add to `class UnitOfWork(Protocol)`, after the `decisions` property:

```python
    @property
    def branches(self) -> BranchStore: ...

    @property
    def branch_chunks(self) -> BranchChunkStore: ...

    @property
    def file_extractions(self) -> FileExtractionStore: ...
```

- [ ] **Step 4: Create `storage/sqlite/branch_repository.py`**

```python
"""SqliteBranchRepository — BranchStore over ``branches`` + ``branch_files`` (spec §6.1)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import BranchIndexSource, BranchStatus, FileChangeKind
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

_BRANCH_COLUMNS = (
    "name", "head_sha", "base_name", "merge_base_sha", "source", "worktree_path",
    "is_default", "pipeline_hash", "indexed_at", "last_used_at", "status",
    "merged_into", "retired_at", "purge_after", "pinned",
)
_UPSERT_BRANCH_SQL = (
    f"INSERT INTO branches ({', '.join(_BRANCH_COLUMNS)}) VALUES "
    f"({', '.join(':' + c for c in _BRANCH_COLUMNS)}) ON CONFLICT(name) DO UPDATE SET "
    + ", ".join(f"{c}=excluded.{c}" for c in _BRANCH_COLUMNS if c != "name")
)
_SELECT_BRANCH_SQL = f"SELECT {', '.join(_BRANCH_COLUMNS)} FROM branches"
_INSERT_FILE_SQL = (
    "INSERT INTO branch_files (branch, path, blob_sha, change_kind) "
    "VALUES (:branch, :path, :blob_sha, :change_kind)"
)


def _branch_to_row(r: BranchRecord) -> dict[str, object]:
    return {
        "name": r.name, "head_sha": r.head_sha, "base_name": r.base_name,
        "merge_base_sha": r.merge_base_sha, "source": r.source.value,
        "worktree_path": r.worktree_path, "is_default": int(r.is_default),
        "pipeline_hash": r.pipeline_hash, "indexed_at": r.indexed_at,
        "last_used_at": r.last_used_at, "status": r.status.value,
        "merged_into": r.merged_into, "retired_at": r.retired_at,
        "purge_after": r.purge_after, "pinned": int(r.pinned),
    }


def _row_to_branch(row: sqlite3.Row) -> BranchRecord:
    return BranchRecord(
        name=row["name"], head_sha=row["head_sha"], base_name=row["base_name"],
        merge_base_sha=row["merge_base_sha"], source=BranchIndexSource(row["source"]),
        worktree_path=row["worktree_path"], is_default=bool(row["is_default"]),
        pipeline_hash=row["pipeline_hash"], indexed_at=row["indexed_at"],
        last_used_at=row["last_used_at"], status=BranchStatus(row["status"]),
        merged_into=row["merged_into"], retired_at=row["retired_at"],
        purge_after=row["purge_after"], pinned=bool(row["pinned"]),
    )


def _row_to_file(row: sqlite3.Row) -> BranchFile:
    return BranchFile(
        branch=row["branch"], path=row["path"], blob_sha=row["blob_sha"],
        change_kind=FileChangeKind(row["change_kind"]),
    )


@dataclass(frozen=True, slots=True)
class SqliteBranchRepository:
    provider: ConnectionProvider

    async def upsert_branch(self, record: BranchRecord) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, _UPSERT_BRANCH_SQL, _branch_to_row(record))

    async def get_branch(self, name: str) -> BranchRecord | None:
        sql = _SELECT_BRANCH_SQL + " WHERE name = ?"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(lambda: conn.execute(sql, (name,)).fetchone())
        return _row_to_branch(row) if row else None

    async def list_branches(self) -> tuple[BranchRecord, ...]:
        sql = _SELECT_BRANCH_SQL + " ORDER BY is_default DESC, name"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql).fetchall())
        return tuple(_row_to_branch(r) for r in rows)

    async def default_branch_name(self) -> str | None:
        sql = "SELECT name FROM branches WHERE is_default = 1 ORDER BY indexed_at DESC LIMIT 1"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(lambda: conn.execute(sql).fetchone())
        return row["name"] if row else None

    async def replace_files(self, branch: str, files: Sequence[BranchFile]) -> None:
        rows = [
            {"branch": branch, "path": f.path, "blob_sha": f.blob_sha,
             "change_kind": f.change_kind.value}
            for f in files
        ]
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM branch_files WHERE branch = ?", (branch,))
            await asyncio.to_thread(conn.executemany, _INSERT_FILE_SQL, rows)

    async def list_files(self, branch: str) -> tuple[BranchFile, ...]:
        sql = "SELECT branch, path, blob_sha, change_kind FROM branch_files WHERE branch = ? ORDER BY path"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, (branch,)).fetchall())
        return tuple(_row_to_file(r) for r in rows)

    async def count_files(self, branch: str) -> int:
        sql = "SELECT COUNT(*) FROM branch_files WHERE branch = ?"
        async with _maybe_acquire(self.provider) as conn:
            return int(await asyncio.to_thread(lambda: conn.execute(sql, (branch,)).fetchone()[0]))

    async def delete_branch(self, name: str) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM branch_files WHERE branch = ?", (name,))
            await asyncio.to_thread(conn.execute, "DELETE FROM branches WHERE name = ?", (name,))

    async def delete_all(self) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM branch_files")
            await asyncio.to_thread(conn.execute, "DELETE FROM branches")
```

- [ ] **Step 5: Create `storage/sqlite/branch_chunk_repository.py`**

```python
"""SqliteBranchChunkRepository — BranchChunkStore over ``branch_chunks`` (spec §6.1)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import BranchSlice
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.branch_records import ChunkMembership
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

_INSERT_SQL = (
    "INSERT INTO branch_chunks (branch, chunk_id, source_path, start_line, end_line, changed, slice) "
    "VALUES (:branch, :chunk_id, :source_path, :start_line, :end_line, :changed, :slice)"
)
_SELECT_SQL = (
    "SELECT branch, chunk_id, source_path, start_line, end_line, changed, slice "
    "FROM branch_chunks WHERE branch = ? ORDER BY source_path, start_line, chunk_id"
)


def _membership_to_row(m: ChunkMembership) -> dict[str, object]:
    return {
        "branch": m.branch, "chunk_id": m.chunk_id, "source_path": m.source_path,
        "start_line": m.start_line, "end_line": m.end_line, "changed": int(m.changed),
        "slice": m.slice.value,
    }


def _row_to_membership(row: sqlite3.Row) -> ChunkMembership:
    return ChunkMembership(
        branch=row["branch"], chunk_id=row["chunk_id"], source_path=row["source_path"],
        start_line=row["start_line"], end_line=row["end_line"], changed=bool(row["changed"]),
        slice=BranchSlice(row["slice"]),
    )


@dataclass(frozen=True, slots=True)
class SqliteBranchChunkRepository:
    provider: ConnectionProvider

    async def replace_membership(self, branch: str, rows: Sequence[ChunkMembership]) -> None:
        params = [_membership_to_row(m) for m in rows]
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM branch_chunks WHERE branch = ?", (branch,))
            await asyncio.to_thread(conn.executemany, _INSERT_SQL, params)

    async def list_membership(self, branch: str) -> tuple[ChunkMembership, ...]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(_SELECT_SQL, (branch,)).fetchall())
        return tuple(_row_to_membership(r) for r in rows)

    async def count_for_branch(self, branch: str) -> int:
        sql = "SELECT COUNT(*) FROM branch_chunks WHERE branch = ?"
        async with _maybe_acquire(self.provider) as conn:
            return int(await asyncio.to_thread(lambda: conn.execute(sql, (branch,)).fetchone()[0]))

    async def delete_for_branch(self, branch: str) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM branch_chunks WHERE branch = ?", (branch,))

    async def delete_all(self) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM branch_chunks")
```

- [ ] **Step 6: Create `storage/sqlite/file_extraction_repository.py`**

```python
"""SqliteFileExtractionRepository — FileExtractionStore over ``file_extractions`` (spec §6.1)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.branch_records import FileExtraction
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

_COLUMNS = (
    "blob_sha", "path", "pipeline_hash", "chunk_spans", "tree_json", "members_json",
    "references_json", "created_at",
)
# COALESCE keeps a P1-populated tree/members/references column when a later
# spans-only write (P0 shape) lands on the same key.
_UPSERT_SQL = (
    f"INSERT INTO file_extractions ({', '.join(_COLUMNS)}) VALUES "
    f"({', '.join(':' + c for c in _COLUMNS)}) "
    "ON CONFLICT(blob_sha, path, pipeline_hash) DO UPDATE SET "
    "chunk_spans=excluded.chunk_spans, "
    "tree_json=COALESCE(excluded.tree_json, tree_json), "
    "members_json=COALESCE(excluded.members_json, members_json), "
    "references_json=COALESCE(excluded.references_json, references_json), "
    "created_at=excluded.created_at"
)
_GET_SQL = (
    f"SELECT {', '.join(_COLUMNS)} FROM file_extractions "
    "WHERE blob_sha = ? AND path = ? AND pipeline_hash = ?"
)
_DELETE_UNREFERENCED_SQL = (
    "DELETE FROM file_extractions WHERE NOT EXISTS (SELECT 1 FROM branch_files bf "
    "WHERE bf.blob_sha = file_extractions.blob_sha AND bf.path = file_extractions.path)"
)


def _row_to_extraction(row: sqlite3.Row) -> FileExtraction:
    return FileExtraction(
        blob_sha=row["blob_sha"], path=row["path"], pipeline_hash=row["pipeline_hash"],
        chunk_spans=row["chunk_spans"], created_at=row["created_at"],
        tree_json=row["tree_json"], members_json=row["members_json"],
        references_json=row["references_json"],
    )


@dataclass(frozen=True, slots=True)
class SqliteFileExtractionRepository:
    provider: ConnectionProvider

    async def upsert_many(self, rows: Sequence[FileExtraction]) -> None:
        params = [
            {"blob_sha": r.blob_sha, "path": r.path, "pipeline_hash": r.pipeline_hash,
             "chunk_spans": r.chunk_spans, "tree_json": r.tree_json,
             "members_json": r.members_json, "references_json": r.references_json,
             "created_at": r.created_at}
            for r in rows
        ]
        if not params:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.executemany, _UPSERT_SQL, params)

    async def get(self, blob_sha: str, path: str, pipeline_hash: str) -> FileExtraction | None:
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(_GET_SQL, (blob_sha, path, pipeline_hash)).fetchone()
            )
        return _row_to_extraction(row) if row else None

    async def delete_unreferenced(self) -> int:
        async with _maybe_acquire(self.provider) as conn:
            cursor = await asyncio.to_thread(conn.execute, _DELETE_UNREFERENCED_SQL)
        return int(cursor.rowcount)

    async def delete_all(self) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM file_extractions")
```

- [ ] **Step 7: Add the two `ChunkStore` methods to `chunk_repository.py`**

Add `from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk` (extend the existing import) and, after `_REFRESH_SPAN_SQL`:

```python
_UNREFERENCED_PROJECT_SQL = (
    "SELECT id FROM chunks WHERE package = ? AND NOT EXISTS "
    "(SELECT 1 FROM branch_chunks bc WHERE bc.chunk_id = chunks.id)"
)


def _insert_rows_returning_ids(conn: sqlite3.Connection, rows: list[dict[str, object]]) -> tuple[int, ...]:
    # Per-row execute so ``lastrowid`` is exact on every supported SQLite
    # (``INSERT … RETURNING`` needs 3.35+, which the manylinux floor does not
    # promise). One statement per chunk inside one transaction is cheap.
    ids: list[int] = []
    for row in rows:
        cursor = conn.execute(_INSERT_CHUNK_SQL, row)
        ids.append(int(cursor.lastrowid))
    return tuple(ids)
```

(add `import sqlite3` at the top). Then two methods on `SqliteChunkRepository`, after `insert`:

```python
    async def insert_returning_ids(self, chunks: tuple[Chunk, ...]) -> tuple[int, ...]:
        rows = [_chunk_to_row(c) for c in chunks]
        if not rows:
            return ()
        async with _maybe_acquire(self.provider) as conn:
            return await asyncio.to_thread(_insert_rows_returning_ids, conn, rows)

    async def delete_unreferenced_project_chunks(self) -> tuple[int, ...]:
        # Two acquisitions on purpose: ``delete_by_ids`` re-enters
        # ``_maybe_acquire``, and the ambient lock is not re-entrant.
        async with _maybe_acquire(self.provider) as conn:
            ids = await asyncio.to_thread(
                lambda: [r["id"] for r in conn.execute(
                    _UNREFERENCED_PROJECT_SQL, (PROJECT_PACKAGE_NAME,)
                ).fetchall()]
            )
        await self.delete_by_ids(ids)
        return tuple(ids)
```

- [ ] **Step 8: Wire the repositories into the unit of work**

`storage/sqlite/uow.py` — imports:

```python
from pydocs_mcp.storage.sqlite.branch_chunk_repository import SqliteBranchChunkRepository
from pydocs_mcp.storage.sqlite.branch_repository import SqliteBranchRepository
from pydocs_mcp.storage.sqlite.file_extraction_repository import SqliteFileExtractionRepository
```

Fields, right after `_decisions`:

```python
    _branches: SqliteBranchRepository | None = field(default=None, init=False, repr=False)
    _branch_chunks: SqliteBranchChunkRepository | None = field(
        default=None, init=False, repr=False
    )
    _file_extractions: SqliteFileExtractionRepository | None = field(
        default=None, init=False, repr=False
    )
```

In `__aenter__`, after `self._decisions = …`:

```python
            self._branches = SqliteBranchRepository(provider=self.provider)
            self._branch_chunks = SqliteBranchChunkRepository(provider=self.provider)
            self._file_extractions = SqliteFileExtractionRepository(provider=self.provider)
```

In `delete_all`, children first — insert BEFORE `await self.chunks.delete_all()`:

```python
        await self.branch_chunks.delete_all()
        await self.file_extractions.delete_all()
        await self.branches.delete_all()
```

Three properties after `decisions`:

```python
    @property
    def branches(self) -> SqliteBranchRepository:
        if self._branches is None:
            raise UnitOfWorkNotEnteredError("branches")
        return self._branches

    @property
    def branch_chunks(self) -> SqliteBranchChunkRepository:
        if self._branch_chunks is None:
            raise UnitOfWorkNotEnteredError("branch_chunks")
        return self._branch_chunks

    @property
    def file_extractions(self) -> SqliteFileExtractionRepository:
        if self._file_extractions is None:
            raise UnitOfWorkNotEnteredError("file_extractions")
        return self._file_extractions
```

`storage/composite_uow.py` — extend `_DISPATCH_ATTRS` with `"branches", "branch_chunks", "file_extractions"` (before `"vectors"`). `storage/sqlite/__init__.py` — import and export `SqliteBranchRepository`, `SqliteBranchChunkRepository`, `SqliteFileExtractionRepository` (add to `__all__`, alphabetical).

- [ ] **Step 9: Extend the fakes**

In `tests/_fakes.py`, add imports `from pydocs_mcp.models import PROJECT_PACKAGE_NAME` (extend the existing `pydocs_mcp.models` import) and `from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord, ChunkMembership, FileExtraction`. Add three stores before `class FakeUnitOfWork`:

```python
@dataclass
class InMemoryBranchStore:
    records: dict[str, BranchRecord] = field(default_factory=dict)
    files: dict[str, list[BranchFile]] = field(default_factory=dict)
    calls: list[_Call] = field(default_factory=list)

    async def upsert_branch(self, record: BranchRecord) -> None:
        self.calls.append(_Call("upsert_branch", record))
        self.records[record.name] = record

    async def get_branch(self, name: str) -> BranchRecord | None:
        return self.records.get(name)

    async def list_branches(self) -> tuple[BranchRecord, ...]:
        return tuple(sorted(self.records.values(), key=lambda r: (not r.is_default, r.name)))

    async def default_branch_name(self) -> str | None:
        defaults = [r for r in self.records.values() if r.is_default]
        return max(defaults, key=lambda r: r.indexed_at).name if defaults else None

    async def replace_files(self, branch: str, files) -> None:
        self.calls.append(_Call("replace_files", (branch, tuple(files))))
        self.files[branch] = [replace(f, branch=branch) for f in files]

    async def list_files(self, branch: str) -> tuple[BranchFile, ...]:
        return tuple(sorted(self.files.get(branch, []), key=lambda f: f.path))

    async def count_files(self, branch: str) -> int:
        return len(self.files.get(branch, []))

    async def delete_branch(self, name: str) -> None:
        self.records.pop(name, None)
        self.files.pop(name, None)

    async def delete_all(self) -> None:
        self.records.clear()
        self.files.clear()


@dataclass
class InMemoryBranchChunkStore:
    rows: dict[str, list[ChunkMembership]] = field(default_factory=dict)
    calls: list[_Call] = field(default_factory=list)

    async def replace_membership(self, branch: str, rows) -> None:
        self.calls.append(_Call("replace_membership", (branch, tuple(rows))))
        self.rows[branch] = list(rows)

    async def list_membership(self, branch: str) -> tuple[ChunkMembership, ...]:
        return tuple(
            sorted(self.rows.get(branch, []), key=lambda m: (m.source_path, m.start_line or 0, m.chunk_id))
        )

    async def count_for_branch(self, branch: str) -> int:
        return len(self.rows.get(branch, []))

    async def delete_for_branch(self, branch: str) -> None:
        self.rows.pop(branch, None)

    async def delete_all(self) -> None:
        self.rows.clear()

    def referenced_ids(self) -> set[int]:
        return {m.chunk_id for rows in self.rows.values() for m in rows}


@dataclass
class InMemoryFileExtractionStore:
    rows: dict[tuple[str, str, str], FileExtraction] = field(default_factory=dict)
    # Linked by make_fake_uow_factory so delete_unreferenced mirrors the SQL join.
    branches: InMemoryBranchStore | None = None

    async def upsert_many(self, rows) -> None:
        for r in rows:
            self.rows[(r.blob_sha, r.path, r.pipeline_hash)] = r

    async def get(self, blob_sha: str, path: str, pipeline_hash: str) -> FileExtraction | None:
        return self.rows.get((blob_sha, path, pipeline_hash))

    async def delete_unreferenced(self) -> int:
        live = {
            (f.blob_sha, f.path)
            for files in (self.branches.files.values() if self.branches else [])
            for f in files
        }
        stale = [k for k in self.rows if (k[0], k[1]) not in live]
        for k in stale:
            del self.rows[k]
        return len(stale)

    async def delete_all(self) -> None:
        self.rows.clear()
```

Add to `InMemoryChunkStore` a link field and the two methods (after `insert`):

```python
    # Linked by make_fake_uow_factory so the project GC mirrors the SQL NOT EXISTS.
    membership: InMemoryBranchChunkStore | None = None

    async def insert_returning_ids(self, chunks) -> tuple[int, ...]:
        materialised = tuple(chunks)
        before = {id(c) for cs in self.by_package.values() for c in cs}
        await self.insert(materialised)
        added = [c for cs in self.by_package.values() for c in cs if id(c) not in before]
        return tuple(c.id for c in added if c.id is not None)

    async def delete_unreferenced_project_chunks(self) -> tuple[int, ...]:
        referenced = self.membership.referenced_ids() if self.membership else set()
        rows = self.by_package.get(PROJECT_PACKAGE_NAME, [])
        stale = tuple(c.id for c in rows if c.id is not None and c.id not in referenced)
        await self.delete_by_ids(list(stale))
        return stale
```

(`membership` must be declared as a dataclass field of `InMemoryChunkStore`, i.e. placed with the other fields at the top of the class, not among the methods.)

`FakeUnitOfWork`: add fields `branches_store: InMemoryBranchStore = field(default_factory=InMemoryBranchStore)`, `branch_chunks_store: InMemoryBranchChunkStore = field(default_factory=InMemoryBranchChunkStore)`, `file_extractions_store: InMemoryFileExtractionStore = field(default_factory=InMemoryFileExtractionStore)`; the three `init=False` attributes `branches`, `branch_chunks`, `file_extractions`; proxies in `__post_init__` and `__aexit__` (`_NotEnteredProxy("branches")` etc.); real stores in `__aenter__`; and in `delete_all`, before `await self.chunks_store.delete(None)`:

```python
        await self.branch_chunks_store.delete_all()
        await self.file_extractions_store.delete_all()
        await self.branches_store.delete_all()
```

`make_fake_uow_factory`: add kwargs `branches: InMemoryBranchStore | None = None`, `branch_chunks: InMemoryBranchChunkStore | None = None`, `file_extractions: InMemoryFileExtractionStore | None = None`; build `brs = branches or InMemoryBranchStore()`, `bcs = branch_chunks or InMemoryBranchChunkStore()`, `fes = file_extractions or InMemoryFileExtractionStore()`; link `chs.membership = bcs` and `fes.branches = brs` (guarded with `if … is None`, like the `nss.references` link); pass `branches_store=brs, branch_chunks_store=bcs, file_extractions_store=fes` to `FakeUnitOfWork`.

- [ ] **Step 10: Run the tests and the gate**

Run: `uv run --no-sync pytest tests/storage/test_branch_repositories.py tests/storage/test_protocol_conformance.py tests/storage/test_composite_uow_protocol_conformance.py tests/test_fakes.py tests/application/test_indexing_service_diff_merge.py -q`
Expected: PASS.
Run: `uv run --no-sync ruff format python/pydocs_mcp/storage tests/_fakes.py tests/storage/test_branch_repositories.py && uv run --no-sync ruff check python/ tests/ && uv run --no-sync mypy python/pydocs_mcp && uv run --no-sync complexipy python/pydocs_mcp --max-complexity-allowed 15 && uv run --no-sync vulture python/pydocs_mcp --min-confidence 80`
Expected: clean.

- [ ] **Step 11: Commit**

```bash
git add python/pydocs_mcp/storage tests/_fakes.py tests/storage/test_branch_repositories.py
git commit -m "feat(storage): branch, membership and extraction-cache stores on the unit of work"
```

---

### Task 6: `discovered_paths` on the extraction result, and the working-tree manifest builder

**Files:**
- Modify: `python/pydocs_mcp/application/protocols.py` (`ExtractionResult.discovered_paths`), `python/pydocs_mcp/extraction/pipeline/chunk_extractor.py` (`_unwrap`)
- Create: `python/pydocs_mcp/application/branch_manifest.py`
- Modify: `tests/_fakes.py` (`FakeGitRepository`)
- Test: `tests/application/test_branch_manifest.py`

**Interfaces:**
- Produces: `ExtractionResult.discovered_paths: tuple[str, ...]` (absolute paths exactly as `state.files.paths`); `BranchManifest(name, head_sha, source, pipeline_hash, files, worktree_path=None)`; `BranchManifestBuilder` Protocol with `async build(project_root: Path, discovered_paths: Sequence[str]) -> BranchManifest | None`; `NoBranchManifestBuilder` (returns `None`); `WorkingTreeManifestBuilder(git_repository_for: Callable[[Path], GitRepository], pipeline_hash: str)`; helpers `project_relative_path(path, root) -> str`, `branch_display_name(branch, head_sha) -> str`.
- `FakeGitRepository(branch=…, head=…, tracked={path: blob}, changes={path: FileChangeKind}, hashes={path: blob}, fail=False)` in `tests/_fakes.py`, recording `hashed_paths`.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_branch_manifest.py
"""WorkingTreeManifestBuilder (spec §6.3 step 1, §6.14 items 1/5/7)."""

from __future__ import annotations

import logging
from pathlib import Path

from pydocs_mcp.application.branch_manifest import (
    BranchManifest,
    BranchManifestBuilder,
    NoBranchManifestBuilder,
    WorkingTreeManifestBuilder,
    branch_display_name,
    project_relative_path,
)
from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.git.null_repository import NullGitRepository
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, BranchIndexSource, FileChangeKind
from tests._fakes import FakeGitRepository


def test_project_relative_path_is_posix_and_symlink_preserving(tmp_path: Path) -> None:
    assert project_relative_path(str(tmp_path / "pkg" / "a.py"), tmp_path) == "pkg/a.py"
    assert project_relative_path("/elsewhere/x.py", tmp_path) == "/elsewhere/x.py"


def test_branch_display_name_rules() -> None:
    assert branch_display_name("feature/x", "a" * 40) == "feature/x"
    assert branch_display_name(None, "8783c8c1234") == "detached-8783c8c"
    assert branch_display_name(None, None) == NON_GIT_BRANCH_NAME


def test_extraction_result_defaults_discovered_paths_to_empty() -> None:
    assert ExtractionResult.__dataclass_fields__["discovered_paths"].default == ()


async def test_builder_uses_index_blobs_and_hashes_only_dirty_files(tmp_path: Path) -> None:
    git = FakeGitRepository(
        branch="main", head="b" * 40,
        tracked={"pkg/a.py": "blob-a", "pkg/b.py": "blob-b-old"},
        changes={"pkg/b.py": FileChangeKind.MODIFIED, "pkg/c.py": FileChangeKind.ADDED},
        hashes={"pkg/b.py": "blob-b-new", "pkg/c.py": "blob-c"},
    )
    builder = WorkingTreeManifestBuilder(git_repository_for=lambda root: git, pipeline_hash="p")
    paths = [str(tmp_path / p) for p in ("pkg/a.py", "pkg/b.py", "pkg/c.py")]
    manifest = await builder.build(tmp_path, paths)
    assert isinstance(builder, BranchManifestBuilder)
    assert manifest == BranchManifest(
        name="main", head_sha="b" * 40, source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p", worktree_path=str(tmp_path),
        files=(
            _file("main", "pkg/a.py", "blob-a"),
            _file("main", "pkg/b.py", "blob-b-new"),
            _file("main", "pkg/c.py", "blob-c"),
        ),
    )
    assert git.hashed_paths == ["pkg/b.py", "pkg/c.py"]  # unchanged tracked files never re-hashed


async def test_builder_without_git_yields_sentinel_branch_and_blank_blobs(tmp_path: Path) -> None:
    builder = WorkingTreeManifestBuilder(
        git_repository_for=lambda root: NullGitRepository(), pipeline_hash="p"
    )
    manifest = await builder.build(tmp_path, [str(tmp_path / "a.py")])
    assert manifest is not None
    assert manifest.name == NON_GIT_BRANCH_NAME and manifest.head_sha == ""
    assert manifest.files == (_file(NON_GIT_BRANCH_NAME, "a.py", ""),)


async def test_builder_degrades_and_logs_when_git_fails(tmp_path: Path, caplog) -> None:
    git = FakeGitRepository(branch="main", head="b" * 40, fail=True)
    builder = WorkingTreeManifestBuilder(git_repository_for=lambda root: git, pipeline_hash="p")
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        manifest = await builder.build(tmp_path, [str(tmp_path / "a.py")])
    assert manifest is not None and manifest.name == NON_GIT_BRANCH_NAME
    assert manifest.files[0].blob_sha == ""
    assert "git_manifest_unavailable" in caplog.text


async def test_null_builder_returns_none(tmp_path: Path) -> None:
    assert await NoBranchManifestBuilder().build(tmp_path, []) is None


def _file(branch: str, path: str, blob: str):
    from pydocs_mcp.storage.branch_records import BranchFile

    return BranchFile(branch=branch, path=path, blob_sha=blob)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_branch_manifest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.application.branch_manifest'`.

- [ ] **Step 3: Add `discovered_paths` to `ExtractionResult` and populate it**

In `application/protocols.py`, `class ExtractionResult`, after `class_attribute_types`:

```python
    # The exact absolute paths the discovery stage walked (spec §6.14 item 5):
    # the branch manifest is built from these, so it equals what was
    # extracted by construction — no second walk, no drift.
    discovered_paths: tuple[str, ...] = field(default=())
```

In `extraction/pipeline/chunk_extractor.py::_unwrap`, add to the `ExtractionResult(...)` call:

```python
            discovered_paths=tuple(state.files.paths),
```

- [ ] **Step 4: Add `FakeGitRepository` to `tests/_fakes.py`**

```python
@dataclass
class FakeGitRepository:
    """In-memory GitRepository (spec §6.2) — no subprocess, records hashed paths."""

    branch: str | None = None
    head: str | None = None
    tracked: dict[str, str] = field(default_factory=dict)
    changes: dict[str, FileChangeKind] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)
    worktrees: tuple[tuple[str, str | None], ...] = ()
    fail: bool = False
    hashed_paths: list[str] = field(default_factory=list)

    def _guard(self) -> None:
        if self.fail:
            raise GitCommandError(("git", "fake"), "exit 128", "fatal: simulated")

    def current_branch(self) -> str | None:
        self._guard()
        return self.branch

    def head_sha(self) -> str | None:
        self._guard()
        return self.head

    def index_manifest(self) -> tuple[tuple[str, str], ...]:
        self._guard()
        return tuple(self.tracked.items())

    def hash_objects(self, paths) -> tuple[tuple[str, str], ...]:
        self._guard()
        self.hashed_paths.extend(paths)
        return tuple((p, self.hashes[p]) for p in paths)

    def working_tree_changes(self) -> tuple[tuple[str, FileChangeKind], ...]:
        self._guard()
        return tuple(self.changes.items())

    def list_worktrees(self) -> tuple[tuple[str, str | None], ...]:
        self._guard()
        return self.worktrees
```

with `from pydocs_mcp.git.errors import GitCommandError` and `FileChangeKind` added to the `pydocs_mcp.models` import.

- [ ] **Step 5: Create `application/branch_manifest.py`**

```python
"""Branch manifest for the working-tree branch (spec §6.3 step 1).

Application layer on purpose (spec §6.14 item 1): it composes the git port with
the discovery result. Blob ids come from git's own index for tracked,
unmodified files and from ``hash-object`` only for files git reports as
changed or untracked, so an unchanged tree costs no file reads.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, BranchIndexSource, FileChangeKind
from pydocs_mcp.storage.branch_records import BranchFile

log = logging.getLogger("pydocs-mcp")

_DETACHED_PREFIX = "detached-"
_SHORT_SHA_LEN = 7


@dataclass(frozen=True, slots=True)
class BranchManifest:
    """Everything ``reindex_package`` needs to stamp one branch (spec §6.1)."""

    name: str
    head_sha: str
    source: BranchIndexSource
    pipeline_hash: str
    files: tuple[BranchFile, ...]
    worktree_path: str | None = None


@runtime_checkable
class BranchManifestBuilder(Protocol):
    async def build(
        self, project_root: Path, discovered_paths: Sequence[str]
    ) -> BranchManifest | None: ...


@dataclass(frozen=True, slots=True)
class NoBranchManifestBuilder:
    """Null Object — no branch dimension (tests and callers that never wired git)."""

    async def build(
        self, project_root: Path, discovered_paths: Sequence[str]
    ) -> BranchManifest | None:
        return None


def project_relative_path(path: str, root: Path) -> str:
    """POSIX path relative to ``root``; a path outside the root passes through.

    ``os.path.abspath`` rather than ``Path.resolve()`` so a symlinked file keeps
    its in-tree location — the same rule as the chunkers' ``_relpath``, which
    writes ``chunks.source_path``; the two must agree for membership joins.
    """
    try:
        rel = Path(os.path.abspath(path)).relative_to(os.path.abspath(root))  # noqa: PTH100
    except ValueError:
        return path
    return PurePosixPath(rel).as_posix()


def branch_display_name(branch: str | None, head_sha: str | None) -> str:
    """Ref short name, ``detached-<sha7>``, or the non-git sentinel (spec §2)."""
    if branch:
        return branch
    if head_sha:
        return f"{_DETACHED_PREFIX}{head_sha[:_SHORT_SHA_LEN]}"
    return NON_GIT_BRANCH_NAME


def _blob_ids(git: GitRepository, relative: Sequence[str]) -> dict[str, str]:
    """Blob ids from git's stat cache; re-hash only files git reports as changed."""
    tracked = dict(git.index_manifest())
    dirty = {p for p, kind in git.working_tree_changes() if kind is not FileChangeKind.DELETED}
    to_hash = [p for p in relative if p in dirty or p not in tracked]
    hashed = dict(git.hash_objects(to_hash)) if to_hash else {}
    return {p: hashed.get(p) or tracked.get(p, "") for p in relative}


def _read_identity(
    git: GitRepository, relative: Sequence[str]
) -> tuple[str | None, str | None, dict[str, str]]:
    return git.current_branch(), git.head_sha(), _blob_ids(git, relative)


@dataclass(frozen=True, slots=True)
class WorkingTreeManifestBuilder:
    git_repository_for: Callable[[Path], GitRepository]
    pipeline_hash: str

    async def build(
        self, project_root: Path, discovered_paths: Sequence[str]
    ) -> BranchManifest | None:
        git = self.git_repository_for(project_root)
        relative = tuple(project_relative_path(p, project_root) for p in discovered_paths)
        try:
            branch, head, blobs = await asyncio.to_thread(_read_identity, git, relative)
        except GitCommandError as exc:
            # R8: a git hiccup never aborts an index pass — blob-less rows
            # still give the branch its membership; the cache is skipped.
            log.warning(
                '{"event": "git_manifest_unavailable", "root": "%s", "error": "%s"}',
                project_root,
                exc,
            )
            branch, head, blobs = None, None, {}
        name = branch_display_name(branch, head)
        files = tuple(BranchFile(branch=name, path=p, blob_sha=blobs.get(p, "")) for p in relative)
        return BranchManifest(
            name=name,
            head_sha=head or "",
            source=BranchIndexSource.WORKING_TREE,
            pipeline_hash=self.pipeline_hash,
            files=files,
            worktree_path=str(project_root),
        )


__all__ = (
    "BranchManifest",
    "BranchManifestBuilder",
    "NoBranchManifestBuilder",
    "WorkingTreeManifestBuilder",
    "branch_display_name",
    "project_relative_path",
)
```

- [ ] **Step 6: Run the tests and the gate**

Run: `uv run --no-sync pytest tests/application/test_branch_manifest.py tests/extraction -q -k "extractor or end_to_end or protocols"`
Expected: PASS.
Run: `uv run --no-sync ruff format python/pydocs_mcp/application/branch_manifest.py python/pydocs_mcp/application/protocols.py python/pydocs_mcp/extraction/pipeline/chunk_extractor.py tests/_fakes.py tests/application/test_branch_manifest.py && uv run --no-sync ruff check python/ tests/ && uv run --no-sync mypy python/pydocs_mcp`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/application/branch_manifest.py python/pydocs_mcp/application/protocols.py python/pydocs_mcp/extraction/pipeline/chunk_extractor.py tests/_fakes.py tests/application/test_branch_manifest.py
git commit -m "feat(branches): working-tree manifest builder over discovered paths"
```

---

### Task 7: Membership, extraction cache, project GC — wired into `reindex_package`

**Files:**
- Create: `python/pydocs_mcp/application/branch_membership.py`
- Modify: `python/pydocs_mcp/application/indexing_service.py` (`ChunkDiffOutcome`; `_diff_merge_chunks`; `reindex_package`; `remove_package`), `python/pydocs_mcp/application/project_indexer.py`, `python/pydocs_mcp/storage/factories.py` (`build_project_indexer`)
- Test: `tests/application/test_branch_membership.py`

**Interfaces:**
- Produces: `ChunkDiffOutcome(removed_ids: tuple[int, ...], added_chunks: tuple[Chunk, ...], kept_assignments: tuple[tuple[Chunk, int], ...])`; `IndexingService._diff_merge_chunks(...) -> ChunkDiffOutcome` (no longer deletes anything); `IndexingService.reindex_package(..., branch_manifest: BranchManifest | None = None)`; `ProjectIndexer.manifest_builder: BranchManifestBuilder` (default `NoBranchManifestBuilder()`).
- Produces in `branch_membership.py`: `Assignment = tuple[Chunk, int]`; `membership_rows(manifest, assignments) -> tuple[ChunkMembership, ...]`; `extraction_rows(manifest, assignments, now) -> tuple[FileExtraction, ...]`; `async write_branch_membership(uow, *, manifest, assignments, now)`; `async write_file_extraction_cache(uow, *, manifest, assignments, now)`; `async collect_project_garbage(uow) -> tuple[int, ...]`; `async drop_all_branches(uow)`.
- Removal policy (spec §6.14 item 3): dependency packages, and any package indexed with `branch_manifest=None`, delete `removed_ids` and their vectors exactly as today; the project package with a manifest swaps membership and lets `collect_project_garbage` reclaim rows.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_branch_membership.py
"""Membership swap, extraction cache, project GC, and their wiring (spec §6.1, §6.3)."""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_mcp.application.branch_manifest import (
    BranchManifest,
    NoBranchManifestBuilder,
    WorkingTreeManifestBuilder,
)
from pydocs_mcp.application.branch_membership import (
    collect_project_garbage,
    extraction_rows,
    membership_rows,
    write_branch_membership,
    write_file_extraction_cache,
)
from pydocs_mcp.application.indexing_service import ChunkDiffOutcome, IndexingService
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchIndexSource, Chunk, Package, PackageOrigin
from pydocs_mcp.storage.branch_records import BranchFile
from tests._fakes import InMemoryChunkStore, SpyVectorStore, make_fake_uow_factory


def _chunk(title: str, path: str, start: int, end: int, package: str = PROJECT_PACKAGE_NAME) -> Chunk:
    return Chunk.from_test_inputs(
        package=package, module=path.replace("/", ".").removesuffix(".py"), title=title, text=title,
        metadata={"source_path": path, "start_line": start, "end_line": end},
    )


def _package(name: str = PROJECT_PACKAGE_NAME, origin: PackageOrigin = PackageOrigin.PROJECT) -> Package:
    return Package(name=name, version="0", summary="", homepage="", dependencies=(),
                   content_hash="h", origin=origin)


def _manifest(name: str = "main", files=(("pkg/a.py", "blob-a"), ("pkg/b.py", "blob-b"))) -> BranchManifest:
    return BranchManifest(
        name=name, head_sha="c" * 40, source=BranchIndexSource.WORKING_TREE, pipeline_hash="p",
        files=tuple(BranchFile(branch=name, path=p, blob_sha=b) for p, b in files),
        worktree_path="/repo",
    )


def test_membership_rows_carry_per_branch_spans() -> None:
    rows = membership_rows(_manifest(), ((_chunk("t", "pkg/a.py", 3, 9), 41),))
    assert [(r.branch, r.chunk_id, r.source_path, r.start_line, r.end_line) for r in rows] == [
        ("main", 41, "pkg/a.py", 3, 9)
    ]


def test_extraction_rows_group_spans_per_blob_and_skip_blank_blobs() -> None:
    manifest = _manifest(files=(("pkg/a.py", "blob-a"), ("pkg/n.py", "")))
    rows = extraction_rows(
        manifest,
        ((_chunk("x", "pkg/a.py", 1, 2), 1), (_chunk("y", "pkg/a.py", 3, 4), 2), (_chunk("z", "pkg/n.py", 1, 1), 3)),
        now=7.0,
    )
    assert len(rows) == 1
    assert (rows[0].blob_sha, rows[0].path, rows[0].pipeline_hash, rows[0].created_at) == ("blob-a", "pkg/a.py", "p", 7.0)
    assert json.loads(rows[0].chunk_spans) == [[1, 1, 2], [2, 3, 4]]


async def test_write_branch_membership_replaces_the_previous_working_tree_branch() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await write_branch_membership(uow, manifest=_manifest("old"), assignments=(), now=1.0)
        await write_branch_membership(
            uow, manifest=_manifest("main"), assignments=((_chunk("t", "pkg/a.py", 1, 2), 5),), now=2.0
        )
        assert [b.name for b in await uow.branches.list_branches()] == ["main"]
        assert await uow.branches.default_branch_name() == "main"
        assert await uow.branch_chunks.count_for_branch("old") == 0
        assert [m.chunk_id for m in await uow.branch_chunks.list_membership("main")] == [5]
        await uow.commit()


async def test_reindex_project_package_writes_membership_cache_and_collects_garbage() -> None:
    chunks_store = InMemoryChunkStore()
    vectors = SpyVectorStore()
    factory = make_fake_uow_factory(chunks=chunks_store, vectors=vectors)
    service = IndexingService(uow_factory=factory)
    keep, drop = _chunk("keep", "pkg/a.py", 1, 2), _chunk("drop", "pkg/b.py", 1, 2)
    await service.reindex_package(_package(), (keep, drop), (), branch_manifest=_manifest())
    drop_id = next(c.id for c in chunks_store.by_package[PROJECT_PACKAGE_NAME] if c.text == "drop")
    new = _chunk("new", "pkg/b.py", 1, 3)
    await service.reindex_package(_package(), (keep, new), (), branch_manifest=_manifest())
    async with factory() as uow:
        rows = await uow.branch_chunks.list_membership("main")
        assert sorted(m.source_path for m in rows) == ["pkg/a.py", "pkg/b.py"]
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", "p") is not None
        assert await uow.file_extractions.get("blob-b", "pkg/b.py", "p") is not None
        assert {c.text for c in chunks_store.by_package[PROJECT_PACKAGE_NAME]} == {"keep", "new"}
    assert drop_id in vectors.removed  # the orphan's vector was dropped by the GC path


async def test_dependency_package_keeps_direct_removal() -> None:
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)
    dep = _package("requests", PackageOrigin.DEPENDENCY)
    a, b = _chunk("a", "r/a.py", 1, 1, "requests"), _chunk("b", "r/b.py", 1, 1, "requests")
    await service.reindex_package(dep, (a, b), ())
    await service.reindex_package(dep, (a,), ())
    assert [c.text for c in chunks_store.by_package["requests"]] == ["a"]
    assert any(call.method == "delete_by_ids" for call in chunks_store.calls)


async def test_project_package_without_manifest_keeps_legacy_removal() -> None:
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)
    a, b = _chunk("a", "pkg/a.py", 1, 1), _chunk("b", "pkg/b.py", 1, 1)
    await service.reindex_package(_package(), (a, b), ())
    await service.reindex_package(_package(), (a,), ())
    assert [c.text for c in chunks_store.by_package[PROJECT_PACKAGE_NAME]] == ["a"]


async def test_diff_outcome_reports_kept_assignments() -> None:
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)
    kept = _chunk("kept", "pkg/a.py", 1, 1)
    await service.reindex_package(_package(), (kept,), ())
    async with factory() as uow:
        outcome = await service._diff_merge_chunks(
            uow, package_name=PROJECT_PACKAGE_NAME, incoming_chunks=(kept, _chunk("n", "pkg/n.py", 1, 1))
        )
    assert isinstance(outcome, ChunkDiffOutcome)
    assert [c.text for c, _ in outcome.kept_assignments] == ["kept"]
    assert [c.text for c in outcome.added_chunks] == ["n"] and outcome.removed_ids == ()


async def test_remove_project_package_drops_branch_rows() -> None:
    factory = make_fake_uow_factory()
    service = IndexingService(uow_factory=factory)
    await service.reindex_package(_package(), (_chunk("t", "pkg/a.py", 1, 1),), (), branch_manifest=_manifest())
    await service.remove_package(PROJECT_PACKAGE_NAME)
    async with factory() as uow:
        assert await uow.branches.list_branches() == ()
        assert await uow.branch_chunks.count_for_branch("main") == 0
        assert await collect_project_garbage(uow) == ()


def test_project_indexer_default_builder_is_the_null_object() -> None:
    from pydocs_mcp.application.project_indexer import ProjectIndexer

    assert ProjectIndexer.__dataclass_fields__["manifest_builder"].default_factory is NoBranchManifestBuilder


def test_factory_wires_the_working_tree_builder(tmp_path: Path) -> None:
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import build_project_indexer

    db = tmp_path / "p.db"
    open_index_database(db).close()
    bundle = build_project_indexer(AppConfig.load(), db, use_inspect=False, inspect_depth=None)
    assert isinstance(bundle.orchestrator.manifest_builder, WorkingTreeManifestBuilder)
    assert bundle.orchestrator.manifest_builder.pipeline_hash == bundle.pipeline_hash
```

`SpyVectorStore` does not exist in `tests/_fakes.py` yet (checked 2026-09-04); add it next to `NullVectorStore`'s usages, importing `NullVectorStore` is not needed — it is a standalone spy:

```python
@dataclass
class SpyVectorStore:
    """NullVectorStore that records ids passed to add/remove."""

    added: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)

    async def add_vectors(self, ids, vectors) -> None:
        self.added.extend(ids)

    async def remove_vectors(self, ids) -> None:
        self.removed.extend(ids)

    async def clear_all(self) -> None:
        self.added.clear()
        self.removed.clear()
```

(`InMemoryChunkStore.calls` records `_Call(method, payload)` entries — the assertion above reads `.method`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_branch_membership.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.application.branch_membership'`.

- [ ] **Step 3: Create `application/branch_membership.py`**

```python
"""Membership swap, extraction cache, and the project-scoped GC (spec §6.1).

Functions over an OPEN ``uow`` — called inside ``IndexingService.reindex_package``'s
transaction so membership, cache and GC commit atomically with the chunk diff
(spec §6.3 step 6). Kept out of ``indexing_service.py`` on purpose (§6.14 item 2).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydocs_mcp.models import Chunk, ChunkFilterField
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership, FileExtraction

if TYPE_CHECKING:
    from pydocs_mcp.application.branch_manifest import BranchManifest
    from pydocs_mcp.storage.protocols import UnitOfWork

Assignment = tuple[Chunk, int]


def _span(chunk: Chunk) -> tuple[str, int | None, int | None]:
    md = chunk.metadata
    return (
        str(md.get(ChunkFilterField.SOURCE_PATH.value) or ""),
        md.get(ChunkFilterField.START_LINE.value),
        md.get(ChunkFilterField.END_LINE.value),
    )


def membership_rows(
    manifest: BranchManifest, assignments: Sequence[Assignment]
) -> tuple[ChunkMembership, ...]:
    rows = []
    for chunk, chunk_id in assignments:
        path, start, end = _span(chunk)
        rows.append(ChunkMembership(manifest.name, chunk_id, path, start, end))
    return tuple(rows)


def extraction_rows(
    manifest: BranchManifest, assignments: Sequence[Assignment], now: float
) -> tuple[FileExtraction, ...]:
    """One cache row per file with a blob id; blank blobs (no git) are skipped."""
    blob_by_path = {f.path: f.blob_sha for f in manifest.files if f.blob_sha}
    spans: dict[str, list[list[int | None]]] = defaultdict(list)
    for chunk, chunk_id in assignments:
        path, start, end = _span(chunk)
        if path in blob_by_path:
            spans[path].append([chunk_id, start, end])
    return tuple(
        FileExtraction(blob_by_path[p], p, manifest.pipeline_hash, json.dumps(s), now)
        for p, s in spans.items()
    )


async def write_branch_membership(
    uow: UnitOfWork, *, manifest: BranchManifest, assignments: Sequence[Assignment], now: float
) -> None:
    """Stamp the branch, swap its manifest and membership, retire the previous
    working-tree branch of the same root (P0 keeps today's one-branch-per-checkout
    semantics; P1 replaces the retire step with the §6.8a retention policy)."""
    for other in await uow.branches.list_branches():
        if other.name != manifest.name and other.worktree_path == manifest.worktree_path:
            await uow.branch_chunks.delete_for_branch(other.name)
            await uow.branches.delete_branch(other.name)
    record = BranchRecord(
        name=manifest.name, head_sha=manifest.head_sha, source=manifest.source,
        pipeline_hash=manifest.pipeline_hash, indexed_at=now, last_used_at=now,
        is_default=True, worktree_path=manifest.worktree_path,
    )
    await uow.branches.upsert_branch(record)
    await uow.branches.replace_files(manifest.name, manifest.files)
    await uow.branch_chunks.replace_membership(manifest.name, membership_rows(manifest, assignments))


async def write_file_extraction_cache(
    uow: UnitOfWork, *, manifest: BranchManifest, assignments: Sequence[Assignment], now: float
) -> None:
    await uow.file_extractions.upsert_many(extraction_rows(manifest, assignments, now))


async def collect_project_garbage(uow: UnitOfWork) -> tuple[int, ...]:
    """Project chunks no branch references, then cache rows no manifest references."""
    removed = await uow.chunks.delete_unreferenced_project_chunks()
    await uow.file_extractions.delete_unreferenced()
    return removed


async def drop_all_branches(uow: UnitOfWork) -> None:
    """``remove_package('__project__')`` / ``clear_all`` cascade."""
    for record in await uow.branches.list_branches():
        await uow.branch_chunks.delete_for_branch(record.name)
        await uow.branches.delete_branch(record.name)
    await uow.file_extractions.delete_unreferenced()
```

- [ ] **Step 4: Refactor `_diff_merge_chunks` into a pure diff returning `ChunkDiffOutcome`**

In `indexing_service.py` add the imports (`PackageOrigin` to the `pydocs_mcp.models` import; `time`; the four `branch_membership` functions; `BranchManifest` under `TYPE_CHECKING`) and, next to `IndexingStats`:

```python
@dataclass(frozen=True, slots=True)
class ChunkDiffOutcome:
    """What the multiset diff decided (spec §6.14 item 3) — no writes performed."""

    removed_ids: tuple[int, ...]
    added_chunks: tuple[Chunk, ...]
    kept_assignments: tuple[tuple[Chunk, int], ...]
```

In `_diff_merge_chunks`: change the return annotation to `ChunkDiffOutcome`; replace `kept_chunks: list[Chunk] = []` with `kept_assignments: list[tuple[Chunk, int]] = []`; in the per-hash loop replace `kept_chunks.extend(incoming_for_hash[:keep_count])` with `kept_assignments.extend(zip(incoming_for_hash[:keep_count], existing_ids[:keep_count], strict=True))`; DELETE the `if removed_ids: … delete_by_ids … remove_vectors` block entirely; keep the span refresh but feed it `tuple(c for c, _ in kept_assignments)`; return `ChunkDiffOutcome(tuple(removed_ids), tuple(added_chunks), tuple(kept_assignments))`. Update its docstring's first line to "Compute the chunk diff (AC-3 + AC-8 + AC-9) — writes nothing; the caller applies the removal policy."

- [ ] **Step 5: Apply the removal policy and the branch stamp in `reindex_package`**

Add the parameter `branch_manifest: BranchManifest | None = None` after `project_root`. Replace the `_removed_ids, added_chunks = await self._diff_merge_chunks(...)` call and the `if added_chunks:` block with:

```python
            outcome = await self._diff_merge_chunks(
                uow, package_name=package.name, incoming_chunks=chunks
            )
            # Removal policy (spec §6.14 item 3): dependency packages, and any
            # package indexed without a branch manifest, delete removed rows
            # directly (today's behavior). The project package with a manifest
            # swaps membership and lets the project-scoped GC reclaim rows.
            if package.origin is PackageOrigin.DEPENDENCY or branch_manifest is None:
                await _drop_removed_chunks(uow, outcome.removed_ids)
```

(keep the `module_members.delete` / `packages.delete` / `packages.upsert` lines as they are), then:

```python
            added_ids: tuple[int, ...] = ()
            if outcome.added_chunks:
                added_ids = await uow.chunks.insert_returning_ids(outcome.added_chunks)
                await self._maybe_write_vectors(uow, package, outcome.added_chunks)
```

and, right before `await uow.commit()`:

```python
            if branch_manifest is not None:
                assignments = outcome.kept_assignments + tuple(
                    zip(outcome.added_chunks, added_ids, strict=True)
                )
                await self._stamp_branch(uow, branch_manifest, assignments)
```

Add two helpers (module-level function + method):

```python
async def _drop_removed_chunks(uow: UnitOfWork, removed_ids: tuple[int, ...]) -> None:
    if not removed_ids:
        return
    await uow.chunks.delete_by_ids(list(removed_ids))
    await uow.vectors.remove_vectors(list(removed_ids))
```

```python
    async def _stamp_branch(
        self, uow: UnitOfWork, manifest: BranchManifest, assignments: tuple[tuple[Chunk, int], ...]
    ) -> None:
        now = time.time()
        await write_branch_membership(uow, manifest=manifest, assignments=assignments, now=now)
        await write_file_extraction_cache(uow, manifest=manifest, assignments=assignments, now=now)
        removed = await collect_project_garbage(uow)
        if removed:
            await uow.vectors.remove_vectors(list(removed))
```

In `remove_package`, after the existing `references.delete_for_package` / `node_scores.delete_for_package` calls and before `packages.delete`, add:

```python
            if package_name == PROJECT_PACKAGE_NAME:
                await drop_all_branches(uow)
```

(`PROJECT_PACKAGE_NAME` is imported from `pydocs_mcp.models`.) Update the `reindex_package` docstring's "Canonical order" sentence to mention "→ branch stamp (membership, cache, project GC; project package with a manifest only)".

- [ ] **Step 6: Wire the builder through `ProjectIndexer` and the composition root**

`project_indexer.py`: import `BranchManifestBuilder, NoBranchManifestBuilder` from `pydocs_mcp.application.branch_manifest` and `field` from `dataclasses`; add the LAST field:

```python
    # The working-tree manifest builder (spec §6.3 step 1). The Null Object
    # default keeps existing callers and tests branch-free; the composition
    # root wires WorkingTreeManifestBuilder.
    manifest_builder: BranchManifestBuilder = field(default_factory=NoBranchManifestBuilder)
```

In `_index_project_source`, after the `existing … content_hash` early return and before `members = …`:

```python
        manifest = await self.manifest_builder.build(project_dir, result.discovered_paths)
```

and pass `branch_manifest=manifest,` to `self.indexing_service.reindex_package(...)`.

`storage/factories.py::build_project_indexer`: import `WorkingTreeManifestBuilder` from `pydocs_mcp.application.branch_manifest` and `git_repository_factory` from `pydocs_mcp.git.factory`; add to the `ProjectIndexer(...)` construction:

```python
        manifest_builder=WorkingTreeManifestBuilder(
            git_repository_for=git_repository_factory(config.git),
            pipeline_hash=pipeline_hash,
        ),
```

- [ ] **Step 7: Run the write-side suites and the gate**

Run: `uv run --no-sync pytest tests/application tests/storage tests/extraction tests/integration -q -x`
Expected: PASS. If a test asserted `_diff_merge_chunks` returned a 2-tuple, it now destructures `ChunkDiffOutcome` fields (`grep -rn "_diff_merge_chunks" tests` lists them).
Run: `uv run --no-sync ruff format python/pydocs_mcp/application python/pydocs_mcp/storage/factories.py tests/application/test_branch_membership.py tests/_fakes.py && uv run --no-sync ruff check python/ tests/ && uv run --no-sync mypy python/pydocs_mcp && uv run --no-sync complexipy python/pydocs_mcp --max-complexity-allowed 15 && uv run --no-sync vulture python/pydocs_mcp --min-confidence 80`
Expected: clean. `reindex_package` must stay under the complexity budget: if complexipy flags it, move the `outcome`/`added_ids` block into a `_persist_chunk_diff(uow, package, chunks, branch_manifest) -> tuple[ChunkDiffOutcome, tuple[int, ...]]` method — same statements, one call site.

- [ ] **Step 8: Commit**

```bash
git add python/pydocs_mcp/application/branch_membership.py python/pydocs_mcp/application/indexing_service.py python/pydocs_mcp/application/project_indexer.py python/pydocs_mcp/storage/factories.py tests/application/test_branch_membership.py tests/_fakes.py
git commit -m "feat(branches): membership swap, extraction cache and project GC inside reindex_package"
```

---

### Task 8: `meta.branch` on every response

**Files:**
- Modify: `python/pydocs_mcp/application/freshness.py` (`EnvelopeInfo.branch`, `IndexFreshnessProbe.read_default_branch`), `python/pydocs_mcp/application/envelope.py` (`_assemble_meta`), `python/pydocs_mcp/application/tool_response.py` (`MetaModel.branch`), `python/pydocs_mcp/storage/factories.py` (`build_freshness_probe`)
- Modify: `tests/application/test_response_envelope.py`, `tests/test_structured_envelope.py`, `tests/fixtures/goldens/mcp_registration_surface.json` (regenerated)
- Modify (gated): `docs/tool-contracts.md` (§2.4 + §6 row 8)
- Test: `tests/application/test_meta_branch.py`

**Interfaces:**
- Produces: `EnvelopeInfo.branch: str | None = None`; `IndexFreshnessProbe(read_default_branch: Callable[[], str | None] = lambda: None)`; `meta["branch"]` on every envelope (`None` when the probe has no info, the bundle predates v16, or the branch is the non-git sentinel); `MetaModel.branch: str | None = None`.
- Rendering rule (spec §6.7): the header line and every card are unchanged in P0 — only `meta` changes.

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_meta_branch.py
"""meta.branch (spec §6.7, contract §2.4): declared, sourced from the probe, null-safe."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydocs_mcp.application.envelope import ResponseEnvelope, _assemble_meta
from pydocs_mcp.application.freshness import EnvelopeInfo, IndexFreshnessProbe
from pydocs_mcp.application.tool_response import MetaModel, ReferencesMetaModel, SuggestionMetaModel
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import NON_GIT_BRANCH_NAME
from pydocs_mcp.storage.factories import build_freshness_probe, build_sqlite_uow_factory
from pydocs_mcp.storage.index_metadata import IndexMetadata


def _info(branch: str | None) -> EnvelopeInfo:
    return EnvelopeInfo(indexed_commit="a" * 40, live_commit="a" * 40, age_days=0,
                        package_count=1, stale=False, branch=branch)


def test_meta_model_declares_branch_on_every_variant() -> None:
    for model in (MetaModel, ReferencesMetaModel, SuggestionMetaModel):
        assert "branch" in model.model_fields
        assert model.model_fields["branch"].default is None


def test_assemble_meta_carries_branch_and_degrades_to_null() -> None:
    with_info = _assemble_meta(tool="get_overview", project="p", info=_info("feature/x"),
                               truncated=False, extras={})
    assert with_info["branch"] == "feature/x"
    no_info = _assemble_meta(tool="get_overview", project="p", info=None, truncated=False, extras={})
    assert no_info["branch"] is None
    assert set(no_info) == {"tool", "project", "indexed_git_head", "live_git_head",
                            "index_stale", "truncated", "branch"}


def test_probe_maps_the_non_git_sentinel_to_none() -> None:
    meta = IndexMetadata("p", "/p", "prov", "m", 3, "h", indexed_at=0.0, git_head="")
    probe = IndexFreshnessProbe(
        enabled=True, ttl_seconds=0.0, read_metadata=lambda: meta,
        resolve_live_head=lambda: None, count_packages=lambda: 1,
        read_default_branch=lambda: NON_GIT_BRANCH_NAME,
    )
    info = asyncio.run(probe.envelope_info())
    assert info is not None and info.branch is None


def test_probe_default_closure_is_null_safe() -> None:
    meta = IndexMetadata("p", "/p", "prov", "m", 3, "h", indexed_at=0.0)
    probe = IndexFreshnessProbe(enabled=True, ttl_seconds=0.0, read_metadata=lambda: meta,
                                resolve_live_head=lambda: None, count_packages=lambda: 1)
    assert asyncio.run(probe.envelope_info()).branch is None


def test_factory_probe_reads_the_default_branch_from_the_bundle(tmp_path: Path) -> None:
    from pydocs_mcp.models import BranchIndexSource
    from pydocs_mcp.storage.branch_records import BranchRecord
    from pydocs_mcp.storage.index_metadata import write_index_metadata

    db = tmp_path / "b.db"
    conn = open_index_database(db)
    write_index_metadata(conn, IndexMetadata("p", str(tmp_path), "prov", "m", 3, "h", 1.0))
    conn.close()

    async def _seed() -> None:
        async with build_sqlite_uow_factory(db)() as uow:
            await uow.branches.upsert_branch(BranchRecord(
                "feature/x", "a" * 40, BranchIndexSource.WORKING_TREE, "h", 1.0, 1.0, is_default=True,
            ))
            await uow.commit()

    asyncio.run(_seed())
    probe = build_freshness_probe(db_path=db, project_root=tmp_path, enabled=True, ttl_seconds=0.0)
    assert asyncio.run(probe.envelope_info()).branch == "feature/x"


def test_factory_probe_is_none_on_a_pre_v16_bundle(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), "
                 "project_name TEXT, project_root TEXT, embedding_provider TEXT, "
                 "embedding_model TEXT, embedding_dim INTEGER, pipeline_hash TEXT, "
                 "indexed_at REAL, git_head TEXT)")
    conn.execute("CREATE TABLE packages (name TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO index_metadata (id, project_name, indexed_at) VALUES (1, 'p', 1.0)")
    conn.commit()
    conn.close()
    probe = build_freshness_probe(db_path=db, project_root=tmp_path, enabled=True, ttl_seconds=0.0)
    assert asyncio.run(probe.envelope_info()).branch is None


async def test_envelope_text_is_unchanged_by_branch() -> None:
    class _Probe:
        async def envelope_info(self):
            return _info("feature/x")

    envelope = ResponseEnvelope(probe=_Probe(), surface="cli", pointers_enabled=False)

    async def _body():
        return "body"

    response = await envelope.wrap("get_overview", "p", _body)
    assert "feature/x" not in response.text  # P0 rendering rule: meta only
    assert response.meta["branch"] == "feature/x"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest tests/application/test_meta_branch.py -q`
Expected: FAIL — `TypeError: EnvelopeInfo.__init__() got an unexpected keyword argument 'branch'`.

- [ ] **Step 3: Extend the probe and the envelope**

`application/freshness.py`: import `NON_GIT_BRANCH_NAME` from `pydocs_mcp.models`; add `branch: str | None = None` as the LAST field of `EnvelopeInfo`; add to `IndexFreshnessProbe` after `now`:

```python
    # Spec §6.7 / §6.14 item 6: one more sync closure, the default branch name
    # from the ``branches`` table (None on a pre-v16 bundle). The non-git
    # sentinel renders as null — the contract's "not a git repository" value.
    read_default_branch: Callable[[], str | None] = lambda: None
```

and in `_compute`, build the info with `branch=self._branch()` where:

```python
    def _branch(self) -> str | None:
        name = self.read_default_branch()
        return None if name in (None, NON_GIT_BRANCH_NAME) else name
```

`application/envelope.py::_assemble_meta`: add `"branch": info.branch if info else None,` after `"index_stale"` in the `meta` dict (before `"truncated"`). `application/tool_response.py`: add `branch: str | None = None` to `MetaModel` with the docstring line "``branch`` (§2.4): the branch the answer came from; null for a non-git project or a pre-v16 bundle."

`storage/factories.py::build_freshness_probe`: add a closure and pass it:

```python
    def _read_default_branch() -> str | None:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT name FROM branches WHERE is_default = 1 ORDER BY indexed_at DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return None  # pre-v16 bundle, opened without migration on purpose
        finally:
            conn.close()
        return row[0] if row else None
```

```python
        read_default_branch=_read_default_branch,
```

- [ ] **Step 4: Update the pinned envelope tests and regenerate the golden**

- `tests/application/test_response_envelope.py::test_meta_maps_envelope_info_fields`: add `"branch": None` to the expected dict (the test's `EnvelopeInfo` fixture carries no branch). The positive case, `meta["branch"] == "feature/x"`, is already asserted by `test_meta_branch.py::test_envelope_text_is_unchanged_by_branch`, so nothing else changes in this module.
- `tests/test_structured_envelope.py`: add `"branch"` to the expected meta key set near line 111 and `assert meta["branch"] is None` next to the `index_stale` assertion in `test_structured_meta_contract_fields` (the handlers fixture has no branches row).
- Regenerate the registration golden: `uv run --no-sync python -c "import tests.test_mcp_registration_snapshot as t; t.write_golden()"`, then `git diff --stat tests/fixtures/goldens/` must show ONLY `outputSchema` additions of a `branch` property (inspect with `git diff tests/fixtures/goldens/mcp_registration_surface.json | grep '^[+-]' | grep -v branch` — expected: no other changed lines besides JSON punctuation).

- [ ] **Step 5: Amend the contract (gated)**

This step lands in the same commit as the code, but the PR must not merge before the owner ratifies spec §7 (ADR 0021 precedent: implementation and amendment travel together, ratification gates the merge). In `docs/tool-contracts.md`:

1. In §2.1's JSON block add, after `"index_stale"`: `"branch": "string | null — branch the answer came from (null when the project is not a git repository, or the bundle predates schema v16)",`.
2. After §2.3, add:

```markdown
### 2.4 The `meta.branch` field

Every tool carries `meta.branch: str | null` — the branch the answer came from,
following the §2.2 / §2.3 additive-extension precedent. It is sourced from the
`branches` table (schema v16) through the freshness probe; `null` when the project is
not a git repository, when the bundle predates v16, or when the probe is disabled.
Purely additive: names, parameters, items rows, and the text rendering are invariant
(amendment proposed by `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md`
§7; owner ratification pending).
```

3. In §6's table add row 8: `| 8 | \`meta.branch\` added to every tool (§2.4) | Added | Additive optional meta field (\`null\` for non-git projects). Text bytes unchanged. |`.

Then `uv run --no-sync pytest tests/test_doc_conformance.py tests/test_readme_jargon_audit.py -q` — Expected: PASS.

- [ ] **Step 6: Run the envelope suites and the gate**

Run: `uv run --no-sync pytest tests/application/test_meta_branch.py tests/application/test_response_envelope.py tests/test_structured_envelope.py tests/test_mcp_registration_snapshot.py tests/test_server_run_tool.py tests/storage/test_factories_freshness_probe.py -q`
Expected: PASS.
Run: `uv run --no-sync ruff format python/pydocs_mcp/application python/pydocs_mcp/storage/factories.py tests/application/test_meta_branch.py && uv run --no-sync ruff check python/ tests/ && uv run --no-sync mypy python/pydocs_mcp`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/application/freshness.py python/pydocs_mcp/application/envelope.py python/pydocs_mcp/application/tool_response.py python/pydocs_mcp/storage/factories.py tests/application/test_meta_branch.py tests/application/test_response_envelope.py tests/test_structured_envelope.py tests/fixtures/goldens/mcp_registration_surface.json docs/tool-contracts.md
git commit -m "feat(envelope): meta.branch on every response (contract §2.4, additive)"
```

---

### Task 9: `pydocs-mcp branches` — list the indexed branches

**Files:**
- Create: `python/pydocs_mcp/application/branch_listing.py`
- Modify: `python/pydocs_mcp/__main__.py` (parser + `_cmd_branches` + `_CMD_TABLE`)
- Test: `tests/test_cli_branches.py`

**Interfaces:**
- Produces: `BranchSummary(name, status, head_sha, indexed_at, is_default, file_count, chunk_count)`; `async list_branch_summaries(uow_factory) -> tuple[BranchSummary, ...]`; `format_branch_summaries(summaries, now) -> str`; CLI verb `pydocs-mcp branches [project] [--cache-dir DIR] [-v]`, exit 0 with a table, exit 1 with a hint when no bundle exists for the project.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_branches.py
"""The ``branches`` verb (spec §6.9, P0: list only)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application.branch_listing import (
    BranchSummary,
    format_branch_summaries,
    list_branch_summaries,
)
from pydocs_mcp.db import cache_path_for_project, open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchIndexSource, BranchStatus, Chunk
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord, ChunkMembership
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


def _seed(db: Path) -> None:
    open_index_database(db).close()

    async def _run() -> None:
        async with build_sqlite_uow_factory(db)() as uow:
            await uow.branches.upsert_branch(BranchRecord(
                "main", "c" * 40, BranchIndexSource.WORKING_TREE, "p", 100.0, 100.0, is_default=True,
            ))
            await uow.branches.replace_files("main", [BranchFile("main", "pkg/a.py", "b")])
            ids = await uow.chunks.insert_returning_ids((
                Chunk.from_test_inputs(package=PROJECT_PACKAGE_NAME, module="m", title="t", text="t"),
            ))
            await uow.branch_chunks.replace_membership("main", [ChunkMembership("main", ids[0], "pkg/a.py")])
            await uow.commit()

    asyncio.run(_run())


def test_list_branch_summaries_counts_files_and_chunks(tmp_path: Path) -> None:
    db = tmp_path / "b.db"
    _seed(db)
    summaries = asyncio.run(list_branch_summaries(build_sqlite_uow_factory(db)))
    assert summaries == (
        BranchSummary("main", BranchStatus.ACTIVE, "c" * 40, 100.0, True, 1, 1),
    )


def test_format_renders_one_line_per_branch() -> None:
    text = format_branch_summaries(
        (BranchSummary("main", BranchStatus.ACTIVE, "c" * 40, 100.0, True, 3, 42),), now=100.0 + 3 * 3600,
    )
    assert "main" in text and "ccccccc" in text and "3h" in text and "42" in text and "*" in text


def test_cli_lists_branches_for_a_project(tmp_path: Path, capsys, monkeypatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    db = cache_dir / cache_path_for_project(project).name
    _seed(db)
    code = _cli_main(["branches", str(project), "--cache-dir", str(cache_dir)])
    out = capsys.readouterr().out
    assert code == 0 and "main" in out and "ccccccc" in out


def test_cli_hints_when_no_index_exists(tmp_path: Path, capsys) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    code = _cli_main(["branches", str(project), "--cache-dir", str(tmp_path / "empty")])
    assert code == 1 and "pydocs-mcp index" in capsys.readouterr().out
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest tests/test_cli_branches.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.application.branch_listing'`.

- [ ] **Step 3: Create `application/branch_listing.py`**

```python
"""Read side of the ``branches`` CLI verb (spec §6.9): one summary per indexed branch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.models import BranchStatus
from pydocs_mcp.storage.protocols import UnitOfWork

_SHORT_SHA_LEN = 7
_HEADER = ("branch", "status", "head", "indexed", "files", "chunks")


@dataclass(frozen=True, slots=True)
class BranchSummary:
    name: str
    status: BranchStatus
    head_sha: str
    indexed_at: float
    is_default: bool
    file_count: int
    chunk_count: int


async def list_branch_summaries(
    uow_factory: Callable[[], UnitOfWork],
) -> tuple[BranchSummary, ...]:
    async with uow_factory() as uow:
        records = await uow.branches.list_branches()
        summaries = [
            BranchSummary(
                name=r.name, status=r.status, head_sha=r.head_sha, indexed_at=r.indexed_at,
                is_default=r.is_default,
                file_count=await uow.branches.count_files(r.name),
                chunk_count=await uow.branch_chunks.count_for_branch(r.name),
            )
            for r in records
        ]
    return tuple(summaries)


def _age_label(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def format_branch_summaries(summaries: tuple[BranchSummary, ...], now: float) -> str:
    """Plain-text table for the CLI; ``*`` marks the default branch."""
    rows = [_HEADER] + [
        (
            f"{'*' if s.is_default else ' '} {s.name}",
            s.status.value,
            s.head_sha[:_SHORT_SHA_LEN] or "-",
            f"{_age_label(max(0.0, now - s.indexed_at))} ago",
            str(s.file_count),
            str(s.chunk_count),
        )
        for s in summaries
    ]
    widths = [max(len(r[i]) for r in rows) for i in range(len(_HEADER))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in rows)


__all__ = ("BranchSummary", "format_branch_summaries", "list_branch_summaries")
```

- [ ] **Step 4: Add the verb to `__main__.py`**

In `_build_parser`, after the `link` parser block:

```python
    sp_branches = sub.add_parser(
        "branches",
        help="List the indexed branches of a project",
        description=(
            "One line per branch stamped in the project's index: name, status, head, age, "
            "file and chunk counts; '*' marks the default (checked-out) branch. Read-only."
        ),
    )
    sp_branches.add_argument("project", nargs="?", default=".")
    sp_branches.add_argument("--cache-dir", **_cache_dir)
    sp_branches.add_argument("-v", "--verbose", **_verbose)
```

Add the handler next to `_cmd_link`:

```python
def _cmd_branches(args: argparse.Namespace) -> int:
    """The ``branches`` verb (spec §6.9): list the branches stamped in the bundle."""
    import asyncio
    import time

    from pydocs_mcp.application.branch_listing import (
        format_branch_summaries,
        list_branch_summaries,
    )
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    project, db_path = _project_and_db(args)
    if not db_path.exists():
        print(f"branches: no index for {project} at {db_path}; run `pydocs-mcp index {project}`")
        return 1
    open_index_database(db_path).close()
    summaries = asyncio.run(list_branch_summaries(build_sqlite_uow_factory(db_path)))
    print(format_branch_summaries(summaries, now=time.time()))
    return 0
```

and `"branches": _cmd_branches,` in `_CMD_TABLE` after `"link"`.

- [ ] **Step 5: Run the tests and the gate**

Run: `uv run --no-sync pytest tests/test_cli_branches.py tests/test_cli.py tests/test_main_cli.py tests/test_doc_conformance.py -q`
Expected: PASS (`test_documented_cli_invocation_parses` only checks documented invocations; the verb is documented in Task 10).
Run: `uv run --no-sync ruff format python/pydocs_mcp/__main__.py python/pydocs_mcp/application/branch_listing.py tests/test_cli_branches.py && uv run --no-sync ruff check python/ tests/ && uv run --no-sync mypy python/pydocs_mcp && uv run --no-sync complexipy python/pydocs_mcp --max-complexity-allowed 15`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/__main__.py python/pydocs_mcp/application/branch_listing.py tests/test_cli_branches.py
git commit -m "feat(cli): pydocs-mcp branches lists the indexed branches"
```

---

### Task 10: End-to-end proof on a real repository, docs, and the full gate

**Files:**
- Test: `tests/integration/test_multi_branch_p0.py`
- Modify: `CHANGELOG.md` (under `## [0.6.0] — Unreleased` → `### Added`), `DOCUMENTATION.md` (a `### Branches` subsection next to the multi-repo / `link` material)

**Interfaces:**
- Consumes everything above through the public composition root (`build_project_indexer` + `run_index_pass`) and the CLI (`branches`).
- Proves spec AC-3 (bytes unchanged), AC-10 (v15 → v16 re-embeds nothing), AC-12 (no git), AC-13 (git failure never aborts a pass), and the P0 row of §10.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_multi_branch_p0.py
"""P0 end to end: index a real git checkout, verify the branch tables, re-run,
edit, switch branch — through the same composition root the CLI uses."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application import run_index_pass
from pydocs_mcp.db import cache_path_for_project, open_index_database
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import build_project_indexer

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x", "PATH": "/usr/bin:/bin"}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={**_ENV, "HOME": str(root)})


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "a.py").write_text('def alpha():\n    """A."""\n    return 1\n', encoding="utf-8")
    (root / "pkg" / "b.py").write_text('def beta():\n    """B."""\n    return 2\n', encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _index(root: Path, db: Path, config: AppConfig) -> None:
    bundle = build_project_indexer(config, db, use_inspect=False, inspect_depth=None)
    asyncio.run(run_index_pass(
        orchestrator=bundle.orchestrator, indexing_service=bundle.indexing_service,
        pipeline_hash=bundle.pipeline_hash, project=root,
        embedding_provider=config.embedding.provider, embedding_model=config.embedding.model_name,
        embedding_dim=config.embedding.dim, force=False, include_project_source=True,
        include_dependencies=False, workers=1, check_integrity=bundle.check_integrity,
        rebuild_fts=bundle.rebuild_fts, stamp_metadata=bundle.stamp_metadata,
        write_aggregates=bundle.write_aggregates,
    ))


def _rows(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def test_first_pass_stamps_branch_manifest_membership_and_cache(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    _index(root, db, AppConfig.load())
    assert _rows(db, "SELECT name, is_default, source, length(head_sha) FROM branches") == [
        ("main", 1, "working_tree", 40)
    ]
    files = dict(_rows(db, "SELECT path, length(blob_sha) FROM branch_files"))
    assert files == {"pkg/__init__.py": 40, "pkg/a.py": 40, "pkg/b.py": 40}
    project_chunks = _rows(db, f"SELECT COUNT(*) FROM chunks WHERE package='{PROJECT_PACKAGE_NAME}'")[0][0]
    assert _rows(db, "SELECT COUNT(*) FROM branch_chunks WHERE branch='main'")[0][0] == project_chunks > 0
    assert _rows(db, "SELECT COUNT(*) FROM file_extractions")[0][0] >= 2  # a.py and b.py carry chunks


def test_unchanged_pass_is_cached_and_edit_updates_membership(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    before = _rows(db, "SELECT chunk_id, source_path, start_line FROM branch_chunks ORDER BY 1")
    _index(root, db, config)
    assert _rows(db, "SELECT chunk_id, source_path, start_line FROM branch_chunks ORDER BY 1") == before

    (root / "pkg" / "b.py").write_text('def gamma():\n    """G."""\n    return 3\n', encoding="utf-8")
    _index(root, db, config)
    titles = {r[0] for r in _rows(db, f"SELECT title FROM chunks WHERE package='{PROJECT_PACKAGE_NAME}'")}
    assert any("gamma" in t for t in titles) and not any("beta" in t for t in titles)
    blobs = dict(_rows(db, "SELECT path, blob_sha FROM branch_files"))
    assert _rows(db, f"SELECT COUNT(*) FROM file_extractions WHERE path='pkg/b.py' AND blob_sha='{blobs['pkg/b.py']}'") == [(1,)]


def test_switching_branch_replaces_the_working_tree_branch_record(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    _git(root, "checkout", "-q", "-b", "feature/x")
    (root / "pkg" / "c.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "feature")
    _index(root, db, config)
    assert _rows(db, "SELECT name FROM branches") == [("feature/x",)]  # P0: one branch per checkout
    assert _rows(db, "SELECT COUNT(*) FROM branch_chunks WHERE branch='main'") == [(0,)]


def test_non_git_project_uses_the_sentinel_branch(tmp_path: Path) -> None:
    root, db = tmp_path / "plain", tmp_path / "p.db"
    (root / "m").mkdir(parents=True)
    (root / "m" / "x.py").write_text("def x():\n    return 0\n", encoding="utf-8")
    _index(root, db, AppConfig.load())
    assert _rows(db, "SELECT name, head_sha FROM branches") == [(NON_GIT_BRANCH_NAME, "")]
    assert _rows(db, "SELECT DISTINCT blob_sha FROM branch_files") == [("",)]
    assert _rows(db, "SELECT COUNT(*) FROM file_extractions") == [(0,)]


def test_cli_branches_lists_the_stamped_branch(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    _index(root, cache / cache_path_for_project(root).name, AppConfig.load())
    assert _cli_main(["branches", str(root), "--cache-dir", str(cache)]) == 0
    assert "main" in capsys.readouterr().out


def test_v15_bundle_upgrade_re_extracts_once_without_re_embedding(tmp_path: Path, monkeypatch) -> None:
    from tests.test_db_schema_v16_migration import _V15_SCRIPT  # the v15 fixture script

    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    vectors_before = _rows(db, "SELECT COUNT(*) FROM chunks WHERE embedded=1")[0][0]
    # Downgrade the stamp only: the tables exist, but a 15-stamped open must
    # clear __project__'s content_hash and re-extract (spec §6.1 migration).
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 15")
    conn.commit()
    conn.close()
    open_index_database(db).close()
    assert _rows(db, "SELECT content_hash FROM packages WHERE name='__project__'") == [(None,)]
    _index(root, db, config)
    assert _rows(db, "SELECT COUNT(*) FROM chunks WHERE embedded=1")[0][0] == vectors_before
    assert _rows(db, "SELECT COUNT(*) FROM branch_chunks")[0][0] > 0
    assert _V15_SCRIPT  # keeps the import meaningful for the linter
```

- [ ] **Step 2: Run it to verify the state before docs**

Run: `uv run --no-sync pytest tests/integration/test_multi_branch_p0.py -q`
Expected: PASS (Tasks 1–9 make it green; a failure here is a wiring gap — fix in the owning task's files, never by loosening the test). If it passes on the first run, that is the expected outcome of an integration test written after its units.

- [ ] **Step 3: Document**

`CHANGELOG.md`, under `## [0.6.0] — Unreleased` → `### Added`, first bullet:

```markdown
- **Branch dimension, foundation (schema v16)** — every project index now stamps the
  checked-out branch (`branches`), its file manifest with git blob ids (`branch_files`),
  chunk membership with per-branch spans (`branch_chunks`), and a blob-keyed extraction
  cache (`file_extractions`); project chunks no branch references are garbage-collected
  with their vectors. Every tool response carries an additive `meta.branch` field
  (`null` for non-git projects). New verb: `pydocs-mcp branches` lists the indexed
  branches. Git is optional: without a `git` binary or repository, behavior is unchanged
  except for one `git_unavailable` log. Schema v15 → v16 is an additive in-place
  migration; the first index pass after upgrading re-extracts the project package once
  to populate the new tables and re-embeds nothing (chunk content hashes are unchanged).
  Text output of every tool is byte-identical. Design:
  `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` (P0).
```

`DOCUMENTATION.md`: find the multi-repo section (`grep -n "^## \|^### " DOCUMENTATION.md | grep -i "multi-repo\|link"`) and add after it:

```markdown
### Branches (foundation)

Every index pass stamps the checked-out branch: `pydocs-mcp branches .` lists the
branches recorded in a project's bundle with their head, age, and file/chunk counts
(`*` marks the default branch), and every MCP/CLI response carries `meta.branch`
(`null` when the project is not a git repository). Git is optional and read-only —
see the `git:` block in `python/pydocs_mcp/defaults/default_config.yaml` for
`enabled` (`auto` | `on` | `off`), `binary`, and `timeout_seconds`. Indexing several
branches, the `branch` selector, and diff-scoped search follow in the next release
train (`docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md`).
```

Run: `uv run --no-sync pytest tests/test_doc_conformance.py tests/test_readme_jargon_audit.py -q` — Expected: PASS (the documented `pydocs-mcp branches .` invocation must parse).

- [ ] **Step 4: Run the full CI gate**

```bash
uv run --no-sync ruff format --check python/ tests/ benchmarks/
uv run --no-sync ruff check python/ tests/ benchmarks/
uv run --no-sync mypy python/pydocs_mcp
uv run --no-sync complexipy python/pydocs_mcp --max-complexity-allowed 15
uv run --no-sync vulture python/pydocs_mcp --min-confidence 80
uv run --no-sync pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90 -q
uv run --no-sync --with-editable benchmarks pytest benchmarks/tests/ -q
uv lock --check
```

Expected: every command exits 0. (No Rust files changed, so the `cargo` jobs are unaffected.)

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_multi_branch_p0.py CHANGELOG.md DOCUMENTATION.md
git commit -m "test(branches): P0 end-to-end over a real checkout; changelog + docs"
```

---

## Acceptance mapping (P0 slice of spec §9)

| Spec AC | Proven by |
|---|---|
| AC-3 byte identity | `tests/test_structured_envelope.py::test_text_content_block_is_byte_identical_to_golden` (unchanged golden text), `test_meta_branch.py::test_envelope_text_is_unchanged_by_branch` |
| AC-10 v15 → v16 | `test_db_schema_v16_migration.py`, `test_multi_branch_p0.py::test_v15_bundle_upgrade_re_extracts_once_without_re_embedding` |
| AC-12 no git | `test_multi_branch_p0.py::test_non_git_project_uses_the_sentinel_branch`, `test_config_git_block.py` |
| AC-13 bounded git failure | `test_git_subprocess_repository.py::test_failures_become_git_command_error`, `test_branch_manifest.py::test_builder_degrades_and_logs_when_git_fails` |
| AC-14 docs (P0 part) | Task 8 step 5, Task 10 step 3 |
| P0 row of §10 | Tasks 1–9 |

## Plan self-review (done at authoring time)

- **Spec coverage.** §6.1 v16 tables/index/migration → Task 4; project-scoped GC → Tasks 5/7; §6.2 P0 port subset + Null/Subprocess adapters + creator function → Task 3; §6.3 step 1 (manifest, blob ids from git's index, hash only dirty files) → Task 6; step 6 atomicity → Task 7 (all writes inside `reindex_package`'s transaction); §6.7 `meta.branch` + rendering rule → Task 8; §6.9 `git:` block + `branches` verb → Tasks 3/9; §6.11 no-git and timeout rows → Tasks 3/6; §6.12 migration/bytes tests → Tasks 4/8/10; §6.13/§6.14 module map and boundaries → the File structure table. Deferred by design to P1: blob-cache *reads*, tree/member/sweep JSON population, v17 branch columns, `branch` parameter, ref watcher.
- **Placeholders.** None: every step carries its code or its exact command; the two "if the tool flags it" notes name the concrete refactor to apply.
- **Type consistency.** `insert_returning_ids` / `delete_unreferenced_project_chunks` (Task 5) are the names Task 7 calls; `ChunkDiffOutcome` fields (`removed_ids`, `added_chunks`, `kept_assignments`) match Task 7's tests; `WorkingTreeManifestBuilder(git_repository_for=…, pipeline_hash=…)` (Task 6) is what Task 7 wires in `factories.py`; `read_default_branch` (Task 8) matches the probe test; `GitConfig` / `GitEnablement` / `git_repository_factory` (Task 3) are what Tasks 6–7 import; `BranchRecord` positional order `(name, head_sha, source, pipeline_hash, indexed_at, last_used_at, …)` is used consistently in Tasks 5, 8, 9.
