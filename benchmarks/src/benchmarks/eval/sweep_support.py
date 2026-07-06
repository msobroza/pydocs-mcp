"""Helpers the sweep loop threads around each leg / task (spec §4.6).

Extracted verbatim from ``runner.py`` when the sweep body moved to
``sweep.py``: metric-spec parsing, opt-in Protocol seeding
(``HasLibrary*`` / ``IndexesDependencies``), gold capture / injection,
and tracker run metadata (config flattening, env tags, close fan-out).
"""

from __future__ import annotations

import platform
import subprocess
import sys
import traceback
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from .metrics import MRR, NDCGAtK, PassAt1Needle, RecallAtK
from .metrics.base_metric import Metric
from .serialization import metric_registry
from .systems.base_system import (
    HasGoldResolver,
    HasLibrary,
    HasLibraryName,
    HasResolvedLibrary,
    IndexesDependencies,
)

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

    from .datasets.base_dataset import EvalTask
    from .systems.base_system import RetrievedItem


def _build_metric(spec: str) -> Metric:
    """Resolve ``recall@<k>`` / ``ndcg@<k>`` / ``mrr`` / ``pass@1-needle``
    to a metric instance. Walks ``metric_registry`` for the simple cases and
    instantiates ``RecallAtK(k)`` / ``NDCGAtK(k)`` for the parameterised
    forms.

    Single source of construction so the runner can sweep arbitrary k
    values via ``--metrics recall@1,recall@5,ndcg@10`` without the registry
    needing one entry per k.
    """
    if spec == "mrr":
        return MRR()
    if spec == "pass@1-needle":
        return PassAt1Needle()
    if spec.startswith("recall@"):
        k_part = spec.split("@", 1)[1]
        try:
            k = int(k_part)
        except ValueError as exc:
            raise ValueError(
                f"recall metric spec must be ``recall@<int>``, got {spec!r}",
            ) from exc
        return RecallAtK(k=k)
    if spec.startswith("ndcg@"):
        k_part = spec.split("@", 1)[1]
        try:
            k = int(k_part)
        except ValueError as exc:
            raise ValueError(
                f"ndcg metric spec must be ``ndcg@<int>``, got {spec!r}",
            ) from exc
        return NDCGAtK(k=k)
    # WHY: fall through to the registry so a future custom-named metric
    # registered under a single key still resolves.
    return metric_registry.build(spec)


async def _resolve_and_inject(
    system: object,
    task: EvalTask,
    retrieved: tuple[RetrievedItem, ...],
) -> EvalTask:
    """Run the system's ``GoldResolver`` and inject its result into a fresh
    task (frozen gold -> ``dataclasses.replace``, never mutated).

    Opt-in via ``isinstance(system, HasGoldResolver)`` — a system without a
    ``gold_resolver`` (RepoQA flows) is a strict no-op that returns the
    SAME task object, leaving the existing ``ast_body`` relevance path
    untouched. Returns the (possibly augmented) task so the caller can hand
    it to ``scorer.score``.
    """
    if not isinstance(system, HasGoldResolver):
        return task
    resolved = await system.gold_resolver.resolve(task, retrieved)
    return replace(
        task,
        gold=replace(
            task.gold,
            extra={**task.gold.extra, "resolved_chunk_ids": resolved},
        ),
    )


def _capture_library_resolution(system: object, task: EvalTask) -> EvalTask:
    """Record the library id the system resolved during ``index()`` into a
    fresh task's ``gold.extra`` (frozen gold -> ``dataclasses.replace``).

    Opt-in via ``isinstance(system, HasResolvedLibrary)`` — a system that
    doesn't expose ``last_resolved_library_id`` (pydocs / RepoQA flows) is a
    strict no-op that returns the SAME task object.

    Injects two keys for matching systems (Context7):
      - ``resolved_library_id`` — the router's pick (or the configured
        oracle id), feeding the ``library_resolution@1`` metric. Always
        injected, even when ``None``/empty, so the metric reads a present
        (falsy) value rather than a missing key.
      - ``coverage_signal`` — ``bool(rid)``: True iff resolution produced a
        non-empty id. This is the side channel Task 4's ``coverage`` metric
        falls back to for non-enumerable stores (no chunk-id set to count).

    Called BEFORE ``_resolve_and_inject`` in the loop so the injected extra
    survives that helper's ``{**task.gold.extra}`` spread.
    """
    if not isinstance(system, HasResolvedLibrary):
        return task
    rid = system.last_resolved_library_id
    return replace(
        task,
        gold=replace(
            task.gold,
            extra={
                **task.gold.extra,
                "resolved_library_id": rid,
                "coverage_signal": bool(rid),
            },
        ),
    )


def _maybe_set_index_dependencies(system: object, include_deps: bool) -> None:
    """Seed the dependency-indexing toggle on opt-in systems.

    Opt-in via the ``IndexesDependencies`` ``runtime_checkable`` Protocol
    (``systems/base_system.py``): ``PydocsMcpSystem`` exposes
    ``index_dependencies``. Comparative systems that don't are a strict no-op
    — the ``isinstance`` gate documents the contract at the type level and
    keeps the attribute off unrelated systems.
    """
    if isinstance(system, IndexesDependencies):
        system.index_dependencies = include_deps


def _maybe_set_library(system: object, metadata: Mapping[str, str]) -> None:
    """Seed comparative-system library identifiers from task metadata.

    Systems-agnostic via two ``runtime_checkable`` Protocols declared in
    ``systems/base_system.py``:

    - ``HasLibraryName`` — the human name (e.g. ``"psf/black"``).
      ``Context7System`` opts in.
    - ``HasLibrary`` — the install identifier
      (e.g. ``"psf/black@abcdef1"``). ``NeuledgeSystem`` opts in.

    Pydocs-mcp implements neither and is a strict no-op. Routing via
    ``isinstance`` against the Protocols (rather than bare ``hasattr``)
    documents the contract at the type level and prevents accidental
    injection into unrelated ``library_name`` fields on future systems.

    Source key precedence is ``repo`` then ``library``: RepoQA carries
    ``metadata["repo"]`` (a ``"org/name"`` slug) while DS-1000 carries
    ``metadata["library"]`` (a bare package name like ``"pandas"``). Both
    datasets thus reach Context7 / Neuledge through the same seam — without
    this fallback, ``search()`` would raise on DS-1000 for lack of a library.
    """
    name = metadata.get("repo") or metadata.get("library")
    if not name:
        return
    if isinstance(system, HasLibraryName):
        system.library_name = name
    if isinstance(system, HasLibrary):
        # WHY: only RepoQA's ``repo`` slug pairs with a ``commit`` to form
        # the ``{repo}@{sha7}`` install id. DS-1000's bare ``library`` has
        # no commit, so it seeds the install id verbatim.
        commit = metadata.get("commit", "")
        system.library = f"{name}@{commit[:7]}" if commit else name


def _flatten_app_config(cfg: AppConfig) -> dict[str, str]:
    """Dump ``AppConfig`` to a flat ``{dot.key: str(value)}`` mapping.

    Trackers (MLflow especially) want flat ``Mapping[str, str]`` params.
    ``model_dump()`` gives the nested dict; we walk it once and collapse
    keys with ``.``.
    """
    nested = cfg.model_dump()
    return dict(_flatten(nested))


def _flatten(
    obj: object,
    prefix: str = "",
) -> list[tuple[str, str]]:
    """Recursive dot-key flattener for nested dicts.

    Lists become ``str([...])`` so the value space stays string-typed
    without rewriting list-of-strings → joined-csv heuristics that would
    re-bite us on nested dicts inside lists.
    """
    items: list[tuple[str, str]] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            items.extend(_flatten(v, key))
    else:
        items.append((prefix, str(obj)))
    return items


def _run_tags() -> dict[str, str]:
    """Best-effort env tags: git SHA + platform info. Missing git or
    non-git working tree degrades each tag to ``""`` rather than aborting
    the run.
    """
    return {
        "git_sha": _git_sha(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
    }


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        # WHY: detached / no-git / git binary missing — return empty so the
        # tag still exists (downstream code does ``tags["git_sha"]`` without
        # an Optional check).
        return ""
    return out.strip()


def _close_all(handles, trackers, *, status) -> None:
    """Close every (handle, tracker) pair, swallowing per-tracker errors
    so one bad close doesn't block the others. Status applies uniformly.
    """
    for h, tracker in zip(handles, trackers):
        try:
            tracker.close_run(h, status=status)
        except Exception:
            # WHY: a tracker that fails to flush its close record must not
            # mask the original sweep error — keep the broad ``except`` so
            # one bad tracker doesn't block the others. But dump the
            # traceback to stderr so close-time errors aren't invisible
            # (TODO: route to logger once the runner gets one).
            traceback.print_exc(file=sys.stderr)
