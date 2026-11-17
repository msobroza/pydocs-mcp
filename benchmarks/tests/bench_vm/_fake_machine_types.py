"""Machine-type facts the fake cloud describes: the shapes the launcher's machine check sees.

Where the live Compute API was asked (``gcloud compute machine-types describe
… --format=json``, europe-west4-a, 2026-11-17, #434), each fake reports what it
answered: no ``architecture`` at all for the classic x86 families N2 and E2,
``X86_64`` for G2 and ``ARM64`` for T2A.
"""

from __future__ import annotations

from pydocs_eval.bench_vm.bench_cloud import MachineTypeFacts

N2_STANDARD_8 = MachineTypeFacts(
    name="n2-standard-8", architecture=None, guest_cpus=8, memory_mb=32768
)
G2_STANDARD_8 = MachineTypeFacts(
    name="g2-standard-8",
    architecture="X86_64",
    guest_cpus=8,
    memory_mb=32768,
    bundled_accelerators=(("nvidia-l4", 1),),
)
# Not asked live; a reported X86_64 keeps a refusal on memory alone covered.
N1_STANDARD_8 = MachineTypeFacts(
    name="n1-standard-8", architecture="X86_64", guest_cpus=8, memory_mb=30720
)
N2_STANDARD_4 = MachineTypeFacts(
    name="n2-standard-4", architecture=None, guest_cpus=4, memory_mb=16384
)
N2_HIGHMEM_8 = MachineTypeFacts(
    name="n2-highmem-8", architecture=None, guest_cpus=8, memory_mb=65536
)
T2A_STANDARD_8 = MachineTypeFacts(
    name="t2a-standard-8", architecture="ARM64", guest_cpus=8, memory_mb=32768
)
E2_STANDARD_8 = MachineTypeFacts(
    name="e2-standard-8", architecture=None, guest_cpus=8, memory_mb=32768
)
DEFAULT_MACHINE_TYPES = (
    N2_STANDARD_8,
    G2_STANDARD_8,
    N1_STANDARD_8,
    N2_STANDARD_4,
    N2_HIGHMEM_8,
    T2A_STANDARD_8,
    E2_STANDARD_8,
)
