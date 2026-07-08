"""disable_rust() end-to-end contract with lazily-importing consumers.

``disable_rust()`` (pydocs_mcp._fast) only takes effect for a consumer if that
consumer resolves ``pydocs_mcp._fast`` functions INSIDE the call (a deferred
``from pydocs_mcp._fast import X`` at function-body scope), not at module
import time. ``ContentHashStage._hash`` and ``AstMemberExtractor._parse_dir`` /
``_parse_files`` follow this pattern today (see content_hash.py and
ast_extractor.py). A refactor that hoists any of those imports to module level
would silently re-bind the Rust implementation before disable_rust() ever
swaps it — this test pins that contract by asserting the fallback sentinels
we install are the functions actually invoked, not just that ``_fast``'s own
attributes were swapped (tests/test_fast_import.py only covers the latter).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import FileBundle, IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages.content_hash import ContentHashStage
from pydocs_mcp.extraction.strategies.members.ast_extractor import AstMemberExtractor


def _reload_fast() -> None:
    """Reset pydocs_mcp._fast to its natural (non-disabled) state."""
    mods_to_remove = [k for k in sys.modules if k.startswith("pydocs_mcp._fast")]
    for k in mods_to_remove:
        del sys.modules[k]
    import pydocs_mcp._fast  # re-import normally


@pytest.mark.asyncio
async def test_content_hash_stage_uses_fallback_after_disable_rust(tmp_path: Path) -> None:
    """ContentHashStage._hash must call the swapped-in fallback, not a
    module-level-bound Rust reference captured before disable_rust()."""
    from pydocs_mcp import _fallback
    from pydocs_mcp._fast import disable_rust

    sentinel_calls: list[list[str]] = []

    def sentinel_hash_files(paths: list[str]) -> str:
        sentinel_calls.append(paths)
        return "sentinel-hash"

    f = tmp_path / "a.py"
    f.write_text("x = 1\n")

    try:
        with patch.object(_fallback, "hash_files", sentinel_hash_files):
            disable_rust()
            state = IngestionState(
                files=FileBundle(
                    target=tmp_path,
                    target_kind=TargetKind.PROJECT,
                    paths=(str(f),),
                )
            )
            stage = ContentHashStage()
            out = await stage.run(state)

        assert sentinel_calls == [[str(f)]], (
            "ContentHashStage._hash did not route through the disable_rust() "
            "fallback swap — a module-level import would have bound the "
            "pre-swap function instead."
        )
        assert out.files.content_hash == "sentinel-hash"
    finally:
        _reload_fast()


def test_ast_extractor_parse_dir_uses_fallback_after_disable_rust(tmp_path: Path) -> None:
    """AstMemberExtractor._parse_dir must call the swapped-in
    walk_py_files/read_files_parallel/parse_py_file fallbacks."""
    from pydocs_mcp import _fallback
    from pydocs_mcp._fast import disable_rust

    f = tmp_path / "mod.py"
    f.write_text("def public_fn():\n    pass\n")

    walk_calls: list[str] = []
    read_calls: list[list[str]] = []
    parse_calls: list[str] = []

    # Capture the real implementations before patching — the sentinels below
    # replace `_fallback.<name>` in place, so closing over the *original*
    # function (not re-reading the now-patched module attribute) avoids
    # infinite self-recursion.
    real_walk_py_files = _fallback.walk_py_files
    real_read_files_parallel = _fallback.read_files_parallel
    real_parse_py_file = _fallback.parse_py_file

    def sentinel_walk_py_files(root: str) -> list[str]:
        walk_calls.append(root)
        return real_walk_py_files(root)

    def sentinel_read_files_parallel(paths: list[str]) -> list[tuple[str, str]]:
        read_calls.append(paths)
        return real_read_files_parallel(paths)

    def sentinel_parse_py_file(source: str) -> list[_fallback.ParsedMember]:
        parse_calls.append(source)
        return real_parse_py_file(source)

    try:
        with (
            patch.object(_fallback, "walk_py_files", sentinel_walk_py_files),
            patch.object(_fallback, "read_files_parallel", sentinel_read_files_parallel),
            patch.object(_fallback, "parse_py_file", sentinel_parse_py_file),
        ):
            disable_rust()
            extractor = AstMemberExtractor()
            members = extractor._parse_dir(tmp_path, "demo_pkg")

        assert walk_calls == [str(tmp_path)], (
            "AstMemberExtractor._parse_dir did not route walk_py_files "
            "through the disable_rust() fallback swap."
        )
        assert read_calls, (
            "AstMemberExtractor._parse_files did not route read_files_parallel "
            "through the disable_rust() fallback swap."
        )
        assert parse_calls, (
            "AstMemberExtractor._parse_files did not route parse_py_file "
            "through the disable_rust() fallback swap."
        )
        names = {m.metadata["name"] for m in members}
        assert "public_fn" in names
    finally:
        _reload_fast()
