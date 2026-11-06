"""The bench VM image recipes and their Cloud Build config, checked offline.

The images check themselves in their build's smoke step; these tests pin
what can be checked without a container runtime or network:

- the dependency helper's parsing and the flavour-aware ``uv pip check``;
- the committed eval lock and CUDA swaps agree with ``uv.lock``;
- the CPU flavour's locked closure holds no CUDA package, the CUDA one does,
  and the CUDA base image, the locked torch and the swaps share one major;
- both recipes are multi-stage with no Rust toolchain in the final stage,
  share their pins, and set the paths and variables the smoke step and the
  run tags read;
- the Cloud Build config takes exactly the three inputs, smokes before it
  pushes, and pushes only the commit's own tag.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from pydocs_eval.bench_vm.container_paths import REPO_DIR
from pydocs_eval.sweep_support import COMMIT_SHA_ENV_VAR

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BENCH_VM_DIR = _REPO_ROOT / "infra" / "bench-vm"
_DOCKER_DIR = _BENCH_VM_DIR / "docker"
_FLAVOURS = ("cpu", "cuda")


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _DOCKER_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # smoke.py imports image_deps by its top-level name, as it does in the image.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


image_deps = _load("image_deps")
smoke = _load("smoke")


# ---- uv.lock helpers -------------------------------------------------------


def _lock_packages() -> dict[str, list[dict]]:
    lock = tomllib.loads((_REPO_ROOT / "uv.lock").read_text())
    packages: dict[str, list[dict]] = {}
    for package in lock["package"]:
        packages.setdefault(image_deps.normalize_name(package["name"]), []).append(package)
    return packages


def _locked_version(name: str) -> str:
    entries = _lock_packages()[image_deps.normalize_name(name)]
    assert len(entries) == 1, f"{name} has {len(entries)} versions in uv.lock"
    return entries[0]["version"]


def _locked_closure(extras: tuple[str, ...]) -> set[str]:
    """Every package the product + ``extras`` can pull in, markers ignored.

    Ignoring markers over-approximates the Linux closure, so a package absent
    here is absent on every platform.
    """
    packages = _lock_packages()
    (product,) = packages["pydocs-mcp"]
    todo = list(product["dependencies"])
    for extra in extras:
        todo += product["optional-dependencies"][extra]
    seen: set[str] = set()
    while todo:
        dependency = todo.pop()
        name = image_deps.normalize_name(dependency["name"])
        for package in packages[name]:
            for extra in dependency.get("extra", []):
                todo += package.get("optional-dependencies", {}).get(extra, [])
            if name not in seen:
                todo += package.get("dependencies", [])
        seen.add(name)
    return seen


def _is_cuda_package(name: str) -> bool:
    return bool(re.match(r"^(torch|triton|nvidia-.*|cuda-.*)$", name))


# ---- image_deps: parsing and checks ----------------------------------------

_EXPORT = """\
# comment line
fastembed==0.8.0 \\
    --hash=sha256:aaa \\
    --hash=sha256:bbb
Onnx_Runtime==1.26.0 ; sys_platform == 'linux' \\
    --hash=sha256:ccc
onnxruntime-gpu @ https://example.invalid/ort.whl \\
    --hash=sha256:ddd
"""


def test_split_requirement_blocks_keeps_hash_lines_and_normalizes_names():
    blocks = image_deps.split_requirement_blocks(_EXPORT)

    assert sorted(blocks) == ["fastembed", "onnx-runtime", "onnxruntime-gpu"]
    assert blocks["fastembed"].count("--hash=") == 2
    assert image_deps.pinned_version(blocks["onnx-runtime"]) == "1.26.0"
    assert image_deps.pinned_version(blocks["onnxruntime-gpu"]) == ""


def test_without_names_drops_by_normalized_name():
    blocks = image_deps.split_requirement_blocks(_EXPORT)

    kept = image_deps.without_names(blocks, ["FastEmbed", "onnx.runtime"])

    assert sorted(kept) == ["onnxruntime-gpu"]


_MISSING_FASTEMBED = (
    "The package `pydocs-mcp` requires `fastembed>=0.4,<1.0`, but it's not installed"
)
_MISSING_RICH = "The package `pydocs-mcp-eval` requires `rich>=13.0`, but it's not installed"


def test_check_accepts_the_fastembed_gap_only_in_the_cuda_flavour():
    cuda = image_deps.ImageFlavour.CUDA
    cpu = image_deps.ImageFlavour.CPU

    assert image_deps.unexpected_check_failures(_MISSING_FASTEMBED, cuda) == []
    assert image_deps.unexpected_check_failures(_MISSING_FASTEMBED, cpu) == [_MISSING_FASTEMBED]
    assert image_deps.unexpected_check_failures(_MISSING_RICH, cuda) == [_MISSING_RICH]


# ---- committed locks agree with uv.lock ------------------------------------


def test_eval_lock_pins_every_eval_requirement_and_never_the_product():
    pinned = image_deps.split_requirement_blocks(image_deps.EVAL_LOCK_FILE.read_text())
    wanted = image_deps.eval_requirements(_REPO_ROOT / "benchmarks" / "pyproject.toml")

    for requirement in wanted:
        name = image_deps.normalize_name(re.split(r"[\s<>=!~\[;]", requirement, maxsplit=1)[0])
        assert name in pinned, f"{name} is not in eval-requirements.txt: re-run lock-eval"
    assert "pydocs-mcp" not in pinned
    assert all("--hash=sha256:" in block for block in pinned.values())


def test_eval_lock_keeps_the_uv_lock_version_of_every_shared_package():
    locked = _lock_packages()
    pinned = image_deps.split_requirement_blocks(image_deps.EVAL_LOCK_FILE.read_text())

    stale = {
        name: (image_deps.pinned_version(block), [p["version"] for p in locked[name]])
        for name, block in pinned.items()
        if name in locked
        and image_deps.pinned_version(block) not in {p["version"] for p in locked[name]}
    }

    assert stale == {}, (
        f"eval-requirements.txt is stale against uv.lock: re-run lock-eval ({stale})"
    )


def test_cuda_swaps_pin_the_locked_versions_of_the_builds_they_replace():
    swaps = image_deps.split_requirement_blocks(image_deps.CUDA_SWAPS_FILE.read_text())

    assert sorted(swaps) == sorted(image_deps.CUDA_SWAPS.values())
    assert image_deps.pinned_version(swaps["fastembed-gpu"]) == _locked_version("fastembed")
    assert f"onnxruntime_gpu-{_locked_version('onnxruntime')}-" in swaps["onnxruntime-gpu"]
    assert all("--hash=sha256:" in block for block in swaps.values())


def test_build_constraints_pin_both_build_backends_with_hashes():
    pinned = image_deps.split_requirement_blocks(image_deps.BUILD_CONSTRAINTS_FILE.read_text())

    assert sorted(pinned) == ["maturin", "setuptools"]
    assert all(image_deps.pinned_version(b) and "--hash=sha256:" in b for b in pinned.values())


# ---- flavour closures and the one CUDA major --------------------------------


def test_cpu_flavour_closure_holds_no_cuda_package():
    closure = _locked_closure(image_deps.FLAVOUR_EXTRAS[image_deps.ImageFlavour.CPU])

    assert sorted(n for n in closure if _is_cuda_package(n)) == []
    assert {"turbovec", "fastembed", "onnxruntime"} <= closure


def test_cuda_flavour_closure_carries_torch_and_its_nvidia_wheels():
    closure = _locked_closure(image_deps.FLAVOUR_EXTRAS[image_deps.ImageFlavour.CUDA])

    assert {"torch", "nvidia-cudnn-cu13", "nvidia-cublas"} <= closure


def test_cuda_base_image_torch_and_onnxruntime_gpu_share_one_cuda_major():
    base = re.search(r"nvidia/cuda:(\d+)\.", _dockerfile("cuda"))
    torch_cuda = _locked_version("cuda-toolkit")
    cudnn_suffix = {n for n in _lock_packages() if n.startswith("nvidia-cudnn-cu")}
    swaps = image_deps.CUDA_SWAPS_FILE.read_text()

    assert base is not None
    major = base.group(1)
    assert torch_cuda.split(".")[0] == major
    assert cudnn_suffix == {f"nvidia-cudnn-cu{major}"}
    assert f"onnxruntime-cuda-{major} package feed" in swaps.replace("\n# ", " ")


# ---- the two recipes --------------------------------------------------------


def _dockerfile(flavour: str) -> str:
    return (_DOCKER_DIR / f"Dockerfile.{flavour}").read_text()


def _stages(text: str) -> list[str]:
    return re.split(r"^(?=FROM )", text, flags=re.MULTILINE)[1:]


def _args(text: str) -> dict[str, str]:
    return dict(re.findall(r"^ARG (\w+)=(\S+)$", text, flags=re.MULTILINE))


@pytest.mark.parametrize("flavour", _FLAVOURS)
def test_recipe_builds_in_a_builder_stage_and_keeps_rust_out_of_the_final_one(flavour):
    stages = _stages(_dockerfile(flavour))
    final = stages[-1]

    assert any(s.startswith("FROM ${RUST_IMAGE} AS builder") for s in stages)
    assert final.startswith("FROM ${RUNTIME_IMAGE}\n")
    assert not re.search(r"cargo|rustc|rustup|RUST_IMAGE|--from=builder /usr/local", final)
    assert "COPY --from=builder /opt/venv /opt/venv" in final


@pytest.mark.parametrize("flavour", _FLAVOURS)
def test_recipe_sets_the_paths_and_variables_the_runs_read(flavour):
    final = _stages(_dockerfile(flavour))[-1]

    assert f"WORKDIR {smoke.REPO_DIR}" in final
    assert f"COPY . {smoke.REPO_DIR}" in final
    assert f"LD_PRELOAD={smoke.OPENBLAS_LIBRARY}" in final
    assert f"{smoke.FLAVOUR_ENV_VAR}={flavour}" in final
    assert f"ENV {COMMIT_SHA_ENV_VAR}=${{COMMIT}}" in final
    assert f"--flavour {flavour}" in _dockerfile(flavour)


def test_both_recipes_share_every_pin_but_the_runtime_base():
    cpu, cuda = _args(_dockerfile("cpu")), _args(_dockerfile("cuda"))

    assert {k: v for k, v in cpu.items() if k != "RUNTIME_IMAGE"} == {
        k: v for k, v in cuda.items() if k != "RUNTIME_IMAGE"
    }
    images = [v for k, v in cpu.items() if k.endswith("_IMAGE")] + [cuda["RUNTIME_IMAGE"]]
    assert len(images) == 4
    for image in images:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"{image} is not pinned by digest"


def test_cuda_recipe_bakes_in_the_nvidia_library_path():
    final = _stages(_dockerfile("cuda"))[-1]

    assert f"--output {smoke.NVIDIA_LD_CONF}" in final
    assert "ldconfig" in final
    assert "nvidia-ld-conf" not in _stages(_dockerfile("cpu"))[-1]


# ---- the Cloud Build config -------------------------------------------------


def _cloudbuild() -> dict:
    return yaml.safe_load((_BENCH_VM_DIR / "cloudbuild.yaml").read_text())


_TAG = "${_REPOSITORY}/bench-${_FLAVOUR}:${_COMMIT}"


def test_cloudbuild_takes_the_commit_the_flavour_and_the_repository():
    assert sorted(_cloudbuild()["substitutions"]) == ["_COMMIT", "_FLAVOUR", "_REPOSITORY"]


def test_cloudbuild_pushes_only_the_commit_tag_and_only_through_images():
    config = _cloudbuild()

    assert config["images"] == [_TAG]
    for step in config["steps"]:
        assert "push" not in step.get("args", []), f"step {step['id']} pushes"
        assert "docker push" not in step.get("script", "")


def test_cloudbuild_builds_from_the_archive_and_smokes_last():
    steps = {step["id"]: step for step in _cloudbuild()["steps"]}
    order = list(steps)

    assert order == ["check-inputs", "unpack-commit-archive", "build", "smoke"]
    assert "git get-tar-commit-id" in steps["unpack-commit-archive"]["script"]
    assert "--file=repo/infra/bench-vm/docker/Dockerfile.${_FLAVOUR}" in steps["build"]["args"]
    assert steps["build"]["args"][-1] == "repo"
    assert _TAG in steps["smoke"]["args"]
    assert f"{smoke.REPO_DIR}/infra/bench-vm/docker/smoke.py" in steps["smoke"]["args"]


def test_cloudbuild_refuses_any_region_but_europe_west4():
    script = _cloudbuild()["steps"][0]["script"]

    assert "BUILD_LOCATION == europe-west4" in script
    assert "^europe-west4-docker\\.pkg\\.dev/" in script


# ---- the smoke step's CLIs ---------------------------------------------------


_SUPERVISOR_HELP = (sys.executable, "-m", "pydocs_eval.bench_vm", "supervise", "--help")


def test_the_smoke_step_starts_the_supervisor_the_container_runs():
    assert _SUPERVISOR_HELP in smoke.CLI_COMMANDS


def test_the_supervisor_help_the_smoke_step_runs_starts_without_a_vm():
    completed = subprocess.run(
        _SUPERVISOR_HELP,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )

    assert completed.returncode == 0, completed.stderr
    assert "--state-dir" in completed.stdout


def test_the_smoke_steps_repository_is_the_supervisors_fixed_repository():
    assert smoke.REPO_DIR == REPO_DIR
