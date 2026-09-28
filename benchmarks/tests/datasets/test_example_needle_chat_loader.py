"""The ``example-needle-chat`` loader over fixture records — no git, no network.

The chat slice's records name their gold as sites (``path``, ``start``, ``end``,
``symbol``). The loader derives everything consumers read from that one list —
``GoldAnswer.file_set``, the gate-safe ``extra`` symbols, ``metadata.site_i`` and
``metadata.gold_file_count`` — so a record can never disagree with itself, and it
validates every closed vocabulary before a task is yielded.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.example_needle_chat import (
    ChatDatasetError,
    ChatSplit,
    ExampleNeedleChatDataset,
)

_URL = "https://github.com/msobroza/example_needle.git"
_SHA = "9c170b02fe93a759ddf5739b777e07e3c162b12b"
_SITE = {"path": "src/needle/a.py", "start": 3, "end": 9, "symbol": "alpha"}


def _record(task: str, *, split: str = "dev", sites: list[dict] | None = None, **meta: str) -> dict:
    return {
        "task_id": f"example-needle-chat/{task}",
        "repo_url": _URL,
        "commit": _SHA,
        "query": f"What does {task} do?",
        "gold": {"sites": sites if sites is not None else [_SITE]},
        "metadata": {
            "gold_embedder_model": "qwen/qwen3-embedding-4b",
            "gold_embedder_dim": "2560",
            "split": split,
            "shape": "where",
            "gold_source": "curated",
            "gold_ratified": "false",
            "query_source": "agent",
            **meta,
        },
    }


@dataclass
class FakeRepoCache:
    """A pinned checkout on disk, served without git; records every checkout asked for."""

    root: Path
    checked_out: list[tuple[str, str]] = field(default_factory=list)

    def checkout(self, url: str, sha: str) -> Path:
        self.checked_out.append((url, sha))
        return self.root

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return tuple(sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")))


async def _tasks(
    tmp_path: Path, records: list[dict], cache: FakeRepoCache | None = None, **kwargs: object
) -> list[EvalTask]:
    fixture = tmp_path / "records.jsonl"
    fixture.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    repo_cache = cache or FakeRepoCache(root=tmp_path / "checkout")
    dataset = ExampleNeedleChatDataset(fixture_path=fixture, repo_cache=repo_cache, **kwargs)
    return [task async for task in dataset.tasks()]


async def test_a_slice_yields_its_records_verbatim_in_file_order(tmp_path: Path) -> None:
    records = [_record("q00"), _record("h00", split="test"), _record("q01")]

    tasks = await _tasks(tmp_path, records, split="dev")

    assert [task.task_id for task in tasks] == [
        "example-needle-chat/q00",
        "example-needle-chat/q01",
    ]
    assert [task.query for task in tasks] == ["What does q00 do?", "What does q01 do?"]


async def test_the_gold_is_derived_from_the_sites(tmp_path: Path) -> None:
    sites = [
        _SITE,
        {"path": "src/needle/a.py", "start": 20, "end": 24, "symbol": "beta"},
        {"path": "README.md", "start": 97, "end": 106, "symbol": "retriever"},
    ]

    (task,) = await _tasks(tmp_path, [_record("q03", sites=sites)])

    assert task.gold.file_set == ("src/needle/a.py", "README.md")
    assert task.gold.extra == {"symbol_0": "alpha", "symbol_1": "beta", "symbol_2": "retriever"}
    assert task.metadata["gold_file_count"] == "2"
    assert [task.metadata[f"site_{i}"] for i in range(3)] == [
        "src/needle/a.py:3-9",
        "src/needle/a.py:20-24",
        "README.md:97-106",
    ]


async def test_every_task_carries_the_pins(tmp_path: Path) -> None:
    (task,) = await _tasks(tmp_path, [_record("q00")])

    assert {key: task.metadata[key] for key in ("repo", "commit")} == {
        "repo": "msobroza/example_needle",
        "commit": _SHA,
    }
    assert task.metadata["gold_embedder_model"] == "qwen/qwen3-embedding-4b"
    assert task.metadata["gold_embedder_dim"] == "2560"
    assert (task.metadata["split"], task.metadata["shape"]) == ("dev", "where")
    assert (task.metadata["gold_source"], task.metadata["query_source"]) == ("curated", "agent")
    assert task.metadata["gold_ratified"] == "false"


@pytest.mark.parametrize(
    ("split", "expected"),
    [
        (ChatSplit.TEST, ["t0"]),
        (ChatSplit.RESERVED, ["r0"]),
        (ChatSplit.HELD_OUT, ["t0", "r0"]),
        (ChatSplit.ALL, ["q0", "t0", "r0"]),
    ],
)
async def test_the_composite_slices_are_unions_of_the_literal_ones(
    tmp_path: Path, split: ChatSplit, expected: list[str]
) -> None:
    records = [_record("q0"), _record("t0", split="test"), _record("r0", split="reserved")]

    tasks = await _tasks(tmp_path, records, split=split)

    assert [task.task_id.rsplit("/", 1)[1] for task in tasks] == expected


async def test_an_unknown_slice_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(ChatDatasetError, match="small_dev") as excinfo:
        await _tasks(tmp_path, [_record("q00")], split="small_dev")

    assert "held_out" in str(excinfo.value), "the message lists the accepted slices"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("shape", "wherever"),
        ("gold_source", "llm"),
        ("query_source", "bot"),
        ("gold_ratified", "yes"),
        # A record's slice is a literal; the composite names are selections, never stored.
        ("split", "held_out"),
    ],
)
async def test_a_value_outside_its_vocabulary_names_the_value_and_the_accepted_set(
    tmp_path: Path, key: str, value: str
) -> None:
    with pytest.raises(ChatDatasetError, match=repr(value)) as excinfo:
        await _tasks(tmp_path, [_record("q00", **{key: value})], split="all")

    assert "q00" in str(excinfo.value), "the message names the offending record"
    assert "expected one of" in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    [
        _URL,
        "https://github.com/msobroza/example_needle",
        "https://github.com/msobroza/example_needle.git/",
    ],
)
async def test_the_repo_slug_is_the_same_however_the_url_ends(tmp_path: Path, url: str) -> None:
    (task,) = await _tasks(tmp_path, [{**_record("q00"), "repo_url": url}])

    assert task.metadata["repo"] == "msobroza/example_needle"


async def test_a_commit_that_is_not_40_hex_is_refused(tmp_path: Path) -> None:
    record = {**_record("q00"), "commit": "9c170b0"}

    with pytest.raises(ChatDatasetError, match="9c170b0"):
        await _tasks(tmp_path, [record])


@pytest.mark.parametrize(
    "site",
    [
        {"path": "src/needle/a.py", "start": 9, "end": 3, "symbol": "alpha"},
        {"path": "src/needle/a.py", "start": 0, "end": 3, "symbol": "alpha"},
        {"path": "src/needle/a.py", "start": 3, "end": 9, "symbol": "not an identifier"},
    ],
)
async def test_a_malformed_site_is_refused(tmp_path: Path, site: dict) -> None:
    with pytest.raises(ChatDatasetError, match="q00"):
        await _tasks(tmp_path, [_record("q00", sites=[site])])


async def test_a_record_without_sites_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ChatDatasetError, match="q00"):
        await _tasks(tmp_path, [_record("q00", sites=[])])


async def test_the_corpus_is_the_pinned_checkout_with_docs_and_config_files(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    for rel, body in {
        "src/needle/a.py": "def alpha(): ...\n",
        "README.md": "# needle\n",
        "pyproject.toml": "[project]\n",
        "docs/logo.png": "not text\n",
    }.items():
        (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
        (checkout / rel).write_text(body, encoding="utf-8")
    cache = FakeRepoCache(root=checkout)
    (task,) = await _tasks(tmp_path, [_record("q00")], cache=cache)

    corpus = task.corpus_source()
    try:
        materialized = sorted(
            p.relative_to(corpus).as_posix() for p in corpus.rglob("*") if p.is_file()
        )
    finally:
        shutil.rmtree(corpus)
    assert materialized == ["README.md", "pyproject.toml", "src/needle/a.py"]
    assert cache.checked_out == [(_URL, _SHA)]
