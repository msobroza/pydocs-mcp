"""Decorator-based registries for the four pluggable axes (spec §4.4).

Mirrors ``pydocs_mcp.retrieval.serialization.ComponentRegistry`` — same
``register(name)`` decorator, same ``build(name, **kwargs)`` constructor.
Four module-level instances (one per axis) keep namespaces disjoint so
a dataset called ``"recall"`` can't accidentally mask a metric of the
same name.

A registered class is its own constructor: ``build(name, **kwargs)`` calls
``cls(**kwargs)``. This is intentionally simpler than the retrieval
registry — eval plug-ins do not need the ``from_dict`` / ``BuildContext``
recursion machinery, so we don't pay for it.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    # WHY: importing the Protocols at runtime triggers a circular import
    # — ``trackers/__init__.py`` (loaded as a side-effect of resolving
    # ``.trackers.base_tracker``) imports ``jsonl_tracker``, which imports
    # back into this module for ``tracker_registry``. Under
    # ``from __future__ import annotations`` the Protocol names are only
    # referenced inside the module-level type annotations below, so a
    # TYPE_CHECKING-only import is sufficient.
    from .datasets.base_dataset import Dataset
    from .metrics.base_metric import Metric
    from .systems.base_system import System
    from .trackers.base_tracker import ExperimentTracker

T = TypeVar("T")


class _Registry(Generic[T]):
    """Name → class registry. The class is its own factory."""

    def __init__(self) -> None:
        self._items: dict[str, type[T]] = {}

    def register(self, name: str) -> Callable[[type[T]], type[T]]:
        def decorator(cls: type[T]) -> type[T]:
            # WHY: duplicate registration is a wiring bug — surface it at
            # import time rather than letting the second decorator silently
            # win.
            if name in self._items:
                raise ValueError(f"{name!r} already registered")
            self._items[name] = cls
            return cls

        return decorator

    def build(self, name: str, **kwargs: object) -> T:
        try:
            cls = self._items[name]
        except KeyError as exc:
            raise KeyError(
                f"unknown entry {name!r}; have {sorted(self._items)}"
            ) from exc
        # WHY: plug-in constructors take plug-in-specific kwargs the generic
        # ``T`` can't capture — type safety is decorative here, runtime
        # registration is the real contract.
        return cls(**kwargs)  # type: ignore[call-arg]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))


dataset_registry: _Registry[Dataset] = _Registry()
metric_registry: _Registry[Metric] = _Registry()
tracker_registry: _Registry[ExperimentTracker] = _Registry()
system_registry: _Registry[System] = _Registry()
