"""Import guard for the library-coupled ``[retrieval]`` extra.

The base ``pydocs-mcp-eval`` install serves the black-box agent-efficiency
track, which needs only the ``pydocs-mcp`` CLI on ``PATH`` — NOT the
``pydocs_mcp`` Python library. The library-coupled parts (in-process retrieval
systems under ``pydocs_eval.systems``, the ``pydocs_eval.optimize`` overlay
server, and the ``tool_docs`` / ``usage_skill`` artifacts) import ``pydocs_mcp``
directly and therefore require the ``[retrieval]`` extra, which declares
``pydocs-mcp>=0.5``.

Without this guard a base-install user hits a bare ``ModuleNotFoundError:
No module named 'pydocs_mcp'`` with no hint that an extra exists. The guard
turns that into a ``RuntimeError`` naming the exact pip command, mirroring the
product repo's ``[late-interaction]`` / ``[watch]`` install-hint style
(see ``python/pydocs_mcp/storage/fast_plaid_uow.py``).

Usage — two shapes depending on where ``pydocs_mcp`` is imported:

- **Module-level import boundary** (the artifact modules): wrap the top-level
  ``from pydocs_mcp... import ...`` in ``try/except ImportError`` and call
  :func:`raise_missing_retrieval_extra` from the ``except`` block. Firing at
  import time is correct there — the module simply cannot be defined without
  the library.

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

from importlib.util import find_spec
from typing import NoReturn

# Single source of truth for the actionable install hint. Tested verbatim
# against ``"pydocs-mcp-eval[retrieval]"`` so a substring match guarantees the
# pip command stays correct across edits (mirrors the product repo's
# ``_INSTALL_HINT`` constants).
_INSTALL_HINT = (
    "The library-coupled retrieval track requires the 'retrieval' extra "
    "(it declares pydocs-mcp>=0.5, the indexed-retrieval library). "
    'Install with: pip install "pydocs-mcp-eval[retrieval]". '
    "The base install serves only the black-box agent-efficiency track "
    "(pydocs-mcp CLI on PATH), which does not import the pydocs_mcp library."
)


def raise_missing_retrieval_extra(cause: BaseException | None = None) -> NoReturn:
    """Raise a ``RuntimeError`` carrying the ``[retrieval]`` install hint.

    Call from an ``except ImportError`` block wrapping a ``pydocs_mcp`` import
    so the base-install user sees an actionable command instead of a bare
    ``ModuleNotFoundError``. ``cause`` chains the original ImportError for a
    ``raise ... from`` traceback.

    Example::

        try:
            from pydocs_mcp.application.tool_docs import TOOL_DOCS
        except ImportError as exc:
            raise_missing_retrieval_extra(exc)
    """
    raise RuntimeError(_INSTALL_HINT) from cause


def require_retrieval_extra() -> None:
    """Ensure ``pydocs_mcp`` is importable; raise the install hint otherwise.

    Import-time cheap: uses :func:`importlib.util.find_spec` so it does NOT
    execute ``pydocs_mcp``'s (heavy) package ``__init__`` — it only checks that
    the top-level module can be located. Deferred-import boundaries call this at
    the start of the first library-coupled method so construction stays free of
    the ``pydocs_mcp`` import cost.

    Raises:
        RuntimeError: ``pydocs_mcp`` is not installed (the ``[retrieval]`` extra
            is missing); the message names the exact pip command.
    """
    try:
        found = find_spec("pydocs_mcp") is not None
    except ImportError as exc:
        # ``find_spec`` itself raises ImportError when an intermediate parent
        # package is missing — treat that identically to "not found".
        raise_missing_retrieval_extra(exc)
    if not found:
        raise_missing_retrieval_extra()
