"""AC-1 / AC-7: `--watch` flag presence + parser shape."""
from __future__ import annotations

import pytest


def test_serve_subparser_accepts_watch_flag() -> None:
    """AC-1: `pydocs-mcp serve <project> --watch` parses without error."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["serve", ".", "--watch"])
    assert args.cmd == "serve"
    assert getattr(args, "watch", False) is True


def test_serve_subparser_watch_defaults_false() -> None:
    """AC-7: without `--watch`, the namespace.watch is False (or unset
    falling through to YAML's enabled=false)."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["serve", "."])
    # `store_true` default is False; pin that explicitly so we don't
    # accidentally start defaulting to True.
    assert getattr(args, "watch", False) is False


def test_index_subparser_rejects_watch_flag() -> None:
    """`--watch` is `serve`-only (spec §4.2 out of scope: watch mode for
    `pydocs-mcp index`). Argparse should refuse the flag for `index`."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["index", ".", "--watch"])
