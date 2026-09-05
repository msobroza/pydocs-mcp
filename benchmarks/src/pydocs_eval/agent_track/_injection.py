"""Harness-side session-start injection (ADR 0016 Stage 1, action item 3).

The injection axis of the baseline campaign is realized in the harness, NOT by a
serve YAML flag: for injection-on cells the runner invokes the product
``session-start-context`` CLI against the rollout corpus and prepends the
returned marker-led pack to the shared ``claude -p`` prompt; injection-off cells
run the bare scaffold unchanged. Absent this step the injection-on cells would
be byte-identical to injection-off and the pre-registered injection secondary
would measure exactly zero (ADR 0016 §Decision Stage 1).

``assemble_prompt`` is the single prompt-assembly seam the campaign calls before
building a ``RolloutRequest`` (``trajectory/rollout.py`` takes the prompt
pre-assembled). injection-off returns the base prompt byte-identically, so the
default Q&A-track behavior is untouched.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

# Reuse the Phase 2 contract-mirror marker (parity-pinned to the product constant
# in trajectory/attribution.py) so there is ONE wire constant across the eval
# package and no second copy to drift.
from pydocs_eval.trajectory.attribution import INJECTED_CONTEXT_MARKER

__all__ = [
    "INJECTED_CONTEXT_MARKER",
    "SessionStartPackFetcher",
    "assemble_prompt",
    "fetch_session_start_pack",
    "prepend_session_start_pack",
    "validated_pack",
]

# Product CLI that prints the deterministic session-start pack (marker +
# preamble + overview card + version inventory). Corpus selection is
# ``--project-dir`` (python/pydocs_mcp/__main__.py, ``session-start-context``);
# the token budget and the enabled flag are YAML, never CLI flags.
_SESSION_START_ARGS = ("-m", "pydocs_mcp", "session-start-context", "--project-dir")

# The product CLI may index the corpus on first call; bound the wait so a hung
# index fails loud instead of stalling the campaign (external-call discipline).
_FETCH_TIMEOUT_SECONDS = 600.0

# Seam: injection-on cells fetch the pack from the product CLI; tests pass a
# canned fetcher so the pin test needs no subprocess and no product install.
SessionStartPackFetcher = Callable[[Path, Path], str]


def fetch_session_start_pack(python: Path, corpus_dir: Path) -> str:
    """Invoke ``<python> -m pydocs_mcp session-start-context --project-dir <corpus>``.

    Returns the printed pack (marker line first). Raises ``RuntimeError`` with the
    corpus and the captured stderr when the CLI fails, so a broken corpus fails
    loud at the seam rather than silently injecting nothing.

    Example:
        >>> fetch_session_start_pack(  # doctest: +SKIP
        ...     Path("/venv/bin/python"), Path("/corpus")
        ... ).splitlines()[0] == INJECTED_CONTEXT_MARKER
        True
    """
    proc = subprocess.run(
        [str(python), *_SESSION_START_ARGS, str(corpus_dir)],
        capture_output=True,
        text=True,
        timeout=_FETCH_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"session-start-context failed (rc={proc.returncode}) for corpus "
            f"{str(corpus_dir)!r}: {proc.stderr[:300]!r}"
        )
    return validated_pack(proc.stdout, corpus_dir)


def validated_pack(stdout: str, corpus_dir: Path) -> str:
    """Strip the CLI's trailing newline and assert the marker is the first line.

    A first line other than ``INJECTED_CONTEXT_MARKER`` means the wrong output was
    captured (or the pack format changed) — fail loud with the offending value.
    """
    pack = stdout.rstrip("\n")  # only trailing: keep the marker as exact line one
    first = pack.split("\n", 1)[0]
    if first != INJECTED_CONTEXT_MARKER:
        raise RuntimeError(
            f"session-start pack for corpus {str(corpus_dir)!r} has first line "
            f"{first!r}, expected the marker {INJECTED_CONTEXT_MARKER!r}"
        )
    return pack


def prepend_session_start_pack(base_prompt: str, pack: str) -> str:
    """Prepend the marker-led pack to the base prompt with one blank-line join.

    The injected prompt is exactly ``pack`` + one blank line + the unchanged
    ``base_prompt`` — so injection-on differs from injection-off by precisely the
    marker-led pack (ADR 0016 pin test).
    """
    return f"{pack}\n\n{base_prompt}"


def assemble_prompt(
    base_prompt: str,
    *,
    inject: bool,
    python: Path,
    corpus_dir: Path,
    fetch: SessionStartPackFetcher = fetch_session_start_pack,
) -> str:
    """Return the arm's prompt, prepending the session-start pack iff ``inject``.

    injection-off (``inject=False``) returns ``base_prompt`` byte-identically —
    the bare scaffold is unchanged, so the default Q&A track is untouched.
    injection-on fetches the pack for ``corpus_dir`` and prepends it. ``fetch`` is
    the subprocess seam (defaults to the real product CLI; tests inject a fake).
    """
    if not inject:
        return base_prompt
    pack = fetch(python, corpus_dir)
    return prepend_session_start_pack(base_prompt, pack)
