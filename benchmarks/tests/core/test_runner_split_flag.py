"""``--split`` CLI wiring: argparse choices mirror ``VALID_SPLITS``.

The choices list used to be a hardcoded literal that duplicated the shared
helper's ``VALID_SPLITS`` — a drift hazard that silently rejects any split
added to the helper (exactly what happened when ``small_dev`` landed).
These tests pin the single-source-of-truth wiring.
"""

from __future__ import annotations

from pydocs_eval.datasets._split import VALID_SPLITS
from pydocs_eval.runner import _build_arg_parser


def test_split_choices_mirror_valid_splits() -> None:
    parser = _build_arg_parser()
    (split_action,) = [a for a in parser._actions if a.dest == "split"]
    assert tuple(split_action.choices) == VALID_SPLITS


def test_split_flag_accepts_small_dev() -> None:
    args = _build_arg_parser().parse_args(
        ["--configs", "cfg.yaml", "--split", "small_dev"],
    )
    assert args.split == "small_dev"
