"""Helpers for dependency discovery + inspect-mode symbol collection.

Inspect-mode symbol collection lives here (not in the legacy ``indexer.py``
module that has been deleted) so the ``extraction/*`` package stays
self-contained. Only :class:`InspectMemberExtractor` consumes the output —
the function returns only ``symbols`` (no chunks, no package record);
chunk extraction flows through the ingestion pipeline.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import logging
import pkgutil
from pathlib import Path

from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField

log = logging.getLogger("pydocs-mcp")

SKIP_IMPORT = frozenset({
    "setuptools", "pip", "wheel", "pkg_resources",
    "distutils", "_distutils_hack", "certifi",
})
IMPORT_ALIASES = {
    "pillow": "PIL", "scikit-learn": "sklearn", "python-dateutil": "dateutil",
    "pyyaml": "yaml", "beautifulsoup4": "bs4", "opencv-python": "cv2",
    "opencv-python-headless": "cv2", "attrs": "attr",
}

# Per-member text truncation limits. The pre-refactor inspect path trimmed
# both signature and docstring before persisting; without these, a
# pathological module (e.g. an ML library with 10KB-docstring helper
# functions) bloats FTS rows and slows queries. See /ultrareview F12.
MAX_SIGNATURE_CHARS: int = 200
MAX_DOCSTRING_CHARS: int = 1024
# Per-module member cap. Defaults to MembersConfig.members_per_module_cap
# (120) but callers should pass the configured value through; the
# constant is the back-stop / unit-test default.
DEFAULT_MEMBERS_PER_MODULE_CAP: int = 120


def find_installed_distribution(dep_name: str):
    """Locate the installed ``importlib.metadata`` distribution for *dep_name*.

    Returns ``None`` if no matching distribution is installed — the service
    treats that as a non-fatal skip rather than a hard failure, because
    declared-but-not-installed deps are common during local development.
    """
    target = normalize_package_name(dep_name)
    for dist in importlib.metadata.distributions():
        raw = dist.metadata["Name"]
        if not raw:
            continue
        if raw.lower().replace("-", "_") == target:
            return dist
    return None


def find_site_packages_root(any_file: str) -> str:
    """Walk up to find site-packages directory."""
    for parent in Path(any_file).parents:
        if parent.name in ("site-packages", "dist-packages"):
            return str(parent)
    return str(Path(any_file).parent.parent)


def _extract_by_import(
    dist,
    depth: int = 1,
    *,
    members_per_module_cap: int = DEFAULT_MEMBERS_PER_MODULE_CAP,
) -> dict:
    """Live-import a distribution's modules and collect ModuleMember symbols.

    Returns ``{"symbols": tuple[ModuleMember, ...]}`` — the shape
    :class:`InspectMemberExtractor` consumes. The extractor only reads
    ``record["symbols"]``; chunk extraction in sub-PR #5 flows through the
    ingestion pipeline, not this helper.

    ``members_per_module_cap`` is enforced PER MODULE during the collection
    walk — once a module yields ``cap`` members we stop iterating its
    ``getmembers`` output for that module. Submodules each get their own
    fresh budget. Restores the pre-refactor DoS guard (F4).

    Side-effectful (runs module top-level code). Callers must own the risk —
    :class:`InspectMemberExtractor` falls back to :class:`AstMemberExtractor`
    when an import raises.
    """
    pkg_name = normalize_package_name(dist.metadata["Name"])
    if pkg_name in SKIP_IMPORT:
        return {"symbols": ()}

    import_name = IMPORT_ALIASES.get(pkg_name, pkg_name)
    try:
        mod = importlib.import_module(import_name)
    except Exception:  # noqa: BLE001 -- spec §9.2: live-import fallback allowlist
        return {"symbols": ()}

    symbols: list[ModuleMember] = []
    _collect_symbols(
        mod, pkg_name, import_name, symbols, depth,
        members_per_module_cap=members_per_module_cap,
    )
    return {"symbols": tuple(symbols)}


def _collect_symbols(
    mod,
    pkg_name: str,
    module_path: str,
    symbols: list[ModuleMember],
    remaining_depth: int,
    *,
    members_per_module_cap: int = DEFAULT_MEMBERS_PER_MODULE_CAP,
) -> None:
    """Recurse into submodules; collect function + class signatures.

    ``remaining_depth == 1`` means root module only; ``2`` adds one level of
    submodules, and so on. ``pkg_name`` is the normalized (PEP 503) package
    key stamped on every :class:`ModuleMember`; ``module_path`` is the full
    dotted path used for recursion + metadata. Per-module cap + per-field
    truncation are applied here (F4 + F12 from /ultrareview).
    """
    try:
        members = inspect.getmembers(mod)
    except Exception:  # noqa: BLE001 -- defensive; getmembers can raise on exotic modules
        return

    collected_in_this_module = 0
    for name, obj in members:
        if collected_in_this_module >= members_per_module_cap:
            log.debug(
                "members_per_module_cap=%d reached for %s; truncating",
                members_per_module_cap, module_path,
            )
            break
        if name.startswith("_"):
            continue
        if inspect.isfunction(obj) or inspect.isclass(obj):
            try:
                sig = str(inspect.signature(obj))
            except (ValueError, TypeError):
                sig = "(...)"
            kind = "class" if inspect.isclass(obj) else "function"
            symbols.append(ModuleMember(metadata={
                ModuleMemberFilterField.PACKAGE.value: pkg_name,
                ModuleMemberFilterField.MODULE.value: module_path,
                ModuleMemberFilterField.NAME.value: name,
                ModuleMemberFilterField.KIND.value: kind,
                "signature": _truncate(sig, MAX_SIGNATURE_CHARS),
                "return_annotation": "",
                "parameters": (),
                "docstring": _truncate(inspect.getdoc(obj) or "", MAX_DOCSTRING_CHARS),
            }))
            collected_in_this_module += 1

    if remaining_depth <= 1 or not hasattr(mod, "__path__"):
        return

    try:
        submodules = list(pkgutil.iter_modules(mod.__path__))
    except Exception:  # noqa: BLE001 -- iter_modules can blow up on namespace packages
        return

    for _finder, subname, _ispkg in submodules:
        if subname.startswith("_"):
            continue
        full_name = f"{module_path}.{subname}"
        try:
            submod = importlib.import_module(full_name)
        except Exception:  # noqa: BLE001 -- per-submodule failure is non-fatal
            log.debug("submodule import failed: %s", full_name)
            continue
        _collect_symbols(
            submod, pkg_name, full_name, symbols, remaining_depth - 1,
            members_per_module_cap=members_per_module_cap,
        )


def _truncate(text: str, limit: int) -> str:
    """Cap ``text`` to ``limit`` chars, appending an ellipsis marker so
    consumers can tell truncation happened without re-measuring."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


__all__ = (
    "IMPORT_ALIASES",
    "SKIP_IMPORT",
    "find_installed_distribution",
    "find_site_packages_root",
    "_extract_by_import",
)
