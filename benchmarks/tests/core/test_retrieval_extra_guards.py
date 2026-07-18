"""Pin the ``[retrieval]`` extra boundary.

The base ``pydocs-mcp-eval`` install serves the black-box agent-efficiency
track (pydocs-mcp CLI on PATH only) and does NOT depend on the ``pydocs_mcp``
Python library. The library-coupled parts import ``pydocs_mcp`` and are gated
behind the ``[retrieval]`` extra. Import guards
(``pydocs_eval._retrieval_extra``) turn a missing-extra ``ModuleNotFoundError``
into an actionable ``RuntimeError`` naming ``pip install
"pydocs-mcp-eval[retrieval]"``.

These tests hide ``pydocs_mcp`` with the standard CPython sentinel
(``sys.modules["pydocs_mcp"] = None`` makes ``import pydocs_mcp`` raise
``ImportError`` even though the library IS installed in this dev repo) and
assert each boundary raises the actionable hint. They are fully offline: no
subprocess, no socket, no live LLM.

The two guard shapes are covered:

- **Deferred (method-level)** — the retrieval systems import ``pydocs_mcp``
  inside methods, so construction stays cheap and the guard fires when the
  first library-coupled method runs (``index()``).
- **Module-level** — the optimize artifacts + overlay server import
  ``pydocs_mcp`` at module scope, so the guard fires at import time (asserted
  via ``importlib.reload`` with the library hidden).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

# The install hint the guard raises; a substring match keeps the tests honest
# against the single-source-of-truth constant without re-encoding the whole
# sentence. Matches ``pydocs_eval._retrieval_extra._INSTALL_HINT``.
_EXPECTED_PIP_HINT = 'pip install "pydocs-mcp-eval[retrieval]"'

# The upgrade hint the version-skew path raises. The ``-U`` distinguishes it
# from the missing-extra hint (which has no ``-U``).
_EXPECTED_UPGRADE_HINT = 'pip install -U "pydocs-mcp-eval[retrieval]"'


def _hide_pydocs_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block every ``import pydocs_mcp[...]`` for the duration of the test.

    Setting ``sys.modules[name] = None`` is the CPython sentinel that makes a
    subsequent ``import name`` raise ``ImportError`` even when the module is
    installed. We drop the already-imported ``pydocs_mcp`` submodules first so
    a cached entry can't satisfy the import, then plant the ``None`` sentinel
    on the top-level name (which is what every guard's import/``find_spec``
    resolves against). ``monkeypatch`` restores ``sys.modules`` after the test.
    """
    for name in list(sys.modules):
        if name == "pydocs_mcp" or name.startswith("pydocs_mcp."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "pydocs_mcp", None)


# --- The guard helper itself ------------------------------------------------


def test_require_retrieval_extra_raises_actionable_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydocs_eval._retrieval_extra import require_retrieval_extra

    _hide_pydocs_mcp(monkeypatch)
    with pytest.raises(RuntimeError) as excinfo:
        require_retrieval_extra()
    assert _EXPECTED_PIP_HINT in str(excinfo.value)


def test_require_retrieval_extra_is_noop_when_library_present() -> None:
    # WHY: the guard must NOT fire in this dev repo, where pydocs_mcp is
    # importable — a false-positive would break every retrieval run here.
    from pydocs_eval._retrieval_extra import require_retrieval_extra

    require_retrieval_extra()  # must not raise


# --- Version-aware branch: missing extra vs version skew --------------------


def test_missing_extra_path_raises_install_hint_not_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Module ABSENT (find_spec is None): the guard must name the missing-extra
    # install command and must NOT mislead the user toward an -U upgrade.
    from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

    _hide_pydocs_mcp(monkeypatch)
    with pytest.raises(RuntimeError) as excinfo:
        raise_missing_retrieval_extra(ImportError("No module named 'pydocs_mcp'"))
    message = str(excinfo.value)
    assert _EXPECTED_PIP_HINT in message
    assert _EXPECTED_UPGRADE_HINT not in message


def test_version_skew_path_raises_upgrade_hint_naming_floor() -> None:
    # Module PRESENT (find_spec resolves in this dev repo) but a symbol import
    # failed: the guard must diagnose version skew — name the required floor and
    # advise the -U upgrade, NOT the plain missing-extra install. Offline: we
    # invoke the guard directly with a simulated symbol-missing ImportError
    # while pydocs_mcp stays importable (no reload, no network).
    from pydocs_eval._retrieval_extra import (
        _REQUIRED_PYDOCS_MCP,
        raise_missing_retrieval_extra,
    )

    symbol_error = ImportError(
        "cannot import name 'TOTAL_TOKEN_BUDGET' from 'pydocs_mcp.application.tool_docs'"
    )
    with pytest.raises(RuntimeError) as excinfo:
        raise_missing_retrieval_extra(symbol_error)
    message = str(excinfo.value)
    # Names the floor from the single-source constant (no duplicated literal).
    assert _REQUIRED_PYDOCS_MCP in message
    assert f"pydocs-mcp>={_REQUIRED_PYDOCS_MCP}" in message
    # Advises the upgrade, not the plain install — this user HAS the extra.
    assert _EXPECTED_UPGRADE_HINT in message


def test_version_skew_hint_reports_installed_version_when_known() -> None:
    # In this dev repo pydocs-mcp IS installed with resolvable metadata, so the
    # skew message must surface the concrete installed version ("you have X"),
    # helping the user see the gap between installed and required.
    from importlib.metadata import version

    from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

    installed = version("pydocs-mcp")
    with pytest.raises(RuntimeError) as excinfo:
        raise_missing_retrieval_extra(ImportError("symbol missing"))
    assert f"you have {installed}" in str(excinfo.value)


# --- Deferred (method-level) boundary: retrieval systems --------------------


def test_pydocs_system_index_raises_without_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Construct while the library is present (config load needs it), then hide
    # pydocs_mcp and assert the FIRST library-coupled method surfaces the hint.
    from pydocs_eval.systems.pydocs import PydocsMcpSystem
    from pydocs_mcp.retrieval.config import AppConfig

    config = AppConfig.load()
    system = PydocsMcpSystem()  # construction is cheap — no runtime import
    _hide_pydocs_mcp(monkeypatch)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(system.index(Path("/nonexistent-corpus"), config))
    assert _EXPECTED_PIP_HINT in str(excinfo.value)


def test_pydocs_oracle_index_raises_without_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The oracle overrides index() without super() — its own guard must fire.
    from pydocs_eval.systems.pydocs_oracle import PydocsOracleSystem
    from pydocs_mcp.retrieval.config import AppConfig

    config = AppConfig.load()
    system = PydocsOracleSystem()
    _hide_pydocs_mcp(monkeypatch)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(system.index(Path("/nonexistent-corpus"), config))
    assert _EXPECTED_PIP_HINT in str(excinfo.value)


def test_pydocs_system_construction_does_not_need_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WHY: the runner builds every registered system on a bare build(); that
    # must stay free of the pydocs_mcp import even without the extra.
    from pydocs_eval.systems.pydocs import PydocsMcpSystem

    _hide_pydocs_mcp(monkeypatch)
    PydocsMcpSystem()  # must not raise


# --- Module-level boundary: optimize artifacts + overlay server -------------


def _reload_expecting_guard(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    """Reload ``module_name`` with ``pydocs_mcp`` hidden and assert the guard.

    The module-level ``try/except`` guard sits ABOVE the module body (and any
    ``@artifact_registry.register`` decorator), so a failed reload raises the
    ``RuntimeError`` before anything else runs — the registry is untouched on
    the failure path.

    Restore is NOT a second ``importlib.reload``: that would re-execute the
    ``@register`` decorator (duplicate-registration ``ValueError``) AND mint a
    NEW class object, breaking the ``isinstance`` / registry identity that other
    tests rely on. Instead we snapshot the module's ``__dict__`` up front and
    restore it in place, so the ORIGINAL class objects (and the registry entry
    that already points at them) survive intact.
    """
    module = importlib.import_module(module_name)
    saved_dict = dict(module.__dict__)
    _hide_pydocs_mcp(monkeypatch)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            importlib.reload(module)
        assert _EXPECTED_PIP_HINT in str(excinfo.value)
    finally:
        # A failed reload re-execs into the SAME module object, partly
        # overwriting its namespace before the guard raised. Restore the
        # snapshot in place so the original class identities (and thus the
        # registry entry + downstream isinstance checks) are preserved.
        module.__dict__.clear()
        module.__dict__.update(saved_dict)


def test_tool_docs_artifact_module_raises_without_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reload_expecting_guard(monkeypatch, "pydocs_eval.optimize.artifacts.tool_docs")


def test_delimited_grammar_module_raises_without_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The shared delimited grammar delegates to the product's
    # description_source module (ADR 0005), so it is library-coupled like the
    # artifacts that consume it and must surface the same actionable hint.
    _reload_expecting_guard(monkeypatch, "pydocs_eval.optimize.artifacts._delimited")


def test_usage_skill_artifact_module_raises_without_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reload_expecting_guard(monkeypatch, "pydocs_eval.optimize.artifacts.usage_skill")


def test_overlay_server_module_raises_without_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reload_expecting_guard(monkeypatch, "pydocs_eval.optimize._overlay_server")
