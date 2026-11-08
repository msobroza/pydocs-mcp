"""``make bench-vm-check``: the bench VM tree, checked with no credentials and no backend.

Each test runs the real target in a copy of the tree (the Makefile, every
tracked or new file under ``infra/bench-vm/``, and a link to ``benchmarks/``),
in an environment with no cloud credentials and a throwaway ``HOME``, so a
breakage made here never touches the checkout. The Terraform provider is
downloaded once per session into a shared plugin cache (or taken from the
caller's ``TF_PLUGIN_CACHE_DIR``); everything else is local.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BENCH_VM = Path("infra") / "bench-vm"
_LOCK_FILE = _BENCH_VM / "terraform" / ".terraform.lock.hcl"
_TOOLS = ("make", "terraform", "shellcheck", "hadolint")
_MISSING_TOOLS = [tool for tool in _TOOLS if shutil.which(tool) is None]

needs_tools = pytest.mark.skipif(
    bool(_MISSING_TOOLS), reason=f"bench-vm-check needs {', '.join(_MISSING_TOOLS)}"
)


@pytest.fixture(scope="session")
def plugin_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    preset = os.environ.get("TF_PLUGIN_CACHE_DIR")
    return Path(preset) if preset else tmp_path_factory.mktemp("tf-plugin-cache")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of what ``bench-vm-check`` reads, safe to break."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--"]
        + ["Makefile", str(_BENCH_VM)],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout.decode()
    copy = tmp_path / "repo"
    for name in filter(None, listed.split("\0")):
        if not (_REPO_ROOT / name).exists():  # deleted in the working tree, not yet staged
            continue
        target = copy / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_REPO_ROOT / name, target)
    (copy / "benchmarks").symlink_to(_REPO_ROOT / "benchmarks", target_is_directory=True)
    return copy


def _write_wrapper(directory: Path, name: str, executable: str) -> None:
    # A wrapper, not a symlink: a linked venv python would lose its venv.
    wrapper = directory / name
    wrapper.write_text(f'#!/bin/sh\nexec "{executable}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)


def _python_shim(home: Path) -> Path:
    """A directory whose ``python`` is this interpreter, as the check calls it."""
    shim = home / "python-shim"
    shim.mkdir(parents=True, exist_ok=True)
    _write_wrapper(shim, "python", sys.executable)
    return shim


def _credential_free_env(home: Path, plugin_cache: Path, path: str | None = None) -> dict[str, str]:
    # No GOOGLE_* / CLOUDSDK_* variable and a HOME with no gcloud config or ADC:
    # the check must pass with nothing to authenticate with.
    home.mkdir(exist_ok=True)
    shim = _python_shim(home)
    return {
        "PATH": path or f"{shim}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
        "TF_PLUGIN_CACHE_DIR": str(plugin_cache),
    }


def _make_check(
    tree: Path, plugin_cache: Path, path: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "bench-vm-check"],
        cwd=tree,
        env=_credential_free_env(tree.parent / "home", plugin_cache, path),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stdout + completed.stderr


def _failed_steps(completed: subprocess.CompletedProcess[str]) -> str:
    # The check's summary line; make adds its own `Error 1` line after it.
    prefix = "bench-vm-check: failed: "
    summaries = [line for line in completed.stderr.splitlines() if line.startswith(prefix)]
    assert len(summaries) == 1, _output(completed)
    return summaries[0].removeprefix(prefix)


def _files_under(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_the_makefile_declares_bench_vm_check_as_a_phony_target() -> None:
    text = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "\nbench-vm-check:" in text
    phony = text.split("\n\n", 1)[0]
    assert "bench-vm-check" in phony


@needs_tools
def test_a_clean_tree_passes_and_is_left_exactly_as_it_was(tree: Path, plugin_cache: Path) -> None:
    before = _files_under(tree / _BENCH_VM)

    completed = _make_check(tree, plugin_cache)

    assert completed.returncode == 0, _output(completed)
    assert completed.stdout.strip().splitlines()[-1] == "bench-vm-check: ok"
    # Every committed run spec is reported with its run id.
    for spec in sorted((tree / _BENCH_VM / "runs").glob("*.yaml")):
        assert f"{_BENCH_VM / 'runs' / spec.name}: {spec.stem}-" in completed.stdout
    # No .terraform/, no rewritten lock file, nothing new anywhere in the tree.
    assert _files_under(tree / _BENCH_VM) == before


@needs_tools
def test_a_broken_run_spec_fails_naming_the_spec(tree: Path, plugin_cache: Path) -> None:
    broken = _BENCH_VM / "runs" / "broken.yaml"
    (tree / broken).write_text("git_ref: main\nkind: nope\n", encoding="utf-8")

    completed = _make_check(tree, plugin_cache)

    assert completed.returncode != 0
    assert f"{broken}: kind: got 'nope'" in completed.stderr
    assert _failed_steps(completed) == "run specs"


@needs_tools
def test_an_unformatted_terraform_file_fails_naming_the_file(
    tree: Path, plugin_cache: Path
) -> None:
    unformatted = _BENCH_VM / "terraform" / "unformatted.tf"
    (tree / unformatted).write_text("locals {\n  a   = 1\n}\n", encoding="utf-8")

    completed = _make_check(tree, plugin_cache)

    assert completed.returncode != 0
    assert str(unformatted) in _output(completed)
    assert _failed_steps(completed) == "terraform fmt"


@needs_tools
def test_a_shellcheck_finding_fails_naming_the_script(tree: Path, plugin_cache: Path) -> None:
    script = _BENCH_VM / "host" / "startup.sh"
    with (tree / script).open("a", encoding="utf-8") as handle:
        handle.write("\necho $1\n")

    completed = _make_check(tree, plugin_cache)

    assert completed.returncode != 0
    assert f"In {script} line" in completed.stdout
    assert _failed_steps(completed) == "shellcheck"


@needs_tools
def test_a_hadolint_finding_fails_naming_the_dockerfile(tree: Path, plugin_cache: Path) -> None:
    dockerfile = _BENCH_VM / "docker" / "Dockerfile.cpu"
    with (tree / dockerfile).open("a", encoding="utf-8") as handle:
        handle.write("\nRUN cd /tmp && echo hi\n")

    completed = _make_check(tree, plugin_cache)

    assert completed.returncode != 0
    assert f"{dockerfile}:" in completed.stdout
    assert _failed_steps(completed) == "hadolint"


def _terraform_whose_first_init_fails(directory: Path, real_terraform: str) -> None:
    """A ``terraform`` that fails its first ``init`` the way a flaky registry does."""
    directory.mkdir()
    marker = directory / "init-failed-once"
    script = directory / "terraform"
    script.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        f'  if [ "$arg" = init ] && [ ! -e "{marker}" ]; then\n'
        f'    : > "{marker}"\n'
        '    echo "Error: Failed to query available provider packages: '
        'net/http: TLS handshake timeout" >&2\n'
        "    exit 1\n"
        "  fi\n"
        "done\n"
        f'exec "{real_terraform}" "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)


@needs_tools
def test_a_transient_terraform_init_failure_is_retried(
    tree: Path, plugin_cache: Path, tmp_path: Path
) -> None:
    # init is the check's one network call; a registry or proxy blip on it once
    # failed unrelated steps' tests in full-suite runs, so it gets a bounded retry.
    real_terraform = shutil.which("terraform")
    assert real_terraform is not None  # needs_tools guarantees it
    flaky = tmp_path / "flaky-terraform"
    _terraform_whose_first_init_fails(flaky, real_terraform)
    shim = _python_shim(tree.parent / "home")
    path = os.pathsep.join([str(flaky), str(shim), os.environ.get("PATH", "")])

    completed = _make_check(tree, plugin_cache, path=path)

    assert completed.returncode == 0, _output(completed)
    assert completed.stdout.rstrip().endswith("bench-vm-check: ok")
    assert "terraform init: attempt 1 of 3 failed" in completed.stderr


@needs_tools
def test_a_missing_tool_fails_naming_the_tool(
    tree: Path, plugin_cache: Path, tmp_path: Path
) -> None:
    # A PATH with every tool the check needs except hadolint.
    if any((Path(d) / "hadolint").exists() for d in ("/usr/bin", "/bin")):
        pytest.skip("hadolint is installed system-wide; it cannot be hidden from PATH")
    tools = tmp_path / "bin"
    tools.mkdir()
    _write_wrapper(tools, "python", sys.executable)
    for tool in ("make", "terraform", "shellcheck"):
        found = shutil.which(tool)
        assert found is not None  # needs_tools guarantees it
        _write_wrapper(tools, tool, found)

    completed = _make_check(
        tree, plugin_cache, path=os.pathsep.join([str(tools), "/usr/bin", "/bin"])
    )

    assert completed.returncode != 0
    assert "hadolint: not found on PATH" in completed.stderr
    assert _failed_steps(completed) == "hadolint"
