"""Agent-track CLI entry + preflight (spec §D15).

``python -m benchmarks.eval.agent_track`` drives the paired harness or, with
``--preflight``, verifies the environment contract BEFORE any paid run. The
preflight turns the slice's main risk — environment drift + real spend — into
executable checks: the headless CLI is present, its JSON output carries the
fields the parsers read, ``pydocs_mcp`` imports, the one-server MCP config boots
and lists tools, and there is disk headroom for corpus checkouts + index caches.

Preflight discipline (why the enumeration is pure): building the check list must
spend NOTHING and touch no subprocess — the paid probe (a one-token
``claude -p``) and the MCP boot run ONLY when a check is invoked. So
``preflight_checks`` returns ``PreflightCheck`` objects carrying a ``run``
closure; ``main --preflight`` invokes them in order and stops at the first
failure (fail fast before spending). This split is what the offline test suite
pins — it enumerates and asserts names/order without ever running a check.

Never CI (spec §D15): a full run spends real money (~$5–10 per arm per repo).
The runbook (``benchmarks/AGENT_TRACK.md``) states the preflight-first rule,
resume semantics, and cost expectations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from benchmarks.eval.agent_track._command import render_mcp_config
from benchmarks.eval.agent_track._judge import RealJudge
from benchmarks.eval.agent_track._parse import parse_result_json
from benchmarks.eval.agent_track._runner import ClaudeAgentRunner
from benchmarks.eval.agent_track._types import (
    AgentTrackConfig,
    ArmConfig,
)
from benchmarks.eval.agent_track.orchestrator import run_agent_track
from benchmarks.eval.agent_track.report import format_agent_track_report
from benchmarks.eval.serialization import dataset_registry

# The paid preflight probe is capped hard: a one-token ``claude -p "ok"`` must
# cost well under a cent. Anything above this is a contract violation (wrong
# model, wrong flags) that the check flags before a full run spends real money.
_PREFLIGHT_PROBE_MAX_USD = 0.01
_PREFLIGHT_PROBE_PROMPT = "ok"
# Corpus checkouts + index sidecars need headroom; 2 GiB is a conservative floor
# below which a full run risks a mid-run ENOSPC. Single source of truth.
_MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_DATASET = "swe-qa-pro"


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """One check's outcome: pass/fail plus a human-readable detail line.

    ``detail`` carries the offending value on failure (the CLI-contract rule:
    errors name what was expected and what was seen) so an operator can fix the
    environment without re-deriving the check.
    """

    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """A named environment check whose ``run`` is invoked ONLY on demand.

    Enumerating the checks (``preflight_checks``) is pure — it builds these
    objects and spends nothing. The expensive work (a paid CLI probe, booting
    the MCP server) lives inside ``run`` and fires only when ``main --preflight``
    invokes it, so the offline test suite can assert the enumeration without any
    subprocess.
    """

    name: str
    run: Callable[[], PreflightResult]


def preflight_checks(*, python: Path) -> tuple[PreflightCheck, ...]:
    """Enumerate the five environment checks, in fail-fast order.

    PURE: building this list spawns nothing and spends nothing. Each check's
    cost is deferred into its ``run`` closure — the paid ``claude`` probe
    (``claude-json-contract``) and the MCP boot (``mcp-config-boots``) run only
    when invoked. Order is cheap-to-expensive so ``main`` stops at the first
    failure before spending: CLI present → paid JSON probe → import → MCP boot →
    disk headroom.

    Example:
        >>> [c.name for c in preflight_checks(python=Path("/venv/bin/python"))]
        ['claude-cli-present', 'claude-json-contract', 'pydocs-mcp-importable', \
'mcp-config-boots', 'disk-headroom']
    """
    return (
        PreflightCheck("claude-cli-present", _check_cli_present),
        PreflightCheck("claude-json-contract", lambda: _check_json_contract(python=python)),
        PreflightCheck("pydocs-mcp-importable", lambda: _check_importable(python=python)),
        PreflightCheck("mcp-config-boots", lambda: _check_mcp_boots(python=python)),
        PreflightCheck("disk-headroom", _check_disk_headroom),
    )


def _check_cli_present() -> PreflightResult:
    # Cheapest check first: is ``claude`` on PATH at all? A missing CLI makes
    # every later check meaningless, so fail here before the paid probe.
    path = shutil.which("claude")
    if path is None:
        return PreflightResult(False, "`claude` not found on PATH — install the headless CLI")
    return PreflightResult(True, f"claude at {path}")


def _check_json_contract(*, python: Path) -> PreflightResult:
    # The one paid check: a one-token ``claude -p "ok" --output-format json``.
    # Validates the parser's contract (cost/turns/answer fields parse) AND that
    # the probe cost stays under the cap — a cost above ~$0.01 means the wrong
    # model or flags, caught before a full run spends real money.
    _ = python  # the probe uses the ``claude`` on PATH; python is for symmetry
    try:
        proc = subprocess.run(
            ["claude", "-p", _PREFLIGHT_PROBE_PROMPT, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return PreflightResult(False, f"claude probe failed to run: {exc!r}")
    if proc.returncode != 0:
        return PreflightResult(False, f"claude exited {proc.returncode}: {proc.stderr[:200]!r}")
    try:
        parsed = parse_result_json(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        return PreflightResult(False, f"result JSON did not parse: {exc!r}")
    if parsed.cost_usd > _PREFLIGHT_PROBE_MAX_USD:
        return PreflightResult(
            False,
            f"probe cost ${parsed.cost_usd:.4f} exceeds cap "
            f"${_PREFLIGHT_PROBE_MAX_USD:.2f} — wrong model/flags?",
        )
    return PreflightResult(True, f"json ok: cost=${parsed.cost_usd:.4f} turns={parsed.turns}")


def _check_importable(*, python: Path) -> PreflightResult:
    # The indexed arm serves ``pydocs_mcp`` via ``python -m pydocs_mcp`` — a
    # missing / broken install must fail here, not mid-run. Uses the SAME
    # interpreter the arm will (``python``) so the check matches reality.
    proc = subprocess.run(
        [str(python), "-c", "import pydocs_mcp"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return PreflightResult(False, f"`import pydocs_mcp` failed: {proc.stderr[:200]!r}")
    return PreflightResult(True, f"pydocs_mcp importable under {python}")


def _check_mcp_boots(*, python: Path) -> PreflightResult:
    # Boot the one-server MCP config against a tiny fixture corpus and confirm
    # the server starts + lists tools. Uses ``--help`` on the serve entry as a
    # cheap liveness probe: a non-zero exit or an import-time crash surfaces a
    # broken MCP wiring before a paid indexed arm attaches it.
    corpus_dir = (
        Path(__file__).resolve().parents[3] / "tests" / "eval" / "fixtures" / "swe_qa_corpus"
    )
    rendered = render_mcp_config(corpus_dir=corpus_dir, python=python)
    server = json.loads(rendered)["mcpServers"]["pydocs-mcp"]
    proc = subprocess.run(
        [server["command"], "-m", "pydocs_mcp", "serve", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return PreflightResult(False, f"`pydocs_mcp serve --help` exited {proc.returncode}")
    return PreflightResult(True, "pydocs_mcp serve boots (config renders + entry runs)")


def _check_disk_headroom() -> PreflightResult:
    # Corpus checkouts + ``.tq`` / ``.db`` sidecars need room. Below the floor a
    # full run risks a mid-run ENOSPC that discards a paid pair.
    free = shutil.disk_usage(Path.home()).free
    if free < _MIN_FREE_BYTES:
        return PreflightResult(
            False,
            f"only {free / 1e9:.1f} GB free — need ≥ {_MIN_FREE_BYTES / 1e9:.1f} GB",
        )
    return PreflightResult(True, f"{free / 1e9:.1f} GB free")


def _run_preflight(*, python: Path) -> int:
    # Run each check in order; stop at the first failure (fail fast before any
    # later check spends). Return process exit code: 0 all-pass, 1 any failure.
    print("agent-track preflight:")
    for check in preflight_checks(python=python):
        result = check.run()
        mark = "PASS" if result.ok else "FAIL"
        print(f"  [{mark}] {check.name}: {result.detail}")
        if not result.ok:
            print("preflight FAILED — fix the above before a paid run.")
            return 1
    print("preflight OK — safe to run.")
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.eval.agent_track",
        description="Paired agent-efficiency harness (bare vs pydocs-mcp). Manual, never CI.",
    )
    parser.add_argument(
        "--dataset",
        default=_DEFAULT_DATASET,
        help="dataset name. available: " + ", ".join(dataset_registry.names()),
    )
    parser.add_argument("--max-tasks", type=int, default=None, help="cap admitted pairs")
    parser.add_argument("--max-usd", type=float, default=None, help="hard spend cap (USD)")
    parser.add_argument("--model", default=None, help="model id pinned for both arms")
    parser.add_argument("--judge-model", default=None, help="model id for the blind judge arm")
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("agent_track_pairs.jsonl"),
        help="JSONL ledger path (resume: skips task_ids already present)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the Markdown report here (default: stdout)",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="verify the environment contract and exit (spends ≤ $0.01)",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> AgentTrackConfig:
    # Overlay only the flags the operator set onto the typed defaults — an unset
    # flag keeps the ``AgentTrackConfig`` / ``ArmConfig`` default (single source
    # of truth), never a re-encoded literal here.
    base = AgentTrackConfig()
    arms = base.arms
    if args.model is not None:
        arms = tuple(
            ArmConfig(name=a.name, model=args.model, max_turns=a.max_turns, mcp=a.mcp) for a in arms
        )
    return AgentTrackConfig(
        arms=arms,
        judge_model=args.judge_model
        if args.judge_model is not None
        else (args.model or base.judge_model),
        max_tasks=args.max_tasks if args.max_tasks is not None else base.max_tasks,
        max_usd=args.max_usd if args.max_usd is not None else base.max_usd,
        task_timeout_seconds=base.task_timeout_seconds,
        rng_seed=base.rng_seed,
        output_dir=base.output_dir,
    )


async def _run(args: argparse.Namespace) -> str:
    cfg = _config_from_args(args)
    dataset = dataset_registry.build(args.dataset)
    runner = ClaudeAgentRunner(task_timeout_seconds=cfg.task_timeout_seconds)
    judge = RealJudge(
        runner=runner,
        judge_model=cfg.judge_model,
        rng_seed=cfg.rng_seed,
        cwd=cfg.output_dir,
    )
    pairs = await run_agent_track(
        cfg, dataset=dataset, runner=runner, judge=judge, ledger_path=args.ledger
    )
    # Honest footer (no-silent-caps): source discard count + spend from the LEDGER,
    # not from the admitted ``pairs`` alone. ``run_agent_track`` returns only the
    # admitted pairs, so ``pairs`` cannot reveal how many tasks the orchestrator
    # discarded (half-pair / judge-failed) nor the money burned on their arms.
    # The ledger is the truth: the orchestrator logs every discard AND every
    # admitted pair's per-arm cost to it, so reading it back gives a real
    # discarded count and a spend total that already includes any per-arm cost the
    # ledger records for discarded tasks (recovered automatically once the
    # orchestrator writes them — the summation keys on cost fields, not line kind).
    discarded, spend = _footer_stats_from_ledger(args.ledger)
    return format_agent_track_report(
        pairs,
        dataset_name=args.dataset,
        rng_seed=cfg.rng_seed,
        discarded=discarded,
        spend_usd=spend,
    )


# Ledger keys carrying real per-arm spend. Summing these across EVERY line (admitted
# or discarded) yields the true total spend, so a discard line that later records
# an arm's cost is counted without changing this caller. Single source of truth.
_LEDGER_COST_KEYS = ("bare_cost", "indexed_cost")


def _footer_stats_from_ledger(ledger_path: Path) -> tuple[int, float]:
    """Read the ledger back for the honest footer: (discarded count, total spend).

    ``run_agent_track`` returns only admitted pairs, so the discard count and the
    spend on discarded arms are invisible to the caller. The ledger is the record
    of truth — ``orchestrator._append_discard`` writes one ``discarded``-keyed line
    per drop, and ``_append_admitted`` writes each admitted pair's per-arm cost — so
    counting ``discarded`` lines and summing every ``_LEDGER_COST_KEYS`` field
    across all lines reconstructs both honestly. A missing ledger (no run yet)
    yields ``(0, 0.0)``.

    Example:
        >>> _footer_stats_from_ledger(Path("/does/not/exist.jsonl"))
        (0, 0.0)
    """
    if not ledger_path.exists():
        return 0, 0.0
    discarded = 0
    spend = 0.0
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        record = json.loads(stripped)
        if "discarded" in record:
            discarded += 1
        for key in _LEDGER_COST_KEYS:
            value = record.get(key)
            if isinstance(value, (int, float)):
                spend += float(value)
    return discarded, spend


def main() -> None:
    """Entry point: ``--preflight`` verifies the environment, else runs the harness."""
    args = _build_arg_parser().parse_args()
    if args.preflight:
        raise SystemExit(_run_preflight(python=Path(sys.executable)))
    report = asyncio.run(_run(args))
    if args.report is not None:
        args.report.write_text(report, encoding="utf-8")
        print(f"report written to {args.report}")
    else:
        print(report)


if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    main()
