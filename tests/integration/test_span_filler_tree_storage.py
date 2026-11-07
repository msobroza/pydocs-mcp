"""What the span filler costs in ``document_trees`` on an installed dependency (issue #380).

The filler duplicates a node's unclaimed lines into the tree JSON, so the
storage cost is real; this pins its shape on the installed-dist fixture (a
dependency indexed from a real dist-info, the path every dependency takes):

- each line is filler of at most ONE node (a module's filler excludes its
  classes' spans, a class's excludes its methods'), so the growth is bounded
  by the indexed files' own bytes plus the JSON framing per line;
- nothing but ``document_trees`` grows — the chunk rows are identical.

The measured sizes are emitted as one structured log line
(``span_filler_tree_storage``) so the figure the PR reports is reproducible.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder
from tests._index_fixture import run_pass_with_embedder
from tests._installed_dist_fixture import installed_distribution

_DEPENDENCY = "acmefill"
# Bytes of JSON framing one filler entry adds beyond its text: ``[1234,"…"],``.
_JSON_BYTES_PER_ENTRY = 16

_CORE_PY = '''\
"""Acme core — a docstring that spans
several lines, the way library modules do.
"""

__all__ = ("Pool", "DEFAULT_SIZE")

DEFAULT_SIZE = 8  # the pool size every caller gets
_REGISTRY: dict[str, int] = {}


@register
class Pool:
    """A bounded pool."""

    size = DEFAULT_SIZE

    def acquire(self) -> int:
        return 1

    # Release hands the slot back.
    @staticmethod
    def release() -> None:
        return None


if __name__ == "__main__":
    Pool().acquire()
'''

log = logging.getLogger(__name__)


def _filler_config(tmp_path: Path, *, on: bool) -> AppConfig:
    overlay = tmp_path / f"filler_{'on' if on else 'off'}.yaml"
    overlay.write_text(
        f"extraction:\n  chunking:\n    python:\n      span_filler: {'true' if on else 'false'}\n"
    )
    return AppConfig.load(explicit_path=overlay)


def _index(tmp_path: Path, project: Path, *, on: bool) -> Path:
    db_path = tmp_path / f"trees_{'on' if on else 'off'}.db"
    open_index_database(db_path).close()
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))
    run_pass_with_embedder(
        _filler_config(tmp_path, on=on),
        db_path,
        project,
        embedder=embedder,
        dependency_names=(_DEPENDENCY,),
    )
    return db_path


def _tree_bytes(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(CAST(tree_json AS BLOB))), 0) FROM document_trees "
            "WHERE package = ?",
            (_DEPENDENCY,),
        ).fetchone()
    return int(row[0])


def _chunk_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT package, module, title, text, content_hash, qualified_name "
                "FROM chunks ORDER BY package, module, qualified_name, text"
            ).fetchall()
        ]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text('"""App."""\n')
    return root


def test_the_filler_grows_only_the_trees_and_within_the_files_own_bytes(
    tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with installed_distribution(
        tmp_path / "dist", monkeypatch, name=_DEPENDENCY, modules={"core.py": _CORE_PY}
    ):
        off = _index(tmp_path, project, on=False)
        on = _index(tmp_path, project, on=True)

    off_bytes, on_bytes = _tree_bytes(off), _tree_bytes(on)
    source_bytes = len(_CORE_PY.encode())
    source_lines = len(_CORE_PY.splitlines())
    log.info(
        json.dumps(
            {
                "event": "span_filler_tree_storage",
                "package": _DEPENDENCY,
                "tree_bytes_off": off_bytes,
                "tree_bytes_on": on_bytes,
                "source_bytes": source_bytes,
            }
        )
    )
    assert off_bytes > 0, "the dependency's trees must be persisted to be measured"
    assert on_bytes > off_bytes
    assert on_bytes - off_bytes <= source_bytes + _JSON_BYTES_PER_ENTRY * source_lines
    assert _chunk_rows(on) == _chunk_rows(off)
