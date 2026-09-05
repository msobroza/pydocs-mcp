"""Campaign pre-registration config + launch-refusal loader (ADR 0018 §Decision).

Holds the paired-exact-gate campaign's FROZEN registration: the fixed decision
slots (``alpha``, ``delta_min``, ``k_plateau``, ``n_val``, gate rule, explore
split) and the MEASURED slots the Phase 3 paid arc fills (``pi_d``,
``cost_rollout``, ``m_mb``, ``c_sel``, ``confirmed_target``, ``g_gate_evals``,
``minibatch_panel_instance_ids``). A measured slot left null is ``[TO BE
MEASURED]``.

The launch gate is :func:`authorize_launch`: it raises :class:`UnfilledSlotsError`
naming EVERY empty measured slot while any is null (ADR 0018 §Decision: "Launching
with any slot unfilled ... is forbidden"). :func:`registration_hash` over the
canonical registration bytes is the identity the candidate super-ledger
references — pre-registration is only meaningful if the frozen text is hash-pinned
before the (resolve, cost) frontier is visible.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from pydocs_eval.trajectory.blob_store import canonical_json

__all__ = [
    "MEASURED_SLOTS",
    "PanelOverlapError",
    "PreRegistration",
    "UnfilledSlotsError",
    "assert_panel_disjoint_from_val",
    "authorize_launch",
    "load_preregistration",
    "registration_hash",
]

# The slots the Phase 3 paid arc fills; each null == unfilled == launch-blocking.
# Order is the reporting/error order (ADR 0018 §"Measured-input slots" table).
MEASURED_SLOTS: tuple[str, ...] = (
    "pi_d",
    "cost_rollout",
    "m_mb",
    "c_sel",
    "confirmed_target",
    "g_gate_evals",
    "minibatch_panel_instance_ids",
)

# A measured slot may be written as YAML ``null`` OR this literal marker string;
# both parse to ``None`` so a half-filled registration cannot smuggle the marker
# text through as a real value.
_TBM_MARKER = "[TO BE MEASURED]"


class UnfilledSlotsError(Exception):
    """A campaign launch was requested while ≥1 measured slot is still null.

    Carries the offending empty slot names so the operator sees exactly what the
    Phase 3 paid arc must fill before launch (ADR 0018 §Decision).
    """

    def __init__(self, empty_slots: tuple[str, ...]) -> None:
        self.empty_slots = empty_slots
        super().__init__(
            f"campaign launch forbidden: measured slots still null: {list(empty_slots)}; "
            f"expected every slot of {list(MEASURED_SLOTS)} filled by the Phase 3 paid arc"
        )


@dataclass(frozen=True, slots=True)
class PreRegistration:
    """The frozen ADR 0018 registration — fixed slots set, measured slots nullable.

    ``minibatch_panel_instance_ids`` is ``None`` until measured and a (possibly
    empty) tuple once the Phase 3 discriminative subset is chosen; an empty tuple
    still counts as unfilled (a campaign needs a real panel).
    """

    version: str
    alpha: float
    delta_min: float
    k_plateau: int
    n_val: int
    gate_rule: str
    explore_fraction: float
    stopping_rules: tuple[str, ...]
    pi_d: float | None = None
    cost_rollout: float | None = None
    m_mb: float | None = None
    c_sel: float | None = None
    confirmed_target: float | None = None
    g_gate_evals: int | None = None
    minibatch_panel_instance_ids: tuple[str, ...] | None = None

    def unfilled_slots(self) -> tuple[str, ...]:
        """The measured slots still null (or an empty panel), in reporting order."""
        return tuple(name for name in MEASURED_SLOTS if self._is_empty(name))

    def is_launchable(self) -> bool:
        """True iff every measured slot is filled (no null, non-empty panel)."""
        return not self.unfilled_slots()

    def _is_empty(self, name: str) -> bool:
        value = getattr(self, name)
        return value is None or value == ()

    def to_dict(self) -> dict[str, object]:
        """Canonical-JSON-ready dict; the exact payload :func:`registration_hash` digests."""
        panel = self.minibatch_panel_instance_ids
        return {
            "version": self.version,
            "alpha": self.alpha,
            "delta_min": self.delta_min,
            "k_plateau": self.k_plateau,
            "n_val": self.n_val,
            "gate_rule": self.gate_rule,
            "explore_fraction": self.explore_fraction,
            "stopping_rules": list(self.stopping_rules),
            "pi_d": self.pi_d,
            "cost_rollout": self.cost_rollout,
            "m_mb": self.m_mb,
            "c_sel": self.c_sel,
            "confirmed_target": self.confirmed_target,
            "g_gate_evals": self.g_gate_evals,
            "minibatch_panel_instance_ids": None if panel is None else list(panel),
        }


def authorize_launch(prereg: PreRegistration) -> None:
    """Gate a campaign launch — raise :class:`UnfilledSlotsError` if any slot is null.

    The standing refusal ADR 0018 mandates: no launch (and thus no paid candidate
    evaluation) while a measured slot is ``[TO BE MEASURED]``. A no-op on a fully
    filled registration.
    """
    empty = prereg.unfilled_slots()
    if empty:
        raise UnfilledSlotsError(empty)


class PanelOverlapError(Exception):
    """A minibatch panel shares ≥1 instance_id with the val gate list (ADR 0018).

    Overlap would correlate the minibatch filter with gate noise and inflate the
    realized false-accept above α/2; the disjointness is a pre-registration
    invariant. Carries the overlapping ids.
    """

    def __init__(self, overlap: tuple[str, ...]) -> None:
        self.overlap = overlap
        super().__init__(
            f"minibatch panel overlaps the val gate on {list(overlap)}; "
            f"expected panel instance_ids disjoint from the {len(overlap)}+ val.txt gate ids"
        )


def assert_panel_disjoint_from_val(
    prereg: PreRegistration, val_instance_ids: frozenset[str]
) -> None:
    """Enforce ADR 0018's panel/gate disjointness — raise on any shared instance_id.

    A no-op while the panel slot is unfilled (nothing to overlap yet). Once filled,
    the Phase 3 fixed panels MUST share no id with the val gate list.
    """
    panel = prereg.minibatch_panel_instance_ids
    if not panel:
        return
    overlap = tuple(sorted(set(panel) & val_instance_ids))
    if overlap:
        raise PanelOverlapError(overlap)


def registration_hash(prereg: PreRegistration) -> str:
    """SHA-256 of the canonical registration bytes — the super-ledger reference.

    Digests :meth:`PreRegistration.to_dict` through ``canonical_json`` (sorted
    keys), so two registrations with identical content hash identically regardless
    of field insertion order, and any edit to a frozen slot changes the hash
    loudly.
    """
    import hashlib

    payload = canonical_json(prereg.to_dict())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_preregistration(path: Path) -> PreRegistration:
    """Parse a pre-registration YAML into a :class:`PreRegistration` (no launch gate).

    Loading NEVER authorizes a launch — it only reads the frozen text so the
    dry-run can hash it and the power report can consume its filled slots. Call
    :func:`authorize_launch` separately to enforce the refusal. ``null`` or the
    ``[TO BE MEASURED]`` marker on a measured slot both parse to ``None``.

    Raises:
        ValueError: on a missing required fixed slot, naming the key.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: pre-registration must be a mapping, got {type(raw).__name__}")
    return _from_raw(raw, source=str(path))


def _from_raw(raw: Mapping[str, object], *, source: str) -> PreRegistration:
    return PreRegistration(
        version=str(_require(raw, "version", source)),
        alpha=float(_require(raw, "alpha", source)),  # type: ignore[arg-type]
        delta_min=float(_require(raw, "delta_min", source)),  # type: ignore[arg-type]
        k_plateau=int(_require(raw, "k_plateau", source)),  # type: ignore[arg-type]
        n_val=int(_require(raw, "n_val", source)),  # type: ignore[arg-type]
        gate_rule=str(_require(raw, "gate_rule", source)),
        explore_fraction=float(_require(raw, "explore_fraction", source)),  # type: ignore[arg-type]
        stopping_rules=tuple(str(r) for r in raw.get("stopping_rules") or ()),
        pi_d=_measured_float(raw, "pi_d"),
        cost_rollout=_measured_float(raw, "cost_rollout"),
        m_mb=_measured_float(raw, "m_mb"),
        c_sel=_measured_float(raw, "c_sel"),
        confirmed_target=_measured_float(raw, "confirmed_target"),
        g_gate_evals=_measured_int(raw, "g_gate_evals"),
        minibatch_panel_instance_ids=_measured_panel(raw, "minibatch_panel_instance_ids"),
    )


def _require(raw: Mapping[str, object], key: str, source: str) -> object:
    if key not in raw or raw[key] is None:
        raise ValueError(f"{source}: missing required fixed slot {key!r}")
    return raw[key]


def _measured(raw: Mapping[str, object], key: str) -> object | None:
    """A measured slot: ``None`` for null or the ``[TO BE MEASURED]`` marker."""
    value = raw.get(key)
    return None if value is None or value == _TBM_MARKER else value


def _measured_float(raw: Mapping[str, object], key: str) -> float | None:
    value = _measured(raw, key)
    return None if value is None else float(value)  # type: ignore[arg-type]


def _measured_int(raw: Mapping[str, object], key: str) -> int | None:
    value = _measured(raw, key)
    return None if value is None else int(value)  # type: ignore[arg-type]


def _measured_panel(raw: Mapping[str, object], key: str) -> tuple[str, ...] | None:
    value = _measured(raw, key)
    return None if value is None else tuple(str(item) for item in value)  # type: ignore[union-attr]
