"""Per-campaign index pre-build — canonical checkouts indexed once (ADR 0014 item 3).

Runs over the campaign's INSTANCE LIST (not the whole 1,888-row snapshot, which
would burn days of embedding on untouched instances — ADR 0014 option (iii)
folded in). Each distinct ``(repo, base_commit)`` becomes one canonical checkout,
indexed once (project-only); the step is idempotent over already-built slots
(``create_checkout`` skips an existing ``.git`` and ``index_checkout`` skips an
existing ``.db``), so a resumed pre-build never re-clones or re-embeds.

The ``(repo, base_commit, clone_url)`` per instance is loaded from a manifest
(one JSON object per line) rather than re-deriving it from the pinned HF snapshot
here — that keeps this module offline-testable with synthetic specs and injected
git/index seams, and lets the network dataset load stay in ``datasets_swe``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.campaign.index_cache import create_checkout, index_checkout


@dataclass(frozen=True, slots=True)
class InstanceSpec:
    """One instance's repo coordinates for the index pre-build.

    ``clone_url`` is where ``create_checkout`` clones from (a remote URL on the
    host; a local path in offline tests). Many instances share one ``(repo,
    base_commit)`` — the pre-build dedupes on that pair.
    """

    instance_id: str
    repo: str
    base_commit: str
    clone_url: str

    @property
    def checkout_key(self) -> tuple[str, str]:
        return (self.repo, self.base_commit)


def load_instance_manifest(path: Path) -> list[InstanceSpec]:
    """Parse a JSONL instance manifest into :class:`InstanceSpec` rows.

    Raises:
        ValueError: on a line missing a required key — a malformed manifest is a
            launch-blocking defect, named with the offending line number.
    """
    specs: list[InstanceSpec] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        specs.append(_spec_from_line(stripped, lineno))
    return specs


def _spec_from_line(line: str, lineno: int) -> InstanceSpec:
    try:
        raw = json.loads(line)
        return InstanceSpec(
            instance_id=str(raw["instance_id"]),
            repo=str(raw["repo"]),
            base_commit=str(raw["base_commit"]),
            clone_url=str(raw["clone_url"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            f"instance manifest line {lineno}: {exc}; expected keys "
            "instance_id, repo, base_commit, clone_url"
        ) from exc


def distinct_checkouts(specs: Sequence[InstanceSpec]) -> list[InstanceSpec]:
    """One representative :class:`InstanceSpec` per distinct ``(repo, base_commit)``.

    Deterministic (sorted by key), so the pre-build order is stable across runs.
    """
    by_key: dict[tuple[str, str], InstanceSpec] = {}
    for spec in specs:
        by_key.setdefault(spec.checkout_key, spec)
    return [by_key[key] for key in sorted(by_key)]


def prebuild_index(
    specs: Sequence[InstanceSpec],
    *,
    cache_root: Path,
    python: Path,
    shallow: bool = False,
    git: Callable[[list[str]], None] | None = None,
    index_fn: Callable[[Path, Path], tuple[Path, Path]] | None = None,
) -> dict[tuple[str, str], tuple[Path, Path]]:
    """Create + index one canonical checkout per distinct ``(repo, base_commit)``.

    Returns ``{(repo, commit): (db_path, tq_path)}``. Idempotent over built slots.
    ``git`` / ``index_fn`` are the injectable seams (default: real git subprocess
    + shipped index CLI); offline tests pass a fake git and
    :func:`index_project_in_process`.
    """
    built: dict[tuple[str, str], tuple[Path, Path]] = {}
    for spec in distinct_checkouts(specs):
        checkout = create_checkout(
            cache_root,
            repo=spec.repo,
            commit=spec.base_commit,
            clone_url=spec.clone_url,
            shallow=shallow,
            git=git,
        )
        built[spec.checkout_key] = index_checkout(
            checkout, python=python, cache_root=cache_root, index_fn=index_fn
        )
    return built
