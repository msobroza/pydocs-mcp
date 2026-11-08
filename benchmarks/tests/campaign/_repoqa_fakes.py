"""A synthetic RepoQA release shaped like the pinned one: 10 repos x 10 needles.

The real release (71 MB) never enters a test. This builder writes a release-
shaped JSON with the same per-repo needle count, so ``dev`` holds 2 needles per
repo, ``test`` 8 and ``small_test`` 3 — the proportions the fresh draw is
pinned against — and points the registry's ``repoqa`` at it.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from pydocs_eval.datasets.repoqa import RepoQADataset
from pydocs_eval.registries import dataset_registry

SYNTHETIC_REPOS = 10
SYNTHETIC_NEEDLES_PER_REPO = 10


def _needle_file(index: int) -> str:
    # A blank line, then the def: the release's 0-based ``start_line`` names the def.
    return f"\ndef needle_{index:02d}(value):\n    return value + {index}\n"


def _repo_entry(repo: int) -> dict[str, object]:
    paths = [f"pkg/module_{n:02d}.py" for n in range(SYNTHETIC_NEEDLES_PER_REPO)]
    return {
        "repo": f"owner{repo:02d}/repo{repo:02d}",
        "commit_sha": f"{repo:02d}" + "a" * 38,
        "topic": "synthetic",
        "content": {path: _needle_file(n) for n, path in enumerate(paths)},
        "needles": [
            {
                "path": path,
                "name": f"needle_{n:02d}",
                "start_line": 1,
                "end_line": 3,
                "description": f"Adds {n} to its argument (repo {repo}).",
            }
            for n, path in enumerate(paths)
        ],
    }


def write_synthetic_repoqa_release(path: Path) -> Path:
    """Write the synthetic release to ``path`` and return it."""
    payload = {"python": [_repo_entry(repo) for repo in range(SYNTHETIC_REPOS)]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def use_synthetic_repoqa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Every registry build of ``repoqa`` reads the synthetic release; returns its path."""
    release = write_synthetic_repoqa_release(tmp_path / "repoqa_synthetic.json")
    fixture_backed = partial(RepoQADataset, fixture_path=release)
    monkeypatch.setitem(dataset_registry._items, "repoqa", fixture_backed)  # type: ignore[arg-type]
    return release


def write_exclusion_file(path: Path, task_ids: list[str], *, provenance: str = "test") -> Path:
    """An exclusion file in the committed format, holding ``task_ids``."""
    payload = {"provenance": provenance, "task_ids": sorted(task_ids)}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
