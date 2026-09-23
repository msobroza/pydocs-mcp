"""A switched structuring model re-extracts ONCE and then settles (#347).

With ``decision_capture.llm_structuring.enabled``, the model under ``llm:``
structures every mined decision, and its grounded answer is persisted on the
project's decision records. Structuring runs inside ``capture_decisions``, on
every pass and before the package cache check — so while the model identity
reached no cache key, switching ``llm.model_name`` paid the new model on every
pass and discarded its answer as a cache hit, forever, healed only by
``--force``.

Proven over real passes through a real composition root, with the LLM builder
replaced by a recording fake that answers per model: ``model-a`` structures
nothing, ``model-b`` structures the one mined decision.

1. the ``model-a`` pass indexes and then settles;
2. switching to ``model-b`` re-extracts once and PERSISTS model-b's structured
   fields — before the fix this pass was a cache hit that threw them away;
3. the next ``model-b`` pass settles;
4. rotating only ``api_key`` re-extracts nothing, although the rotated key
   reaches the client builder;
5. back to the shipped defaults (structuring off) restores the exact stock hash.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval import llm_clients
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder, RecordingLlmClientBuilder
from tests._index_fixture import run_pass_with_embedder

_RATIONALE = "retries stay bounded so a hung backend never stalls indexing"
_MARKER = f"# WHY: {_RATIONALE}"


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate ``AppConfig.load()`` from the developer's own config: "stock"
    here must mean the shipped defaults, not whatever ``PYDOCS_CONFIG_PATH``,
    ``./pydocs-mcp.yaml``, ``~/.config/pydocs-mcp/config.yaml`` or a
    ``PYDOCS_DECISION_CAPTURE*`` / ``PYDOCS_LLM*`` env var says."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    prefixes = ("PYDOCS_DECISION_CAPTURE", "PYDOCS_LLM")
    for name in [name for name in os.environ if name.startswith(prefixes)]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


@pytest.fixture
def llm_builder(monkeypatch: pytest.MonkeyPatch) -> RecordingLlmClientBuilder:
    """Replaces the autouse fake builder, whose empty reply table would raise on
    the first structuring call (and a project pass does not catch it)."""
    builder = RecordingLlmClientBuilder()
    monkeypatch.setattr(llm_clients, "build_llm_client", builder)
    return builder


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """One module with one ``# WHY:`` marker: exactly one mined decision."""
    pkg = tmp_path / "proj" / "app"
    pkg.mkdir(parents=True)
    (pkg / "retry.py").write_text(
        f'"""Retry policy."""\n\n\ndef attempt(value: int) -> int:\n    {_MARKER}\n    return value\n'
    )
    return pkg.parent


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "structuring.db"
    open_index_database(path).close()
    return path


def _structuring_config(tmp_path: Path, model_name: str, api_key: str = "") -> AppConfig:
    overlay = tmp_path / f"structuring-{model_name}-{api_key or 'nokey'}.yaml"
    key_line = f"  api_key: {api_key}\n" if api_key else ""
    overlay.write_text(
        "decision_capture:\n  llm_structuring:\n    enabled: true\n"
        f"llm:\n  model_name: {model_name}\n{key_line}"
    )
    return AppConfig.load(explicit_path=overlay)


def _project_hash(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_hash FROM packages WHERE name = ?", (PROJECT_PACKAGE_NAME,)
        ).fetchone()
    assert row is not None, "the project package was never persisted"
    return str(row[0])


def _decision_rows(db_path: Path) -> list[tuple[str, str, str | None]]:
    """``(title, verification, structured)`` for every project decision."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT title, verification, structured FROM decision_records "
            "WHERE package = ? ORDER BY title",
            (PROJECT_PACKAGE_NAME,),
        ).fetchall()
    return [(str(title), str(tier), structured) for title, tier, structured in rows]


def _structuring_reply(title: str) -> str:
    """A reply that structures ``title`` with a field grounded in its evidence."""
    return json.dumps({"decisions": [{"title": title, "decision": _RATIONALE}]})


def test_a_switched_structuring_model_settles_instead_of_looping(
    db_path: Path,
    project_dir: Path,
    tmp_path: Path,
    llm_builder: RecordingLlmClientBuilder,
) -> None:
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))
    stock = AppConfig.load()
    model_a = _structuring_config(tmp_path, "model-a")
    model_b = _structuring_config(tmp_path, "model-b")

    assert run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder).project_indexed
    stock_hash = _project_hash(db_path)

    # (1) model-a structures nothing: its pass indexes (structuring moved the
    # decision token off the stock pin), then settles.
    assert run_pass_with_embedder(model_a, db_path, project_dir, embedder=embedder).project_indexed
    assert llm_builder.chat_calls > 0, "structuring never consulted the LLM"
    model_a_hash = _project_hash(db_path)
    [(title, tier, structured)] = _decision_rows(db_path)
    assert (tier, structured) == ("verbatim", None)
    settled = run_pass_with_embedder(model_a, db_path, project_dir, embedder=embedder)
    assert settled.project_indexed is False
    assert _project_hash(db_path) == model_a_hash

    # (2) model-b re-extracts once and its structured fields land. Before the
    # fix this pass was a cache hit: model-b was paid, then its answer dropped.
    llm_builder.replies_by_model["model-b"] = _structuring_reply(title)
    switched = run_pass_with_embedder(model_b, db_path, project_dir, embedder=embedder)
    assert switched.project_indexed is True
    model_b_hash = _project_hash(db_path)
    assert model_b_hash != model_a_hash
    [(_title, tier, structured)] = _decision_rows(db_path)
    assert tier == "verified"
    assert json.loads(structured or "{}") == {"decision": _RATIONALE}

    # (3) …and then it settles: no write, nothing embedded, the hash frozen.
    calls_before = list(embedder.calls)
    settled = run_pass_with_embedder(model_b, db_path, project_dir, embedder=embedder)
    assert settled.project_indexed is False
    assert embedder.calls == calls_before, "a settled pass must embed nothing"
    assert _project_hash(db_path) == model_b_hash

    # (4) a rotated api_key reaches the builder but never the hash.
    rotated = _structuring_config(tmp_path, "model-b", api_key="sk-rotated")
    rotated_pass = run_pass_with_embedder(rotated, db_path, project_dir, embedder=embedder)
    assert rotated_pass.project_indexed is False
    assert llm_builder.built[-1][0].api_key == "sk-rotated"
    assert _project_hash(db_path) == model_b_hash

    # (5) back to the shipped defaults: structuring off folds nothing, so the
    # exact stock hash comes back.
    assert run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder).project_indexed
    assert _project_hash(db_path) == stock_hash
