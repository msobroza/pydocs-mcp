"""The GPU pre-flight: a pure check over a probe, and the probe that asks the image's interpreter.

Prior art: the campaign ``test_smoke`` pins the messages of its pure host
check, so a GPU run that would compute on the CPU cannot pass it silently.
The probe runs a real interpreter; ``torch`` and ``onnxruntime`` are stand-in
packages put first on its path, so no GPU is needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.gpu_preflight import (
    CUDA_EXECUTION_PROVIDER,
    GpuProbe,
    GpuProbeError,
    gpu_preflight_failures,
    probe_gpu,
)

_READY = GpuProbe(
    torch_version="2.5.1+cu124",
    torch_sees_cuda=True,
    torch_error=None,
    onnxruntime_providers=(CUDA_EXECUTION_PROVIDER, "CPUExecutionProvider"),
    onnxruntime_error=None,
)


def _probe(**changes: object) -> GpuProbe:
    values = {
        "torch_version": _READY.torch_version,
        "torch_sees_cuda": _READY.torch_sees_cuda,
        "torch_error": _READY.torch_error,
        "onnxruntime_providers": _READY.onnxruntime_providers,
        "onnxruntime_error": _READY.onnxruntime_error,
        **changes,
    }
    return GpuProbe(**values)  # type: ignore[arg-type]


# --- the pure check: pinned messages ---------------------------------------------


def test_an_image_whose_torch_sees_cuda_and_whose_onnxruntime_lists_it_passes() -> None:
    assert gpu_preflight_failures(_READY) == []


def test_a_torch_without_cuda_is_named() -> None:
    failures = gpu_preflight_failures(_probe(torch_sees_cuda=False))

    assert failures == [
        "torch 2.5.1+cu124 sees no CUDA device (torch.cuda.is_available() is False), "
        "expected the GPU this run was given: check the NVIDIA driver and that the "
        "container was started with the GPU"
    ]


def test_a_torch_that_does_not_import_is_named() -> None:
    probe = _probe(torch_version=None, torch_sees_cuda=False, torch_error="ModuleNotFoundError: x")

    assert gpu_preflight_failures(probe) == [
        "torch does not import (ModuleNotFoundError: x), expected the CUDA image's torch: "
        "without it this GPU run would compute on the CPU"
    ]


def test_an_onnxruntime_without_the_cuda_provider_is_named() -> None:
    probe = _probe(onnxruntime_providers=("AzureExecutionProvider", "CPUExecutionProvider"))

    assert gpu_preflight_failures(probe) == [
        "onnxruntime lists the execution providers "
        "['AzureExecutionProvider', 'CPUExecutionProvider'], expected "
        "'CUDAExecutionProvider' among them: onnxruntime-gpu is missing or cannot load CUDA"
    ]


def test_an_onnxruntime_that_does_not_import_is_named() -> None:
    probe = _probe(onnxruntime_providers=None, onnxruntime_error="ImportError: libcudnn")

    assert gpu_preflight_failures(probe) == [
        "onnxruntime does not import (ImportError: libcudnn), expected onnxruntime-gpu "
        "listing 'CUDAExecutionProvider'"
    ]


def test_every_gap_is_named_at_once() -> None:
    probe = _probe(torch_sees_cuda=False, onnxruntime_providers=("CPUExecutionProvider",))

    failures = gpu_preflight_failures(probe)

    assert len(failures) == 2
    assert failures[0].startswith("torch ")
    assert failures[1].startswith("onnxruntime ")


# --- the probe: a real interpreter, stand-in packages -------------------------------


def _stand_in_packages(root: Path, *, torch_body: str, onnxruntime_body: str) -> dict[str, str]:
    for name, body in (("torch", torch_body), ("onnxruntime", onnxruntime_body)):
        (root / name).mkdir(parents=True)
        (root / name / "__init__.py").write_text(body, encoding="utf-8")
    return {**os.environ, "PYTHONPATH": str(root)}


_CUDA_TORCH = """
__version__ = "2.5.1+cu124"
class cuda:
    @staticmethod
    def is_available():
        return True
"""
_CPU_TORCH = _CUDA_TORCH.replace("return True", "return False").replace("cu124", "cpu")
_CUDA_ONNXRUNTIME = """
def get_available_providers():
    return ["CUDAExecutionProvider", "CPUExecutionProvider"]
"""


def test_the_probe_reports_what_the_interpreter_sees(tmp_path: Path) -> None:
    env = _stand_in_packages(tmp_path, torch_body=_CUDA_TORCH, onnxruntime_body=_CUDA_ONNXRUNTIME)

    probe = probe_gpu((sys.executable,), env=env)

    assert probe == _READY


def test_the_probe_reports_a_cpu_torch(tmp_path: Path) -> None:
    env = _stand_in_packages(tmp_path, torch_body=_CPU_TORCH, onnxruntime_body=_CUDA_ONNXRUNTIME)

    probe = probe_gpu((sys.executable,), env=env)

    assert (probe.torch_version, probe.torch_sees_cuda) == ("2.5.1+cpu", False)


def test_the_probe_reports_a_package_that_fails_to_import(tmp_path: Path) -> None:
    env = _stand_in_packages(
        tmp_path,
        torch_body=_CUDA_TORCH,
        onnxruntime_body="raise ImportError('libcudnn.so.9: cannot open shared object file')",
    )

    probe = probe_gpu((sys.executable,), env=env)

    assert probe.onnxruntime_providers is None
    assert probe.onnxruntime_error == ("ImportError: libcudnn.so.9: cannot open shared object file")
    assert gpu_preflight_failures(probe)[0].startswith("onnxruntime does not import")


def test_a_probe_that_cannot_run_is_an_error_naming_the_interpreter(tmp_path: Path) -> None:
    missing = str(tmp_path / "no-such-python")

    with pytest.raises(GpuProbeError, match="no-such-python"):
        probe_gpu((missing,), env=dict(os.environ))


def test_a_probe_that_hangs_is_stopped_at_its_timeout(tmp_path: Path) -> None:
    env = _stand_in_packages(
        tmp_path, torch_body="import time\ntime.sleep(30)", onnxruntime_body=_CUDA_ONNXRUNTIME
    )

    with pytest.raises(GpuProbeError, match=r"did not answer within 0\.5s"):
        probe_gpu((sys.executable,), env=env, timeout_s=0.5)


def test_a_probe_whose_last_line_is_not_its_answer_is_an_error(tmp_path: Path) -> None:
    # A package that prints at exit leaves the probe's answer short of the last line.
    torch_body = _CUDA_TORCH + "\nimport atexit\natexit.register(print, 'null')\n"
    env = _stand_in_packages(tmp_path, torch_body=torch_body, onnxruntime_body=_CUDA_ONNXRUNTIME)

    with pytest.raises(GpuProbeError, match="expected one JSON object"):
        probe_gpu((sys.executable,), env=env)


def test_a_probe_answering_providers_as_a_string_is_an_error(tmp_path: Path) -> None:
    env = _stand_in_packages(
        tmp_path,
        torch_body=_CUDA_TORCH,
        onnxruntime_body="def get_available_providers():\n    return 'CUDAExecutionProvider'\n",
    )

    with pytest.raises(GpuProbeError, match="expected a list of provider names"):
        probe_gpu((sys.executable,), env=env)
