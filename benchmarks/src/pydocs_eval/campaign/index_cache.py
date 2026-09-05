"""Canonical-checkout project-index cache (ADR 0014 §Decision 3, item 2-3).

The PROJECT index is a pure function of ``(repo files at base_commit, embedder +
ingestion config)`` but the product cache key is path-based
(``dirname + md5(abs_path)[:10]``, ``db.cache_path_for_project``). So rather than
change the product, the runner makes the *path* canonical: one pristine checkout
per ``(repo, base_commit, scope_id)`` at
``<cache_root>/<repo_slug>@<commit>@<scope_id>/``, indexed ONCE with
``--skip-deps --no-inspect``. The ``scope_id`` component (ADR 0021 6) rides the
checkout PATH because the product derives the db name purely from that path
(``cache_path_for_project``), so multilang-on vs -off need distinct paths to get
distinct ``.db`` slots — otherwise ``index_checkout``'s ``db.exists()``
short-circuit would silently reuse a Python-only index for a multilang cell. The
path-derived key is then stable, and
before a rollout's serve starts the runner pre-seeds that rollout workspace's own
cache slot by COPYING the ``.db``/``.tq`` pair to the workspace-path-derived key —
computed by calling the product's ``cache_path_for_project``, never by re-deriving
the hash (ADR 0014 §Consequences "rides an implementation detail" — the pin test
guards it). The pre-seed COPIES rather than hardlinks: the product opens the
``.db`` read-write under ``journal_mode=WAL`` (``db.py`` ``_connect_or_recreate``),
so a hardlinked slot shared across rollouts would let one rollout's in-place WAL
write-back mutate the shared canonical bytes and poison every sibling — a
structural hazard, not a corner case (money-review finding 2).

``pydocs_mcp`` is imported function-locally (like the agent-track adapter) so the
eval base-install floor stays intact. The subprocess index path is the shipped
product behavior; :func:`index_project_in_process` is the same product indexer
driven in-process, the offline-testable seam that needs no ``claude`` and no
model download when the caller mocks ``build_embedder``.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

# The exact index flags ADR 0014 pins: project-only (``--skip-deps``), no
# import side effects (``--no-inspect``). Single source of truth so a flag
# rename is one edit and the pin test asserts this list verbatim.
_INDEX_FLAGS = ("--skip-deps", "--no-inspect")
_SERVE_MODULE = ("-m", "pydocs_mcp", "index")

# Hex chars of the product pipeline hash used as the default scope-identity slug
# component (ADR 0021 6). Long enough that on/off scopes never collide; short
# enough to keep the checkout dir name readable.
_SCOPE_ID_LEN = 16


def repo_slug(repo: str) -> str:
    """``owner/name`` → ``owner__name`` (SWE-bench dir convention, no slashes).

    Raises on a slug with no ``/`` — a malformed repo cannot name a checkout dir.

    Example:
        >>> repo_slug("conan-io/conan")
        'conan-io__conan'
    """
    if "/" not in repo:
        raise ValueError(f"invalid repo slug: got {repo!r}, expected 'owner/name'")
    return repo.replace("/", "__")


def resolve_scope_id(scope_id: str | None) -> str:
    """Return ``scope_id`` verbatim, or derive it from the active product config.

    The canonical index slot MUST differ whenever the built index would differ —
    above all across the multilang extension-scope fold (ADR 0021 7a), so
    ``index_checkout``'s ``db.exists()`` short-circuit can never reuse a
    Python-only index for a multilang cell. The default reads the SAME
    ``AppConfig.load()`` surface the index subprocess resolves (env-driven), so
    the runner's slug matches what the build produces; a truncated pipeline hash
    is the cheapest honest identity that already folds embedder + backend + scope
    (ADR 0021 6). ``pydocs_mcp`` stays a function-local import (eval floor).
    """
    if scope_id is not None:
        return scope_id
    from pydocs_mcp.retrieval.config import AppConfig

    return AppConfig.load().ingestion_pipeline_hash[:_SCOPE_ID_LEN]


def canonical_checkout_dir(
    cache_root: Path, repo: str, commit: str, *, scope_id: str | None = None
) -> Path:
    """The pristine-checkout path for ``(repo, commit)`` at one index scope:
    ``<root>/<slug>@<commit>@<scope_id>/``.

    The ``scope_id`` component (ADR 0021 6/7b) separates multilang-on and -off
    index slots. It MUST ride the checkout PATH — not the ``.db`` name — because
    the product derives the db name purely from the checkout path
    (``cache_path_for_project``), so two scopes need two distinct checkout paths
    to get two distinct db slots (both buildable side by side). The cost is a
    per-scope re-clone of the same source; correctness over disk. ``scope_id``
    defaults to the active product pipeline identity.
    """
    if not commit:
        raise ValueError(f"commit must be a non-empty sha, got {commit!r}")
    return cache_root / f"{repo_slug(repo)}@{commit}@{resolve_scope_id(scope_id)}"


def build_index_command(checkout_dir: Path, python: Path, cache_root: Path) -> list[str]:
    """The exact ``<python> -m pydocs_mcp index <dir> --skip-deps --no-inspect
    --cache-dir <root>`` argv (ADR 0014 item 2). Pin-tested for flag drift.

    Example:
        >>> build_index_command(Path("/c/r@a"), Path("/py"), Path("/c"))[:5]
        ['/py', '-m', 'pydocs_mcp', 'index', '/c/r@a']
    """
    return [
        str(python),
        *_SERVE_MODULE,
        str(checkout_dir),
        *_INDEX_FLAGS,
        "--cache-dir",
        str(cache_root),
    ]


def canonical_index_paths(checkout_dir: Path, cache_root: Path) -> tuple[Path, Path]:
    """The ``(db, tq)`` paths a ``--cache-dir <root>`` index of ``checkout_dir`` writes.

    Mirrors the product: ``db`` = ``cache_root / cache_path_for_project(checkout).name``
    (the CLI preserves the per-project slug under the overridden root,
    ``__main__._project_and_db``), and the ``.tq`` sidecar is ``db.with_suffix('.tq')``.
    """
    from pydocs_mcp.db import cache_path_for_project

    db_name = cache_path_for_project(checkout_dir).name
    db = cache_root / db_name
    return db, db.with_suffix(".tq")


def workspace_cache_paths(workspace: Path) -> tuple[Path, Path]:
    """The default-``CACHE_DIR`` ``(db, tq)`` slot a plain ``serve <workspace>`` reads.

    The rollout serve runs with no ``--cache-dir``, so it resolves
    ``cache_path_for_project(workspace)`` under ``~/.pydocs-mcp``; the ``.tq``
    mirrors it via ``with_suffix`` (``storage/search_backend.py``).
    """
    from pydocs_mcp.db import cache_path_for_project

    db = cache_path_for_project(workspace)
    return db, db.with_suffix(".tq")


def create_checkout(
    cache_root: Path,
    *,
    repo: str,
    commit: str,
    clone_url: str,
    shallow: bool = False,
    scope_id: str | None = None,
    git: Callable[[list[str]], None] | None = None,
) -> Path:
    """Clone ``clone_url`` to the canonical dir and check out ``commit`` (idempotent).

    A dir that already carries a ``.git`` is treated as built and returned as-is,
    so a re-run (or a resumed pre-build) never re-clones. ``shallow`` adds a
    blobless filter (``--filter=blob:none``) — capable of large repos without a
    full history download while still reaching an arbitrary ``commit``.
    ``scope_id`` selects the index-scope slot (ADR 0021 6): distinct scopes clone
    into distinct dirs so both are buildable side by side. The ``git`` seam is
    injected so unit tests can script it (``None`` ⇒ the real subprocess runner);
    the integration test passes the real runner against a tiny local fixture repo.
    """
    run_git = git if git is not None else _run
    dest = canonical_checkout_dir(cache_root, repo, commit, scope_id=scope_id)
    if (dest / ".git").is_dir():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    clone = ["git", "clone"]
    if shallow:
        clone += ["--filter=blob:none"]
    run_git([*clone, clone_url, str(dest)])
    run_git(["git", "-C", str(dest), "checkout", commit])
    return dest


def index_checkout(
    checkout_dir: Path,
    *,
    python: Path,
    cache_root: Path,
    index_fn: Callable[[Path, Path], tuple[Path, Path]] | None = None,
) -> tuple[Path, Path]:
    """Index ``checkout_dir`` once (project-only) and return its ``(db, tq)`` paths.

    ``index_fn(checkout_dir, cache_root) -> (db, tq)`` is the execution seam:
    the default runs the shipped ``<python> -m pydocs_mcp index`` subprocess;
    the offline test injects :func:`index_project_in_process`. Idempotent — an
    already-built ``db`` short-circuits so a resumed pre-build skips it.
    """
    db, tq = canonical_index_paths(checkout_dir, cache_root)
    if db.exists():
        return db, tq
    runner = index_fn or (lambda d, root: _subprocess_index(d, python=python, cache_root=root))
    return runner(checkout_dir, cache_root)


def _subprocess_index(checkout_dir: Path, *, python: Path, cache_root: Path) -> tuple[Path, Path]:
    """Run the shipped index CLI as a subprocess; return the produced paths."""
    _run(build_index_command(checkout_dir, python, cache_root))
    return canonical_index_paths(checkout_dir, cache_root)


def preseed_workspace(
    canonical_db: Path,
    canonical_tq: Path,
    workspace: Path,
) -> tuple[Path, Path]:
    """Copy the canonical ``.db``/``.tq`` into ``workspace``'s cache slot.

    Computes the destination via :func:`workspace_cache_paths` (product's
    ``cache_path_for_project``), so a plain ``serve <workspace>`` hits the
    pre-built index instead of re-embedding. The ``.tq`` is optional (a
    lexical-only or empty corpus may produce none); its absence is not an error.

    Copies rather than hardlinks BOTH sidecars: the ``.db`` is opened read-write
    under ``journal_mode=WAL`` at serve time, so a shared inode is a structural
    poisoning hazard (finding 2); the ``.tq`` is copied too because turbovec's
    load-time mmap mode is not provably read-only across the pinned
    ``turbovec>=0.5,<1.0`` range, so the same conservative copy applies. Future
    product option: open the slot ``.db`` with ``?mode=ro&immutable=1`` at serve
    time, which would make a read-only hardlink safe — a product change, not one
    for the eval layer to make.

    Raises:
        FileNotFoundError: if ``canonical_db`` is missing — pre-seeding a slot
            from an unbuilt index is a runner bug, not a degrade case.
    """
    if not canonical_db.exists():
        raise FileNotFoundError(
            f"canonical index db is missing: {canonical_db} (index the checkout first)"
        )
    dst_db, dst_tq = workspace_cache_paths(workspace)
    _copy_index_file(canonical_db, dst_db)
    if canonical_tq.exists():
        _copy_index_file(canonical_tq, dst_tq)
    return dst_db, dst_tq


def _copy_index_file(src: Path, dst: Path) -> None:
    """Copy ``src`` → ``dst`` with a fresh inode (never a hardlink; finding 2).

    ``copy2`` gives the slot its own inode so an in-place serve-time WAL write to
    the slot cannot mutate the shared canonical bytes. Cross-device by nature, so
    there is no EXDEV path to fall back from. Idempotent: an existing ``dst`` is
    removed first so a re-seed always reflects the current canonical index.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.copy2(src, dst)


def index_project_in_process(checkout_dir: Path, cache_root: Path) -> tuple[Path, Path]:
    """Drive the REAL product indexer in-process (project-only), no subprocess.

    Mirrors ``__main__._run_indexing`` with ``--skip-deps --no-inspect``: it is
    the offline seam :func:`index_checkout` accepts as ``index_fn`` so a test can
    build a genuine ``.db``/``.tq`` on a tiny repo with a mocked embedder — no
    ``claude``, no model download. ``pydocs_mcp`` imports stay function-local.
    """
    import asyncio

    db, tq = canonical_index_paths(checkout_dir, cache_root)
    asyncio.run(_index_async(checkout_dir, db))
    return db, tq


async def _index_async(project: Path, db_path: Path) -> None:
    """The async index pass over ``project`` into ``db_path`` (static, project-only)."""
    from pydocs_mcp.application import run_index_pass
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import build_project_indexer

    db_path.parent.mkdir(parents=True, exist_ok=True)
    open_index_database(db_path).close()
    config = AppConfig.load()
    bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
    await run_index_pass(
        orchestrator=bundle.orchestrator,
        indexing_service=bundle.indexing_service,
        pipeline_hash=bundle.pipeline_hash,
        project=project,
        embedding_provider=config.embedding.provider,
        embedding_model=config.embedding.model_name,
        embedding_dim=config.embedding.dim,
        force=False,
        include_project_source=True,
        include_dependencies=False,
        workers=1,
        check_integrity=bundle.check_integrity,
        rebuild_fts=bundle.rebuild_fts,
        stamp_metadata=bundle.stamp_metadata,
        write_aggregates=bundle.write_aggregates,
    )


def _run(cmd: list[str]) -> None:
    """Run a subprocess, raising ``CalledProcessError`` (with output) on failure."""
    subprocess.run(cmd, check=True, capture_output=True, text=True)
