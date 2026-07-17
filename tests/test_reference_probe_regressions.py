"""Reference-resolution probe regressions (ADR 0004 — the three sanctioned fixes).

Builds a tiny probe package on disk and indexes it through the REAL
ingestion pipeline + IndexingService (the same machinery as
``tests/conftest.py``'s ``integration_conn``), then asserts each probe
finding stays fixed:

- **9a project-code addressing** — bare project-qualified targets
  (``probepkg.mod.thing`` stored under ``__project__``) resolve through
  ``LookupService`` target strings (contract §3 dotted-target grammar).
- **9b ``ast.ImportFrom.level``** — relative imports qualify against the
  importing module, so ``__init__`` re-exports emit resolvable qnames.
- **9c Rule C for project code** — a unique bare-name suffix match
  resolves within the project's own qname universe; ambiguity stays
  ``None`` (Rule D conservatism preserved).

Out of scope by design (declared ``resolution: syntactic``, ADR 0004):
shadowed imports and annotated locals — pinned below as the known
conservative behavior, NOT bugs to fix in Phase 0.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.db import open_index_database, rebuild_fulltext_index
from pydocs_mcp.extraction import (
    AstMemberExtractor,
    PipelineChunkExtractor,
    build_ingestion_pipeline,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import (
    build_sqlite_indexing_service,
    build_sqlite_lookup_service,
)

# ── Probe package sources ────────────────────────────────────────────────

# 9b: `__init__` re-export via relative import (probe: emitted
# to_name='mod.thing' UNRESOLVED before the ImportFrom.level fix).
_INIT_PY = "from .mod import thing\n"

# 9c: `entry()` calls `one_of_a_kind()` bare — same-module, no import, so
# only Rule C can resolve it (probe: structurally dead for __project__).
# Alpha.dup / Beta.dup exist so an ambiguous suffix stays conservative.
_MOD_PY = '''\
"""Probe module."""


def thing():
    return "thing"


def one_of_a_kind():
    return 1


def entry():
    return one_of_a_kind()


class Alpha:
    def dup(self):
        return "a"


class Beta:
    def dup(self):
        return "b"
'''

# Shadowed import + annotated local — the ADR 0004 OUT-of-scope probe
# cases, pinned as declared-syntactic behavior.
_CALLER_PY = '''\
"""Probe caller module."""

from probepkg.mod import thing


def call_thing():
    return thing()


def shadowed():
    thing = _local
    return thing()


def _local():
    return None


def annotated(a):
    x: Alpha = a
    return x.dup()
'''


@pytest.fixture(scope="module")
def probe_db(tmp_path_factory) -> Path:
    """Index the probe package through the real pipeline into a SQLite DB."""
    root = tmp_path_factory.mktemp("probe")
    project = root / "probe_project"
    pkg = project / "probepkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "probe-project"\nversion = "0.0.0"\ndependencies = []\n'
    )
    (pkg / "__init__.py").write_text(_INIT_PY)
    (pkg / "mod.py").write_text(_MOD_PY)
    (pkg / "caller.py").write_text(_CALLER_PY)

    db_path = root / "probe.db"
    open_index_database(db_path).close()
    service = build_sqlite_indexing_service(db_path)

    async def _index() -> None:
        from tests._fakes import MockEmbedder, make_fake_uow_factory

        pipeline = build_ingestion_pipeline(
            AppConfig(),
            embedder=MockEmbedder(),
            uow_factory=make_fake_uow_factory(),
        )
        extractor = PipelineChunkExtractor(pipeline=pipeline)
        result = await extractor.extract_from_project(project)
        members = await AstMemberExtractor().extract_from_project(project)
        await service.reindex_package(
            result.package,
            result.chunks,
            members,
            trees=result.trees,
            references=result.references,
            reference_aliases=result.reference_aliases,
            class_attribute_types=result.class_attribute_types,
        )

    asyncio.run(_index())
    conn = open_index_database(db_path)
    rebuild_fulltext_index(conn)
    conn.close()
    return db_path


def _lookup(db_path: Path, target: str, show: str = "default") -> str:
    svc = build_sqlite_lookup_service(db_path)
    return asyncio.run(svc.lookup(LookupInput(target=target, show=show)))


def _reference_rows(db_path: Path) -> list[tuple[str, str, str | None, str]]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT from_node_id, to_name, to_node_id, kind FROM node_references"
    ).fetchall()
    conn.close()
    return rows


# ── 9a — project-code addressing (P0, contract §3) ───────────────────────


def test_project_symbol_reachable_by_bare_qualified_name(probe_db):
    """``probepkg.mod.thing`` lives under ``__project__`` with prefixless
    module ids — the target string must still resolve (probe P0)."""
    out = _lookup(probe_db, "probepkg.mod.thing")
    assert '"node_id": "probepkg.mod.thing"' in out


def test_project_module_target_renders_tree(probe_db):
    out = _lookup(probe_db, "probepkg.mod")
    assert '"node_id": "probepkg.mod"' in out


def test_project_single_segment_target_falls_back_to_project_module(probe_db):
    """``probepkg`` is not an indexed PACKAGE (only ``__project__`` is);
    the single-segment fallback renders the project's ``probepkg``
    package-``__init__`` module tree instead of NotFoundError."""
    out = _lookup(probe_db, "probepkg")
    assert '"node_id": "probepkg"' in out


def test_project_references_direction_resolves(probe_db):
    """``refs probepkg.mod.thing`` — the admitted regression at
    tests/test_cli.py (0.5.x): now a positive path end-to-end."""
    out = _lookup(probe_db, "probepkg.mod.thing", show="callers")
    assert "Callers of" in out
    assert "probepkg.caller" in out


def test_unknown_symbol_under_project_module_raises_not_found(probe_db):
    """A genuine miss BELOW a resolvable project module prefix surfaces the
    symbol-level NotFoundError (the fallback resolved ``probepkg``'s
    ``__init__`` tree; the symbol itself is absent)."""
    with pytest.raises(NotFoundError, match="'probepkg.nope.thing' not found in probepkg"):
        _lookup(probe_db, "probepkg.nope.thing")


def test_fully_unknown_target_keeps_pre_fix_message(probe_db):
    """When neither the package NOR the ``__project__`` fallback matches,
    the pre-fix NotFoundError message shape is preserved byte-for-byte."""
    with pytest.raises(NotFoundError, match="no module matching 'nosuchpkg.mod.thing'"):
        _lookup(probe_db, "nosuchpkg.mod.thing")
