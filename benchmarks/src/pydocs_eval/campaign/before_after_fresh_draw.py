"""The fresh repoqa test draw: ``--split-seed`` and ``--exclude-task-ids``.

``repoqa-qa/small_test`` was consumed by an earlier confirmation run, so a final
confirmation needs a sample nobody has measured on. The fresh draw is that
sample: the same size (30) and the same per-repo apportionment as
``small_test``, drawn under the operator's seed from the held-out ``test``
partition after the consumed ids are filtered out.

**The partition does not move.** ``stratified_split`` drives the dev/test cut
AND the ``small_test`` subsample from ONE ``random.Random(split_seed)``, so
forwarding a new seed to the dataset's ``split_seed`` would re-cut dev/test and
hand the "fresh" sample tasks the ladder already tuned on (seed-0 ``dev`` and
``small_dev``). The pool is therefore ``test`` at the dataset's OWN split seed,
and ``--split-seed`` seeds only the redraw from it
(``_split.reseeded_stratified_subsample``).

**Filter, then draw.** The excluded ids leave the pool before the draw, so the
draw always has its full size and is disjoint by construction; a postcondition
guard (:func:`refuse_overlap_with_excluded_ids`) still refuses, by name, any
draw that intersects the excluded set.

The exclusion file is JSON: ``{"provenance": "<how the ids were derived>",
"task_ids": ["repoqa-qa/repo_qa/…", …]}``. Its sha256, id count and provenance
are carried into the plan, the arms' settings and the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.datasets._split import _DEFAULT_SMALL_TEST_SIZE, reseeded_stratified_subsample
from pydocs_eval.datasets.base_dataset import EvalTask

#: The one slice a fresh draw replaces, the one dataset it is defined for, and
#: the partition it is drawn from. repoqa-qa is stratified by repo, the key
#: ``small_test`` itself is apportioned by.
FRESH_DRAW_DATASET = "repoqa-qa"
FRESH_DRAW_SLICE = "small_test"
FRESH_DRAW_POOL_SLICE = "test"
#: The draw's size: ``small_test``'s own target, read from its single source.
FRESH_DRAW_SIZE = _DEFAULT_SMALL_TEST_SIZE
_STRATUM_KEY = "repo"
_EXCLUSION_SHAPE = '{"provenance": "<non-empty text>", "task_ids": ["<task id>", ...]}'


@dataclass(frozen=True, slots=True)
class ExcludedTaskIds:
    """The ids a fresh draw must avoid, and where they came from."""

    source: str
    sha256: str
    provenance: str
    task_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FreshTestDraw:
    """A redraw of ``repoqa-qa/small_test``: its seed, and the ids it excludes."""

    seed: int
    excluded: ExcludedTaskIds | None = None

    def plan_lines(self) -> list[str]:
        """What the plan prints under its split line: the seed, then the exclusion."""
        lines = [
            f"draw:       fresh {FRESH_DRAW_SLICE}, seed {self.seed}, from {FRESH_DRAW_POOL_SLICE} "
            "at the dataset's own split seed (the dev/test partition is unchanged)"
        ]
        if self.excluded is not None:
            lines += [
                f"excluded:   {self.excluded.source} — {len(self.excluded.task_ids)} id(s), "
                f"sha256 {self.excluded.sha256}",
                f"            provenance: {self.excluded.provenance}",
            ]
        return lines

    def report_line(self) -> str:
        """One bullet of the report's provenance."""
        if self.excluded is None:
            return f"- fresh draw: seed {self.seed}, no ids excluded"
        return (
            f"- fresh draw: seed {self.seed}, {len(self.excluded.task_ids)} id(s) excluded "
            f"from `{self.excluded.source}` (sha256 `{self.excluded.sha256}`): "
            f"{self.excluded.provenance}"
        )

    def sample_key(self) -> tuple[int, str | None]:
        """What makes two draws the same sample: the seed and the exclusion file's BYTES.

        The path is left out on purpose — the same file named by another path
        (a re-run from another directory) draws the same tasks.
        """
        return (self.seed, None if self.excluded is None else self.excluded.sha256)

    def describe(self) -> str:
        """The draw in one phrase, for a refusal that names two of them."""
        if self.excluded is None:
            return f"seed {self.seed}, no exclusion file"
        return f"seed {self.seed}, exclusion sha256 {self.excluded.sha256}"

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> FreshTestDraw:
        """The draw an arm's settings file recorded (``asdict`` of it; the ids frozen at plan time)."""
        excluded = record.get("excluded")
        if not isinstance(excluded, Mapping):
            return cls(seed=int(record["seed"]))  # type: ignore[call-overload]
        fields = {**excluded, "task_ids": tuple(excluded["task_ids"])}
        return cls(seed=int(record["seed"]), excluded=ExcludedTaskIds(**fields))  # type: ignore[call-overload]


def add_fresh_draw_arguments(parser: argparse.ArgumentParser) -> None:
    """``--split-seed`` and ``--exclude-task-ids``; without them no draw happens."""
    parser.add_argument(
        "--split-seed",
        type=int,
        default=None,
        help=f"redraw {FRESH_DRAW_DATASET}/{FRESH_DRAW_SLICE} under this seed from its "
        f"{FRESH_DRAW_POOL_SLICE} partition (the partition keeps the dataset's own split seed)",
    )
    parser.add_argument(
        "--exclude-task-ids",
        type=Path,
        default=None,
        help=f"JSON file of task ids the redraw must avoid ({_EXCLUSION_SHAPE}); "
        "needs --split-seed",
    )


def fresh_draw_from_args(args: argparse.Namespace) -> FreshTestDraw | None:
    """The draw the flags ask for; None when neither is given.

    Raises:
        MeasurementPlanError: ``--exclude-task-ids`` without ``--split-seed`` (a
            redraw's seed is what the ledger records, so it is never implied),
            or an exclusion file outside the declared shape.
    """
    if args.split_seed is None:
        if args.exclude_task_ids is not None:
            raise MeasurementPlanError(
                f"--exclude-task-ids {args.exclude_task_ids} needs --split-seed <int>: the "
                "redraw's seed is recorded beside the excluded ids, so it must be named"
            )
        return None
    excluded = (
        None if args.exclude_task_ids is None else load_excluded_task_ids(args.exclude_task_ids)
    )
    return FreshTestDraw(seed=int(args.split_seed), excluded=excluded)


def load_excluded_task_ids(path: Path) -> ExcludedTaskIds:
    """The exclusion file at ``path``, its ids sorted, its bytes digested.

    Raises:
        MeasurementPlanError: the file is missing, is not JSON, or is outside
            the declared shape — the message names the offending value.
    """
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise MeasurementPlanError(
            f"--exclude-task-ids {path}: not a readable JSON file ({exc}); expected {_EXCLUSION_SHAPE}"
        ) from exc
    provenance, task_ids = _exclusion_fields(path, payload)
    return ExcludedTaskIds(
        source=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        provenance=provenance,
        task_ids=tuple(sorted(task_ids)),
    )


def _exclusion_fields(path: Path, payload: object) -> tuple[str, list[str]]:
    if not isinstance(payload, dict):
        _refuse_shape(path, f"a JSON {type(payload).__name__}, not an object")
    provenance = payload.get("provenance")
    if not isinstance(provenance, str) or not provenance.strip():
        _refuse_shape(path, f"provenance {provenance!r}, expected non-empty text")
    task_ids = payload.get("task_ids")
    if not isinstance(task_ids, list) or not task_ids:
        _refuse_shape(path, f"task_ids {task_ids!r}, expected a non-empty list")
    _refuse_bad_ids(path, task_ids)
    return provenance, task_ids


def _refuse_bad_ids(path: Path, task_ids: list[object]) -> None:
    not_text = [each for each in task_ids if not isinstance(each, str) or not each]
    if not_text:
        _refuse_shape(path, f"task_ids holds {not_text!r}, expected non-empty strings")
    repeated = sorted(str(each) for each, n in Counter(task_ids).items() if n > 1)
    if repeated:
        _refuse_shape(path, f"task_ids repeats {repeated!r}, expected each id once")


def _refuse_shape(path: Path, problem: str) -> NoReturn:
    raise MeasurementPlanError(f"--exclude-task-ids {path}: {problem}; expected {_EXCLUSION_SHAPE}")


def refuse_unsupported_fresh_draw(dataset_names: Sequence[str], split: str) -> None:
    """A fresh draw is defined for ``repoqa-qa/small_test`` only; refuse anything else by name."""
    expected = f"expected {FRESH_DRAW_DATASET}/{FRESH_DRAW_SLICE}"
    others = [name for name in dataset_names if name != FRESH_DRAW_DATASET]
    if others:
        raise MeasurementPlanError(
            f"--split-seed redraws one dataset's slice, and {others!r} is not it; {expected}"
        )
    if split != FRESH_DRAW_SLICE:
        raise MeasurementPlanError(f"--split-seed redraws no {split!r} slice; {expected}")


def draw_fresh_test_tasks(
    pool: Sequence[EvalTask], draw: FreshTestDraw, *, size: int = FRESH_DRAW_SIZE
) -> tuple[EvalTask, ...]:
    """Filter the excluded ids out of ``pool``, then draw ``size`` tasks stratified by repo.

    Raises:
        MeasurementPlanError: an excluded id is not in ``pool`` (so it would
            exclude nothing), or the draw intersects the excluded set.
    """
    remaining = list(pool) if draw.excluded is None else _filtered_pool(pool, draw.excluded)
    drawn = reseeded_stratified_subsample(
        remaining,
        seed=draw.seed,
        size=size,
        stratum_of=_stratum_of,
        sort_key=lambda task: task.task_id,
    )
    if draw.excluded is not None:
        refuse_overlap_with_excluded_ids([task.task_id for task in drawn], draw.excluded)
    return tuple(drawn)


def _stratum_of(task: EvalTask) -> str:
    """The task's repo; a task without one would be drawn unstratified, so it is refused."""
    repo = task.metadata.get(_STRATUM_KEY)
    if not repo:
        raise MeasurementPlanError(
            f"task {task.task_id!r} carries {_STRATUM_KEY}={repo!r}; the fresh draw is "
            f"apportioned by repo, so expected every task to name its repository"
        )
    return str(repo)


def _filtered_pool(pool: Sequence[EvalTask], excluded: ExcludedTaskIds) -> list[EvalTask]:
    """``pool`` without the excluded ids — each of which must be in it."""
    _refuse_ids_outside_the_pool(pool, excluded)
    avoid = set(excluded.task_ids)
    return [task for task in pool if task.task_id not in avoid]


def _refuse_ids_outside_the_pool(pool: Sequence[EvalTask], excluded: ExcludedTaskIds) -> None:
    """An id the pool lacks is a typo, another slice's id or an unframed record id."""
    pool_ids = {task.task_id for task in pool}
    outside = [task_id for task_id in excluded.task_ids if task_id not in pool_ids]
    if outside:
        raise MeasurementPlanError(
            f"--exclude-task-ids {excluded.source} names {len(outside)} id(s) outside the "
            f"{FRESH_DRAW_DATASET}/{FRESH_DRAW_POOL_SLICE} pool the redraw is drawn from: "
            f"{outside!r}; expected framed task ids of that partition"
        )


def refuse_overlap_with_excluded_ids(drawn_ids: Sequence[str], excluded: ExcludedTaskIds) -> None:
    """The postcondition: a draw that shares any id with the excluded set is refused by name."""
    overlap = sorted(set(drawn_ids) & set(excluded.task_ids))
    if overlap:
        raise MeasurementPlanError(
            f"the fresh draw overlaps the excluded ids of {excluded.source} "
            f"(sha256 {excluded.sha256}) on {overlap!r}; expected a draw disjoint from them"
        )


__all__ = (
    "FRESH_DRAW_DATASET",
    "FRESH_DRAW_POOL_SLICE",
    "FRESH_DRAW_SIZE",
    "FRESH_DRAW_SLICE",
    "ExcludedTaskIds",
    "FreshTestDraw",
    "add_fresh_draw_arguments",
    "draw_fresh_test_tasks",
    "fresh_draw_from_args",
    "load_excluded_task_ids",
    "refuse_overlap_with_excluded_ids",
    "refuse_unsupported_fresh_draw",
)
