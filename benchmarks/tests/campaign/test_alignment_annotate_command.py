"""The ``annotate-alignment`` verb: the annotation page on one label file, through ``streamlit run``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.alignment_annotate_command import (
    add_annotate_alignment_command,
    cmd_annotate_alignment,
)
from tests.judge._annotation_fakes import write_labelled_file


class FakeStreamlitLauncher:
    """Stands in for ``streamlit run``: records each command line and starts no server."""

    def __init__(self, exit_code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.exit_code = exit_code

    def __call__(self, command: Sequence[str]) -> int:
        self.commands.append(list(command))
        return self.exit_code


def _no_streamlit(name: str) -> None:
    """``importlib.util.find_spec`` where the ``[ask]`` extra is not installed."""
    return None


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_annotate_alignment_command(parser.add_subparsers())
    return parser.parse_args(["annotate-alignment", *argv])


@pytest.fixture
def labels(tmp_path: Path) -> Path:
    path = tmp_path / "repoqa-qa__jev-1.13.jsonl"
    write_labelled_file(path)
    return path


def test_the_campaign_cli_offers_the_verb(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["annotate-alignment", "--help"])

    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert "--labels" in out
    assert "--read-only" in out


def test_the_page_runs_on_the_label_file_and_the_exit_status_is_streamlits(labels: Path) -> None:
    launcher = FakeStreamlitLauncher(exit_code=3)

    status = cmd_annotate_alignment(_args("--labels", str(labels)), launch=launcher)

    assert status == 3
    (command,) = launcher.commands
    assert command[:4] == [sys.executable, "-m", "streamlit", "run"]
    page = Path(command[4])
    assert page.parts[-3:] == ("pydocs_eval", "judge", "annotate_app.py")
    assert page.is_file()
    assert command[5:] == ["--", str(labels)]


def test_read_only_is_handed_to_the_page(labels: Path) -> None:
    launcher = FakeStreamlitLauncher()

    cmd_annotate_alignment(_args("--labels", str(labels), "--read-only"), launch=launcher)

    (command,) = launcher.commands
    assert command[5:] == ["--", str(labels), "--read-only"]


def test_a_label_file_that_does_not_exist_is_refused_before_anything_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = FakeStreamlitLauncher()
    absent = tmp_path / "absent.jsonl"

    status = cmd_annotate_alignment(_args("--labels", str(absent)), launch=launcher)

    assert (status, launcher.commands) == (2, [])
    assert f"--labels {absent} does not exist" in capsys.readouterr().err


def test_a_label_file_the_reader_refuses_is_refused_before_anything_starts(
    labels: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels.write_text('{"revision": "0.9"}\n', encoding="utf-8")
    launcher = FakeStreamlitLauncher()

    status = cmd_annotate_alignment(_args("--labels", str(labels)), launch=launcher)

    assert (status, launcher.commands) == (2, [])
    assert f"{labels.name} line 1: revision '0.9'" in capsys.readouterr().err


def test_without_streamlit_it_names_the_ask_extra_and_starts_nothing(
    labels: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = FakeStreamlitLauncher()

    status = cmd_annotate_alignment(
        _args("--labels", str(labels)), launch=launcher, find_module=_no_streamlit
    )

    assert (status, launcher.commands) == (2, [])
    assert 'pip install "pydocs-mcp-eval[ask]"' in capsys.readouterr().err
