"""``get_references(direction="inherits")`` — both senses over the real pipeline.

Mirrors the live repro from the verification panel: a probe package with
``mod_beta.BetaBase`` and ``mod_gamma.GammaChild(BetaBase)`` (base imported
via from-import, so the captured INHERITS edge stores the bare source-text
name ``'BetaBase'`` in ``to_name``). Pre-fix, ``inherits(target)`` resolved
via exact ``to_name`` equality against the dotted target, so every
contract-sanctioned dotted target returned "No bases found"; rows that DID
match were the target's SUBCLASSES rendered under a "Bases of" heading.

Post-fix, ``inherits`` answers BOTH senses with correct labels:
- BASES — from-side INHERITS edges (``from_node_id == target``).
- SUBCLASSES — INHERITS edges into the target (``to_node_id == target``,
  plus exact fully-dotted ``to_name`` matches; never bare-suffix).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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

_MOD_BETA_PY = '''\
"""Probe base module."""


class BetaBase:
    def greet(self):
        return "beta"
'''

# The base arrives via from-import, so the class statement's source text is
# the BARE name — the captured edge is ``GammaChild → to_name='BetaBase'``
# (resolved through the import alias map). ``Loner`` pins the empty path.
_MOD_GAMMA_PY = '''\
"""Probe subclass module."""

from probeinh.mod_beta import BetaBase


class GammaChild(BetaBase):
    def bark(self):
        return "gamma"


class Loner:
    def sit(self):
        return "alone"
'''


@pytest.fixture(scope="module")
def inherits_db(tmp_path_factory) -> Path:
    """Index the probe package through the real pipeline into a SQLite DB."""
    root = tmp_path_factory.mktemp("inherits_probe")
    project = root / "probe_project"
    pkg = project / "probeinh"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "probe-inh"\nversion = "0.0.0"\ndependencies = []\n'
    )
    (pkg / "__init__.py").write_text("")
    (pkg / "mod_beta.py").write_text(_MOD_BETA_PY)
    (pkg / "mod_gamma.py").write_text(_MOD_GAMMA_PY)

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


def _inherits(db_path: Path, target: str):
    svc = build_sqlite_lookup_service(db_path)
    return asyncio.run(svc.lookup_with_items(LookupInput(target=target, show="inherits")))


# ── Sense 1: bases of the child ──────────────────────────────────────────


def test_child_lists_its_base(inherits_db):
    """``inherits(GammaChild)`` lists ``BetaBase`` under "Bases of" — the
    pre-fix exact ``to_name`` probe returned "No bases found" here."""
    body, _items, _ = _inherits(inherits_db, "probeinh.mod_gamma.GammaChild")
    assert "## Bases of `probeinh.mod_gamma.GammaChild`" in body, body
    assert "probeinh.mod_beta.BetaBase" in body, body
    assert "No inheritance edges" not in body, body


# ── Sense 2: subclasses of the base ──────────────────────────────────────


def test_base_lists_its_subclass(inherits_db):
    """``inherits(BetaBase)`` lists ``GammaChild`` under "Subclasses of" —
    pre-fix these rows (when they matched at all) were mislabeled "Bases"."""
    body, _items, _ = _inherits(inherits_db, "probeinh.mod_beta.BetaBase")
    assert "## Subclasses of `probeinh.mod_beta.BetaBase`" in body, body
    assert "probeinh.mod_gamma.GammaChild" in body, body
    # The pre-fix mislabel: subclass rows must never render under "Bases of".
    assert "## Bases of" not in body, body


# ── items[] keep the §3.5 shape, from/to per actual edge ─────────────────


def test_items_rows_keep_contract_shape(inherits_db):
    _body, items, _ = _inherits(inherits_db, "probeinh.mod_gamma.GammaChild")
    assert items, "expected one §3.5 row per rendered edge"
    row = items[0]
    assert row["direction"] == "inherits"
    assert row["kind"] == "inherits"
    assert row["from_qualified_name"] == "probeinh.mod_gamma.GammaChild"
    assert row["to_qualified_name"] == "probeinh.mod_beta.BetaBase"


def test_base_items_rows_point_from_subclass(inherits_db):
    _body, items, _ = _inherits(inherits_db, "probeinh.mod_beta.BetaBase")
    assert items, "expected one §3.5 row per rendered edge"
    row = items[0]
    assert row["direction"] == "inherits"
    assert row["from_qualified_name"] == "probeinh.mod_gamma.GammaChild"
    assert row["to_qualified_name"] == "probeinh.mod_beta.BetaBase"


# ── Empty path ───────────────────────────────────────────────────────────


def test_class_without_edges_reports_none(inherits_db):
    body, items, _ = _inherits(inherits_db, "probeinh.mod_gamma.Loner")
    assert "No inheritance edges found for `probeinh.mod_gamma.Loner`." in body, body
    assert items == ()
