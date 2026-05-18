"""Bundled stdlib + builtins qnames for the reference resolver (AC #15 follow-up to #5c).

Loaded lazily on first access via importlib.resources. The
``include_stdlib`` knob in `reference_graph.resolver.include_stdlib`
gates whether `IndexingService._resolve_references` merges this set into
the resolver's qname universe.

Module-level state pattern: `_RESOLVER_CONFIG` is read by IndexingService;
`configure_from_app_config(cfg)` in `application/mcp_inputs.py` calls
`_set_resolver_config(cfg.reference_graph.resolver)` at server / CLI
startup. Matches the same pattern used by `_CAPTURE_CONFIG` in
`pipeline/stages.py` and `_LIMIT_DEFAULT`/`_LIMIT_MAX` in `mcp_inputs.py`.
"""
from __future__ import annotations

import json
from importlib.resources import files

from pydocs_mcp.retrieval.config import ReferenceResolverConfig

# Module-level config slot. Defaults to safe shipped values; overwritten at
# server / CLI startup by `configure_from_app_config(cfg)`.
_RESOLVER_CONFIG: ReferenceResolverConfig = ReferenceResolverConfig()

# Lazy-loaded cache of the bundled stdlib qnames frozenset. Module-level
# cache so repeated reindex calls don't re-parse the JSON.
_STDLIB_QNAMES_CACHE: frozenset[str] | None = None


def _get_resolver_config() -> ReferenceResolverConfig:
    """Accessor for the module-level resolver config (test-visible)."""
    return _RESOLVER_CONFIG


def _set_resolver_config(cfg: ReferenceResolverConfig) -> None:
    """Module-internal setter called from `mcp_inputs.configure_from_app_config`
    at server / CLI startup. Tests can also call this directly to override.
    """
    global _RESOLVER_CONFIG
    _RESOLVER_CONFIG = cfg


def load_stdlib_qnames() -> frozenset[str]:
    """Return the bundled stdlib + builtins qnames as a frozen set.

    Lazy module-level cache — first call parses the JSON; subsequent calls
    return the same frozenset object (identity-equal).
    """
    global _STDLIB_QNAMES_CACHE
    if _STDLIB_QNAMES_CACHE is None:
        text = files("pydocs_mcp.defaults").joinpath("stdlib_qnames.json").read_text()
        data = json.loads(text)
        _STDLIB_QNAMES_CACHE = frozenset(data["qnames"])
    return _STDLIB_QNAMES_CACHE
