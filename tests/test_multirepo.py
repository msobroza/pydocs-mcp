"""Multi-repo db resolution: discover, name derivation, project selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database, write_index_metadata
from pydocs_mcp.multirepo import (
    EmbedderMismatchError,
    LoadedProject,
    discover_workspace,
    load_project,
    select_project,
    validate_project_embedder,
)
from pydocs_mcp.storage.index_metadata import IndexMetadata


def _build_db(path: Path, *, name: str | None, indexed_at: float = 1.0) -> Path:
    conn = open_index_database(path)
    if name is not None:
        write_index_metadata(
            conn,
            IndexMetadata(
                project_name=name,
                project_root=f"/src/{name}",
                embedding_provider="fastembed",
                embedding_model="bge",
                embedding_dim=384,
                pipeline_hash="h",
                indexed_at=indexed_at,
            ),
        )
    conn.close()
    return path


def test_load_project_prefers_stamped_name(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "webapp_1a2b3c4d5e.db", name="my-webapp")
    proj = load_project(db)
    assert proj.name == "my-webapp"  # stamped name wins over filename
    assert proj.metadata is not None and proj.indexed_at == 1.0


def test_load_project_falls_back_to_filename_for_legacy(tmp_path: Path) -> None:
    # No metadata stamped -> name from the {name}_{slug} filename, legacy fallback
    # metadata (dim unknown, oldest recency).
    db = _build_db(tmp_path / "backend_0123456789.db", name=None)
    proj = load_project(db)
    assert proj.name == "backend"
    assert proj.indexed_at == 0.0 and proj.metadata.embedding_dim == -1


def test_load_project_legacy_reads_packages_embedding_model(tmp_path: Path) -> None:
    db = tmp_path / "legacy_0123456789.db"
    conn = open_index_database(db)
    conn.execute("INSERT INTO packages(name, embedding_model) VALUES('__project__', 'bge')")
    conn.commit()
    conn.close()
    proj = load_project(db)
    assert proj.metadata.embedding_model == "bge"  # recovered from packages table


def test_filename_name_with_underscores(tmp_path: Path) -> None:
    db = _build_db(tmp_path / "my_cool_app_abcdef0123.db", name=None)
    assert load_project(db).name == "my_cool_app"


def test_discover_workspace_loads_all_dbs(tmp_path: Path) -> None:
    _build_db(tmp_path / "a_0000000000.db", name="alpha")
    _build_db(tmp_path / "b_1111111111.db", name="beta")
    projects = discover_workspace(tmp_path)
    assert {p.name for p in projects} == {"alpha", "beta"}


def test_discover_workspace_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_workspace(tmp_path / "nope")


def test_discover_workspace_empty_raises(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x")  # no .db files
    with pytest.raises(ValueError, match="no .db bundles"):
        discover_workspace(tmp_path)


def _proj(name: str, at: float, stem: str = "") -> LoadedProject:
    meta = IndexMetadata(
        project_name=name,
        project_root="",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=at,
    )
    return LoadedProject(name=name, db_path=Path(f"/x/{stem or name}.db"), metadata=meta)


def test_select_project_exact(tmp_path: Path) -> None:
    ps = [_proj("alpha", 1.0), _proj("beta", 2.0)]
    assert select_project(ps, "beta").name == "beta"


def test_select_project_missing_raises() -> None:
    with pytest.raises(KeyError, match="no loaded project named 'gamma'"):
        select_project([_proj("alpha", 1.0)], "gamma")


def test_select_project_same_name_most_recent_wins(tmp_path: Path) -> None:
    old = _build_db(tmp_path / "app_aaaaaaaaaa.db", name="app", indexed_at=100.0)
    new = _build_db(tmp_path / "app_bbbbbbbbbb.db", name="app", indexed_at=200.0)
    projects = [load_project(old), load_project(new)]
    chosen = select_project(projects, "app")
    assert chosen.indexed_at == 200.0  # most-recently-indexed wins


def test_select_project_by_full_stem_disambiguates(tmp_path: Path) -> None:
    a = _build_db(tmp_path / "app_aaaaaaaaaa.db", name="app", indexed_at=100.0)
    _build_db(tmp_path / "app_bbbbbbbbbb.db", name="app", indexed_at=200.0)
    projects = [load_project(a), load_project(tmp_path / "app_bbbbbbbbbb.db")]
    # Pass the full {name}_{slug} stem to pin the older one despite recency.
    chosen = select_project(projects, "app_aaaaaaaaaa")
    assert chosen.db_path.stem == "app_aaaaaaaaaa"


# ── L3: embedder-mismatch validation ──


def test_validate_embedder_ok(tmp_path: Path) -> None:
    proj = load_project(_build_db(tmp_path / "a_0000000000.db", name="a"))
    validate_project_embedder(proj, model="bge", dim=384)  # matches -> no raise


def test_validate_embedder_model_mismatch_raises(tmp_path: Path) -> None:
    proj = load_project(_build_db(tmp_path / "a_0000000000.db", name="a"))
    with pytest.raises(EmbedderMismatchError, match="was indexed with embedder 'bge'"):
        validate_project_embedder(proj, model="qwen", dim=384)


def test_validate_embedder_dim_mismatch_raises(tmp_path: Path) -> None:
    proj = load_project(_build_db(tmp_path / "a_0000000000.db", name="a"))  # dim 384
    with pytest.raises(EmbedderMismatchError, match="dim 384.*dim 768"):
        validate_project_embedder(proj, model="bge", dim=768)


def test_validate_embedder_legacy_unknown_is_permitted(tmp_path: Path) -> None:
    # No metadata, no packages.embedding_model -> unknown identity -> permitted.
    proj = load_project(_build_db(tmp_path / "old_0000000000.db", name=None))
    validate_project_embedder(proj, model="anything", dim=999)  # no raise
