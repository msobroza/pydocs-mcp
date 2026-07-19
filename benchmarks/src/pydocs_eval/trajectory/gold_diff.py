"""Gold-patch parser (ADR 0011 gold side, action item 3).

Extracts the set of files a SWE-bench-Live instance's gold ``patch`` modifies,
stated by our own rule (NOT a copy of the harness's ``get_modified_files``,
which takes only source-side files and skips ``/dev/null`` sources — it serves
the test-file *reset* path and would drop every new file the patch creates;
new files appear in 21.1% of instances).

Our rule per file section:

- the **target** file is gold (unless ``/dev/null`` — a deletion);
- a ``/dev/null`` **source** section is a new file: its target is gold;
- a **deletion** (target ``/dev/null``) contributes its source;
- a **rename** contributes source **and** target.

Backed by unidiff ``PatchSet`` so every measured edge case survives without
naive ``line.split(' ')`` header parsing (forbidden — unquoted paths carry
spaces): multi-hunk, multi-file, new files, no-newline, deletions, renames
(including hunkless ``similarity index 100%``), the one binary
``Binary files … differ`` one-liner, and the one ``new file mode 120000``
symlink. ``patch``/``test_patch`` file-set disjointness is asserted in the
parser (measured 0/1888 overlap; the assert stays so a dataset refresh that
breaks the invariant fails loudly). Instances are deduped by ``instance_id``
(the known ``conan-io__conan-18153`` duplicate).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from unidiff import PatchSet

from pydocs_eval.trajectory.schema import TrajectoryError

_DEV_NULL = "/dev/null"


class GoldPatchError(TrajectoryError):
    """A gold patch violated an asserted invariant (e.g. file-set disjointness)."""


@dataclass(frozen=True, slots=True)
class GoldPatch:
    """The gold-side file facts for one instance.

    ``gold_files`` are workspace-relative POSIX paths (``a/``/``b/`` prefixes
    stripped) modified by the instance ``patch``; ``test_files`` are those
    modified by ``test_patch``. Disjointness is asserted at construction.
    """

    instance_id: str
    gold_files: frozenset[str]
    test_files: frozenset[str]


def _strip_ab(path: str) -> str:
    """Drop the git ``a/`` or ``b/`` prefix from a diff-header path."""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def modified_files(patch: str) -> frozenset[str]:
    """Return the set of files ``patch`` modifies, by the ADR 0011 gold rule.

    Empty/whitespace ``patch`` (an empty or ``None`` gold prediction) yields an
    empty set rather than raising.

    Example:
        >>> p = "diff --git a/x.py b/x.py\\n--- a/x.py\\n+++ b/x.py\\n"
        >>> modified_files(p)
        frozenset({'x.py'})
    """
    files: set[str] = set()
    for section in PatchSet(patch or ""):
        _collect_section_files(section, files)
    return frozenset(files)


def _collect_section_files(section: object, out: set[str]) -> None:
    """Add the gold-modified paths of one unidiff file section to ``out``."""
    source = getattr(section, "source_file", _DEV_NULL)
    target = getattr(section, "target_file", _DEV_NULL)
    is_rename = bool(getattr(section, "is_rename", False))
    if target != _DEV_NULL:
        out.add(_strip_ab(target))
    if source != _DEV_NULL and (is_rename or target == _DEV_NULL):
        out.add(_strip_ab(source))


def parse_gold_patch(instance_id: str, patch: str, test_patch: str) -> GoldPatch:
    """Build a :class:`GoldPatch`, asserting ``patch``/``test_patch`` disjointness.

    Raises :class:`GoldPatchError` if the two file sets overlap — measured
    0/1888 in the dataset, so an overlap signals a corrupted or refreshed
    instance, not a normal case.
    """
    gold = modified_files(patch)
    tests = modified_files(test_patch)
    overlap = gold & tests
    if overlap:
        raise GoldPatchError(
            f"gold/test file overlap for {instance_id!r}: {sorted(overlap)!r}; "
            "expected disjoint patch and test_patch file sets"
        )
    return GoldPatch(instance_id=instance_id, gold_files=gold, test_files=tests)


def dedupe_instances(instances: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """Drop duplicate instances by ``instance_id``, first occurrence winning.

    The dataset carries a real duplicate (``conan-io__conan-18153`` ×2 in
    ``full``); this keeps parsing idempotent over it.
    """
    seen: set[str] = set()
    kept: list[Mapping[str, object]] = []
    for instance in instances:
        instance_id = _require_instance_id(instance)
        if instance_id in seen:
            continue
        seen.add(instance_id)
        kept.append(instance)
    return kept


def _require_instance_id(instance: Mapping[str, object]) -> str:
    """Return the instance's ``instance_id`` string or raise with context."""
    value = instance.get("instance_id")
    if not isinstance(value, str):
        raise GoldPatchError(
            f"instance missing str 'instance_id': got {value!r} in {dict(instance)!r}"
        )
    return value


def coerce_test_names(value: object) -> tuple[str, ...]:
    """Coerce a dataset F2P/P2P field to a tuple of names.

    Live stores native lists; classic SWE-bench stores a JSON-encoded string
    (``'["a::b"]'``). Both are accepted (``json.loads`` when a ``str``).
    """
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, (list, tuple)):
        raise GoldPatchError(f"test-name field must be a list or JSON list string: got {value!r}")
    return tuple(str(name) for name in value)
