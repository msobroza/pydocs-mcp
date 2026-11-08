"""Provider keys: where each lives in Secret Manager, how it is read, and where it may go.

The real adapter's ``gcloud secrets versions access`` is replayed by a
``ScriptedCommandRunner``; nothing reads a real secret. Every value here is a
fake, and the tests check it appears in no error message.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.bounded_command import CompletedCommand
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud
from pydocs_eval.bench_vm.provider_secrets import (
    PROVIDER_KEYS,
    child_environment_with_provider_keys,
    read_provider_keys,
    secret_id_for,
)
from pydocs_eval.bench_vm.run_kinds import RUN_KIND_TABLE, RunKind

from ._fakes import FakeBenchCloud, ScriptedCommandRunner, failed, ok
from ._spec_dicts import REPO_ROOT

_FAKE_ANTHROPIC = "sk-ant-FAKE-0b7e41d2-never-print-me"
_FAKE_OPENROUTER = "sk-or-FAKE-93c1aa07-never-print-me"
_SECRETS_TF = REPO_ROOT / "infra" / "bench-vm" / "terraform" / "secrets.tf"


def _gcloud(*script: CompletedCommand | str) -> tuple[GcloudBenchCloud, ScriptedCommandRunner]:
    runner = ScriptedCommandRunner(script=list(script))
    return GcloudBenchCloud(runner=runner, sleep=lambda _s: None), runner


# --- the kind table and Terraform agree ---------------------------------------------


def test_every_provider_key_a_kind_declares_is_a_secret_terraform_declares() -> None:
    declared = set(re.findall(r'=\s*"(bench-[a-z0-9-]+)"', _SECRETS_TF.read_text("utf-8")))
    needed = {secret_id_for(key) for row in RUN_KIND_TABLE.values() for key in row.provider_keys}

    assert needed == declared


def test_the_paid_kinds_declare_their_provider_keys() -> None:
    assert RUN_KIND_TABLE[RunKind.AGENT_TRACK].provider_keys == ("ANTHROPIC_API_KEY",)
    assert RUN_KIND_TABLE[RunKind.BEFORE_AFTER].provider_keys == ("OPENROUTER_API_KEY",)
    assert RUN_KIND_TABLE[RunKind.RETRIEVAL].provider_keys == ()
    assert frozenset({"ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"}) == PROVIDER_KEYS


def test_a_provider_key_maps_to_its_bench_secret() -> None:
    assert secret_id_for("ANTHROPIC_API_KEY") == "bench-anthropic-api-key"


# --- GcloudBenchCloud.access_secret --------------------------------------------------


def test_the_adapter_reads_the_latest_version_of_the_secret_in_the_project() -> None:
    cloud, runner = _gcloud(ok(_FAKE_ANTHROPIC))

    value = cloud.access_secret("bench-project", "bench-anthropic-api-key")

    assert value == _FAKE_ANTHROPIC
    call = runner.calls[0]
    assert call.argv[:5] == ("gcloud", "secrets", "versions", "access", "latest")
    assert "--secret=bench-anthropic-api-key" in call.argv
    assert "--project=bench-project" in call.argv
    assert call.timeout_s > 0


def test_the_adapter_drops_the_trailing_newline_an_echo_added() -> None:
    cloud, _ = _gcloud(ok(_FAKE_ANTHROPIC + "\n"))

    assert cloud.access_secret("bench-project", "bench-anthropic-api-key") == _FAKE_ANTHROPIC


def test_the_adapter_retries_a_transient_error() -> None:
    cloud, runner = _gcloud(failed("ERROR: HTTPError 503: backendError"), ok(_FAKE_ANTHROPIC))

    assert cloud.access_secret("bench-project", "bench-anthropic-api-key") == _FAKE_ANTHROPIC
    assert len(runner.calls) == 2


def test_the_adapter_gives_up_after_a_bounded_number_of_attempts() -> None:
    cloud, runner = _gcloud("timeout")

    with pytest.raises(BenchCloudError, match="bench-anthropic-api-key"):
        cloud.access_secret("bench-project", "bench-anthropic-api-key")
    assert len(runner.calls) == 3


def test_a_missing_secret_is_a_cloud_error_naming_it() -> None:
    cloud, _ = _gcloud(failed("ERROR: NOT_FOUND: Secret [bench-anthropic-api-key] not found"))

    with pytest.raises(BenchCloudError, match="bench-anthropic-api-key.*NOT_FOUND"):
        cloud.access_secret("bench-project", "bench-anthropic-api-key")


def test_a_secret_that_is_not_utf8_is_a_cloud_error_without_its_bytes() -> None:
    cloud, _ = _gcloud(CompletedCommand(returncode=0, stdout=b"\xffsk-\xfe", stderr=b""))

    with pytest.raises(BenchCloudError, match="expected UTF-8 text") as raised:
        cloud.access_secret("bench-project", "bench-anthropic-api-key")
    assert "sk-" not in str(raised.value)
    assert "\\xff" not in str(raised.value)


def test_an_empty_secret_is_refused() -> None:
    cloud, _ = _gcloud(ok("\n"))

    with pytest.raises(BenchCloudError, match="got an empty value"):
        cloud.access_secret("bench-project", "bench-anthropic-api-key")


# --- FakeBenchCloud.access_secret ------------------------------------------------------


def test_the_fake_answers_its_secrets_and_records_only_the_secret_id(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path, secrets={"bench-anthropic-api-key": _FAKE_ANTHROPIC})

    assert cloud.access_secret("bench-project", "bench-anthropic-api-key") == _FAKE_ANTHROPIC
    assert cloud.requests[-1].operation == "access_secret"
    assert _FAKE_ANTHROPIC not in repr(cloud.requests)


def test_the_fake_raises_on_a_secret_it_does_not_hold(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path)

    with pytest.raises(BenchCloudError, match="bench-openrouter-api-key"):
        cloud.access_secret("bench-project", "bench-openrouter-api-key")


# --- reading a kind's keys and building the child's environment ------------------------


def test_only_the_declared_keys_are_read(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(
        root=tmp_path,
        secrets={
            "bench-anthropic-api-key": _FAKE_ANTHROPIC,
            "bench-openrouter-api-key": _FAKE_OPENROUTER,
        },
    )

    keys = read_provider_keys(cloud, "bench-project", ("ANTHROPIC_API_KEY",))

    assert keys == {"ANTHROPIC_API_KEY": _FAKE_ANTHROPIC}
    read = [r.args["secret_id"] for r in cloud.requests if r.operation == "access_secret"]
    assert read == ["bench-anthropic-api-key"]


def test_a_kind_without_keys_reads_no_secret(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path)

    assert read_provider_keys(cloud, "bench-project", ()) == {}
    assert cloud.requests == []


def test_the_child_gets_the_declared_keys_and_no_inherited_provider_key() -> None:
    inherited = {"PATH": "/usr/bin", "OPENROUTER_API_KEY": "inherited", "ANTHROPIC_API_KEY": "old"}

    env = child_environment_with_provider_keys(inherited, {"ANTHROPIC_API_KEY": _FAKE_ANTHROPIC})

    assert env == {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": _FAKE_ANTHROPIC}
