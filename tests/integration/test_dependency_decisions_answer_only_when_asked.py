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
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import OverviewInput, SearchInput, WhyInput
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._fakes import MockEmbedder
from tests._index_fixture import run_pass_with_embedder
from tests._installed_dist_fixture import InstalledDistribution, installed_distribution

_SHARDS = (
    "amber", "birch", "cedar", "delta", "ember", "fjord", "grove", "harbor", "iris", "jade",
    "kelp", "lotus", "maple", "nectar", "onyx", "pearl", "quartz", "raven", "sage", "tundra",
)  # fmt: skip
_QUERY = "why keep rows in memory"
# WHY: item scores come from float32 scoring math whose last digits vary by CPU
# (the #307 identity golden's tolerance, same reason).
_SCORE_REL_TOLERANCE = 1e-5
_SCORE_ABS_TOLERANCE = 1e-6

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
    """``text`` and ``items`` of each call, JSON-normalized."""
    router, _services = build_routers(config, db_path=db, surface="mcp")
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


def _within_float_noise(answer: dict) -> dict:
    items = [
        {
            key: pytest.approx(value, rel=_SCORE_REL_TOLERANCE, abs=_SCORE_ABS_TOLERANCE)
            if key == "score" and isinstance(value, float)
            else value
            for key, value in item.items()
        }
        for item in answer["items"]
    ]
    return {**answer, "items": items}


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
        assert after[call] == _within_float_noise(answer), call


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
