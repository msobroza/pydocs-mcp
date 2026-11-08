"""Smoke step of every bench VM image build: a broken image never gets a tag.

Cloud Build (``infra/bench-vm/cloudbuild.yaml``) runs this inside the freshly
built image, before the push; ``build-local.sh`` runs it the same way. It runs
every check, prints one JSON line per check, and exits non-zero if any failed.

    python /opt/pydocs-mcp/infra/bench-vm/docker/smoke.py --flavour cpu --commit <sha>

Cloud Build has no GPU, so the CUDA checks stop at what needs no device: the
three CUDA majors agree, onnxruntime was built with its CUDA provider, and that
provider's shared-library dependencies all resolve without
``run_eval_gpu.sh`` (only the driver's ``libcuda.so.1``, which the NVIDIA
container toolkit mounts at run time, may be missing). The in-VM pre-flight
checks the device path.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from image_deps import CUDA_SWAPS, ImageFlavour, nvidia_library_dirs

# The repository's fixed in-image path (Dockerfile.cpu / Dockerfile.cuda).
REPO_DIR = Path("/opt/pydocs-mcp")
# The pthread OpenBLAS the CI benchmark job preloads (.github/workflows/benchmark.yml).
OPENBLAS_LIBRARY = "/usr/lib/x86_64-linux-gnu/libopenblas.so.0"
FLAVOUR_ENV_VAR = "BENCH_IMAGE_FLAVOUR"
NVIDIA_LD_CONF = Path("/etc/ld.so.conf.d/bench-nvidia-wheels.conf")
# Every CLI the run kinds and the supervisor drive must start, the supervisor
# included: it is the container's entrypoint (infra/bench-vm/host/startup.sh).
CLI_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("pydocs-mcp", "--help"),
    (sys.executable, "-m", "pydocs_eval.runner", "--help"),
    (sys.executable, "-m", "pydocs_eval.bench_vm", "supervise", "--help"),
    ("claude", "--version"),
    ("gcloud", "--version"),
    ("git", "--version"),
)
# Distributions that would put CUDA libraries in the CPU image.
_CUDA_DISTRIBUTION = re.compile(r"^(torch|triton|nvidia-.*|cuda-.*|onnxruntime-gpu|fastembed-gpu)$")
# Supplied by the host's NVIDIA driver at container start, never by the image.
_DRIVER_LIBRARIES = frozenset({"libcuda.so.1"})

Check = Callable[[], str]


class SmokeFailure(Exception):
    """One smoke check failed; the message names what and why."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def check_commit(expected: str) -> Check:
    def run() -> str:
        from pydocs_eval.sweep_support import COMMIT_SHA_ENV_VAR

        actual = os.environ.get(COMMIT_SHA_ENV_VAR, "")
        _require(
            actual == expected,
            f"{COMMIT_SHA_ENV_VAR}={actual!r}, expected the build's commit {expected!r}",
        )
        return f"{COMMIT_SHA_ENV_VAR}={actual}"

    return run


def check_flavour(expected: ImageFlavour) -> Check:
    def run() -> str:
        actual = os.environ.get(FLAVOUR_ENV_VAR, "")
        _require(actual == expected, f"{FLAVOUR_ENV_VAR}={actual!r}, expected {expected!r}")
        return f"{FLAVOUR_ENV_VAR}={actual}"

    return run


def check_repository() -> str:
    _require(Path.cwd() == REPO_DIR, f"working directory {Path.cwd()}, expected {REPO_DIR}")
    for relative in ("pyproject.toml", "uv.lock", "benchmarks/configs", "benchmarks/fixtures"):
        _require((REPO_DIR / relative).exists(), f"{REPO_DIR / relative} missing from the image")
    return str(REPO_DIR)


def check_openblas_preloaded() -> str:
    preload = os.environ.get("LD_PRELOAD", "").split(":")
    _require(OPENBLAS_LIBRARY in preload, f"LD_PRELOAD={preload!r} lacks {OPENBLAS_LIBRARY}")
    real = os.path.realpath(OPENBLAS_LIBRARY)
    _require(
        "openblas-pthread" in real, f"{OPENBLAS_LIBRARY} -> {real}, expected the pthread variant"
    )
    _require(real in Path("/proc/self/maps").read_text(), f"{real} is not mapped into this process")
    # The global namespace resolves cblas_sgemm only because of the preload.
    _require(hasattr(ctypes.CDLL(None), "cblas_sgemm"), "cblas_sgemm does not resolve globally")
    return f"{OPENBLAS_LIBRARY} -> {real}"


def check_imports() -> str:
    native = importlib.import_module("pydocs_mcp._native")
    turbovec = importlib.import_module("turbovec._turbovec")
    importlib.import_module("pydocs_mcp.storage.turboquant_uow")
    importlib.import_module("pydocs_eval.runner")
    return f"{Path(native.__file__).name}, {Path(turbovec.__file__).name}"


def check_clis() -> str:
    for argv in CLI_COMMANDS:
        completed = subprocess.run(  # noqa: S603 — CLI_COMMANDS is a module constant
            argv, capture_output=True, text=True, timeout=120, check=False
        )
        _require(
            completed.returncode == 0,
            f"`{' '.join(argv)}` exited {completed.returncode}: {completed.stderr.strip()[-500:]}",
        )
    return f"{len(CLI_COMMANDS)} CLIs start"


def _installed_distributions() -> set[str]:
    return {dist.metadata["Name"].lower() for dist in importlib.metadata.distributions()}


def check_no_cuda_libraries() -> str:
    cuda_dists = sorted(d for d in _installed_distributions() if _CUDA_DISTRIBUTION.match(d))
    _require(not cuda_dists, f"CUDA distributions in the CPU image: {cuda_dists}")
    site = Path(importlib.import_module("pydocs_mcp").__file__).parents[1]
    cuda_files = sorted(str(p) for p in site.rglob("libcu*.so*")) + sorted(
        str(p) for p in site.rglob("libcudnn*.so*")
    )
    _require(not cuda_files, f"CUDA libraries in the CPU image: {cuda_files[:5]}")
    return "no torch, nvidia-*, cuda-* or libcu* in site-packages"


def check_cuda_swaps() -> str:
    installed = _installed_distributions()
    for cpu_build, gpu_build in CUDA_SWAPS.items():
        _require(gpu_build in installed, f"{gpu_build} is not installed")
        _require(cpu_build not in installed, f"{cpu_build} (CPU build) is still installed")
    return ", ".join(CUDA_SWAPS.values())


def _major(version: str | None) -> str:
    return (version or "").split(".")[0]


def check_cuda_majors() -> str:
    import onnxruntime
    import torch

    base = os.environ.get("CUDA_VERSION", "")
    ort_info = importlib.import_module("onnxruntime.capi.build_and_package_info")
    ort_cuda = getattr(ort_info, "cuda_version", "")
    majors = {
        "base image": _major(base),
        "torch": _major(torch.version.cuda),
        "onnxruntime": _major(ort_cuda),
    }
    _require(
        len(set(majors.values())) == 1 and "" not in majors.values(),
        f"CUDA majors disagree: {majors}",
    )
    providers = onnxruntime.get_available_providers()
    _require("CUDAExecutionProvider" in providers, f"onnxruntime providers {providers} lack CUDA")
    return (
        f"CUDA {majors['torch']} (base {base}, torch {torch.version.cuda}, onnxruntime {ort_cuda})"
    )


def _unresolved_libraries(library: Path) -> Iterator[str]:
    ldd = subprocess.run(  # noqa: S603
        ["ldd", str(library)],  # noqa: S607 — the image's own glibc ldd, on PATH
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    for line in ldd.stdout.splitlines():
        if "not found" in line:
            yield line.split()[0]


def check_cuda_library_path() -> str:
    _require(
        NVIDIA_LD_CONF.is_file(), f"{NVIDIA_LD_CONF} missing: the library-path fix was not baked in"
    )
    baked = NVIDIA_LD_CONF.read_text().split()
    _require(
        baked == nvidia_library_dirs(),
        f"{NVIDIA_LD_CONF} lists {baked}, expected {nvidia_library_dirs()}",
    )
    capi = Path(importlib.import_module("onnxruntime").__file__).parent / "capi"
    provider = capi / "libonnxruntime_providers_cuda.so"
    _require(provider.is_file(), f"{provider} missing: onnxruntime was not built with CUDA")
    missing = sorted(set(_unresolved_libraries(provider)) - _DRIVER_LIBRARIES)
    _require(not missing, f"{provider.name} cannot resolve {missing} without run_eval_gpu.sh")
    return f"{provider.name} resolves via {NVIDIA_LD_CONF.name} ({len(baked)} dirs)"


def build_checks(flavour: ImageFlavour, commit: str) -> dict[str, Check]:
    checks: dict[str, Check] = {
        "commit": check_commit(commit),
        "flavour": check_flavour(flavour),
        "repository": check_repository,
        "openblas_preloaded": check_openblas_preloaded,
        "imports": check_imports,
        "clis": check_clis,
    }
    if flavour is ImageFlavour.CPU:
        checks["no_cuda_libraries"] = check_no_cuda_libraries
    else:
        checks["cuda_swaps"] = check_cuda_swaps
        checks["cuda_majors"] = check_cuda_majors
        checks["cuda_library_path"] = check_cuda_library_path
    return checks


def run_checks(checks: dict[str, Check]) -> bool:
    # Every failure is reported; none aborts the remaining checks.
    passed = True
    for name, check in checks.items():
        try:
            record = {"check": name, "ok": True, "detail": check()}
        except Exception as error:
            passed = False
            record = {"check": name, "ok": False, "detail": f"{type(error).__name__}: {error}"}
        print(json.dumps(record), flush=True)
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a bench VM image from inside it.")
    parser.add_argument("--flavour", required=True, choices=[f.value for f in ImageFlavour])
    parser.add_argument(
        "--commit", required=True, help="the 40-hex commit the image was built from"
    )
    args = parser.parse_args(argv)
    passed = run_checks(build_checks(ImageFlavour(args.flavour), args.commit))
    print(
        json.dumps({"smoke": "passed" if passed else "failed", "flavour": args.flavour}), flush=True
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
