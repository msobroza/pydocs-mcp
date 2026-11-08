"""Named fakes for the before/after corpus-workspace tests: two tiny corpora, a fake indexer.

Nothing here spends anything: the indexer writes a genuine (tiny) stamped
bundle where the product would, and records which corpora it was asked to
index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.campaign.before_after_corpora import IndexIdentity, plan_task_workspaces
from pydocs_eval.campaign.index_cache import canonical_index_paths
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.datasets.corpus import materialize_corpus

EMBEDDER_MODEL = "qwen/qwen3-embedding-4b"
EMBEDDER_DIM = 2560
SCOPE_ID = "0123456789abcdef"
INDEX_IDENTITY = IndexIdentity(
    embedder_model=EMBEDDER_MODEL, embedder_dim=EMBEDDER_DIM, scope_id=SCOPE_ID
)

# Two tiny corpora: 40 source bytes each, so the token estimate is checkable.
ALPHA_FILES = {"a.py": "x" * 20, "pkg/b.py": "y" * 20}
BETA_FILES = {"c.py": "z" * 40}


def corpus_task(task_id: str, repo: str | None, commit: str, files: dict[str, str]) -> EvalTask:
    metadata = {"repo": repo, "commit": commit} if repo is not None else {}
    return EvalTask(
        task_id=task_id,
        query="where is the needle?",
        gold=GoldAnswer(file_set=("a.py",)),
        corpus_source=lambda f=files: materialize_corpus(f),  # type: ignore[misc]
        metadata=metadata,
    )


def three_tasks_over_two_corpora() -> tuple[EvalTask, ...]:
    return (
        corpus_task("t1", "psf/black", "a" * 40, ALPHA_FILES),
        corpus_task("t2", "psf/black", "a" * 40, ALPHA_FILES),
        corpus_task("t3", "openai/openai-python", "b" * 40, BETA_FILES),
    )


@dataclass(slots=True)
class FakeCorpusIndexer:
    """Writes a genuine stamped (but empty) bundle where the product would.

    Records every corpus it was asked to index so a test can prove a corpus is
    built once, never once per task and never twice across runs.
    """

    model: str = EMBEDDER_MODEL
    dim: int = EMBEDDER_DIM
    indexed: list[Path] = field(default_factory=list)

    def __call__(self, source_dir: Path, cache_root: Path) -> tuple[Path, Path]:
        from pydocs_mcp.db import open_index_database
        from pydocs_mcp.storage.index_metadata import IndexMetadata, write_index_metadata

        self.indexed.append(source_dir)
        db, tq = canonical_index_paths(source_dir, cache_root)
        db.parent.mkdir(parents=True, exist_ok=True)
        connection = open_index_database(db)
        try:
            write_index_metadata(
                connection,
                IndexMetadata(
                    project_name=source_dir.name,
                    project_root=str(source_dir),
                    embedding_provider="openai",
                    embedding_model=self.model,
                    embedding_dim=self.dim,
                    pipeline_hash=SCOPE_ID,
                    indexed_at=1.0,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        tq.write_bytes(b"vectors")
        return db, tq


def plan_workspaces(root: Path, tasks: tuple[EvalTask, ...] = ()):
    return plan_task_workspaces(
        tasks or three_tasks_over_two_corpora(),
        workspace=root,
        identity=INDEX_IDENTITY,
        usd_per_1m_embed=0.02,
    )
