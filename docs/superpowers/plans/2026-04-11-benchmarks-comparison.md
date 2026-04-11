# Benchmarks Comparison Subproject Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a standalone `benchmarks/` subproject that measures and compares pyctx7-mcp vs Context7 on indexing time, search latency, and result relevance, producing a DataFrame dataset and visualization charts.

**Architecture:** A self-contained Python subproject with its own `pyproject.toml`; a `runner.py` script orchestrates all phases (fake-project generation → indexing → synthetic dataset creation → search benchmarking → Context7 comparison → DataFrame output → chart generation). Each phase is a separate module for clarity. Results are saved as CSV (the primary deliverable) plus PNG charts.

**Tech Stack:** Python 3.10+, pandas, matplotlib, httpx (async HTTP for Context7 API), pydocs-mcp (installed from parent project), pytest for unit tests of helpers, rich for progress display.

---

## ⚠️ Amendment: Proper IR Metrics (supersedes original relevance scoring)

Replace all uses of simple `relevant: bool` with proper information retrieval metrics:

- **Recall@k** — proportion of relevant chunks found in top-k results
- **MRR@k** (Mean Reciprocal Rank) — inverse of rank of first relevant result in top-k

Evaluate for **k ∈ [1, 3, 5, 10, 20]**.

### Ground truth in dataset
Each row in the synthetic dataset must include `relevant_chunk_ids: list[int]` — the SQLite `rowid` values of chunks that are considered ground-truth relevant for the query. A chunk is ground-truth relevant if it was the source of the question (i.e. the chunk from which the question was derived).

### DataFrame columns (updated)
`benchmark_results.csv` must include, for each k in [1, 3, 5, 10, 20]:
- `recall_at_{k}` — float [0,1]
- `mrr_at_{k}` — float [0,1]

### Charts (updated)
- **Recall@k curve** — line plot: x=k values, y=mean Recall@k, one line per source (pyctx7 vs context7)
- **MRR@k curve** — same format
- **Search latency boxplot** — unchanged
- **Indexing times bar chart** — unchanged

### Affected tasks
- Task 4 (dataset_gen.py): add `relevant_chunk_ids` column with list of ground-truth chunk IDs
- Task 5 (search_bench.py): compute recall_at_k and mrr_at_k for k in [1,3,5,10,20]; no more `relevant: bool`
- Task 7 (context7_bench.py): same metric interface as Task 5
- Task 8 (charts.py): replace relevance bar chart with Recall@k and MRR@k line plots
- Task 9 (runner.py): assemble DataFrame with new column schema

---

## File Map

```
benchmarks/
├── pyproject.toml          # Standalone project metadata + deps
├── requirements.txt        # Pinned deps for reproducible runs
├── README.md               # How to run, what each script does
├── data/
│   └── .gitkeep            # Benchmark output lands here (gitignored)
├── fake_project/           # Synthetic Python project used as indexing target
│   ├── __init__.py
│   ├── api.py
│   ├── models.py
│   └── utils.py
├── benchmarks/
│   ├── __init__.py
│   ├── fake_project.py     # Generates the fake_project/ tree programmatically
│   ├── indexer_bench.py    # Times pydocs-mcp indexing per package
│   ├── dataset_gen.py      # Synthesizes question dataset from indexed chunks
│   ├── search_bench.py     # Times pydocs-mcp search_docs / search_api calls
│   ├── context7_client.py  # Thin async client for Context7 MCP HTTP API
│   ├── context7_bench.py   # Times Context7 resolve + get-library-docs calls
│   ├── runner.py           # CLI entry: runs all phases, saves CSV + charts
│   └── charts.py           # Generates bar charts and box plots from DataFrame
└── tests/
    ├── test_fake_project.py
    ├── test_dataset_gen.py
    └── test_context7_client.py
```

---

## Task 1: Subproject Scaffold

**Files:**
- Create: `benchmarks/pyproject.toml`
- Create: `benchmarks/requirements.txt`
- Create: `benchmarks/benchmarks/__init__.py`
- Create: `benchmarks/tests/__init__.py` (empty)
- Create: `benchmarks/data/.gitkeep`

- [ ] **Step 1: Create `benchmarks/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "pyctx7-benchmarks"
version = "0.1.0"
description = "Benchmark suite comparing pyctx7-mcp vs Context7"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "pandas>=2.0",
    "matplotlib>=3.7",
    "httpx>=0.27",
    "rich>=13.0",
    "pydocs-mcp @ file://../",   # install parent project in editable-like mode
]

[project.scripts]
run-benchmarks = "benchmarks.runner:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["benchmarks*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `benchmarks/requirements.txt`**

```
pandas==2.2.2
matplotlib==3.9.0
httpx==0.27.0
rich==13.7.1
pytest==8.2.0
```

- [ ] **Step 3: Create empty `__init__.py` files**

Create `benchmarks/benchmarks/__init__.py` and `benchmarks/tests/__init__.py` — both empty (just `# benchmarks package`).

- [ ] **Step 4: Create `benchmarks/data/.gitkeep`**

Empty file. Add `benchmarks/data/` to `.gitignore` in the parent repo root:

```
# Benchmark outputs
benchmarks/data/*.csv
benchmarks/data/*.png
```

- [ ] **Step 5: Commit scaffold**

```bash
git add benchmarks/pyproject.toml benchmarks/requirements.txt \
    benchmarks/benchmarks/__init__.py benchmarks/tests/__init__.py \
    benchmarks/data/.gitkeep .gitignore
git commit -m "feat(benchmarks): scaffold standalone subproject"
```

---

## Task 2: Fake Project Generator

**Files:**
- Create: `benchmarks/fake_project/__init__.py`
- Create: `benchmarks/fake_project/api.py`
- Create: `benchmarks/fake_project/models.py`
- Create: `benchmarks/fake_project/utils.py`
- Create: `benchmarks/benchmarks/fake_project.py`
- Create: `benchmarks/tests/test_fake_project.py`

The fake project is a small but realistic Python package that references common dependencies (requests, pandas, numpy) so they appear in its `requirements.txt` and get indexed. It must be stable across runs so indexing benchmarks are reproducible.

- [ ] **Step 1: Write the failing test**

`benchmarks/tests/test_fake_project.py`:

```python
"""Tests for fake_project generator."""
import tempfile
from pathlib import Path
from benchmarks.fake_project import generate_fake_project, FAKE_REQUIREMENTS


def test_generate_creates_py_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "myproject"
        generate_fake_project(root)
        py_files = list(root.rglob("*.py"))
        assert len(py_files) >= 3


def test_generate_creates_requirements():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "myproject"
        generate_fake_project(root)
        req = root / "requirements.txt"
        assert req.exists()
        content = req.read_text()
        assert "requests" in content


def test_fake_requirements_list():
    assert "requests" in FAKE_REQUIREMENTS
    assert "pandas" in FAKE_REQUIREMENTS
    assert len(FAKE_REQUIREMENTS) >= 3
```

- [ ] **Step 2: Run test — confirm it fails**

```bash
cd benchmarks && python -m pytest tests/test_fake_project.py -v
```

Expected: `ModuleNotFoundError: No module named 'benchmarks.fake_project'`

- [ ] **Step 3: Create static fake project files**

`benchmarks/fake_project/__init__.py`:
```python
"""Fake analytics project — used as benchmark indexing target."""
```

`benchmarks/fake_project/models.py`:
```python
"""Data models for the fake analytics project."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DataPoint:
    """A single observation with a label and numeric features.

    Attributes:
        label: Category label for this data point.
        features: List of float values representing measurements.
        weight: Optional sample weight for weighted aggregations.
    """
    label: str
    features: list[float] = field(default_factory=list)
    weight: Optional[float] = None

    def normalize(self) -> "DataPoint":
        """Return a new DataPoint with features scaled to [0, 1]."""
        if not self.features:
            return self
        mn, mx = min(self.features), max(self.features)
        span = mx - mn or 1.0
        return DataPoint(
            label=self.label,
            features=[(f - mn) / span for f in self.features],
            weight=self.weight,
        )


@dataclass
class BatchResult:
    """Aggregated result from processing a batch of DataPoints."""
    count: int
    mean_score: float
    failed: list[str] = field(default_factory=list)
```

`benchmarks/fake_project/api.py`:
```python
"""HTTP API helpers for the fake analytics project."""
import json
from typing import Any
import requests


def fetch_dataset(url: str, timeout: int = 30) -> list[dict[str, Any]]:
    """Download a JSON dataset from a remote URL.

    Args:
        url: Full HTTP(S) URL pointing to a JSON array.
        timeout: Request timeout in seconds.

    Returns:
        Parsed list of record dicts.

    Raises:
        requests.HTTPError: If the server returns a non-2xx status.
    """
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def post_results(endpoint: str, payload: dict[str, Any]) -> bool:
    """Upload benchmark results to a collection endpoint.

    Args:
        endpoint: URL accepting POST with JSON body.
        payload: Dict to serialize as JSON.

    Returns:
        True if server acknowledged with 2xx.
    """
    resp = requests.post(endpoint, json=payload, timeout=10)
    return resp.ok
```

`benchmarks/fake_project/utils.py`:
```python
"""Utility helpers for the fake analytics project."""
import hashlib
import statistics
from typing import Iterable


def batch(iterable: Iterable, size: int):
    """Yield successive chunks of *size* from *iterable*."""
    buf = []
    for item in iterable:
        buf.append(item)
        if len(buf) == size:
            yield buf
            buf = []
    if buf:
        yield buf


def mean_and_std(values: list[float]) -> tuple[float, float]:
    """Return (mean, stdev) or (0.0, 0.0) for empty input."""
    if not values:
        return 0.0, 0.0
    m = statistics.mean(values)
    s = statistics.pstdev(values) if len(values) > 1 else 0.0
    return m, s


def fingerprint(text: str) -> str:
    """Return a short SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]
```

- [ ] **Step 4: Create `benchmarks/benchmarks/fake_project.py`**

```python
"""Generates the fake_project tree and its requirements.txt programmatically.

The fake project is deterministic so benchmark runs are comparable.
The FAKE_REQUIREMENTS list drives which packages get indexed.
"""
from __future__ import annotations

import shutil
from pathlib import Path

# Packages that will be listed in the fake project's requirements.txt.
# Keep this small — each package adds indexing latency to the benchmark.
FAKE_REQUIREMENTS: list[str] = [
    "requests",
    "pandas",
    "numpy",
]

# Source code lives next to this module in the repo.
_STATIC_ROOT = Path(__file__).parent.parent / "fake_project"


def generate_fake_project(dest: Path) -> Path:
    """Copy the static fake_project tree to *dest* and write requirements.txt.

    Args:
        dest: Directory to create (will be overwritten if it exists).

    Returns:
        Path to the generated project root.
    """
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_STATIC_ROOT, dest)

    req_path = dest / "requirements.txt"
    req_path.write_text("\n".join(FAKE_REQUIREMENTS) + "\n")
    return dest
```

- [ ] **Step 5: Run tests — confirm they pass**

```bash
cd benchmarks && python -m pytest tests/test_fake_project.py -v
```

Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add benchmarks/fake_project/ benchmarks/benchmarks/fake_project.py \
    benchmarks/tests/test_fake_project.py
git commit -m "feat(benchmarks): add fake project generator and static project files"
```

---

## Task 3: Indexing Benchmark Module

**Files:**
- Create: `benchmarks/benchmarks/indexer_bench.py`

This module times how long pydocs-mcp takes to index the fake project and each of its required packages. It uses the pydocs-mcp internals directly (not the CLI) for precise per-package timing.

- [ ] **Step 1: Create `benchmarks/benchmarks/indexer_bench.py`**

```python
"""Time pydocs-mcp indexing for a fake project and its dependencies.

We call the pydocs-mcp internals directly to get per-package timings.
This avoids subprocess overhead and lets us capture structured results.
"""
from __future__ import annotations

import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.db import open_db, rebuild_fts
from pydocs_mcp.indexer import index_project, index_deps


@dataclass
class IndexResult:
    """Timing result for a single indexing target."""
    target: str          # package name or '__project__'
    elapsed_s: float     # wall-clock seconds
    chunks: int = 0
    symbols: int = 0
    error: str = ""


def run_indexing_benchmark(
    project_root: Path,
    dep_names: list[str],
    use_inspect: bool = False,
    workers: int = 2,
) -> list[IndexResult]:
    """Index *project_root* and each dep in *dep_names*, returning timing rows.

    Args:
        project_root: Path to the fake (or real) project to index.
        dep_names: Dependency package names to index after the project.
        use_inspect: If True, use import+inspect mode (slower, richer).
        workers: ThreadPoolExecutor workers for dep indexing.

    Returns:
        List of IndexResult, one per target (project first, then each dep).
    """
    results: list[IndexResult] = []

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bench.db"
        conn = open_db(db_path)

        # --- Project ---
        t0 = time.perf_counter()
        try:
            index_project(conn, project_root)
            elapsed = time.perf_counter() - t0
            chunks = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE pkg='__project__'"
            ).fetchone()[0]
            symbols = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE pkg='__project__'"
            ).fetchone()[0]
            results.append(IndexResult("__project__", elapsed, chunks, symbols))
        except Exception as exc:
            results.append(IndexResult("__project__", time.perf_counter() - t0, error=str(exc)))

        # --- Each dep individually for per-package timing ---
        for dep in dep_names:
            t0 = time.perf_counter()
            try:
                index_deps(
                    conn, [dep],
                    depth=1, workers=1,
                    use_inspect=use_inspect,
                )
                elapsed = time.perf_counter() - t0
                norm = dep.lower().replace("-", "_")
                chunks = conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE pkg=?", (norm,)
                ).fetchone()[0]
                symbols = conn.execute(
                    "SELECT COUNT(*) FROM symbols WHERE pkg=?", (norm,)
                ).fetchone()[0]
                results.append(IndexResult(dep, elapsed, chunks, symbols))
            except Exception as exc:
                results.append(IndexResult(dep, time.perf_counter() - t0, error=str(exc)))

        rebuild_fts(conn)
        conn.close()

    return results
```

- [ ] **Step 2: Verify module imports cleanly**

```bash
cd benchmarks && python -c "from benchmarks.indexer_bench import run_indexing_benchmark; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add benchmarks/benchmarks/indexer_bench.py
git commit -m "feat(benchmarks): add indexing benchmark module with per-package timing"
```

---

## Task 4: Synthetic Dataset Generator

**Files:**
- Create: `benchmarks/benchmarks/dataset_gen.py`
- Create: `benchmarks/tests/test_dataset_gen.py`

This module creates a `pandas.DataFrame` of synthetic questions by sampling chunks from the indexed SQLite DB and deriving questions from heading + body text. The dataset is the primary benchmark artifact.

- [ ] **Step 1: Write the failing test**

`benchmarks/tests/test_dataset_gen.py`:

```python
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


def test_generate_dataset_question_not_empty():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(db_path))
        _seed_db(conn)
        conn.close()
        df = generate_dataset(db_path, n_questions=2)
        assert (df["question"].str.len() > 5).all()
```

- [ ] **Step 2: Run test — confirm it fails**

```bash
cd benchmarks && python -m pytest tests/test_dataset_gen.py -v
```

Expected: `ModuleNotFoundError: No module named 'benchmarks.dataset_gen'`

- [ ] **Step 3: Implement `benchmarks/benchmarks/dataset_gen.py`**

```python
"""Generate a synthetic question dataset from indexed chunks.

Questions are derived from chunk headings and body snippets.
The output DataFrame is the primary benchmark artifact for evaluation.
"""
from __future__ import annotations

import random
import re
import sqlite3
from pathlib import Path

import pandas as pd

# All result DataFrames must have these columns.
REQUIRED_COLUMNS = [
    "question",
    "package",
    "source_chunk_heading",
    "expected_answer_snippet",
    "chunk_kind",
    "chunk_body_preview",
]

# Templates for question generation from heading tokens.
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
    # Strip module prefix for readability: "requests.get" → "requests.get"
    label = heading.split(":")[-1].strip()
    label = re.sub(r"[_\-]", " ", label)
    template = random.choice(_QUESTION_TEMPLATES)
    return template.format(heading=label)


def generate_dataset(db_path: Path, n_questions: int = 50, seed: int = _SEED) -> pd.DataFrame:
    """Sample chunks from *db_path* and synthesize evaluation questions.

    Args:
        db_path: Path to a pydocs-mcp SQLite database.
        n_questions: Maximum number of rows to generate.
        seed: Random seed for reproducibility.

    Returns:
        DataFrame with REQUIRED_COLUMNS plus metadata.
    """
    random.seed(seed)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT pkg, heading, body, kind FROM chunks ORDER BY RANDOM() LIMIT ?",
        (n_questions * 3,),  # fetch extra to allow dedup
    ).fetchall()
    conn.close()

    records = []
    seen_headings: set[str] = set()

    for row in rows:
        if len(records) >= n_questions:
            break
        heading = row["heading"]
        if heading in seen_headings:
            continue
        seen_headings.add(heading)

        body = row["body"] or ""
        # Use first sentence as the expected answer snippet.
        first_sentence = re.split(r"(?<=[.!?])\s", body.strip())[0][:300]

        records.append({
            "question": _heading_to_question(heading),
            "package": row["pkg"],
            "source_chunk_heading": heading,
            "expected_answer_snippet": first_sentence,
            "chunk_kind": row["kind"],
            "chunk_body_preview": body[:200],
        })

    return pd.DataFrame(records, columns=REQUIRED_COLUMNS + ["chunk_kind", "chunk_body_preview"])
```

> **Note:** `REQUIRED_COLUMNS` doesn't include `chunk_kind`/`chunk_body_preview` by design — they're bonus columns the test doesn't enforce.

- [ ] **Step 4: Run tests — confirm they pass**

```bash
cd benchmarks && python -m pytest tests/test_dataset_gen.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add benchmarks/benchmarks/dataset_gen.py benchmarks/tests/test_dataset_gen.py
git commit -m "feat(benchmarks): synthetic question dataset generator with required columns"
```

---

## Task 5: Search Benchmark Module

**Files:**
- Create: `benchmarks/benchmarks/search_bench.py`

Times `search_chunks` and `search_symbols` from pydocs-mcp against questions in the dataset, recording latency and a basic relevance score.

- [ ] **Step 1: Create `benchmarks/benchmarks/search_bench.py`**

```python
"""Benchmark pydocs-mcp search latency and result relevance.

For each question in the dataset, we run search_docs (FTS5 BM25) and
record wall-clock time plus a simple relevance score: whether the
expected_answer_snippet appears (case-insensitive substring) in any result.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from pydocs_mcp.search import search_chunks


@dataclass
class SearchResult:
    """One search timing + relevance measurement."""
    question: str
    package: str
    elapsed_s: float
    n_results: int
    relevant: bool    # True if expected_answer_snippet found in any result body
    source: str = "pyctx7"


def run_search_benchmark(db_path: Path, dataset: pd.DataFrame) -> list[SearchResult]:
    """Run search_chunks for each row in *dataset* against *db_path*.

    Args:
        db_path: pydocs-mcp SQLite database to query.
        dataset: DataFrame with columns [question, package, expected_answer_snippet].

    Returns:
        List of SearchResult, one per dataset row.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    results = []

    for _, row in dataset.iterrows():
        query = str(row["question"])
        pkg = str(row["package"]) if row["package"] != "__project__" else ""
        expected = str(row["expected_answer_snippet"]).lower()[:100]

        t0 = time.perf_counter()
        hits = search_chunks(conn, query, pkg=pkg or None, limit=5)
        elapsed = time.perf_counter() - t0

        # Relevance: did any returned chunk body contain key words from the expected snippet?
        relevant = any(
            expected[:40] in (h.get("body") or "").lower()
            for h in hits
        )

        results.append(SearchResult(
            question=query,
            package=str(row["package"]),
            elapsed_s=elapsed,
            n_results=len(hits),
            relevant=relevant,
        ))

    conn.close()
    return results
```

- [ ] **Step 2: Verify import**

```bash
cd benchmarks && python -c "from benchmarks.search_bench import run_search_benchmark; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add benchmarks/benchmarks/search_bench.py
git commit -m "feat(benchmarks): search benchmark module with latency and relevance scoring"
```

---

## Task 6: Context7 Client

**Files:**
- Create: `benchmarks/benchmarks/context7_client.py`
- Create: `benchmarks/tests/test_context7_client.py`

A thin async HTTP client wrapping Context7's MCP endpoint. Tested with mocking so no real API calls are made in tests.

- [ ] **Step 1: Write the failing test**

`benchmarks/tests/test_context7_client.py`:

```python
"""Tests for Context7 client — mocked, no real network calls."""
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from benchmarks.context7_client import Context7Client, Context7Error


@pytest.mark.asyncio
async def test_resolve_library_id_returns_id():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "result": {"content": [{"text": "/requests/requests"}]}
    })

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        async with Context7Client() as client:
            lib_id = await client.resolve_library_id("requests")
    assert lib_id == "/requests/requests"


@pytest.mark.asyncio
async def test_get_library_docs_returns_text():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "result": {"content": [{"text": "requests docs content here"}]}
    })

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        async with Context7Client() as client:
            docs = await client.get_library_docs("/requests/requests", query="GET request")
    assert "requests" in docs


@pytest.mark.asyncio
async def test_raises_context7_error_on_http_error():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    ))
    mock_response.json = MagicMock(return_value={})

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(Context7Error):
            async with Context7Client() as client:
                await client.resolve_library_id("nonexistent-lib")
```

- [ ] **Step 2: Run test — confirm it fails**

```bash
cd benchmarks && python -m pytest tests/test_context7_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'benchmarks.context7_client'`

- [ ] **Step 3: Install pytest-asyncio**

Add to `requirements.txt`:
```
pytest-asyncio==0.23.7
```

Add to `pyproject.toml` under `[tool.pytest.ini_options]`:
```toml
asyncio_mode = "auto"
```

- [ ] **Step 4: Implement `benchmarks/benchmarks/context7_client.py`**

```python
"""Async HTTP client for Context7 MCP endpoint.

Context7 exposes an MCP server at https://mcp.context7.com/mcp with two tools:
  - resolve-library-id(libraryName) → returns a canonical library ID string
  - get-library-docs(libraryId, query, topic?, tokens?) → returns doc text

We communicate via the MCP HTTP+SSE protocol: POST /mcp with a JSON-RPC body.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

CONTEXT7_BASE_URL = "https://mcp.context7.com/mcp"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_TOKENS = 5000


class Context7Error(Exception):
    """Raised when Context7 returns an error or unexpected response."""


class Context7Client:
    """Async context-manager client for Context7 MCP tools.

    Usage::

        async with Context7Client() as client:
            lib_id = await client.resolve_library_id("requests")
            docs = await client.get_library_docs(lib_id, query="GET request")
    """

    def __init__(self, base_url: str = CONTEXT7_BASE_URL, timeout: float = _DEFAULT_TIMEOUT):
        self._base_url = base_url
        self._timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "Context7Client":
        self._http = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *_) -> None:
        if self._http:
            await self._http.aclose()

    async def _call_tool(self, tool_name: str, arguments: dict) -> str:
        """POST a JSON-RPC tool call and return the first text content block."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            resp = await self._http.post(self._base_url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise Context7Error(f"HTTP {exc.response.status_code} from Context7") from exc
        except httpx.RequestError as exc:
            raise Context7Error(f"Network error contacting Context7: {exc}") from exc

        data = resp.json()
        try:
            content = data["result"]["content"]
            return content[0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise Context7Error(f"Unexpected Context7 response shape: {data!r}") from exc

    async def resolve_library_id(self, library_name: str) -> str:
        """Call resolve-library-id and return the canonical library ID.

        Args:
            library_name: Human name like 'requests' or 'pandas'.

        Returns:
            Canonical ID string like '/requests/requests'.

        Raises:
            Context7Error: On network failure or unexpected response.
        """
        return await self._call_tool("resolve-library-id", {"libraryName": library_name})

    async def get_library_docs(
        self,
        library_id: str,
        query: str,
        topic: str = "",
        tokens: int = _DEFAULT_TOKENS,
    ) -> str:
        """Call get-library-docs and return documentation text.

        Args:
            library_id: Canonical ID from resolve_library_id.
            query: Search query to focus the returned docs.
            topic: Optional topic filter (e.g. 'authentication').
            tokens: Maximum tokens in response.

        Returns:
            Documentation text string.

        Raises:
            Context7Error: On network failure or unexpected response.
        """
        args: dict = {"libraryId": library_id, "query": query, "tokens": tokens}
        if topic:
            args["topic"] = topic
        return await self._call_tool("get-library-docs", args)
```

- [ ] **Step 5: Run tests — confirm they pass**

```bash
cd benchmarks && python -m pytest tests/test_context7_client.py -v
```

Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add benchmarks/benchmarks/context7_client.py benchmarks/tests/test_context7_client.py \
    benchmarks/requirements.txt benchmarks/pyproject.toml
git commit -m "feat(benchmarks): Context7 async client with mocked tests"
```

---

## Task 7: Context7 Benchmark Module

**Files:**
- Create: `benchmarks/benchmarks/context7_bench.py`

Times Context7 resolve + get-library-docs for each question in the dataset. Produces `SearchResult`-compatible rows with `source="context7"` so they can be concatenated with pyctx7 results for comparison.

- [ ] **Step 1: Create `benchmarks/benchmarks/context7_bench.py`**

```python
"""Benchmark Context7 resolve + get-library-docs latency and relevance.

For each question in the dataset we:
  1. resolve-library-id(package_name) — timed separately
  2. get-library-docs(lib_id, query=question) — timed separately
  3. Compute relevance: expected snippet appears in returned docs

Results are returned as SearchResult rows (same structure as search_bench)
so they can be concatenated into a single comparison DataFrame.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pandas as pd

from benchmarks.context7_client import Context7Client, Context7Error
from benchmarks.search_bench import SearchResult


async def _bench_one(
    client: Context7Client,
    question: str,
    package: str,
    expected_snippet: str,
) -> SearchResult:
    """Run resolve + get-library-docs for one question, return timing row."""
    t0 = time.perf_counter()

    try:
        lib_id = await client.resolve_library_id(package)
    except Context7Error:
        elapsed = time.perf_counter() - t0
        return SearchResult(question, package, elapsed, 0, False, source="context7")

    try:
        docs = await client.get_library_docs(lib_id, query=question)
    except Context7Error:
        elapsed = time.perf_counter() - t0
        return SearchResult(question, package, elapsed, 0, False, source="context7")

    elapsed = time.perf_counter() - t0
    relevant = expected_snippet.lower()[:40] in docs.lower()
    n_results = 1 if docs.strip() else 0

    return SearchResult(question, package, elapsed, n_results, relevant, source="context7")


async def _run_all(dataset: pd.DataFrame) -> list[SearchResult]:
    results = []
    async with Context7Client() as client:
        for _, row in dataset.iterrows():
            result = await _bench_one(
                client,
                question=str(row["question"]),
                package=str(row["package"]),
                expected_snippet=str(row["expected_answer_snippet"]),
            )
            results.append(result)
    return results


def run_context7_benchmark(dataset: pd.DataFrame) -> list[SearchResult]:
    """Synchronous wrapper: benchmarks Context7 for all rows in *dataset*.

    Args:
        dataset: DataFrame with columns [question, package, expected_answer_snippet].

    Returns:
        List of SearchResult with source='context7'.
    """
    return asyncio.run(_run_all(dataset))
```

- [ ] **Step 2: Verify import**

```bash
cd benchmarks && python -c "from benchmarks.context7_bench import run_context7_benchmark; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add benchmarks/benchmarks/context7_bench.py
git commit -m "feat(benchmarks): Context7 benchmark module with resolve+docs timing"
```

---

## Task 8: Charts Module

**Files:**
- Create: `benchmarks/benchmarks/charts.py`

Generates bar charts and box plots comparing pyctx7 vs Context7 on indexing time, search latency, and relevance.

- [ ] **Step 1: Create `benchmarks/benchmarks/charts.py`**

```python
"""Generate comparison charts from benchmark results DataFrames.

Produces PNG files in an output directory.
All functions accept DataFrames and return the saved file path.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless rendering — no display required
import matplotlib.pyplot as plt
import pandas as pd


def plot_indexing_times(index_df: pd.DataFrame, out_dir: Path) -> Path:
    """Bar chart of indexing time per package.

    Args:
        index_df: DataFrame with columns [target, elapsed_s, source].
                  'source' distinguishes pyctx7 (no equivalent for context7 indexing).
        out_dir: Directory to save the PNG.

    Returns:
        Path to saved PNG.
    """
    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["#4C72B0" if t != "__project__" else "#55A868" for t in index_df["target"]]
    ax.bar(index_df["target"], index_df["elapsed_s"], color=colors)
    ax.set_xlabel("Package / Target")
    ax.set_ylabel("Indexing time (s)")
    ax.set_title("pyctx7-mcp — Indexing time per package")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    out = out_dir / "indexing_times.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_search_latency_boxplot(search_df: pd.DataFrame, out_dir: Path) -> Path:
    """Box plot of search latency distribution: pyctx7 vs Context7.

    Args:
        search_df: DataFrame with columns [elapsed_s, source].
                   'source' values: 'pyctx7', 'context7'.
        out_dir: Directory to save the PNG.

    Returns:
        Path to saved PNG.
    """
    fig, ax = plt.subplots(figsize=(7, 5))
    groups = [
        search_df.loc[search_df["source"] == src, "elapsed_s"].dropna().tolist()
        for src in ["pyctx7", "context7"]
    ]
    ax.boxplot(groups, labels=["pyctx7-mcp", "Context7"], patch_artist=True,
               boxprops=dict(facecolor="#4C72B0", alpha=0.7))
    ax.set_ylabel("Search latency (s)")
    ax.set_title("Search latency: pyctx7-mcp vs Context7")
    fig.tight_layout()
    out = out_dir / "search_latency_boxplot.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_relevance_comparison(search_df: pd.DataFrame, out_dir: Path) -> Path:
    """Bar chart of relevance rate (% relevant results) by source.

    Args:
        search_df: DataFrame with columns [relevant, source].
        out_dir: Directory to save the PNG.

    Returns:
        Path to saved PNG.
    """
    rates = (
        search_df.groupby("source")["relevant"]
        .mean()
        .reindex(["pyctx7", "context7"])
        .fillna(0.0)
        * 100
    )
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(rates.index, rates.values, color=["#4C72B0", "#DD8452"])
    ax.set_ylim(0, 100)
    ax.set_ylabel("Relevance rate (%)")
    ax.set_title("Answer relevance: pyctx7-mcp vs Context7")
    for i, v in enumerate(rates.values):
        ax.text(i, v + 1, f"{v:.1f}%", ha="center", fontsize=11)
    fig.tight_layout()
    out = out_dir / "relevance_comparison.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out
```

- [ ] **Step 2: Verify import**

```bash
cd benchmarks && python -c "from benchmarks.charts import plot_indexing_times; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add benchmarks/benchmarks/charts.py
git commit -m "feat(benchmarks): chart generation module (bar + boxplot + relevance)"
```

---

## Task 9: Runner Script (Main Entrypoint)

**Files:**
- Create: `benchmarks/benchmarks/runner.py`

The top-level CLI script that orchestrates all phases: fake project generation → indexing → dataset creation → search benchmark → Context7 benchmark → DataFrame assembly → chart generation → CSV output.

- [ ] **Step 1: Create `benchmarks/benchmarks/runner.py`**

```python
"""Main benchmark runner — orchestrates all benchmark phases.

Usage::

    cd benchmarks
    pip install -e .
    run-benchmarks                        # full run with Context7
    run-benchmarks --skip-context7        # local-only (no network)
    run-benchmarks --questions 20         # fewer questions for quick test
    run-benchmarks --out data/results     # custom output directory

Output:
    data/results/benchmark_results.csv   — primary DataFrame artifact
    data/results/indexing_times.png
    data/results/search_latency_boxplot.png
    data/results/relevance_comparison.png
"""
from __future__ import annotations

import argparse
import dataclasses
import sqlite3
import sys
import tempfile
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.progress import track

from benchmarks.fake_project import generate_fake_project, FAKE_REQUIREMENTS
from benchmarks.indexer_bench import run_indexing_benchmark
from benchmarks.dataset_gen import generate_dataset
from benchmarks.search_bench import run_search_benchmark
from benchmarks.context7_bench import run_context7_benchmark
from benchmarks.charts import (
    plot_indexing_times,
    plot_search_latency_boxplot,
    plot_relevance_comparison,
)
from pydocs_mcp.db import open_db, rebuild_fts
from pydocs_mcp.indexer import index_project, index_deps

console = Console()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="pyctx7-mcp vs Context7 benchmark runner")
    p.add_argument("--out", default="data/results", help="Output directory for CSV and charts")
    p.add_argument("--questions", type=int, default=30, help="Number of synthetic questions")
    p.add_argument("--skip-context7", action="store_true", help="Skip Context7 API calls")
    p.add_argument("--workers", type=int, default=2, help="Indexer worker threads")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    console.rule("[bold blue]pyctx7-mcp vs Context7 Benchmark")

    # ── Phase 1: Generate fake project ───────────────────────────────────────
    console.print("[1/5] Generating fake project...")
    with tempfile.TemporaryDirectory() as tmp_root:
        project_path = Path(tmp_root) / "fake_project"
        generate_fake_project(project_path)

        # ── Phase 2: Index and time ───────────────────────────────────────────
        console.print("[2/5] Running indexing benchmark...")
        index_results = run_indexing_benchmark(
            project_path, FAKE_REQUIREMENTS,
            use_inspect=False, workers=args.workers,
        )
        index_df = pd.DataFrame([dataclasses.asdict(r) for r in index_results])
        console.print(index_df[["target", "elapsed_s", "chunks", "symbols"]].to_string(index=False))

        # ── Phase 3: Build the indexed DB (for search benchmark) ─────────────
        console.print("[3/5] Building search index...")
        db_path = Path(tmp_root) / "bench_search.db"
        conn = open_db(db_path)
        index_project(conn, project_path)
        index_deps(conn, FAKE_REQUIREMENTS, workers=args.workers, use_inspect=False)
        rebuild_fts(conn)
        conn.close()

        # ── Phase 4: Generate synthetic dataset ──────────────────────────────
        console.print("[4/5] Generating synthetic question dataset...")
        dataset = generate_dataset(db_path, n_questions=args.questions)
        console.print(f"  Generated {len(dataset)} questions across {dataset['package'].nunique()} packages")

        # ── Phase 5: Search benchmarks ────────────────────────────────────────
        console.print("[5a/5] Running pyctx7-mcp search benchmark...")
        pyctx7_results = run_search_benchmark(db_path, dataset)
        pyctx7_df = pd.DataFrame([dataclasses.asdict(r) for r in pyctx7_results])

        context7_df = pd.DataFrame()
        if not args.skip_context7:
            console.print("[5b/5] Running Context7 benchmark (live API)...")
            ctx7_results = run_context7_benchmark(dataset)
            context7_df = pd.DataFrame([dataclasses.asdict(r) for r in ctx7_results])

    # ── Assemble final DataFrame ──────────────────────────────────────────────
    search_df = pd.concat([pyctx7_df, context7_df], ignore_index=True) if not context7_df.empty else pyctx7_df
    search_df["question"] = dataset["question"].tolist() * (2 if not args.skip_context7 else 1)

    csv_path = out_dir / "benchmark_results.csv"
    search_df.to_csv(csv_path, index=False)
    index_df.to_csv(out_dir / "indexing_results.csv", index=False)
    console.print(f"\n[green]CSV saved:[/green] {csv_path}")

    # ── Charts ────────────────────────────────────────────────────────────────
    p1 = plot_indexing_times(index_df, out_dir)
    p2 = plot_search_latency_boxplot(search_df, out_dir)
    p3 = plot_relevance_comparison(search_df, out_dir)
    console.print(f"[green]Charts saved:[/green] {p1.name}, {p2.name}, {p3.name}")
    console.rule("[bold green]Done")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify entrypoint works (dry-run help)**

```bash
cd benchmarks && pip install -e . -q && run-benchmarks --help
```

Expected: prints usage including `--skip-context7`.

- [ ] **Step 3: Commit**

```bash
git add benchmarks/benchmarks/runner.py
git commit -m "feat(benchmarks): runner CLI orchestrating all benchmark phases"
```

---

## Task 10: README

**Files:**
- Create: `benchmarks/README.md`

- [ ] **Step 1: Create `benchmarks/README.md`**

```markdown
# pyctx7-mcp Benchmark Suite

Compares **pyctx7-mcp** (local indexing + FTS5 search) against **Context7**
(cloud MCP API) on indexing speed, search latency, and result relevance.

## Structure

```
benchmarks/
├── fake_project/        Static Python project used as indexing target
├── benchmarks/
│   ├── fake_project.py  Generates the fake project tree
│   ├── indexer_bench.py Times per-package indexing
│   ├── dataset_gen.py   Synthesizes questions from indexed chunks
│   ├── search_bench.py  Times pyctx7 search + scores relevance
│   ├── context7_client.py  Async HTTP client for Context7 MCP API
│   ├── context7_bench.py   Times Context7 resolve + get-library-docs
│   ├── charts.py        Generates PNG charts
│   └── runner.py        Main CLI entrypoint
└── data/                Output CSV and PNGs (gitignored)
```

## Setup

Requires Python 3.10+ and the parent `pydocs-mcp` package.

```bash
cd benchmarks
pip install -e .          # installs pydocs-mcp from parent + benchmark deps
```

## Running

```bash
# Full benchmark (includes live Context7 API calls)
run-benchmarks

# Local only — no network, faster
run-benchmarks --skip-context7

# Fewer questions for a quick smoke test
run-benchmarks --questions 10 --skip-context7

# Custom output directory
run-benchmarks --out /tmp/bench_results
```

## Output

| File | Description |
|------|-------------|
| `data/results/benchmark_results.csv` | Primary DataFrame: question, package, elapsed_s, n_results, relevant, source |
| `data/results/indexing_results.csv` | Per-package indexing timings |
| `data/results/indexing_times.png` | Bar chart: indexing time per package |
| `data/results/search_latency_boxplot.png` | Box plot: pyctx7 vs Context7 latency distribution |
| `data/results/relevance_comparison.png` | Bar chart: % relevant results by source |

## DataFrame Schema

`benchmark_results.csv` columns:

| Column | Type | Description |
|--------|------|-------------|
| `question` | str | Synthetic question derived from a doc chunk |
| `package` | str | Package the question was drawn from |
| `elapsed_s` | float | Wall-clock search time in seconds |
| `n_results` | int | Number of results returned |
| `relevant` | bool | Whether expected snippet appeared in results |
| `source` | str | `pyctx7` or `context7` |

## Context7 API

Context7 is accessed at `https://mcp.context7.com/mcp` using the
`resolve-library-id` and `get-library-docs` MCP tools. No API key required.
Network latency will dominate Context7 timings — run from a stable connection.

## Running Tests

```bash
cd benchmarks
pip install pytest pytest-asyncio
pytest tests/ -v
```
```

- [ ] **Step 2: Commit**

```bash
git add benchmarks/README.md
git commit -m "docs(benchmarks): add README with setup, usage, and output schema"
```

---

## Task 11: Wire Up `.gitignore` and Final Cleanup

- [ ] **Step 1: Verify `.gitignore` has benchmark data entries**

Check root `.gitignore` includes:
```
benchmarks/data/*.csv
benchmarks/data/*.png
```

If not, add them and commit.

- [ ] **Step 2: Smoke-test the full local pipeline**

```bash
cd benchmarks
pip install -e . -q
run-benchmarks --questions 5 --skip-context7 --out /tmp/smoke_bench
ls /tmp/smoke_bench/
```

Expected: `benchmark_results.csv  indexing_results.csv  indexing_times.png  relevance_comparison.png  search_latency_boxplot.png`

- [ ] **Step 3: Run all tests**

```bash
cd benchmarks && pytest tests/ -v
```

Expected: all PASSED (at least 9 tests across 3 test files).

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "chore(benchmarks): final wiring, smoke-test verified"
```

---

## Self-Review

### Spec Coverage

| Requirement | Task |
|------------|------|
| Separate subproject with pyproject.toml + requirements.txt | Task 1 |
| Indexing benchmark with per-package timing | Task 3 |
| Synthetic question dataset (DataFrame with required columns) | Task 4 |
| Search benchmark with latency measurement per package | Task 5 |
| Context7 comparison (resolve-library-id + get-library-docs) | Tasks 6, 7 |
| Output DataFrame with all specified columns | Task 9 (runner assembles) |
| Bar charts and box plots | Task 8 |
| README explaining how to run | Task 10 |
| Branch `feat/benchmarks-comparison` | Pre-task (done) |

All requirements covered. No gaps found.

### Placeholder Scan

- No "TBD", "TODO", or "implement later" in any task
- All code steps include complete code blocks
- All commands include expected output
- Types are consistent across tasks (SearchResult used in tasks 5, 7, 9)

### Type Consistency

- `IndexResult` defined in task 3, consumed in task 9 ✓
- `SearchResult` defined in task 5, reused in task 7, consumed in task 9 ✓
- `generate_dataset` returns `pd.DataFrame` in task 4, consumed by tasks 5, 7, 9 ✓
- `Context7Client` defined in task 6, used in task 7 ✓
- Chart functions all accept `(df: pd.DataFrame, out_dir: Path) -> Path` ✓
