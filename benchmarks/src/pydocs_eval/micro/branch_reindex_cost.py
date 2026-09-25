"""``branch_reindex_cost`` — what indexing a second branch costs against its diff.

Spec R21 / §6.12 (#320): adding a branch must cost its diff, not the project.
This builds a synthetic repository in a temp dir (``main`` with ``--files``
modules; ``feature/x`` edits one function in each of ``changed`` of them), then
runs the real ``pydocs-mcp index`` twice and ``pydocs-mcp index --branch
feature/x`` once, in this process. A counting embedder stands in for the model —
no download, and a count of every text embedded. The report carries the
``--branch`` run's ``branch_reindex`` line (the branch pass's own counts: files
total / reused / extracted, chunks embedded / shared, vectors removed) beside
each run's wall time and embedder count.

The counts follow the diff; the ``--branch`` run's wall time does not. Before
its branch pass it repeats the working-tree pass over the checked-out ``main``,
which re-parses every file of ``main`` (that pass extracts before its package
gate; the incremental working tree is P2.6) and then finds nothing to write.
The second plain ``index`` is the control that times that pass alone, and
``seconds_branch_pass`` is the ``--branch`` run less the control::

    python -m pydocs_eval.micro.branch_reindex_cost --files 200 --changed-percent 1 5 20
    python -m pydocs_eval.micro.branch_reindex_cost --files 200 --changed 5

One JSON report per diff size, each on a fresh repository, printed as a list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra
from pydocs_eval.micro.synthetic_branch_repository import (
    FEATURE_BRANCH,
    build_synthetic_branch_repository,
)

try:
    # WHY the full module path: scripts/smoke_check_benchmark_imports.py resolves
    # `from package import name` with hasattr(package, name), which a subpackage
    # only passes once something has imported it — the dotted form is checked
    # by importing the module itself (#320 CI).
    import pydocs_mcp.extraction.strategies.embedders as product_embedders
    from pydocs_mcp.__main__ import main as pydocs_mcp_cli
    from pydocs_mcp.application.branch_pass import BranchPassOutcome
    from pydocs_mcp.retrieval.config import AppConfig
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

_DEFAULT_FILES = 200
_DEFAULT_CHANGED_PERCENTS = (1.0, 5.0, 20.0)
# Project source only, statically: the branch pass never touches dependencies,
# and indexing site-packages would bury the number this benchmark exists for.
_INDEX_FLAGS = ("--no-inspect", "--skip-deps")
# ``application/branch_pass.py`` logs one JSON line per pass on this logger.
_PRODUCT_LOGGER = "pydocs-mcp"
_BRANCH_REINDEX_EVENT = "branch_reindex"
_LINE_ONLY_FIELDS = frozenset({"event", "branch"})


@dataclass(frozen=True, slots=True)
class BranchReindexCostReport:
    """What ``index --branch feature/x`` cost against its diff."""

    files: int
    changed: int
    changed_percent: float
    # Wall time of each ``pydocs-mcp index`` run. The first run of a process
    # also pays its first-use costs (lazy imports, the grammar probe), and so
    # does the first report's ``--branch`` run, for the branch pass's own.
    seconds_first_branch: float
    # The control: a plain ``index`` re-run of the unchanged checkout. The
    # ``--branch`` run makes this same working-tree pass first; it re-parses
    # every file of ``main`` before its package gate, so it grows with the
    # project, not the diff (#320; the incremental working tree is P2.6).
    seconds_working_tree_rerun: float
    seconds_second_branch: float
    # ``seconds_second_branch`` less the control: the branch pass's own time.
    # It parses and embeds only the diff, but still writes the branch's whole
    # file list, membership and tree-tier rows (from the extraction cache), so
    # it has a project-sized floor of its own. A difference of two wall times:
    # it carries the noise of both.
    seconds_branch_pass: float
    # Texts the counting embedder received during the first and the
    # ``--branch`` run.
    embeddings_first_branch: int
    embeddings_second_branch: int
    # The ``--branch`` run's ``branch_reindex`` line.
    branch_reindex: BranchPassOutcome


@dataclass(slots=True)
class BenchmarkTextCountingEmbedder:
    """The ``Embedder`` shape with hash-derived vectors and a running text count."""

    dim: int
    # The configured spelling: the index stamps it as the bundle's embedder.
    model_name: str
    texts_embedded: int = 0

    async def embed_query(self, text: str) -> np.ndarray:
        return _hash_vector(text, self.dim)

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[np.ndarray, ...]:
        self.texts_embedded += len(texts)
        return tuple(_hash_vector(text, self.dim) for text in texts)

    def take_count(self) -> int:
        """The texts embedded since the last call; resets the count."""
        count, self.texts_embedded = self.texts_embedded, 0
        return count


@dataclass(frozen=True, slots=True)
class _IndexRun:
    seconds: float
    embeddings: int


@dataclass(frozen=True, slots=True)
class _IndexRuns:
    first: _IndexRun
    working_tree_rerun: _IndexRun
    second: _IndexRun
    # The ``--branch`` run's ``branch_reindex`` line.
    outcome: BranchPassOutcome


def changed_file_count(files: int, percent: float) -> int:
    """``percent`` of ``files`` as a file count; a non-zero share changes at least one."""
    if not 0 <= percent <= 100:
        raise ValueError(f"invalid diff size: got {percent}%, expected 0 to 100")
    return 0 if percent == 0 else max(1, round(files * percent / 100))


def run(*, files: int, changed: int, work_dir: Path) -> BranchReindexCostReport:
    """Build the repository under ``work_dir``, index ``main`` then ``feature/x``
    into a bundle beside it, and report the ``--branch`` run's cost."""
    _require_sizes(files, changed)
    repo, bundle_dir = work_dir / "repo", work_dir / "bundle"
    build_synthetic_branch_repository(repo, files=files, changed=changed)
    runs = _index_main_then_the_branch(repo, bundle_dir)
    control = runs.working_tree_rerun.seconds
    return BranchReindexCostReport(
        files=files,
        changed=changed,
        changed_percent=round(100 * changed / files, 2),
        seconds_first_branch=runs.first.seconds,
        seconds_working_tree_rerun=control,
        seconds_second_branch=runs.second.seconds,
        seconds_branch_pass=round(runs.second.seconds - control, 3),
        embeddings_first_branch=runs.first.embeddings,
        embeddings_second_branch=runs.second.embeddings,
        branch_reindex=runs.outcome,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        sizes = _diff_sizes(args)
    except ValueError as exc:
        parser.error(str(exc))
    _route_product_logs(verbose=args.verbose)
    reports = [_run_in_temp_dir(args.files, changed) for changed in sizes]
    print(json.dumps([asdict(report) for report in reports], indent=2))
    return 0


def _require_sizes(files: int, changed: int) -> None:
    if files < 1:
        raise ValueError(f"invalid repository size: got files={files}, expected at least 1")
    if not 0 <= changed <= files:
        raise ValueError(
            f"invalid diff size: got changed={changed}, expected 0 <= changed <= files={files}"
        )


def _index_main_then_the_branch(repo: Path, bundle_dir: Path) -> _IndexRuns:
    """``index``, the ``index`` control re-run, then ``index --branch feature/x``."""
    config = AppConfig.load().embedding
    embedder = BenchmarkTextCountingEmbedder(dim=config.dim, model_name=config.model_name)
    with _embedder_installed(embedder):
        first = _index(embedder, repo, bundle_dir)
        control = _index(embedder, repo, bundle_dir)
        second, outcome = _index_the_branch(embedder, repo, bundle_dir)
    return _IndexRuns(first, control, second, outcome)


def _index_the_branch(
    embedder: BenchmarkTextCountingEmbedder, repo: Path, bundle_dir: Path
) -> tuple[_IndexRun, BranchPassOutcome]:
    # Collect around this run only: a working-tree pass that logs a
    # branch_reindex line of its own must not count as the branch's.
    with _branch_reindex_payloads() as payloads:
        second = _index(embedder, repo, bundle_dir, "--branch", FEATURE_BRANCH)
    return second, _single_outcome(payloads)


def _index(
    embedder: BenchmarkTextCountingEmbedder, repo: Path, bundle_dir: Path, *flags: str
) -> _IndexRun:
    argv = ["pydocs-mcp", "index", str(repo), *_INDEX_FLAGS, "--cache-dir", str(bundle_dir), *flags]
    started = time.perf_counter()
    with _argv(argv):
        exit_code = pydocs_mcp_cli()
    seconds = round(time.perf_counter() - started, 3)
    if exit_code != 0:
        raise RuntimeError(f"`{' '.join(argv)}` exited {exit_code}; rerun it with -v")
    return _IndexRun(seconds, embedder.take_count())


def _single_outcome(payloads: Sequence[dict[str, object]]) -> BranchPassOutcome:
    """The one branch pass the ``--branch`` run made, from its log line."""
    if len(payloads) != 1:
        raise RuntimeError(
            f"expected one {_BRANCH_REINDEX_EVENT!r} line from `index --branch "
            f"{FEATURE_BRANCH}`, got {len(payloads)}: {list(payloads)!r}"
        )
    counts = {k: v for k, v in payloads[0].items() if k not in _LINE_ONLY_FIELDS}
    return BranchPassOutcome(**counts)


def _hash_vector(text: str, dim: int) -> np.ndarray:
    """A deterministic unit-range vector per text (the ``MockEmbedder`` recipe)."""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
    return np.random.default_rng(seed).uniform(-1.0, 1.0, size=dim).astype(np.float32)


@contextmanager
def _embedder_installed(embedder: BenchmarkTextCountingEmbedder) -> Iterator[None]:
    """Every ``build_embedder`` call the index path makes returns ``embedder``."""
    original = product_embedders.build_embedder
    product_embedders.build_embedder = lambda _config: embedder
    try:
        yield
    finally:
        product_embedders.build_embedder = original


@contextmanager
def _argv(argv: list[str]) -> Iterator[None]:
    """``sys.argv`` for the product CLI, whose ``main()`` takes no arguments."""
    original = sys.argv
    sys.argv = argv
    try:
        yield
    finally:
        sys.argv = original


class _BranchReindexLineHandler(logging.Handler):
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        super().__init__(level=logging.INFO)
        self.payloads = payloads

    def emit(self, record: logging.LogRecord) -> None:
        payload = _branch_reindex_payload(record.getMessage())
        if payload is not None:
            self.payloads.append(payload)


def _branch_reindex_payload(message: str) -> dict[str, object] | None:
    if not message.startswith("{"):
        return None
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None
    return payload if payload.get("event") == _BRANCH_REINDEX_EVENT else None


@contextmanager
def _branch_reindex_payloads() -> Iterator[list[dict[str, object]]]:
    """Collect every ``branch_reindex`` payload the product logs meanwhile."""
    payloads: list[dict[str, object]] = []
    handler = _BranchReindexLineHandler(payloads)
    logger = logging.getLogger(_PRODUCT_LOGGER)
    previous_level = logger.level
    # The line is INFO: a quieter root (a caller's own logging setup) must not drop it.
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield payloads
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _route_product_logs(*, verbose: bool) -> None:
    """One stderr handler, WARNING unless ``verbose``: the product's own
    ``basicConfig`` then finds a handler and leaves stdout to the report."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO if verbose else logging.WARNING)
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _run_in_temp_dir(files: int, changed: int) -> BranchReindexCostReport:
    with tempfile.TemporaryDirectory(prefix="branch_reindex_cost-") as work_dir:
        return run(files=files, changed=changed, work_dir=Path(work_dir))


def _diff_sizes(args: argparse.Namespace) -> list[int]:
    """The ``--changed`` counts, else ``--changed-percent`` of ``--files``; checked."""
    if args.changed is not None:
        sizes = list(args.changed)
    else:
        sizes = [changed_file_count(args.files, p) for p in args.changed_percent]
    for changed in sizes:
        _require_sizes(args.files, changed)
    return sizes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pydocs_eval.micro.branch_reindex_cost",
        description="What `pydocs-mcp index --branch` costs against the branch's diff.",
    )
    parser.add_argument(
        "--files", type=int, default=_DEFAULT_FILES, help="modules on main (default: %(default)s)"
    )
    _add_diff_size_arguments(parser)
    parser.add_argument("-v", "--verbose", action="store_true", help="show the index logs")
    return parser


def _add_diff_size_arguments(parser: argparse.ArgumentParser) -> None:
    """``--changed N …`` or ``--changed-percent P …``: one report per value."""
    sizes = parser.add_mutually_exclusive_group()
    sizes.add_argument("--changed", type=int, nargs="+", metavar="N", help="modules to edit")
    percents = " ".join(f"{percent:g}" for percent in _DEFAULT_CHANGED_PERCENTS)
    sizes.add_argument(
        "--changed-percent",
        type=float,
        nargs="+",
        metavar="P",
        default=_DEFAULT_CHANGED_PERCENTS,
        help=f"share of the modules to edit, in percent (default: {percents})",
    )


__all__ = (
    "BenchmarkTextCountingEmbedder",
    "BranchReindexCostReport",
    "changed_file_count",
    "main",
    "run",
)


if __name__ == "__main__":
    raise SystemExit(main())
