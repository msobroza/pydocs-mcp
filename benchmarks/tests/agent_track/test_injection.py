"""Harness-side session-start injection (ADR 0016 Stage 1, action item 3).

The injection axis lives in the harness, not a serve YAML flag: injection-on
cells prepend the product ``session-start-context`` pack to the shared
scaffold; injection-off cells run the bare scaffold unchanged. The ADR-mandated
pin test proves the two prompts differ by EXACTLY the marker-led pack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.agent_track._command import task_prompt
from pydocs_eval.agent_track._injection import (
    INJECTED_CONTEXT_MARKER,
    assemble_prompt,
    prepend_session_start_pack,
    validated_pack,
)

_FAKE_PACK = (
    f"{INJECTED_CONTEXT_MARKER}\n"
    "This context was injected at session start.\n\n"
    "## Installed packages\nfastapi 0.115.0\n"
)


def _fake_fetch(python: Path, corpus_dir: Path) -> str:
    return _FAKE_PACK


def test_injection_off_is_byte_identical_bare_scaffold() -> None:
    base = task_prompt("What does X do?")
    out = assemble_prompt(
        base, inject=False, python=Path("/p"), corpus_dir=Path("/c"), fetch=_fake_fetch
    )
    assert out == base  # injection-off = bare scaffold, unchanged


def test_injection_on_prepends_exactly_the_marker_led_pack() -> None:
    # THE PIN TEST (ADR 0016): injection-on vs injection-off differ by EXACTLY
    # the marker-led pack; the first line is the wire-constant marker.
    base = task_prompt("What does X do?")
    on = assemble_prompt(
        base, inject=True, python=Path("/p"), corpus_dir=Path("/c"), fetch=_fake_fetch
    )
    off = assemble_prompt(
        base, inject=False, python=Path("/p"), corpus_dir=Path("/c"), fetch=_fake_fetch
    )
    assert on == f"{_FAKE_PACK}\n\n{off}"
    assert on.splitlines()[0] == INJECTED_CONTEXT_MARKER
    assert on.removeprefix(f"{_FAKE_PACK}\n\n") == off  # the ONLY difference


def test_prepend_joins_with_one_blank_line() -> None:
    assert prepend_session_start_pack("BASE", "PACK") == "PACK\n\nBASE"


def test_validated_pack_strips_only_trailing_newline() -> None:
    # The CLI print adds a trailing newline; the marker must survive as line one.
    pack = validated_pack(f"{_FAKE_PACK}\n", Path("/c"))
    assert pack == _FAKE_PACK.rstrip("\n")
    assert pack.splitlines()[0] == INJECTED_CONTEXT_MARKER


def test_validated_pack_rejects_missing_marker() -> None:
    with pytest.raises(RuntimeError, match="expected the marker"):
        validated_pack("no marker here\n## Installed packages\n", Path("/c"))


def test_marker_matches_attribution_contract_copy() -> None:
    # The injection module reuses the same contract-mirror marker the Phase 2
    # attribution layer pins to the product constant — one wire constant, no drift.
    from pydocs_eval.trajectory.attribution import (
        INJECTED_CONTEXT_MARKER as attribution_marker,
    )

    assert attribution_marker == INJECTED_CONTEXT_MARKER
