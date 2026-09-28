"""The ``example-needle-chat`` dataset: the chat slice, vendored with code-authored gold.

Open-ended questions about ``msobroza/example_needle`` at one pinned commit — the
ten repro questions are the ``dev`` slice; the thirty held-out questions are
``test`` (the adoption set the ladder may run on) and ``reserved`` (consumed once,
by final confirmation). Records are VENDORED and read through
``importlib.resources``; the corpus is the pinned checkout, materialized lazily
and widened past ``.py`` because gold names ``README.md`` and config files.

A record names its gold ONCE, as sites (``path``, ``start``, ``end``,
``symbol``). The loader derives what consumers read — ``GoldAnswer.file_set``,
the gate-safe ``extra`` symbols, ``metadata.site_i`` and
``metadata.gold_file_count`` — so a record cannot disagree with itself, and it
validates every closed vocabulary before a task is yielded. A record may also
carry ``gold.reference_answer``, which rides the gold as a ``ReferenceAnswer``.

Each record's slice is a stored literal: the ``reserved`` membership was drawn
once at authoring time (``example_needle_chat_reserved``) and the loader never
draws. ``before-after --split`` reaches every slice name here through its
per-dataset slice map, ``DATASET_SLICE_NAMES``.

Example:
    >>> dataset_registry.build("example-needle-chat", split="dev")  # doctest: +SKIP
"""

from __future__ import annotations

import importlib.resources as ir
import json
import re
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any

from ..registries import dataset_registry
from ._repo_cache import RepoCache, RepoCacheLike, read_checkout_files
from .base_dataset import (
    GOLD_EMBEDDER_DIM_KEY,
    GOLD_EMBEDDER_MODEL_KEY,
    REFERENCE_ANSWER_KEY,
    EvalTask,
    GoldAnswer,
    ReferenceAnswer,
)
from .corpus import materialize_corpus

_DATASET_NAME = "example-needle-chat"
_REVISION = "1.0"
# The product's default ``include_extensions`` (text/config set plus ``.py``): gold
# names README.md and config files, and a gold file outside the corpus can never
# be retrieved.
CORPUS_GLOBS: tuple[str, ...] = (
    "*.py",
    "*.md",
    "*.toml",
    "*.yaml",
    "*.yml",
    "*.cfg",
    "*.ini",
    "*.txt",
    "*.json",
    "*.rst",
)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
# Gate-safe: both rubric gates tokenize every ``extra`` value, so a symbol is one
# identifier, never prose.
_GATE_SAFE_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_RATIFIED_VALUES = ("false", "true")
# The record field a reference answer is stored under: the record format's own
# name, so renaming the in-memory REFERENCE_ANSWER_KEY never changes the records.
_REFERENCE_FIELD = "reference_answer"
_REQUIRED_METADATA = (
    GOLD_EMBEDDER_MODEL_KEY,
    GOLD_EMBEDDER_DIM_KEY,
    "split",
    "shape",
    "gold_source",
    "gold_ratified",
    "query_source",
)


class ChatSplit(StrEnum):
    """The chat slice names — the ONE place they live."""

    DEV = "dev"
    TEST = "test"
    RESERVED = "reserved"
    HELD_OUT = "held_out"
    ALL = "all"


class ChatQuestionShape(StrEnum):
    """What a finished answer looks like — the completeness rule is defined per shape."""

    WHERE = "where"
    WHERE_ALL = "where_all"
    HOW_FLOW = "how_flow"
    WHY = "why"
    HOW_TO = "how_to"
    COMPARE = "compare"
    VAGUE = "vague"


class ChatGoldSource(StrEnum):
    """How the gold was written: from the code at the pinned commit — drafted, then
    ratified by the owner (``metadata.gold_ratified`` records whether that happened)."""

    CURATED = "curated"


class ChatQuerySource(StrEnum):
    """Who wrote the question."""

    AGENT = "agent"
    OWNER = "owner"


# A record stores a literal slice; HELD_OUT and ALL are selections, never stored.
_LITERAL_SPLITS = (ChatSplit.DEV, ChatSplit.TEST, ChatSplit.RESERVED)
_SELECTIONS: Mapping[ChatSplit, frozenset[ChatSplit]] = {
    ChatSplit.DEV: frozenset({ChatSplit.DEV}),
    ChatSplit.TEST: frozenset({ChatSplit.TEST}),
    ChatSplit.RESERVED: frozenset({ChatSplit.RESERVED}),
    ChatSplit.HELD_OUT: frozenset({ChatSplit.TEST, ChatSplit.RESERVED}),
    ChatSplit.ALL: frozenset(_LITERAL_SPLITS),
}
# WHY dev by default: ``reserved`` is consumed exactly once, by final confirmation,
# so a caller that names no slice must never reach it.
DEFAULT_CHAT_SPLIT = ChatSplit.DEV


class ChatDatasetError(ValueError):
    """A vendored chat record, or a requested slice, outside its declared shape."""


@dataclass(frozen=True, slots=True)
class GoldSite:
    """One place the answer lives: a 1-indexed, inclusive line span and its symbol."""

    path: str
    start: int
    end: int
    symbol: str

    @property
    def span(self) -> str:
        return f"{self.path}:{self.start}-{self.end}"


@dataset_registry.register(_DATASET_NAME)
@dataclass
class ExampleNeedleChatDataset:
    """The vendored chat slice over ``msobroza/example_needle`` (the owner's own repo)."""

    name: str = _DATASET_NAME
    revision: str = _REVISION
    split: str = DEFAULT_CHAT_SPLIT
    fixture_path: Path | None = None
    # WHY injected: tests pass a no-git fake; production clones the public repo.
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)

    def __post_init__(self) -> None:
        # Refuse an unknown slice at construction, where the before/after plan
        # maps a dataset's refusal to a named plan error.
        chat_split(self.split)

    async def tasks(self) -> AsyncIterator[EvalTask]:
        wanted = _SELECTIONS[chat_split(self.split)]
        # Every record is validated, selected or not: a malformed vendored record
        # fails loudly whichever slice a run asked for.
        for task in [self._record_to_task(record) for record in self._read_records()]:
            if ChatSplit(task.metadata["split"]) in wanted:
                yield task

    def _read_records(self) -> list[dict[str, Any]]:
        if self.fixture_path is not None:
            text = self.fixture_path.read_text(encoding="utf-8")
        else:
            package = ir.files("pydocs_eval.datasets.data.example_needle_chat")
            text = package.joinpath("records.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _record_to_task(self, record: Mapping[str, Any]) -> EvalTask:
        task_id = str(record.get("task_id", "<no task_id>"))
        url, commit = _checked_pin(task_id, record)
        gold_record = record.get("gold") or {}
        sites = _checked_sites(task_id, gold_record.get("sites"))
        reference = _checked_reference(task_id, gold_record.get(_REFERENCE_FIELD))
        metadata = {
            **_checked_metadata(task_id, record.get("metadata") or {}),
            "repo": _repo_slug(task_id, url),
            "commit": commit,
            **_site_metadata(sites),
        }
        return EvalTask(
            task_id=task_id,
            query=str(record["query"]),
            gold=gold_of(sites, reference),
            # Bound to this record's pin; the clone happens lazily, so a task that
            # is never scored costs no checkout.
            corpus_source=partial(_pinned_corpus, self.repo_cache, url, commit),
            metadata=metadata,
        )


def _pinned_corpus(repo_cache: RepoCacheLike, url: str, commit: str) -> Path:
    """The pinned checkout's corpus files, materialized into a fresh directory."""
    return materialize_corpus(
        read_checkout_files(repo_cache.checkout(url, commit), globs=CORPUS_GLOBS)
    )


def chat_split(name: str) -> ChatSplit:
    """The slice a run asked for, refused by name when it is not a chat slice."""
    try:
        return ChatSplit(name)
    except ValueError:
        raise ChatDatasetError(
            f"{_DATASET_NAME}: unknown slice {name!r}, expected one of {_values(ChatSplit)}"
        ) from None


def gold_of(sites: Iterable[GoldSite], reference: ReferenceAnswer | None = None) -> GoldAnswer:
    """Distinct paths in site order, one gate-safe symbol per site, and the
    reference answer when the record carries one."""
    ordered = tuple(sites)
    extra: dict[str, object] = {f"symbol_{i}": site.symbol for i, site in enumerate(ordered)}
    if reference is not None:
        extra[REFERENCE_ANSWER_KEY] = reference
    return GoldAnswer(file_set=tuple(dict.fromkeys(site.path for site in ordered)), extra=extra)


def _checked_pin(task_id: str, record: Mapping[str, Any]) -> tuple[str, str]:
    url, commit = record.get("repo_url"), record.get("commit")
    if not isinstance(url, str) or not url:
        raise ChatDatasetError(f"{task_id}: repo_url = {url!r}, expected a repository URL")
    if not isinstance(commit, str) or not _SHA40.fullmatch(commit):
        raise ChatDatasetError(f"{task_id}: commit = {commit!r}, expected a 40-hex commit sha")
    return url, commit


def _checked_sites(task_id: str, raw: object) -> tuple[GoldSite, ...]:
    if not isinstance(raw, list) or not raw:
        raise ChatDatasetError(f"{task_id}: gold.sites = {raw!r}, expected a non-empty list")
    return tuple(_checked_site(task_id, entry) for entry in raw)


def _checked_site(task_id: str, entry: Mapping[str, Any]) -> GoldSite:
    site = GoldSite(
        path=str(entry.get("path", "")),
        start=int(entry.get("start", 0)),
        end=int(entry.get("end", 0)),
        symbol=str(entry.get("symbol", "")),
    )
    well_formed = site.path and 1 <= site.start <= site.end
    if not well_formed or not _GATE_SAFE_SYMBOL.fullmatch(site.symbol):
        raise ChatDatasetError(
            f"{task_id}: gold site {dict(entry)!r}, expected a path, 1 <= start <= end "
            "and one gate-safe identifier as the symbol"
        )
    return site


def _checked_reference(task_id: str, raw: object) -> ReferenceAnswer | None:
    """The record's reference answer, when the judge's writer has stored one."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise _malformed_reference_error(task_id, raw, f"got a {type(raw).__name__}")
    try:
        return ReferenceAnswer(
            text=raw["text"], model_id=raw["model_id"], prompt_hash=raw["prompt_hash"]
        )
    except (KeyError, ValueError) as exc:
        raise _malformed_reference_error(task_id, raw, repr(exc)) from None


def _malformed_reference_error(task_id: str, raw: object, why: str) -> ChatDatasetError:
    return ChatDatasetError(
        f"{task_id}: gold.{_REFERENCE_FIELD} = {raw!r}, expected an object with a non-empty "
        f"text, a non-empty model_id and a prompt_hash string ({why})"
    )


def _checked_metadata(task_id: str, raw: Mapping[str, Any]) -> dict[str, str]:
    missing = [key for key in _REQUIRED_METADATA if key not in raw]
    if missing:
        raise ChatDatasetError(f"{task_id}: metadata is missing {missing}")
    metadata = {key: str(value) for key, value in raw.items()}
    _in_vocabulary(task_id, metadata, "split", [s.value for s in _LITERAL_SPLITS])
    _in_vocabulary(task_id, metadata, "shape", _values(ChatQuestionShape))
    _in_vocabulary(task_id, metadata, "gold_source", _values(ChatGoldSource))
    _in_vocabulary(task_id, metadata, "query_source", _values(ChatQuerySource))
    _in_vocabulary(task_id, metadata, "gold_ratified", list(_RATIFIED_VALUES))
    dim = metadata[GOLD_EMBEDDER_DIM_KEY]
    if not dim.isdigit():
        raise ChatDatasetError(
            f"{task_id}: metadata.{GOLD_EMBEDDER_DIM_KEY} = {dim!r}, expected digits"
        )
    return metadata


def _in_vocabulary(task_id: str, metadata: Mapping[str, str], key: str, allowed: list[str]) -> None:
    value = metadata[key]
    if value not in allowed:
        raise ChatDatasetError(f"{task_id}: metadata.{key} = {value!r}, expected one of {allowed}")


def _site_metadata(sites: tuple[GoldSite, ...]) -> dict[str, str]:
    spans = {f"site_{i}": site.span for i, site in enumerate(sites)}
    return {"gold_file_count": str(len({site.path for site in sites})), **spans}


def _repo_slug(task_id: str, url: str) -> str:
    """``owner/name`` from a repository URL — what the per-task workspace keys on.

    The trailing slash goes before the ``.git`` suffix, the crosscommitvuln rule,
    so ``.../o/n.git/`` and ``.../o/n`` name the same repository.
    """
    parts = url.rstrip("/").removesuffix(".git").split("/")
    if len(parts) < 2 or not all(parts[-2:]):
        raise ChatDatasetError(f"{task_id}: repo_url = {url!r}, expected .../<owner>/<name>")
    return f"{parts[-2]}/{parts[-1]}"


def _values(vocabulary: type[StrEnum]) -> list[str]:
    return [member.value for member in vocabulary]


__all__ = (
    "CORPUS_GLOBS",
    "DEFAULT_CHAT_SPLIT",
    "ChatDatasetError",
    "ChatGoldSource",
    "ChatQuerySource",
    "ChatQuestionShape",
    "ChatSplit",
    "ExampleNeedleChatDataset",
    "GoldSite",
    "chat_split",
    "gold_of",
)
