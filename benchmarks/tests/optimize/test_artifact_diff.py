"""Per-section markdown artifact diff (ADR 0020 §Owner checkpoint 1a): golden output
over a fixture mutation + added/removed/unchanged handling."""

from __future__ import annotations

from pydocs_mcp.application.description_source import render_sections

from pydocs_eval.optimize.artifact_diff import render_artifact_diff

_SEED = {
    "SERVER_INSTRUCTIONS": "line one\nline two\nline three",
    "SESSION_START_PREAMBLE": "kept",
}
_CANDIDATE = {
    "SERVER_INSTRUCTIONS": "line one\nline TWO changed\nline three\nline four",
    "SESSION_START_PREAMBLE": "kept",
}

_GOLDEN = """# Artifact diff: seed -> candidate

sections changed: 1  added: 0  removed: 0

## SERVER_INSTRUCTIONS (modified)

```diff
--- seed/SERVER_INSTRUCTIONS
+++ candidate/SERVER_INSTRUCTIONS
@@ -1,3 +1,4 @@
 line one
-line two
+line TWO changed
 line three
+line four
```
"""


def test_golden_per_section_modified_diff() -> None:
    out = render_artifact_diff(render_sections(_SEED), render_sections(_CANDIDATE))
    assert out == _GOLDEN


def test_unchanged_sections_are_omitted() -> None:
    """SESSION_START_PREAMBLE is identical in both — it must not appear as a block."""
    out = render_artifact_diff(render_sections(_SEED), render_sections(_CANDIDATE))
    assert "SESSION_START_PREAMBLE" not in out


def test_added_and_removed_sections_are_labelled() -> None:
    seed = {"SERVER_INSTRUCTIONS": "a"}
    candidate = {"SESSION_START_PREAMBLE": "b"}
    out = render_artifact_diff(render_sections(seed), render_sections(candidate))
    assert "## SESSION_START_PREAMBLE (added)" in out
    assert "## SERVER_INSTRUCTIONS (removed)" in out
    assert "added: 1  removed: 1" in out


def test_identical_documents_report_no_changes() -> None:
    doc = render_sections(_SEED)
    out = render_artifact_diff(doc, doc)
    assert "sections changed: 0  added: 0  removed: 0" in out
