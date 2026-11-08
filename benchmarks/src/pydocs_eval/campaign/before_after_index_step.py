"""The arm-side incremental re-index: each arm's bundles, re-extracted by ITS product.

A before/after run builds one bundle per corpus and hands it to BOTH arms
(``before_after_corpora``). The bundle key is ``slug@scope_id``, and
``scope_id`` is the ingestion-pipeline hash only — it carries no chunk-tree
rule — while the serve child (``serve --workspace``) never re-indexes. So a
candidate whose chunker emits a different TREE (issue #380's span filler,
``chunk-trees/2``) would serve trees the baseline's product extracted, and the
measured difference would be zero by construction.

Before serving, each arm therefore runs the shipped incremental
``pydocs-mcp index`` over every task bundle, under its own product (the arm
child's ``PYTHONPATH``): a package whose chunk-tree salt moved misses its hash
and re-extracts; the chunk cache then finds every chunk text unchanged and
embeds nothing. Arms are sequential, so sharing one bundle is safe; the price
is a ping-pong of EXTRACTION time — the baseline re-extracts it one way, the
candidate the other — never of embeds. (Folding the salt into the bundle key
would re-embed the corpus into a fresh directory; copy-on-first-use is the
fallback if the extraction time ever bites.)

Each step records its wall time and its embed diff — the chunk content hashes
the step added, i.e. the texts it handed the embed stage — in the arm summary,
so an empty diff is something a reader can check, not assume.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.campaign.before_after_corpora import CorpusWorkspaceError
from pydocs_eval.campaign.index_cache import build_index_command, canonical_index_paths

log = logging.getLogger("pydocs-eval.campaign.before-after-index-step")

# One structured line per refreshed bundle, so a run.log answers "what did the
# index step cost this arm" by grep.
INDEX_STEP_EVENT = "before_after_index_step"

# Bound on one bundle's re-index. Generous on purpose: a re-extract of a large
# corpus is minutes, and a step that hangs must still end the arm by name
# rather than hold the endpoint budget open forever.
_INDEX_STEP_TIMEOUT_SECONDS = 3600.0
# How much of a failed index's stderr the error carries.
_STDERR_TAIL_CHARS = 2000

# ``(source_dir, bundle_dir)``: re-index ``source_dir`` into the bundle in place.
BundleIndexer = Callable[[Path, Path], None]


@dataclass(frozen=True, slots=True)
class BundleIndexStep:
    """One bundle's incremental re-index, as the arm summary records it.

    ``embed_diff`` is the number of chunk content hashes present after the step
    and absent before it: a chunk hash folds the chunk's text, so this is every
    text the step could have embedded. ``0`` is the expected value for a
    tree-only change.
    """

    bundle_dir: str
    source_dir: str
    wall_seconds: float
    embed_diff: int
    chunks_after: int


def build_bundle_index_command(
    source_dir: Path, bundle_dir: Path, *, python: Path, config: Path | None
) -> list[str]:
    """The shipped index argv, writing back into ``bundle_dir`` itself.

    The bundle's ``.db`` is named after ``source_dir`` (it was copied from the
    canonical build of that very path), so ``--cache-dir <bundle_dir>`` lands
    on it. The flags are the build's own (``index_cache._INDEX_FLAGS``) and
    never ``--force``, which would wipe the chunk cache and re-embed everything.
    """
    return build_index_command(source_dir, python, bundle_dir, config=config)


def refresh_bundle_indexes(
    bundle_sources: Mapping[str, str],
    *,
    python: Path,
    config: Path | None,
    index_fn: BundleIndexer | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[BundleIndexStep, ...]:
    """Re-index every ``bundle_dir -> source_dir`` pair once, in bundle order.

    ``index_fn`` is the execution seam: the default runs the shipped CLI as a
    subprocess that inherits this process's environment — the arm child's, so
    the product doing the extraction is the commit under test.

    Raises:
        CorpusWorkspaceError: a bundle does not hold its source's index. Running
            the step anyway would build a fresh index, embedding the whole corpus
            at a cost no plan printed.
    """
    run = index_fn or _subprocess_indexer(python=python, config=config)
    pairs = sorted((Path(bundle), Path(source)) for bundle, source in bundle_sources.items())
    for bundle, source in pairs:
        _bundle_db(source, bundle)  # refuse every unusable bundle before indexing any
    return tuple(_refresh_one(bundle, source, run, clock) for bundle, source in pairs)


def _refresh_one(
    bundle: Path, source: Path, run: BundleIndexer, clock: Callable[[], float]
) -> BundleIndexStep:
    db = _bundle_db(source, bundle)
    before = _chunk_hashes(db)
    started = clock()
    run(source, bundle)
    wall = clock() - started
    after = _chunk_hashes(db)
    step = BundleIndexStep(
        bundle_dir=str(bundle),
        source_dir=str(source),
        wall_seconds=wall,
        embed_diff=len(after - before),
        chunks_after=len(after),
    )
    log.info(json.dumps({"event": INDEX_STEP_EVENT, **_as_log_fields(step)}))
    return step


def _as_log_fields(step: BundleIndexStep) -> dict[str, object]:
    return {
        "bundle_dir": step.bundle_dir,
        "wall_seconds": round(step.wall_seconds, 3),
        "embed_diff": step.embed_diff,
        "chunks_after": step.chunks_after,
    }


def _bundle_db(source: Path, bundle: Path) -> Path:
    """The ``.db`` the step writes into, or a refusal naming both directories."""
    db, _tq = canonical_index_paths(source, bundle)
    if db.is_file():
        return db
    raise CorpusWorkspaceError(
        f"the bundle {bundle} holds no index of its source {source} (expected {db.name}); "
        "re-indexing it would build the corpus from scratch and embed all of it — "
        "rebuild the workspace with --build-indexes instead"
    )


def _chunk_hashes(db: Path) -> set[str]:
    """Every chunk content hash the bundle holds (read-only; no product import)."""
    try:
        with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            rows = conn.execute("SELECT content_hash FROM chunks").fetchall()
    except sqlite3.Error as exc:
        raise CorpusWorkspaceError(f"could not read the chunk hashes of {db}: {exc}") from exc
    return {str(row[0]) for row in rows if row[0] is not None}


def _subprocess_indexer(*, python: Path, config: Path | None) -> BundleIndexer:
    def run(source: Path, bundle: Path) -> None:
        command = build_bundle_index_command(source, bundle, python=python, config=config)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=_INDEX_STEP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise CorpusWorkspaceError(
                f"the index step for {bundle} ran past {_INDEX_STEP_TIMEOUT_SECONDS:.0f}s"
            ) from exc
        except OSError as exc:  # the interpreter itself could not be started
            raise CorpusWorkspaceError(
                f"the index step for {bundle} could not start {command[0]!r}: {exc}"
            ) from exc
        if completed.returncode != 0:
            raise CorpusWorkspaceError(
                f"the index step for {bundle} exited {completed.returncode}: "
                f"{completed.stderr[-_STDERR_TAIL_CHARS:].strip() or 'no stderr'}"
            )

    return run


__all__ = (
    "INDEX_STEP_EVENT",
    "BundleIndexStep",
    "BundleIndexer",
    "build_bundle_index_command",
    "refresh_bundle_indexes",
)
