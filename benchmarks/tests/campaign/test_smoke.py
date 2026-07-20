"""Smoke preconditions: fail-fast with actionable messages on the dev machine."""

from __future__ import annotations

import pytest

from pydocs_eval.campaign.smoke import (
    HostProbe,
    SmokePreconditionError,
    check_preconditions,
    ensure_preconditions,
    probe_host,
    run_smoke,
)


def _ready_probe() -> HostProbe:
    return HostProbe(
        arch="x86_64",
        container_runtime="/usr/bin/docker",
        claude_cli="/usr/bin/claude",
        free_disk_gb=250.0,
        ram_gb=32.0,
    )


def _dev_mac_probe() -> HostProbe:
    return HostProbe(
        arch="arm64", container_runtime=None, claude_cli=None, free_disk_gb=15.0, ram_gb=8.0
    )


def test_ready_host_passes() -> None:
    assert check_preconditions(_ready_probe()) == []
    ensure_preconditions(_ready_probe())  # no raise


def test_dev_machine_fails_all_gates() -> None:
    failures = check_preconditions(_dev_mac_probe())
    joined = "\n".join(failures)
    assert "arch is 'arm64'" in joined
    assert "no container runtime" in joined
    assert "'claude' CLI is not on PATH" in joined
    assert "free disk is 15.0 GB" in joined
    assert "RAM is 8.0 GB" in joined


def test_ensure_preconditions_raises_on_dev_machine() -> None:
    with pytest.raises(SmokePreconditionError, match="precondition"):
        ensure_preconditions(_dev_mac_probe())


def test_unknown_ram_is_not_a_hard_fail() -> None:
    probe = HostProbe(
        arch="x86_64", container_runtime="/d", claude_cli="/c", free_disk_gb=200.0, ram_gb=None
    )
    assert check_preconditions(probe) == []


async def test_run_smoke_requires_exactly_three_instances() -> None:
    async def _pipeline(instances):  # pragma: no cover - never reached
        raise AssertionError("pipeline should not run")

    with pytest.raises(ValueError, match="exactly 3 instances"):
        await run_smoke(["i1", "i2"], pipeline=_pipeline, probe=_ready_probe())


async def test_run_smoke_gates_before_pipeline() -> None:
    async def _pipeline(instances):  # pragma: no cover - never reached on dev machine
        raise AssertionError("pipeline ran despite failed preconditions")

    with pytest.raises(SmokePreconditionError):
        await run_smoke(["i1", "i2", "i3"], pipeline=_pipeline, probe=_dev_mac_probe())


def test_probe_host_returns_populated_fields() -> None:
    probe = probe_host()
    assert isinstance(probe.arch, str) and probe.arch
    assert probe.free_disk_gb > 0
