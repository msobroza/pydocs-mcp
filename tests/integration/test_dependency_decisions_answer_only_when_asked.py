"""Dependency decisions answer only when asked, over a real index (#346).

One bundle, indexed twice through the real composition root: first stock, then
with ``decision_capture.include_deps: true``, which mines 20 ``# WHY:`` markers
from a synthetic installed dependency (``tests/_installed_dist_fixture.py``).
Every one of them shares words with the project's own decision, so an unscoped
decision search would rank them next to it.

- The project-facing answers — ``get_why(query)``, ``get_why(targets=<project
  symbol>)``, the ``get_why()`` dashboard, ``get_overview()`` and the default
  ``search_codebase(kind="decision")`` — are byte-identical before and after
  (``text`` and ``items``; item scores within float noise).
- The dependency's decisions answer when a request names them:
  ``search_codebase(kind="decision", scope="deps" | package=<dep>)`` and
  ``get_why(targets=<dependency symbol>)``, each card tagged with its package.
- ``scope="deps"`` over two dependencies with decisions answers from both.
- Ordinary searches (``kind="any"`` / ``"docs"``, any scope) leave the
  dependency's decision chunks out, and the default search answers as it did
  before they were mined (``text`` and ``items``; item scores within float
  noise; a fresh index: the same but for chunk ids); ``package=<dep>`` brings
  them back.
- That filter follows what the loaded bundle holds, not the serving config: a
  bundle mined with ``include_deps`` and served under the stock config — the
  read-write load, a read-only ``--db`` load, a multi-repo union — still leaves
  them out, and a stock bundle builds no filter whatever the serving config.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, closing
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import OverviewInput, SearchInput, WhyInput
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ChunkOrigin
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._fakes import MockEmbedder
from tests._index_fixture import run_pass_with_embedder
from tests._installed_dist_fixture import InstalledDistribution, installed_distribution
from tests._score_tolerance import scores_within_float_noise

_SHARDS = (
    "amber", "birch", "cedar", "delta", "ember", "fjord", "grove", "harbor", "iris", "jade",
    "kelp", "lotus", "maple", "nectar", "onyx", "pearl", "quartz", "raven", "sage", "tundra",
)  # fmt: skip
_QUERY = "why keep rows in memory"

_CORE_PY = '''\
"""Core storage helpers."""


class Store:
    """Keeps rows in memory."""

    def save(self, row: str) -> int:
        # WHY: keep rows in memory because the data set is small
        return len(row)
'''


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Stock means the shipped defaults, not the developer's own config."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    for name in [name for name in os.environ if name.startswith("PYDOCS_DECISION_CAPTURE")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


def _shard_loaders(shards: tuple[str, ...]) -> str:
    """One function per shard, one ``# WHY:`` marker each (inside the function:
    the module node carries the docstring, not top-level comments)."""
    functions = "".join(
        f"\n\ndef load_{shard}() -> int:\n"
        f"    # WHY: keep rows in memory for the {shard} shard\n"
        f"    return {index}\n"
        for index, shard in enumerate(shards)
    )
    return f'"""Shard loaders."""{functions}'


def _dependency_name() -> str:
    return f"pydocs_read_scope_dep_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[InstalledDistribution]:
    """20 shard loaders, so the dependency's decisions outnumber the ranked slots."""
    modules = {"shards.py": _shard_loaders(_SHARDS)}
    with installed_distribution(
        tmp_path, monkeypatch, name=_dependency_name(), modules=modules
    ) as dist:
        yield dist


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "core.py").write_text(_CORE_PY, encoding="utf-8")
    return root


def _index(config: AppConfig, db: Path, project_dir: Path, *dependencies: str) -> None:
    embedder = MockEmbedder(dim=config.embedding.dim)
    stats = run_pass_with_embedder(
        config, db, project_dir, embedder=embedder, dependency_names=dependencies
    )
    assert stats.failed == 0, "a dependency pass failed"


def _ask(config: AppConfig, db: Path, calls: dict[str, tuple[str, object]]) -> dict:
    """``text`` and ``items`` of each call over ``db``, JSON-normalized."""
    router, _services = build_routers(config, db_path=db, surface="mcp")
    return _answers(router, calls)


def _answers(router: ToolRouter, calls: dict[str, tuple[str, object]]) -> dict:
    """``text`` and ``items`` of each call, JSON-normalized."""
    answers = {}
    for name, (method, payload) in calls.items():
        response = asyncio.run(getattr(router, method)(payload))
        answers[name] = {"text": response.text, "items": response.items}
    return json.loads(json.dumps(answers, sort_keys=True, default=str))


def _project_facing_calls() -> dict[str, tuple[str, object]]:
    return {
        "why": ("get_why", WhyInput(query=_QUERY)),
        "why_target": ("get_why", WhyInput(targets=["app.core"])),
        "dashboard": ("get_why", WhyInput()),
        "overview": ("get_overview", OverviewInput()),
        "search_decision": ("search_codebase", SearchInput(query=_QUERY, kind="decision")),
    }


@pytest.fixture
def include_deps_config(tmp_path: Path) -> AppConfig:
    overlay = tmp_path / "include-deps.yaml"
    overlay.write_text("decision_capture:\n  include_deps: true\n")
    return AppConfig.load(explicit_path=overlay)


def test_project_answers_are_byte_identical_with_dependency_decisions_present(
    tmp_path: Path,
    project_dir: Path,
    dependency: InstalledDistribution,
    include_deps_config: AppConfig,
) -> None:
    db = tmp_path / "proj.db"
    open_index_database(db).close()
    stock = AppConfig.load()
    _index(stock, db, project_dir, dependency.name)
    before = _ask(stock, db, _project_facing_calls())
    # The project decision answers every one of them in the first place.
    for call in ("why", "why_target", "dashboard", "search_decision"):
        assert before[call]["items"], call
    assert "## Decisions\n" in before["overview"]["text"]

    _index(include_deps_config, db, project_dir, dependency.name)
    deps_search = SearchInput(query=_QUERY, kind="decision", scope="deps")
    after = _ask(
        include_deps_config,
        db,
        {**_project_facing_calls(), "deps_search": ("search_codebase", deps_search)},
    )
    assert {row["package"] for row in after.pop("deps_search")["items"]} == {dependency.name}
    for call, answer in before.items():
        assert after[call] == scores_within_float_noise(answer), call


def test_dependency_decisions_answer_when_a_request_names_them(
    tmp_path: Path,
    project_dir: Path,
    dependency: InstalledDistribution,
    include_deps_config: AppConfig,
) -> None:
    db = tmp_path / "proj.db"
    open_index_database(db).close()
    _index(include_deps_config, db, project_dir, dependency.name)
    name = dependency.name
    answers = _ask(
        include_deps_config,
        db,
        {
            "deps": ("search_codebase", SearchInput(query=_QUERY, kind="decision", scope="deps")),
            "package": (
                "search_codebase",
                SearchInput(query=_QUERY, kind="decision", package=name),
            ),
            "project": (
                "search_codebase",
                SearchInput(query=_QUERY, kind="decision", scope="project"),
            ),
            "target": ("get_why", WhyInput(targets=[f"{name}.shards"])),
        },
    )
    for call in ("deps", "package"):
        items = answers[call]["items"]
        assert items and {row["package"] for row in items} == {name}, call
        assert f"· from `{name}`\n" in answers[call]["text"], call
    assert {row["package"] for row in answers["project"]["items"]} == {PROJECT_PACKAGE_NAME}
    assert "· from `" not in answers["project"]["text"]
    # The target follows its own package: every card on it is the dependency's.
    target_text = answers["target"]["text"]
    assert answers["target"]["items"]
    assert target_text.count("** — ") == target_text.count(f"· from `{name}`\n") > 0


def test_scope_deps_covers_every_dependency_with_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
    include_deps_config: AppConfig,
) -> None:
    """Two dependencies with records push ``package in [...]`` through the real
    pre-filter, fetchers and post-filters; each answers under its own package."""
    shards_per_dependency = {_dependency_name(): _SHARDS[:2], _dependency_name(): _SHARDS[2:4]}
    deps_search = SearchInput(query=_QUERY, kind="decision", scope="deps")
    with ExitStack() as installed:
        for name, shards in shards_per_dependency.items():
            modules = {"shards.py": _shard_loaders(shards)}
            installed.enter_context(
                installed_distribution(tmp_path, monkeypatch, name=name, modules=modules)
            )
        db = tmp_path / "proj.db"
        open_index_database(db).close()
        _index(include_deps_config, db, project_dir, *shards_per_dependency)
        answer = _ask(include_deps_config, db, {"deps": ("search_codebase", deps_search)})["deps"]
    assert {row["package"] for row in answer["items"]} == set(shards_per_dependency)
    for name in shards_per_dependency:
        assert f"· from `{name}`\n" in answer["text"], name


# ── Ordinary searches (kind="any" / "docs") ──────────────────────────────────

# A dependency decision chunk renders under its bare title — the project's own
# decision reads "keep rows in memory because …", so this prefix is the shards'.
_DEPENDENCY_DECISION_HEADING = "## keep rows in memory for the "


def _decision_chunk_ids(db: Path, package: str) -> set[str]:
    """Ids (as ``items[]`` spells them) of ``package``'s decision-record chunks."""
    sql = "SELECT id FROM chunks WHERE origin = ? AND package = ?"
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(sql, (ChunkOrigin.DECISION_RECORD.value, package)).fetchall()
    return {str(row[0]) for row in rows}


def _ordinary_search_calls(**fields: str) -> dict[str, tuple[str, object]]:
    """The default search, ``kind="docs"`` and both narrowing scopes."""
    return {
        "default": ("search_codebase", SearchInput(query=_QUERY, **fields)),
        "docs": ("search_codebase", SearchInput(query=_QUERY, kind="docs", **fields)),
        "deps": ("search_codebase", SearchInput(query=_QUERY, scope="deps", **fields)),
        "project": ("search_codebase", SearchInput(query=_QUERY, scope="project", **fields)),
    }


def _identity_calls() -> dict[str, tuple[str, object]]:
    """The ordinary searches promised to answer as stock (not ``scope="deps"``)."""
    return {name: call for name, call in _ordinary_search_calls().items() if name != "deps"}


def _calls_naming(package: str) -> dict[str, tuple[str, object]]:
    """The default and ``kind="docs"`` searches with ``package=`` naming ``package``."""
    calls = _ordinary_search_calls(package=package)
    return {name: calls[name] for name in ("default", "docs")}


def test_ordinary_searches_leave_dependency_decisions_out(
    tmp_path: Path,
    project_dir: Path,
    dependency: InstalledDistribution,
    include_deps_config: AppConfig,
) -> None:
    """Every shard decision shares the query's words, so without the exclusion
    they take the top ranks of all four searches (the #346 review probe)."""
    db = tmp_path / "proj.db"
    open_index_database(db).close()
    _index(include_deps_config, db, project_dir, dependency.name)
    decision_ids = _decision_chunk_ids(db, dependency.name)
    assert len(decision_ids) == len(_SHARDS), "the dependency's decisions were not indexed"
    answers = _ask(include_deps_config, db, _ordinary_search_calls())
    for call, answer in answers.items():
        assert answer["items"], call
        assert not {row["id"] for row in answer["items"]} & decision_ids, call
        assert _DEPENDENCY_DECISION_HEADING not in answer["text"], call
    # scope="deps" still answers from the dependency's ordinary chunks.
    assert dependency.name in {row["package"] for row in answers["deps"]["items"]}


# WHY scores compare within float noise in the two identity tests below: over the
# mined bundle the dense branch takes the candidate-id allowlist path (the
# dependency-decision exclusion), over the stock bundle the unfiltered ANN path.
# Both score the same vectors, but they accumulate float32 in a different order,
# so a score's last digits can differ: CI on #353 scored one dependency docs hit
# 6.083324432373047 (mined) against 6.083323955535889 (stock) in one job, while
# the two matched bit-for-bit locally. Text, order, ids and every other field
# stay exact.


def test_default_search_answers_as_if_dependency_decisions_were_never_mined(
    tmp_path: Path,
    project_dir: Path,
    dependency: InstalledDistribution,
    include_deps_config: AppConfig,
) -> None:
    """Same bundle, stock first: turning ``include_deps`` on changes nothing a
    default or ``kind="docs"`` search returns — ``text`` and ``items`` exactly,
    item scores within float noise: the dense branch scores the same vectors
    whether the excluded ones are absent or filtered out, only in a different
    float32 order. It holds whichever config serves the mined bundle: the
    filter follows what the bundle holds.

    The ids match too because the re-index goes through the chunk-level diff,
    which keeps every unchanged row (and so its rowid). A fresh index is
    different: it inserts the decision chunks among the dependency's own and
    shifts the rowids after them, so the fresh-index test below compares
    modulo ids.

    ``scope="deps"`` is left out on purpose: it routes through BM25, whose
    FTS5 corpus statistics count the excluded rows too, so its fused ranking is
    not promised to stay put (only that the decisions stay out, tested above)."""
    db = tmp_path / "proj.db"
    open_index_database(db).close()
    stock = AppConfig.load()
    calls = _identity_calls()
    _index(stock, db, project_dir, dependency.name)
    assert not _decision_chunk_ids(db, dependency.name)
    before = _ask(stock, db, calls)

    _index(include_deps_config, db, project_dir, dependency.name)
    assert _decision_chunk_ids(db, dependency.name)
    for serving in (include_deps_config, stock):
        after = _ask(serving, db, calls)
        for call, answer in before.items():
            assert after[call] == scores_within_float_noise(answer), call


def _without_chunk_ids(answer: dict) -> dict:
    """``answer`` with each item's ``id`` dropped — a fresh index renumbers rows."""
    items = [{key: value for key, value in row.items() if key != "id"} for row in answer["items"]]
    return {"text": answer["text"], "items": items}


def test_fresh_index_default_search_matches_stock_but_for_chunk_ids(
    tmp_path: Path,
    project_dir: Path,
    dependency: InstalledDistribution,
    include_deps_config: AppConfig,
) -> None:
    """Two fresh bundles of one corpus, stock and with ``include_deps``: the
    same text, rows and order, scores within float noise — only the chunk ids
    differ, because the mined decision chunks take rowids in the middle of the
    dependency's."""
    answers = {}
    for name, config in (("stock", AppConfig.load()), ("mined", include_deps_config)):
        db = tmp_path / name / "proj.db"
        db.parent.mkdir()
        open_index_database(db).close()
        _index(config, db, project_dir, dependency.name)
        answers[name] = _ask(config, db, _identity_calls())
    for call, answer in answers["stock"].items():
        expected = scores_within_float_noise(_without_chunk_ids(answer))
        assert _without_chunk_ids(answers["mined"][call]) == expected, call


def test_a_search_naming_the_dependency_returns_its_decisions(
    tmp_path: Path,
    project_dir: Path,
    dependency: InstalledDistribution,
    include_deps_config: AppConfig,
) -> None:
    """``package=<dep>`` asks for that dependency, decisions included."""
    db = tmp_path / "proj.db"
    open_index_database(db).close()
    _index(include_deps_config, db, project_dir, dependency.name)
    decision_ids = _decision_chunk_ids(db, dependency.name)
    answers = _ask(include_deps_config, db, _calls_naming(dependency.name))
    for call, answer in answers.items():
        assert {row["id"] for row in answer["items"]} & decision_ids, call
        assert _DEPENDENCY_DECISION_HEADING in answer["text"], call


# ── The filter follows the bundle, not the serving config ────────────────────


def _assert_no_dependency_decision(answers: dict, name: str, decision_ids: set[str]) -> None:
    """No row of ``name``'s is one of its decision chunks, and no card renders one."""
    for call, answer in answers.items():
        assert answer["items"], call
        rows = {row["id"] for row in answer["items"] if row["package"] == name}
        assert not rows & decision_ids, call
        assert _DEPENDENCY_DECISION_HEADING not in answer["text"], call


@pytest.fixture
def mined_bundle(
    tmp_path: Path,
    project_dir: Path,
    dependency: InstalledDistribution,
    include_deps_config: AppConfig,
) -> Path:
    """A bundle indexed with ``include_deps: true`` — its dependency's decisions mined."""
    db = tmp_path / "proj.db"
    open_index_database(db).close()
    _index(include_deps_config, db, project_dir, dependency.name)
    assert _decision_chunk_ids(db, dependency.name)
    return db


@pytest.mark.parametrize("read_only", [False, True], ids=["db_path", "read_only_db"])
def test_a_serving_config_without_include_deps_still_leaves_them_out(
    mined_bundle: Path, dependency: InstalledDistribution, read_only: bool
) -> None:
    """The index/serve split: index with ``include_deps``, serve (read-write, or
    read-only as ``--db`` loads it) under the stock config. The ordinary
    searches still leave the decisions out; ``package=<dep>`` still asks."""
    stock = AppConfig.load()
    selector = {"db_paths": [mined_bundle]} if read_only else {"db_path": mined_bundle}
    router, services = build_routers(stock, surface="mcp", **selector)
    assert [svc.holds_dependency_decisions for svc in services] == [True]
    decision_ids = _decision_chunk_ids(mined_bundle, dependency.name)
    _assert_no_dependency_decision(
        _answers(router, _ordinary_search_calls()), dependency.name, decision_ids
    )
    for call, answer in _answers(router, _calls_naming(dependency.name)).items():
        assert {row["id"] for row in answer["items"]} & decision_ids, call


def test_a_read_only_union_leaves_the_mined_bundle_s_decisions_out(
    tmp_path: Path, mined_bundle: Path, dependency: InstalledDistribution
) -> None:
    """A multi-repo union under the stock config: the mined bundle filters, the
    stock one beside it (no dependency, so any dependency row is the mined
    bundle's) builds no filter."""
    stock = AppConfig.load()
    other_dir = tmp_path / "other"
    (other_dir / "lib").mkdir(parents=True)
    (other_dir / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (other_dir / "lib" / "rows.py").write_text(_CORE_PY, encoding="utf-8")
    other = tmp_path / "other.db"
    open_index_database(other).close()
    _index(stock, other, other_dir)
    router, services = build_routers(stock, db_paths=[mined_bundle, other], surface="mcp")
    assert [svc.holds_dependency_decisions for svc in services] == [True, False]
    answers = _answers(router, _ordinary_search_calls())
    decision_ids = _decision_chunk_ids(mined_bundle, dependency.name)
    _assert_no_dependency_decision(answers, dependency.name, decision_ids)
    assert dependency.name in {row["package"] for row in answers["deps"]["items"]}


def test_a_stock_bundle_builds_no_filter_whatever_the_serving_config(
    tmp_path: Path,
    project_dir: Path,
    dependency: InstalledDistribution,
    include_deps_config: AppConfig,
) -> None:
    """A serving config with ``include_deps: true`` over a bundle that holds no
    dependency decision adds nothing: every answer is the stock answer."""
    db = tmp_path / "proj.db"
    open_index_database(db).close()
    stock = AppConfig.load()
    _index(stock, db, project_dir, dependency.name)
    for serving in (stock, include_deps_config):
        _router, services = build_routers(serving, db_path=db)
        assert [svc.holds_dependency_decisions for svc in services] == [False]
    calls = _ordinary_search_calls()
    assert _ask(include_deps_config, db, calls) == _ask(stock, db, calls)
