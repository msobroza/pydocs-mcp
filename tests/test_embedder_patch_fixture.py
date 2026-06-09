"""Regression guard for the autouse build_embedder patch.

CI once turned red because ``build_retrieval_context`` constructs a real
``FastEmbedEmbedder`` at composition time, which downloads
``BAAI/bge-small-en-v1.5`` from the network in ``__post_init__``. On a runner
where that download failed, ~95 shape-asserting tests ERRORED at fixture setup
even though none of them needs real embeddings.

The cure is the autouse ``_patch_build_embedder_with_mock`` fixture in
``tests/conftest.py`` (mirrors the existing autouse LLM patch). These tests
prove it is live: we make the *real* embedder explode on construction, then
show that the production composition path
(``build_retrieval_context`` -> ``build_embedder``) still succeeds and yields
the mock — because the factory is patched before any FastEmbed download can
run.

If the autouse fixture is removed, ``test_build_retrieval_context_uses_mock``
fails immediately: the poisoned ``__post_init__`` propagates out of
``build_embedder`` and ``build_retrieval_context`` raises.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.factories import build_retrieval_context
from tests._fakes import MockEmbedder


@pytest.fixture
def _poison_fastembed_post_init(monkeypatch):
    """Make any real FastEmbedEmbedder construction blow up.

    If anything in the suite still reaches the concrete FastEmbed class
    (i.e. the autouse mock patch isn't covering this call site), this turns
    the silent network download into a loud, deterministic failure — no
    network access required to surface the regression.
    """
    from pydocs_mcp.extraction.strategies.embedders import fastembed as _fastembed

    def _boom(self) -> None:
        raise AssertionError(
            "FastEmbedEmbedder.__post_init__ ran — the autouse "
            "build_embedder mock is not covering this call site (would hit "
            "the network for BAAI/bge-small-en-v1.5).",
        )

    monkeypatch.setattr(_fastembed.FastEmbedEmbedder, "__post_init__", _boom)


def test_build_retrieval_context_uses_mock(tmp_path, _poison_fastembed_post_init):
    """The production composition path builds without touching FastEmbed.

    ``build_retrieval_context`` -> ``build_embedder(config.embedding)`` is the
    exact call the ``test_server.py`` / ``test_end_to_end.py`` fixtures make.
    With FastEmbed poisoned, this only passes when the autouse patch swaps in
    the mock embedder. The shipped default config selects ``provider=fastembed``
    so this is a faithful regression for the original CI failure.
    """
    config = AppConfig.load()
    assert config.embedding.provider == "fastembed"

    context = build_retrieval_context(tmp_path / "x.db", config)

    # The embedder threaded onto the context is the deterministic mock, sized
    # to the configured dim — never a real FastEmbedEmbedder.
    assert isinstance(context.embedder, MockEmbedder)
    assert context.embedder.dim == config.embedding.dim


def test_build_embedder_factory_returns_mock(_poison_fastembed_post_init):
    """The re-bound factory name on retrieval.factories yields the mock.

    Direct probe of the second bind site the autouse fixture patches — the
    ``from ... import build_embedder`` alias inside ``retrieval/factories.py``
    that production code dereferences.
    """
    from pydocs_mcp.retrieval import factories as _retrieval_factories

    config = AppConfig.load()
    embedder = _retrieval_factories.build_embedder(config.embedding)
    assert isinstance(embedder, MockEmbedder)
    assert embedder.dim == config.embedding.dim
