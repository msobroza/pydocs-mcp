"""``annotate-alignment``: open the annotation page on one alignment label file.

The page (``pydocs_eval.judge.annotate_app``) runs under ``streamlit run``, and
Streamlit comes with the eval suite's ``[ask]`` extra. The verb reads the label
file first, so a wrong path or a line the reader refuses is named here rather
than in the browser. It then starts Streamlit on the page and returns its exit
status. ``--read-only`` reaches the page, which then writes nothing.

Exit status: Streamlit's own, or 2 when the label file or the install is refused.

Usage:
    python -m pydocs_eval.campaign annotate-alignment --labels FILE [--read-only]

The same page without this verb:
    streamlit run <site-packages>/pydocs_eval/judge/annotate_app.py -- FILE [--read-only]
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pydocs_eval.judge.annotate_app as annotate_app
from pydocs_eval.judge.alignment_label_file import read_alignment_labels

_EXIT_REFUSED = 2
_STREAMLIT_MISSING = (
    "the annotation page needs Streamlit, which comes with the eval suite's [ask] extra: "
    'pip install "pydocs-mcp-eval[ask]"'
)

#: Runs one ``streamlit run`` command line and returns its exit status.
StreamlitLauncher = Callable[[Sequence[str]], int]
#: Locates an installed module without importing it (``importlib.util.find_spec``).
ModuleFinder = Callable[[str], object | None]


def add_annotate_alignment_command(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``annotate-alignment`` on the campaign CLI's subparser."""
    parser = sub.add_parser(
        "annotate-alignment",
        help="open the alignment annotation page (Streamlit) on a label file",
    )
    parser.add_argument("--labels", type=Path, required=True, help="the alignment label file")
    parser.add_argument(
        "--read-only", action="store_true", help="review the file: the page writes nothing"
    )
    parser.set_defaults(func=cmd_annotate_alignment)


def cmd_annotate_alignment(
    args: argparse.Namespace,
    *,
    launch: StreamlitLauncher | None = None,
    find_module: ModuleFinder = importlib.util.find_spec,
) -> int:
    """Start the page on ``--labels``; refuse an unreadable file or a missing Streamlit first."""
    try:
        _refuse_unreadable_labels(args.labels)
        _refuse_missing_streamlit(find_module)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"annotate-alignment: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    command = annotation_page_command(args.labels, read_only=args.read_only)
    return (launch or _run_streamlit)(command)


def annotation_page_command(labels: Path, *, read_only: bool) -> list[str]:
    """The ``streamlit run`` command line that opens the page on ``labels``."""
    page = str(Path(annotate_app.__file__))
    flags = ["--read-only"] if read_only else []
    return [sys.executable, "-m", "streamlit", "run", page, "--", str(labels), *flags]


def _refuse_unreadable_labels(labels: Path) -> None:
    if not labels.is_file():
        raise ValueError(f"--labels {labels} does not exist, expected an alignment label file")
    read_alignment_labels(labels)


def _refuse_missing_streamlit(find_module: ModuleFinder) -> None:
    if find_module("streamlit") is None:
        raise RuntimeError(_STREAMLIT_MISSING)


def _run_streamlit(command: Sequence[str]) -> int:
    # The command is this interpreter, Streamlit, the page and the operator's own file.
    return subprocess.run(list(command), check=False).returncode


__all__ = ("add_annotate_alignment_command", "annotation_page_command", "cmd_annotate_alignment")
