"""Materialize a task's long-context window to a fresh tmp project dir.

Spec §4.8: each RepoQA task ships a ``{relative_path: source}`` mapping
that the runner writes to disk so the standard
``ProjectIndexer.index_project(<tmp>)`` can run on a realistic project
layout. Kept as a free function (not a method on ``RepoQADataset``) so
SWE-bench / other datasets can reuse it without inheritance.

Lifetime contract: the caller owns the returned directory and is
responsible for ``shutil.rmtree(base)`` when done. The runner does this
inside a try/finally around the per-task ``system.index`` / ``search``
loop so a crash in one task can't leak the corpus for the next one.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path


def materialize_corpus(
    files: Mapping[str, str],
    parent: Path | None = None,
) -> Path:
    """Write ``{relative_path: source}`` into a fresh tmp dir and return it.

    Args:
        files: Mapping of POSIX-style relative paths to file content. Each
            key is treated as ``Path(rel)`` relative to the new dir;
            intermediate directories are created as needed.
        parent: Optional parent directory under which the tmp dir is
            created. Defaults to the system tmpdir. Tests pass
            ``tmp_path`` so artifacts stay scoped to a single pytest run.

    Returns:
        The newly-created directory. Always unique across calls. The caller
        owns cleanup — typically via ``shutil.rmtree`` in a ``try/finally``
        wrapping the per-task ``system.index/search`` cycle (runner does
        this; see spec §4.6).
    """
    # WHY: ``mkdtemp`` (not ``mkstemp``) — we want a directory, and the
    # ``repoqa_`` prefix lets ``find /tmp -name 'repoqa_*'`` clean up
    # orphans after a crashed run.
    base = Path(tempfile.mkdtemp(prefix="repoqa_", dir=parent))
    for rel, body in files.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return base
