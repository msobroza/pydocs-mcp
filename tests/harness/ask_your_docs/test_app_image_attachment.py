"""AC25 (spec 2026-07-11-multimodal-image-agent): capability badge + image
chips, via the AppTest pattern of test_app_attachment.py."""

import pytest

pytest.importorskip("streamlit")

from pydocs_mcp.harness.ask_your_docs.scope_capabilities import NO_SCOPE_CAPABILITIES

from ._connection_fakes import FakeBearer
from ._fixture import make_bundle

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import page, page_env, write_config


def test_capability_badge_and_image_chips_render(tmp_path, monkeypatch):
    make_bundle(tmp_path / "ws" / "demo_0123456789.db", members=[("mod_a", "Foo", "class")])
    # vision=None leaves detection to the ladder: gpt-4o-mini is a static-table positive.
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="gpt-4o-mini", vision=None))
    at = page(
        scope_capabilities=NO_SCOPE_CAPABILITIES,
        connection_bearer=FakeBearer(),
        image_chips=["shot.png", "diagram.webp"],
    )
    at.run()
    assert not at.exception, at.exception
    # Badge: the sidebar caption carries the detection verdict + source.
    captions = [c.value for c in at.caption]
    assert any("vision: yes (static)" in c for c in captions), captions
    # Image chips render as markdown pills (distinct from symbol buttons).
    markdown = " ".join(m.value for m in at.markdown)
    assert "🖼 shot.png" in markdown and "🖼 diagram.webp" in markdown
    assert not any("shot.png" in b.label for b in at.button)  # not buttons
