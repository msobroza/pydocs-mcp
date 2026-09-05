"""Index cache: exact flags (pin), path derivation, pre-seed, real git + indexer."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pydocs_eval.campaign.index_cache import (
    _INDEX_FLAGS,
    _SCOPE_ID_LEN,
    build_index_command,
    canonical_checkout_dir,
    canonical_index_paths,
    create_checkout,
    index_checkout,
    index_project_in_process,
    preseed_workspace,
    repo_slug,
    resolve_scope_id,
    workspace_cache_paths,
)


def test_repo_slug_replaces_slash() -> None:
    assert repo_slug("conan-io/conan") == "conan-io__conan"


def test_repo_slug_rejects_unslashed() -> None:
    with pytest.raises(ValueError, match="expected 'owner/name'"):
        repo_slug("noslash")


def test_canonical_checkout_dir_shape(tmp_path) -> None:
    # ADR 0021 6: the scope_id rides the slug after the commit.
    d = canonical_checkout_dir(tmp_path, "o/r", "abc123", scope_id="sc0pe")
    assert d == tmp_path / "o__r@abc123@sc0pe"


def test_canonical_checkout_dir_default_scope_from_product(tmp_path) -> None:
    # Default scope_id derives from the active product pipeline identity — a
    # non-empty fixed-length hex slug appended after the commit.
    expected = resolve_scope_id(None)
    d = canonical_checkout_dir(tmp_path, "o/r", "abc123")
    assert d.name == f"o__r@abc123@{expected}"
    assert len(expected) == _SCOPE_ID_LEN


def test_resolve_scope_id_passthrough() -> None:
    # An explicit scope_id is used verbatim (no product read).
    assert resolve_scope_id("explicit") == "explicit"


def test_different_scope_ids_get_distinct_buildable_slots(tmp_path) -> None:
    # CRITICAL (ADR 0021 6): same repo@commit under two scopes → two checkout
    # dirs → two db slots, so index_checkout's db.exists() short-circuit can
    # never reuse one scope's index for the other. Both build side by side.
    on = canonical_checkout_dir(tmp_path, "o/r", "abc", scope_id="on")
    off = canonical_checkout_dir(tmp_path, "o/r", "abc", scope_id="off")
    assert on != off

    built: list[Path] = []

    def _fake_index(checkout: Path, root: Path) -> tuple[Path, Path]:
        db, tq = canonical_index_paths(checkout, root)
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"idx")
        built.append(db)
        return db, tq

    for d in (on, off):
        d.mkdir(parents=True)
        index_checkout(d, python=Path("/py"), cache_root=tmp_path, index_fn=_fake_index)

    db_on, _ = canonical_index_paths(on, tmp_path)
    db_off, _ = canonical_index_paths(off, tmp_path)
    assert db_on != db_off  # distinct slots
    assert db_on.exists() and db_off.exists()  # both built side by side
    assert built == [db_on, db_off]  # neither short-circuited the other's build


def test_build_index_command_pins_exact_flags(tmp_path) -> None:
    cmd = build_index_command(tmp_path / "c", Path("/py"), tmp_path / "root")
    # ADR 0014 pins project-only + no-inspect: guard against a silent flag rename.
    assert _INDEX_FLAGS == ("--skip-deps", "--no-inspect")
    assert cmd[:4] == ["/py", "-m", "pydocs_mcp", "index"]
    assert "--skip-deps" in cmd and "--no-inspect" in cmd
    assert cmd[-2:] == ["--cache-dir", str(tmp_path / "root")]


def test_canonical_index_paths_match_product_slug(tmp_path) -> None:
    from pydocs_mcp.db import cache_path_for_project

    checkout = tmp_path / "o__r@abc"
    checkout.mkdir()
    db, tq = canonical_index_paths(checkout, tmp_path / "root")
    assert db.name == cache_path_for_project(checkout).name  # product slug preserved
    assert db.parent == tmp_path / "root"
    assert tq == db.with_suffix(".tq")


def test_index_checkout_skips_already_built(tmp_path) -> None:
    checkout = tmp_path / "o__r@abc"
    checkout.mkdir()
    db, _tq = canonical_index_paths(checkout, tmp_path)
    db.write_bytes(b"prebuilt")
    calls: list = []

    def _never(_d, _r):  # index_fn must not run when the slot exists
        calls.append(1)
        return db, db.with_suffix(".tq")

    index_checkout(checkout, python=Path("/py"), cache_root=tmp_path, index_fn=_never)
    assert calls == []  # idempotent


def test_preseed_copies_db_and_tq(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.db as db_mod

    monkeypatch.setattr(db_mod, "CACHE_DIR", tmp_path / "user_cache")
    canonical_db = tmp_path / "canon.db"
    canonical_tq = tmp_path / "canon.tq"
    canonical_db.write_bytes(b"DBDATA")
    canonical_tq.write_bytes(b"TQDATA")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    dst_db, dst_tq = preseed_workspace(canonical_db, canonical_tq, workspace)
    assert dst_db.read_bytes() == b"DBDATA"
    assert dst_tq.read_bytes() == b"TQDATA"
    assert (dst_db, dst_tq) == workspace_cache_paths(workspace)


def test_preseed_missing_db_raises(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.db as db_mod

    monkeypatch.setattr(db_mod, "CACHE_DIR", tmp_path / "user_cache")
    with pytest.raises(FileNotFoundError, match="canonical index db is missing"):
        preseed_workspace(tmp_path / "nope.db", tmp_path / "nope.tq", tmp_path / "ws")


def test_preseed_db_is_copy_not_hardlink(tmp_path, monkeypatch) -> None:
    # Money-review finding 2: the product opens the .db RW under journal_mode=WAL
    # (in-place writes at the inode), so a hardlinked slot would let one rollout's
    # WAL write-back mutate the shared canonical bytes. The pre-seed MUST copy.
    import pydocs_mcp.db as db_mod

    monkeypatch.setattr(db_mod, "CACHE_DIR", tmp_path / "user_cache")
    canonical_db = tmp_path / "canon.db"
    canonical_db.write_bytes(b"DBDATA")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    dst_db, _ = preseed_workspace(canonical_db, tmp_path / "absent.tq", workspace)
    assert dst_db.stat().st_ino != canonical_db.stat().st_ino  # distinct inodes
    assert canonical_db.stat().st_nlink == 1  # canonical not hardlinked anywhere


def test_preseed_db_mutation_isolation(tmp_path, monkeypatch) -> None:
    # A rollout's serve opens the slot .db RW; a WAL checkpoint appends/rewrites in
    # place. Simulate that in-place write on the slot and assert the canonical
    # bytes are untouched — the copy severs the inode-sharing hazard (finding 2).
    import pydocs_mcp.db as db_mod

    monkeypatch.setattr(db_mod, "CACHE_DIR", tmp_path / "user_cache")
    canonical_db = tmp_path / "canon.db"
    canonical_db.write_bytes(b"CANON")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    dst_db, _ = preseed_workspace(canonical_db, tmp_path / "absent.tq", workspace)
    with dst_db.open("ab") as fh:  # an in-place RW append, as a serve reindex would do
        fh.write(b"-MUTATED-BY-ROLLOUT")
    assert canonical_db.read_bytes() == b"CANON"  # canonical index NOT poisoned


def test_preseed_tq_is_copy_not_hardlink(tmp_path, monkeypatch) -> None:
    # The .tq is copied too: turbovec's load mmap mode is not provably read-only
    # across the pinned range, so the same conservative copy applies (finding 2).
    import pydocs_mcp.db as db_mod

    monkeypatch.setattr(db_mod, "CACHE_DIR", tmp_path / "user_cache")
    canonical_db = tmp_path / "canon.db"
    canonical_tq = tmp_path / "canon.tq"
    canonical_db.write_bytes(b"DBDATA")
    canonical_tq.write_bytes(b"TQDATA")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _, dst_tq = preseed_workspace(canonical_db, canonical_tq, workspace)
    assert dst_tq.read_bytes() == b"TQDATA"
    assert dst_tq.stat().st_ino != canonical_tq.stat().st_ino


def test_preseed_reseed_is_idempotent(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.db as db_mod

    monkeypatch.setattr(db_mod, "CACHE_DIR", tmp_path / "user_cache")
    canonical = tmp_path / "canon.db"
    canonical.write_bytes(b"V1")
    ws = tmp_path / "ws"
    ws.mkdir()
    preseed_workspace(canonical, tmp_path / "absent.tq", ws)
    canonical.write_bytes(b"V2")
    dst_db, _ = preseed_workspace(canonical, tmp_path / "absent.tq", ws)
    assert dst_db.read_bytes() == b"V2"  # re-seed reflects the current canonical


# --- Integration: real git fixture + the real in-process indexer -------------


def _git(cmd: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *cmd],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


def _make_source_repo(root: Path) -> tuple[Path, str]:
    """A tiny real git repo with one documented module; return (path, commit)."""
    src = root / "src_repo"
    src.mkdir()
    _git(["init"], src)
    (src / "widget.py").write_text(
        '"""Widget module."""\n\n\ndef compute(x: int) -> int:\n'
        '    """Return x doubled — a documented function for the indexer."""\n'
        "    return x * 2\n"
    )
    _git(["add", "."], src)
    _git(["commit", "-m", "init"], src)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(src), check=True, capture_output=True, text=True
    ).stdout.strip()
    return src, sha


def test_end_to_end_checkout_index_and_preseed(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.db as db_mod

    monkeypatch.setattr(db_mod, "CACHE_DIR", tmp_path / "user_cache")
    src, sha = _make_source_repo(tmp_path)
    cache_root = tmp_path / "canonical"

    checkout = create_checkout(cache_root, repo="acme/widget", commit=sha, clone_url=str(src))
    assert (checkout / ".git").is_dir()
    assert (checkout / "widget.py").is_file()

    db, tq = index_checkout(
        checkout, python=Path("/unused"), cache_root=cache_root, index_fn=index_project_in_process
    )
    assert db.exists()  # the real indexer wrote the SQLite cache

    workspace = tmp_path / "rollout_ws"
    workspace.mkdir()
    dst_db, _dst_tq = preseed_workspace(db, tq, workspace)
    assert dst_db.exists()
    assert dst_db.read_bytes() == db.read_bytes()  # workspace slot mirrors the canonical index


def test_create_checkout_idempotent(tmp_path) -> None:
    src, sha = _make_source_repo(tmp_path)
    cache_root = tmp_path / "canonical"
    first = create_checkout(cache_root, repo="acme/widget", commit=sha, clone_url=str(src))
    calls: list = []
    second = create_checkout(
        cache_root,
        repo="acme/widget",
        commit=sha,
        clone_url=str(src),
        git=lambda c: calls.append(c),
    )
    assert first == second
    assert calls == []  # existing .git short-circuits, no re-clone
