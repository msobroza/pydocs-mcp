"""Stdlib qnames module-level loader + config slot tests."""

from __future__ import annotations

import pytest


def test_get_resolver_config_returns_default_before_configuration():
    """Before configure_from_app_config runs, accessor returns include_stdlib=True."""
    from pydocs_mcp.extraction.strategies.stdlib_qnames import _get_resolver_config
    from pydocs_mcp.retrieval.config import ReferenceResolverConfig

    cfg = _get_resolver_config()
    assert isinstance(cfg, ReferenceResolverConfig)
    assert cfg.include_stdlib is True


def test_load_stdlib_qnames_returns_frozenset_with_expected_entries():
    """The loader returns a frozenset; spot-check canonical examples."""
    from pydocs_mcp.extraction.strategies.stdlib_qnames import load_stdlib_qnames

    qnames = load_stdlib_qnames()
    assert isinstance(qnames, frozenset)
    assert len(qnames) >= 1500
    assert "os" in qnames
    assert "os.path.join" in qnames
    assert "asyncio" in qnames
    assert "len" in qnames
    assert "builtins.len" in qnames


def test_load_stdlib_qnames_caches_after_first_call():
    """Lazy module-level cache — second call returns the SAME frozenset object."""
    from pydocs_mcp.extraction.strategies.stdlib_qnames import load_stdlib_qnames

    a = load_stdlib_qnames()
    b = load_stdlib_qnames()
    assert a is b  # identity check — cached


def test_set_resolver_config_overwrites_module_constant():
    """`_set_resolver_config(cfg)` updates the module-level slot for tests."""
    from pydocs_mcp.extraction.strategies import stdlib_qnames as mod
    from pydocs_mcp.retrieval.config import ReferenceResolverConfig

    original = mod._get_resolver_config()
    new_cfg = ReferenceResolverConfig(include_stdlib=False)
    mod._set_resolver_config(new_cfg)
    try:
        assert mod._get_resolver_config().include_stdlib is False
    finally:
        # Restore module state so other tests aren't affected.
        mod._set_resolver_config(original)
