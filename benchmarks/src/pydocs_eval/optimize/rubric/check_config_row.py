"""One ``checks:`` YAML row → one ``Check``, with every value type-enforced.

Split out of ``checks.py`` (which owns the measurement) purely for size: this
module owns the CONFIG BOUNDARY, and the boundary has to be strict.

WHY strict rather than tolerant: ``AskRubricSettings.checks`` receives dataclass
INSTANCES from a ``mode="before"`` validator, and pydantic's
``revalidate_instances`` defaults to ``"never"`` — so nothing downstream
re-checks what this function returns. A YAML ``weight: "0.25"`` that survives
here reaches ``score_checks`` as a string and raises inside the composite, at
trial 14, with the rollout and the judge call already paid for. And a ``wieght:``
typo that is merely IGNORED is worse than a crash: the check silently keeps its
1.0 default, which re-weights the objective, moves ``rubric_config_hash``, and
reports nothing. Both are rejected by name, here, at load time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydocs_eval.optimize.rubric.checks import Check

__all__ = ["check_from_config_row"]

#: The ``checks:`` YAML row keys beyond name/kind/params. Every one is defaulted
#: by ``Check`` itself, so an omitted key must reach the dataclass as ABSENT
#: rather than as a second copy of the default — that is what keeps the run
#: config free of a second spelling of the check policy defaults.
_CONFIG_POLICY_KEYS = ("weight", "required", "fail", "applies_to", "weight_by_type")

#: Every key a row may carry; anything else is a typo, never an extension.
_CONFIG_ROW_KEYS = ("name", "kind", "params", *_CONFIG_POLICY_KEYS)


def check_from_config_row(row: Mapping[str, object]) -> Check:
    """Build one ``Check`` from a YAML ``checks:`` row, honoring its own defaults.

    Raises:
        ValueError: an unknown or missing row key, or a value that is not of the
            field's type — each naming the offending check and value.

    Example:
        >>> check_from_config_row({"name": "r", "kind": "gold_recall", "fail": None}).fail is None
        True
    """
    name = str(_required_row_value(row, "name"))
    _reject_unknown_row_keys(row, name)
    policy: dict[str, object] = {key: row[key] for key in _CONFIG_POLICY_KEYS if key in row}
    if "weight" in policy:
        policy["weight"] = _as_float(policy["weight"], name=name, key="weight")
    if "required" in policy:
        policy["required"] = bool(policy["required"])
    if policy.get("fail") is not None:
        policy["fail"] = _as_float(policy["fail"], name=name, key="fail")
    if "applies_to" in policy:
        policy["applies_to"] = tuple(
            str(t) for t in _as_sequence(policy["applies_to"], name=name, key="applies_to")
        )
    if "weight_by_type" in policy:
        policy["weight_by_type"] = {
            str(k): _as_float(v, name=name, key="weight_by_type")
            for k, v in _as_mapping(policy["weight_by_type"], name=name).items()
        }
    return Check(
        name=name,
        kind=str(_required_row_value(row, "kind")),
        params=dict(_as_mapping(row.get("params", {}), name=name)),
        **policy,  # type: ignore[arg-type]
    )


def _required_row_value(row: Mapping[str, object], key: str) -> object:
    """One mandatory row key, or a ValueError naming the row rather than a KeyError."""
    if key not in row:
        raise ValueError(f"checks row is missing {key!r}: {dict(row)!r}")
    return row[key]


def _reject_unknown_row_keys(row: Mapping[str, object], name: str) -> None:
    """Reject typo'd row keys, which would otherwise re-weight the objective silently."""
    unknown = sorted(set(row) - set(_CONFIG_ROW_KEYS))
    if unknown:
        raise ValueError(
            f"check {name!r} has unknown key(s) {unknown}; a checks row accepts "
            f"{list(_CONFIG_ROW_KEYS)} — an ignored key would silently leave the "
            "field at its default and re-weight the objective"
        )


def _as_float(value: object, *, name: str, key: str) -> float:
    """A row number as a float, or a ValueError naming the check, key and value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"check {name!r} {key} must be a number; got {value!r}")
    return float(value)


def _as_sequence(value: object, *, name: str, key: str) -> Sequence[object]:
    """A row list, never a bare string (which would iterate into characters)."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(
            f"check {name!r} {key} must be a list of task types; got {value!r} — a bare "
            "string iterates into characters and silently matches no task type"
        )
    return value


def _as_mapping(value: object, *, name: str) -> Mapping[str, object]:
    """A row mapping, or a ValueError naming the check."""
    if not isinstance(value, Mapping):
        raise ValueError(f"check {name!r} expects a mapping here; got {value!r}")
    return value
