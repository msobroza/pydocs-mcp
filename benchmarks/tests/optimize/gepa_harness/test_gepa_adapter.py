"""The thin GEPAAdapter: firewall-before-rollout (R3), projection, lineage, budget seam.

These drive ``adapter.evaluate`` directly (it is synchronous and calls
``asyncio.run`` per candidate), so the tests are plain ``def`` — a running event
loop would break the internal ``asyncio.run``. gepa supplies ``EvaluationBatch``;
``pydocs_mcp`` is pulled transitively via ``Candidate`` ([retrieval] extra).
"""

from __future__ import annotations

import types
from pathlib import Path

from pydocs_eval.optimize.candidates.candidate import Candidate
from pydocs_eval.optimize.gepa_harness.adapter import CampaignGEPAAdapter
from pydocs_eval.optimize.gepa_harness.reflective import InstanceTrajectory

_INSTANCES = ("i1", "i2")


def _seed() -> Candidate:
    return Candidate.seed()


def _corrupt_tool(seed: Candidate) -> Candidate:
    """A candidate that fails the firewall: a TOOL section stripped of its markers."""
    tool_key = next(k for k in seed.sections if k.startswith("TOOL:"))
    return Candidate.from_gepa({**seed.sections, tool_key: "no required markers here"})


def _mutate_server(seed: Candidate) -> Candidate:
    """A firewall-valid mutation: fresh SERVER_INSTRUCTIONS prose (no markers/budget)."""
    return Candidate.from_gepa(
        {**seed.sections, "SERVER_INSTRUCTIONS": "A distinct, valid server instruction body."}
    )


def _adapter(harness, tmp_path: Path, **fitness_kwargs) -> CampaignGEPAAdapter:
    return CampaignGEPAAdapter(
        fitness=harness.make_fitness(tmp_path, _INSTANCES, **fitness_kwargs),
        ledger=harness.make_ledger(tmp_path),
        seed_hash=_seed().candidate_hash,
    )


def test_invalid_candidate_is_firewalled_before_any_rollout(harness, tmp_path: Path) -> None:
    """An invalid candidate returns failure scores with NO rollout_fn call (R3)."""
    # rollout_raises=True: if evaluate touched the campaign, the fake would raise.
    adapter = _adapter(harness, tmp_path, rollout_raises=True)
    invalid = _corrupt_tool(_seed())
    batch = adapter.evaluate(list(_INSTANCES), invalid.to_gepa(), capture_traces=True)
    assert batch.scores == [0.0, 0.0]
    assert batch.num_metric_calls == 0  # budget seam holds even on the reject path
    record = adapter.ledger.latest(invalid.candidate_hash)
    assert record is not None and record.valid is False
    assert record.n_rollouts == 0 and record.campaign_ids == ()  # zero-rollout, demonstrable (R3)
    assert record.violations  # the violation was recorded
    assert all(t.valid is False and t.feedback for t in batch.trajectories)


def test_valid_candidate_projects_scores_and_records_seed(harness, tmp_path: Path) -> None:
    """A valid candidate delegates to CampaignFitness and projects (score, feedback)."""
    adapter = _adapter(harness, tmp_path, soft=0.5)
    batch = adapter.evaluate(list(_INSTANCES), _seed().to_gepa(), capture_traces=True)
    assert batch.scores == [0.5, 0.5]
    assert batch.num_metric_calls == 0
    assert {t.instance_id: t.feedback for t in batch.trajectories} == {
        "i1": "feedback-i1",
        "i2": "feedback-i2",
    }
    record = adapter.ledger.latest(_seed().candidate_hash)
    assert record.valid is True and record.n_rollouts == 2
    assert record.lineage_parent is None and record.mutation_record.proposer == "seed"
    assert record.minibatch_scores == {"i1": 0.5, "i2": 0.5}
    assert len(record.campaign_ids) == 1


def test_reflection_lineage_is_stashed_and_attributed(harness, tmp_path: Path) -> None:
    """make_reflective_dataset stashes parent+refs; the next child inherits them (R3)."""
    adapter = _adapter(harness, tmp_path)
    seed = _seed()
    adapter.evaluate(list(_INSTANCES), seed.to_gepa(), capture_traces=True)  # records seed
    eval_batch = types.SimpleNamespace(
        trajectories=[InstanceTrajectory("i1", 0.4, "grep missed the file")]
    )
    dataset = adapter.make_reflective_dataset(seed.to_gepa(), eval_batch, ["SERVER_INSTRUCTIONS"])
    assert dataset["SERVER_INSTRUCTIONS"][0]["Feedback"] == "grep missed the file"

    child = _mutate_server(seed)
    adapter.evaluate(list(_INSTANCES), child.to_gepa(), capture_traces=False)
    record = adapter.ledger.latest(child.candidate_hash)
    assert record.lineage_parent == seed.candidate_hash
    assert record.mutation_record.proposer == "reflection"
    assert record.mutation_record.component == "SERVER_INSTRUCTIONS"
    assert record.reflector_input_refs  # the exact facts shown to the reflector
    for ref in record.reflector_input_refs:
        assert (adapter.ledger.blobs_dir / ref).is_file()  # content-addressed on disk


def test_evaluate_records_each_candidate_once(harness, tmp_path: Path) -> None:
    """gepa re-evaluates a candidate on several minibatches — the ledger records once."""
    adapter = _adapter(harness, tmp_path)
    adapter.evaluate(list(_INSTANCES), _seed().to_gepa())
    adapter.evaluate(["i1"], _seed().to_gepa())  # same candidate, different minibatch
    assert len(adapter.ledger.records()) == 1
