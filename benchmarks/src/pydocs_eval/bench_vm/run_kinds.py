"""The kind table: one row per ``RunKind``, the facts every bench VM layer reads.

A run spec names its kind; the row says what that kind runs and what the spec
may and may not say about it. The run-spec loader uses the rows to refuse bad
specs on the laptop (an owned flag in ``args``, a GPU on a CPU-only kind, a paid
kind without its spend guard). The supervisor reads the same rows to build the
child's argv and environment and to run the kind's pre-flight.

Adding a kind — the campaign's ``prebuild-index`` or ``write-references`` — is a
new ``RunKind`` member plus one row in ``RUN_KIND_TABLE``; nothing else branches
on the kind.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from pydocs_eval.bench_vm.kind_flags import (
    KindInvocation,
    agent_track_flags,
    before_after_flags,
    retrieval_flags,
)


class RunKind(StrEnum):
    """What a bench VM run measures; each member is one row of the kind table."""

    RETRIEVAL = "retrieval"
    AGENT_TRACK = "agent_track"
    BEFORE_AFTER = "before_after"


class BenchmarkInput(StrEnum):
    """The typed benchmark-input fields of a run spec, as the spec spells them."""

    DATASET = "dataset"
    SPLIT = "split"
    CONFIGS = "configs"
    CORPUS_DIR = "corpus_dir"
    BASELINE_REF = "baseline_ref"
    CANDIDATE_REF = "candidate_ref"


@dataclass(frozen=True, slots=True)
class RunKindRow:
    """Everything the bench VM knows about one kind.

    ``owned_flags`` are the CLI flags the supervisor sets itself: from the typed
    inputs (dataset, split, configs, the frozen commits, ``max_usd``, ``--gpu``)
    or pointed into the run's state and cache directories. A spec's passthrough
    ``args`` may name none of them; ``owned_flag_values`` builds them for one run
    (``kind_flags``). ``preflight`` is the argv the supervisor runs
    before the child, completed with the same owned flags less
    ``preflight_omits`` and the spec's ``args``; empty means the kind has no
    pre-flight of its own (the GPU probe applies to every GPU run).
    ``provider_keys`` are the environment variables the kind's CLI reads its
    provider keys from; the supervisor reads each from Secret Manager into the
    child's environment only (``provider_secrets``). ``product_clone`` asks
    for a blobless clone of the product repository under the cache directory,
    holding the run's frozen commits (``product_clone``).
    """

    cli: tuple[str, ...]
    owned_flags: frozenset[str]
    provider_keys: tuple[str, ...]
    preflight: tuple[str, ...]
    may_use_gpu: bool
    # A paid kind spends model tokens billed outside the GCP budget, so its spec
    # must carry both ``confirm_spend: true`` and a ``max_usd`` cap.
    paid: bool
    accepted_inputs: frozenset[BenchmarkInput]
    required_inputs: frozenset[BenchmarkInput]
    owned_flag_values: Callable[[KindInvocation], tuple[str, ...]]
    # ``None`` = any number of configs; before/after compares ONE config across
    # two commits, so its row pins exactly one.
    exact_config_count: int | None = None
    preflight_omits: frozenset[str] = frozenset()
    product_clone: bool = False


_RETRIEVAL_CLI = ("python", "-m", "pydocs_eval.runner")
_AGENT_TRACK_CLI = ("python", "-m", "pydocs_eval.agent_track")
_BEFORE_AFTER_CLI = ("python", "-m", "pydocs_eval.campaign", "before-after")

_RETRIEVAL_ROW = RunKindRow(
    cli=_RETRIEVAL_CLI,
    owned_flags=frozenset(
        {
            "--dataset",
            "--split",
            "--configs",
            "--corpus-dir",
            "--gpu",
            "--run-id",
            "--results-dir",
            "--report",
            "--bench-cache",
        }
    ),
    provider_keys=(),
    preflight=(),
    may_use_gpu=True,
    paid=False,
    accepted_inputs=frozenset(
        {
            BenchmarkInput.DATASET,
            BenchmarkInput.SPLIT,
            BenchmarkInput.CONFIGS,
            BenchmarkInput.CORPUS_DIR,
        }
    ),
    # The runner's --configs is required; it has no default overlay.
    required_inputs=frozenset({BenchmarkInput.CONFIGS}),
    owned_flag_values=retrieval_flags,
)

_AGENT_TRACK_ROW = RunKindRow(
    cli=_AGENT_TRACK_CLI,
    # --preflight is owned because passing it through would turn the run into
    # its own pre-flight and exit having measured nothing.
    owned_flags=frozenset({"--dataset", "--max-usd", "--ledger", "--report", "--preflight"}),
    provider_keys=("ANTHROPIC_API_KEY",),
    preflight=(*_AGENT_TRACK_CLI, "--preflight"),
    may_use_gpu=False,
    paid=True,
    accepted_inputs=frozenset({BenchmarkInput.DATASET}),
    required_inputs=frozenset(),
    owned_flag_values=agent_track_flags,
)

_BEFORE_AFTER_ROW = RunKindRow(
    cli=_BEFORE_AFTER_CLI,
    owned_flags=frozenset(
        {
            "--baseline",
            "--candidate",
            "--config",
            "--split",
            "--max-usd",
            "--confirm-spend",
            "--workspace",
            "--out",
            "--repo",
        }
    ),
    provider_keys=("OPENROUTER_API_KEY",),
    # Without --confirm-spend the command prints its plan and spends nothing.
    preflight=_BEFORE_AFTER_CLI,
    may_use_gpu=False,
    paid=True,
    accepted_inputs=frozenset(
        {
            BenchmarkInput.DATASET,
            BenchmarkInput.SPLIT,
            BenchmarkInput.CONFIGS,
            BenchmarkInput.BASELINE_REF,
            BenchmarkInput.CANDIDATE_REF,
        }
    ),
    # --split is required and spelled <dataset>/<split>, so both halves are.
    required_inputs=frozenset(
        {
            BenchmarkInput.DATASET,
            BenchmarkInput.SPLIT,
            BenchmarkInput.CONFIGS,
            BenchmarkInput.BASELINE_REF,
            BenchmarkInput.CANDIDATE_REF,
        }
    ),
    owned_flag_values=before_after_flags,
    exact_config_count=1,
    # The plan-only invocation is the run's own argv without the spend switch.
    preflight_omits=frozenset({"--confirm-spend"}),
    # --repo must resolve and check out both frozen commits.
    product_clone=True,
)

RUN_KIND_TABLE: Mapping[RunKind, RunKindRow] = MappingProxyType(
    {
        RunKind.RETRIEVAL: _RETRIEVAL_ROW,
        RunKind.AGENT_TRACK: _AGENT_TRACK_ROW,
        RunKind.BEFORE_AFTER: _BEFORE_AFTER_ROW,
    }
)


def owned_flags_named_by(args: tuple[str, ...], row: RunKindRow) -> tuple[str, ...]:
    """The passthrough ``args`` tokens that name a flag ``row`` owns, in order.

    A token names an owned flag when it IS the flag, sets it with ``=``, or is a
    prefix of it — argparse's ``allow_abbrev`` (on in every eval CLI) resolves
    ``--datas repoqa`` to ``--dataset``, so a prefix would slip the guard.

    Example:
        >>> owned_flags_named_by(("--limit", "5", "--datas=x"), RUN_KIND_TABLE[RunKind.RETRIEVAL])
        ('--datas=x',)
    """
    return tuple(arg for arg in args if _names_owned_flag(arg, row.owned_flags))


def _names_owned_flag(arg: str, owned: frozenset[str]) -> bool:
    if not arg.startswith("--") or arg == "--":
        return False
    name = arg.split("=", 1)[0]
    return any(flag.startswith(name) for flag in owned)
