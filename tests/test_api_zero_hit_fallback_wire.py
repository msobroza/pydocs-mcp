"""The ``kind="api"`` zero-hit fallback on a REAL index, MCP and CLI surfaces (ADR 0027).

A tiny project is indexed through ``tests/_index_fixture`` with the suite's
mock embedder (deterministic, offline) and served through the production
composition root, ``build_routers``, once per surface. On that bundle:

- a descriptive ``kind="api"`` request whose member lookup finds nothing
  serves exactly the rows, order and scores of a direct ``kind="any"``
  request, and its text minus the label line is the direct text byte for
  byte — rank and source truth survive the fallback;
- an exact-name ``kind="api"`` request (a member hit) is byte-identical with
  the fallback on and off;
- a dense path that already degrades today (the ``.tq`` vector sidecar
  absent) is inherited exactly as a direct ``kind="any"`` sees it.

Every equality request is issued twice (determinism). Outcome tests keep
the requested kind, the raw API zero hit (the fired rule) and the served kind
(the label plus each row's own ``kind``) apart.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.tool_response import SearchEnvelope
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db
from tests.application.test_api_zero_hit_fallback_any import (
    F_EMPTY,
    F_RULE,
    F_SERVED,
    Z_RULE,
    ServedSearch,
    serve_search,
    without_label,
)

_PYPROJECT = """\
[project]
name = "fallbackproj"
version = "0.0.0"
dependencies = []
"""

# The answer to the descriptive query lives in a METHOD's docstring, which no
# member row holds (members are top-level public functions and classes), and
# no top-level name or docstring contains the whole phrase — so kind="api"
# genuinely misses while kind="any" has chunks to rank.
_TREE_PY = '''\
"""Tree helpers."""


def render_widget(name: str) -> str:
    """Render one widget by name."""
    return name


class Tree:
    """A tree of nodes."""

    def walk(self) -> list[str]:
        """Nested serialization of every child node, depth first."""
        return []
'''

DESCRIPTIVE_QUERY = "nested serialization"
EXACT_NAME_QUERY = "render_widget"
SUGGESTIONS_LOGGER = "pydocs_mcp.application.suggestions"
SURFACES = ["mcp", "cli"]


def _write_project(root: Path) -> Path:
    project = root / "fallbackproj"
    (project / "pkg").mkdir(parents=True)
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (project / "pkg" / "__init__.py").write_text("")
    (project / "pkg" / "tree.py").write_text(_TREE_PY)
    return project


@pytest.fixture
def indexed_db(tmp_path: Path) -> Path:
    return index_project_to_db(_write_project(tmp_path), tmp_path / "fallback.db")


def fallback_off_config(config: AppConfig | None = None) -> AppConfig:
    """``config`` (default: the shipped one) with ``api_zero_hit_fallback_any`` off."""
    config = config or AppConfig.load()
    flags = config.output.suggestions.model_copy(update={"api_zero_hit_fallback_any": False})
    return config.model_copy(
        update={"output": config.output.model_copy(update={"suggestions": flags})}
    )


def shipped_router(db_path: Path, surface: str, config: AppConfig | None = None) -> ToolRouter:
    router, _services = build_routers(config or AppConfig.load(), db_path=db_path, surface=surface)
    return router


def serve_twice(
    router: ToolRouter, caplog: pytest.LogCaptureFixture, **fields: Any
) -> ServedSearch:
    """Serve the request twice (determinism) and return it once both responses — text,
    ``items[]``, ``meta``, fired rules, rendered rows — proved identical."""
    first, second = (
        asyncio.run(serve_search(router, SearchInput(**fields), caplog)) for _ in range(2)
    )
    assert (first.response.text, first.response.items) == (
        second.response.text,
        second.response.items,
    )
    assert (first.response.meta, first.rules) == (second.response.meta, second.rules)
    assert first.rendered_rows == second.rendered_rows
    return first


def meta_without_suggestion(served: ServedSearch) -> dict[str, Any]:
    return {key: value for key, value in served.response.meta.items() if key != "suggestion"}


def assert_served_exactly_the_direct_any(fallback: ServedSearch, direct: ServedSearch) -> None:
    """Raw API zero hit: the fallback rule fired, alone. Served kind: the label
    is the first line after the freshness header, then the direct any's rows
    (order, ids, kinds, scores), text, ``meta.truncated`` and the rest of meta."""
    assert fallback.rules == [F_RULE] and direct.rules == []
    header, blank, label = fallback.response.text.split("\n")[:3]
    assert header.startswith("[index: ") and blank == "" and label == F_SERVED
    assert fallback.response.meta["suggestion"] == F_SERVED
    assert fallback.response.items and fallback.response.items == direct.response.items
    # The fallback's member half re-ran the same lookup and found nothing too.
    assert {item["kind"] for item in fallback.response.items} == {"chunk"}
    assert without_label(fallback.response.text) == direct.response.text
    assert fallback.response.meta["truncated"] == direct.response.meta["truncated"]
    assert meta_without_suggestion(fallback) == meta_without_suggestion(direct)
    SearchEnvelope.model_validate(fallback.response.structured())


def assert_both_passes_empty_like_the_retry_hint(fallback: ServedSearch, off: ServedSearch) -> None:
    """Empty: the fallback-off response (Z and R on, the shipped default) with
    the retry line's text replaced by the empty label; rules Z then F."""
    retry = off.response.meta["suggestion"]
    assert retry.startswith('[suggestion: kind="api" matched nothing — retry with')
    assert fallback.rules == [Z_RULE, F_RULE]
    assert fallback.response.text == off.response.text.replace(retry, F_EMPTY)
    assert fallback.response.meta["suggestion"] == F_EMPTY and fallback.response.items == ()
    assert meta_without_suggestion(fallback) == meta_without_suggestion(off)
    SearchEnvelope.model_validate(fallback.response.structured())


# ── Equality with a direct kind="any", on both surfaces ───────────────────

# ``scope="deps"`` routes to its own YAML preset (ADR 0001); on this
# dependency-free bundle a direct any still returns project rows, and the
# fallback must mirror whatever that preset returns.
SERVED_SELECTORS = {
    "defaults": {},
    "package-project": {"package": "__project__"},
    "scope-project": {"scope": "project"},
    "scope-deps": {"scope": "deps"},
}


@pytest.mark.parametrize("selectors", list(SERVED_SELECTORS.values()), ids=list(SERVED_SELECTORS))
@pytest.mark.parametrize("surface", SURFACES)
def test_an_api_zero_hit_serves_exactly_the_direct_any_response(
    indexed_db: Path, surface: str, selectors: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    request = {"query": DESCRIPTIVE_QUERY, **selectors}
    router = shipped_router(indexed_db, surface)
    fallback = serve_twice(router, caplog, kind="api", **request)
    direct = serve_twice(router, caplog, kind="any", **request)
    # Requested kind: the API pass alone (fallback off) finds nothing here.
    off_router = shipped_router(indexed_db, surface, fallback_off_config())
    assert serve_twice(off_router, caplog, kind="api", **request).response.items == ()
    assert_served_exactly_the_direct_any(fallback, direct)


@pytest.mark.parametrize("surface", SURFACES)
def test_an_api_zero_hit_on_an_empty_slice_ends_with_the_empty_label(
    indexed_db: Path, surface: str, caplog: pytest.LogCaptureFixture
) -> None:
    """``package=`` names a package this bundle never indexed: a direct any
    finds nothing on that slice, and so does the fallback, which searches the
    same slice instead of widening to the corpus."""
    request = {"query": DESCRIPTIVE_QUERY, "package": "fastapi"}
    router = shipped_router(indexed_db, surface)
    fallback = serve_twice(router, caplog, kind="api", **request)
    assert serve_twice(router, caplog, kind="any", **request).response.items == ()
    off_router = shipped_router(indexed_db, surface, fallback_off_config())
    off = serve_twice(off_router, caplog, kind="api", **request)
    assert off.response.items == ()  # requested kind: the API pass found nothing
    assert_both_passes_empty_like_the_retry_hint(fallback, off)


def _cli_search_stdout(db_path: Path, kind: str, capsys: pytest.CaptureFixture[str]) -> str:
    """``pydocs-mcp search "<phrase>" --kind <kind> --db <bundle>`` through ``main()``."""
    from pydocs_mcp.__main__ import main

    argv = ["pydocs-mcp", "search", DESCRIPTIVE_QUERY, "--kind", kind, "--db", str(db_path)]
    capsys.readouterr()
    with patch("sys.argv", argv):
        assert main() == 0
    return capsys.readouterr().out


def test_the_cli_command_prints_the_labelled_direct_any_output(
    indexed_db: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger=SUGGESTIONS_LOGGER):
        printed = [_cli_search_stdout(indexed_db, kind, capsys) for kind in ("api", "api", "any")]
    fallback, again, direct = printed
    assert fallback == again  # determinism through the real entry point
    # Raw API zero hit: one fallback record per --kind api run, none for --kind any.
    rules = [r.message for r in caplog.records if r.name == SUGGESTIONS_LOGGER]
    assert len(rules) == 2 and all(F_RULE in rule for rule in rules)
    # Served kind: the label after the header, then exactly the --kind any output.
    assert fallback.split("\n")[2] == F_SERVED
    assert without_label(fallback) == direct
    assert "No symbols found." not in fallback and "retry with" not in fallback


# ── Positive control: an exact-name hit is untouched by the flag ──────────


@pytest.mark.parametrize("surface", SURFACES)
def test_an_exact_name_api_hit_is_byte_identical_with_the_fallback_on_and_off(
    indexed_db: Path, surface: str, caplog: pytest.LogCaptureFixture
) -> None:
    request = {"query": EXACT_NAME_QUERY, "kind": "api"}
    on = serve_twice(shipped_router(indexed_db, surface), caplog, **request)
    off_router = shipped_router(indexed_db, surface, fallback_off_config())
    off = serve_twice(off_router, caplog, **request)
    # Requested kind api, answered by the API pass itself: its member row.
    assert [(item["kind"], item["qualified_name"]) for item in on.response.items] == [
        ("member", "pkg.tree.render_widget")
    ]
    # No zero hit, so no rule fires and nothing is labelled.
    assert on.rules == off.rules == [] and "suggestion" not in on.response.meta
    assert F_SERVED not in on.response.text
    assert (on.response.text, on.response.items) == (off.response.text, off.response.items)
    assert (on.response.meta, on.rendered_rows) == (off.response.meta, off.rendered_rows)


# ── Dense degrade inherited: the .tq sidecar absent ───────────────────────

# Routes every chunk search to the shipped BM25 ∥ dense preset, whose lexical
# half still answers when the dense half degrades to nothing.
_HYBRID_DOCS_OVERLAY = """\
pipelines:
  chunk:
    - default: true
      pipeline_path: pipelines/chunk_search_hybrid.yaml
  member:
    - default: true
      pipeline_path: pipelines/member_search.yaml
"""


def _docs_pipeline_config(tmp_path: Path, docs_pipeline: str) -> AppConfig:
    if docs_pipeline == "shipped-dense-graph":
        return AppConfig.load()
    overlay = tmp_path / "hybrid_docs.yaml"
    overlay.write_text(_HYBRID_DOCS_OVERLAY)
    return AppConfig.load(explicit_path=overlay)


@pytest.mark.parametrize(
    ("docs_pipeline", "degraded_rows_remain"),
    # Determined by running it: the shipped dense + graph default has no other
    # signal, so a direct any finds nothing (the empty outcome); the hybrid
    # preset keeps its BM25 rows (served). Neither raises — a missing sidecar
    # loads as an empty vector index.
    [("shipped-dense-graph", False), ("hybrid-bm25-dense", True)],
)
def test_a_missing_vector_sidecar_degrades_the_fallback_exactly_as_a_direct_any(
    indexed_db: Path,
    tmp_path: Path,
    docs_pipeline: str,
    degraded_rows_remain: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _docs_pipeline_config(tmp_path, docs_pipeline)
    request = {"query": DESCRIPTIVE_QUERY}
    with_sidecar = serve_twice(shipped_router(indexed_db, "mcp", config), caplog, **request)
    indexed_db.with_suffix(".tq").unlink()
    router = shipped_router(indexed_db, "mcp", config)
    direct = serve_twice(router, caplog, kind="any", **request)
    fallback = serve_twice(router, caplog, kind="api", **request)
    off_router = shipped_router(indexed_db, "mcp", fallback_off_config(config))
    off = serve_twice(off_router, caplog, kind="api", **request)
    # The degrade is real (the dense rows are gone, nothing raised), and the
    # requested kind's API pass alone still finds nothing.
    assert direct.response.items != with_sidecar.response.items and off.response.items == ()
    assert bool(direct.response.items) is degraded_rows_remain
    # No new marking: the one suggestion line is the ordinary served/empty label.
    assert fallback.response.text.count("[suggestion:") == 1
    if degraded_rows_remain:
        assert_served_exactly_the_direct_any(fallback, direct)
    else:
        assert_both_passes_empty_like_the_retry_hint(fallback, off)
