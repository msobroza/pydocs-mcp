"""A bundle indexed without an ``index_metadata`` stamp must still load for serving.

``run_index_pass`` stamps ``index_metadata`` last. A bundle produced by
``ProjectIndexer.index_project`` directly — the benchmark harness's path — or by
a CLI run interrupted before the stamp has no row there, so multirepo's
serve-time embedder guard falls back to ``packages.embedding_model``.

That column used to be NULL in every bundle (the stamp never landed), which the
guard treats as "cannot be checked, permit". Now that it is populated, the
fallback compares a real string against ``config.embedding.model_name``, so it
must be the SAME string: a bundle built with the configured embedder must never
be rejected as mismatched. Under the late-interaction preset the persisted
vectors are the LI model's, which is not what the guard describes at all, so
the LI stage records nothing and the fallback keeps permitting.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.multirepo import load_project, validate_project_embedder
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import MockEmbedder


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    pkg = root / "app"
    pkg.mkdir(parents=True)
    for i in range(24):
        (pkg / f"mod_{i:02d}.py").write_text(
            f'"""Module {i}."""\n\n\ndef run_{i:02d}(value: int) -> int:\n'
            f'    """Return value plus {i}."""\n    return value + {i}\n'
        )
    return root


def _index_without_stamp(monkeypatch, db_path: Path, project_dir: Path, config: AppConfig):
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.storage.factories import build_project_indexer

    # The dense embedder reports exactly the configured spelling, as every
    # shipped provider now does.
    monkeypatch.setattr(
        _embedders,
        "build_embedder",
        lambda cfg: MockEmbedder(dim=cfg.dim, model_name=cfg.model_name),
    )
    bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
    asyncio.run(bundle.orchestrator.index_project(project_dir, include_dependencies=False))


def test_dense_bundle_built_with_the_configured_embedder_validates(
    monkeypatch, tmp_path: Path, project_dir: Path
) -> None:
    overlay = tmp_path / "config.yaml"
    overlay.write_text("embedding:\n  model_name: ~/models/side-loaded\n")
    config = AppConfig.load(explicit_path=overlay)
    db_path = tmp_path / "dense.db"
    open_index_database(db_path).close()

    _index_without_stamp(monkeypatch, db_path, project_dir, config)

    project = load_project(db_path)
    assert project.metadata.embedding_model == "~/models/side-loaded"
    validate_project_embedder(project, model=config.embedding.model_name, dim=config.embedding.dim)


def test_late_interaction_bundle_validates_against_the_dense_config(
    monkeypatch, tmp_path: Path, project_dir: Path
) -> None:
    # Persists through the real fast-plaid UoW; the [late-interaction] extra is
    # opt-in and CI's test job does not install it.
    # WHY "fast_plaid.search", not "fast_plaid": the top-level package is pure Python and
    # imports even when the native usearch/numkong wheels cannot load (macOS 14 saw
    # numkong >= 7.5 fail on a libSystem symbol), which turned this skip into an error.
    pytest.importorskip("fast_plaid.search", reason="[late-interaction] extra not usable here")
    from pydocs_mcp import pipelines as shipped

    class _FakeMultiVectorEmbedder:
        dim = 16
        model_name = "lightonai/LateOn-Code"

        def _tokens(self, text: str):
            import hashlib

            import numpy as np

            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
            raw = np.random.default_rng(seed).standard_normal((4, self.dim)).astype(np.float32)
            return list(raw / np.maximum(np.linalg.norm(raw, axis=1, keepdims=True), 1e-9))

        async def embed_query(self, text: str):
            return self._tokens(text)

        async def embed_chunks(self, texts):
            return tuple(self._tokens(t) for t in texts)

    li_yaml = Path(shipped.__file__).parent / "ingestion_late_interaction.yaml"
    overlay = tmp_path / "li.yaml"
    overlay.write_text(
        "late_interaction:\n  enabled: true\n  model_name: lightonai/LateOn-Code\n"
        f"extraction:\n  ingestion:\n    pipeline_path: {li_yaml}\n"
    )
    config = AppConfig.load(explicit_path=overlay)
    db_path = tmp_path / "li.db"
    open_index_database(db_path).close()

    from pydocs_mcp.extraction.strategies import embedders as _embedders

    monkeypatch.setattr(
        _embedders, "build_multi_vector_embedder", lambda cfg: _FakeMultiVectorEmbedder()
    )
    _index_without_stamp(monkeypatch, db_path, project_dir, config)

    project = load_project(db_path)
    # The fallback must not have picked up the LI model as the dense identity.
    assert project.metadata.embedding_model == ""
    validate_project_embedder(project, model=config.embedding.model_name, dim=config.embedding.dim)
