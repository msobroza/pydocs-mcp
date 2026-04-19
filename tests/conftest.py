"""Shared pytest fixtures for pydocs-mcp tests."""
import pytest
from pydocs_mcp.db import open_index_database, rebuild_fulltext_index


@pytest.fixture
def conn(tmp_path):
    """File-backed SQLite DB seeded with known project + dep data."""
    c = open_index_database(tmp_path / "test.db")

    c.execute(
        "INSERT INTO packages (name, version, summary, homepage, dependencies, content_hash, origin)"
        " VALUES ('__project__', '0.1', 'Test project', '', '[]', 'aaa', 'project')"
    )
    c.execute(
        "INSERT INTO packages (name, version, summary, homepage, dependencies, content_hash, origin)"
        " VALUES ('requests', '2.28', 'HTTP library', '', '[]', 'bbb', 'dependency')"
    )
    c.execute(
        "INSERT INTO packages (name, version, summary, homepage, dependencies, content_hash, origin)"
        " VALUES ('sqlalchemy', '2.0', 'Database toolkit', '', '[]', 'ccc', 'dependency')"
    )

    # Project chunks
    c.execute(
        "INSERT INTO chunks (package, title, text, origin)"
        " VALUES ('__project__', 'fibonacci', 'Compute the fibonacci sequence for n', 'project_code_section')"
    )
    c.execute(
        "INSERT INTO chunks (package, title, text, origin)"
        " VALUES ('__project__', 'README', 'Project overview and fibonacci examples', 'project_module_doc')"
    )

    # Dep chunks
    c.execute(
        "INSERT INTO chunks (package, title, text, origin)"
        " VALUES ('requests', 'get', 'Send HTTP GET request to a URL', 'dependency_code_section')"
    )
    c.execute(
        "INSERT INTO chunks (package, title, text, origin)"
        " VALUES ('sqlalchemy', 'Session', 'Database session for ORM queries', 'dependency_doc_file')"
    )

    # Project symbols
    c.execute(
        "INSERT INTO module_members (package, module, name, kind, signature, return_annotation, parameters, docstring)"
        " VALUES ('__project__', 'myapp.utils', 'fibonacci', 'function', '(n: int)', 'int', '[]',"
        " 'Return nth fibonacci number')"
    )

    # Dep symbols
    c.execute(
        "INSERT INTO module_members (package, module, name, kind, signature, return_annotation, parameters, docstring)"
        " VALUES ('requests', 'requests.api', 'get', 'function', '(url, **kwargs)', 'Response', '[]',"
        " 'Send GET request')"
    )
    c.execute(
        "INSERT INTO module_members (package, module, name, kind, signature, return_annotation, parameters, docstring)"
        " VALUES ('sqlalchemy', 'sqlalchemy.orm', 'Session', 'class', '()', 'None', '[]',"
        " 'ORM session class')"
    )

    c.commit()
    rebuild_fulltext_index(c)
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
    c = open_index_database(db_path)

    # Index the fake project
    index_project(c, FAKE_PROJECT)

    # Index each package snapshot as if it were an installed dep
    for pkg_name in ("sklearn", "vllm", "langgraph"):
        pkg_dir = PACKAGES_DIR / pkg_name
        py_files = sorted(str(p) for p in pkg_dir.rglob("*.py"))
        chunks, syms = _parse_source_files(pkg_name, py_files, str(pkg_dir), kind_prefix="dep")
        c.executemany(
            "INSERT INTO packages (name, version, summary, homepage, dependencies, content_hash, origin)"
            " VALUES (?, ?, ?, '', '[]', ?, 'dependency')",
            [(pkg_name, "0.0.0", f"{pkg_name} fixture", f"fixture_{pkg_name}")],
        )
        c.executemany(
            "INSERT INTO chunks (package, title, text, origin) VALUES (?, ?, ?, ?)",
            chunks,
        )
        c.executemany(
            "INSERT INTO module_members (package, module, name, kind, signature, return_annotation, parameters, docstring)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            syms,
        )

    c.commit()
    rebuild_fulltext_index(c)
    yield c
    c.close()
