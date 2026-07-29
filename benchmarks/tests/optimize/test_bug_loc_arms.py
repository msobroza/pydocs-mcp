"""Two corpora, ONE task name, ONE rubric — the bug_loc arms config.

Pins what the third framing's minting event actually bought: a ``bug_loc``
arm loads without an eval-side vocabulary edit, both arms share the
harness-invariant ``TASK_HEAD: bug_loc`` section, they stay separately
identified despite binding the SAME objective, and the deterministic layer's
0.5 / 0.5 apportionment (the one number this config argues for at length)
survives a judge skip instead of collapsing to a cliff.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from pydocs_eval.optimize.arm_runtime import resolve_arms
from pydocs_eval.optimize.ask_binding import known_task_names
from pydocs_eval.optimize.dry_run import print_arm_plan
from pydocs_eval.optimize.run_config import _configured_rubric_sections, load_run_config

_BUG_LOC = "optimize_search_skill_bug_loc.yaml"


def _shipped(name: str) -> Path:
    return Path(str(resources.files("pydocs_eval.optimize.configs").joinpath(name)))


def _edited(tmp_path: Path, old: str, new: str, *, stem: str) -> Path:
    text = _shipped(_BUG_LOC).read_text(encoding="utf-8")
    assert old in text, old
    path = tmp_path / f"{stem}.yaml"
    path.write_text(text.replace(old, new), encoding="utf-8")
    return path


@pytest.fixture
def bug_loc():
    return load_run_config(_shipped(_BUG_LOC))


class TestTheTaskNameParityChain:
    def test_the_eval_side_reads_the_product_tuple_with_no_local_copy(self) -> None:
        from pydocs_mcp.harness.platform.skill_artifact import TASK_NAMES

        assert known_task_names() == TASK_NAMES
        assert "bug_loc" in TASK_NAMES

    def test_the_load_firewall_admits_bug_loc_without_an_eval_side_edit(self, bug_loc) -> None:
        # Loading already ran ``_require_enumerated_task_name`` on both arms:
        # widening the product tuple is the WHOLE eval-side change.
        assert {arm.task_name for arm in bug_loc.arms} == {"bug_loc"}


class TestTheThirdRubricObjective:
    def test_the_file_localization_section_is_the_only_one_bound(self, bug_loc) -> None:
        assert sorted(_configured_rubric_sections(bug_loc)) == ["ask_rubric_file_localization"]

    def test_an_arm_naming_an_unconfigured_section_fails_at_load(self, tmp_path: Path) -> None:
        path = _edited(
            tmp_path,
            "rubric: ask_rubric_file_localization",
            "rubric: ask_rubric_file_typo",
            stem="bad_section",
        )
        with pytest.raises(ValueError, match="ask_rubric_file_typo"):
            load_run_config(path)

    def test_the_third_sections_weights_are_validated_at_load(self, tmp_path: Path) -> None:
        # Every CONFIGURED section is validated, not just the first — a third
        # objective must not be the one whose bad weights surface mid-run.
        path = _edited(
            tmp_path,
            "{ name: localization, weight: 0.7",
            "{ name: localization, weight: 0.9",
            stem="bad_weights",
        )
        with pytest.raises(ValueError, match="weights must sum"):
            load_run_config(path)


class TestTheApportionment:
    def test_evidence_carries_half_the_deterministic_layer(self, bug_loc) -> None:
        # THE argued number. Localization is the deliverable AND the issue text
        # frequently quotes the buggy path, so "said it" alone is gameable:
        # evidence is raised from the 0.25 both repo_qa objectives ship to an
        # equal half. Changing this ratio is a measurement decision, not a
        # tidy-up — the YAML states the argument, this pins the result.
        checks = {c.name: c for c in bug_loc.ask_rubric_file_localization.rubric_config.checks}
        assert checks["gold_recall"].weight == 0.5
        assert checks["gold_location_evidence"].weight == 0.5
        assert checks["gold_location_evidence"].kind == "gold_location_evidenced"

    def test_both_checks_are_pure_measures_never_gates(self, bug_loc) -> None:
        # A 1..21-path gold is honest as a gradient and unwinnable as a cutoff.
        for check in bug_loc.ask_rubric_file_localization.rubric_config.checks:
            assert check.required is False
            assert check.fail is None
            assert check.weight > 0.0

    def test_no_vuln_specific_check_rides_along(self, bug_loc) -> None:
        # ``cve_id_exact`` would score a vacuous 0.0 on every bug_loc sample.
        kinds = {c.kind for c in bug_loc.ask_rubric_file_localization.rubric_config.checks}
        tracked = {name for arm in bug_loc.arms for name in arm.scoring.tracked}
        assert "cve_id_exact" not in kinds | tracked


def _bug_loc_task(*gold: str):
    from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer

    return EvalTask(
        task_id=f"swe-bench-verified-loc/bug_loc/rec-{len(gold)}",
        query="the fix does not apply",
        gold=GoldAnswer(file_set=gold),
        corpus_source=lambda: Path(),
    )


def _trace_naming(tmp_path: Path, *paths: str) -> Path:
    """A server trace whose ``read_file`` calls NAME ``paths`` (real event shape)."""
    import json

    from pydocs_eval.optimize.rubric.trajectory_evidence import SERVER_EVENTS_FILENAME

    trace_dir = tmp_path / "traj"
    trace_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "_event": "tool_call",
            "seq": seq,
            "tool": "read_file",
            "args": {"file_path": path},
            "error": None,
            "hit_count": 0,
            "result_ids": [],
            "trajectory_id": "traj",
            "ts": 1784604468.176574,
        }
        for seq, path in enumerate(paths, start=1)
    ]
    (trace_dir / SERVER_EVENTS_FILENAME).write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )
    return trace_dir


class TestTheDeterministicLayerIsNotACliff:
    def test_a_partly_located_answer_still_scores_when_the_judge_skips(self, bug_loc) -> None:
        # "Found 1 of 2 gold files" must not land on the same 0.0 as an empty
        # answer, or accept_margin: 0.02 can never be cleared.
        from pydocs_eval.optimize.fitness.ask_rubric import verdict_when_judge_skipped

        rubric = bug_loc.ask_rubric_file_localization.rubric_config
        assert rubric.keep_deterministic_on_skip is True
        assert verdict_when_judge_skipped(rubric, 0.5) == pytest.approx(0.25)
        assert verdict_when_judge_skipped(rubric, 0.0) == pytest.approx(0.0)
        # Capped at gate_weight: the rubric half stays unearned, never redistributed.
        assert verdict_when_judge_skipped(rubric, 1.0) == pytest.approx(0.5)

    def test_the_argued_half_is_the_composite_the_layer_actually_produces(
        self, bug_loc, tmp_path: Path
    ) -> None:
        # Two attribute assertions cannot see the composite: the gates are
        # demoted to weight 0 only BECAUSE scored checks exist, so a change to
        # that rule would silently turn 0.5*gr + 0.5*gle into a 4-way mean with
        # every other bug_loc test still green. Pin the two asymmetric corners
        # instead — full evidence + wrong answer, and right answer + no
        # evidence must be EQUAL and land at exactly one half.
        from pydocs_eval.optimize.fitness.ask_rubric import verdict_when_judge_skipped
        from pydocs_eval.optimize.rubric.checks import deterministic_checks, score_checks

        from tests.optimize._trajectories import make_trajectory, server_call

        rubric = bug_loc.ask_rubric_file_localization.rubric_config
        checks = deterministic_checks(rubric.gates, rubric.checks)
        gold = ("pkg/broken.py", "pkg/helper.py")
        task = _bug_loc_task(*gold)
        calls = (server_call("read_file"),)

        located_but_unsaid = make_trajectory(
            answer="The bug is somewhere in the request layer." + " padding" * 4,
            tool_calls=calls,
            trace_dir=_trace_naming(tmp_path, *gold),
        )
        said_but_unlocated = make_trajectory(
            answer=f"The files that must change are {gold[0]} and {gold[1]}.",
            tool_calls=calls,
        )

        evidence_only = score_checks(checks, task, located_but_unsaid)
        answer_only = score_checks(checks, task, said_but_unlocated)
        assert evidence_only.outcomes["gold_recall"].score == 0.0
        assert evidence_only.outcomes["gold_location_evidence"].score == 1.0
        assert answer_only.outcomes["gold_recall"].score == 1.0
        assert answer_only.outcomes["gold_location_evidence"].score == 0.0
        assert evidence_only.score == answer_only.score == pytest.approx(0.5)
        # …and that half is what a judge skip converts into a verdict.
        assert verdict_when_judge_skipped(rubric, evidence_only.score) == pytest.approx(0.25)


class TestTheTwoArms:
    def test_they_differ_only_in_corpus(self, bug_loc) -> None:
        first, second = bug_loc.arms
        assert (first.dataset, second.dataset) == ("swe-bench-verified-loc", "lca-bug-loc")
        assert first.task_name == second.task_name == "bug_loc"
        assert first.scoring.rubric == second.scoring.rubric

    def test_one_objective_each_and_a_ladder_that_ranks_it(self, bug_loc) -> None:
        assert {str(arm.scoring.objective) for arm in bug_loc.arms} == {"rubric_verdict"}
        assert bug_loc.ladder.rungs[-1].fitness_name == "ask_rubric"

    def test_a_shared_objective_still_yields_distinct_arm_hashes(self, bug_loc) -> None:
        # ``dataset`` is part of the canonical arm cell, so binding ONE rubric
        # from both arms does not let either resume the other's ledger rows —
        # which is what makes a shared objective safe here.
        first, second = resolve_arms(bug_loc)
        assert first.objective_hash == second.objective_hash
        assert first.arm_hash != second.arm_hash

    def test_the_arm_plan_lists_both_under_the_same_framing(self, bug_loc, capsys) -> None:
        print_arm_plan(resolve_arms(bug_loc))
        out = capsys.readouterr().out
        assert out.count("task_name=bug_loc") == 2
        assert "dataset=swe-bench-verified-loc@" in out
        assert "dataset=lca-bug-loc@" in out

    def test_the_dry_run_walks_both_arms_and_spends_nothing(
        self, bug_loc, tmp_path: Path, capsys
    ) -> None:
        # The owner-facing gate: a config that cannot be dry-run cannot be
        # reviewed before spend. Scripted doubles all the way down, so this
        # asserts the whole pipeline wires up AND that the total is $0.00.
        import asyncio

        from pydocs_eval.optimize.dry_run import dry_run

        exit_code = asyncio.run(dry_run(bug_loc, ledger_path=tmp_path / "ledger.jsonl"))
        out = capsys.readouterr().out
        assert exit_code == 0
        assert out.count("task_name=bug_loc") == 2
        assert "total spend: $0.00" in out


class TestTheSharedTaskHead:
    def test_both_arms_fold_the_SAME_harness_invariant_section_text(self, bug_loc) -> None:
        from pydocs_mcp.harness.builtin.ask_your_docs.agent import _resolved_skill_block
        from pydocs_mcp.harness.platform.skill_artifact import load_packaged_skill

        blocks = {_resolved_skill_block(None, arm.task_name) for arm in bug_loc.arms}
        assert len(blocks) == 1
        block = next(iter(blocks))
        assert block is not None
        assert load_packaged_skill().task_head("bug_loc") in block

    def test_the_ask_harness_delivers_the_new_sections(self) -> None:
        from pydocs_mcp.harness.builtin.ask_your_docs.binding import (
            DELIVERED_SECTION_CHANNELS,
            RECOGNIZED_UNDELIVERED_SECTIONS,
        )

        channel = "system_prompt_suffix.skill_block"
        assert DELIVERED_SECTION_CHANNELS["TASK_HEAD: bug_loc"] == channel
        assert DELIVERED_SECTION_CHANNELS["HARNESS_TASK_HEAD: ask_your_docs.bug_loc"] == channel
        # The other harness's slice travels with the document but is not ours.
        assert "HARNESS_TASK_HEAD: external.bug_loc" in RECOGNIZED_UNDELIVERED_SECTIONS


class TestTheSplitProbeKeysOnTheRecord:
    def test_the_probe_partitions_the_same_unit_the_fitness_splits_on(self, tmp_path: Path) -> None:
        # A preflight that partitions on the row id while the paid run
        # partitions on the record reports a split the run will never produce.
        import asyncio

        from pydocs_eval.datasets.bug_localization import SweBenchVerifiedLocDataset
        from pydocs_eval.optimize._split import partition_task_ids, task_split
        from pydocs_eval.optimize.dry_run import _probe_split_keys

        fixture = Path(__file__).parents[1] / "fixtures" / "swe_bench_verified_loc_mini.jsonl"
        path = _edited(
            tmp_path,
            "dataset: { name: swe-bench-verified-loc }",
            f"dataset: {{ name: swe-bench-verified-loc, fixture_path: {fixture} }}",
            stem="fixture_backed",
        )
        cfg = load_run_config(path)

        probe_train, probe_holdout = partition_task_ids(_probe_split_keys(cfg))

        async def _drain():
            return [t async for t in SweBenchVerifiedLocDataset(fixture_path=fixture).tasks()]

        rows = asyncio.run(_drain())
        fitness_train = [t for t in rows if task_split(t.record_id) == "train"]
        assert (len(probe_train), len(probe_holdout)) == (
            len(fitness_train),
            len(rows) - len(fitness_train),
        )
        assert set(probe_train) == {t.record_id for t in fitness_train}
