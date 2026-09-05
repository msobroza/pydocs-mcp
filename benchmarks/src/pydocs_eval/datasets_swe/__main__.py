"""Build CLI for the committed Phase 3 dataset artifacts (ADR 0013).

Subcommands re-buildable at any time over the PINNED revisions::

    python -m pydocs_eval.datasets_swe overlap    # -> data/swe/overlap-report.md
    python -m pydocs_eval.datasets_swe splits      # -> data/swe/splits/*
    python -m pydocs_eval.datasets_swe touch-log   # -> data/swe/pro-touch-log.jsonl
    python -m pydocs_eval.datasets_swe all         # all of the above (one download)

Only these entry points hit the network (via :mod:`download`). The pure functions they
call are offline-testable; the committed outputs are what the campaign lockfile consumes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from . import composition, overlap, splits, touch_log
from .download import default_cache_dir, download_parquet, read_live_records, read_pro_python
from .pins import (
    LIVE_DISTINCT_ROWS,
    LIVE_PIN,
    LIVE_RAW_ROWS,
    LIVE_REPOS,
    PRO_PIN,
    PRO_PYTHON_INSTANCES,
    PRO_PYTHON_REPOS,
    pin_metadata,
)
from .records import LiveRecord, dedupe_live_records


def _default_data_dir() -> Path:
    # benchmarks/src/pydocs_eval/datasets_swe/__main__.py -> benchmarks/data/swe
    return Path(__file__).resolve().parents[3] / "data" / "swe"


@dataclass(frozen=True, slots=True)
class Corpus:
    """The downloaded + deduped inputs shared by the overlap and split builds."""

    raw_records: list[LiveRecord]
    records: list[LiveRecord]  # post-dedupe working set
    pro_repos: list[str]
    pro_instances: int


def _load_corpus(cache_dir: Path | None) -> Corpus:
    raw = read_live_records(download_parquet(LIVE_PIN, cache_dir))
    records = dedupe_live_records(raw)
    pro_repos, pro_instances = read_pro_python(download_parquet(PRO_PIN, cache_dir))
    _assert_live_shape(raw, records)
    _assert_pro_shape(pro_repos, pro_instances)
    return Corpus(raw, records, pro_repos, pro_instances)


def _assert_live_shape(raw: list[LiveRecord], records: list[LiveRecord]) -> None:
    _require(len(raw) == LIVE_RAW_ROWS, "Live raw rows", len(raw), LIVE_RAW_ROWS)
    _require(len(records) == LIVE_DISTINCT_ROWS, "Live working", len(records), LIVE_DISTINCT_ROWS)
    repos = len({r.repo for r in records})
    _require(repos == LIVE_REPOS, "Live repos", repos, LIVE_REPOS)


def _assert_pro_shape(pro_repos: list[str], pro_instances: int) -> None:
    _require(
        pro_instances == PRO_PYTHON_INSTANCES, "Pro-Python", pro_instances, PRO_PYTHON_INSTANCES
    )
    _require(
        len(pro_repos) == len(PRO_PYTHON_REPOS), "Pro repos", len(pro_repos), len(PRO_PYTHON_REPOS)
    )


def _require(ok: bool, label: str, got: object, expected: object) -> None:
    if not ok:
        raise ValueError(f"{label} mismatch: got {got!r}, expected {expected!r} — pin drifted?")


def _build_overlap(corpus: Corpus, out_dir: Path) -> Path:
    report = overlap.compute_overlap(
        corpus.records,
        corpus.pro_repos,
        live_raw_rows=len(corpus.raw_records),
        pro_python_instances=corpus.pro_instances,
    )
    path = out_dir / "overlap-report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(overlap.render_markdown(report))
    return path


def _build_splits(corpus: Corpus, out_dir: Path) -> list[Path]:
    result = splits.build_splits(corpus.records, corpus.pro_repos)
    split_dir = out_dir / "splits"
    split_dir.mkdir(parents=True, exist_ok=True)
    written = [
        _write(split_dir / "dev.txt", composition.render_instance_list(result.dev_instances)),
        _write(split_dir / "val.txt", composition.render_instance_list(result.val_instances)),
        _write(split_dir / "split-config.json", composition.render_split_config(result)),
        _write(
            split_dir / "composition-dev.md",
            composition.render_dev_composition(result, corpus.records),
        ),
        _write(
            split_dir / "composition-val.md",
            composition.render_val_composition(result, corpus.records),
        ),
    ]
    return written


def _build_touch_log(out_dir: Path) -> Path:
    path = out_dir / "pro-touch-log.jsonl"
    existing = touch_log.read_entries(path)
    if any(e.access_type == touch_log.READ_ONLY_MANIFEST for e in existing):
        return path  # already recorded — append-only, do not duplicate
    entry = touch_log.read_only_entry(
        pin_metadata(),
        justification="Phase 3 read-only manifest + R2 overlap computation; zero rollouts.",
    )
    touch_log.append_entry(path, entry)
    return path


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pydocs_eval.datasets_swe")
    parser.add_argument(
        "command",
        choices=("overlap", "splits", "touch-log", "all"),
        help="which committed artifact(s) to build",
    )
    parser.add_argument("--out-dir", type=Path, default=_default_data_dir())
    parser.add_argument("--cache-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.command == "touch-log":
        print(_build_touch_log(args.out_dir))
        return 0
    corpus = _load_corpus(args.cache_dir or default_cache_dir())
    if args.command in ("overlap", "all"):
        print(_build_overlap(corpus, args.out_dir))
    if args.command in ("splits", "all"):
        for path in _build_splits(corpus, args.out_dir):
            print(path)
    if args.command == "all":
        print(_build_touch_log(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
