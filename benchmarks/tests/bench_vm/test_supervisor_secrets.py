"""Provider keys reach the child's environment, and only the child's.

Driven through ``python -m pydocs_eval.bench_vm supervise`` against the fake
cloud, whose Secret Manager holds a fake value per key. The child (and the
kind's pre-flight) get exactly the keys their kind declares; afterwards every
place the run could have leaked one is scanned: every object in the fake GCS
(run record, spec copy, synced state), every file in the local state
directory, every request the fake cloud recorded, and the supervisor's output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.bench_vm.run_record import RunStatus

from ._git_fixture import FIXTURE_CONFIG, run_git
from ._spec_dicts import agent_track_spec, before_after_spec, retrieval_spec
from ._supervise_harness import FAKE_PROVIDER_KEYS, SuperviseHarness, SuperviseResult

_ANTHROPIC = FAKE_PROVIDER_KEYS["ANTHROPIC_API_KEY"]
_OPENROUTER = FAKE_PROVIDER_KEYS["OPENROUTER_API_KEY"]


def _launch_before_after(harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]) -> str:
    repo = harness.launch.repo
    run_git(repo.path, "tag", "v-old", repo.head())
    repo.write("notes/candidate.txt", "candidate\n")
    harness.launch.publish_images(repo.commit("candidate commit"))
    repo.push_main()
    spec = before_after_spec(
        configs=[FIXTURE_CONFIG], baseline_ref="v-old", args=["--model", "qwen/qwen3-4b"]
    )
    return harness.launch_run(capsys, "before-after", spec)


def _files_under(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()] if root.is_dir() else []


def _places_holding(harness: SuperviseHarness, result: SuperviseResult, value: str) -> list[str]:
    """Every place outside the child's environment where ``value`` appears."""
    cloud = harness.launch.cloud
    needle = value.encode()
    places = [
        f"gcs:{path.relative_to(cloud.root)}"
        for path in _files_under(cloud.root)
        if needle in path.read_bytes()
    ]
    places += [
        f"state:{path.relative_to(harness.state_dir)}"
        for path in _files_under(harness.state_dir)
        if needle in path.read_bytes()
    ]
    places += [f"request:{request}" for request in cloud.requests if value in repr(request)]
    if value in result.err:
        places.append("supervisor stderr")
    return places


def _read_secret_ids(harness: SuperviseHarness) -> list[str]:
    requests = harness.launch.cloud.requests
    return [str(r.args["secret_id"]) for r in requests if r.operation == "access_secret"]


def test_the_agent_track_child_gets_only_the_anthropic_key(
    supervise_harness: SuperviseHarness,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A key the container happened to carry must not reach a kind that did not declare it.
    monkeypatch.setenv("OPENROUTER_API_KEY", "inherited-from-the-container")
    run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 0, result.err
    assert [i["provider_keys"] for i in result.invocations] == [
        {"ANTHROPIC_API_KEY": _ANTHROPIC},  # the pre-flight
        {"ANTHROPIC_API_KEY": _ANTHROPIC},  # the child
    ]
    assert _read_secret_ids(supervise_harness) == ["bench-anthropic-api-key"]


def test_the_before_after_child_gets_only_the_openrouter_key(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _launch_before_after(supervise_harness, capsys)

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 0, result.err
    assert result.started["provider_keys"] == {"OPENROUTER_API_KEY": _OPENROUTER}
    assert _read_secret_ids(supervise_harness) == ["bench-openrouter-api-key"]


def test_a_retrieval_child_gets_no_provider_key_and_no_secret_is_read(
    supervise_harness: SuperviseHarness,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inherited-from-the-container")
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(capsys, run_id)

    assert result.started["provider_keys"] == {}
    assert _read_secret_ids(supervise_harness) == []


@pytest.mark.parametrize("kind", ["agent_track", "before_after"])
def test_no_key_value_appears_anywhere_but_the_childs_environment(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    if kind == "agent_track":
        run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())
    else:
        run_id = _launch_before_after(supervise_harness, capsys)

    result = supervise_harness.supervise(capsys, run_id, FIXTURE_TICKS="3", FIXTURE_TICK_S="0.1")

    assert supervise_harness.record(run_id).status is RunStatus.SUCCEEDED, result.err
    # The scan is not vacuous: the run synced state and the child saw a key.
    assert supervise_harness.launch.cloud.syncs
    assert any(result.started["provider_keys"].values())
    for value in FAKE_PROVIDER_KEYS.values():
        assert _places_holding(supervise_harness, result, value) == []


def test_a_secret_that_cannot_be_read_fails_the_run_before_any_preflight(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    del supervise_harness.launch.cloud.secrets["bench-anthropic-api-key"]
    run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())

    result = supervise_harness.supervise(capsys, run_id)

    last = supervise_harness.record(run_id).history[-1]
    assert result.exit_code == 1
    assert last.status is RunStatus.FAILED
    assert last.detail.startswith("reading the run's provider keys failed: ")
    assert "bench-anthropic-api-key" in last.detail
    assert result.invocations == []


def test_a_key_the_preflight_echoes_is_redacted_in_its_log_and_the_supervisors(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())

    # The padding puts the cut of the logged output's tail inside the key.
    result = supervise_harness.supervise(
        capsys, run_id, FIXTURE_PREFLIGHT_ECHO_KEYS="1", FIXTURE_PREFLIGHT_PAD="15989"
    )

    assert result.exit_code == 0, result.err
    assert _places_holding(supervise_harness, result, _ANTHROPIC) == []
    assert _ANTHROPIC[-10:] not in result.err
    log = supervise_harness.state_dir / "logs" / "preflight-attempt-1.log"
    assert "<ANTHROPIC_API_KEY>" in log.read_text(encoding="utf-8")
