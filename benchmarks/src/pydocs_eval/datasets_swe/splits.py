"""Deterministic, seeded, repo-disjoint dev/val split construction (ADR 0013).

The pinned split rule, in order:

1. **Org-level R2 exclusion first** — drop any Live instance whose org appears in the
   frozen Pro-Python test set (ADR 0013 §Decision).
2. **Whole-repo assignment** — every repo goes to exactly ONE split (repo-disjointness),
   drawn by a seeded RNG, stratified on {repo size class, ``difficulty.files`` 1-vs->1,
   ``created_at`` year} so both splits carry comparable profiles, targeting a dev:val
   instance proportion of ~2:1.
3. **Dev-side 10% per-repo contribution cap** — no repo may supply more than 10% of the
   realized dev list; an over-cap repo contributes a seeded subsample and its excess
   instances are left UNUSED (never spilled into val, which would break disjointness).

Pure over ``(records, pro_python_repos, config)`` and deterministic for a fixed seed, so
a re-run produces byte-identical split files (verified by a determinism test).
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from .overlap import excluded_instance_ids
from .records import LiveRecord

# Repo size classes by instance count (datasets-overlap §2: ~90 singletons, heavy head
# at 165/109/102). Single source of truth for the bucket edges.
_SIZE_CLASS_EDGES: tuple[tuple[int, str], ...] = (
    (1, "singleton"),
    (5, "small"),
    (20, "medium"),
)
_SIZE_CLASS_LARGE = "large"


@dataclass(frozen=True, slots=True)
class SplitConfig:
    """Seed + partition parameters, serialized into the committed split-config artifact."""

    seed: int = 20260720
    # ~2:1 dev:val at instance level (ADR 0013). 2/3 of instances target dev.
    dev_fraction: float = 2.0 / 3.0
    # No dev repo may supply more than this fraction of the realized dev list.
    dev_contribution_cap_frac: float = 0.10

    def to_dict(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "dev_fraction": self.dev_fraction,
            "dev_contribution_cap_frac": self.dev_contribution_cap_frac,
            "size_class_edges": [list(e) for e in _SIZE_CLASS_EDGES],
        }


@dataclass(frozen=True, slots=True)
class RepoInfo:
    """One repo's instances + its stratification signature."""

    repo: str
    instance_ids: tuple[str, ...]  # sorted — the stable base for seeded subsampling
    size_class: str
    files_class: str
    modal_year: int

    @property
    def size(self) -> int:
        return len(self.instance_ids)

    @property
    def stratum(self) -> tuple[str, str, int]:
        return (self.size_class, self.files_class, self.modal_year)


@dataclass(frozen=True, slots=True)
class SplitResult:
    """The realized dev/val partition + provenance for the composition tables."""

    dev_instances: tuple[str, ...]
    val_instances: tuple[str, ...]
    dev_repos: tuple[RepoInfo, ...]
    val_repos: tuple[RepoInfo, ...]
    # repo -> (assigned_count, contributed_count); contributed < assigned iff capped.
    dev_contribution: dict[str, tuple[int, int]] = field(default_factory=dict)
    excluded_instances: tuple[str, ...] = ()
    config: SplitConfig = field(default_factory=SplitConfig)

    @property
    def dev_val_ratio(self) -> float:
        return len(self.dev_instances) / len(self.val_instances) if self.val_instances else 0.0


def build_splits(
    records: list[LiveRecord],
    pro_python_repos: list[str],
    config: SplitConfig | None = None,
) -> SplitResult:
    """Construct the fixed dev/val partition per the pinned ADR 0013 rule."""
    cfg = config or SplitConfig()
    excluded = excluded_instance_ids(records, pro_python_repos)
    kept = [r for r in records if r.instance_id not in excluded]
    infos = _repo_infos(kept)
    rng = random.Random(cfg.seed)
    dev_repos, val_repos = _assign_repos(infos, cfg, rng)
    dev_instances, contribution = _apply_dev_cap(dev_repos, cfg, rng)
    val_instances = sorted(iid for info in val_repos for iid in info.instance_ids)
    return SplitResult(
        dev_instances=tuple(sorted(dev_instances)),
        val_instances=tuple(val_instances),
        dev_repos=tuple(sorted(dev_repos, key=lambda i: i.repo)),
        val_repos=tuple(sorted(val_repos, key=lambda i: i.repo)),
        dev_contribution=contribution,
        excluded_instances=tuple(sorted(excluded)),
        config=cfg,
    )


def _repo_infos(records: list[LiveRecord]) -> list[RepoInfo]:
    """Group records into per-repo :class:`RepoInfo` with stratification signatures."""
    by_repo: dict[str, list[LiveRecord]] = {}
    for record in records:
        by_repo.setdefault(record.repo, []).append(record)
    return [_repo_info(repo, rows) for repo, rows in by_repo.items()]


def _repo_info(repo: str, rows: list[LiveRecord]) -> RepoInfo:
    ids = tuple(sorted(r.instance_id for r in rows))
    single = sum(1 for r in rows if r.difficulty_files == 1)
    files_class = "single_file" if single * 2 > len(rows) else "multi_file"
    return RepoInfo(
        repo=repo,
        instance_ids=ids,
        size_class=_size_class(len(rows)),
        files_class=files_class,
        modal_year=_modal_year(rows),
    )


def _size_class(count: int) -> str:
    for edge, label in _SIZE_CLASS_EDGES:
        if count <= edge:
            return label
    return _SIZE_CLASS_LARGE


def _modal_year(rows: list[LiveRecord]) -> int:
    """Most common ``created_at`` year; ties broken toward the earliest year."""
    counts = Counter(r.created_at_year for r in rows)
    top = max(counts.values())
    return min(year for year, n in counts.items() if n == top)


def _assign_repos(
    infos: list[RepoInfo],
    cfg: SplitConfig,
    rng: random.Random,
) -> tuple[list[RepoInfo], list[RepoInfo]]:
    """Assign whole repos to dev/val per stratum, targeting ~``dev_fraction`` instances.

    Strata are iterated in sorted order and repos shuffled with the shared seeded RNG, so
    the draw sequence — and therefore membership — is byte-identical across runs.
    """
    by_stratum: dict[tuple[str, str, int], list[RepoInfo]] = {}
    for info in infos:
        by_stratum.setdefault(info.stratum, []).append(info)
    dev: list[RepoInfo] = []
    val: list[RepoInfo] = []
    for stratum in sorted(by_stratum):
        repos = sorted(by_stratum[stratum], key=lambda i: i.repo)
        rng.shuffle(repos)
        _assign_one_stratum(repos, cfg, dev, val)
    return dev, val


def _assign_one_stratum(
    repos: list[RepoInfo],
    cfg: SplitConfig,
    dev: list[RepoInfo],
    val: list[RepoInfo],
) -> None:
    """Fill a stratum's repos toward the ~``dev_fraction`` instance target, rest to val.

    Uses a midpoint (``+ size/2``) rule so the boundary repo lands on whichever side keeps
    the realized dev fraction CLOSEST to the target — a plain "add until crossing" rule
    always overshoots dev, inflating the realized dev:val ratio above the ~2:1 target.
    """
    target = cfg.dev_fraction * sum(info.size for info in repos)
    dev_count = 0
    for info in repos:
        if dev_count + info.size / 2 <= target:
            dev.append(info)
            dev_count += info.size
        else:
            val.append(info)


def _apply_dev_cap(
    dev_repos: list[RepoInfo],
    cfg: SplitConfig,
    rng: random.Random,
) -> tuple[list[str], dict[str, tuple[int, int]]]:
    """Cap each dev repo's contribution; seeded-subsample the over-cap ones, excess unused."""
    counts = [info.size for info in dev_repos]
    cap = _dev_cap(counts, cfg.dev_contribution_cap_frac)
    instances: list[str] = []
    contribution: dict[str, tuple[int, int]] = {}
    for info in sorted(dev_repos, key=lambda i: i.repo):
        chosen = _subsample(info.instance_ids, cap, rng)
        contribution[info.repo] = (info.size, len(chosen))
        instances.extend(chosen)
    return instances, contribution


def _dev_cap(counts: list[int], frac: float) -> int:
    """Largest per-repo cap ``C`` with ``C <= frac * realized_dev(C)`` (a monotone fixpoint).

    Starts uncapped and lowers the cap until the constraint holds, so no dev repo can
    exceed ``frac`` of the realized (post-subsample) dev list — the ADR's "10% of the
    realized dev list" reading, solved exactly rather than against a pre-cap total.
    """
    total = sum(counts)
    cap = total
    for _ in range(len(counts) + 2):
        realized = sum(min(c, cap) for c in counts)
        new_cap = int(frac * realized)
        if new_cap >= cap:
            return cap
        cap = new_cap
    return cap


def _subsample(instance_ids: tuple[str, ...], cap: int, rng: random.Random) -> list[str]:
    """Seeded subsample of at most ``cap`` ids from a sorted id tuple (excess unused)."""
    if len(instance_ids) <= cap:
        return list(instance_ids)
    pool = list(instance_ids)  # already sorted → stable base for the seeded draw
    rng.shuffle(pool)
    return sorted(pool[:cap])
