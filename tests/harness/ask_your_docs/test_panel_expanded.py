"""Whether a turn's panel opens expanded — the ONE decision both render paths share.

WHY this file exists: the live path (``LiveActivityPanel._show_final``) and the rerun path
(``render_saved_turn``) each used to decide this for themselves, and the rerun path silently
ignored ``collapse_when_done``. So a completed turn collapsed on the next rerun whatever the
setting said, which made the panel look absent. One helper, pinned here, is the fix.
"""

from __future__ import annotations

import pytest

# WHY: activity_view imports streamlit, which only the [harness-ask-your-docs] extra
# installs; CI's core python job has no such extra, so this file must skip there, not
# error at collection — the same guard every sibling harness test carries.
pytest.importorskip("streamlit")

from pydocs_mcp.harness.ask_your_docs.activity_trace import TurnState, TurnTrace
from pydocs_mcp.harness.ask_your_docs.activity_view import panel_expanded
from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import (
    ActivityUiConfig,
    AskYourDocsUiConfig,
)


def _ui(*, collapse: bool) -> AskYourDocsUiConfig:
    return AskYourDocsUiConfig(activity=ActivityUiConfig(collapse_when_done=collapse))


@pytest.mark.parametrize(
    ("state", "collapse", "expected"),
    [
        # A finished turn is the only one collapse_when_done may close.
        (TurnState.COMPLETE, True, False),
        (TurnState.COMPLETE, False, True),
        # A turn that failed or was stopped always stays open, whatever the setting.
        (TurnState.ERROR, True, True),
        (TurnState.ERROR, False, True),
        (TurnState.STOPPED, True, True),
        (TurnState.STOPPED, False, True),
        (TurnState.RUNNING, True, True),
    ],
)
def test_panel_expanded(state: TurnState, collapse: bool, expected: bool) -> None:
    assert panel_expanded(TurnTrace(state=state), _ui(collapse=collapse)) is expected


def test_the_shipped_default_leaves_a_finished_turn_open() -> None:
    """The panel is this release's headline feature; shipping it collapsed hides it."""
    assert panel_expanded(TurnTrace(state=TurnState.COMPLETE), AskYourDocsUiConfig()) is True
