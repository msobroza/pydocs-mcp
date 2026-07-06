"""_capture_safely — the single owner of the swallow-and-warn capture policy.

A broken file must degrade to 'no references captured', never abort
indexing; previously that policy was hand-copied at three sites in
ast_python.py (capture_imports / capture_calls / capture_inherits).
"""

from __future__ import annotations

import logging

from pydocs_mcp.extraction.strategies.chunkers.ast_python import _capture_safely


def test_invokes_capture_fn_with_kwargs() -> None:
    seen: dict[str, object] = {}

    def fake_capture(**kwargs: object) -> None:
        seen.update(kwargs)

    _capture_safely(fake_capture, "pkg/mod.py", body=[1, 2], from_package="pkg")
    assert seen == {"body": [1, 2], "from_package": "pkg"}


def test_swallows_failure_and_warns_with_fn_name_and_label(caplog) -> None:
    def capture_calls(**kwargs: object) -> None:
        raise ValueError("bad node")

    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        _capture_safely(capture_calls, "pkg.mod.Cls.method")
    # capture_fn.__name__ reproduces the pre-refactor per-site messages
    # ("capture_calls failed on <label>: <exc>") verbatim.
    assert "capture_calls failed on pkg.mod.Cls.method: bad node" in caplog.text
