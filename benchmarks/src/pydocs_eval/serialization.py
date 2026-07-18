"""DEPRECATED shim — the module moved to ``pydocs_eval.registries``.

The file held the four decorator registries and no serialization; the new
name says what it is (the sibling ``optimize/registries.py`` already named
the same pattern correctly). pydocs-mcp-eval 0.2.0 published this module
path on PyPI, so the old import keeps working for one deprecation release;
the shim is removed in the release after 0.2.x. Import from
``pydocs_eval.registries`` instead.
"""

from __future__ import annotations

from .registries import (  # noqa: F401
    _Registry,
    dataset_registry,
    metric_registry,
    system_registry,
    tracker_registry,
)
