"""The ``arms:`` run-config block — an evaluation arm is a data cell (design §6).

The canonical cell key set is normative and lives in the run-contract design
§6; quoted here because this module is its typed spelling::

    arms:
      - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner
        settings: {workspace: ~/pydocs-index, model: qwen3-4b}
        tool_names: null            # null → the full nine; a tuple narrows within them
        dataset: crosscommitvuln    # a REGISTERED dataset name
        task_name: vuln             # a product-enumerated v1 task framing
        guidance: search_skill      # artifact family name
        scoring:                    # the ONE objective + any observed metrics
          objective: rubric_verdict
          rubric: ask_rubric
          tracked: [gold_recall]

``runner``, ``settings``, ``tool_names``, ``dataset``, ``task_name``,
``guidance``, ``scoring`` — exactly seven keys, ``extra="forbid"``, so a
misspelled key is a load-time error naming the offending key rather than a
silently ignored no-op.

Four deliberate non-properties of this model:

- **``settings`` stays opaque.** It is the harness-private mapping the dotted
  ``runner`` factory validates (the product's ``AskYourDocsRunnerSettings``
  already forbids extras); enumerating its keys here would mint a second,
  drifting mirror of a type this package does not own. Two keys are the
  exception and are REFUSED there (``arm_runtime.arm_runner_settings``):
  ``tool_names`` and ``architecture`` each already have an authoritative
  source that folds into an identity — the cell key rides the arm hash, the
  bound rubric section's architecture rides the objective hash — so a second
  spelling would let a run execute one thing and record another.
- **``runner`` never resolves at parse time.** Load-time validation checks the
  dotted path against the registered harness-bridge rows (a NAME check, no
  import); the module is imported only when an arm actually runs, so a harness
  behind an optional extra costs nothing until used
  (``ask_binding.resolve_harness_runner_factory``).
- **``tool_names`` is DATA, not an architecture class** (ADR 0016): ``None``
  means the full frozen nine; a tuple narrows within them, order-significant
  because the harness binds tools in the order given.
- **``dataset`` is a REGISTERED dataset name, not a task-id prefix.** The
  registry is the only vocabulary that can produce a corpus
  (``arm_runtime.resolve_arm_dataset``); a prefix alias (``ccv``) would be a
  second spelling that must stay in sync with the registry, with
  ``Dataset.name`` and with the product's ``TASK_NAMES`` — and since arm
  identity folds the NAME, any drift would be silent. ``task_name`` is the
  separate key that carries the corpus's framing.

BASE-install safe: pydantic + the eval-local tool-name / metric registries
only. The product-coupled checks (``guidance`` registered, ``task_name``
enumerated), the corpus check (``dataset`` registered) and the
run-config-coupled one (``scoring.rubric`` names a configured objective) live
in ``load_firewall.assert_arm_cells`` beside the other registry-name checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from pydocs_eval.arm_identity import arm_fingerprint
from pydocs_eval.optimize.arm_scoring import ArmScoring
from pydocs_eval.optimize.rubric.gates import INDEXED_TOOL_NAMES

# The ``module.path:attribute`` shape a ``runner`` must spell. One separator,
# non-empty on both sides — checked here so the load-time bridge lookup gets a
# well-formed key and never a half-parsed path.
_RUNNER_PATH_SEPARATOR = ":"


class ArmCell(BaseModel):
    """One evaluation arm as data (design §6's normative seven-key cell)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runner: str
    settings: Mapping[str, object] = Field(default_factory=dict)
    tool_names: tuple[str, ...] | None = None
    dataset: str
    task_name: str
    guidance: str
    #: Required: what this arm optimizes (exactly one metric) and what it
    #: merely observes. No default — an unstated objective is a config error.
    scoring: ArmScoring

    @field_validator("settings")
    @classmethod
    def _freeze_settings(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        """Make ``frozen=True`` cover the mapping too, not just rebinding it.

        pydantic coerces a ``Mapping`` field to a plain ``dict``, so without
        this the cell's arm hash stays mutable after load: ``to_canonical()``
        reads ``settings`` at call time, and a stray ``cell.settings[k] = v``
        would move ``fingerprint()`` AFTER the arm identity was recorded in a
        ledger. Mirrors the product binding's ``MappingProxyType`` use.
        """
        return MappingProxyType(dict(value))

    @field_serializer("settings")
    def _serialize_settings(self, value: Mapping[str, object]) -> dict[str, object]:
        """Dump the proxy back as a plain dict (pydantic serializes dicts)."""
        return dict(value)

    @field_validator("runner")
    @classmethod
    def _check_runner_path(cls, value: str) -> str:
        """Require the ``module.path:attribute`` factory spelling."""
        module, separator, attribute = value.partition(_RUNNER_PATH_SEPARATOR)
        if not (separator and module and attribute):
            raise ValueError(
                f"invalid runner {value!r}: expected a dotted factory path "
                "'module.path:attribute', e.g. "
                "'pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner'"
            )
        return value

    @field_validator("tool_names")
    @classmethod
    def _check_tool_subset(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        """Require ``None`` (the full nine) or a non-empty subset of them."""
        if value is None:
            return None
        if not value:
            raise ValueError(
                "invalid tool_names (): an empty grant is never what an arm means "
                f"— use null for the full nine {sorted(INDEXED_TOOL_NAMES)}, or name a subset"
            )
        unknown = tuple(name for name in value if name not in INDEXED_TOOL_NAMES)
        if unknown:
            raise ValueError(
                f"invalid tool_names {list(unknown)}: an arm may only NARROW within "
                f"the frozen nine {sorted(INDEXED_TOOL_NAMES)}, never add to them"
            )
        duplicates = tuple(name for i, name in enumerate(value) if name in value[:i])
        if duplicates:
            raise ValueError(
                f"invalid tool_names {list(value)}: duplicate {list(duplicates)} — "
                "the tuple is the bound surface in order, so each tool appears once"
            )
        return value

    def to_canonical(self) -> dict[str, object]:
        """The cell as a JSON-canonicalizable mapping (the arm-hash input).

        Every one of the seven keys is present and JSON-shaped: ``tool_names``
        as a list (or ``None``), ``settings`` as a plain dict. Adding a key to
        the cell therefore moves every arm hash by construction, which is the
        intended cost of widening the normative key set.

        ``scoring`` is the one key that does NOT round-trip whole — see
        :meth:`fingerprint` for which half of it is identity and why.
        """
        return {
            "runner": self.runner,
            "settings": dict(self.settings),
            "tool_names": list(self.tool_names) if self.tool_names is not None else None,
            "dataset": self.dataset,
            "task_name": self.task_name,
            "guidance": self.guidance,
            "scoring": {"objective": str(self.scoring.objective)},
        }

    def fingerprint(
        self, *, guidance_fingerprint: str, delivery_map_hash: str, rubric_config_hash: str
    ) -> str:
        """This cell's arm hash — the design §6 formula over ``to_canonical()``.

        WHY the scoring block folds ASYMMETRICALLY (owner directive
        2026-07-27): the objective binding MOVES verdicts, so the objective
        kind and the RESOLVED ``rubric_config_hash`` are arm identity — two
        arms scored against different objectives must never resume each
        other's ledger lines. ``scoring.tracked`` is pure observation: it
        changes no verdict, so folding it in would make adding a metric
        someone wants to WATCH cost a full re-spend of the arm. And it is the
        resolved hash, not the ``scoring.rubric`` NAME, that folds — identity
        is what was measured, never what the config called it.
        """
        cell = self.to_canonical()
        scoring = dict(cell["scoring"])  # type: ignore[arg-type]  # built above as a dict
        scoring["rubric_config_hash"] = rubric_config_hash
        cell["scoring"] = scoring
        return arm_fingerprint(
            cell=cell,
            guidance_fingerprint=guidance_fingerprint,
            delivery_map_hash=delivery_map_hash,
        )
