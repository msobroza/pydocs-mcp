"""The GPU pre-flight: a GPU run must not compute on the CPU for hours.

Before the child of a GPU run starts, torch must see CUDA and onnxruntime must
list the CUDA execution provider. ``gpu_preflight_failures`` is the pure check
over a ``GpuProbe``, with one message per gap naming the offending value and
what the image must provide (prior art: ``campaign.smoke``'s host probe).

``probe_gpu`` asks the image's interpreter in a child process rather than
importing torch here: a CUDA context created in the supervisor would hold GPU
memory for the whole run, and a broken CUDA install must not crash the
supervisor that has to record the run's ending.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CUDA_EXECUTION_PROVIDER = "CUDAExecutionProvider"
# Importing torch with CUDA takes seconds; a probe still silent after this is hung.
_DEFAULT_PROBE_TIMEOUT_S = 120.0
# Runs in the image's interpreter; prints one JSON object and never raises.
_PROBE_SCRIPT = """
import json
answer = {}
try:
    import torch
except Exception as exc:
    answer["torch_error"] = f"{type(exc).__name__}: {exc}"
else:
    answer["torch_version"] = str(torch.__version__)
    answer["torch_sees_cuda"] = bool(torch.cuda.is_available())
try:
    import onnxruntime
except Exception as exc:
    answer["onnxruntime_error"] = f"{type(exc).__name__}: {exc}"
else:
    answer["onnxruntime_providers"] = [str(p) for p in onnxruntime.get_available_providers()]
print(json.dumps(answer))
"""


class GpuProbeError(RuntimeError):
    """The probe itself could not run or answered something unreadable."""


@dataclass(frozen=True, slots=True)
class GpuProbe:
    """What the image's interpreter reports about torch and onnxruntime.

    ``torch_version`` / ``onnxruntime_providers`` are ``None`` when the package
    does not import; ``*_error`` then carries the import error.
    """

    torch_version: str | None
    torch_sees_cuda: bool
    torch_error: str | None
    onnxruntime_providers: tuple[str, ...] | None
    onnxruntime_error: str | None


def gpu_preflight_failures(probe: GpuProbe) -> list[str]:
    """One message per missing piece (empty: the GPU run may start). Pure over ``probe``."""
    checks = (_check_torch(probe), _check_onnxruntime(probe))
    return [message for message in checks if message is not None]


def _check_torch(probe: GpuProbe) -> str | None:
    if probe.torch_version is None:
        return (
            f"torch does not import ({probe.torch_error}), expected the CUDA image's torch: "
            "without it this GPU run would compute on the CPU"
        )
    if probe.torch_sees_cuda:
        return None
    return (
        f"torch {probe.torch_version} sees no CUDA device (torch.cuda.is_available() is False), "
        "expected the GPU this run was given: check the NVIDIA driver and that the "
        "container was started with the GPU"
    )


def _check_onnxruntime(probe: GpuProbe) -> str | None:
    providers = probe.onnxruntime_providers
    if providers is None:
        return (
            f"onnxruntime does not import ({probe.onnxruntime_error}), expected onnxruntime-gpu "
            f"listing {CUDA_EXECUTION_PROVIDER!r}"
        )
    if CUDA_EXECUTION_PROVIDER in providers:
        return None
    return (
        f"onnxruntime lists the execution providers {list(providers)!r}, expected "
        f"{CUDA_EXECUTION_PROVIDER!r} among them: onnxruntime-gpu is missing or cannot load CUDA"
    )


def probe_gpu(
    python: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout_s: float = _DEFAULT_PROBE_TIMEOUT_S,
) -> GpuProbe:
    """Ask ``python`` (the image's interpreter) what torch and onnxruntime see."""
    argv = [*python, "-c", _PROBE_SCRIPT]
    try:
        completed = subprocess.run(  # argv built here, never a shell
            argv, capture_output=True, timeout=timeout_s, env=dict(env), check=False
        )
    except subprocess.TimeoutExpired:
        raise GpuProbeError(f"the GPU probe did not answer within {timeout_s:g}s") from None
    except OSError as exc:
        raise GpuProbeError(f"the GPU probe could not start {python[0]}: {exc}") from None
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GpuProbeError(f"the GPU probe exited {completed.returncode}: {stderr}")
    return _parsed_probe(completed.stdout)


def probe_image_gpu() -> GpuProbe:
    """The supervisor's default probe: the interpreter it runs on is the image's."""
    return probe_gpu((sys.executable,), env=os.environ)


def _parsed_probe(stdout: bytes) -> GpuProbe:
    try:
        answer = json.loads(stdout.decode("utf-8", errors="replace").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise GpuProbeError(
            f"the GPU probe answered {stdout[-200:]!r}, expected one JSON object ({exc})"
        ) from None
    providers = answer.get("onnxruntime_providers")
    return GpuProbe(
        torch_version=answer.get("torch_version"),
        torch_sees_cuda=bool(answer.get("torch_sees_cuda", False)),
        torch_error=answer.get("torch_error"),
        onnxruntime_providers=None if providers is None else tuple(providers),
        onnxruntime_error=answer.get("onnxruntime_error"),
    )
