"""Tests for PerCallConnectionProvider."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider


async def test_provider_opens_and_closes_connection(tmp_path: Path):
    db_file = tmp_path / "test.db"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE ping (id INTEGER)")
    con.close()

    provider = PerCallConnectionProvider(cache_path=db_file)
    async with provider.acquire() as conn:
        assert conn.row_factory is sqlite3.Row
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ping'")
        assert cur.fetchone()["name"] == "ping"


async def test_provider_independent_connections_per_call(tmp_path: Path):
    db_file = tmp_path / "test.db"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE t (v INTEGER)")
    con.commit()
    con.close()

    provider = PerCallConnectionProvider(cache_path=db_file)
    async with provider.acquire() as c1:
        c1.execute("INSERT INTO t VALUES (1)")
        c1.commit()

    async with provider.acquire() as c2:
        row = c2.execute("SELECT v FROM t").fetchone()
        assert row["v"] == 1
