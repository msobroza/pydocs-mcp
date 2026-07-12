"""ProviderRegistry — decorator-based embedder provider registration.

Replaces the hand-written if/elif chain in ``build_embedder`` /
``build_multi_vector_embedder`` with the repo's registry-plus-decorator
house pattern (``@step_registry.register`` precedent). The registry stores
BUILDER FUNCTIONS whose bodies keep the heavy concrete imports
function-local, so registering a provider costs nothing at import time —
the lazy-import contract the old chain implemented by hand.
"""

from __future__ import annotations

import subprocess
import sys
from typing import get_args

import pytest

from pydocs_mcp.extraction.strategies.embedders import (
    embedder_registry,
    multi_vector_embedder_registry,
)
from pydocs_mcp.extraction.strategies.embedders.registry import ProviderRegistry
from pydocs_mcp.retrieval.config import EmbeddingConfig, LateInteractionConfig

pytestmark = pytest.mark.real_embedder


# ── the generic registry mechanics ─────────────────────────────────────────


def test_register_and_build_round_trip() -> None:
    reg: ProviderRegistry[str, int] = ProviderRegistry("toy provider")

    @reg.register("double")
    def _double(cfg: str) -> int:
        return len(cfg) * 2

    assert reg.build("double", "abc") == 6


def test_register_returns_builder_unchanged() -> None:
    reg: ProviderRegistry[str, int] = ProviderRegistry("toy provider")

    def _builder(cfg: str) -> int:
        return 1

    assert reg.register("x")(_builder) is _builder


def test_duplicate_registration_raises_loudly() -> None:
    reg: ProviderRegistry[str, int] = ProviderRegistry("toy provider")
    reg.register("x")(lambda cfg: 1)

    with pytest.raises(ValueError, match="toy provider 'x' already registered"):
        reg.register("x")(lambda cfg: 2)


def test_unknown_provider_lists_supported_names() -> None:
    reg: ProviderRegistry[str, int] = ProviderRegistry("toy provider")
    reg.register("a")(lambda cfg: 1)
    reg.register("b")(lambda cfg: 2)

    with pytest.raises(ValueError, match=r"Unknown toy provider: 'nope'.*'a'.*'b'"):
        reg.build("nope", "cfg")


# ── the shipped registries ─────────────────────────────────────────────────


def test_embedder_registry_matches_provider_literal() -> None:
    # The EmbeddingConfig.provider Literal is the config-load-time allowlist;
    # the registry is the runtime dispatch. This parity pin makes it
    # impossible to add a provider to one and forget the other.
    literal_names = set(get_args(EmbeddingConfig.model_fields["provider"].annotation))
    assert embedder_registry.names() == literal_names


def test_multi_vector_registry_matches_provider_literal() -> None:
    literal_names = set(get_args(LateInteractionConfig.model_fields["provider"].annotation))
    assert multi_vector_embedder_registry.names() == literal_names


def test_registering_providers_stays_import_light() -> None:
    # The whole point of builder functions over decorated concrete classes:
    # importing the embedders package (which populates both registries) must
    # NOT import any heavy concrete module. Subprocess keeps the check
    # hermetic — this test session has long since imported fastembed.
    code = (
        "import sys; import pydocs_mcp.extraction.strategies.embedders; "
        "heavy = [m for m in ('fastembed', 'sentence_transformers', 'pylate', "
        "'pydocs_mcp.extraction.strategies.embedders.fastembed', "
        "'pydocs_mcp.extraction.strategies.embedders.openai', "
        "'pydocs_mcp.extraction.strategies.embedders.sentence_transformers', "
        "'pydocs_mcp.extraction.strategies.embedders.pylate') "
        "if m in sys.modules]; "
        "assert not heavy, f'heavy modules imported at registration time: {heavy}'"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120)
