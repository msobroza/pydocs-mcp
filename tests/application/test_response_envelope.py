"""ResponseEnvelope — the one wrapper both surfaces share (spec §D4/§D5/§D7)."""

import asyncio

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.freshness import EnvelopeInfo, IndexFreshnessProbe
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger

SHA = "8e2110e" + "0" * 33


def _probe(info: EnvelopeInfo | None) -> IndexFreshnessProbe:
    return (
        IndexFreshnessProbe(
            enabled=info is not None,
            ttl_seconds=0.0,
            read_metadata=lambda: None,  # unused: _compute is bypassed below
            resolve_live_head=lambda: None,
            count_packages=lambda: 0,
        )
        if info is None
        else _StaticProbe(info)
    )


class _StaticProbe:
    """Test double satisfying the probe's async surface with a fixed value."""

    def __init__(self, info: EnvelopeInfo) -> None:
        self._info = info

    async def envelope_info(self) -> EnvelopeInfo:
        return self._info


def _fresh_info(stale: bool = False) -> EnvelopeInfo:
    return EnvelopeInfo(
        indexed_commit=SHA,
        live_commit="f3ab91c" + "0" * 33 if stale else SHA,
        age_days=0,
        package_count=42,
        stale=stale,
    )


def _envelope(info, surface="mcp", pointers=True) -> ResponseEnvelope:
    return ResponseEnvelope(
        probe=_probe(info),
        surface=surface,
        pointers_enabled=pointers,
    )


async def _body() -> str:
    return "## Hit\nbody\n[[next:lookup:pkg.mod.X]]\n"


def test_header_and_resolved_pointer_mcp() -> None:
    out = asyncio.run(_envelope(_fresh_info()).wrap(_body))
    assert out.startswith("[index: 8e2110e · 0d old · 42 packages]\n\n")
    assert '→ get_symbol(target="pkg.mod.X")' in out
    assert "[[next:" not in out


def test_stale_warning_line() -> None:
    out = asyncio.run(_envelope(_fresh_info(stale=True)).wrap(_body))
    assert "[⚠ index stale: indexed 8e2110e, working tree at f3ab91c" in out
    assert "pydocs-mcp index ." in out


def test_cli_surface_pointer_syntax() -> None:
    out = asyncio.run(_envelope(_fresh_info(), surface="cli").wrap(_body))
    assert "→ pydocs-mcp symbol pkg.mod.X" in out


def test_pointers_disabled_are_stripped() -> None:
    out = asyncio.run(_envelope(_fresh_info(), pointers=False).wrap(_body))
    assert "[[next:" not in out and "→" not in out


def test_no_info_renders_body_only() -> None:
    out = asyncio.run(_envelope(None).wrap(_body))
    assert not out.startswith("[index:")


async def _body_with_pointer_shaped_chunk_content() -> str:
    # Mirrors real indexed content: this repo indexes itself as __project__,
    # and tests/application/test_next_pointers.py's own source (once
    # indexed) contains these exact pointer-shaped literals inside chunk
    # text — a show-less lookup-show token and an unknown show word. Both
    # previously KeyError'd out of resolve_pointers (called unconditionally
    # by ResponseEnvelope.wrap at the surface boundary).
    return (
        "## Hit\n"
        "see [[next:lookup-show:x]] and [[next:lookup-show:x:frobnicate]] in source\n"
        "[[next:lookup:pkg.mod.X]]\n"
    )


def test_wrap_does_not_crash_on_pointer_shaped_chunk_content() -> None:
    out = asyncio.run(_envelope(_fresh_info()).wrap(_body_with_pointer_shaped_chunk_content))
    # The one renderer-emitted token still resolves normally...
    assert '→ get_symbol(target="pkg.mod.X")' in out
    # ...while the pointer-shaped content bytes are left verbatim, not raised.
    assert "[[next:lookup-show:x]]" in out
    assert "[[next:lookup-show:x:frobnicate]]" in out


def test_footer_renders_ledger_entries() -> None:
    async def truncating_body() -> str:
        get_active_ledger().record(
            TruncationEntry(description="2 result(s) elided", recovery="[[next:lookup:pkg.mod.X]]")
        )
        return "body\n"

    out = asyncio.run(_envelope(_fresh_info()).wrap(truncating_body))
    assert "[truncated: 1 section" in out
    assert out.rstrip().endswith('2 result(s) elided → get_symbol(target="pkg.mod.X")')


def test_footer_respects_pointers_disabled() -> None:
    # Same truncating body as test_footer_renders_ledger_entries, but with
    # pointers_enabled=False. wrap() strips pointer tokens from the BODY
    # (envelope.py's `strip_pointers` branch) but render_envelope_footer
    # unconditionally calls resolve_pointers() on each ledger entry's
    # recovery token — so a "pointers disabled" deployment still emits
    # "→ get_symbol(...)" syntax in the truncation footer. Pin that this
    # is stripped, matching the body-side contract: no pointer syntax of
    # any kind survives when pointers_enabled=False.
    async def truncating_body() -> str:
        get_active_ledger().record(
            TruncationEntry(description="2 result(s) elided", recovery="[[next:lookup:pkg.mod.X]]")
        )
        return "body\n"

    out = asyncio.run(_envelope(_fresh_info(), pointers=False).wrap(truncating_body))
    assert "[truncated: 1 section" in out
    assert "[[next:" not in out
    assert "→" not in out
    assert "get_symbol" not in out
