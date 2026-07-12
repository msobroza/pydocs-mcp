"""Decorator-based provider registry for embedder construction.

The embedder sibling of retrieval's ``ComponentRegistry``
(``retrieval/serialization.py``): same loud-collision and loud-unknown
conventions, but it registers BUILDER FUNCTIONS
(``Callable[[config], embedder]``) instead of classes with ``from_dict``.
Builders keep the heavy concrete imports function-local, so populating a
registry costs nothing at import time — the lazy-import contract the old
if/elif chain in ``build_embedder`` implemented by hand (fastembed / torch /
pylate must never load before the configured provider is actually built).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

C = TypeVar("C")  # config sub-model consumed by the builders
T = TypeVar("T")  # constructed embedder type


class ProviderRegistry(Generic[C, T]):
    """Decorator-based registry mapping a provider name to a builder function.

    Usage::

        embedder_registry: ProviderRegistry[EmbeddingConfig, Embedder] = (
            ProviderRegistry("embedding provider")
        )

        @embedder_registry.register("fastembed")
        def _build_fastembed(cfg: EmbeddingConfig) -> Embedder:
            from ...fastembed import FastEmbedEmbedder  # lazy — stays in the body
            return FastEmbedEmbedder(...)

        embedder_registry.build(cfg.provider, cfg)
    """

    def __init__(self, kind: str) -> None:
        # ``kind`` names the provider family in error messages
        # (e.g. "embedding provider") so an unknown-name failure reads the
        # same as the old hand-written chain's.
        self._kind = kind
        self._builders: dict[str, Callable[[C], T]] = {}

    def register(self, name: str) -> Callable[[Callable[[C], T]], Callable[[C], T]]:
        def decorator(builder: Callable[[C], T]) -> Callable[[C], T]:
            if name in self._builders:
                raise ValueError(f"{self._kind} {name!r} already registered")
            self._builders[name] = builder
            return builder

        return decorator

    def build(self, name: str, cfg: C) -> T:
        try:
            builder = self._builders[name]
        except KeyError:
            supported = ", ".join(repr(n) for n in sorted(self._builders))
            raise ValueError(f"Unknown {self._kind}: {name!r}. Supported: {supported}.") from None
        return builder(cfg)

    def names(self) -> frozenset[str]:
        """Registered provider names — pinned against the config Literal by test."""
        return frozenset(self._builders)


__all__ = ("ProviderRegistry",)
