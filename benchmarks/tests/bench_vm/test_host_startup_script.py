"""The host startup script, run for real by bash against stub ``docker`` and ``curl``.

``PATH`` holds only the two stubs plus the few base-OS tools the script may
use, so a script that reached for anything else (gcloud, jq, python) fails
here. The stubs record every call; ``curl`` answers like the metadata server.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.commit_layout import HOST_STARTUP_SCRIPT
from pydocs_eval.bench_vm.container_paths import CACHE_DIR, STATE_DIR
from pydocs_eval.bench_vm.vm_request import (
    METADATA_IMAGE,
    METADATA_IMAGE_DIGEST,
    METADATA_RUN_ID,
)

from ._spec_dicts import REPO_ROOT

SCRIPT = REPO_ROOT / HOST_STARTUP_SCRIPT
_BASH = shutil.which("bash") or "/bin/bash"
# What Container-Optimized OS and the Deep Learning VM image both ship.
BASE_OS_TOOLS = ("mkdir", "timeout", "sleep")
_DIGEST = "sha256:" + "ab" * 32
_REGISTRY = "europe-west4-docker.pkg.dev"
_TOKEN = "ya29.fake-registry-token"
_METADATA_URL = "http://metadata.google.internal/computeMetadata/v1/"

_STUB = """#!{python}
import json, os, sys
calls = os.environ["STUB_CALLS"]
name = os.path.basename(sys.argv[0])
stdin = sys.stdin.read() if name == "docker" and sys.argv[1:2] == ["login"] else None
with open(calls, "a") as f:
    f.write(json.dumps({{"tool": name, "argv": sys.argv[1:], "stdin": stdin}}) + "\\n")
if name == "curl":
    answers = json.load(open(os.environ["STUB_METADATA"]))
    url = sys.argv[-1]
    if url not in answers:
        sys.stderr.write("curl: (22) The requested URL returned error: 404\\n")
        sys.exit(22)
    sys.stdout.write(answers[url])
elif sys.argv[1:3] == ["container", "inspect"]:
    sys.exit(0 if os.environ.get("STUB_CONTAINER_EXISTS") == "1" else 1)
"""


def _image(flavour: str) -> str:
    return f"{_REGISTRY}/bench-project/bench/bench-{flavour}@{_DIGEST}"


def _metadata(image: str) -> dict[str, str]:
    attributes = {METADATA_RUN_ID: "cpu-smoke-0123abcd", METADATA_IMAGE: image}
    attributes[METADATA_IMAGE_DIGEST] = _DIGEST
    answers = {f"{_METADATA_URL}instance/attributes/{k}": v for k, v in attributes.items()}
    answers[f"{_METADATA_URL}instance/service-accounts/default/token"] = json.dumps(
        {"access_token": _TOKEN, "expires_in": 3599, "token_type": "Bearer"}
    )
    return answers


@dataclass
class Boot:
    returncode: int
    stderr: str
    calls: list[dict[str, object]]
    host_dir: Path

    def docker(self, verb: str) -> list[list[str]]:
        return [
            list(call["argv"])  # type: ignore[call-overload]
            for call in self.calls
            if call["tool"] == "docker" and call["argv"][0] == verb  # type: ignore[index]
        ]


def _boot(tmp_path: Path, image: str, *, container_exists: bool = False) -> Boot:
    bin_dir, host_dir = tmp_path / "bin", tmp_path / "host"
    bin_dir.mkdir()
    for tool in ("docker", "curl"):
        stub = bin_dir / tool
        stub.write_text(_STUB.format(python=sys.executable), encoding="utf-8")
        stub.chmod(0o755)
    for tool in BASE_OS_TOOLS:
        found = shutil.which(tool)
        assert found is not None, tool
        (bin_dir / tool).symlink_to(found)
    answers = tmp_path / "metadata.json"
    answers.write_text(json.dumps(_metadata(image)), encoding="utf-8")
    calls = tmp_path / "calls.jsonl"
    env = {
        "PATH": str(bin_dir),
        "STUB_CALLS": str(calls),
        "STUB_METADATA": str(answers),
        "STUB_CONTAINER_EXISTS": "1" if container_exists else "0",
        "BENCH_HOST_DIR": str(host_dir),
    }
    completed = subprocess.run(
        [_BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return Boot(completed.returncode, completed.stderr, [json.loads(x) for x in lines], host_dir)


def test_the_startup_script_passes_shellcheck() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed; bench-vm-check runs it in CI")
    completed = subprocess.run(
        [shellcheck, str(SCRIPT)], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, completed.stdout


def test_a_cpu_boot_logs_in_pulls_by_digest_and_starts_the_supervisor(tmp_path: Path) -> None:
    boot = _boot(tmp_path, _image("cpu"))

    assert boot.returncode == 0, boot.stderr
    assert boot.docker("pull") == [["pull", "--quiet", _image("cpu")]]
    (run,) = boot.docker("run")
    host = boot.host_dir
    assert f"{host}/state:{STATE_DIR}" in run
    assert f"{host}/cache:{CACHE_DIR}" in run
    assert run[run.index("--entrypoint") + 1] == "python"
    assert run[run.index(_image("cpu")) + 1 :] == [
        "-m",
        "pydocs_eval.bench_vm",
        "supervise",
        "--state-dir",
        str(STATE_DIR),
        "--cache-dir",
        str(CACHE_DIR),
    ]
    assert "--gpus" not in run
    assert (host / "state").is_dir()
    assert (host / "cache").is_dir()


def test_the_registry_login_takes_the_metadata_token_on_stdin_and_never_logs_it(
    tmp_path: Path,
) -> None:
    boot = _boot(tmp_path, _image("cpu"))

    (login,) = [c for c in boot.calls if c["argv"][0] == "login"]  # type: ignore[index]
    assert login["argv"] == [
        "login",
        "--username",
        "oauth2accesstoken",
        "--password-stdin",
        f"https://{_REGISTRY}",
    ]
    assert login["stdin"] == _TOKEN
    assert _TOKEN not in boot.stderr
    assert all(_TOKEN not in json.dumps(c["argv"]) for c in boot.calls)


def test_a_cuda_boot_exposes_the_gpus(tmp_path: Path) -> None:
    boot = _boot(tmp_path, _image("cuda"))

    (run,) = boot.docker("run")
    assert run[run.index("--gpus") + 1] == "all"


def test_the_script_reads_the_launchers_metadata_keys(tmp_path: Path) -> None:
    boot = _boot(tmp_path, _image("cpu"))

    urls = {c["argv"][-1] for c in boot.calls if c["tool"] == "curl"}  # type: ignore[index]
    for key in (METADATA_IMAGE, METADATA_IMAGE_DIGEST, METADATA_RUN_ID):
        assert f"{_METADATA_URL}instance/attributes/{key}" in urls
    curl = next(c["argv"] for c in boot.calls if c["tool"] == "curl")
    assert "Metadata-Flavor: Google" in curl  # type: ignore[operator]
    assert "--max-time" in curl  # type: ignore[operator]


def test_an_image_not_named_by_its_digest_is_refused_before_any_pull(tmp_path: Path) -> None:
    boot = _boot(tmp_path, f"{_REGISTRY}/bench-project/bench/bench-cpu:latest")

    assert boot.returncode != 0
    assert boot.docker("pull") == []
    assert boot.docker("run") == []
    assert json.loads(boot.stderr.strip().splitlines()[-1])["event"] == "bench_vm_host_failed"


def test_a_second_boot_with_the_container_present_starts_nothing(tmp_path: Path) -> None:
    boot = _boot(tmp_path, _image("cpu"), container_exists=True)

    assert boot.returncode == 0, boot.stderr
    assert boot.docker("pull") == []
    assert boot.docker("run") == []


def test_every_log_line_is_one_json_object_with_an_event(tmp_path: Path) -> None:
    boot = _boot(tmp_path, _image("cpu"))

    lines = [line for line in boot.stderr.splitlines() if line.strip()]
    assert lines
    assert all("event" in json.loads(line) for line in lines)


def test_the_script_is_executable_bash() -> None:
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/bash\n")
    assert os.access(SCRIPT, os.X_OK)
