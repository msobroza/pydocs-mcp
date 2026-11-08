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

Every equality request is issued twice (determinism, R14). Outcome tests keep
the requested kind, the raw API zero hit (the fired rule) and the served kind
(the label plus each row's own ``kind``) apart.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.tool_response import SearchEnvelope, ToolResponse
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db
from tests.application.test_api_zero_hit_fallback_any import (
    F_EMPTY,
    F_RULE,
    F_SERVED,
    POINTER,
    Z_RULE,
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


def fallback_off_config() -> AppConfig:
    """The shipped config with ``output.suggestions.api_zero_hit_fallback_any`` off."""
    config = AppConfig.load()
    flags = config.output.suggestions.model_copy(update={"api_zero_hit_fallback_any": False})
    return config.model_copy(
        update={"output": config.output.model_copy(update={"suggestions": flags})}
    )


def shipped_router(db_path: Path, surface: str, config: AppConfig | None = None) -> ToolRouter:
    router, _services = build_routers(config or AppConfig.load(), db_path=db_path, surface=surface)
    return router


def search_twice(
    router: ToolRouter, caplog: pytest.LogCaptureFixture, **fields: Any
) -> tuple[ToolResponse, list[str]]:
    """Issue the request twice; assert both responses (and fired rules) are
    identical; return one of them with its fired rules."""
    served = [_search_once(router, caplog, **fields) for _ in range(2)]
    (first, first_rules), (second, second_rules) = served
    assert (first.text, first.items, first.meta) == (second.text, second.items, second.meta)
    assert first_rules == second_rules
    return first, first_rules


def _search_once(
    router: ToolRouter, caplog: pytest.LogCaptureFixture, **fields: Any
) -> tuple[ToolResponse, list[str]]:
    caplog.clear()
    with caplog.at_level("INFO", logger=SUGGESTIONS_LOGGER):
        response = asyncio.run(router.search_codebase(SearchInput(**fields)))
    rules = [json.loads(r.message)["rule"] for r in caplog.records if r.name == SUGGESTIONS_LOGGER]
    return response, rules


def without_label_line(text: str) -> str:
    """``text`` with exactly the label line and its newline removed."""
    assert text.count(F_SERVED + "\n") == 1
    return text.replace(F_SERVED + "\n", "", 1)


def meta_without_suggestion(meta: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in meta.items() if key != "suggestion"}


# ── Equality with a direct kind="any", on both surfaces ───────────────────


@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_an_api_zero_hit_serves_exactly_the_direct_any_response(
    indexed_db: Path, surface: str, caplog: pytest.LogCaptureFixture
) -> None:
    router = shipped_router(indexed_db, surface)
    fallback, rules = search_twice(router, caplog, query=DESCRIPTIVE_QUERY, kind="api")
    direct, direct_rules = search_twice(router, caplog, query=DESCRIPTIVE_QUERY, kind="any")
    # Requested kind: the API pass alone finds nothing on this bundle.
    off_router = shipped_router(indexed_db, surface, fallback_off_config())
    api_alone, _off_rules = search_twice(off_router, caplog, query=DESCRIPTIVE_QUERY, kind="api")
    assert api_alone.items == ()
    # Raw API zero hit: the fallback rule fired, and only it.
    assert rules == [F_RULE] and direct_rules == []
    # Served kind: the label is the first line after the freshness header…
    header, blank, label = fallback.text.split("\n")[:3]
    assert header.startswith("[index: ") and blank == "" and label == F_SERVED
    assert fallback.meta["suggestion"] == F_SERVED
    # …and the rows are the direct any rows: order, ids, kinds, scores.
    assert fallback.items and fallback.items == direct.items
    # The fallback's member half re-ran the same lookup and found nothing too.
    assert {item["kind"] for item in fallback.items} == {"chunk"}
    assert without_label_line(fallback.text) == direct.text
    assert fallback.meta["truncated"] == direct.meta["truncated"]
    assert meta_without_suggestion(fallback.meta) == meta_without_suggestion(direct.meta)
    SearchEnvelope.model_validate(fallback.structured())
    SearchEnvelope.model_validate(direct.structured())


def _cli_search_stdout(db_path: Path, kind: str, capsys: pytest.CaptureFixture[str]) -> str:
    """``pydocs-mcp search "<phrase>" --kind <kind> --db <bundle>`` through ``main()``."""
    from pydocs_mcp.__main__ import main

    argv = ["pydocs-mcp", "search", DESCRIPTIVE_QUERY, "--kind", kind, "--db", str(db_path)]
    capsys.readouterr()
    with patch("sys.argv", argv):
        assert main() == 0
    return capsys.readouterr().out


def test_the_cli_command_prints_the_labelled_direct_any_output(
    indexed_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    printed = [_cli_search_stdout(indexed_db, kind, capsys) for kind in ("api", "api", "any")]
    fallback, again, direct = printed
    assert fallback == again  # determinism through the real entry point
    # Requested --kind api; the served kind is named by the label after the header.
    assert fallback.split("\n")[2] == F_SERVED
    assert without_label_line(fallback) == direct
    assert "No symbols found." not in fallback and "retry with" not in fallback


# ── Positive control: an exact-name hit is untouched by the flag ──────────


@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_an_exact_name_api_hit_is_byte_identical_with_the_fallback_on_and_off(
    indexed_db: Path, surface: str, caplog: pytest.LogCaptureFixture
) -> None:
    request = {"query": EXACT_NAME_QUERY, "kind": "api"}
    on, on_rules = search_twice(shipped_router(indexed_db, surface), caplog, **request)
    off_router = shipped_router(indexed_db, surface, fallback_off_config())
    off, off_rules = search_twice(off_router, caplog, **request)
    # Requested kind api, answered by the API pass itself: its member row.
    assert [(item["kind"], item["qualified_name"]) for item in on.items] == [
        ("member", "pkg.tree.render_widget")
    ]
    # No zero hit, so no rule fires and nothing is labelled.
    assert on_rules == off_rules == [] and "suggestion" not in on.meta
    assert F_SERVED not in on.text
    assert (on.text, on.items, on.meta) == (off.text, off.items, off.meta)


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
    ("docs_pipeline", "outcome"),
    # Determined by running it: the shipped dense + graph default has no other
    # signal, so a direct any finds nothing (E); the hybrid preset keeps its
    # BM25 rows (S). Neither raises — a missing sidecar loads as an empty index.
    [("shipped-dense-graph", "E"), ("hybrid-bm25-dense", "S")],
)
def test_a_missing_vector_sidecar_degrades_the_fallback_exactly_as_a_direct_any(
    indexed_db: Path,
    tmp_path: Path,
    docs_pipeline: str,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _docs_pipeline_config(tmp_path, docs_pipeline)
    any_request = {"query": DESCRIPTIVE_QUERY, "kind": "any"}
    with_sidecar, _rules = search_twice(
        shipped_router(indexed_db, "mcp", config), caplog, **any_request
    )
    indexed_db.with_suffix(".tq").unlink()
    router = shipped_router(indexed_db, "mcp", config)
    direct, _direct_rules = search_twice(router, caplog, **any_request)
    fallback, rules = search_twice(router, caplog, query=DESCRIPTIVE_QUERY, kind="api")
    # The degrade is real: the direct any lost its dense rows, and did not raise.
    assert direct.items != with_sidecar.items and bool(direct.items) is (outcome == "S")
    # Requested kind api; raw API zero hit: the fallback rule fired (after Z on E).
    assert rules == ([F_RULE] if outcome == "S" else [Z_RULE, F_RULE])
    # Served kind: exactly the direct any's rows (none on E), and no new marking —
    # the one suggestion line is the ordinary S or E label.
    assert fallback.items == direct.items
    assert fallback.text.count("[suggestion:") == 1
    assert meta_without_suggestion(fallback.meta) == meta_without_suggestion(direct.meta)
    header = fallback.text.split("\n", 1)[0]
    if outcome == "S":
        assert fallback.meta["suggestion"] == F_SERVED
        assert without_label_line(fallback.text) == direct.text
        assert {item["kind"] for item in fallback.items} == {"chunk"}
    else:
        assert fallback.meta["suggestion"] == F_EMPTY
        assert fallback.text == f"{header}\n\nNo symbols found.\n{POINTER}\n\n{F_EMPTY}\n"
