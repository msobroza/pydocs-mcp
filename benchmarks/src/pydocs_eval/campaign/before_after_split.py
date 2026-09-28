"""Loading the tasks one before/after run covers — the same list for both arms.

The plan counts them and the arms answer them, so both go through this module:
a split the plan sized and the arms then disagreed about would silently compare
two different measurements. Task order is the dataset's own and the optional
limit takes a PREFIX of it, so ``--limit 2`` names the same two tasks in both
arms and on a re-run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydocs_eval.campaign.before_after import MeasurementPlanError, dataset_names_for, parse_split
from pydocs_eval.datasets._split import VALID_SPLITS
from pydocs_eval.datasets.base_dataset import Dataset, EvalTask
from pydocs_eval.datasets.example_needle_chat import ChatSplit
from pydocs_eval.registries import dataset_registry

# A framing dataset RE-MINTS another corpus's rows and delegates acquisition,
# caching and slicing to it, so the dev/test slice belongs to that source and
# reaches the wrapper through its documented ``source`` injection seam.
SLICE_ON_SOURCE: Mapping[str, str] = {"repoqa-qa": "repoqa"}

# A dataset whose slices are its own literal record fields, not the global
# stratified vocabulary. Consulted before ``VALID_SPLITS``, never merged into
# it: ``stratified_split`` answers any name it does not know with its
# ``small_test`` subsample, so a globally widened vocabulary would hand another
# dataset a silent subsample for ``reserved``.
DATASET_SLICE_NAMES: Mapping[str, tuple[str, ...]] = {
    "example-needle-chat": tuple(split.value for split in ChatSplit),
}

# ``Dataset`` implementations take the slice name as a ``split`` field.
_SPLIT_FIELD = "split"
_SOURCE_FIELD = "source"


async def load_split_tasks(split_spec: str, *, limit: int | None = None) -> tuple[EvalTask, ...]:
    """Every task of ``<selector>/<split>``, in dataset order, capped by ``limit``.

    Raises:
        MeasurementPlanError: an unparseable selector, an unknown split name, a
            dataset the registry does not know, or a slice with no tasks.
    """
    selector, split = parse_split(split_spec)
    names = dataset_names_for(selector)
    for name in names:
        _validate_split_name(name, split, split_spec)
    tasks: list[EvalTask] = []
    for name in names:
        tasks.extend(await _tasks_of_dataset(name, split))
    if not tasks:
        raise MeasurementPlanError(
            f"split {split_spec!r} yielded no task; check the selector against the "
            f"registered datasets {sorted(dataset_registry.names())}"
        )
    return tuple(tasks[:limit] if limit is not None else tasks)


def slice_names_for(dataset_name: str) -> tuple[str, ...]:
    """The slice names ``dataset_name`` answers — its own, else the global ones.

    Example:
        >>> "reserved" in slice_names_for("example-needle-chat")
        True
    """
    return DATASET_SLICE_NAMES.get(dataset_name, VALID_SPLITS)


def _validate_split_name(dataset_name: str, split: str, split_spec: str) -> None:
    accepted = slice_names_for(dataset_name)
    if split not in accepted:
        raise MeasurementPlanError(
            f"split {split_spec!r} names slice {split!r}, which dataset {dataset_name!r} "
            f"does not have; expected one of {list(accepted)}"
        )


async def _tasks_of_dataset(name: str, split: str) -> list[EvalTask]:
    """One registered dataset's tasks for ``split`` (the corpora stay unmaterialized)."""
    return [task async for task in _sliced_dataset(name, split).tasks()]


def _sliced_dataset(name: str, split: str) -> Dataset:
    """Build ``name`` cut to ``split``, directly or through its source corpus.

    Raises:
        MeasurementPlanError: the registry does not know ``name``, ``name``
            has no dev/test partition to cut (``swe-qa`` slices by REPO, so the
            framing over it answers the whole corpus or nothing), or ``name``
            refuses ``split`` (a slice name outside its own vocabulary).
    """
    source_name = SLICE_ON_SOURCE.get(name)
    try:
        if source_name is None:
            return dataset_registry.build(name, **{_SPLIT_FIELD: split})
        source = dataset_registry.build(source_name, **{_SPLIT_FIELD: split})
        return dataset_registry.build(name, **{_SOURCE_FIELD: source})
    except KeyError as exc:
        raise MeasurementPlanError(
            f"no registered dataset named {name!r}; have {sorted(dataset_registry.names())}"
        ) from exc
    except TypeError as exc:
        raise MeasurementPlanError(
            f"dataset {name!r} takes no {split!r} slice ({exc}); it has no dev/test "
            f"partition, so name a sliceable dataset such as {sorted(SLICE_ON_SOURCE)} "
            "instead of a task name that spans it"
        ) from exc
    except ValueError as exc:
        raise MeasurementPlanError(
            f"dataset {name!r} could not be built for slice {split!r}: {exc}"
        ) from exc


def task_ids_of(tasks: Sequence[EvalTask]) -> tuple[str, ...]:
    """The task ids, in run order — what the plan prints and the report groups by."""
    return tuple(task.task_id for task in tasks)
