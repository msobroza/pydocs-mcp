"""Shared pytest fixtures for pydocs-mcp tests."""
import pytest
from pydocs_mcp.db import open_db, rebuild_fts


@pytest.fixture
def conn(tmp_path):
    """File-backed SQLite DB seeded with known project + dep data."""
    c = open_db(tmp_path / "test.db")

    c.execute(
        "INSERT INTO packages (name, version, summary, homepage, requires, hash)"
        " VALUES ('__project__', '0.1', 'Test project', '', '[]', 'aaa')"
    )
    c.execute(
        "INSERT INTO packages (name, version, summary, homepage, requires, hash)"
        " VALUES ('requests', '2.28', 'HTTP library', '', '[]', 'bbb')"
    )
    c.execute(
        "INSERT INTO packages (name, version, summary, homepage, requires, hash)"
        " VALUES ('sqlalchemy', '2.0', 'Database toolkit', '', '[]', 'ccc')"
    )

    # Project chunks
    c.execute(
        "INSERT INTO chunks (pkg, heading, body, kind)"
        " VALUES ('__project__', 'fibonacci', 'Compute the fibonacci sequence for n', 'project_code')"
    )
    c.execute(
        "INSERT INTO chunks (pkg, heading, body, kind)"
        " VALUES ('__project__', 'README', 'Project overview and fibonacci examples', 'project_doc')"
    )

    # Dep chunks
    c.execute(
        "INSERT INTO chunks (pkg, heading, body, kind)"
        " VALUES ('requests', 'get', 'Send HTTP GET request to a URL', 'dep_code')"
    )
    c.execute(
        "INSERT INTO chunks (pkg, heading, body, kind)"
        " VALUES ('sqlalchemy', 'Session', 'Database session for ORM queries', 'dep_doc')"
    )

    # Project symbols
    c.execute(
        "INSERT INTO symbols (pkg, module, name, kind, signature, returns, params, doc)"
        " VALUES ('__project__', 'myapp.utils', 'fibonacci', 'def', '(n: int)', 'int', '[]',"
        " 'Return nth fibonacci number')"
    )

    # Dep symbols
    c.execute(
        "INSERT INTO symbols (pkg, module, name, kind, signature, returns, params, doc)"
        " VALUES ('requests', 'requests.api', 'get', 'def', '(url, **kwargs)', 'Response', '[]',"
        " 'Send GET request')"
    )
    c.execute(
        "INSERT INTO symbols (pkg, module, name, kind, signature, returns, params, doc)"
        " VALUES ('sqlalchemy', 'sqlalchemy.orm', 'Session', 'class', '()', 'None', '[]',"
        " 'ORM session class')"
    )

    c.commit()
    rebuild_fts(c)
    yield c
    c.close()


import os
from pathlib import Path
from pydocs_mcp.indexer import index_project, _parse_source_files

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FAKE_PROJECT = FIXTURES_DIR / "fake_project"
PACKAGES_DIR = FIXTURES_DIR / "packages"


@pytest.fixture
def integration_conn(tmp_path):
    """DB seeded by running the real indexer against fixture files.

    Indexes the fake_project source + the 3 package snapshots (sklearn, vllm,
    langgraph) using the static parser (_parse_source_files), then rebuilds FTS.
    """
    db_path = tmp_path / "integration.db"
    c = open_db(db_path)

    # Index the fake project
    index_project(c, FAKE_PROJECT)

    # Index each package snapshot as if it were an installed dep
    for pkg_name in ("sklearn", "vllm", "langgraph"):
        pkg_dir = PACKAGES_DIR / pkg_name
        py_files = sorted(str(p) for p in pkg_dir.rglob("*.py"))
        chunks, syms = _parse_source_files(pkg_name, py_files, str(pkg_dir), kind_prefix="dep")
        c.executemany(
            "INSERT INTO packages (name, version, summary, homepage, requires, hash)"
            " VALUES (?, ?, ?, '', '[]', ?)",
            [(pkg_name, "0.0.0", f"{pkg_name} fixture", f"fixture_{pkg_name}")],
        )
        c.executemany(
            "INSERT INTO chunks (pkg, heading, body, kind) VALUES (?, ?, ?, ?)",
            chunks,
        )
        c.executemany(
            "INSERT INTO symbols (pkg, module, name, kind, signature, returns, params, doc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            syms,
        )

    c.commit()
    rebuild_fts(c)
    yield c
    c.close()
