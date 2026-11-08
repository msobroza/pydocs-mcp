"""The ``bench-vm-check`` CI workflow: runs only on bench VM changes, and runs the make target.

It also checks the images' eval lock against ``uv.lock`` (``image_deps.py
lock-eval --check``) and audits it with nothing ignored, so a ``uv.lock``
change that leaves the images' pins behind fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "bench-vm-check.yml"


def _workflow() -> dict:
    loaded = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare `on:` key as the boolean True (YAML 1.1).
    loaded["on"] = loaded.pop(True)
    return loaded


def _steps() -> list[dict]:
    (job,) = _workflow()["jobs"].values()
    return job["steps"]


def test_it_triggers_only_on_the_paths_the_check_reads() -> None:
    triggers = _workflow()["on"]

    assert set(triggers) == {"pull_request", "push"}
    assert triggers["push"]["branches"] == ["main"]
    for trigger in triggers.values():
        assert set(trigger["paths"]) == {
            "infra/**",
            "benchmarks/src/pydocs_eval/**",
            "benchmarks/pyproject.toml",
            # The images' eval lock is compiled against uv.lock: a dependency
            # bump that touches nothing else must still re-check it.
            "uv.lock",
            "Makefile",
            ".github/workflows/bench-vm-check.yml",
        }


def test_it_runs_the_same_make_target() -> None:
    runs = [step.get("run", "") for step in _steps()]

    assert runs[-1].strip() == "make bench-vm-check"


def _step_running(fragment: str) -> int:
    (index,) = [i for i, step in enumerate(_steps()) if fragment in step.get("run", "")]
    return index


def _uv_setup_step() -> int:
    (index,) = [
        i
        for i, step in enumerate(_steps())
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    ]
    return index


def test_it_checks_the_image_eval_lock_against_uv_lock() -> None:
    check = _step_running("python infra/bench-vm/docker/image_deps.py lock-eval --repo . --check")

    # lock-eval drives `uv export` and `uv pip compile`.
    assert _uv_setup_step() < check


def test_it_audits_the_image_eval_lock_with_a_pinned_pip_audit_and_nothing_ignored() -> None:
    step = _step_running("--requirement infra/bench-vm/docker/eval-requirements.txt")
    audit = _steps()[step]["run"]
    (job,) = _workflow()["jobs"].values()

    assert _uv_setup_step() < step
    assert re.search(
        r"uvx pip-audit==\$\{PIP_AUDIT_VERSION\} --strict --disable-pip --no-deps ", audit
    )
    assert "--ignore-vuln" not in audit
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(job["env"]["PIP_AUDIT_VERSION"]))


def test_it_reads_the_repository_and_nothing_else() -> None:
    assert _workflow()["permissions"] == {"contents": "read"}


def test_every_action_and_tool_download_is_pinned() -> None:
    steps = _steps()
    actions = [step["uses"] for step in steps if "uses" in step]
    downloads = "\n".join(step.get("run", "") for step in steps if "curl" in step.get("run", ""))

    assert actions and all(re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", uses) for uses in actions)
    assert "hashicorp/setup-terraform@v3" in actions
    # Each downloaded binary is checked against a committed digest.
    assert downloads.count("curl") == downloads.count("sha256sum --check")
    assert {"hadolint", "shellcheck"} <= set(re.findall(r"(hadolint|shellcheck)", downloads))


def test_the_terraform_it_installs_satisfies_the_roots_required_version() -> None:
    (setup,) = [step for step in _steps() if step.get("uses", "").startswith("hashicorp/")]
    pinned = tuple(int(part) for part in setup["with"]["terraform_version"].split("."))
    versions_tf = (_REPO_ROOT / "infra" / "bench-vm" / "terraform" / "versions.tf").read_text()
    (floor,) = re.findall(r'required_version = ">= ([\d.]+)"', versions_tf)

    assert pinned >= tuple(int(part) for part in floor.split("."))
    # Without the wrapper, terraform's own output and exit code reach the log.
    assert setup["with"]["terraform_wrapper"] is False
