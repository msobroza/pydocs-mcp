"""Composition tables + committed-artifact serialization for the dev/val splits (ADR 0013).

The composition tables are a first-class split-script deliverable (ADR 0013 §Decision):
per-repo counts before and after the dev cap, plus difficulty and year distributions per
split. This module also renders the instance-ID lists and the split-config manifest (seed,
params, realized counts, and the file hashes the campaign lockfile stamps).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from .records import LiveRecord
from .splits import RepoInfo, SplitResult


def render_instance_list(instance_ids: tuple[str, ...]) -> str:
    """One sorted instance ID per line — the committed ``dev.txt`` / ``val.txt`` body."""
    return "\n".join(sorted(instance_ids)) + "\n"


def sha256_text(text: str) -> str:
    """Hex digest of a text artifact (for the split-config + lockfile stamps)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_split_config(result: SplitResult) -> str:
    """Serialize the seed, params, realized counts, and file hashes as JSON."""
    dev_txt = render_instance_list(result.dev_instances)
    val_txt = render_instance_list(result.val_instances)
    payload = {
        "config": result.config.to_dict(),
        "realized": {
            "dev_instances": len(result.dev_instances),
            "val_instances": len(result.val_instances),
            "dev_repos": len(result.dev_repos),
            "val_repos": len(result.val_repos),
            "dev_val_ratio": round(result.dev_val_ratio, 4),
            "org_excluded_instances": len(result.excluded_instances),
        },
        "hashes": {
            "dev.txt": sha256_text(dev_txt),
            "val.txt": sha256_text(val_txt),
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_dev_composition(result: SplitResult, records: list[LiveRecord]) -> str:
    """Dev composition: per-repo assigned-vs-contributed (the cap) + difficulty/year mix."""
    dev_ids = set(result.dev_instances)
    lines = [
        "# Dev split composition",
        "",
        f"- Realized dev instances: **{len(result.dev_instances)}** "
        f"across **{len(result.dev_repos)}** repos",
        f"- Dev:val instance ratio: **{result.dev_val_ratio:.2f}:1**",
        "",
        "## Per-repo contribution (assigned → contributed after 10% cap)",
        "",
        "| Repo | Size class | Assigned | Contributed | Capped |",
        "|---|---|---|---|---|",
    ]
    lines.extend(_contribution_rows(result))
    lines.append("")
    lines.extend(_distribution_section(dev_ids, records))
    return "\n".join(lines) + "\n"


def render_val_composition(result: SplitResult, records: list[LiveRecord]) -> str:
    """Val composition: per-repo counts (uncapped) + difficulty/year mix."""
    val_ids = set(result.val_instances)
    lines = [
        "# Val split composition",
        "",
        f"- Val instances: **{len(result.val_instances)}** "
        f"across **{len(result.val_repos)}** repos",
        "",
        "## Per-repo counts",
        "",
        "| Repo | Size class | Instances |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| `{info.repo}` | {info.size_class} | {info.size} |" for info in result.val_repos
    )
    lines.append("")
    lines.extend(_distribution_section(val_ids, records))
    return "\n".join(lines) + "\n"


def _contribution_rows(result: SplitResult) -> list[str]:
    by_repo = {info.repo: info for info in result.dev_repos}
    rows: list[str] = []
    for repo in sorted(result.dev_contribution):
        assigned, contributed = result.dev_contribution[repo]
        capped = "yes" if contributed < assigned else "no"
        size_class = _size_class_of(by_repo.get(repo))
        rows.append(f"| `{repo}` | {size_class} | {assigned} | {contributed} | {capped} |")
    return rows


def _size_class_of(info: RepoInfo | None) -> str:
    return info.size_class if info is not None else "?"


def _distribution_section(instance_ids: set[str], records: list[LiveRecord]) -> list[str]:
    subset = [r for r in records if r.instance_id in instance_ids]
    files = Counter("1 file" if r.difficulty_files == 1 else ">1 file" for r in subset)
    years = Counter(r.created_at_year for r in subset)
    lines = ["## difficulty.files distribution", "", "| Class | Instances |", "|---|---|"]
    lines.extend(f"| {label} | {files[label]} |" for label in sorted(files))
    lines.extend(["", "## created_at year distribution", "", "| Year | Instances |", "|---|---|"])
    lines.extend(f"| {year} | {years[year]} |" for year in sorted(years))
    lines.append("")
    return lines
