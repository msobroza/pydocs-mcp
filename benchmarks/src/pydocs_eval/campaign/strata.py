"""Reporting-stratum maps for the campaign aggregator (ADR 0021 eval hook).

A *stratum map* is a plain ``instance_id -> stratum_key`` mapping the ``aggregate``
subcommand threads into ``campaign_report(stratum_of=...)`` to break every
contrast into per-stratum sub-contrasts. Strata are reporting-only: the frozen
pre-registration has no strata slot (``optimize/prereg/config.py``), so adding
one never changes the campaign id (ADR 0016 / ADR 0021).

Two ways to obtain a map:

- :func:`load_stratum_map` reads a hand-authored or tool-emitted map file — the
  generic path that also expresses the existing ``difficulty`` (single/multi via
  :func:`~pydocs_eval.campaign.aggregator.difficulty_stratum`) and ``repo``
  strata, which live in the library but were never wired to the CLI.
- :func:`build_gold_language_strata` derives the ``gold_touches_non_python``
  dimension from a run dir's ``facts.json`` ``gold_files`` (already a required
  fact key), labeling each instance by whether its gold patch edits a non-``.py``
  file.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pydocs_eval.trajectory.compute_metrics_cli import (
    ComputeMetricsError,
    discover_trajectories,
    load_facts,
)

# The two gold-language stratum labels. Mirrors ``difficulty_stratum``'s
# single/multi shape so both express as the same ``dict[str, str]``.
_GOLD_NON_PYTHON = "gold_touches_non_python"
_GOLD_PYTHON_ONLY = "gold_python_only"

_FACTS_FILENAME = "facts.json"


def gold_language_stratum(gold_files: Iterable[str]) -> str:
    """Label a gold-file set by whether it edits any non-``.py`` file.

    An all-``.py`` (or empty) gold set is ``gold_python_only``; a single
    non-``.py`` path flips it to ``gold_touches_non_python`` — the multilang
    reporting slice ADR 0021 measures.

    Example:
        >>> gold_language_stratum(["pkg/a.py", "docs/guide.rst"])
        'gold_touches_non_python'
    """
    touches_non_python = any(not path.endswith(".py") for path in gold_files)
    return _GOLD_NON_PYTHON if touches_non_python else _GOLD_PYTHON_ONLY


def build_gold_language_strata(run_dir: Path) -> dict[str, str]:
    """Derive the ``gold_touches_non_python`` map from a run dir's facts.

    Walks every ``<run_dir>/<traj>/facts.json`` via the SAME
    :func:`discover_trajectories` traversal ``compute-metrics`` uses, so the map
    covers exactly the run's graded instances. Returns ``instance_id -> stratum``
    ready for :func:`load_stratum_map`'s consumers.

    Raises:
        ComputeMetricsError: ``run_dir`` is not a trajectory run dir, or a
            ``facts.json`` is missing its required keys (surfaced verbatim).
    """
    strata: dict[str, str] = {}
    for traj_dir in discover_trajectories(run_dir):
        facts = load_facts(traj_dir / _FACTS_FILENAME)
        strata[facts.instance_id] = gold_language_stratum(facts.gold_files)
    return strata


def load_stratum_map(path: Path) -> dict[str, str]:
    """Read an ``instance_id -> stratum_key`` map from a ``.json``/``.jsonl`` file.

    ``.json`` is a single object mapping id to key; any other suffix (``.jsonl``
    by convention) is one ``{"instance_id": ..., "stratum": ...}`` object per
    non-blank line. Both yield the plain ``dict[str, str]`` that
    ``campaign_report(stratum_of=...)`` consumes unchanged.

    Raises:
        FileNotFoundError: ``path`` is absent.
        ValueError: the payload is not the expected shape (message carries the
            offending value + the expected shape).
    """
    if not path.is_file():
        raise FileNotFoundError(f"stratum-map file missing: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return _parse_json_strata(path, text)
    return _parse_jsonl_strata(path, text)


def _parse_json_strata(path: Path, text: str) -> dict[str, str]:
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError(
            f"{path}: stratum map must be a JSON object {{instance_id: stratum_key}}, "
            f"got {type(doc).__name__}"
        )
    return {str(key): str(value) for key, value in doc.items()}


def _parse_jsonl_strata(path: Path, text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or "instance_id" not in row or "stratum" not in row:
            raise ValueError(
                f"{path}:{lineno}: each JSONL row must be an object carrying "
                f"'instance_id' and 'stratum', got {row!r}"
            )
        out[str(row["instance_id"])] = str(row["stratum"])
    return out


# Re-export so callers importing the builder also get the typed run-dir error.
__all__ = [
    "ComputeMetricsError",
    "build_gold_language_strata",
    "gold_language_stratum",
    "load_stratum_map",
]
