"""The owned flags of each kind's CLI: typed spec inputs as flags, and flags pointed into the run's directories.

Each row of the kind table (``run_kinds.RUN_KIND_TABLE``) names one builder
here as its ``owned_flag_values``. The supervisor fills a ``KindInvocation``
from the run spec, the run record's frozen commits and the container's fixed
directories, and puts the builder's flags between the kind's CLI and the
spec's passthrough ``args``.

The state directory holds everything a run writes and a relaunch resumes from
(it mirrors to ``gs://<runs-bucket>/<run-id>/state/``); the cache directory
holds what a later run on another VM may reuse at the same absolute path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Where each kind's output lands under the state directory.
RESULTS_DIR_NAME = "results"
REPORT_NAME = "report.md"
AGENT_TRACK_LEDGER_NAME = "agent_track_pairs.jsonl"
BEFORE_AFTER_OUT_DIR_NAME = "before-after"
# The campaign checks each product commit out under <--out>/worktrees
# (campaign/before_after_command.py): full source trees the state sync skips.
BEFORE_AFTER_WORKTREES_DIR = f"{BEFORE_AFTER_OUT_DIR_NAME}/worktrees"
# Under the cache directory: before/after's per-corpus workspaces (stable
# slugs, so their bundles stay valid at the same path on another VM) and the
# product clone its worktrees check both commits out of.
BEFORE_AFTER_WORKSPACE_DIR = Path("before-after") / "workspace"
PRODUCT_CLONE_DIR_NAME = "product-repo"


@dataclass(frozen=True, slots=True)
class KindInvocation:
    """What a kind's owned flags are filled from.

    The typed inputs come from the run spec, the commits from the run record
    (frozen at the first launch), and the directories are the container's
    fixed paths.
    """

    run_id: str
    dataset: str | None
    split: str | None
    configs: tuple[str, ...]
    corpus_dir: str | None
    baseline_commit: str | None
    candidate_commit: str | None
    max_usd: float | None
    gpu: bool
    state_dir: Path
    cache_dir: Path


def _optional(flag: str, value: str | None) -> tuple[str, ...]:
    return () if value is None else (flag, value)


def _required(name: str, value: str | float | None) -> str:
    if value is None:
        raise ValueError(f"{name}: got None, expected a value the kind's row requires")
    return str(value)


def retrieval_flags(invocation: KindInvocation) -> tuple[str, ...]:
    """``pydocs_eval.runner``: resumable under the run id, its run files in the state directory."""
    state = invocation.state_dir
    return (
        # The runner takes one comma-separated --configs, one column per path.
        "--configs",
        ",".join(invocation.configs),
        *_optional("--dataset", invocation.dataset),
        *_optional("--split", invocation.split),
        *_optional("--corpus-dir", invocation.corpus_dir),
        "--run-id",
        invocation.run_id,
        "--results-dir",
        str(state / RESULTS_DIR_NAME),
        "--report",
        str(state / REPORT_NAME),
        *(("--gpu",) if invocation.gpu else ()),
    )


def agent_track_flags(invocation: KindInvocation) -> tuple[str, ...]:
    """``pydocs_eval.agent_track``: its ledger (keyed by arm) and report in the state directory."""
    state = invocation.state_dir
    return (
        *_optional("--dataset", invocation.dataset),
        "--max-usd",
        str(float(_required("max_usd", invocation.max_usd))),
        "--ledger",
        str(state / AGENT_TRACK_LEDGER_NAME),
        "--report",
        str(state / REPORT_NAME),
    )


def before_after_flags(invocation: KindInvocation) -> tuple[str, ...]:
    """The campaign's ``before-after``: the frozen commits, spending confirmed by the spec."""
    split = f"{_required('dataset', invocation.dataset)}/{_required('split', invocation.split)}"
    return (
        "--baseline",
        _required("baseline_commit", invocation.baseline_commit),
        "--candidate",
        _required("candidate_commit", invocation.candidate_commit),
        "--config",
        invocation.configs[0],
        "--split",
        split,
        "--max-usd",
        str(float(_required("max_usd", invocation.max_usd))),
        # The spec's confirm_spend: true is required for a paid kind, so a
        # spec that reached a VM has confirmed it.
        "--confirm-spend",
        "--workspace",
        str(invocation.cache_dir / BEFORE_AFTER_WORKSPACE_DIR),
        "--out",
        str(invocation.state_dir / BEFORE_AFTER_OUT_DIR_NAME),
        "--repo",
        str(invocation.cache_dir / PRODUCT_CLONE_DIR_NAME),
    )
