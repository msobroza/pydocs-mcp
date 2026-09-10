"""The live panel's end states that a page run cannot reach on demand (PROPOSAL §2, §6).

A Stop press (or a rerun mid-turn) reaches the script thread as a ``BaseException`` that
is not an ``Exception`` — Streamlit's own control flow; a failure after the page's agent
was released is the page going away, not an error. Each small script below drives a
``LiveActivityPanel`` directly under AppTest.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest


def _interrupted_script() -> None:
    import concurrent.futures

    import streamlit as st

    from pydocs_mcp.harness.ask_your_docs.activity_events import ProposedToolCall, RoundEnded
    from pydocs_mcp.harness.ask_your_docs.activity_trace import TraceLimits
    from pydocs_mcp.harness.ask_your_docs.activity_trace_builder import TraceBuilder
    from pydocs_mcp.harness.ask_your_docs.activity_view import LiveActivityPanel, PanelSettings
    from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

    class Interrupted(BaseException):  # what StopException / RerunException look like
        pass

    ui = AskYourDocsUiConfig()
    builder = TraceBuilder(limits=TraceLimits.from_ui_config(ui), redact=str, scope={})
    saved: list = []
    panel = LiveActivityPanel(builder, PanelSettings(ui, False, "host"), "t1", saved.append)
    call = ProposedToolCall("c", "grep", {"pattern": "x"})
    panel.sink(RoundEnded("", "", False, (call,), None, "m"))
    future: concurrent.futures.Future = concurrent.futures.Future()
    future.set_exception(Interrupted())
    try:
        panel.drain(future)
    except Interrupted:
        st.session_state["saved"] = [trace.state.value for trace in saved]


def _released_script() -> None:
    from pydocs_mcp.harness.ask_your_docs.activity_trace import TraceLimits
    from pydocs_mcp.harness.ask_your_docs.activity_trace_builder import TraceBuilder
    from pydocs_mcp.harness.ask_your_docs.activity_view import LiveActivityPanel, PanelSettings
    from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

    ui = AskYourDocsUiConfig()
    builder = TraceBuilder(limits=TraceLimits.from_ui_config(ui), redact=str, scope={})
    panel = LiveActivityPanel(builder, PanelSettings(ui, False, "host"), "t1", print)
    panel.fail("ServeSessionClosedError: released", "ServeSessionClosedError", released=True)


def _failed_with_markdown_script() -> None:
    from pydocs_mcp.harness.ask_your_docs.activity_trace import TraceLimits
    from pydocs_mcp.harness.ask_your_docs.activity_trace_builder import TraceBuilder
    from pydocs_mcp.harness.ask_your_docs.activity_view import (
        LiveActivityPanel,
        PanelSettings,
        render_turn_footer,
    )
    from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

    ui = AskYourDocsUiConfig()
    builder = TraceBuilder(limits=TraceLimits.from_ui_config(ui), redact=str, scope={})
    panel = LiveActivityPanel(builder, PanelSettings(ui, False, "host"), "t1", print)
    trace = panel.fail(
        "RuntimeError: ![x](http://h/?q=1) [y](http://h)", "RuntimeError", released=False
    )
    render_turn_footer(trace, "", "q", "t1")


def _after_the_tools_script() -> None:
    import concurrent.futures
    import threading

    import streamlit as st

    from pydocs_mcp.harness.ask_your_docs.activity_events import (
        ProposedToolCall,
        RoundEnded,
        ToolFinished,
    )
    from pydocs_mcp.harness.ask_your_docs.activity_trace import TraceLimits
    from pydocs_mcp.harness.ask_your_docs.activity_trace_builder import TraceBuilder
    from pydocs_mcp.harness.ask_your_docs.activity_view import LiveActivityPanel, PanelSettings
    from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

    ui = AskYourDocsUiConfig()
    builder = TraceBuilder(limits=TraceLimits.from_ui_config(ui), redact=str, scope={})
    panel = LiveActivityPanel(builder, PanelSettings(ui, False, "host"), "t1", print)
    panel.sink(RoundEnded("", "", False, (ProposedToolCall("c", "grep", {}),), None, "m"))
    panel.sink(ToolFinished("c", "grep", False, "ok", None))
    future: concurrent.futures.Future = concurrent.futures.Future()
    threading.Timer(0.4, future.set_result, ["answer"]).start()  # the final round, still going
    panel.drain(future)
    if st.session_state.get("finish"):
        panel.finish()


def test_a_failure_caption_is_rendered_inert() -> None:
    """The error text comes from the provider / MCP: an image or link in it stays text."""
    at = AppTest.from_function(_failed_with_markdown_script, default_timeout=60).run()
    assert not at.exception, at.exception
    assert [e.value for e in at.error] == [r"RuntimeError: !\[x\](http://h/?q=1) \[y\](http://h)"]


@pytest.mark.parametrize(("finish", "shown"), [(False, True), (True, False)])
def test_writing_the_answer_shows_until_the_turn_ends(finish: bool, shown: bool) -> None:
    at = AppTest.from_function(_after_the_tools_script, default_timeout=60)
    at.session_state["finish"] = finish
    at.run()
    assert not at.exception, at.exception
    assert ("Writing the answer…" in [c.value for c in at.caption]) is shown


def test_a_stop_mid_turn_saves_the_turn_as_stopped() -> None:
    at = AppTest.from_function(_interrupted_script, default_timeout=60).run()
    assert not at.exception, at.exception
    assert at.session_state["saved"] == ["stopped"]
    [status] = at.status
    assert status.state == "error" and status.label.startswith("Stopped by you after 1 step")


def test_a_failure_after_the_page_was_released_reads_as_stopped() -> None:
    at = AppTest.from_function(_released_script, default_timeout=60).run()
    assert not at.exception, at.exception
    [status] = at.status
    assert status.label.startswith("Stopped by you after 0 steps")
    assert "error stopped" not in status.label
