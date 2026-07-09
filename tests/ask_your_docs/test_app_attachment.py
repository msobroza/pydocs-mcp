import os
from pathlib import Path

import pytest

# The Streamlit UI ships only with the [ask-your-docs] extra, which the core CI
# matrix does not install. Skip (don't fail) when streamlit is absent.
pytest.importorskip("streamlit")


def test_attached_symbols_render_as_chips():
    from streamlit.testing.v1 import AppTest

    import pydocs_mcp.ask_your_docs.app as appmod

    os.environ["PYDOCS_WORKSPACE"] = str(Path("~/pydocs-index").expanduser())
    at = AppTest.from_file(appmod.__file__, default_timeout=180)
    at.session_state["attached"] = ["mod_a.Foo"]
    at.run()
    assert not at.exception, at.exception
    assert any("Foo" in b.label for b in at.button)
