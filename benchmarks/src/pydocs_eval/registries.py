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
    """Name → class registry. The class is its own factory.

    Self-populating: pass ``populate`` a callback that imports the owning
    implementation modules, and the registry runs it lazily on the first READ
    (``names`` / ``build``). The callback CANNOT run at import time without a
    cycle — implementations import the registry instance to ``register`` on it —
    so it runs on first read instead, by which point the import graph has
    settled. This kills the "empty registry" trap (a caller no longer has to
    remember to import the impl subpackage first) with zero import-time cost and
    no heavy/optional dep pulled at module scope.
    """

    def __init__(self, *, populate: Callable[[], None] | None = None) -> None:
        self._items: dict[str, type[T]] = {}
        self._populate = populate
        self._populated = populate is None

    def _ensure_populated(self) -> None:
        if self._populated:
            return
        # Flip the flag BEFORE running the import so a re-entrant read during
        # population (an impl module reading this registry at its own import
        # time) sees "already populated" and cannot recurse. ``register`` never
        # reads, so filling ``_items`` mid-populate is safe.
        self._populated = True
        assert self._populate is not None  # _populated was False ⇒ populate set
        self._populate()

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
        self._ensure_populated()
        try:
            cls = self._items[name]
        except KeyError as exc:
            raise KeyError(f"unknown entry {name!r}; have {sorted(self._items)}") from exc
        # WHY: plug-in constructors take plug-in-specific kwargs the generic
        # ``T`` can't capture — type safety is decorative here, runtime
        # registration is the real contract.
        return cls(**kwargs)  # type: ignore[call-arg]

    def names(self) -> tuple[str, ...]:
        self._ensure_populated()
        return tuple(sorted(self._items))


# Each populate callback imports its owning impl package for its
# ``@*_registry.register`` decorator side effects. Function-local imports keep
# the impl→registry cycle broken and the registry module itself light — importing
# ``pydocs_eval.registries`` pulls no dataset/metric/tracker/system code (nor any
# heavy/optional dep) until the first read of the corresponding registry.


def _populate_datasets() -> None:
    from pydocs_eval import datasets as _datasets  # noqa: F401 -- register side effects


def _populate_metrics() -> None:
    from pydocs_eval import metrics as _metrics  # noqa: F401 -- register side effects


def _populate_trackers() -> None:
    from pydocs_eval import trackers as _trackers  # noqa: F401 -- register side effects


def _populate_systems() -> None:
    from pydocs_eval import systems as _systems  # noqa: F401 -- register side effects


dataset_registry: _Registry[Dataset] = _Registry(populate=_populate_datasets)
metric_registry: _Registry[Metric] = _Registry(populate=_populate_metrics)
tracker_registry: _Registry[ExperimentTracker] = _Registry(populate=_populate_trackers)
system_registry: _Registry[System] = _Registry(populate=_populate_systems)
