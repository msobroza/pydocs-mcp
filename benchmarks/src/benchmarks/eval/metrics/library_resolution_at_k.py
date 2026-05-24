"""library_resolution@1 — did Context7's router resolve the task's library
to the right ``/org/project`` id? (spec §4.11, methodology §5.4).

WHY path-segment match + alias instead of equality:

- Context7's resolved ids are ``/org/project`` paths (e.g.
  ``/pandas-dev/pandas``), NEVER bare library names — so an exact compare
  against ``metadata["library"]`` ("pandas") can never match. We test
  whether the library name equals a case-insensitive PATH SEGMENT of the
  id (``rid.lower().split("/")``) instead.
- Segment, not raw substring: a substring test false-positives — "numpy"
  is a substring of an UNRELATED id like ``/pyro-ppl/numpyro``, which
  would wrongly score 1.0. Matching a whole ``/``-delimited segment
  ("numpy" != "numpyro") avoids that.
- Cross-naming gaps: DS-1000 / PyPI call it ``torch`` but Context7
  resolves ``/pytorch/pytorch``; "torch" is not a segment of that id.
  A small module-level alias map bridges the gap (``torch`` also matches
  the ``pytorch`` segment). Segment-match, not equality, because
  torch != pytorch yet they name the same library.

For systems that never populate ``resolved_library_id`` (pydocs /
neuledge) this scores 0.0 — fine; the metric is only meaningful in the
Context7 row of the comparison report.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..datasets.base_dataset import EvalTask
from ..serialization import metric_registry
from ..systems.base_system import RetrievedItem

# WHY: single source of truth for the DS-1000 cross-naming aliases.
# ``torch`` (DS-1000 / PyPI canonical) resolves to Context7's
# ``/pytorch/pytorch`` — "torch" is not a segment of that id, so we
# also accept the "pytorch" segment. Keyed by the DS-1000 library
# name; the value tuple is the set of acceptable path segments.
_LIBRARY_ALIASES: dict[str, tuple[str, ...]] = {"torch": ("torch", "pytorch")}


@metric_registry.register("library_resolution@1")
@dataclass(frozen=True, slots=True)
class LibraryResolution1:
    name: str = "library_resolution@1"

    def compute(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]  # noqa: ARG002
    ) -> float:
        rid = task.gold.extra.get("resolved_library_id")
        lib = task.metadata.get("library", "")
        if not (rid and lib):
            return 0.0
        aliases = _LIBRARY_ALIASES.get(lib, (lib,))
        segments = str(rid).lower().split("/")
        return 1.0 if any(a in segments for a in aliases) else 0.0
