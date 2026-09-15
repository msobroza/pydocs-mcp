"""Session-start context pack builder (ADR 0008): marker + preamble + card + inventory.

Pins the ADR's composition order, the wire-format marker constant, the
card-before-inventory trim order under budget pressure, determinism under
fixed inputs, and the never-trimmed marker/preamble floor.
"""

from __future__ import annotations

import asyncio

from pydocs_mcp.application import session_start_context, tool_docs
from pydocs_mcp.application.session_start_context import (
    CARD_TRUNCATED_NOTE,
    INJECTED_CONTEXT_MARKER,
    INVENTORY_TRUNCATED_NOTE,
    build_session_start_context,
)
from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens
from tests._session_start_fixture import (
    FIRST_MODULE_QNAME,
    PROJECT_PACKAGE,
    build_session_start_fixture,
)

_INVENTORY_HEADING = "## Installed packages"


def _build_pack(budget_tokens: int, *, pointers_enabled: bool = True, **fixture_kwargs) -> str:
    factory, overview = build_session_start_fixture(**fixture_kwargs)
    return asyncio.run(
        build_session_start_context(
            uow_factory=factory,
            overview=overview,
            budget_tokens=budget_tokens,
            pointers_enabled=pointers_enabled,
        )
    )


def _pack_tokens(pack: str) -> int:
    # Empty model name -> tiktoken's o200k_base fallback, the encoding the
    # builder's budget enforcement uses.
    return count_tokens(pack, "")


class TestMarker:
    def test_marker_constant_is_pinned_wire_format(self) -> None:
        """ADR 0008: the Phase 2 attribution matcher does an EXACT match on
        this constant — rewording it is a cross-phase breaking change."""
        assert INJECTED_CONTEXT_MARKER == (
            "[pydocs-mcp session-start-context: harness-injected at session start; "
            "not model-retrieved]"
        )

    def test_pack_first_line_is_the_marker_byte_for_byte(self) -> None:
        pack = _build_pack(budget_tokens=10_000)
        assert pack.splitlines()[0] == INJECTED_CONTEXT_MARKER

    def test_floor_pack_first_line_is_still_the_marker(self) -> None:
        pack = _build_pack(budget_tokens=1)
        assert pack.splitlines()[0] == INJECTED_CONTEXT_MARKER


class TestComposition:
    def test_sections_present_and_ordered(self) -> None:
        pack = _build_pack(budget_tokens=10_000)
        preamble_at = pack.index(tool_docs.SESSION_START_PREAMBLE)
        card_at = pack.index(f"# Overview — {PROJECT_PACKAGE}")
        inventory_at = pack.index(_INVENTORY_HEADING)
        assert pack.index(INJECTED_CONTEXT_MARKER) == 0
        assert preamble_at < card_at < inventory_at

    def test_inventory_rows_are_name_version_sorted_by_name(self) -> None:
        pack = _build_pack(budget_tokens=10_000)
        tail = pack[pack.index(_INVENTORY_HEADING) :].splitlines()
        assert tail[1:] == ["__project__ 0.1.0", "fastapi 0.111.0", "numpy 1.26.4"]

    def test_no_truncation_notes_within_budget(self) -> None:
        pack = _build_pack(budget_tokens=10_000)
        assert CARD_TRUNCATED_NOTE not in pack
        assert INVENTORY_TRUNCATED_NOTE not in pack

    def test_preamble_reads_the_live_tool_docs_attribute(self, monkeypatch) -> None:
        """An apply_source override rebinding SESSION_START_PREAMBLE (ADR 0006) must
        reach every later pack build — the builder reads the module attribute,
        never a from-import snapshot."""
        monkeypatch.setattr(tool_docs, "SESSION_START_PREAMBLE", "OVERRIDDEN PREAMBLE.")
        pack = _build_pack(budget_tokens=10_000)
        assert pack.splitlines()[1] == "OVERRIDDEN PREAMBLE."

    def test_deterministic_under_fixed_inputs(self) -> None:
        factory, overview = build_session_start_fixture()

        async def _twice() -> tuple[str, str]:
            first = await build_session_start_context(
                uow_factory=factory, overview=overview, budget_tokens=500, pointers_enabled=True
            )
            second = await build_session_start_context(
                uow_factory=factory, overview=overview, budget_tokens=500, pointers_enabled=True
            )
            return first, second

        first, second = asyncio.run(_twice())
        assert first == second


class TestBudget:
    def test_card_is_trimmed_before_the_inventory(self) -> None:
        """ADR 0008 trim order: under mild pressure the card loses lines while
        the (distinctive, cheap) inventory stays complete."""
        fixture = {"module_count": 20}
        full = _build_pack(budget_tokens=100_000, **fixture)
        budget = _pack_tokens(full) - 20
        pack = _build_pack(budget_tokens=budget, **fixture)
        assert _pack_tokens(pack) <= budget
        assert CARD_TRUNCATED_NOTE in pack
        assert INVENTORY_TRUNCATED_NOTE not in pack
        tail = pack[pack.index(_INVENTORY_HEADING) :].splitlines()
        assert tail[1:] == ["__project__ 0.1.0", "fastapi 0.111.0", "numpy 1.26.4"]

    def test_inventory_is_trimmed_only_after_the_card_is_exhausted(self) -> None:
        deps = {f"pkg_{i:03d}": "1.0" for i in range(200)}
        pack = _build_pack(budget_tokens=400, dependency_versions=deps)
        assert _pack_tokens(pack) <= 400
        assert CARD_TRUNCATED_NOTE in pack
        assert INVENTORY_TRUNCATED_NOTE in pack
        # Deterministic prefix survives: earliest-sorted rows kept, latest cut.
        assert "pkg_000 1.0" in pack
        assert "pkg_199 1.0" not in pack

    def test_floor_never_drops_marker_preamble_or_notes(self) -> None:
        """A budget below the floor returns the floor (marker + preamble +
        both notes) rather than an unmarked fragment — the marker/preamble are
        machinery the attribution phase needs."""
        pack = _build_pack(budget_tokens=1)
        lines = pack.splitlines()
        assert lines[0] == INJECTED_CONTEXT_MARKER
        assert tool_docs.SESSION_START_PREAMBLE in pack
        assert CARD_TRUNCATED_NOTE in pack
        assert INVENTORY_TRUNCATED_NOTE in pack

    def test_trimmed_pack_respects_the_budget_exactly(self) -> None:
        full = _build_pack(budget_tokens=100_000)
        floor_tokens = _pack_tokens(_build_pack(budget_tokens=1))
        for budget in (_pack_tokens(full) - 5, floor_tokens + 50, floor_tokens + 10):
            pack = _build_pack(budget_tokens=budget)
            assert _pack_tokens(pack) <= budget, f"budget={budget} exceeded"


def test_module_reexports_public_surface() -> None:
    assert session_start_context.INJECTED_CONTEXT_MARKER is INJECTED_CONTEXT_MARKER


class TestPointers:
    """The pack is harness-injected at turn 0 on BOTH channels (ADR 0008), so
    every follow-up it advertises must be a call the agent can issue verbatim —
    and only when the deployment has follow-ups switched on at all.
    """

    def test_card_pointers_render_the_mcp_call_form(self) -> None:
        pack = _build_pack(budget_tokens=10_000)
        assert f'→ get_symbol(target="{FIRST_MODULE_QNAME}", depth="tree")' in pack

    def test_pack_carries_no_raw_pointer_tokens(self) -> None:
        assert "[[next:" not in _build_pack(budget_tokens=10_000)

    def test_pack_never_renders_the_cli_call_form(self) -> None:
        # Even the CLI verb's output is injected into an agent prompt, so the
        # pack has exactly one call form on every channel.
        assert "→ pydocs-mcp" not in _build_pack(budget_tokens=10_000)

    def test_trimmed_pack_carries_no_raw_pointer_tokens(self) -> None:
        # Resolution runs BEFORE the budget fit, so a level cut can never
        # expose a token the full pack had resolved.
        full = _build_pack(budget_tokens=100_000)
        pack = _build_pack(budget_tokens=_pack_tokens(full) - 20)
        assert CARD_TRUNCATED_NOTE in pack
        assert "[[next:" not in pack

    def test_disabled_deployment_gets_no_calls_and_no_tokens(self) -> None:
        """``output.next_pointers.enabled: false`` reaches the pack the same way
        it reaches a tool response: the card is stripped, not resolved."""
        pack = _build_pack(budget_tokens=10_000, pointers_enabled=False)
        assert "[[next:" not in pack
        assert "→ get_symbol(" not in pack

    def test_disabled_deployment_keeps_the_card_itself(self) -> None:
        """Only the follow-ups go — the module map the pack exists to carry stays."""
        pack = _build_pack(budget_tokens=10_000, pointers_enabled=False)
        assert f"`{FIRST_MODULE_QNAME}`" in pack
        assert _INVENTORY_HEADING in pack

    def test_disabled_pack_is_shorter_than_the_resolved_one(self) -> None:
        """Guards against a strip that silently no-ops: the same corpus must
        cost fewer tokens once the calls are gone."""
        enabled = _build_pack(budget_tokens=100_000)
        disabled = _build_pack(budget_tokens=100_000, pointers_enabled=False)
        assert _pack_tokens(disabled) < _pack_tokens(enabled)
