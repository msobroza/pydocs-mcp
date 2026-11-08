"""Every run spec committed under ``infra/bench-vm/runs/`` loads, and the two smokes exist."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.bench_vm import GpuType, RunKind, load_run_spec

from ._spec_dicts import COMMITTED_RUNS_DIR, REPO_ROOT

_COMMITTED_SPECS = sorted(COMMITTED_RUNS_DIR.glob("*.yaml"))


def test_the_tree_ships_a_cpu_and_a_gpu_smoke() -> None:
    assert {path.stem for path in _COMMITTED_SPECS} >= {"cpu-smoke", "gpu-smoke"}


@pytest.mark.parametrize("path", _COMMITTED_SPECS, ids=lambda path: path.stem)
def test_every_committed_run_spec_loads(path: Path) -> None:
    loaded = load_run_spec(path)

    assert loaded.run_id.startswith(f"{path.stem}-")


@pytest.mark.parametrize("path", _COMMITTED_SPECS, ids=lambda path: path.stem)
def test_committed_repository_paths_exist_at_this_commit(path: Path) -> None:
    spec = load_run_spec(path).spec
    paths = [*spec.configs, *([spec.corpus_dir] if spec.corpus_dir else [])]

    missing = [p for p in paths if not (REPO_ROOT / p).exists()]

    assert not missing, f"{path.name} names paths missing from the repository: {missing}"


def test_the_cpu_smoke_is_a_cpu_retrieval_run_over_the_bundled_repoqa_fixture() -> None:
    spec = load_run_spec(COMMITTED_RUNS_DIR / "cpu-smoke.yaml").spec

    assert (spec.kind, spec.gpu, spec.dataset) == (RunKind.RETRIEVAL, None, "repoqa")
    fixture = spec.args[spec.args.index("--fixture") + 1]
    assert (REPO_ROOT / fixture).is_file()


def test_the_gpu_smoke_asks_for_one_l4() -> None:
    spec = load_run_spec(COMMITTED_RUNS_DIR / "gpu-smoke.yaml").spec

    assert spec.gpu is not None
    assert (spec.gpu.type, spec.gpu.count) == (GpuType.NVIDIA_L4, 1)
    assert spec.resolved_machine_type == "g2-standard-8"
