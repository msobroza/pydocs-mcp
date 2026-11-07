"""The GPU pre-flight's probe in tests: canned answers, and a fake that records who asked."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydocs_eval.bench_vm.gpu_preflight import CUDA_EXECUTION_PROVIDER, GpuProbe, GpuProbeError

CUDA_READY_PROBE = GpuProbe(
    torch_version="2.5.1+cu124",
    torch_sees_cuda=True,
    torch_error=None,
    onnxruntime_providers=(CUDA_EXECUTION_PROVIDER, "CPUExecutionProvider"),
    onnxruntime_error=None,
)
CPU_ONLY_PROBE = GpuProbe(
    torch_version="2.5.1+cpu",
    torch_sees_cuda=False,
    torch_error=None,
    onnxruntime_providers=("CPUExecutionProvider",),
    onnxruntime_error=None,
)


@dataclass
class FakeGpuProbe:
    """Stands in for asking the image's interpreter: answers ``probe`` (or raises).

    Records the interpreter each call asked; ``delay_s`` makes it slow.
    """

    probe: GpuProbe = CUDA_READY_PROBE
    error: str | None = None
    delay_s: float = 0.0
    asked: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.asked)

    def __call__(self, python: Sequence[str]) -> GpuProbe:
        self.asked.append(tuple(python))
        time.sleep(self.delay_s)
        if self.error is not None:
            raise GpuProbeError(self.error)
        return self.probe
