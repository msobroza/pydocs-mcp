"""3-instance smoke — end-to-end on the provisioned host only (ADR 0014 item 5).

The smoke runs the full pipeline over 3 dev instances: rollout → traces →
metrics → feedback strings → mini-report, re-measuring serve RSS / index wall /
container footprint on the target x86_64 host and empirically verifying the
mutation-visibility statement and a pre-seeded cache hit.

None of that can run on the development machine (arm64, no container runtime,
under the RAM/disk floor — ADR 0014 §Evidence), so every host-requiring step is
gated behind :func:`ensure_preconditions`, which FAILS FAST with an actionable
message naming the offending value and what the host must provide. On this
machine the guard raises before any rollout is attempted; the test pins the
messages. The host probe is injectable so the checks are unit-testable without
the target host.
"""

from __future__ import annotations

import platform
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

# The harness floor (ADR 0014 §Evidence, swebench-src/README.md:97): x86_64,
# ≥16 GB RAM, ≥120 GB free disk. Single source of truth for the smoke gate.
_REQUIRED_ARCHS = ("x86_64", "amd64")
_MIN_DISK_GB = 120.0
_MIN_RAM_GB = 16.0
_SMOKE_INSTANCE_COUNT = 3
# Container runtimes the grading step can drive (the official amd64 image path).
_CONTAINER_RUNTIMES = ("docker", "podman", "colima")


class SmokePreconditionError(RuntimeError):
    """One or more host preconditions failed — the smoke cannot run here (ADR 0014).

    Carries the full actionable failure list so the operator sees every gap
    (wrong arch AND no container runtime AND …) in one message, not one at a time.
    """


@dataclass(frozen=True, slots=True)
class HostProbe:
    """The measured host facts the smoke gate checks (injectable for tests)."""

    arch: str
    container_runtime: str | None
    claude_cli: str | None
    free_disk_gb: float
    ram_gb: float | None


def probe_host() -> HostProbe:
    """Snapshot the current host: arch, container runtime, claude CLI, disk, RAM."""
    return HostProbe(
        arch=platform.machine(),
        container_runtime=_first_on_path(_CONTAINER_RUNTIMES),
        claude_cli=shutil.which("claude"),
        free_disk_gb=shutil.disk_usage("/").free / 1024**3,
        ram_gb=_ram_gb(),
    )


def check_preconditions(probe: HostProbe) -> list[str]:
    """Return the list of failed-precondition messages (empty ⇒ host is ready).

    Each message names the offending value and the required shape, so the
    operator can act without reading the ADR. Pure over ``probe``.
    """
    checks = (
        _check_arch(probe),
        _check_container(probe),
        _check_claude(probe),
        _check_disk(probe),
        _check_ram(probe),
    )
    return [msg for msg in checks if msg is not None]


def ensure_preconditions(probe: HostProbe | None = None) -> None:
    """Raise :class:`SmokePreconditionError` unless every host precondition holds.

    Called first by :func:`run_smoke`, so a dev-machine invocation fails fast
    before any rollout, container pull, or spend.
    """
    resolved = probe or probe_host()
    failures = check_preconditions(resolved)
    if failures:
        raise SmokePreconditionError(
            "smoke cannot run on this host — "
            + str(len(failures))
            + " precondition(s) failed:\n  - "
            + "\n  - ".join(failures)
        )


def _check_arch(probe: HostProbe) -> str | None:
    if probe.arch in _REQUIRED_ARCHS:
        return None
    return (
        f"arch is {probe.arch!r}, expected one of {list(_REQUIRED_ARCHS)!r}: the per-instance "
        "images are amd64-only; provision the remote x86_64 host (ADR 0014 checkpoint #1)"
    )


def _check_container(probe: HostProbe) -> str | None:
    if probe.container_runtime is not None:
        return None
    return (
        f"no container runtime on PATH (looked for {list(_CONTAINER_RUNTIMES)!r}): grading needs "
        "the official image; install docker on the host (ADR 0014 §Decision 1)"
    )


def _check_claude(probe: HostProbe) -> str | None:
    if probe.claude_cli is not None:
        return None
    return "the 'claude' CLI is not on PATH: install the headless Claude Code CLI on the host"


def _check_disk(probe: HostProbe) -> str | None:
    if probe.free_disk_gb >= _MIN_DISK_GB:
        return None
    return (
        f"free disk is {probe.free_disk_gb:.1f} GB, need >= {_MIN_DISK_GB:.0f} GB "
        "(harness working floor + per-instance images)"
    )


def _check_ram(probe: HostProbe) -> str | None:
    if probe.ram_gb is None or probe.ram_gb >= _MIN_RAM_GB:
        return None  # unknown RAM is not a hard fail; a measured under-floor value is
    return f"RAM is {probe.ram_gb:.1f} GB, need >= {_MIN_RAM_GB:.0f} GB (serve + index headroom)"


def _first_on_path(names: Sequence[str]) -> str | None:
    """The first of ``names`` resolvable on PATH, else ``None``."""
    for name in names:
        found = shutil.which(name)
        if found is not None:
            return found
    return None


def _ram_gb() -> float | None:
    """Total RAM in GiB, or ``None`` when the platform does not expose it portably."""
    try:
        pages = _sysconf("SC_PHYS_PAGES")
        page_size = _sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None
    if pages is None or page_size is None:
        return None
    return pages * page_size / 1024**3


def _sysconf(name: str) -> int | None:
    import os

    if name not in getattr(os, "sysconf_names", {}):
        return None
    return os.sysconf(name)


@dataclass(frozen=True, slots=True)
class SmokeReport:
    """The mini-report the smoke emits: which instances ran + the per-step tally."""

    instances: tuple[str, ...]
    steps_verified: tuple[str, ...]


SmokeStep = Callable[[Sequence[str]], Awaitable[SmokeReport]]


async def run_smoke(
    instances: Sequence[str], *, pipeline: SmokeStep, probe: HostProbe | None = None
) -> SmokeReport:
    """Gate on host preconditions, then drive the 3-instance end-to-end ``pipeline``.

    ``pipeline`` is the injected end-to-end step (rollout → traces → metrics →
    feedback → mini-report) — a seam so the gating logic is testable without the
    host. Raises :class:`SmokePreconditionError` on the dev machine before the
    pipeline runs, and :class:`ValueError` if not exactly 3 instances are given.
    """
    if len(instances) != _SMOKE_INSTANCE_COUNT:
        raise ValueError(
            f"smoke runs exactly {_SMOKE_INSTANCE_COUNT} instances, got {len(instances)}: {list(instances)!r}"
        )
    ensure_preconditions(probe)
    return await pipeline(instances)
