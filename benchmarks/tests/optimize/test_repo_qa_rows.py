"""Multi-framing row identity: record_id, the record-keyed split, the ledger.

The three consequences of the ``repo_qa`` minting event on the ask track:
``sample_row_for_task`` derives both identity keys from explicit fields first,
the train/holdout split is keyed on the RECORD so sibling framings never
straddle it, and the sample ledger carries the record without moving a single
pre-framing line byte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize._split import task_split
from pydocs_eval.optimize.ask_binding import FakeAskRunner
from pydocs_eval.optimize.fitness.ask_rubric import AskRubricFitness, sample_row_for_task
from pydocs_eval.optimize.rubric.judge import FakeRubricJudge
from pydocs_eval.optimize.rubric.model import (
    GateCheck,
    RubricConfig,
    RubricCriterion,
    SampleRubricRecord,
)
from pydocs_eval.optimize.rubric.sample_ledger import SampleRubricLedger, _as_line
from tests.optimize._trajectories import make_trajectory, server_call


def _task(task_id: str, *, record_id: str = "", query: str = "q") -> EvalTask:
    return EvalTask(
        task_id=task_id,
        query=query,
        gold=GoldAnswer(file_set=("pkg/mod.py",)),
        corpus_source=lambda: None,  # type: ignore[arg-type,return-value]
        record_id=record_id,
    )


class TestSampleRowIdentityDerivation:
    def test_a_two_part_id_keeps_its_pre_framing_derivation(self) -> None:
        # The RECORD keeps the whole two-part id (that is what the split hashes
        # and what must never move); the FRAMING is not derivable from a corpus
        # prefix, so it falls back to the default.
        row = sample_row_for_task(_task("ccv/cve-2099-0001"))
        assert row["record_id"] == "ccv/cve-2099-0001"
        assert row["task_name"] == "repo_qa"

    def test_a_bare_id_still_falls_back_to_itself_then_to_the_arm(self) -> None:
        bare = _task("cve-2025-10283")
        assert sample_row_for_task(bare)["record_id"] == "cve-2025-10283"
        assert sample_row_for_task(bare)["task_name"] == "repo_qa"
        assert sample_row_for_task(bare, task_name="vuln")["task_name"] == "vuln"

    def test_a_three_part_id_yields_the_record_and_the_framing(self) -> None:
        row = sample_row_for_task(_task("repoqa-qa/repo_qa/psf/black@f0/x.py::y"))
        assert row["record_id"] == "psf/black@f0/x.py::y"
        assert row["task_name"] == "repo_qa"

    def test_a_swe_qa_style_id_is_not_mistaken_for_a_framed_one(self) -> None:
        # ``swe_qa/<repo>/<index>`` has three segments and NO framing: a
        # positional parser would report task_name="matplotlib" and a
        # prefix-reading one task_name="swe_qa" — neither is an enumerated
        # framing, so the record stays whole and the framing takes the default.
        row = sample_row_for_task(_task("swe_qa/matplotlib/12"))
        assert row["record_id"] == "swe_qa/matplotlib/12"
        assert row["task_name"] == "repo_qa"

    def test_the_explicit_record_field_beats_the_id_parse(self) -> None:
        row = sample_row_for_task(_task("repoqa-qa/repo_qa/r1", record_id="EXPLICIT"))
        assert row["record_id"] == "EXPLICIT"

    def test_the_arms_task_name_still_wins_over_a_framed_id(self) -> None:
        row = sample_row_for_task(_task("repoqa-qa/repo_qa/r1"), task_name="vuln")
        assert row["task_name"] == "vuln"

    def test_every_resolved_framing_is_one_the_product_can_serve(self) -> None:
        # The chain has exactly three steps — arm, framed id, default — and no
        # dataset-prefix step, precisely so it can never hand the product a
        # name ``task_head_section_header`` raises on. A prefix is a corpus
        # namespace; only these two spellings are framings.
        from pydocs_mcp.harness.core.skill_artifact_loader import task_head_section_header

        for task_id in (
            "cve-2025-10283",
            "ccv/cve-2099-0001",
            "sweqapro/swe_qa_pro/qiboteam/qibo/12",
            "swe_qa/matplotlib/12",
            "repoqa-qa/repo_qa/r1",
        ):
            resolved = str(sample_row_for_task(_task(task_id))["task_name"])
            assert task_head_section_header(resolved)  # raises on an unknown name


class TestTheSplitIsKeyedOnTheRecord:
    def test_sibling_framings_of_one_record_land_on_the_same_side(self) -> None:
        # The leak the amendment forbids: two rows minted from one record whose
        # composite ids hash to different sides.
        record = next(
            r
            for r in (f"record-{i}" for i in range(64))
            if task_split(r) != task_split(f"repoqa-qa/repo_qa/{r}")
        )
        siblings = (
            _task(f"repoqa/{record}", record_id=record),
            _task(f"repoqa-qa/repo_qa/{record}", record_id=record),
        )
        assert len({task_split(t.record_id) for t in siblings}) == 1

    async def test_a_pre_framing_corpus_keeps_its_exact_split_membership(
        self, tmp_path: Path
    ) -> None:
        ids = tuple(f"question {i}?" for i in range(16))
        fitness = _fitness(tmp_path, dataset=_ListDataset(ids=ids))
        selected = await fitness._split_tasks("train")
        assert {t.task_id for t in selected} == {i for i in ids if task_split(i) == "train"}

    async def test_a_framed_corpus_splits_on_the_record_not_the_row(self, tmp_path: Path) -> None:
        ids = tuple(f"n{i}" for i in range(24))
        dataset = _ListDataset(ids=tuple(f"repoqa-qa/repo_qa/{i}" for i in ids), records=ids)
        fitness = _fitness(tmp_path, dataset=dataset)
        selected = await fitness._split_tasks("train")
        assert {t.record_id for t in selected} == {i for i in ids if task_split(i) == "train"}

    async def test_a_framed_row_WITHOUT_the_field_splits_on_the_parsed_record(
        self, tmp_path: Path
    ) -> None:
        # The split key and the harness/ledger key are ONE derivation. A framed
        # row carrying no explicit record_id must NOT partition on its row id
        # while ``sample_row_for_task`` clusters it under the parsed record.
        records = tuple(f"n{i}" for i in range(24))
        framed = tuple(f"repoqa-qa/repo_qa/{r}" for r in records)
        # records == ids leaves ``record_id`` empty, so only the id parse can
        # recover the record — the branch the explicit-field tests never reach.
        dataset = _ListDataset(ids=framed, records=framed)
        fitness = _fitness(tmp_path, dataset=dataset)
        selected = await fitness._split_tasks("train")
        assert {t.task_id for t in selected} == {
            f"repoqa-qa/repo_qa/{r}" for r in records if task_split(r) == "train"
        }

    async def test_fieldless_sibling_framings_never_straddle_the_split(
        self, tmp_path: Path
    ) -> None:
        record = next(
            r
            for r in (f"record-{i}" for i in range(64))
            if task_split(r) != task_split(f"repoqa-qa/repo_qa/{r}")
        )
        siblings = (f"repoqa-qa/repo_qa/{record}", f"swe-qa-questions/repo_qa/{record}")
        # Filler keeps both sides non-empty; partition_task_ids rejects a
        # one-sided pool, which would mask the property under test.
        filler = tuple(f"repoqa-qa/repo_qa/f{i}" for i in range(16))
        ids = siblings + filler
        fitness = _fitness(tmp_path, dataset=_ListDataset(ids=ids, records=ids))
        sides = {
            side: {t.task_id for t in await fitness._split_tasks(side)}
            for side in ("train", "holdout")
        }
        assert sides["train"] and sides["holdout"]
        landed = {side for side, members in sides.items() if members & set(siblings)}
        assert len(landed) == 1


class TestTheLedgerCarriesTheRecord:
    def test_a_pre_framing_line_is_byte_identical_to_the_previous_writer(self) -> None:
        record = _record(task_id="ccv/cve-1", record_id="")
        assert "record_id" not in _as_line(record)

    def test_a_framed_line_carries_the_record_id(self) -> None:
        record = _record(task_id="repoqa-qa/repo_qa/n1", record_id="n1")
        assert _as_line(record)["record_id"] == "n1"

    def test_a_legacy_line_without_the_field_still_parses(self, tmp_path: Path) -> None:
        line = _as_line(_record(task_id="ccv/cve-1", record_id=""))
        path = tmp_path / "samples.jsonl"
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
        ledger = SampleRubricLedger(path)
        hit = ledger.lookup(
            fingerprint="fp", split="train", task_id="ccv/cve-1", objective_hash="obj"
        )
        assert hit is not None and hit.record_id == ""

    async def test_a_framed_run_stamps_the_record_on_every_line(self, tmp_path: Path) -> None:
        ids = tuple(f"n{i}" for i in range(24))
        dataset = _ListDataset(ids=tuple(f"repoqa-qa/repo_qa/{i}" for i in ids), records=ids)
        fitness = _fitness(tmp_path, dataset=dataset)
        await fitness.evaluate(_Artifact(), split="train")
        lines = [
            json.loads(line)
            for line in (tmp_path / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert lines
        for line in lines:
            assert line["record_id"] and line["task_id"].endswith(line["record_id"])


# --- fixtures ------------------------------------------------------------


@dataclass(slots=True)
class _ListDataset:
    ids: tuple[str, ...]
    records: tuple[str, ...] = ()
    name: str = "fake"
    revision: str = "0"

    async def tasks(self):
        records = self.records or self.ids
        for task_id, record in zip(self.ids, records, strict=True):
            yield _task(task_id, record_id="" if record == task_id else record, query=task_id)


@dataclass(frozen=True, slots=True)
class _Artifact:
    name: str = "ask_prompt"
    content: str = "seed"

    def render(self) -> str:
        return self.content

    def with_content(self, content: str) -> _Artifact:
        return _Artifact(content=content)

    def validate(self) -> tuple[str, ...]:
        return ()

    def landing_note(self) -> str:
        return "test"

    @property
    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(self.render().encode()).hexdigest()


def _record(*, task_id: str, record_id: str) -> SampleRubricRecord:
    return SampleRubricRecord(
        fingerprint="fp",
        split="train",
        task_id=task_id,
        qa_type="",
        objective_hash="obj",
        gates={},
        gate_pass_fraction=1.0,
        judge_skipped=False,
        criteria={},
        rubric_score=1.0,
        verdict=1.0,
        turns=1,
        wall_seconds=0.1,
        cost_usd=0.0,
        answer_sha256="x",
        record_id=record_id,
    )


def _fitness(tmp_path: Path, *, dataset: _ListDataset) -> AskRubricFitness:
    # The runner is keyed on the sample's ``record_id`` (the contract's own
    # key), which for a framed row is the RECORD — exactly what this change
    # makes the harness see.
    records = dataset.records or dataset.ids
    runner = FakeAskRunner(
        scripted={
            record: make_trajectory(
                answer="pkg/mod.py " + "x" * 40, tool_calls=(server_call("search_codebase"),)
            )
            for record in records
        }
    )
    judge = FakeRubricJudge(scripted={task_id: {"correctness": 8.0} for task_id in dataset.ids})
    return AskRubricFitness(
        dataset=dataset,
        runner_factory=lambda artifact: runner,
        judge=judge,
        rubric=RubricConfig(
            gates=(GateCheck(name="grounded", kind="gold_substring", params={}),),
            criteria=(RubricCriterion(name="correctness", weight=1.0, description="right"),),
        ),
        architecture="text_react",
        sample_ledger=SampleRubricLedger(tmp_path / "samples.jsonl"),
        output_dir=tmp_path / "out",
    )
