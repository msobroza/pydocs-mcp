"""One indexed workspace per corpus, built once and shared by BOTH arms.

A before/after run answers one split under two product commits. The split's
tasks do not all ask about the same repository: ``repoqa-qa/small_test`` carries
30 questions over 10 distinct ``(repo, commit)`` corpora, and a RepoQA corpus is
not a git checkout at all — each task ships its own file set, which
``EvalTask.corpus_source()`` materializes on demand. Pointing every task at one
``--workspace`` therefore made every question search an index that did not
contain its repository, which measures nothing about the change under test.

This module is the preflight and the build for that: it groups a split's tasks
by ``(repo, commit)``, materializes each corpus once, indexes it once with the
run's own serving config, and assembles one served bundle directory per corpus.

**Both arms are given the SAME bundle directories**, built once by the launching
checkout's product — never once per arm. That is exactly what a before/after
wants: the two arms then retrieve from byte-identical indexes, so a difference
in the report comes from the commit under test (the tool descriptions the agent
reads, the responses the server renders) and never from two arms having indexed
the corpus differently.

The bundle is a COPY of the canonical build, for the reason
``index_cache.preseed_workspace`` documents: the product opens a ``.db``
read-write under ``journal_mode=WAL`` at serve time, so a serve child must never
hold the canonical bytes every later run re-seeds from.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.campaign.index_cache import (
    copy_index_file,
    index_checkout,
    repo_slug,
)
from pydocs_eval.datasets.base_dataset import EvalTask

# Where the per-corpus directories live under the operator's ``--workspace``.
# One named subtree, so a bundle home the operator also uses for hand-built
# indexes gains exactly one directory and ``discover_workspace``'s non-recursive
# ``*.db`` glob over that home keeps seeing only what was already there.
TASK_WORKSPACE_ROOT_NAME = "task-workspaces"
_SOURCES_DIR = "sources"
_BUNDLES_DIR = "bundles"
_INDEX_CACHE_DIR = "index-cache"

# Task metadata keys that name a corpus. A task carrying neither keeps the
# shared ``--workspace`` — that is the pre-existing behavior, unchanged.
_REPO_KEY = "repo"
_COMMIT_KEY = "commit"

_COMMIT_CHARS = 12

# The estimate's only assumption: four source bytes per embedded token. Printed
# beside the figure, never measured — the plan must spend nothing to produce it.
_BYTES_PER_EMBED_TOKEN = 4
DEFAULT_USD_PER_1M_EMBED = 0.0


class CorpusWorkspaceError(Exception):
    """A task workspace the operator must fix before the run can measure anything."""


@dataclass(frozen=True, slots=True)
class IndexIdentity:
    """What makes a built bundle usable by THIS run's serving config.

    ``scope_id`` is the product's ingestion-pipeline identity (see
    ``index_cache.resolve_scope_id``): it folds the embedder, the ingestion
    config and the extension scope, so two configs that would build different
    indexes get different directories instead of silently reusing one another's.
    ``embedder_model`` / ``embedder_dim`` are then checked against what a bundle
    actually carries, which catches a hand-placed or half-written one too.
    """

    embedder_model: str
    embedder_dim: int
    scope_id: str


@dataclass(frozen=True, slots=True)
class CorpusWorkspace:
    """One corpus: where its files, its canonical index and its bundle live."""

    repo: str
    commit: str
    task_ids: tuple[str, ...]
    source_dir: Path
    index_dir: Path
    bundle_dir: Path
    source_bytes: int
    is_built: bool

    @property
    def embed_tokens(self) -> int:
        """The estimate the plan prints — source bytes over a fixed divisor."""
        return self.source_bytes // _BYTES_PER_EMBED_TOKEN


@dataclass(frozen=True, slots=True)
class TaskWorkspaces:
    """Which workspace every task searches, and what is left to build.

    ``shared_task_ids`` are the tasks that carry no corpus coordinates; they
    search ``shared_workspace`` (the ``--workspace`` directory itself), which is
    what every task did before this module existed.
    """

    root: Path
    shared_workspace: Path
    corpora: tuple[CorpusWorkspace, ...] = ()
    shared_task_ids: tuple[str, ...] = ()
    usd_per_1m_embed: float = DEFAULT_USD_PER_1M_EMBED

    @property
    def missing(self) -> tuple[CorpusWorkspace, ...]:
        """The corpora whose bundle is not built (or not valid) for this config."""
        return tuple(corpus for corpus in self.corpora if not corpus.is_built)

    @property
    def ready_count(self) -> int:
        return len(self.corpora) - len(self.missing)

    @property
    def missing_embed_tokens(self) -> int:
        return sum(corpus.embed_tokens for corpus in self.missing)

    @property
    def missing_embed_usd(self) -> float:
        """Dollars the missing builds are estimated to cost; ``0.0`` with no price."""
        return self.missing_embed_tokens / 1_000_000 * self.usd_per_1m_embed

    def as_map(self) -> dict[str, str]:
        """``task_id -> bundle directory`` for every task that has its own corpus."""
        return {
            task_id: str(corpus.bundle_dir)
            for corpus in self.corpora
            for task_id in corpus.task_ids
        }

    def workspace_for(self, task_id: str) -> Path:
        """The directory ``task_id`` searches — its own corpus, else the shared one."""
        own = self.as_map().get(task_id)
        return Path(own) if own is not None else self.shared_workspace

    def preflight_lines(self) -> list[str]:
        """What the plan prints about the corpora — free, and spent nothing."""
        if not self.corpora:
            return [
                "corpora:    no task carries (repo, commit) coordinates — every task "
                f"searches {self.shared_workspace} directly"
            ]
        return [
            f"corpora:    {len(self.corpora)} distinct (repo, commit) corpus(es) over "
            f"{len(self.as_map())} task(s) — {self.ready_count} ready, "
            f"{len(self.missing)} to build",
            f"            bundles under {self.root / _BUNDLES_DIR}",
            *self._estimate_lines(),
        ]

    def _estimate_lines(self) -> list[str]:
        if not self.missing:
            return ["            every workspace is built and valid for this config"]
        return [
            f"            ~{self.missing_embed_tokens} embedding token(s) to build "
            f"(source bytes / {_BYTES_PER_EMBED_TOKEN}) "
            f"≈ ${self.missing_embed_usd:.2f}{self._price_note()}",
            "            --confirm-spend REFUSES to start an arm while a workspace is "
            "missing; add --build-indexes to build them first",
        ]

    def _price_note(self) -> str:
        if self.usd_per_1m_embed:
            return f" at ${self.usd_per_1m_embed}/1M embedding tokens"
        return " — no price given; pass --usd-per-1m-embed"


def plan_task_workspaces(
    tasks: Sequence[EvalTask],
    *,
    workspace: Path,
    identity: IndexIdentity,
    usd_per_1m_embed: float = DEFAULT_USD_PER_1M_EMBED,
    materialize: Callable[[EvalTask], Path] = lambda task: task.corpus_source(),
) -> TaskWorkspaces:
    """Group ``tasks`` by corpus and report which bundles still have to be built.

    Materializing a corpus is free — the split's rows already carry the file
    set — so it happens here, at plan time, into the same directory the build
    will index. That keeps the token estimate honest (it is measured off the
    real files) and makes a later ``--build-indexes`` pass a pure index step.

    Raises:
        CorpusWorkspaceError: a task names a malformed repo, or a bundle on disk
            was built with a different embedder than this config uses.
    """
    root = workspace / TASK_WORKSPACE_ROOT_NAME
    grouped = _group_by_corpus(tasks)
    corpora = tuple(
        _corpus_workspace(root, key, group, identity, materialize)
        for key, group in sorted(grouped.items())
    )
    return TaskWorkspaces(
        root=root,
        shared_workspace=workspace,
        corpora=corpora,
        shared_task_ids=tuple(task.task_id for task in tasks if _corpus_key(task) is None),
        usd_per_1m_embed=usd_per_1m_embed,
    )


def build_missing_workspaces(
    workspaces: TaskWorkspaces,
    *,
    python: Path,
    config: Path,
    index_fn: Callable[[Path, Path], tuple[Path, Path]] | None = None,
) -> tuple[Path, ...]:
    """Index every corpus that has no bundle yet, then assemble its bundle.

    ``config`` is the run's serving YAML: the index MUST be built with the same
    embedder the arms will serve under, or ``validate_project_embedder`` refuses
    the bundle at serve time. ``index_fn`` is the execution seam
    (``index_checkout``'s), so tests build a marker bundle with no model.

    Returns the bundle directories it built, in build order.
    """
    built: list[Path] = []
    for corpus in workspaces.missing:
        db, tq = index_checkout(
            corpus.source_dir,
            python=python,
            cache_root=corpus.index_dir,
            config=config,
            index_fn=index_fn,
        )
        _assemble_bundle(db, tq, corpus.bundle_dir)
        built.append(corpus.bundle_dir)
    return tuple(built)


def _assemble_bundle(db: Path, tq: Path, bundle_dir: Path) -> None:
    """Copy one canonical ``.db``/``.tq`` pair into the directory a serve reads.

    ``serve --workspace <dir>`` loads every ``*.db`` directly under ``<dir>``
    (``multirepo.discover_workspace``) and derives each one's ``.tq`` from its
    stem, so the pair keeps its filename. Copies, never hardlinks — see the
    module docstring and ``index_cache.preseed_workspace``.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    copy_index_file(db, bundle_dir / db.name)
    if tq.exists():
        copy_index_file(tq, bundle_dir / tq.name)


def _group_by_corpus(tasks: Sequence[EvalTask]) -> dict[tuple[str, str], list[EvalTask]]:
    """``(repo, commit) -> tasks``, in dataset order within each corpus."""
    grouped: dict[tuple[str, str], list[EvalTask]] = {}
    for task in tasks:
        key = _corpus_key(task)
        if key is not None:
            grouped.setdefault(key, []).append(task)
    return grouped


def _corpus_key(task: EvalTask) -> tuple[str, str] | None:
    """``(repo, commit)`` for a task that names a corpus, else ``None``."""
    repo = task.metadata.get(_REPO_KEY, "")
    commit = task.metadata.get(_COMMIT_KEY, "")
    return (repo, commit) if repo and commit else None


def _corpus_workspace(
    root: Path,
    key: tuple[str, str],
    group: Sequence[EvalTask],
    identity: IndexIdentity,
    materialize: Callable[[EvalTask], Path],
) -> CorpusWorkspace:
    repo, commit = key
    slug = _corpus_slug(repo, commit, group[0].task_id)
    source_dir = root / _SOURCES_DIR / slug
    bundle_dir = root / _BUNDLES_DIR / f"{slug}@{identity.scope_id}"
    _ensure_corpus_source(group[0], source_dir, materialize)
    return CorpusWorkspace(
        repo=repo,
        commit=commit,
        task_ids=tuple(task.task_id for task in group),
        source_dir=source_dir,
        index_dir=root / _INDEX_CACHE_DIR / identity.scope_id,
        bundle_dir=bundle_dir,
        source_bytes=_directory_bytes(source_dir),
        is_built=bundle_is_ready(bundle_dir, identity),
    )


def _corpus_slug(repo: str, commit: str, task_id: str) -> str:
    """``owner/name`` + commit → one directory name, ``owner__name@<sha12>``."""
    try:
        return f"{repo_slug(repo)}@{commit[:_COMMIT_CHARS]}"
    except ValueError as exc:
        raise CorpusWorkspaceError(f"task {task_id!r} names an unusable corpus: {exc}") from exc


def _ensure_corpus_source(task: EvalTask, source_dir: Path, materialize) -> None:
    """Materialize the corpus into ``source_dir`` unless it is already there.

    ``corpus_source()`` writes to a fresh temp dir it hands ownership of, so the
    files are MOVED into the canonical directory: the index path the product
    derives its cache slug from then stays stable across runs, which is what
    lets a second pass skip the build entirely.
    """
    if source_dir.is_dir() and any(source_dir.iterdir()):
        return
    produced = materialize(task)
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    if source_dir.exists():
        source_dir.rmdir()  # empty by the guard above; move would nest inside it
    shutil.move(str(produced), str(source_dir))


def _directory_bytes(source_dir: Path) -> int:
    return sum(path.stat().st_size for path in source_dir.rglob("*") if path.is_file())


def bundle_is_ready(bundle_dir: Path, identity: IndexIdentity) -> bool:
    """Is ``bundle_dir`` already serving an index this run's config can use?

    Uses the product's own loader and embedder check, so the answer is the one
    the serve child will reach — an absent or empty directory is simply "not
    built", while a bundle built with ANOTHER embedder is an error the operator
    has to see (its message carries the model and dimension found on disk and
    the ones this config expects).

    Raises:
        CorpusWorkspaceError: the directory holds a bundle this config cannot
            serve.
    """
    from pydocs_mcp.multirepo import (
        EmbedderMismatchError,
        discover_workspace,
        validate_project_embedders,
    )

    try:
        projects = discover_workspace(bundle_dir)
    except (FileNotFoundError, ValueError):
        return False
    try:
        validate_project_embedders(
            projects, model=identity.embedder_model, dim=identity.embedder_dim
        )
    except EmbedderMismatchError as exc:
        raise CorpusWorkspaceError(
            f"the workspace {bundle_dir} cannot serve this run: {exc}"
        ) from exc
    return True
