"""ResponseEnvelope — the one wrapper both surfaces share (spec §D4/§D5/§D7).

``wrap`` returns a :class:`ToolResponse`: the enveloped markdown ``text``
(header + body + footer, unchanged conventions) plus the structured ``items``
rows and the ``meta`` attribution block of docs/tool-contracts.md §2.1.
"""

import asyncio

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.freshness import EnvelopeInfo, IndexFreshnessProbe
from pydocs_mcp.application.tool_response import ToolResponse
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


def _wrap(envelope: ResponseEnvelope, produce) -> ToolResponse:
    return asyncio.run(envelope.wrap("search_codebase", "proj", produce))


async def _body() -> str:
    return "## Hit\nbody\n[[next:lookup:pkg.mod.X]]\n"


def test_header_and_resolved_pointer_mcp() -> None:
    out = _wrap(_envelope(_fresh_info()), _body).text
    assert out.startswith("[index: 8e2110e · 0d old · 42 packages]\n\n")
    assert '→ get_symbol(target="pkg.mod.X")' in out
    assert "[[next:" not in out


def test_stale_warning_line() -> None:
    out = _wrap(_envelope(_fresh_info(stale=True)), _body).text
    assert "[⚠ index stale: indexed 8e2110e, working tree at f3ab91c" in out
    assert "pydocs-mcp index ." in out


def test_cli_surface_pointer_syntax() -> None:
    out = _wrap(_envelope(_fresh_info(), surface="cli"), _body).text
    assert "→ pydocs-mcp symbol pkg.mod.X" in out


def test_pointers_disabled_are_stripped() -> None:
    out = _wrap(_envelope(_fresh_info(), pointers=False), _body).text
    assert "[[next:" not in out and "→" not in out


def test_no_info_renders_body_only() -> None:
    out = _wrap(_envelope(None), _body).text
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
    out = _wrap(_envelope(_fresh_info()), _body_with_pointer_shaped_chunk_content).text
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

    out = _wrap(_envelope(_fresh_info()), truncating_body).text
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

    out = _wrap(_envelope(_fresh_info(), pointers=False), truncating_body).text
    assert "[truncated: 1 section" in out
    assert "[[next:" not in out
    assert "→" not in out
    assert "get_symbol" not in out


# ── structured meta assembly (contract §2.1) ────────────────────────────────


def test_meta_maps_envelope_info_fields() -> None:
    response = _wrap(_envelope(_fresh_info()), _body)
    assert response.meta == {
        "tool": "search_codebase",
        "project": "proj",
        "indexed_git_head": SHA,
        "live_git_head": SHA,
        "index_stale": False,
        "truncated": False,
    }
    assert response.items == ()


def test_meta_stale_flag_mirrors_probe() -> None:
    response = _wrap(_envelope(_fresh_info(stale=True)), _body)
    assert response.meta["index_stale"] is True
    assert response.meta["indexed_git_head"] != response.meta["live_git_head"]


def test_meta_absent_info_degrades_to_nulls() -> None:
    response = _wrap(_envelope(None), _body)
    assert response.meta["indexed_git_head"] is None
    assert response.meta["live_git_head"] is None
    assert response.meta["index_stale"] is False


def test_meta_empty_commit_strings_become_nulls() -> None:
    # A resolvable metadata row with unresolvable heads stores "" — the wire
    # contract wants null, not empty string (§2.1).
    info = EnvelopeInfo(indexed_commit="", live_commit="", age_days=0, package_count=1, stale=False)
    response = _wrap(_envelope(info), _body)
    assert response.meta["indexed_git_head"] is None
    assert response.meta["live_git_head"] is None


def test_meta_truncated_mirrors_ledger() -> None:
    async def truncating_body() -> str:
        get_active_ledger().record(TruncationEntry(description="elided", recovery=""))
        return "body\n"

    response = _wrap(_envelope(_fresh_info()), truncating_body)
    assert response.meta["truncated"] is True


def test_tuple_body_carries_items_and_meta_extras() -> None:
    async def produce():
        return "body\n", ({"path": "a.py"},), {"truncated": True, "resolution": "syntactic"}

    response = _wrap(_envelope(_fresh_info()), produce)
    assert response.items == ({"path": "a.py"},)
    # Extras merge on top of the assembled meta; truncated ORs (a body-level
    # truncation must never be masked by an empty ledger, and vice versa).
    assert response.meta["truncated"] is True
    assert response.meta["resolution"] == "syntactic"
    assert response.meta["tool"] == "search_codebase"
