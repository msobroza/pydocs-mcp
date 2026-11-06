"""Run-spec loading: strictness, defaults, and the kind table's refusals."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from pydocs_eval.bench_vm import RUN_KIND_TABLE, GpuType, RunKind, RunSpecError, load_run_spec
from pydocs_eval.bench_vm.run_spec import (
    DEFAULT_CPU_MACHINE_TYPE,
    DEFAULT_GPU_MACHINE_TYPE,
    FieldError,
)

from ._spec_dicts import agent_track_spec, before_after_spec, retrieval_spec, write_spec


def _refusals(tmp_path: Path, spec: dict[str, object]) -> tuple[FieldError, ...]:
    with pytest.raises(RunSpecError) as caught:
        load_run_spec(write_spec(tmp_path, "spec", spec))
    return caught.value.errors


def _only_refusal(tmp_path: Path, spec: dict[str, object]) -> FieldError:
    errors = _refusals(tmp_path, spec)
    assert len(errors) == 1, errors
    return errors[0]


# ── Defaults ────────────────────────────────────────────────────────────────


def test_defaults_of_a_minimal_cpu_spec(tmp_path: Path) -> None:
    spec = load_run_spec(write_spec(tmp_path, "minimal", retrieval_spec())).spec

    assert spec.gpu is None
    assert spec.resolved_machine_type == DEFAULT_CPU_MACHINE_TYPE == "n2-standard-8"
    assert spec.spot is True
    assert spec.zone is None
    assert spec.disk_gb == 250
    assert spec.max_runtime == timedelta(hours=6)
    assert spec.self_delete is True
    assert spec.confirm_spend is False
    assert spec.max_usd is None
    assert spec.sync_interval == timedelta(minutes=5)
    assert spec.args == ()


def test_an_empty_gpu_request_is_one_l4_on_a_g2(tmp_path: Path) -> None:
    spec = load_run_spec(write_spec(tmp_path, "gpu", retrieval_spec(gpu={}))).spec

    assert spec.gpu is not None
    assert (spec.gpu.type, spec.gpu.count) == (GpuType.NVIDIA_L4, 1)
    assert spec.resolved_machine_type == DEFAULT_GPU_MACHINE_TYPE == "g2-standard-8"


def test_an_explicit_machine_type_wins_over_the_gpu_default(tmp_path: Path) -> None:
    spec_dict = retrieval_spec(gpu={"count": 2}, machine_type="g2-standard-24")
    spec = load_run_spec(write_spec(tmp_path, "gpu", spec_dict)).spec

    assert spec.resolved_machine_type == "g2-standard-24"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("6h", timedelta(hours=6)),
        ("90m", timedelta(minutes=90)),
        ("1h30m", timedelta(minutes=90)),
        ("2d", timedelta(days=2)),
        ("45s", timedelta(seconds=45)),
    ],
)
def test_durations_take_unit_suffixes(tmp_path: Path, text: str, expected: timedelta) -> None:
    spec_dict = retrieval_spec(max_runtime="7d", sync_interval=text)
    spec = load_run_spec(write_spec(tmp_path, "dur", spec_dict)).spec

    assert spec.sync_interval == expected


# ── Strictness: every field error names the field, the value and the shape ──


@pytest.mark.parametrize(
    ("overrides", "field", "value", "expected_fragment"),
    [
        ({"max_runtme": "1h"}, "max_runtme", "1h", "max_runtime"),
        ({"gpu": {"kind": "l4"}}, "gpu.kind", "l4", "type, count"),
        ({"kind": "notebook"}, "kind", "notebook", "'retrieval', 'agent_track' or 'before_after'"),
        ({"gpu": {"type": "nvidia-a100"}}, "gpu.type", "nvidia-a100", "'nvidia-l4'"),
        ({"gpu": {"count": 0}}, "gpu.count", 0, "greater than or equal to 1"),
        ({"spot": "yes"}, "spot", "yes", "a valid boolean"),
        ({"disk_gb": "250"}, "disk_gb", "250", "a valid integer"),
        ({"disk_gb": 5}, "disk_gb", 5, "greater than or equal to 10"),
        ({"max_runtime": 6}, "max_runtime", 6, "such as 6h"),
        ({"max_runtime": "6 hours"}, "max_runtime", "6 hours", "such as 6h"),
        ({"sync_interval": "0m"}, "sync_interval", "0m", "a positive duration"),
        ({"zone": "us-central1-a"}, "zone", "us-central1-a", "europe-west4"),
        ({"git_ref": ""}, "git_ref", "", "at least 1 character"),
        ({"configs": ["/abs/config.yaml"]}, "configs.0", "/abs/config.yaml", "repository-relative"),
        ({"corpus_dir": "../outside"}, "corpus_dir", "../outside", "repository-relative"),
        ({"args": ["--limit", 5]}, "args.1", 5, "a valid string"),
        (
            {"configs": "benchmarks/configs/baseline.yaml"},
            "configs",
            "benchmarks/configs/baseline.yaml",
            "a valid tuple",
        ),
    ],
)
def test_a_bad_field_is_refused_naming_field_value_and_shape(
    tmp_path: Path, overrides: dict[str, object], field: str, value: object, expected_fragment: str
) -> None:
    error = _only_refusal(tmp_path, retrieval_spec(**overrides))

    assert (error.field, error.value) == (field, value)
    assert expected_fragment in error.expected
    assert repr(value) in error.render()


def test_missing_required_fields_are_named(tmp_path: Path) -> None:
    errors = _refusals(tmp_path, {"dataset": "repoqa"})

    assert {error.field for error in errors} == {"git_ref", "kind"}
    assert all("required" in error.expected for error in errors)


def test_every_error_is_reported_at_once(tmp_path: Path) -> None:
    errors = _refusals(tmp_path, retrieval_spec(spot="yes", disk_gb="big", typo=1))

    assert {error.field for error in errors} == {"spot", "disk_gb", "typo"}


def test_a_duplicated_key_is_refused_with_its_line(tmp_path: Path) -> None:
    path = tmp_path / "dup.yaml"
    path.write_text("git_ref: main\nkind: retrieval\nconfigs: [c.yaml]\nspot: true\nspot: false\n")

    with pytest.raises(RunSpecError) as caught:
        load_run_spec(path)

    (error,) = caught.value.errors
    assert error.field == "(yaml)"
    assert "duplicate key 'spot' at line 5" in str(error.value)


def test_a_non_mapping_document_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- git_ref\n")

    with pytest.raises(RunSpecError, match="a mapping of run-spec fields"):
        load_run_spec(path)


# ── Kind-table refusals ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "token", ["--run-id", "--results-dir=/tmp/x", "--datas", "--gpu", "--bench-cache"]
)
def test_args_naming_an_owned_flag_are_refused(tmp_path: Path, token: str) -> None:
    error = _only_refusal(tmp_path, retrieval_spec(args=["--limit", "5", token]))

    assert (error.field, error.value) == ("args", token)
    assert "--results-dir" in error.expected  # the row's owned flags are listed


def test_args_naming_flags_the_row_does_not_own_pass_through(tmp_path: Path) -> None:
    args = [
        "--fixture",
        "f.json",
        "--limit",
        "5",
        "--dataset-full-prompt",
        "--bench-cache-cleanup",
        "on",
    ]

    spec = load_run_spec(write_spec(tmp_path, "pass", retrieval_spec(args=args))).spec

    assert spec.args == tuple(args)


def test_args_are_checked_against_the_kinds_own_row(tmp_path: Path) -> None:
    error = _only_refusal(tmp_path, agent_track_spec(args=["--max-usd", "100"]))

    assert (error.field, error.value) == ("args", "--max-usd")
    assert load_run_spec(write_spec(tmp_path, "ok", retrieval_spec(args=["--metrics", "mrr"])))


@pytest.mark.parametrize("make_spec", [agent_track_spec, before_after_spec])
def test_a_gpu_on_a_cpu_only_kind_is_refused(tmp_path: Path, make_spec) -> None:
    error = _only_refusal(tmp_path, make_spec(gpu={"count": 1}))

    assert error.field == "gpu"
    assert "CPU only" in error.expected


@pytest.mark.parametrize("make_spec", [agent_track_spec, before_after_spec])
@pytest.mark.parametrize(
    ("drop", "set_", "refused_field"),
    [
        ("confirm_spend", {}, "confirm_spend"),
        ("max_usd", {}, "max_usd"),
        (None, {"confirm_spend": False}, "confirm_spend"),
    ],
)
def test_a_paid_kind_needs_confirm_spend_and_max_usd(
    tmp_path: Path, make_spec, drop: str | None, set_: dict[str, object], refused_field: str
) -> None:
    spec = make_spec(**set_)
    if drop:
        del spec[drop]

    error = _only_refusal(tmp_path, spec)

    assert error.field == refused_field
    assert "spends model tokens" in error.expected or "is paid" in error.expected


@pytest.mark.parametrize("field", ["confirm_spend", "max_usd"])
def test_spend_guards_on_an_unpaid_kind_are_refused(tmp_path: Path, field: str) -> None:
    value = True if field == "confirm_spend" else 10.0

    error = _only_refusal(tmp_path, retrieval_spec(**{field: value}))

    assert error.field == field
    assert "spends no model tokens" in error.expected


@pytest.mark.parametrize(
    "configs",
    [[], ["a.yaml", "b.yaml"]],
)
def test_before_after_needs_exactly_one_config(tmp_path: Path, configs: list[str]) -> None:
    error = _only_refusal(tmp_path, before_after_spec(configs=configs))

    assert error.field == "configs"


@pytest.mark.parametrize("ref", ["baseline_ref", "candidate_ref"])
def test_before_after_needs_both_refs(tmp_path: Path, ref: str) -> None:
    spec = before_after_spec()
    del spec[ref]

    error = _only_refusal(tmp_path, spec)

    assert (error.field, error.value) == (ref, None)
    assert "requires" in error.expected


def test_retrieval_needs_a_config(tmp_path: Path) -> None:
    spec = retrieval_spec()
    del spec["configs"]

    assert _only_refusal(tmp_path, spec).field == "configs"


@pytest.mark.parametrize(
    ("make_spec", "field", "value"),
    [
        (agent_track_spec, "corpus_dir", "benchmarks/tests/fixtures/ds1000_mini.json"),
        (agent_track_spec, "configs", ["a.yaml"]),
        (retrieval_spec, "baseline_ref", "v1"),
        (before_after_spec, "corpus_dir", "x"),
    ],
)
def test_an_input_the_kind_does_not_take_is_refused(
    tmp_path: Path, make_spec, field: str, value: object
) -> None:
    error = _only_refusal(tmp_path, make_spec(**{field: value}))

    assert error.field == field
    assert "which takes" in error.expected


def test_sync_interval_must_be_shorter_than_max_runtime(tmp_path: Path) -> None:
    error = _only_refusal(tmp_path, retrieval_spec(max_runtime="1h", sync_interval="1h"))

    assert error.field == "sync_interval"


def test_valid_specs_of_every_kind_load(tmp_path: Path) -> None:
    for make_spec in (retrieval_spec, agent_track_spec, before_after_spec):
        spec = load_run_spec(write_spec(tmp_path, "ok", make_spec())).spec
        assert spec.row is RUN_KIND_TABLE[spec.kind]


# ── The kind table ──────────────────────────────────────────────────────────


def test_run_kind_is_an_upper_snake_str_enum_with_one_row_per_member() -> None:
    assert [member.name for member in RunKind] == ["RETRIEVAL", "AGENT_TRACK", "BEFORE_AFTER"]
    assert set(RUN_KIND_TABLE) == set(RunKind)


def test_the_paid_kinds_are_agent_track_and_before_after() -> None:
    paid = {kind for kind, row in RUN_KIND_TABLE.items() if row.paid}

    assert paid == {RunKind.AGENT_TRACK, RunKind.BEFORE_AFTER}
    assert all(RUN_KIND_TABLE[kind].provider_keys for kind in paid)
