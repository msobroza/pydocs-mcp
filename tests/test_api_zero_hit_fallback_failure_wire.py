"""A failed ``kind="api"`` zero-hit fallback on a real index, through the composition root (ADR 0027).

The project is indexed with the suite's mock embedder. It is then served with
``QueryEmbeddingFailsEmbedder``, whose query embedding raises the way an
unavailable embedder does. It is installed at the same factory seam the suite's
autouse embedder fixture patches. The member lookup never embeds, so the API
pass runs and finds nothing. The fallback pass embeds the query and raises.
Expected:

- the router raises ``ServiceUnavailableError`` carrying the fixed prefix and
  the cause, chained from the original;
- the CLI prints the usual ``Error: …`` line and exits 1;
- no rule is logged.

Two controls show that the fallback pass is what fails. With the fallback off,
the same request is today's zero hit and succeeds. A direct ``kind="any"``
fails too, but without the fallback's prefix.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._fakes import MockEmbedder
from tests._index_fixture import index_project_to_db

FAILURE_PREFIX = 'search_codebase(kind="api") found no symbols; its kind="any" fallback failed'
EMBEDDER_DOWN = "query embedding endpoint unreachable"
DESCRIPTIVE_QUERY = "nested serialization"
SUGGESTIONS_LOGGER = "pydocs_mcp.application.suggestions"

# The answer sits in a METHOD's docstring, which no member row holds, so the
# member lookup genuinely misses and only the kind="any" path could answer.
_CODEC_PY = '''\
"""Codec helpers."""


class Codec:
    """Encodes records."""

    def encode_tree(self) -> bytes:
        """Nested serialization of every child record."""
        return b""
'''


class QueryEmbeddingFailsEmbedder(MockEmbedder):
    """Embeds chunks like the mock, but every query embedding raises — an
    unavailable embedding endpoint at serve time."""

    async def embed_query(self, text: str):
        raise RuntimeError(EMBEDDER_DOWN)


def build_query_embedding_fails_embedder(cfg) -> QueryEmbeddingFailsEmbedder:
    return QueryEmbeddingFailsEmbedder(dim=cfg.dim, model_name=cfg.model_name)


@pytest.fixture
def served_with_a_failing_query_embedder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A bundle indexed with the mock embedder, then served by the failing one."""
    project = tmp_path / "codecproj"
    (project / "codec").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "codecproj"\nversion = "0"\n')
    (project / "codec" / "__init__.py").write_text("")
    (project / "codec" / "records.py").write_text(_CODEC_PY)
    db = index_project_to_db(project, tmp_path / "codec.db")
    # The serve-time seam the autouse mock patches (retrieval.factories).
    monkeypatch.setattr(
        "pydocs_mcp.retrieval.factories.build_embedder", build_query_embedding_fails_embedder
    )
    return db


def fallback_off_overlay(tmp_path: Path) -> Path:
    overlay = tmp_path / "fallback_off.yaml"
    overlay.write_text("output:\n  suggestions:\n    api_zero_hit_fallback_any: false\n")
    return overlay


def run_cli_search(db: Path, kind: str, capsys, config: Path | None = None) -> tuple[int, str, str]:
    """``pydocs-mcp [--config X] search "<phrase>" --kind <kind> --db <bundle>`` via ``main()``."""
    from pydocs_mcp.__main__ import main

    prefix = ["pydocs-mcp"] + (["--config", str(config)] if config else [])
    argv = prefix + ["search", DESCRIPTIVE_QUERY, "--kind", kind, "--db", str(db)]
    capsys.readouterr()
    with patch("sys.argv", argv):
        code = main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("surface", ["mcp", "cli"])
async def test_a_failed_fallback_raises_the_typed_error_from_the_composition_root(
    served_with_a_failing_query_embedder: Path, surface: str, caplog: pytest.LogCaptureFixture
) -> None:
    router, _services = build_routers(
        AppConfig.load(), db_path=served_with_a_failing_query_embedder, surface=surface
    )
    with caplog.at_level("INFO", logger=SUGGESTIONS_LOGGER):
        with pytest.raises(ServiceUnavailableError) as caught:
            await router.search_codebase(SearchInput(query=DESCRIPTIVE_QUERY, kind="api"))
    assert str(caught.value) == f"{FAILURE_PREFIX}: RuntimeError: {EMBEDDER_DOWN}"
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert [r for r in caplog.records if r.name == SUGGESTIONS_LOGGER] == []


def test_the_cli_prints_the_fallback_failure_and_exits_1(
    served_with_a_failing_query_embedder: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger=SUGGESTIONS_LOGGER):
        code, out, err = run_cli_search(served_with_a_failing_query_embedder, "api", capsys)
    assert code == 1 and out == ""
    assert err.splitlines()[0] == f"Error: {FAILURE_PREFIX}: RuntimeError: {EMBEDDER_DOWN}"
    assert [r for r in caplog.records if r.name == SUGGESTIONS_LOGGER] == []


def test_the_failure_comes_from_the_fallback_pass(
    served_with_a_failing_query_embedder: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = served_with_a_failing_query_embedder
    # Requested kind api, fallback off: the API pass alone never embeds, so
    # today's zero hit succeeds on the same failing embedder.
    code, out, _err = run_cli_search(db, "api", capsys, config=fallback_off_overlay(tmp_path))
    assert code == 0 and "No symbols found." in out and "retry with" in out
    # A direct kind="any" fails on the embedder too, without the fallback's prefix.
    code, out, err = run_cli_search(db, "any", capsys)
    assert code == 1 and out == ""
    assert err.startswith("Error: ") and FAILURE_PREFIX not in err and EMBEDDER_DOWN in err
