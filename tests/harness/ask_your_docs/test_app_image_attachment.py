"""AC25 (spec 2026-07-11-multimodal-image-agent): capability badge + image
chips, via the AppTest pattern of test_app_attachment.py."""

import os
from pathlib import Path

import pytest

pytest.importorskip("streamlit")


def test_capability_badge_and_image_chips_render():
    from streamlit.testing.v1 import AppTest

    import pydocs_mcp.ask_your_docs.app as appmod

    os.environ["PYDOCS_WORKSPACE"] = str(Path("~/pydocs-index").expanduser())
    at = AppTest.from_file(appmod.__file__, default_timeout=180)
    at.session_state["image_chips"] = ["shot.png", "diagram.webp"]
    at.run()
    assert not at.exception, at.exception
    # Badge: the sidebar caption carries the detection verdict + source
    # (default model gpt-4o-mini → static-table positive).
    captions = [c.value for c in at.caption]
    assert any("vision: yes (static)" in c for c in captions), captions
    # Image chips render as markdown pills (distinct from symbol buttons).
    markdown = " ".join(m.value for m in at.markdown)
    assert "🖼 shot.png" in markdown and "🖼 diagram.webp" in markdown
    assert not any("shot.png" in b.label for b in at.button)  # not buttons
