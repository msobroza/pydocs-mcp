import pytest

# agent.py imports the [ask-your-docs] langchain/langgraph stack at module level;
# that extra is not on the core CI matrix. Skip (don't fail) when it is absent.
pytest.importorskip("langchain_core")


def test_weave_prepends_deduped_context():
    from pydocs_mcp.ask_your_docs.agent import weave_attachments

    woven = weave_attachments(["a.b.C", "a.b.C", "d.e.f"], "how does it work?")
    assert woven == "Regarding `a.b.C`, `d.e.f`: how does it work?"


def test_weave_empty_is_identity():
    from pydocs_mcp.ask_your_docs.agent import weave_attachments

    assert weave_attachments([], "hi") == "hi"
