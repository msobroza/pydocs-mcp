"""Campaign runner CLI (ADR 0014 / ADR 0016).

Subcommands::

    python -m pydocs_eval.campaign prebuild-index --manifest M --cache-root R --python PY
    python -m pydocs_eval.campaign aggregate --campaign-id ID --cell name=agg.json \
        --contrast name=treatment/control [--stratum-map map.json] [--out report.json]
    python -m pydocs_eval.campaign build-strata --run-dir RUN [--out map.json]
    python -m pydocs_eval.campaign smoke-check   # host precondition report

``prebuild-index`` builds the canonical-checkout index cache over an instance
manifest (host-side; hits git + the index CLI). ``aggregate`` is pure and
offline: it reads per-cell ``aggregate.json`` files and emits the campaign report
skeleton, optionally broken down by a ``--stratum-map`` reporting dimension.
``build-strata`` derives the ``gold_touches_non_python`` map from a run dir's
``facts.json`` gold files (ADR 0021), for feeding back into ``aggregate
--stratum-map``. ``smoke-check`` prints the host preconditions so an operator can
see, before launch, exactly what the machine is missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydocs_eval.campaign.aggregator import (
    NamedContrast,
    campaign_report,
    load_cell_aggregate,
)
from pydocs_eval.campaign.prebuild import load_instance_manifest, prebuild_index
from pydocs_eval.campaign.smoke import check_preconditions, probe_host
from pydocs_eval.campaign.strata import build_gold_language_strata, load_stratum_map
from pydocs_eval.trajectory.blob_store import canonical_json


def _cmd_prebuild(args: argparse.Namespace) -> int:
    specs = load_instance_manifest(args.manifest)
    built = prebuild_index(
        specs, cache_root=args.cache_root, python=args.python, shallow=args.shallow
    )
    for (repo, commit), (db, _tq) in sorted(built.items()):
        print(f"{repo}@{commit} -> {db}")
    print(f"pre-built {len(built)} checkout(s) from {len(specs)} instance(s)")
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    cells = {name: load_cell_aggregate(name, path) for name, path in _parse_cells(args.cell)}
    contrasts = [_parse_contrast(spec) for spec in args.contrast]
    stratum_of = load_stratum_map(args.stratum_map) if args.stratum_map is not None else None
    report = campaign_report(args.campaign_id, cells, contrasts, stratum_of=stratum_of)
    text = canonical_json(report) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote campaign report to {args.out}")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_build_strata(args: argparse.Namespace) -> int:
    strata = build_gold_language_strata(args.run_dir)
    text = canonical_json(strata) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote gold-language strata for {len(strata)} instance(s) to {args.out}")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_smoke_check(_args: argparse.Namespace) -> int:
    failures = check_preconditions(probe_host())
    if not failures:
        print("host preconditions: OK (all satisfied)")
        return 0
    print(f"host preconditions: {len(failures)} FAILED", file=sys.stderr)
    for msg in failures:
        print(f"  - {msg}", file=sys.stderr)
    return 1


def _parse_cells(pairs: list[str]) -> list[tuple[str, Path]]:
    """Parse ``name=path`` cell args into ``(name, Path)`` tuples."""
    out: list[tuple[str, Path]] = []
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--cell must be name=path, got {pair!r}")
        name, path = pair.split("=", 1)
        out.append((name, Path(path)))
    return out


def _parse_contrast(spec: str) -> NamedContrast:
    """Parse ``name=treatment/control`` into a :class:`NamedContrast`."""
    if "=" not in spec or "/" not in spec.split("=", 1)[1]:
        raise SystemExit(f"--contrast must be name=treatment/control, got {spec!r}")
    name, arms = spec.split("=", 1)
    treatment, control = arms.split("/", 1)
    return NamedContrast(name=name, treatment=treatment, control=control)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pydocs_eval.campaign")
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("prebuild-index", help="build the canonical-checkout index cache")
    pre.add_argument("--manifest", type=Path, required=True, help="JSONL instance manifest")
    pre.add_argument("--cache-root", type=Path, required=True)
    pre.add_argument("--python", type=Path, required=True, help="interpreter for the index CLI")
    pre.add_argument("--shallow", action="store_true", help="blobless clone (--filter=blob:none)")
    pre.set_defaults(func=_cmd_prebuild)

    agg = sub.add_parser("aggregate", help="emit the cross-cell campaign report skeleton")
    agg.add_argument("--campaign-id", required=True)
    agg.add_argument("--cell", action="append", default=[], help="name=path/to/aggregate.json")
    agg.add_argument("--contrast", action="append", default=[], help="name=treatment/control")
    agg.add_argument(
        "--stratum-map",
        type=Path,
        default=None,
        help="JSON object OR JSONL {instance_id: stratum_key} map; breaks every "
        "contrast into per-stratum sub-contrasts. Expresses difficulty "
        "(single/multi), repo, or gold_touches_non_python (from `build-strata`) "
        "strata via one flag. Reporting-only — never changes the campaign id.",
    )
    agg.add_argument("--out", type=Path, default=None)
    agg.set_defaults(func=_cmd_aggregate)

    strata = sub.add_parser(
        "build-strata",
        help="derive a gold_touches_non_python stratum map from a run dir's facts.json",
    )
    strata.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="compute-metrics run dir (trajectory subdirs each carrying facts.json)",
    )
    strata.add_argument(
        "--out", type=Path, default=None, help="write the map here (default: stdout)"
    )
    strata.set_defaults(func=_cmd_build_strata)

    smoke = sub.add_parser("smoke-check", help="report host preconditions for the smoke")
    smoke.set_defaults(func=_cmd_smoke_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
