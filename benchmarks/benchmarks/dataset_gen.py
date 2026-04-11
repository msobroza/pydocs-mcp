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
    # Search parameters — map directly to search_chunks() arguments
    "search_query",         # str — FTS5 query terms (heading without template wrapper)
    "search_topic",         # str — heading prefix for topic LIKE filter
    "search_internal",      # bool — False for deps, True for project code
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

# Headings to skip — too generic or meaningless for question generation.
_SKIP_HEADINGS = frozenset({
    "overview", "introduction", "summary", "description",
    "notes", "see also", "references", "examples",
    "type aliases", "getting help", "getting started",
    "basic operations", "public functions", "table of contents",
    "license", "documentation", "installation", "quickstart",
})

# Minimum length for a heading to be useful.
_MIN_HEADING_LEN = 8

# Patterns that indicate test files, internal comments, or non-API content.
_BAD_HEADING_PATTERNS = re.compile(
    r"(test[_s]|conftest|https?://|TODO|FIXME|HACK|WORKAROUND"
    r"|should be|we pass|need to|able to|default case"
    r"|moved|compat|pickle|assert_|in help\()",
    re.IGNORECASE,
)


def _is_good_heading(heading: str) -> bool:
    """Return True if heading is specific enough for a useful question."""
    label = heading.split(":")[-1].strip().lower()
    if len(label) < _MIN_HEADING_LEN:
        return False
    if label in _SKIP_HEADINGS:
        return False
    # Skip whitespace-only or purely numeric headings
    if not re.search(r"[a-zA-Z]", label):
        return False
    # Skip headings that look like test/internal code
    if _BAD_HEADING_PATTERNS.search(heading):
        return False
    return True


def _looks_like_api_heading(heading: str) -> bool:
    """Return True if heading looks like a public API symbol (e.g. 'pandas.DataFrame.merge')."""
    return "." in heading and not heading.startswith("http")


def _heading_to_question(heading: str) -> str:
    """Derive a natural-language question from a chunk heading."""
    label = heading.split(":")[-1].strip()
    label = re.sub(r"[_\-]", " ", label)
    template = random.choice(_QUESTION_TEMPLATES)
    return template.format(heading=label)


def generate_dataset(db_path: Path, n_questions: int = 50, seed: int = _SEED) -> pd.DataFrame:
    """Sample chunks from *db_path* and synthesize evaluation questions.

    Prioritizes documentation chunks (dep_doc, readme) and API-style headings
    (e.g. 'pandas.DataFrame.merge') over internal code or test files.

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

    # Prioritize doc chunks and API-like headings.
    # Query in two tiers: first dep_doc/readme, then dep_code with API headings.
    all_rows: list[tuple] = []

    # Tier 1: Documentation chunks (highest quality)
    all_rows.extend(conn.execute(
        "SELECT rowid, pkg, heading, body, kind FROM chunks "
        "WHERE kind IN ('dep_doc', 'readme') AND length(body) > 50 "
        "ORDER BY RANDOM() LIMIT ?",
        (n_questions * 3,),
    ).fetchall())

    # Tier 2: Code chunks with API-like headings (e.g. 'pandas.DataFrame.merge')
    all_rows.extend(conn.execute(
        "SELECT rowid, pkg, heading, body, kind FROM chunks "
        "WHERE kind = 'dep_code' AND heading LIKE '%.%' "
        "AND heading NOT LIKE '%test%' AND heading NOT LIKE '%conftest%' "
        "AND length(body) > 80 "
        "ORDER BY RANDOM() LIMIT ?",
        (n_questions * 5,),
    ).fetchall())

    records = []
    seen_headings: set[str] = set()

    for row in all_rows:
        if len(records) >= n_questions:
            break
        rowid, pkg, heading, body, kind = row
        if heading in seen_headings:
            continue
        if not _is_good_heading(heading):
            continue
        seen_headings.add(heading)

        body = body or ""
        first_sentence = re.split(r"(?<=[.!?])\s", body.strip())[0][:300]
        if len(first_sentence) < 20:
            continue

        # Ground truth: include all chunks whose heading starts with this one.
        # This handles the dep_doc / dep_code split where the same module
        # produces multiple chunks (e.g. 'numpy.ctypeslib' and 'numpy.ctypeslib:Overview').
        related = conn.execute(
            "SELECT rowid FROM chunks WHERE heading = ? OR heading LIKE ?",
            (heading, heading + ":%"),
        ).fetchall()
        all_ids = [r[0] for r in related] if related else [rowid]

        # Build search parameters that map to search_chunks() arguments.
        # search_query: the heading as raw terms (no template wrapper).
        # search_topic: the base heading (before ':') for LIKE filtering.
        base_heading = heading.split(":")[0].strip()
        search_query = re.sub(r"[_\-.]", " ", base_heading)
        is_internal = kind in ("project_code", "project_doc")

        records.append({
            "question": _heading_to_question(heading),
            "package": pkg,
            "source_chunk_heading": heading,
            "expected_answer_snippet": first_sentence,
            "chunk_kind": kind,
            "chunk_body_preview": body[:200],
            "relevant_chunk_ids": all_ids,
            "search_query": search_query,
            "search_topic": base_heading,
            "search_internal": is_internal,
        })

    conn.close()
    return pd.DataFrame(records, columns=REQUIRED_COLUMNS)
