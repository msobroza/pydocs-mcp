"""Import guard for the library-coupled ``[retrieval]`` extra.

The base ``pydocs-mcp-eval`` install serves the black-box agent-efficiency
track, which needs only the ``pydocs-mcp`` CLI on ``PATH`` — NOT the
``pydocs_mcp`` Python library. The library-coupled parts (in-process retrieval
systems under ``pydocs_eval.systems``, the ``pydocs_eval.optimize`` overlay
server, and the ``tool_docs`` / ``usage_skill`` artifacts) import ``pydocs_mcp``
directly and therefore require the ``[retrieval]`` extra, which declares
``pydocs-mcp>=0.5.1``.

Two failure modes get two different actionable messages:

- **Missing extra** — ``pydocs_mcp`` is not installed at all. The base user
  hits a bare ``ModuleNotFoundError: No module named 'pydocs_mcp'`` with no hint
  that an extra exists; the guard names the ``pip install
  "pydocs-mcp-eval[retrieval]"`` command.
- **Version skew** — ``pydocs_mcp`` IS installed but too old, so a symbol these
  adapters consume (e.g. ``pydocs_mcp.application.tool_docs.TOTAL_TOKEN_BUDGET``,
  added after the v0.5.0 tag) is missing and its import raises ``ImportError``.
  A "just install the extra" message would be a dead-end loop for a user who
  already HAS the extra — so the guard names the required floor
  (``_REQUIRED_PYDOCS_MCP``), the installed version, and the ``-U`` upgrade
  command instead.

The two are told apart by ``importlib.util.find_spec("pydocs_mcp")``: a ``None``
spec means the top-level module can't be located (missing extra), whereas a
present spec with a failed symbol import means version skew.

Both styles mirror the product repo's ``[late-interaction]`` / ``[graph]``
install-hint pattern (see ``python/pydocs_mcp/storage/fast_plaid_uow.py``).

Usage — two shapes depending on where ``pydocs_mcp`` is imported:

- **Module-level import boundary** (the artifact modules): wrap the top-level
  ``from pydocs_mcp... import ...`` in ``try/except ImportError`` and call
  :func:`raise_missing_retrieval_extra` from the ``except`` block. Firing at
  import time is correct there — the module simply cannot be defined without
  the library. The helper inspects ``find_spec`` to pick the missing-extra vs
  version-skew message.

- **Deferred (method-level) import boundary** (the retrieval systems): call
  :func:`require_retrieval_extra` at the start of the first method that needs
  the library. The system's *construction* stays cheap (the runner builds every
  registered system on a bare ``build()``), and the actionable error surfaces
  the moment a library-coupled method actually runs.

This module itself imports nothing from ``pydocs_mcp`` — it is import-time cheap
and never fires in this dev repo, where ``pydocs_mcp`` is installed and
importable.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from typing import NoReturn

# Single source of truth for the required ``pydocs-mcp`` floor, mirroring the
# ``pydocs-mcp>=0.5.1`` pin in ``benchmarks/pyproject.toml`` (the [retrieval]
# extra, where the version is the user-facing knob). 0.5.1 is the first release
# exporting the ``pydocs_mcp.application.tool_docs`` contract constants the
# artifacts consume — see the module docstring. Referenced by the version-skew
# message below so the floor is stated in exactly one Python place.
_REQUIRED_PYDOCS_MCP = "0.5.1"

# The PyPI distribution name (dash form) — distinct from the import name
# ``pydocs_mcp`` (underscore). ``importlib.metadata.version`` keys on the
# distribution name.
_PYDOCS_MCP_DIST = "pydocs-mcp"

# Actionable hint for the "extra not installed at all" path. Tested verbatim
# against ``"pydocs-mcp-eval[retrieval]"`` so a substring match guarantees the
# pip command stays correct across edits (mirrors the product repo's
# ``_INSTALL_HINT`` constants).
_INSTALL_HINT = (
    "The library-coupled retrieval track requires the 'retrieval' extra "
    "(it declares pydocs-mcp>=" + _REQUIRED_PYDOCS_MCP + ", the indexed-retrieval "
    "library). "
    'Install with: pip install "pydocs-mcp-eval[retrieval]". '
    "The base install serves only the black-box agent-efficiency track "
    "(pydocs-mcp CLI on PATH), which does not import the pydocs_mcp library."
)


def _installed_pydocs_mcp_version() -> str | None:
    """Return the installed ``pydocs-mcp`` version, or ``None`` if unknowable.

    Offline: reads distribution metadata already on disk (no network). Returns
    ``None`` when the distribution metadata is absent — e.g. an editable/source
    checkout on ``PYTHONPATH`` with no installed dist — so the version-skew
    message can omit the "you have X" clause rather than crash.
    """
    try:
        return version(_PYDOCS_MCP_DIST)
    except PackageNotFoundError:
        return None


def _version_skew_hint() -> str:
    """Build the "installed but too old" hint naming the floor + upgrade command.

    Single-sources the floor from :data:`_REQUIRED_PYDOCS_MCP` and reports the
    installed version when metadata is available, so the user who already has
    the extra sees WHY the import failed (skew, not a missing extra) and the
    exact ``-U`` command to fix it.
    """
    installed = _installed_pydocs_mcp_version()
    have = f"you have {installed}" if installed else "the installed version is too old"
    return (
        "The library-coupled retrieval track requires pydocs-mcp>="
        + _REQUIRED_PYDOCS_MCP
        + " but "
        + have
        + " — a symbol these adapters consume "
        "(the pydocs_mcp.application.tool_docs contract constants, added after "
        "v0.5.0) is missing. The 'retrieval' extra is installed; this is a "
        "version skew, not a missing extra. "
        'Upgrade with: pip install -U "pydocs-mcp-eval[retrieval]".'
    )


def raise_missing_retrieval_extra(cause: BaseException | None = None) -> NoReturn:
    """Raise a ``RuntimeError`` with the right ``[retrieval]`` hint for the case.

    Call from an ``except ImportError`` block wrapping a ``pydocs_mcp`` import.
    The helper distinguishes two failure modes via
    :func:`importlib.util.find_spec`:

    - ``find_spec("pydocs_mcp") is None`` → the library is not installed →
      missing-extra hint (``pip install "pydocs-mcp-eval[retrieval]"``).
    - the spec EXISTS but a symbol import failed → the library is present but
      too old (version skew) → upgrade hint naming :data:`_REQUIRED_PYDOCS_MCP`,
      the installed version, and ``pip install -U "pydocs-mcp-eval[retrieval]"``.

    ``cause`` chains the original ImportError for a ``raise ... from`` traceback.

    Example::

        try:
            from pydocs_mcp.application.tool_docs import TOTAL_TOKEN_BUDGET
        except ImportError as exc:
            raise_missing_retrieval_extra(exc)
    """
    if _pydocs_mcp_spec_present():
        raise RuntimeError(_version_skew_hint()) from cause
    raise RuntimeError(_INSTALL_HINT) from cause


def _pydocs_mcp_spec_present() -> bool:
    """Whether ``pydocs_mcp`` can be located without importing its package init.

    ``find_spec`` itself raises ``ModuleNotFoundError`` (an ``ImportError``
    subclass) when an intermediate parent is missing; the test-suite sentinel
    ``sys.modules["pydocs_mcp"] = None`` makes it raise too. Both mean "not
    locatable" → treat as absent.
    """
    try:
        return find_spec("pydocs_mcp") is not None
    except ImportError:
        return False


def require_retrieval_extra() -> None:
    """Ensure ``pydocs_mcp`` is importable; raise the missing-extra hint otherwise.

    Import-time cheap: uses :func:`importlib.util.find_spec` so it does NOT
    execute ``pydocs_mcp``'s (heavy) package ``__init__`` — it only checks that
    the top-level module can be located. Deferred-import boundaries call this at
    the start of the first library-coupled method so construction stays free of
    the ``pydocs_mcp`` import cost.

    This path fires only for the "not installed at all" case (``find_spec``
    returns ``None``): a locatable-but-too-old library passes ``find_spec`` and
    surfaces its version skew later, at the deferred ``from pydocs_mcp... import``
    inside the method, which routes through :func:`raise_missing_retrieval_extra`.

    Raises:
        RuntimeError: ``pydocs_mcp`` is not installed (the ``[retrieval]`` extra
            is missing); the message names the exact pip command.
    """
    if not _pydocs_mcp_spec_present():
        raise_missing_retrieval_extra()
