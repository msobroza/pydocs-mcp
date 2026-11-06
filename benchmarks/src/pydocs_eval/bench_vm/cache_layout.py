"""Where the persisted caches live: under the fixed cache directory, and in the cache bucket.

**In the container.** The child runs with ``HOME`` at ``<cache dir>/home``, so
every cache the eval suite keeps under ``~`` lands at a fixed path on the cache
mount, the same on every VM: the bench index cache and the answer judge's
answers (``~/.pydocs-mcp/bench``, ``_bench_cache``), and the dataset and
repository downloads (``~/.cache/pydocs-mcp`` and ``~/.cache/pydocs-eval``, the
dataset loaders' roots). ``HF_HOME`` and ``FASTEMBED_CACHE_PATH`` are set
explicitly, so the embedder weights land there too (fastembed's own default is
a temporary directory). Before/after's workspaces are already under the cache
directory through its owned ``--workspace`` flag.

**In the bucket**, partitioned by dataset, plus one shared partition::

    shared/huggingface/  shared/fastembed/                  embedder weights (mirrors)
    datasets/<dataset>/downloads/{pydocs-mcp,pydocs-eval}/  dataset and repository downloads
    datasets/<dataset>/judge/                               answer-judge cache, one file per request digest
    datasets/<dataset>/bench-index/<deps partition>/<key>.tar   reference-project index entries
    datasets/<dataset>/before-after/<bundle>.tar            before/after corpora

A reference-project index covers the installed dependencies, which its cache
key does not see, so its partition is named by the image's ``uv.lock`` digest
and flavour: the CPU image carries no torch, the CUDA image does.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote

from pydocs_eval._bench_cache import CACHE_ROOT_UNDER_HOME
from pydocs_eval.bench_vm.kind_flags import BEFORE_AFTER_WORKSPACE_DIR
from pydocs_eval.campaign.before_after_corpora import TASK_WORKSPACE_ROOT_NAME
from pydocs_eval.judge.jev_cache import JEV_CACHE_SUBDIR

CHILD_HOME_DIR_NAME = "home"
UV_LOCK_NAME = "uv.lock"
# The partition of a run spec without a dataset (the CLI's own default dataset).
DEFAULT_DATASET_PARTITION = "default"
_DEPS_DIGEST_CHARS = 16
_SHARED_PREFIX = "shared/"
_DATASETS_PREFIX = "datasets/"
_DOT_CACHE = Path(".cache")
# Download tools and atomic writers keep these while a file is in flight.
CACHE_MIRROR_EXCLUDE_PATTERN = (
    r".*(\.tmp|\.partial|\.incomplete|\.lock|-journal|~)$|.*\.tmp/.*|(.*/)?\.locks/.*"
)


class ArchiveShelf(StrEnum):
    """Where index entries are kept as one archive each, by producer."""

    BENCH_INDEX = "bench-index"
    BEFORE_AFTER = "before-after"


@dataclass(frozen=True, slots=True)
class CacheMirror:
    """A download cache, mirrored as a plain directory: ``local_dir`` ⇄ ``uri``."""

    local_dir: Path
    uri: str


@dataclass(frozen=True, slots=True)
class CacheLayout:
    """One run's cache paths and partitions."""

    cache_dir: Path
    bucket: str
    dataset: str | None
    # ``uv-lock-<digest>-<flavour>``, ``None`` when the image has no uv.lock.
    deps_partition: str | None

    @property
    def home(self) -> Path:
        return self.cache_dir / CHILD_HOME_DIR_NAME

    @property
    def bench_index_root(self) -> Path:
        return self.home / CACHE_ROOT_UNDER_HOME

    @property
    def task_workspaces_root(self) -> Path:
        return self.cache_dir / BEFORE_AFTER_WORKSPACE_DIR / TASK_WORKSPACE_ROOT_NAME

    @property
    def shared_uri(self) -> str:
        return f"gs://{self.bucket}/{_SHARED_PREFIX}"

    @property
    def dataset_uri(self) -> str:
        partition = quote(self.dataset or DEFAULT_DATASET_PARTITION, safe="")
        return f"gs://{self.bucket}/{_DATASETS_PREFIX}{partition}/"

    def shelf_uri(self, shelf: ArchiveShelf) -> str | None:
        """The prefix one shelf's archives sit under; ``None`` when it has no partition."""
        if shelf is ArchiveShelf.BEFORE_AFTER:
            return f"{self.dataset_uri}{shelf}/"
        if self.deps_partition is None:
            return None
        return f"{self.dataset_uri}{shelf}/{self.deps_partition}/"

    def mirrors(self) -> tuple[CacheMirror, ...]:
        environment = child_cache_environment(self.cache_dir)
        downloads = self.home / _DOT_CACHE
        return (
            CacheMirror(Path(environment["HF_HOME"]), self.shared_uri + "huggingface/"),
            CacheMirror(Path(environment["FASTEMBED_CACHE_PATH"]), self.shared_uri + "fastembed/"),
            CacheMirror(downloads / "pydocs-mcp", self.dataset_uri + "downloads/pydocs-mcp/"),
            CacheMirror(downloads / "pydocs-eval", self.dataset_uri + "downloads/pydocs-eval/"),
            CacheMirror(self.bench_index_root / JEV_CACHE_SUBDIR, self.dataset_uri + "judge/"),
        )


def child_cache_environment(cache_dir: Path) -> dict[str, str]:
    """The variables that put the child's caches under the fixed cache directory."""
    home = cache_dir / CHILD_HOME_DIR_NAME
    return {
        "HOME": str(home),
        "HF_HOME": str(home / _DOT_CACHE / "huggingface"),
        "FASTEMBED_CACHE_PATH": str(home / _DOT_CACHE / "fastembed"),
    }


def deps_partition_name(repo_dir: Path, flavour: str) -> str | None:
    """``uv-lock-<first 16 hex of sha256(uv.lock)>-<flavour>``; ``None`` without a uv.lock."""
    try:
        lock = (repo_dir / UV_LOCK_NAME).read_bytes()
    except OSError:
        return None
    return f"uv-lock-{hashlib.sha256(lock).hexdigest()[:_DEPS_DIGEST_CHARS]}-{flavour}"
