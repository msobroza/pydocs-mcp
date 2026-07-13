"""EROFS degradation — in-memory linking when nothing is writable (AC20)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.server import _open_overlay_store
from pydocs_mcp.storage.in_memory_cross_link_store import InMemoryCrossLinkStore
from pydocs_mcp.storage.sqlite.cross_link_store import SqliteCrossLinkStore


class _Cfg:
    class reference_graph:
        class cross_repo:
            overlay_dir = None


def test_writable_workspace_gets_the_persisted_store(tmp_path: Path) -> None:
    store, persisted = _open_overlay_store(_Cfg, tmp_path, None)
    assert persisted and isinstance(store, SqliteCrossLinkStore)
    assert (tmp_path / "pydocs-links.sqlite3").exists()


def test_unwritable_everywhere_degrades_to_in_memory(tmp_path: Path, monkeypatch) -> None:
    # AC20: both overlay locations unwritable → InMemoryCrossLinkStore,
    # correct semantics, nothing persisted.
    workspace = tmp_path / "ro"
    workspace.mkdir()
    home_links = tmp_path / "home-links"
    home_links.mkdir()
    workspace.chmod(0o555)
    home_links.chmod(0o555)
    try:
        # Point the home fallback at the read-only stand-in.
        import pydocs_mcp.server as server_mod

        real_expanduser = Path.expanduser

        def _fake_expanduser(self: Path) -> Path:
            if "pydocs-mcp" in str(self):
                return home_links / "x.sqlite3".replace("x", "fallback")
            return real_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", _fake_expanduser)
        store, persisted = server_mod._open_overlay_store(_Cfg, workspace, None)
        assert not persisted
        assert isinstance(store, InMemoryCrossLinkStore)
        assert list(workspace.iterdir()) == []  # nothing written
    finally:
        workspace.chmod(0o755)
        home_links.chmod(0o755)


class _CfgWithOverride:
    class reference_graph:
        class cross_repo:
            overlay_dir = None  # set per-test below


def test_overlay_dir_override_wins(tmp_path: Path) -> None:
    target = tmp_path / "custom"
    target.mkdir()
    _CfgWithOverride.reference_graph.cross_repo.overlay_dir = target
    store, persisted = _open_overlay_store(_CfgWithOverride, tmp_path, None)
    assert persisted
    assert (target / "pydocs-links.sqlite3").exists()
