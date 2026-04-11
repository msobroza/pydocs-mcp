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
