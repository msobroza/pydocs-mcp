import pytest

# The Streamlit UI ships only with the [harness-ask-your-docs] extra, which the core CI
# matrix does not install. Skip (don't fail) when streamlit is absent.
pytest.importorskip("streamlit")

from pydocs_mcp.harness.ask_your_docs.scope_capabilities import NO_SCOPE_CAPABILITIES

from ._fixture import make_bundle

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import page, page_env


def test_attached_symbols_render_as_chips(tmp_path):
    make_bundle(tmp_path / "ws" / "demo_0123456789.db", members=[("mod_a", "Foo", "class")])
    at = page(scope_capabilities=NO_SCOPE_CAPABILITIES, attached=["mod_a.Foo"])
    at.run()
    assert not at.exception, at.exception
    assert any("Foo" in b.label for b in at.button)
