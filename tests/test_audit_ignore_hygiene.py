"""The three pip-audit sites keep one ignore set between them.

ci.yml's ``security`` job audits the lock, fresh-install.yml audits what an unlocked install
resolves, and CLAUDE.md documents the gate command a local run reproduces CI with. An ignore
dropped in one place and kept in another, or added without a WHY, used to rely on a reviewer
remembering "drop them in both places together"; these tests make it a failing check.

The langchain-openai pair GHSA-r7w7-9xr2-qq2r / PYSEC-2026-76 (CVE-2026-41488) was ignored only
because the core ``openai<2.0`` cap held langchain-openai below its fixed 1.1.14; the cap is
lifted and the lock carries a fixed release, so the pair must not come back in any of them.

Core dependencies only (PyYAML), so CI's ``python`` job runs it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_FRESH_INSTALL_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "fresh-install.yml"
_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"
_IGNORED_ID = re.compile(r"--ignore-vuln\s+([A-Z]+-[\w-]+)")
_LANGCHAIN_OPENAI_PAIR = (r"GHSA-r7w7-9xr2-qq2r\b", r"PYSEC-2026-76\b")


def _named_step_run(workflow: Path, job_name: str, step_name: str) -> str:
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    (step,) = [step for step in jobs[job_name]["steps"] if step.get("name") == step_name]
    return step["run"]


def _security_job_ignored_ids() -> list[str]:
    return _IGNORED_ID.findall(_named_step_run(_CI_WORKFLOW, "security", "pip-audit"))


def _fresh_install_audit_run() -> str:
    return _named_step_run(_FRESH_INSTALL_WORKFLOW, "fresh-install", "pip-audit (fresh resolution)")


def _claude_md_gate_command() -> str:
    """The documented ``uvx pip-audit`` command, joined across its ``\\`` continuations."""
    lines = _CLAUDE_MD.read_text(encoding="utf-8").splitlines()
    (start,) = [i for i, line in enumerate(lines) if line.startswith("uvx pip-audit")]
    command: list[str] = []
    for line in lines[start:]:
        code = line.split("#", 1)[0].rstrip()
        command.append(code.removesuffix("\\"))
        if not code.endswith("\\"):
            break
    return " ".join(command)


def _security_job_comments() -> str:
    """Every comment line of ci.yml's ``security`` job (where each ignore's WHY lives)."""
    text = _CI_WORKFLOW.read_text(encoding="utf-8")
    job = text.split("\n  security:\n", 1)[1]
    job = re.split(r"\n  [\w-]+:\n", job, maxsplit=1)[0]
    return "\n".join(line.strip() for line in job.splitlines() if line.strip().startswith("#"))


def test_the_security_job_ignores_each_id_once() -> None:
    ignored = _security_job_ignored_ids()

    assert ignored, "ci.yml's pip-audit step lost its --ignore-vuln flags (parser drift?)"
    assert len(ignored) == len(set(ignored)), f"duplicate --ignore-vuln flags: {ignored}"


def test_claude_md_gate_command_ignores_exactly_what_the_security_job_ignores() -> None:
    documented = set(_IGNORED_ID.findall(_claude_md_gate_command()))

    assert documented == set(_security_job_ignored_ids())


def test_fresh_install_ignores_nothing_the_security_job_does_not_justify() -> None:
    run = _fresh_install_audit_run()

    assert "--strict --disable-pip --no-deps" in run
    assert set(_IGNORED_ID.findall(run)) <= set(_security_job_ignored_ids())


def test_every_security_job_ignore_is_named_in_a_why_comment() -> None:
    comments = _security_job_comments()

    unexplained = [vuln for vuln in _security_job_ignored_ids() if vuln not in comments]
    assert unexplained == [], f"--ignore-vuln without a WHY comment: {unexplained}"


def test_the_fixed_langchain_openai_advisory_is_ignored_nowhere() -> None:
    sites = (_CI_WORKFLOW, _FRESH_INSTALL_WORKFLOW, _CLAUDE_MD)

    found = [
        f"{site.relative_to(_REPO_ROOT)}: {pattern}"
        for site in sites
        for pattern in _LANGCHAIN_OPENAI_PAIR
        if re.search(pattern, site.read_text(encoding="utf-8"))
    ]
    assert found == [], f"GHSA-r7w7-9xr2-qq2r / PYSEC-2026-76 still present: {found}"
