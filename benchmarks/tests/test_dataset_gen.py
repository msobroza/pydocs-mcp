"""Tests for synthetic dataset generation."""
import sqlite3
import tempfile
from pathlib import Path
import pandas as pd
from benchmarks.dataset_gen import generate_dataset, REQUIRED_COLUMNS


def _seed_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS packages(
            name TEXT, version TEXT, summary TEXT,
            homepage TEXT, requires TEXT, cache_hash TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks(
            id INTEGER PRIMARY KEY, pkg TEXT, heading TEXT,
            body TEXT, kind TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbols(
            id INTEGER PRIMARY KEY, pkg TEXT, module TEXT,
            name TEXT, kind TEXT, signature TEXT,
            returns TEXT, params TEXT, doc TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO chunks(pkg, heading, body, kind) VALUES(?,?,?,?)",
        [
            ("requests", "requests.get", "Send HTTP GET request. Returns Response.", "doc"),
            ("requests", "requests.post", "Send HTTP POST request with JSON body.", "doc"),
            ("pandas", "DataFrame.merge", "Merge DataFrame objects with a database-style join.", "doc"),
        ],
    )
    conn.commit()


def test_generate_dataset_returns_dataframe():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(db_path))
        _seed_db(conn)
        conn.close()
        df = generate_dataset(db_path, n_questions=3)
        assert isinstance(df, pd.DataFrame)


def test_generate_dataset_has_required_columns():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(db_path))
        _seed_db(conn)
        conn.close()
        df = generate_dataset(db_path, n_questions=3)
        for col in REQUIRED_COLUMNS:
            assert col in df.columns, f"Missing column: {col}"


def test_generate_dataset_has_ground_truth():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(db_path))
        _seed_db(conn)
        conn.close()
        df = generate_dataset(db_path, n_questions=3)
        # Each row must have a non-empty list of relevant chunk IDs
        assert "relevant_chunk_ids" in df.columns
        for ids in df["relevant_chunk_ids"]:
            assert isinstance(ids, list)
            assert len(ids) >= 1
            assert all(isinstance(i, int) for i in ids)


def test_generate_dataset_question_not_empty():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(db_path))
        _seed_db(conn)
        conn.close()
        df = generate_dataset(db_path, n_questions=2)
        assert (df["question"].str.len() > 5).all()
