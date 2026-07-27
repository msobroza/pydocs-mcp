"""harness/core/extra_guard — the product-side missing-extra guard.

Core-only: probes fake module names, so no optional extra is needed.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.core.extra_guard import require_extra_modules


def test_all_present_is_a_no_op() -> None:
    require_extra_modules(("importlib", "argparse"), extra="x", command="y")


def test_missing_module_exits_naming_extra_and_command() -> None:
    with pytest.raises(SystemExit) as excinfo:
        require_extra_modules(
            ("importlib", "definitely_not_a_real_module_xyz"),
            extra="harness-ask-your-docs",
            command="harness-ask-your-docs",
        )
    msg = str(excinfo.value)
    assert "definitely_not_a_real_module_xyz" in msg
    assert "pip install 'pydocs-mcp[harness-ask-your-docs]'" in msg
    assert msg.startswith("harness-ask-your-docs needs")
