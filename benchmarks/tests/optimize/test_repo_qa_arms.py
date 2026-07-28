"""The flagship: two arms, two corpora, two rubrics, ONE shared TASK_HEAD.

Pins the demonstration the ``repo_qa`` minting event exists to make: arms
sharing a ``task_name`` share the harness-invariant guidance section while
binding independent objectives and independent observational metrics.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from pydocs_eval.optimize.arm_runtime import resolve_arms
from pydocs_eval.optimize.ask_binding import known_task_names
from pydocs_eval.optimize.dry_run import print_arm_plan
from pydocs_eval.optimize.run_config import _configured_rubric_sections, load_run_config

_FLAGSHIP = "optimize_search_skill_repo_qa.yaml"


def _shipped(name: str) -> Path:
    return Path(str(resources.files("pydocs_eval.optimize.configs").joinpath(name)))


@pytest.fixture
def flagship():
    return load_run_config(_shipped(_FLAGSHIP))


class TestTheTaskNameParityChain:
    def test_the_eval_side_reads_the_product_tuple_with_no_local_copy(self) -> None:
        from pydocs_mcp.harness.core.skill_artifact_loader import TASK_NAMES

        assert known_task_names() == TASK_NAMES
        assert "repo_qa" in TASK_NAMES

    def test_the_load_firewall_admits_repo_qa_without_an_eval_side_edit(self, flagship) -> None:
        # Loading already ran ``_require_enumerated_task_name`` on both arms.
        assert {arm.task_name for arm in flagship.arms} == {"repo_qa"}


class TestTwoObjectivesInOneConfig:
    def test_both_named_rubric_sections_are_bindable(self, flagship) -> None:
        assert sorted(_configured_rubric_sections(flagship)) == [
            "ask_rubric",
            "ask_rubric_localization",
        ]

    def test_an_arm_naming_an_unconfigured_section_fails_at_load(self, tmp_path: Path) -> None:
        text = (
            _shipped(_FLAGSHIP)
            .read_text(encoding="utf-8")
            .replace("rubric: ask_rubric_localization", "rubric: ask_rubric_typo")
        )
        path = tmp_path / "bad.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="ask_rubric_typo"):
            load_run_config(path)

    def test_a_second_section_with_bad_weights_fails_at_load_not_at_trial_14(
        self, tmp_path: Path
    ) -> None:
        # The single-section validation loop was the gap: only ``ask_rubric``
        # was ever checked, so a second objective's weights slipped through.
        text = (
            _shipped(_FLAGSHIP)
            .read_text(encoding="utf-8")
            .replace("{ name: identification, weight: 0.7", "{ name: identification, weight: 0.9")
        )
        path = tmp_path / "bad_weights.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="weights must sum"):
            load_run_config(path)


class TestTheDeterministicLayerIsNotACliff:
    def test_the_localization_objective_keeps_its_gate_score_on_a_skip(self, flagship) -> None:
        # gold_substring_all is this section's SCORING signal, not a screen: a
        # symbol-only answer must not score identically to an empty one, or the
        # arm's objective is flat 0/1 and accept_margin can never be met.
        from pydocs_eval.optimize.fitness.ask_rubric import verdict_when_judge_skipped

        rubric = flagship.ask_rubric_localization.rubric_config
        assert rubric.keep_deterministic_on_skip is True
        assert verdict_when_judge_skipped(rubric, 0.5) == pytest.approx(0.25)
        assert verdict_when_judge_skipped(rubric, 0.0) == pytest.approx(0.0)
        # Still strictly below any judged sample: the rubric weight is unearned.
        assert verdict_when_judge_skipped(rubric, 1.0) < 1.0

    def test_the_screen_only_objective_keeps_the_historical_cliff(self, flagship) -> None:
        from pydocs_eval.optimize.fitness.ask_rubric import verdict_when_judge_skipped

        rubric = flagship.ask_rubric.rubric_config
        assert rubric.keep_deterministic_on_skip is False
        assert verdict_when_judge_skipped(rubric, 0.5) == 0.0

    def test_the_knob_is_reachable_from_yaml_at_all(self, tmp_path: Path) -> None:
        # It was landed on RubricConfig but never forwarded, so no config could
        # set it — pydantic silently dropped the key.
        text = (
            _shipped(_FLAGSHIP)
            .read_text(encoding="utf-8")
            .replace("  keep_deterministic_on_skip: true", "  keep_deterministic_on_skip: false")
        )
        path = tmp_path / "cliff.yaml"
        path.write_text(text, encoding="utf-8")
        assert (
            load_run_config(path).ask_rubric_localization.rubric_config.keep_deterministic_on_skip
            is False
        )


class TestEveryConfiguredObjectiveIsBound:
    def test_a_section_no_arm_binds_fails_at_load(self, tmp_path: Path) -> None:
        # Before a second section existed, "configured" implied "bound". A
        # declared objective nothing scores is a silent no-op, not a default.
        text = (
            _shipped(_FLAGSHIP)
            .read_text(encoding="utf-8")
            .replace("      rubric: ask_rubric_localization", "      rubric: ask_rubric")
        )
        path = tmp_path / "orphan.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="ask_rubric_localization"):
            load_run_config(path)

    def test_an_arms_free_config_may_bind_only_the_implicit_section(self, tmp_path: Path) -> None:
        text = _shipped(_FLAGSHIP).read_text(encoding="utf-8")
        head, _sep, _arms = text.partition("\narms:\n")
        path = tmp_path / "implicit.yaml"
        path.write_text(head, encoding="utf-8")
        with pytest.raises(ValueError, match="ask_rubric_localization"):
            load_run_config(path)


class TestTheDryRunProbeGuardsTheRealPartition:
    def test_the_probe_keys_on_the_same_unit_the_fitness_splits_on(self, tmp_path: Path) -> None:
        # A preflight that partitions on the row id while the paid run
        # partitions on the record reports — and guards — a split the run will
        # never produce. Sync by necessity: ``_probe_split_keys`` drains the
        # fixture through ``asyncio.run``, exactly as the CLI does.
        import asyncio

        from pydocs_eval.datasets.repo_qa import RepoQaQuestionDataset
        from pydocs_eval.optimize._split import partition_task_ids, task_split
        from pydocs_eval.optimize.dry_run import _probe_split_keys

        fixture = Path(__file__).parents[1] / "fixtures" / "repoqa_mini.json"
        text = (
            _shipped(_FLAGSHIP)
            .read_text(encoding="utf-8")
            .replace(
                "dataset: { name: repoqa-qa }",
                f"dataset: {{ name: repoqa-qa, fixture_path: {fixture} }}",
            )
        )
        path = tmp_path / "fixture_backed.yaml"
        path.write_text(text, encoding="utf-8")
        cfg = load_run_config(path)

        probe_train, probe_holdout = partition_task_ids(_probe_split_keys(cfg))

        async def _drain():
            return [t async for t in RepoQaQuestionDataset(fixture_path=fixture).tasks()]

        rows = asyncio.run(_drain())
        fitness_train = [t for t in rows if task_split(t.record_id) == "train"]
        assert (len(probe_train), len(probe_holdout)) == (
            len(fitness_train),
            len(rows) - len(fitness_train),
        )
        assert set(probe_train) == {t.record_id for t in fitness_train}


class TestTheFramedRevisionCarriesTheCorpusPin:
    def test_each_framing_stamps_its_wrapped_release(self) -> None:
        from pydocs_eval.datasets.repo_qa import RepoQaQuestionDataset, SweQaQuestionDataset
        from pydocs_eval.datasets.repoqa import RepoQADataset
        from pydocs_eval.datasets.swe_qa import SweQaDataset

        # ``dataset={name}@{revision}`` in the arm plan and every run record
        # must distinguish two runs over different corpus releases.
        assert RepoQaQuestionDataset().revision.endswith(RepoQADataset.revision)
        assert SweQaQuestionDataset().revision.endswith(SweQaDataset.revision)
        assert RepoQaQuestionDataset().revision != SweQaQuestionDataset().revision


class TestTheTwoArms:
    def test_they_differ_in_corpus_rubric_and_watched_metrics(self, flagship) -> None:
        first, second = flagship.arms
        assert (first.dataset, second.dataset) == ("repoqa-qa", "swe-qa-questions")
        assert (first.scoring.rubric, second.scoring.rubric) == (
            "ask_rubric_localization",
            "ask_rubric",
        )
        assert set(first.scoring.tracked) != set(second.scoring.tracked)

    def test_one_objective_each_and_a_ladder_that_ranks_it(self, flagship) -> None:
        assert {str(arm.scoring.objective) for arm in flagship.arms} == {"rubric_verdict"}
        assert flagship.ladder.rungs[-1].fitness_name == "ask_rubric"

    def test_different_rubrics_mean_different_objective_and_arm_hashes(self, flagship) -> None:
        first, second = resolve_arms(flagship)
        assert first.objective_hash != second.objective_hash
        assert first.arm_hash != second.arm_hash

    def test_the_arm_plan_lists_both_under_the_same_framing(self, flagship, capsys) -> None:
        print_arm_plan(resolve_arms(flagship))
        out = capsys.readouterr().out
        assert out.count("task_name=repo_qa") == 2
        assert "dataset=repoqa-qa@" in out and "dataset=swe-qa-questions@" in out


class TestTheSharedTaskHead:
    def test_both_arms_fold_the_SAME_harness_invariant_section_text(self, flagship) -> None:
        from pydocs_mcp.harness.ask_your_docs.agent import _resolved_skill_block
        from pydocs_mcp.harness.core.skill_artifact_loader import load_packaged_skill

        blocks = {_resolved_skill_block(None, arm.task_name) for arm in flagship.arms}
        assert len(blocks) == 1
        block = next(iter(blocks))
        assert block is not None
        assert load_packaged_skill().task_head("repo_qa") in block

    def test_the_ask_harness_delivers_the_new_sections(self) -> None:
        from pydocs_mcp.harness.ask_your_docs.binding import (
            DELIVERED_SECTION_CHANNELS,
            RECOGNIZED_UNDELIVERED_SECTIONS,
        )

        assert DELIVERED_SECTION_CHANNELS["TASK_HEAD: repo_qa"] == (
            "system_prompt_suffix.skill_block"
        )
        assert DELIVERED_SECTION_CHANNELS["HARNESS_TASK_HEAD: ask_your_docs.repo_qa"] == (
            "system_prompt_suffix.skill_block"
        )
        # The other harness's slice travels with the document but is not ours.
        assert "HARNESS_TASK_HEAD: external.repo_qa" in RECOGNIZED_UNDELIVERED_SECTIONS
