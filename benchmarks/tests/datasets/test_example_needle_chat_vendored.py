"""Pins on the REAL packaged ``example-needle-chat`` records.

Read through ``importlib.resources`` exactly as a built wheel resolves them. The
ten ``dev`` questions are the repro questions verbatim; the thirty held-out
questions are 20 ``test`` and 10 ``reserved``, the reserved ten drawn once at
authoring time and stored as literals — so a test here re-runs that draw and
fails when a stored split disagrees.

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
from collections import Counter
from pathlib import Path

import pytest

import pydocs_mcp
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.example_needle_chat import CORPUS_GLOBS, ChatQuestionShape
from pydocs_eval.datasets.example_needle_chat_reserved import draw_reserved
from pydocs_eval.registries import dataset_registry

_PACKAGE = "pydocs_eval.datasets.data.example_needle_chat"
_TREE = Path(__file__).parents[1] / "fixtures" / "example_needle_chat" / "tree_9c170b0.json"
# Authoring records, not package data: read from the source tree.
_DATA_DIR = Path(str(ir.files(_PACKAGE)))
_AUTHORING_PROMPT = _DATA_DIR / "authoring_prompt.md"
_ACCOUNTING = _DATA_DIR / "held_out_gold_accounting.jsonl"
_REPO_URL = "https://github.com/msobroza/example_needle.git"
_COMMIT = "9c170b02fe93a759ddf5739b777e07e3c162b12b"
_GATE_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
# Question words and glue: a shape's stem ("what is the ... and when") is not overlap.
_STOPWORDS = frozenset(
    {"a", "and", "do", "does", "how", "i", "is", "the", "what", "when", "where", "which"}
)

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
_RECORDS_SHA256 = "0a8f5b761937a950127ddaabbadafb0546129bbcf51c22d2871dea12542bf91c"
# sha256 of the sorted "task_id<TAB>split" lines, first 16 hex: the literal slice
# membership, the one-time reserved draw included.
_SPLIT_MEMBERSHIP_DIGEST = "70811567aaa9dafc"


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


def _site_spans(task: EvalTask) -> list[str]:
    """The task's ``site_<i>`` spans, ``path:start-end`` each."""
    return [value for key, value in task.metadata.items() if key.startswith("site_")]


async def test_the_dev_slice_is_the_ten_repro_questions_verbatim_in_order() -> None:
    tasks = await _tasks("dev")

    assert tuple(task.query for task in tasks) == _REPRO_QUESTIONS
    assert [task.task_id for task in tasks] == [f"example-needle-chat/q{i:02d}" for i in range(10)]


@pytest.mark.parametrize(
    ("split", "count"),
    [("dev", 10), ("test", 20), ("reserved", 10), ("held_out", 30), ("all", 40)],
)
async def test_each_slice_holds_its_records(split: str, count: int) -> None:
    assert len(await _tasks(split)) == count


async def test_the_held_out_set_is_test_and_reserved_after_the_dev_questions() -> None:
    held_out = [task.task_id for task in await _tasks("held_out")]
    test = {task.task_id for task in await _tasks("test")}
    reserved = {task.task_id for task in await _tasks("reserved")}

    assert set(held_out) == test | reserved and not test & reserved
    assert held_out == [f"example-needle-chat/q{i:02d}" for i in range(10, 40)]


async def test_every_question_is_asked_once() -> None:
    queries = [task.query for task in await _tasks("all")]

    assert len(set(queries)) == len(queries)


async def test_every_shape_is_held_out_twice_and_reserved_once() -> None:
    shapes = {shape.value for shape in ChatQuestionShape}
    held_out = Counter(task.metadata["shape"] for task in await _tasks("held_out"))

    assert set(held_out) == shapes
    assert min(held_out.values()) >= 2, held_out
    assert {task.metadata["shape"] for task in await _tasks("reserved")} == shapes


async def test_at_least_eight_held_out_questions_need_two_or_more_files() -> None:
    held_out = await _tasks("held_out")

    assert sum(int(task.metadata["gold_file_count"]) >= 2 for task in held_out) >= 8


async def test_the_reserved_slice_is_the_seeded_draw_over_the_held_out_set() -> None:
    held_out = {task.task_id: task.metadata["shape"] for task in await _tasks("held_out")}

    assert draw_reserved(held_out) == {task.task_id for task in await _tasks("reserved")}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _content_words(question: str) -> set[str]:
    return set(_words(question)) - _STOPWORDS


async def test_no_held_out_question_shares_half_its_words_with_a_dev_one() -> None:
    """A floor under the reviewer check that no held-out question paraphrases a dev one."""
    dev_words = [(task.task_id, _content_words(task.query)) for task in await _tasks("dev")]
    for held in await _tasks("held_out"):
        ours = _content_words(held.query)
        for dev_id, theirs in dev_words:
            overlap = len(ours & theirs) / len(ours | theirs)
            assert overlap < 0.5, (held.task_id, dev_id, sorted(ours & theirs))


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
        spans = _site_spans(task)
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

    q01_spans = _site_spans(tasks["example-needle-chat/q01"])
    q03_spans = _site_spans(tasks["example-needle-chat/q03"])

    assert any(span.startswith("src/needle/pipeline.py:116-") for span in q01_spans)
    assert "README.md:97-106" in q03_spans


def _shingles(text: str, size: int) -> set[tuple[str, ...]]:
    """Every run of ``size`` consecutive words, lowercased, template markup dropped."""
    words = _words(re.sub(r"\{[{%#].*?[}%#]\}", " ", text))
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def _chat_agent_prompt_files() -> list[Path]:
    """Every prompt the chat agent under test can be given, in the product tree."""
    root = Path(pydocs_mcp.__file__).parent
    return [*sorted((root / "harness").rglob("*.j2")), root / "defaults" / "descriptions.md"]


def test_the_authoring_prompt_shares_no_text_with_the_chat_agent_prompts() -> None:
    """The held-out set is written from shapes and code, never from what it measures."""
    authoring = _shingles(_AUTHORING_PROMPT.read_text(encoding="utf-8"), 8)
    prompts = _chat_agent_prompt_files()

    assert any(path.name == "system_v2.j2" for path in prompts), "the prompt tree moved"
    for path in prompts:
        shared = authoring & _shingles(path.read_text(encoding="utf-8"), 8)
        assert not shared, f"{path.name} shares {sorted(shared)[:3]}"


async def test_the_authoring_prompt_quotes_no_dev_question() -> None:
    # Six words, not five: a shape's own stem ("what is the difference between")
    # opens any question of that shape, so five-word runs flag the template.
    authoring = _shingles(_AUTHORING_PROMPT.read_text(encoding="utf-8"), 6)

    for task in await _tasks("dev"):
        assert not authoring & _shingles(task.query, 6), task.task_id


def _accounting() -> dict[str, dict]:
    lines = _ACCOUNTING.read_text(encoding="utf-8").splitlines()
    return {entry["task_id"]: entry for entry in map(json.loads, lines)}


def _inside_a_site(path: str, line: int, sites: list[dict]) -> bool:
    return any(s["path"] == path and s["start"] <= line <= s["end"] for s in sites)


def _excluded_hits(entry: dict) -> list[tuple[str, str]]:
    """Every ``(reason, "path:line")`` the entry leaves out, across its identifiers."""
    return [
        (reason, hit)
        for block in entry["grep"]
        for reason, hits in block["excluded"].items()
        for hit in hits
    ]


async def test_every_held_out_gold_site_comes_with_its_grep_accounting() -> None:
    """The authoring record matches the frozen gold: a changed site needs new accounting.

    A grep hit inside a gold site is accounted for by that site; every other
    non-test hit of an identifier is listed with the reason it was left out.
    """
    accounting, tree = _accounting(), _tree()
    records = {record["task_id"]: record for record in _records()}
    held_out = [task.task_id for task in await _tasks("held_out")]

    assert sorted(accounting) == sorted(held_out)
    for task_id in held_out:
        entry = accounting[task_id]
        sites = [
            {k: site[k] for k in ("path", "start", "end", "symbol")} for site in entry["sites"]
        ]
        assert sites == records[task_id]["gold"]["sites"], task_id
        assert all(site["why"].strip() for site in entry["sites"]), task_id
        for reason, hit in _excluded_hits(entry):
            path, line = hit.rsplit(":", 1)
            assert reason.strip() and path in tree, (task_id, hit)
            assert 1 <= int(line) <= tree[path], (task_id, hit)
            assert not _inside_a_site(path, int(line), sites), (task_id, hit)


def test_the_records_are_byte_pinned() -> None:
    assert hashlib.sha256(_raw_records()).hexdigest() == _RECORDS_SHA256


def test_the_split_membership_is_pinned() -> None:
    lines = sorted(f"{rec['task_id']}\t{rec['metadata']['split']}" for rec in _records())
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]

    assert digest == _SPLIT_MEMBERSHIP_DIGEST
