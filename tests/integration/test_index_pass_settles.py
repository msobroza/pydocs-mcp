"""Repeated identical index passes must settle, whatever the embedder is called.

Reviving ``packages.embedding_model`` re-armed a comparison between two
independently-derived strings: the stamp comes from the embedder instance, while
``run_index_pass`` swept with ``config.embedding.model_name``. They agree only
for a dense embedder whose provider passes the configured name through verbatim.
Two shipped configurations break that assumption, and in both the sweep then
flags every package on every pass, blanks ``packages.content_hash``, and defeats
the package-level cache permanently:

* the late-interaction preset, where the stamp is ``late_interaction.model_name``
  but the sweep compares ``embedding.model_name`` — different config sections
  that can never be equal;
* an airgap side-load, where ``SentenceTransformersEmbedder`` (and PyLate)
  rewrite ``model_name`` to the expanded local directory, so the configured
  spelling and the reported name diverge.

Invalidation on an embedder change is handled structurally instead: the embedder
identity folds into ``ingestion_pipeline_hash``, which folds into the package
content hash, so a real change misses the cache on its own. These tests pin the
settling property that the string comparison broke.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder
from tests._index_fixture import run_pass_with_embedder
from tests.integration._late_interaction_guard import skip_unless_fast_plaid_native_loads


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Enough modules that fast-plaid can train a codec on the LI path.

    The multi-vector index refuses to build from a handful of chunks
    ("no heldout samples were generated"), which is a property of the real
    backend rather than of what these tests pin.
    """
    root = tmp_path / "proj"
    pkg = root / "app"
    pkg.mkdir(parents=True)
    for i in range(24):
        (pkg / f"mod_{i:02d}.py").write_text(
            f'"""Module {i} of the settling fixture."""\n\n\n'
            f"def run_{i:02d}(value: int) -> int:\n"
            f'    """Return value plus {i}, so each module embeds distinctly."""\n'
            f"    return value + {i}\n"
        )
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "settle.db"
    open_index_database(path).close()
    return path


def test_settles_when_the_embedder_renames_itself(
    tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """Airgap shape: the embedder reports a resolved path, the config a spelling.

    ``SentenceTransformersEmbedder.__post_init__`` rewrites ``model_name`` to the
    expanded side-load directory, so the two legitimately differ. That must not
    look like a model swap on every pass.
    """
    overlay = tmp_path / "config.yaml"
    overlay.write_text("embedding:\n  model_name: ~/models/side-loaded\n")
    config = AppConfig.load(explicit_path=overlay)

    # The embedder reports the RESOLVED directory, as the real one does.
    embedder = CountingEmbedder(inner=MockEmbedder(model_name="/home/u/models/side-loaded"))

    first = run_pass_with_embedder(config, db_path, project_dir, embedder=embedder)
    assert first.project_indexed is True

    embedder.calls.clear()
    second = run_pass_with_embedder(config, db_path, project_dir, embedder=embedder)

    assert second.project_indexed is False, (
        "an embedder whose reported name differs from the configured spelling "
        "was treated as a model swap, re-indexing the corpus on every pass"
    )
    assert embedder.calls == []


def test_settles_under_the_late_interaction_preset(
    tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """LI shape: the stamp names the LI model, the sweep compares the dense one."""
    # Persists through the real fast-plaid UoW; the [late-interaction] extra is
    # opt-in and CI's test job does not install it. The stage-level contract is
    # pinned without the extra in tests/extraction/test_embed_chunks_multi_vector.py.
    skip_unless_fast_plaid_native_loads()
    from pydocs_mcp import pipelines as shipped

    li_yaml = Path(shipped.__file__).parent / "ingestion_late_interaction.yaml"

    class _FakeMultiVectorEmbedder:
        """Deterministic per-text multi-vectors.

        The vectors must actually DIFFER between chunks: fast-plaid trains a
        codec over them and a degenerate (all-identical) set makes torch abort
        the process rather than raise.
        """

        dim = 16
        model_name = "lightonai/LateOn-Code"

        def __init__(self) -> None:
            self.calls: list[int] = []

        def _tokens(self, text: str):
            import hashlib

            import numpy as np

            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
            rng = np.random.default_rng(seed)
            raw = rng.standard_normal((4, self.dim)).astype(np.float32)
            norms = np.linalg.norm(raw, axis=1, keepdims=True)
            return list(raw / np.maximum(norms, 1e-9))

        async def embed_query(self, text: str):
            return self._tokens(text)

        async def embed_chunks(self, texts):
            self.calls.append(len(texts))
            return tuple(self._tokens(t) for t in texts)

    overlay = tmp_path / "li_config.yaml"
    overlay.write_text(
        "late_interaction:\n  enabled: true\n  model_name: lightonai/LateOn-Code\n"
        f"extraction:\n  ingestion:\n    pipeline_path: {li_yaml}\n"
    )
    config = AppConfig.load(explicit_path=overlay)

    mve = _FakeMultiVectorEmbedder()
    dense = CountingEmbedder(inner=MockEmbedder(model_name=config.embedding.model_name))

    mp = pytest.MonkeyPatch()
    try:
        from pydocs_mcp.extraction.strategies import embedders as _embedders

        mp.setattr(_embedders, "build_multi_vector_embedder", lambda cfg: mve)

        first = run_pass_with_embedder(config, db_path, project_dir, embedder=dense)
        assert first.project_indexed is True

        second = run_pass_with_embedder(config, db_path, project_dir, embedder=dense)
        assert second.project_indexed is False, (
            "the late-interaction preset re-indexed an unchanged project: the "
            "LI model name was compared against the dense model name"
        )
    finally:
        mp.undo()
