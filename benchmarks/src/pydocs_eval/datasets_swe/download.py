"""Pinned-revision parquet download + read edge for the SWE-bench snapshots (ADR 0013).

This is the ONLY module that touches the network or a parquet engine. It fetches each
snapshot's parquet at its PINNED HF revision (never auto-latest) via ``huggingface_hub``
and reduces the nested columns to the flat :mod:`records` dataclasses the pure functions
consume. Heavy imports (``huggingface_hub``, ``pyarrow``) are function-local so importing
this module — and the whole ``datasets_swe`` package — stays stdlib-cheap; they are
declared under the ``[datasets-swe]`` extra and only needed to (re)build the committed
artifacts, never to run the offline test suite (the download is mocked there).
"""

from __future__ import annotations

from pathlib import Path

from .pins import LIVE_PIN, PRO_PIN, PRO_PYTHON_LANGUAGE, DatasetPin
from .records import LiveRecord


def default_cache_dir() -> Path:
    """Local parquet cache root (mirrors the swe-qa loaders' ``~/.cache`` convention)."""
    return Path("~/.cache/pydocs-mcp/swe-bench-snapshots").expanduser()


def download_parquet(pin: DatasetPin, cache_dir: Path | None = None) -> list[Path]:
    """Download ``pin``'s parquet files at its PINNED revision; return local paths.

    Uses ``huggingface_hub.hf_hub_download`` (content-addressed by ``revision``), so the
    bytes are reproducible and cached — a re-run with the same pin re-hits the cache.
    """
    from huggingface_hub import hf_hub_download  # heavy; keep import-cost off module load

    root = cache_dir or default_cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    return [
        Path(
            hf_hub_download(
                repo_id=pin.dataset_id,
                filename=name,
                revision=pin.revision,
                repo_type="dataset",
                local_dir=str(root / pin.revision),
            )
        )
        for name in pin.parquet_files
    ]


def _read_columns(paths: list[Path], columns: list[str]) -> list[dict[str, object]]:
    """Read the named columns from parquet shard(s) into a list of row dicts."""
    import pyarrow.parquet as pq  # heavy; function-local by design

    rows: list[dict[str, object]] = []
    for path in paths:
        table = pq.read_table(path, columns=columns)
        rows.extend(table.to_pylist())
    return rows


def read_live_records(paths: list[Path]) -> list[LiveRecord]:
    """Reduce the Live ``full`` parquet shard(s) to :class:`LiveRecord` rows.

    ``difficulty`` is a ``{files, hunks, lines}`` struct; ``created_at`` a timestamp —
    both flattened to the two ints the split stratification needs.
    """
    rows = _read_columns(paths, ["instance_id", "repo", "difficulty", "created_at"])
    return [_to_live_record(row) for row in rows]


def _to_live_record(row: dict[str, object]) -> LiveRecord:
    difficulty = row["difficulty"] or {}
    return LiveRecord(
        instance_id=str(row["instance_id"]),
        repo=str(row["repo"]),
        difficulty_files=int(difficulty.get("files", 0)),  # type: ignore[attr-defined]
        created_at_year=_year_of(row["created_at"]),
    )


def _year_of(value: object) -> int:
    """Extract a 4-digit year from a parquet timestamp (datetime) or ISO string."""
    year = getattr(value, "year", None)
    if year is not None:
        return int(year)
    return int(str(value)[:4])


def read_pro_python(paths: list[Path]) -> tuple[list[str], int]:
    """Distinct sorted Pro **Python** repos + the Python instance count.

    Filters ``repo_language == "python"`` — the entire public Pro Python surface — so the
    R2 org-exclusion set (and the 266/3 assertion) is MEASURED from the pinned parquet.
    """
    rows = _read_columns(paths, ["repo", "repo_language"])
    python = [row for row in rows if str(row["repo_language"]).lower() == PRO_PYTHON_LANGUAGE]
    return sorted({str(row["repo"]) for row in python}), len(python)


def read_pro_python_repos(paths: list[Path]) -> list[str]:
    """Distinct, sorted Pro Python repos (drops the count — see :func:`read_pro_python`)."""
    repos, _count = read_pro_python(paths)
    return repos


def load_live_records(cache_dir: Path | None = None) -> list[LiveRecord]:
    """Download (pinned) + read the Live ``full`` records — the build-script entry point."""
    return read_live_records(download_parquet(LIVE_PIN, cache_dir))


def load_pro_python_repos(cache_dir: Path | None = None) -> list[str]:
    """Download (pinned) + read the Pro Python repos — the build-script entry point."""
    return read_pro_python_repos(download_parquet(PRO_PIN, cache_dir))
