"""The ``arms:`` block — cell shape, the key firewall, and arm identity (design §6).

Three surfaces are pinned here: the normative seven-key cell (nothing more,
nothing less, with offending-value error messages), the widened run-config key
firewall (an unknown top-level key is a load error, not a silent no-op), and
the arm-hash formula (deterministic; every identity component moves it, and the
observational ``scoring.tracked`` list deliberately does not).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_eval.arm_identity import NO_GUIDANCE_FINGERPRINT, arm_fingerprint, delivery_map_hash
from pydocs_eval.optimize.arms import ArmCell
from pydocs_eval.optimize.rubric.gates import INDEXED_TOOL_NAMES

_ASK_RUNNER = "pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner"
_RUBRIC_HASH = "r" * 64


def _cell(**overrides: object) -> ArmCell:
    base: dict[str, object] = {
        "runner": _ASK_RUNNER,
        "settings": {"workspace": "~/pydocs-index", "model": "qwen3-4b"},
        "tool_names": None,
        "dataset": "crosscommitvuln",
        "task_name": "ccv",
        "guidance": "search_skill",
        "scoring": {"objective": "rubric_verdict", "rubric": "ask_rubric"},
    }
    return ArmCell(**{**base, **overrides})


def _hash(cell: ArmCell, **overrides: str) -> str:
    kwargs: dict[str, str] = {
        "guidance_fingerprint": "g",
        "delivery_map_hash": "d",
        "rubric_config_hash": _RUBRIC_HASH,
    }
    return cell.fingerprint(**{**kwargs, **overrides})


class TestCellShape:
    def test_the_seven_canonical_keys_are_exactly_the_model_fields(self) -> None:
        # The normative key set (run-contract design §6) — every other document
        # quotes it, so this is the one place code pins it.
        assert set(ArmCell.model_fields) == {
            "runner",
            "settings",
            "tool_names",
            "dataset",
            "task_name",
            "guidance",
            "scoring",
        }

    def test_an_unknown_cell_key_is_rejected_by_name(self) -> None:
        with pytest.raises(ValidationError, match="architecture"):
            _cell(architecture="text_react")

    def test_a_missing_required_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="task_name"):
            ArmCell(runner=_ASK_RUNNER, dataset="ccv", guidance="search_skill")

    def test_a_missing_scoring_block_names_the_expected_shape(self) -> None:
        # Explicit beats implicit for the objective: an arm that does not say
        # what it optimizes is a config error, never a defaulted guess.
        with pytest.raises(ValidationError, match="scoring"):
            ArmCell(
                runner=_ASK_RUNNER,
                dataset="crosscommitvuln",
                task_name="ccv",
                guidance="search_skill",
            )

    def test_settings_stays_an_opaque_mapping(self) -> None:
        # No enumeration eval-side: the harness factory owns the keys (its own
        # extra="forbid"), so an unfamiliar key rides through untouched.
        cell = _cell(settings={"anything_the_harness_wants": [1, 2, 3]})
        assert cell.settings["anything_the_harness_wants"] == [1, 2, 3]

    def test_settings_cannot_be_mutated_after_load(self) -> None:
        # frozen=True must cover the MAPPING too, not just rebinding the field:
        # to_canonical() reads settings at call time, so an in-place write would
        # move fingerprint() AFTER that arm identity was written to a ledger.
        cell = _cell(settings={"model": "qwen3-4b"})
        with pytest.raises(TypeError, match="item assignment"):
            cell.settings["model"] = "other"  # type: ignore[index]
        assert cell.settings["model"] == "qwen3-4b"

    def test_a_malformed_runner_path_names_the_offending_value(self) -> None:
        with pytest.raises(ValidationError, match="not_a_factory_path"):
            _cell(runner="not_a_factory_path")


class TestToolNames:
    def test_null_means_the_full_nine(self) -> None:
        assert _cell(tool_names=None).tool_names is None

    def test_a_subset_narrows_within_the_frozen_nine(self) -> None:
        assert _cell(tool_names=("search_codebase", "read_file")).tool_names == (
            "search_codebase",
            "read_file",
        )

    def test_a_tool_outside_the_nine_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Bash"):
            _cell(tool_names=("search_codebase", "Bash"))

    def test_an_empty_grant_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="empty grant"):
            _cell(tool_names=())

    def test_a_duplicate_tool_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            _cell(tool_names=("read_file", "read_file"))

    def test_the_domain_matches_the_products_frozen_nine(self) -> None:
        # The one base-install-safe spelling of the nine, pinned against the
        # product's own — a second copy would be exactly the drift the gate
        # module's comment guards against.
        ds = pytest.importorskip("pydocs_mcp.application.description_source")
        assert frozenset(ds.FROZEN_TOOL_NAMES) == INDEXED_TOOL_NAMES


class TestArmIdentity:
    def test_the_formula_matches_its_golden_bytes(self) -> None:
        # The arm hash is a PERSISTED, money-costing key: ledger rows carry it
        # and a resume matches it exactly. Comparing hash-to-hash inside one
        # process cannot notice a formula change, a cell-key re-spelling, or a
        # tweak to the shared canonicalizer — this literal can. Moving it
        # orphans every ledger and forces a full re-spend, so it changes only
        # as a deliberate, reviewed measurement bump (the doctrine ADR 0017
        # applies to ``CellConfig.to_dict()`` golden bytes).
        assert (
            arm_fingerprint(
                cell={"task_name": "ccv"},
                guidance_fingerprint="abc",
                delivery_map_hash="def",
            )
            == "d23bd69416b2c08b870dbc7f37725db07900cc0c81d7065786a9186cbd6f2fa0"
        )

    def test_the_hash_is_deterministic_across_key_orders(self) -> None:
        one = _cell(settings={"a": 1, "b": 2})
        two = _cell(settings={"b": 2, "a": 1})
        assert _hash(one) == _hash(two)

    def test_every_component_moves_the_hash(self) -> None:
        base = _hash(_cell())
        assert _hash(_cell(tool_names=("read_file",))) != base
        assert _hash(_cell(), guidance_fingerprint="OTHER") != base
        assert _hash(_cell(), delivery_map_hash="OTHER") != base
        assert _hash(_cell(), rubric_config_hash="OTHER") != base

    def test_every_cell_key_moves_the_hash(self) -> None:
        base = _hash(_cell())
        moved = {
            "runner": "pydocs_eval.agent_track._runner:ClaudeAgentRunner",
            "settings": {"model": "other"},
            "tool_names": ("read_file",),
            "dataset": "swe-qa-pro",
            "task_name": "sweqapro",
            "guidance": "usage_skill",
        }
        # ``scoring`` is deliberately absent — only half of it folds, which
        # TestScoringIdentityAsymmetry pins. Tying the two sets together is
        # what makes widening the cell force an explicit coverage decision
        # instead of leaving a silent gap.
        assert set(moved) | {"scoring"} == set(ArmCell.model_fields)
        for key, value in moved.items():
            assert _hash(_cell(**{key: value})) != base, key

    def test_the_no_guidance_sentinel_is_distinct_from_an_empty_render(self) -> None:
        cell = _cell().to_canonical()
        assert arm_fingerprint(
            cell=cell, guidance_fingerprint=NO_GUIDANCE_FINGERPRINT, delivery_map_hash="d"
        ) != arm_fingerprint(cell=cell, guidance_fingerprint="", delivery_map_hash="d")

    def test_delivery_map_hash_is_order_independent_and_content_sensitive(self) -> None:
        assert delivery_map_hash({"a": "x", "b": "y"}) == delivery_map_hash({"b": "y", "a": "x"})
        assert delivery_map_hash({"a": "x"}) != delivery_map_hash({"a": "z"})

    def test_to_canonical_carries_every_key_json_shaped(self) -> None:
        canonical = _cell(tool_names=("read_file",)).to_canonical()
        assert set(canonical) == set(ArmCell.model_fields)
        assert canonical["tool_names"] == ["read_file"]
        assert isinstance(canonical["settings"], dict)


class TestScoringIdentityAsymmetry:
    """The objective binds identity; the tracked list is pure observation."""

    def test_a_different_resolved_rubric_flips_arm_identity(self) -> None:
        # The objective MOVES verdicts, so resuming across two objectives would
        # mix measurements — the resolved rubric hash is identity, not metadata.
        assert _hash(_cell(), rubric_config_hash="s" * 64) != _hash(_cell())

    def test_the_rubric_NAME_is_not_what_identity_folds(self) -> None:
        # Two arms naming differently-spelled sections that resolve to the SAME
        # objective are the same arm: identity is what was measured, not what
        # the config called it.
        renamed = _cell(scoring={"objective": "rubric_verdict", "rubric": "ccv_strict"})
        assert _hash(renamed) == _hash(_cell())

    def test_changing_the_tracked_list_does_not_flip_arm_identity(self) -> None:
        # Adding an observational metric must never invalidate resume state —
        # nothing about the arm's verdicts moved.
        watched = _cell(
            scoring={
                "objective": "rubric_verdict",
                "rubric": "ask_rubric",
                "tracked": ["gold_recall", "cve_id_exact"],
            }
        )
        assert _hash(watched) == _hash(_cell())

    def test_the_objective_kind_rides_the_canonical_cell(self) -> None:
        # One member today, so no second value can be constructed to flip the
        # hash — this pins the fold site instead, which is what a second
        # objective would travel through.
        assert _cell().to_canonical()["scoring"] == {"objective": "rubric_verdict"}
