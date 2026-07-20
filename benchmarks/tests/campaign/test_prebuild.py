"""Pre-build: manifest parse, (repo,commit) dedupe, idempotent slot builds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.campaign.prebuild import (
    InstanceSpec,
    distinct_checkouts,
    load_instance_manifest,
    prebuild_index,
)


def _manifest(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_load_manifest_parses_rows(tmp_path) -> None:
    path = _manifest(
        tmp_path / "m.jsonl",
        [{"instance_id": "i1", "repo": "o/r", "base_commit": "abc", "clone_url": "u"}],
    )
    specs = load_instance_manifest(path)
    assert specs == [InstanceSpec("i1", "o/r", "abc", "u")]


def test_load_manifest_bad_line_raises_with_lineno(tmp_path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text('{"instance_id": "i1"}\n')  # missing keys
    with pytest.raises(ValueError, match="line 1"):
        load_instance_manifest(path)


def test_distinct_checkouts_dedupes_by_repo_commit() -> None:
    specs = [
        InstanceSpec("i1", "o/r", "abc", "u"),
        InstanceSpec("i2", "o/r", "abc", "u"),  # same (repo, commit) as i1
        InstanceSpec("i3", "o/r", "def", "u"),
    ]
    distinct = distinct_checkouts(specs)
    assert [s.checkout_key for s in distinct] == [("o/r", "abc"), ("o/r", "def")]


def test_prebuild_builds_one_slot_per_distinct_checkout(tmp_path) -> None:
    specs = [
        InstanceSpec("i1", "o/r", "abc", "url-a"),
        InstanceSpec("i2", "o/r", "abc", "url-a"),
        InstanceSpec("i3", "o/r", "def", "url-a"),
    ]
    checked_out: list = []
    indexed: list = []

    def _fake_git(cmd: list[str]) -> None:
        # Simulate a clone by creating the .git dir the checkout would produce.
        if cmd[:2] == ["git", "clone"]:
            Path(cmd[-1], ".git").mkdir(parents=True)
        checked_out.append(cmd)

    def _fake_index(checkout: Path, cache_root: Path) -> tuple[Path, Path]:
        db = cache_root / f"{checkout.name}.db"
        db.write_bytes(b"idx")
        indexed.append(checkout.name)
        return db, db.with_suffix(".tq")

    built = prebuild_index(
        specs, cache_root=tmp_path, python=Path("/py"), git=_fake_git, index_fn=_fake_index
    )
    assert set(built) == {("o/r", "abc"), ("o/r", "def")}
    assert sorted(indexed) == ["o__r@abc", "o__r@def"]  # one index per distinct checkout


def test_prebuild_empty_manifest_builds_nothing(tmp_path) -> None:
    built = prebuild_index([], cache_root=tmp_path, python=Path("/py"))
    assert built == {}
