"""Candidate ledger — append-only JSONL mutation record (ADR 0017/0019 R3).

The optimization-run super-ledger: every proposed candidate — accepted,
gate-rejected, OR validity-rejected — appends one line here in the
campaign-ledger idiom (``campaign/ledger.py``): sha256 identity
(``candidate_hash``), a last-write-wins index, and idempotent accrual (an
exact-duplicate line never double-counts rollouts or cost). It carries the
three lineage fields with no prior ledger precedent — ``lineage_parent``,
``mutation_record``, ``reflector_input_refs`` — so the whole mutation tree is
reconstructable and a VALIDITY-REJECTED candidate is provably zero-rollout
straight from its own entry (R3 demonstrable).

The zero-rollout guarantee is STRUCTURAL, not conventional: ``CandidateRecord``
rejects at construction any invalid entry that claims a rollout, a score, or a
gate decision (an invalid candidate never spends). Lines are written as sorted-
key canonical JSON (``blob_store.canonical_json``) so the on-disk bytes are
deterministic and golden-byte testable, and the source document + reflector
inputs are stored content-addressed under ``blobs/<sha256>`` beside the ledger.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.trajectory.blob_store import canonical_json, write_result_blob

log = logging.getLogger(__name__)

LEDGER_FILENAME = "candidates.jsonl"
_BLOBS_DIRNAME = "blobs"


@dataclass(frozen=True, slots=True)
class MutationRecord:
    """What produced a candidate: the mutated component + proposer metadata.

    ``component`` is the mutated section key (``None`` for the seed or an
    all-section merge); ``selector`` is the GEPA module selector that picked it
    (``None`` for the seed). ``metadata`` carries free proposer facts (model id,
    iteration) as strings so the line stays JSON-stable.
    """

    proposer: str
    component: str | None = None
    selector: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def to_record(self) -> dict[str, object]:
        return {
            "proposer": self.proposer,
            "component": self.component,
            "selector": self.selector,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_record(cls, raw: Mapping[str, object]) -> MutationRecord:
        return cls(
            proposer=str(raw["proposer"]),
            component=_opt_str(raw.get("component")),
            selector=_opt_str(raw.get("selector")),
            metadata={str(k): str(v) for k, v in dict(raw.get("metadata") or {}).items()},
        )


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """A candidate's gate inputs + verdict — mirrors ``trajectory.gate.GateDecision``.

    Recorded verbatim from the sanctioned gate so the ledger never re-derives an
    acceptance signal (R2): the adapter's acceptance path consumes ``GateDecision``
    and nothing else, and this is where it lands for audit.
    """

    resolve_rate: float
    n_graded: int
    n_infra_excluded: int
    cost_usd: float
    within_budget: bool
    passed: bool

    def to_record(self) -> dict[str, object]:
        return {
            "resolve_rate": self.resolve_rate,
            "n_graded": self.n_graded,
            "n_infra_excluded": self.n_infra_excluded,
            "cost_usd": self.cost_usd,
            "within_budget": self.within_budget,
            "passed": self.passed,
        }

    @classmethod
    def from_record(cls, raw: Mapping[str, object]) -> GateOutcome:
        return cls(
            resolve_rate=float(raw["resolve_rate"]),
            n_graded=int(raw["n_graded"]),
            n_infra_excluded=int(raw["n_infra_excluded"]),
            cost_usd=float(raw["cost_usd"]),
            within_budget=bool(raw["within_budget"]),
            passed=bool(raw["passed"]),
        )

    @classmethod
    def from_decision(cls, decision: object) -> GateOutcome:
        """Project a ``trajectory.gate.GateDecision`` onto the ledger shape (no re-derivation)."""
        return cls(
            resolve_rate=decision.resolve_rate,  # type: ignore[attr-defined]
            n_graded=decision.n_graded,  # type: ignore[attr-defined]
            n_infra_excluded=decision.n_infra_excluded,  # type: ignore[attr-defined]
            cost_usd=decision.cost_usd,  # type: ignore[attr-defined]
            within_budget=decision.within_budget,  # type: ignore[attr-defined]
            passed=decision.passed,  # type: ignore[attr-defined]
        )


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """One candidate's full lineage + verdict line (ADR 0019 §Decision 8).

    ``valid`` is the firewall validity verdict (NOT gate acceptance). The
    zero-rollout invariant is enforced at construction: an invalid candidate
    MUST carry zero rollouts, no minibatch scores, and no gate decision — an
    invalid candidate never reaches a rollout, so a nonzero claim is a
    construction bug, raised with the offending values (R3).
    """

    candidate_hash: str
    document_ref: str
    lineage_parent: str | None
    mutation_record: MutationRecord
    reflector_input_refs: tuple[str, ...]
    valid: bool
    violations: tuple[str, ...]
    n_rollouts: int = 0
    minibatch_scores: Mapping[str, float] = field(default_factory=dict)
    gate: GateOutcome | None = None
    campaign_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.valid:
            return
        spent = self.n_rollouts or self.minibatch_scores or self.gate is not None
        if spent:
            raise ValueError(
                "invalid candidate claims rollout spend: got "
                f"n_rollouts={self.n_rollouts}, minibatch_scores={dict(self.minibatch_scores)}, "
                f"gate={self.gate!r}; expected zero rollouts, no scores, and no gate "
                f"(candidate {self.candidate_hash!r} was firewall-rejected before any spend)"
            )

    def to_record(self) -> dict[str, object]:
        return {
            "candidate_hash": self.candidate_hash,
            "document_ref": self.document_ref,
            "lineage_parent": self.lineage_parent,
            "mutation_record": self.mutation_record.to_record(),
            "reflector_input_refs": list(self.reflector_input_refs),
            "valid": self.valid,
            "violations": list(self.violations),
            "n_rollouts": self.n_rollouts,
            "minibatch_scores": dict(self.minibatch_scores),
            "gate": self.gate.to_record() if self.gate is not None else None,
            "campaign_ids": list(self.campaign_ids),
        }

    def to_line(self) -> str:
        """The exact on-disk JSONL byte shape (sorted-key canonical JSON)."""
        return canonical_json(self.to_record())

    @classmethod
    def from_record(cls, raw: Mapping[str, object]) -> CandidateRecord:
        gate = raw.get("gate")
        return cls(
            candidate_hash=str(raw["candidate_hash"]),
            document_ref=str(raw["document_ref"]),
            lineage_parent=_opt_str(raw.get("lineage_parent")),
            mutation_record=MutationRecord.from_record(dict(raw["mutation_record"])),  # type: ignore[arg-type]
            reflector_input_refs=tuple(str(r) for r in raw.get("reflector_input_refs") or ()),
            valid=bool(raw["valid"]),
            violations=tuple(str(v) for v in raw.get("violations") or ()),
            n_rollouts=int(raw.get("n_rollouts", 0)),
            minibatch_scores={
                str(k): float(v) for k, v in dict(raw.get("minibatch_scores") or {}).items()
            },
            gate=GateOutcome.from_record(dict(gate)) if gate is not None else None,  # type: ignore[arg-type]
            campaign_ids=tuple(str(c) for c in raw.get("campaign_ids") or ()),
        )


@dataclass(slots=True)
class CandidateLedger:
    """Append-only JSONL ledger with a ``candidate_hash`` last-write-wins index.

    Load-on-init rebuilds the index from any existing file; a corrupt trailing
    line (killed mid-write) is skipped with a warning so the entries before it
    survive. ``blobs_dir`` (beside the ledger) holds the content-addressed source
    documents + reflector inputs the lineage fields reference.
    """

    path: Path
    _index: dict[str, CandidateRecord] = field(default_factory=dict, init=False)
    # Rollouts/cost accrue once per distinct canonical line — a re-appended
    # EXACT-duplicate is idempotent and does not double-count (campaign-ledger
    # spend_key idiom), while a genuinely new line for the same candidate is a
    # distinct accrual identity and counts additionally.
    _accrued_lines: set[str] = field(default_factory=set, init=False)
    _rollouts: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._load()

    @property
    def blobs_dir(self) -> Path:
        return self.path.parent / _BLOBS_DIRNAME

    def stage_blob(self, data: bytes) -> str:
        """Write ``data`` to ``blobs/<sha256>`` and return the digest ref (write-once)."""
        return write_result_blob(self.blobs_dir, data)

    def stage_document(self, document: str) -> str:
        """Content-address a candidate's rendered document; return its blob ref."""
        return self.stage_blob(document.encode("utf-8"))

    def stage_reflector_inputs(self, inputs: Sequence[bytes]) -> tuple[str, ...]:
        """Content-address the exact facts shown to the reflector; return their refs."""
        return tuple(self.stage_blob(data) for data in inputs)

    def record(self, record: CandidateRecord) -> CandidateRecord:
        """Append one candidate line and update the resume index + accrual."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = record.to_line()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._index[record.candidate_hash] = record
        self._accrue(line, record)
        return record

    def latest(self, candidate_hash: str) -> CandidateRecord | None:
        """The most recent record for ``candidate_hash``, or ``None`` if unseen."""
        return self._index.get(candidate_hash)

    def records(self) -> list[CandidateRecord]:
        """The latest record per candidate (last-write-wins), in first-seen order."""
        return list(self._index.values())

    def total_rollouts(self) -> int:
        """Rollouts summed over distinct lines — invalid candidates contribute zero."""
        return self._rollouts

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            record = self._parse_line(stripped)
            if record is not None:
                self._index[record.candidate_hash] = record
                self._accrue(stripped, record)

    def _parse_line(self, line: str) -> CandidateRecord | None:
        try:
            return CandidateRecord.from_record(json.loads(line))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("candidate ledger: skipping corrupt line in %s: %s", self.path, exc)
            return None

    def _accrue(self, line: str, record: CandidateRecord) -> None:
        # Accrue once per distinct canonical line so an exact-duplicate append is
        # idempotent (campaign-ledger finding-4 idiom). ``_load`` passes the raw
        # stored line and ``record`` passes ``to_line()``; both are canonical
        # (the writer only ever emits canonical JSON), so the keys agree.
        if line in self._accrued_lines:
            return
        self._accrued_lines.add(line)
        self._rollouts += record.n_rollouts


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)
