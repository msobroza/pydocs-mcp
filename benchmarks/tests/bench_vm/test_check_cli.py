"""``python -m pydocs_eval.bench_vm check``: exit codes, output, and no network or product import."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from pydocs_eval.bench_vm import load_run_spec
from pydocs_eval.bench_vm.__main__ import main

from ._spec_dicts import COMMITTED_RUNS_DIR, REPO_ROOT, agent_track_spec, retrieval_spec, write_spec


class NetworkCallMade(AssertionError):
    """Raised by the guarded socket layer when the check tries to connect."""


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise NetworkCallMade("the bench VM check opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


def test_valid_specs_print_their_run_ids_and_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_network: None
) -> None:
    first = write_spec(tmp_path, "first", retrieval_spec())
    second = write_spec(tmp_path, "second", retrieval_spec(gpu={}))

    exit_code = main(["check", str(first), str(second)])

    out = capsys.readouterr()
    assert exit_code == 0
    assert out.out.splitlines() == [
        f"{first}: {load_run_spec(first).run_id}",
        f"{second}: {load_run_spec(second).run_id}",
    ]
    assert out.err == ""


def test_any_refused_spec_exits_non_zero_and_names_its_field_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_network: None
) -> None:
    good = write_spec(tmp_path, "good", retrieval_spec())
    bad = write_spec(tmp_path, "bad", agent_track_spec(max_usd=None, gpu={}, spto=False))

    exit_code = main(["check", str(good), str(bad)])

    out = capsys.readouterr()
    assert exit_code == 1
    assert out.out.startswith(f"{good}: good-")
    assert f"{bad}: spto: got False, expected a known field" in out.err
    # Field errors first stop kind rules from running; fix them and the rules speak.
    assert f"{bad}: gpu:" not in out.err


def test_kind_rule_errors_each_get_a_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_network: None
) -> None:
    bad = write_spec(
        tmp_path, "bad", agent_track_spec(max_usd=None, gpu={}, args=["--ledger", "x"])
    )

    exit_code = main(["check", str(bad)])

    err_lines = capsys.readouterr().err.splitlines()
    assert exit_code == 1
    assert {line.split(":")[1].strip() for line in err_lines} == {"args", "gpu", "max_usd"}
    assert (
        f"{bad}: args: got '--ledger', expected a flag kind agent_track does not own"
        in err_lines[0]
    )


def test_a_missing_file_is_refused_not_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.yaml"

    assert main(["check", str(missing)]) == 1
    assert "(file): got" in capsys.readouterr().err


def test_check_imports_nothing_from_the_product_and_reads_no_credentials(tmp_path: Path) -> None:
    # A clean child with no cloud credentials in its environment: the check must
    # pass without them and leave pydocs_mcp unimported.
    script = (
        "import sys\n"
        "from pydocs_eval.bench_vm.__main__ import main\n"
        "code = main(['check', *sys.argv[1:]])\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] == 'pydocs_mcp')\n"
        "assert not leaked, leaked\n"
        "sys.exit(code)\n"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(REPO_ROOT / "benchmarks" / "src"),
    }
    specs = sorted(str(path) for path in COMMITTED_RUNS_DIR.glob("*.yaml"))

    result = subprocess.run(
        [sys.executable, "-c", script, *specs],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == len(specs)
