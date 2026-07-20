"""CLI: ``pydocs-eval-prereg`` — inspect the frozen campaign pre-registration.

Prints the :func:`registration_hash` the super-ledger references, the launch
authorization verdict (which measured slots are still ``[TO BE MEASURED]``), and
the code-computed power/false-accept table over the ADR-pinned discordance grid —
so the owner budget checkpoint reads its numbers from a re-runnable computer, not
hand math (ADR 0018 action item 6).

``--authorize`` turns the refusal into the exit code: exit 3 (and the empty-slot
list) while any measured slot is null, exit 0 once the registration is launchable.

Exit codes:
    0 — report rendered (or, with --authorize, the registration is launchable).
    2 — the pre-registration file could not be read/parsed.
    3 — --authorize was requested but measured slots remain null.
"""

from __future__ import annotations

import argparse
import sys
from importlib import resources
from pathlib import Path

from pydocs_eval.optimize.prereg.config import (
    PreRegistration,
    UnfilledSlotsError,
    authorize_launch,
    load_preregistration,
    registration_hash,
)
from pydocs_eval.optimize.prereg.report import render_power_report

# The ADR-pinned discordance grid the power tables are reported over (§Evidence).
_ADR_PI_DS: tuple[float, ...] = (0.10, 0.20, 0.30)
_PACKAGED = "campaign_preregistration.yaml"


def packaged_preregistration_path() -> Path:
    """Resolve the shipped ``campaign_preregistration.yaml`` in a built install."""
    return Path(str(resources.files("pydocs_eval.optimize.configs") / _PACKAGED))


def _render(prereg: PreRegistration) -> str:
    """The human report: hash + launch verdict + power table."""
    empty = prereg.unfilled_slots()
    verdict = "LAUNCHABLE" if not empty else f"BLOCKED — null slots: {list(empty)}"
    head = [
        f"registration_hash: {registration_hash(prereg)}",
        f"launch: {verdict}",
        "",
    ]
    return "\n".join(head) + render_power_report(prereg, _ADR_PI_DS)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pydocs-eval-prereg",
        description="Inspect the frozen ADR 0018 campaign pre-registration.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="pre-registration YAML (default: the shipped campaign_preregistration.yaml)",
    )
    parser.add_argument(
        "--authorize",
        action="store_true",
        help="exit non-zero (3) while any measured slot is null (the launch gate)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``pydocs-eval-prereg`` console script."""
    args = _build_parser().parse_args(argv)
    path = args.config or packaged_preregistration_path()
    try:
        prereg = load_preregistration(path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.authorize:
        return _authorize(prereg)
    print(_render(prereg), end="")
    return 0


def _authorize(prereg: PreRegistration) -> int:
    """Enforce the launch refusal as an exit code (3 blocked, 0 launchable)."""
    try:
        authorize_launch(prereg)
    except UnfilledSlotsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print("launch authorized: all measured slots filled")
    return 0


if __name__ == "__main__":  # pragma: no cover - module-run convenience
    sys.exit(main())
