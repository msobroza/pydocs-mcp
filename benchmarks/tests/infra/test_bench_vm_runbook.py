"""The bench VM runbook (``infra/bench-vm/README.md``) documents what the tree really offers.

Its commands are checked against the merged code: every ``make bench-vm*``
line against the Makefile with ``make -n``, every ``python -m
pydocs_eval.bench_vm`` line against the CLI's parser, every ``terraform``
flag against ``terraform <command> -help``. Nothing here runs a cloud command.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from pydocs_eval.bench_vm.__main__ import build_bench_vm_parser

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BENCH_VM = _REPO_ROOT / "infra" / "bench-vm"
_README = _BENCH_VM / "README.md"
# CLAUDE.md's README audit grep, plus bare issue references (#123).
_JARGON = re.compile(r"PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+|#[0-9]+")


def _readme() -> str:
    return _README.read_text(encoding="utf-8")


def _code_lines() -> list[str]:
    """The lines of the README's code blocks, trailing comments dropped."""
    lines: list[str] = []
    in_block = False
    for line in _readme().splitlines():
        if line.lstrip().startswith("```"):
            in_block = not in_block
        elif in_block:
            lines.append(line.split(" #", 1)[0].strip())
    return lines


def _command_lines(prefix: str) -> list[str]:
    return [line for line in _code_lines() if line.startswith(prefix)]


def _bench_vm_targets() -> set[str]:
    makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"^(bench-vm[\w-]*):", makefile, flags=re.MULTILINE))


def test_the_readme_carries_no_pr_issue_or_task_jargon() -> None:
    assert _JARGON.findall(_readme()) == []


def test_every_bench_vm_target_is_documented_with_a_command() -> None:
    documented = {shlex.split(line)[1] for line in _command_lines("make bench-vm")}

    # The five targets the runbook must cover; a new one belongs in it too.
    assert _bench_vm_targets() >= {
        "bench-vm",
        "bench-vm-status",
        "bench-vm-stop",
        "bench-vm-pull",
        "bench-vm-check",
    }
    assert documented == _bench_vm_targets()
    assert any("DRY_RUN=1" in line for line in _command_lines("make bench-vm "))


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_every_documented_make_command_is_accepted_by_the_makefile() -> None:
    for line in _command_lines("make bench-vm"):
        argv = ["make", "-n", "--no-print-directory", *shlex.split(line)[1:]]
        completed = subprocess.run(
            argv, cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60, check=False
        )

        assert completed.returncode == 0, f"{line}: {completed.stderr}"
        for variable in re.findall(r"(?<!\w)RUN=(\S+)", line):
            assert (_BENCH_VM / variable).is_file(), f"{line}: no spec {variable}"


def test_every_documented_cli_command_parses() -> None:
    lines = _command_lines("PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm")

    assert lines, "the runbook shows the CLI the make targets call"
    for line in lines:
        argv = shlex.split(line)[4:]
        build_bench_vm_parser().parse_args(argv)  # exits (SystemExit) on an unknown flag


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform is not installed")
def test_every_documented_terraform_flag_exists() -> None:
    lines = _command_lines("terraform ")

    assert {shlex.split(line)[1] for line in lines} >= {"init", "apply", "output"}
    for line in lines:
        command, *rest = shlex.split(line)[1:]
        help_text = subprocess.run(
            ["terraform", command, "-help"], capture_output=True, text=True, timeout=60, check=False
        ).stdout
        for flag in (token.split("=", 1)[0] for token in rest if token.startswith("-")):
            assert re.search(rf"^\s+{re.escape(flag)}\b", help_text, re.MULTILINE), (
                f"{line}: terraform {command} has no {flag}"
            )


def test_every_declared_secret_is_named_with_its_gcloud_command() -> None:
    secrets_tf = (_BENCH_VM / "terraform" / "secrets.tf").read_text(encoding="utf-8")
    secret_ids = re.findall(r'=\s*"(bench-[a-z-]+-api-key)"', secrets_tf)
    added = [line for line in _code_lines() if "gcloud secrets versions add" in line]

    assert len(secret_ids) == 2
    for secret_id in secret_ids:
        assert any(f" {secret_id} " in f" {line} " for line in added), secret_id


def test_it_covers_setup_relaunch_ssh_and_cost() -> None:
    text = _readme()

    for phrase in (
        "gcloud auth application-default set-quota-project",
        "terraform init -backend-config=",
        "self_delete: false",
        "--tunnel-through-iap",
        "spot: false",
        "PREEMPTIBLE_NVIDIA_L4_GPUS",
        "NVIDIA_L4_GPUS",
        "50 EUR",
        "-backend=false -lockfile=readonly",
    ):
        assert phrase in text, phrase
    # Relaunch after each ending the run record can hold.
    for status in ("succeeded", "failed", "timed_out", "preempted", "stopped", "lost"):
        assert f"`{status}`" in text, status
