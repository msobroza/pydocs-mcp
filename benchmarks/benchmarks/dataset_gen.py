"""Generate a synthetic question dataset from indexed chunks.

Questions are derived from chunk headings and body snippets.
Each row includes ground-truth relevant_chunk_ids for Recall@k / MRR@k evaluation.
The output DataFrame is the primary benchmark artifact.
"""
from __future__ import annotations

import random
import re
import sqlite3
from pathlib import Path

import pandas as pd

# All result DataFrames must have these columns (plus extras are allowed).
REQUIRED_COLUMNS = [
    "question",
    "package",
    "source_chunk_heading",
    "expected_answer_snippet",
    "chunk_kind",
    "chunk_body_preview",
    "relevant_chunk_ids",   # list[int] — ground truth for Recall@k / MRR@k
]

_QUESTION_TEMPLATES = [
    "How do I use {heading}?",
    "What does {heading} do?",
    "Show me an example of {heading}.",
    "Explain the {heading} functionality.",
    "What parameters does {heading} accept?",
    "When should I use {heading}?",
    "What is the return type of {heading}?",
]

_SEED = 42


def _heading_to_question(heading: str) -> str:
    """Derive a natural-language question from a chunk heading."""
    label = heading.split(":")[-1].strip()
    label = re.sub(r"[_\-]", " ", label)
    template = random.choice(_QUESTION_TEMPLATES)
    return template.format(heading=label)


def generate_dataset(db_path: Path, n_questions: int = 50, seed: int = _SEED) -> pd.DataFrame:
    """Sample chunks from *db_path* and synthesize evaluation questions.

    Each row's relevant_chunk_ids contains the rowid of the source chunk,
    providing ground truth for Recall@k and MRR@k computation.

    Args:
        db_path: Path to a pydocs-mcp SQLite database.
        n_questions: Maximum number of rows to generate.
        seed: Random seed for reproducibility.

    Returns:
        DataFrame with REQUIRED_COLUMNS.
    """
    random.seed(seed)

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT rowid, pkg, heading, body, kind FROM chunks ORDER BY RANDOM() LIMIT ?",
        (n_questions * 3,),
    ).fetchall()
    conn.close()

    records = []
    seen_headings: set[str] = set()

    for row in rows:
        if len(records) >= n_questions:
            break
        rowid, pkg, heading, body, kind = row
        if heading in seen_headings:
            continue
        seen_headings.add(heading)

        body = body or ""
        first_sentence = re.split(r"(?<=[.!?])\s", body.strip())[0][:300]

        records.append({
            "question": _heading_to_question(heading),
            "package": pkg,
            "source_chunk_heading": heading,
            "expected_answer_snippet": first_sentence,
            "chunk_kind": kind,
            "chunk_body_preview": body[:200],
            "relevant_chunk_ids": [rowid],  # ground truth: the source chunk
        })

    return pd.DataFrame(records, columns=REQUIRED_COLUMNS)
