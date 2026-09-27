"""Pins on the REAL packaged ``example-needle-chat`` records.

Read through ``importlib.resources`` exactly as a built wheel resolves them. The
ten ``dev`` questions are the repro questions verbatim; ``test`` and ``reserved``
stay empty until the held-out set lands (#369), which flips those counts.

``tree_9c170b0.json`` maps every corpus file of the pinned commit to its line
count — built from GitHub's archive of that commit (``gh api
repos/msobroza/example_needle/tarball/<sha>``), filtered by the loader's own
corpus globs — so "every gold site lies in the pinned checkout" is checked here
without a clone.
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib.resources as ir
import json
import re
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.example_needle_chat import CORPUS_GLOBS, ChatQuestionShape
from pydocs_eval.registries import dataset_registry

_PACKAGE = "pydocs_eval.datasets.data.example_needle_chat"
_TREE = Path(__file__).parents[1] / "fixtures" / "example_needle_chat" / "tree_9c170b0.json"
_REPO_URL = "https://github.com/msobroza/example_needle.git"
_COMMIT = "9c170b02fe93a759ddf5739b777e07e3c162b12b"
_GATE_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

# The repro runner's questions, verbatim and in order (the dev slice's source).
_REPRO_QUESTIONS = (
    "Where is the MaxSim late-interaction score computed and what does it return?",
    "How are metadata filters applied during search? Where is the filter mask built and how "
    "does it interact with top_k?",
    "How does needle avoid importing torch when I just import the package?",
    "What are all the places I need to change to add a new retriever backend?",
    "How do I index a folder of PDFs and then search it restricted to documents from 2024 or "
    "later? Give me an example.",
    "What is the difference between the pickle, numpy and in-memory index stores, and when "
    "should I use each?",
    "What exceptions can a search raise, and where are they raised?",
    "Walk me through what happens from the pipeline entry point to the top-k hits: which "
    "functions are called, in order?",
    "Which configuration options control rendering DPI and batch size, and where are their "
    "defaults defined?",
    "How does pagination of results work?",
)

# Regenerated ONLY when the records change — and a gold edit is then a visible diff.
_RECORDS_SHA256 = "6baef6f6cef1317a56c2f29bb58cb48f33c0f264123f226e023798122d1c9770"
# sha256 of the sorted "task_id<TAB>split" lines, first 16 hex: the literal slice
# membership (the reserved draw of #369 lands here too).
_SPLIT_MEMBERSHIP_DIGEST = "954a625b2bdd4db2"


def _raw_records() -> bytes:
    return ir.files(_PACKAGE).joinpath("records.jsonl").read_bytes()


def _records() -> list[dict]:
    text = _raw_records().decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def _tasks(split: str) -> list[EvalTask]:
    dataset = dataset_registry.build("example-needle-chat", split=split)
    return [task async for task in dataset.tasks()]


def _tree() -> dict[str, int]:
    return json.loads(_TREE.read_text(encoding="utf-8"))


async def test_the_dev_slice_is_the_ten_repro_questions_verbatim_in_order() -> None:
    tasks = await _tasks("dev")

    assert tuple(task.query for task in tasks) == _REPRO_QUESTIONS
    assert [task.task_id for task in tasks] == [f"example-needle-chat/q{i:02d}" for i in range(10)]


@pytest.mark.parametrize(
    ("split", "count"), [("test", 0), ("reserved", 0), ("held_out", 0), ("all", 10)]
)
async def test_the_held_out_slices_stay_empty_until_step_1b(split: str, count: int) -> None:
    assert len(await _tasks(split)) == count


async def test_every_record_carries_the_pins() -> None:
    for record, task in zip(_records(), await _tasks("all"), strict=True):
        assert (record["repo_url"], record["commit"]) == (_REPO_URL, _COMMIT), task.task_id
        assert task.metadata["repo"] == "msobroza/example_needle"
        assert task.metadata["commit"] == _COMMIT
        assert task.metadata["gold_embedder_model"] == "qwen/qwen3-embedding-4b"
        assert task.metadata["gold_embedder_dim"] == "2560"
        assert task.metadata["gold_source"] == "curated"
        assert task.metadata["query_source"] == "agent"
        assert task.metadata["gold_ratified"] in ("true", "false")


async def test_every_shape_is_covered_by_the_dev_slice() -> None:
    shapes = {task.metadata["shape"] for task in await _tasks("dev")}

    assert shapes == {shape.value for shape in ChatQuestionShape}


async def test_every_gold_site_lies_inside_the_pinned_tree() -> None:
    tree = _tree()
    for task in await _tasks("all"):
        spans = [value for key, value in task.metadata.items() if key.startswith("site_")]
        assert spans, task.task_id
        for span in spans:
            path, lines = span.rsplit(":", 1)
            start, end = (int(part) for part in lines.split("-"))
            assert path in tree, f"{task.task_id}: {path} is not in the pinned checkout"
            assert 1 <= start <= end <= tree[path], f"{task.task_id}: {span} runs past EOF"


async def test_every_gold_path_is_materialized_by_the_corpus() -> None:
    for task in await _tasks("all"):
        assert task.gold.file_set, task.task_id
        for path in task.gold.file_set:
            assert any(fnmatch.fnmatch(Path(path).name, glob) for glob in CORPUS_GLOBS), path


async def test_gold_extra_holds_only_gate_safe_symbols() -> None:
    for task in await _tasks("all"):
        for key, value in task.gold.extra.items():
            assert key.startswith("symbol_") and isinstance(value, str), task.task_id
            assert _GATE_SAFE.fullmatch(value), f"{task.task_id}: {value!r} is not an identifier"


async def test_the_gold_covers_the_sites_the_repro_answers_missed() -> None:
    """Spec §Problem 4: q01 missed the second mask in pipeline.py; q03 missed README.md."""
    tasks = {task.task_id: task for task in await _tasks("dev")}

    def spans(task_id: str) -> set[str]:
        metadata = tasks[f"example-needle-chat/{task_id}"].metadata
        return {value for key, value in metadata.items() if key.startswith("site_")}

    assert any(span.startswith("src/needle/pipeline.py:116-") for span in spans("q01"))
    assert "README.md:97-106" in spans("q03")


def test_the_records_are_byte_pinned() -> None:
    assert hashlib.sha256(_raw_records()).hexdigest() == _RECORDS_SHA256


def test_the_split_membership_is_pinned() -> None:
    lines = sorted(f"{rec['task_id']}\t{rec['metadata']['split']}" for rec in _records())
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]

    assert digest == _SPLIT_MEMBERSHIP_DIGEST
