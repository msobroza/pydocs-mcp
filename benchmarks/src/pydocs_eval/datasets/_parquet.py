"""Pinned-revision parquet release edge for the retrieval-track dataset loaders.

The sibling ``_download.py`` is this package's STDLIB edge: a single JSONL
behind a pinned HuggingFace revision, fetched with ``urllib``. Some corpora
publish only parquet (the bug-localization pair does), and no stdlib module
reads parquet — so this is the second edge, and the only module in
``pydocs_eval.datasets`` that imports a parquet engine.

``huggingface_hub`` and ``pyarrow`` are imported FUNCTION-LOCALLY and declared
under the ``[datasets-parquet]`` extra, so importing this module (and therefore
the whole ``datasets`` package, which the registry populates eagerly) stays
stdlib-cheap and the offline test suite — which reaches every loader through
its ``fixture_path`` seam — never needs the wheels at all.

WHY not reuse ``datasets_swe/download.py``, which does the same two things:
that package states in its own docstring that its heavy deps exist "only to
(re)build the committed artifacts, never to run the offline test suite"
(ADR 0013). Importing it from a loader would silently turn ``[datasets-swe]``
into a runtime extra and make that statement false. Two edges, two contracts,
no cross-package coupling.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

# Actionable hint for the "extra not installed" path, mirroring
# ``_retrieval_extra._INSTALL_HINT``: a bare ``ModuleNotFoundError: No module
# named 'pyarrow'`` raised inside an ``asyncio.to_thread`` worker names neither
# the extra nor the reason it exists.
_INSTALL_HINT = (
    "Resolving a pinned parquet dataset release requires the 'datasets-parquet' "
    "extra (huggingface_hub to fetch the pinned revision, pyarrow to read it). "
    'Install with: pip install "pydocs-mcp-eval[datasets-parquet]". '
    "The offline test suite needs neither wheel — every loader reaches its rows "
    "through the fixture_path seam."
)

# Bound on the metadata (ETag) round-trip ``hf_hub_download`` makes before it
# streams. WHY it is set at all: the sibling stdlib edge ``_download.py`` passes
# ``timeout=60`` to ``urlopen``, and an unbounded edge stalls a paid campaign
# inside a worker thread with no wall bound of its own. ``hf_hub_download``
# retries the transfer itself (huggingface_hub's own bounded backoff), so this
# is the one knob the caller owns.
_HF_ETAG_TIMEOUT_SECONDS = 60


def _raise_missing_parquet_extra(cause: BaseException) -> NoReturn:
    """Restate a missing ``huggingface_hub``/``pyarrow`` as an actionable error."""
    raise RuntimeError(_INSTALL_HINT) from cause


@dataclass(frozen=True, slots=True)
class ParquetPin:
    """One content-addressed HF parquet snapshot: what to fetch, at which commit.

    Pinning the ``revision`` (never ``main``) is what makes a re-run months
    later load byte-identical rows. Bumping a pin is a deliberate edit: re-read
    the schema, re-check the row count, then update the constant.
    """

    dataset_id: str
    revision: str
    #: Repo-relative parquet paths, e.g. ``("data/test-00000-of-00001.parquet",)``.
    files: tuple[str, ...]
    #: Rows the pinned shard(s) must contain. Enforced by
    #: :func:`read_parquet_rows`, so a pin edit that silently swaps the slice
    #: (LCA's three language configs ship IDENTICALLY NAMED parquet files under
    #: ``py/``, ``java/`` and ``kt/``) fails loudly instead of scoring another
    #: language's corpus.
    expected_rows: int

    def to_dict(self) -> dict[str, object]:
        """Lockfile-serializable form (mirrors ``datasets_swe.pins.DatasetPin``)."""
        return {
            "dataset_id": self.dataset_id,
            "revision": self.revision,
            "files": list(self.files),
            "expected_rows": self.expected_rows,
        }


def default_parquet_cache_dir() -> Path:
    """Local parquet cache root (mirrors the swe-qa loaders' ``~/.cache`` convention)."""
    return Path("~/.cache/pydocs-mcp/hf-parquet").expanduser()


def download_parquet(pin: ParquetPin, cache_dir: Path | None = None) -> list[Path]:
    """Fetch ``pin``'s parquet file(s) at the PINNED revision; return local paths.

    ``hf_hub_download`` is content-addressed by ``revision``, so the bytes are
    reproducible and a re-run with the same pin re-hits the local cache.

    Example:
        >>> download_parquet(SWE_BENCH_VERIFIED_PIN)  # doctest: +SKIP
        [PosixPath('~/.cache/.../test-00000-of-00001.parquet')]
    """
    try:
        from huggingface_hub import hf_hub_download  # heavy; keep off module load
    except ImportError as exc:
        _raise_missing_parquet_extra(exc)

    root = (cache_dir or default_parquet_cache_dir()) / pin.revision
    root.mkdir(parents=True, exist_ok=True)
    return [
        Path(
            hf_hub_download(
                repo_id=pin.dataset_id,
                filename=name,
                revision=pin.revision,
                repo_type="dataset",
                local_dir=str(root),
                etag_timeout=_HF_ETAG_TIMEOUT_SECONDS,
            )
        )
        for name in pin.files
    ]


class ParquetPinMismatchError(RuntimeError):
    """A pinned parquet release did not contain the row count it declares."""


def assert_pin_row_count(rows: list[dict[str, Any]], pin: ParquetPin) -> list[dict[str, Any]]:
    """``rows`` unchanged, or a :class:`ParquetPinMismatchError` naming both counts.

    Split out of :func:`read_parquet_rows` so the guard itself is testable
    without the parquet wheels — the offline suite must stay free of them.

    Raises:
        ParquetPinMismatchError: the shard(s) hold a different number of rows
            than the pin records.
    """
    if len(rows) == pin.expected_rows:
        return rows
    raise ParquetPinMismatchError(
        f"{pin.dataset_id} at revision {pin.revision} file(s) {list(pin.files)} hold "
        f"{len(rows)} row(s), expected {pin.expected_rows}; re-verify the pin before "
        "changing expected_rows (an identically named parquet exists under every "
        "config directory of a multi-config dataset)"
    )


def read_parquet_rows(
    paths: list[Path], columns: list[str], pin: ParquetPin
) -> list[dict[str, Any]]:
    """Read the named columns from ``pin``'s parquet shard(s) into row dicts.

    Naming the columns (rather than reading the whole table) keeps the memory
    cost proportional to what a loader actually consumes — the bug-localization
    snapshots carry 13 and 41 columns respectively, of which the loaders read 6.

    The row count is checked against ``pin.expected_rows``, so a pin edit that
    points at a different slice or a release that silently changes shape fails
    HERE, naming both numbers — rather than downstream as a short corpus.
    """
    try:
        import pyarrow.parquet as pq  # heavy; function-local by design
    except ImportError as exc:
        _raise_missing_parquet_extra(exc)

    rows: list[dict[str, Any]] = []
    for path in paths:
        table = pq.read_table(path, columns=columns)
        rows.extend(table.to_pylist())
    return assert_pin_row_count(rows, pin)


__all__ = [
    "ParquetPin",
    "ParquetPinMismatchError",
    "assert_pin_row_count",
    "default_parquet_cache_dir",
    "download_parquet",
    "read_parquet_rows",
]
