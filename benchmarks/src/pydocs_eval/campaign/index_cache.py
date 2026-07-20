"""Canonical-checkout project-index cache (ADR 0014 §Decision 3, item 2-3).

The PROJECT index is a pure function of ``(repo files at base_commit, embedder +
ingestion config)`` but the product cache key is path-based
(``dirname + md5(abs_path)[:10]``, ``db.cache_path_for_project``). So rather than
change the product, the runner makes the *path* canonical: one pristine checkout
per ``(repo, base_commit)`` at ``<cache_root>/<repo_slug>@<commit>/``, indexed
ONCE with ``--skip-deps --no-inspect``. The path-derived key is then stable, and
before a rollout's serve starts the runner pre-seeds that rollout workspace's own
cache slot by hardlinking (copy fallback across filesystems) the ``.db``/``.tq``
pair to the workspace-path-derived key — computed by calling the product's
``cache_path_for_project``, never by re-deriving the hash (ADR 0014 §Consequences
"rides an implementation detail" — the pin test guards it).

``pydocs_mcp`` is imported function-locally (like the agent-track adapter) so the
eval base-install floor stays intact. The subprocess index path is the shipped
product behavior; :func:`index_project_in_process` is the same product indexer
driven in-process, the offline-testable seam that needs no ``claude`` and no
model download when the caller mocks ``build_embedder``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

# The exact index flags ADR 0014 pins: project-only (``--skip-deps``), no
# import side effects (``--no-inspect``). Single source of truth so a flag
# rename is one edit and the pin test asserts this list verbatim.
_INDEX_FLAGS = ("--skip-deps", "--no-inspect")
_SERVE_MODULE = ("-m", "pydocs_mcp", "index")


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


def canonical_checkout_dir(cache_root: Path, repo: str, commit: str) -> Path:
    """The pristine-checkout path for ``(repo, commit)``: ``<root>/<slug>@<commit>/``."""
    if not commit:
        raise ValueError(f"commit must be a non-empty sha, got {commit!r}")
    return cache_root / f"{repo_slug(repo)}@{commit}"


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
    git: Callable[[list[str]], None] | None = None,
) -> Path:
    """Clone ``clone_url`` to the canonical dir and check out ``commit`` (idempotent).

    A dir that already carries a ``.git`` is treated as built and returned as-is,
    so a re-run (or a resumed pre-build) never re-clones. ``shallow`` adds a
    blobless filter (``--filter=blob:none``) — capable of large repos without a
    full history download while still reaching an arbitrary ``commit``. The
    ``git`` seam is injected so unit tests can script it (``None`` ⇒ the real
    subprocess runner); the integration test passes the real runner against a
    tiny local fixture repo.
    """
    run_git = git if git is not None else _run
    dest = canonical_checkout_dir(cache_root, repo, commit)
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
    """Hardlink (copy fallback) the canonical ``.db``/``.tq`` into ``workspace``'s slot.

    Computes the destination via :func:`workspace_cache_paths` (product's
    ``cache_path_for_project``), so a plain ``serve <workspace>`` hits the
    pre-built index instead of re-embedding. The ``.tq`` is optional (a
    lexical-only or empty corpus may produce none); its absence is not an error.

    Raises:
        FileNotFoundError: if ``canonical_db`` is missing — pre-seeding a slot
            from an unbuilt index is a runner bug, not a degrade case.
    """
    if not canonical_db.exists():
        raise FileNotFoundError(
            f"canonical index db is missing: {canonical_db} (index the checkout first)"
        )
    dst_db, dst_tq = workspace_cache_paths(workspace)
    _link_or_copy(canonical_db, dst_db)
    if canonical_tq.exists():
        _link_or_copy(canonical_tq, dst_tq)
    return dst_db, dst_tq


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink ``src`` → ``dst`` (fast, no extra bytes); copy across filesystems.

    Idempotent: an existing ``dst`` is removed first so a re-seed always reflects
    the current canonical index. ``os.link`` fails with ``OSError`` across
    devices (EXDEV) — the copy fallback covers that (ADR 0014 item 2).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
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
