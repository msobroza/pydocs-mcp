"""CrossCommitVuln QA dataset loader (design §6.1) — vendored records,
lazy git-checkout corpus.

Mirrors ``swe_qa_pro.py`` with two deliberate differences: (a) records are
VENDORED and read via ``importlib.resources`` (resolves in a built wheel,
not just a source checkout) — no download, no on-disk cache; (b) the
checkout SHA is the pinned pre-fix parent (``prefix_sha``) stored in the
vendored record. Hard invariant (design §5.0): one record <-> one
``repo_url`` <-> one 40-hex ``prefix_sha``; the corpus is materialized
HISTORY-LESS (``read_checkout_files`` -> ``materialize_corpus``, no
``.git``), so no commit signal is observable by the model.
"""

from __future__ import annotations

import importlib.resources as ir
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registries import dataset_registry
from ._crosscommitvuln_build import gold_from_record
from ._repo_cache import RepoCache, RepoCacheLike, read_checkout_files
from .base_dataset import EvalTask
from .corpus import materialize_corpus

log = logging.getLogger(__name__)

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataset_registry.register("crosscommitvuln")
@dataclass
class CrossCommitVulnDataset:
    """CrossCommitVuln-Bench transformed to QA (CC BY 4.0; see vendored NOTICE)."""

    name: str = "crosscommitvuln"
    revision: str = "1.0"
    fixture_path: Path | None = None
    # WHY: injected so tests pass a no-git/no-network fake; production wiring
    # gets the real ``RepoCache`` by default (one clone per repo across rounds).
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-eval").expanduser(),
    )

    async def tasks(self) -> AsyncIterator[EvalTask]:
        records = self._read_records()
        vendored, dropped = 0, 0
        for rec in records:
            task = self._record_to_task(rec)
            if task is None:
                dropped += 1
                continue
            vendored += 1
            yield task
        # No-silent-caps: an operator must see the vendored count and how
        # many records were dropped as malformed (design §4.3).
        log.info(
            "crosscommitvuln: vendored %d task(s), excluded %d malformed record(s)",
            vendored,
            dropped,
        )

    def _read_records(self) -> list[dict[str, Any]]:
        if self.fixture_path is not None:
            text = self.fixture_path.read_text()
        else:
            text = (
                ir.files("pydocs_eval.datasets.data.crosscommitvuln")
                .joinpath("records.jsonl")
                .read_text()
            )
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _record_to_task(self, rec: dict[str, Any]) -> EvalTask | None:
        url = rec.get("repo_url")
        sha = rec.get("prefix_sha")
        # §5.0 invariant, enforced by shape: exactly one repo URL string and
        # exactly one full 40-hex pinned snapshot per record.
        if not isinstance(url, str) or not url:
            log.info("crosscommitvuln: dropping %r — repo_url %r", rec.get("task_id"), url)
            return None
        if not isinstance(sha, str) or not _SHA40.fullmatch(sha):
            log.info(
                "crosscommitvuln: dropping %r — prefix_sha %r is not 40-hex",
                rec.get("task_id"),
                sha,
            )
            return None
        return EvalTask(
            task_id=rec["task_id"],
            query=rec["query"],
            gold=gold_from_record(rec),
            # Default-arg closure captures this record's pin; checkout + copy
            # happen lazily so a task that's never scored costs no clone.
            corpus_source=lambda u=url, c=sha: materialize_corpus(
                read_checkout_files(self.repo_cache.checkout(u, c))
            ),
            metadata=dict(rec.get("metadata", {})),
        )
