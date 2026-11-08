"""Run ids: determinism over explicit fields, and the stem rule's 63-character bound."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import Field, StrictInt

from pydocs_eval.bench_vm import RunSpec, RunSpecError, derive_run_id, load_run_spec
from pydocs_eval.bench_vm.run_id import (
    GCE_NAME_MAX_CHARS,
    MAX_ATTEMPT,
    MAX_STEM_CHARS,
    bench_vm_instance_name,
)

from ._spec_dicts import before_after_spec, retrieval_spec, write_spec

_SPEC_TEXT = """\
# A smoke run.
git_ref: main
kind: retrieval
configs: [benchmarks/configs/baseline.yaml]
max_runtime: 1h
"""
_SAME_SPEC_REWRITTEN = """\
max_runtime:   60m      # same hour, other spelling

configs:
  - benchmarks/configs/baseline.yaml
kind: "retrieval"
git_ref: 'main'
"""


def _run_id(directory: Path, text: str, stem: str = "smoke") -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.yaml"
    path.write_text(text, encoding="utf-8")
    return load_run_spec(path).run_id


def test_the_run_id_is_the_stem_plus_8_hex_of_the_set_fields_canonical_json(tmp_path: Path) -> None:
    run_id = _run_id(tmp_path, _SPEC_TEXT)

    # The arm_identity idiom: sorted keys, compact separators, sha256 hex.
    explicit = {
        "git_ref": "main",
        "kind": "retrieval",
        "configs": ["benchmarks/configs/baseline.yaml"],
        "max_runtime": "PT1H",
    }
    canonical = json.dumps(explicit, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert run_id == f"smoke-{hashlib.sha256(canonical.encode()).hexdigest()[:8]}"


def test_comments_key_order_whitespace_and_spelling_do_not_move_the_id(tmp_path: Path) -> None:
    assert _run_id(tmp_path / "a", _SPEC_TEXT) == _run_id(tmp_path / "b", _SAME_SPEC_REWRITTEN)


def test_the_same_spec_always_gets_the_same_id(tmp_path: Path) -> None:
    assert _run_id(tmp_path / "a", _SPEC_TEXT) == _run_id(tmp_path / "b", _SPEC_TEXT)


@pytest.mark.parametrize(
    ("make_spec", "field", "value"),
    [
        (retrieval_spec, "git_ref", "v0.6.0"),
        (retrieval_spec, "dataset", "ds1000"),
        (retrieval_spec, "split", "small_test"),
        (retrieval_spec, "configs", ["benchmarks/configs/dense.yaml"]),
        (retrieval_spec, "corpus_dir", "benchmarks/tests/fixtures/bug_loc_corpus"),
        (retrieval_spec, "args", ["--limit", "3"]),
        (retrieval_spec, "machine_type", "n2-standard-16"),
        (retrieval_spec, "gpu", {"count": 1}),
        (retrieval_spec, "spot", False),
        (retrieval_spec, "zone", "europe-west4-b"),
        (retrieval_spec, "disk_gb", 100),
        (retrieval_spec, "max_runtime", "2h"),
        (retrieval_spec, "self_delete", False),
        (retrieval_spec, "sync_interval", "10m"),
        (before_after_spec, "baseline_ref", "v0.5.0"),
        (before_after_spec, "candidate_ref", "feature"),
        (before_after_spec, "max_usd", 6.5),
    ],
)
def test_every_field_value_moves_the_id(make_spec, field: str, value: object) -> None:
    base = make_spec()
    before = RunSpec.model_validate(base)
    after = RunSpec.model_validate({**base, field: value})

    assert derive_run_id("s", before) != derive_run_id("s", after)


def test_the_kind_itself_moves_the_id() -> None:
    common = {"git_ref": "main", "dataset": "repoqa"}
    retrieval = RunSpec.model_validate(retrieval_spec(**common))
    agent = RunSpec.model_validate(
        {**common, "kind": "agent_track", "confirm_spend": True, "max_usd": 1.0}
    )

    assert derive_run_id("s", retrieval) != derive_run_id("s", agent)


def test_a_changed_code_default_does_not_move_the_id() -> None:
    class _NewerToolingRunSpec(RunSpec):
        # A later tooling release that grows the default disk.
        disk_gb: StrictInt = Field(default=500, ge=10)

    spec_dict = retrieval_spec()
    today = RunSpec.model_validate(spec_dict)
    upgraded = _NewerToolingRunSpec.model_validate(spec_dict)

    assert today.disk_gb != upgraded.disk_gb
    assert derive_run_id("s", today) == derive_run_id("s", upgraded)


def test_stating_a_default_explicitly_is_a_different_spec() -> None:
    implicit = RunSpec.model_validate(retrieval_spec())
    explicit = RunSpec.model_validate(retrieval_spec(spot=True))

    assert implicit.spot == explicit.spot
    assert derive_run_id("s", implicit) != derive_run_id("s", explicit)


# ── The stem rule ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("stem", ["CPU-smoke", "cpu_smoke", "cpu.smoke", "smoke run"])
def test_a_stem_outside_lowercase_digits_and_hyphens_is_refused(tmp_path: Path, stem: str) -> None:
    with pytest.raises(RunSpecError) as caught:
        load_run_spec(write_spec(tmp_path, stem, retrieval_spec()))

    (error,) = caught.value.errors
    assert (error.field, error.value) == ("file stem", stem)
    assert "lowercase letters, digits and hyphens" in error.expected


def test_a_stem_one_past_the_limit_is_refused_naming_stem_and_limit(tmp_path: Path) -> None:
    stem = "a" * (MAX_STEM_CHARS + 1)

    with pytest.raises(RunSpecError) as caught:
        load_run_spec(write_spec(tmp_path, stem, retrieval_spec()))

    (error,) = caught.value.errors
    assert error.value == stem
    assert f"at most {MAX_STEM_CHARS} characters" in error.expected
    assert str(GCE_NAME_MAX_CHARS) in error.expected


def test_a_stem_at_the_limit_keeps_every_name_within_63_characters(tmp_path: Path) -> None:
    stem = "a" * MAX_STEM_CHARS

    run_id = load_run_spec(write_spec(tmp_path, stem, retrieval_spec())).run_id
    vm_name = bench_vm_instance_name(run_id, MAX_ATTEMPT)

    assert re.fullmatch(rf"{stem}-[0-9a-f]{{8}}", run_id)
    assert len(run_id) <= GCE_NAME_MAX_CHARS
    assert len(vm_name) == GCE_NAME_MAX_CHARS
    assert re.fullmatch(r"[a-z]([-a-z0-9]*[a-z0-9])?", vm_name)  # Compute Engine's name rule


def test_stem_errors_are_reported_with_field_errors(tmp_path: Path) -> None:
    with pytest.raises(RunSpecError) as caught:
        load_run_spec(write_spec(tmp_path, "Bad", retrieval_spec(spot="yes")))

    assert [error.field for error in caught.value.errors] == ["file stem", "spot"]


@pytest.mark.parametrize("attempt", [0, MAX_ATTEMPT + 1])
def test_an_attempt_outside_the_bound_is_refused(attempt: int) -> None:
    with pytest.raises(
        ValueError, match=rf"got {attempt}, expected an integer in 1\.\.{MAX_ATTEMPT}"
    ):
        bench_vm_instance_name("smoke-0123abcd", attempt)
