"""One task's outcome, and the record a resumable tracker keeps of it.

Split out of ``sweep.py`` (which still re-exports ``TaskObservation``) so
the value object and its persisted form change together: a field added
here must also be written by ``to_record`` and read by ``from_record``, or
a resumed run would restore observations that lack it (issue #420).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

# The keys ``to_record`` writes besides ``task_id`` and ``step``;
# ``from_record`` requires every one of them.
_RECORD_FIELDS = ("scores", "index_seconds", "search_seconds", "cache_hit", "metadata")


@dataclass(frozen=True, slots=True)
class TaskObservation:
    """One task's outcome, surfaced to programmatic callers.

    ``index_seconds`` is ALWAYS measured; ``cache_hit`` records whether the
    system served a cached index (spec D9: a ~0 s cache lookup is not an
    indexing measurement — aggregation and trackers skip hit rows).

    ``metadata`` carries the task's ``EvalTask.metadata`` verbatim so the
    report's ``## By qa_type`` breakout (spec §D14) can group per-task scores
    by ``metadata["qa_type"]`` without re-parsing tracker JSONL. Empty for
    datasets that carry no per-task metadata (RepoQA, DS-1000).
    """

    task_id: str
    scores: dict[str, float]
    index_seconds: float
    search_seconds: float
    cache_hit: bool
    metadata: Mapping[str, str] = field(default_factory=dict)

    def to_record(self, *, step: int) -> dict[str, object]:
        """The JSON-ready record a resumable tracker stores for this task.

        ``step`` is the task's position in its leg — the same ``step`` its
        metric lines carry — so a reader can join the two. Floats survive a
        JSON round trip exactly, which is what makes a resumed leg's
        aggregates bit-identical to an uninterrupted one's.
        """
        return {
            "task_id": self.task_id,
            "step": step,
            "scores": dict(self.scores),
            "index_seconds": self.index_seconds,
            "search_seconds": self.search_seconds,
            "cache_hit": self.cache_hit,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> TaskObservation:
        """Rebuild the observation ``to_record`` wrote; raise naming the task if malformed."""
        task_id = record.get("task_id")
        if not isinstance(task_id, str):
            raise ValueError(f"task record has no string task_id: got {task_id!r}")
        missing = [name for name in _RECORD_FIELDS if name not in record]
        if missing:
            raise ValueError(
                f"cannot restore task {task_id!r}: its record lacks {', '.join(missing)}; "
                f"expected every field TaskObservation.to_record writes "
                f"({', '.join(_RECORD_FIELDS)})"
            )
        return cls(
            task_id=task_id,
            scores={str(k): float(_number(v)) for k, v in _mapping(record["scores"]).items()},
            index_seconds=float(_number(record["index_seconds"])),
            search_seconds=float(_number(record["search_seconds"])),
            cache_hit=bool(record["cache_hit"]),
            metadata={str(k): str(v) for k, v in _mapping(record["metadata"]).items()},
        )


def _mapping(value: object) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"expected a JSON object in a task record, got {value!r}")
    return value


def _number(value: object) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"expected a number in a task record, got {value!r}")
    return value


__all__ = ("TaskObservation",)
