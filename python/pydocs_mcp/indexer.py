"""Extraction-only helpers for project source + installed deps (spec §8, AC #17).

Two modes for deps:
  static (``use_inspect=False``): read .py from site-packages, same parser as project.
  inspect (``use_inspect=True``):  import modules, use ``inspect.getmembers``.

Static mode is faster, safer (no side-effects), and fully parallelisable.

This module is intentionally thin: the public surface is a set of ``extract_*``
coroutines that return ``(chunks, package)`` or ``members``. All orchestration —
iteration over deps, cache checks, stats accumulation, ``IndexingService``
writes — lives in :class:`pydocs_mcp.application.IndexProjectService`.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import logging
import os
import pkgutil
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp._fast import (
    extract_module_doc,
    hash_files,
    parse_py_file,
    read_files_parallel,
    split_into_chunks,
    walk_py_files,
)
from pydocs_mcp.constants import (
    CLASS_DOCSTRING_MAX,
    CLASS_FULL_DOC_MAX,
    CLASS_METHODS_MAX,
    FUNC_DOCSTRING_MAX,
    METHOD_SUMMARY_MAX,
    MODULE_DOCSTRING_MAX,
    PARAM_DEFAULT_MAX,
    PARAMS_JSON_MAX,
    REQUIREMENTS_PARSE_MAX,
    RETURN_TYPE_MAX,
    SIGNATURE_MAX,
)
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
    Parameter,
)

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

# ──────────────────────────────────────────────────────────────────────────
# Extraction cache (scoped to a single ``IndexProjectService.index_project``)
# ──────────────────────────────────────────────────────────────────────────
#
# ``ChunkExtractorAdapter`` and ``MemberExtractorAdapter`` run the SAME
# expensive extraction (walk + Rust/regex parse for static; ``inspect``
# for imports). Without a cache, calling chunks-then-members would walk
# the whole tree twice.
#
# The cache keys on the resolved ``Path``/``dep_name`` so a single call to
# ``IndexProjectService.index_project`` shares work between the two
# adapters. It is explicitly cleared at the end of the service call via
# :func:`clear_extraction_cache` so successive runs see fresh data.
@dataclass(frozen=True, slots=True)
class _ProjectExtractionRecord:
    chunks: tuple[Chunk, ...]
    members: tuple[ModuleMember, ...]
    package: Package


@dataclass(frozen=True, slots=True)
class _DependencyExtractionRecord:
    chunks: tuple[Chunk, ...]
    members: tuple[ModuleMember, ...]
    package: Package


_project_cache: dict[str, _ProjectExtractionRecord] = {}
_dependency_cache: dict[tuple[str, bool, int], _DependencyExtractionRecord] = {}


def clear_extraction_cache() -> None:
    """Drop the module-level project/dep extraction cache.

    Called by :class:`IndexProjectService.index_project` at the end of a
    run so a subsequent indexing pass re-extracts from scratch (the whole
    point of running the indexer is to pick up edits).
    """
    _project_cache.clear()
    _dependency_cache.clear()


# ── Shared: parse .py files without importing ─────────────────────────────

def _extract_from_source_files(
    package_name: str,
    py_paths: list[str],
    root: str,
    kind_prefix: str = "project",
) -> tuple[list[Chunk], list[ModuleMember]]:
    """Parse .py files with Rust/regex parser. No imports needed.

    Used for both project source and deps in static mode.
    Returns (chunks, module_members) as typed domain models.
    """
    file_contents = read_files_parallel(py_paths)
    chunks: list[Chunk] = []
    module_members: list[ModuleMember] = []

    for filepath, source in file_contents:
        if not source:
            continue

        try:
            relative_path = os.path.relpath(filepath, root)
        except ValueError:
            continue
        module = relative_path.replace(os.sep, ".").removesuffix(".py").replace(".__init__", "")

        doc = extract_module_doc(source)
        if len(doc) > 20:
            chunks.append(
                Chunk(
                    text=doc[:MODULE_DOCSTRING_MAX],
                    metadata={
                        ChunkFilterField.PACKAGE.value: package_name,
                        ChunkFilterField.TITLE.value: module,
                        ChunkFilterField.ORIGIN.value: f"{kind_prefix}_doc",
                    },
                )
            )

        for symbol in parse_py_file(source):
            module_members.append(
                ModuleMember(
                    metadata={
                        ModuleMemberFilterField.PACKAGE.value: package_name,
                        ModuleMemberFilterField.MODULE.value: module,
                        ModuleMemberFilterField.NAME.value: symbol.name,
                        ModuleMemberFilterField.KIND.value: symbol.kind,
                        "signature": symbol.signature,
                        "return_annotation": "",
                        "parameters": (),
                        "docstring": symbol.docstring,
                    }
                )
            )

        for heading, body in split_into_chunks(source):
            chunks.append(
                Chunk(
                    text=body,
                    metadata={
                        ChunkFilterField.PACKAGE.value: package_name,
                        ChunkFilterField.TITLE.value: f"{module}:{heading}",
                        ChunkFilterField.ORIGIN.value: f"{kind_prefix}_code",
                    },
                )
            )

    return chunks, module_members


# ── Project source ────────────────────────────────────────────────────────

def _extract_project_record(root: Path) -> _ProjectExtractionRecord:
    """Synchronously walk + hash + parse ``root``, returning the full record.

    Shared by :func:`extract_project_chunks` and :func:`extract_project_members`
    via ``_project_cache`` — the two coroutines run back-to-back in
    :class:`IndexProjectService.index_project`, so the cache means the walk
    runs once per service call.
    """
    cache_key = str(root)
    cached = _project_cache.get(cache_key)
    if cached is not None:
        return cached

    package_name = "__project__"
    py_paths = walk_py_files(str(root))
    new_hash = hash_files(py_paths)
    chunks_list, members_list = _extract_from_source_files(
        package_name, py_paths, str(root),
    )
    package = Package(
        name=package_name,
        version="local",
        summary=f"Project: {root.name}",
        homepage="",
        dependencies=(),
        content_hash=new_hash,
        origin=PackageOrigin.PROJECT,
    )
    record = _ProjectExtractionRecord(
        chunks=tuple(chunks_list),
        members=tuple(members_list),
        package=package,
    )
    _project_cache[cache_key] = record
    return record


async def extract_project_chunks(
    project_dir: Path,
) -> tuple[tuple[Chunk, ...], Package]:
    """Return (chunks, package) for the project under ``project_dir``.

    The underlying ``walk_py_files`` + ``hash_files`` + parse is CPU-bound,
    so we run it on a worker thread via ``asyncio.to_thread`` to keep the
    event loop responsive.
    """
    record = await asyncio.to_thread(_extract_project_record, project_dir)
    return record.chunks, record.package


async def extract_project_members(project_dir: Path) -> tuple[ModuleMember, ...]:
    """Return the module-member tuple for the project under ``project_dir``."""
    record = await asyncio.to_thread(_extract_project_record, project_dir)
    return record.members


# ── Dependency helpers ────────────────────────────────────────────────────

def _build_package_record(dist, name: str, version: str) -> dict:
    package_record = {
        "name": name, "version": version,
        "hash": hashlib.md5(f"{name}:{version}".encode()).hexdigest()[:12],
        "summary": dist.metadata["Summary"] or "",
        "homepage": dist.metadata["Home-page"] or "",
        "requires": tuple(
            r.split(";")[0].strip() for r in (dist.requires or [])[:REQUIREMENTS_PARSE_MAX]
        ),
        "chunks": [], "symbols": [],
    }
    payload = dist.metadata.get_payload()
    if isinstance(payload, str) and len(payload.strip()) > 50:
        for heading, body in split_into_chunks(payload.strip()):
            package_record["chunks"].append(
                Chunk(
                    text=body,
                    metadata={
                        ChunkFilterField.PACKAGE.value: name,
                        ChunkFilterField.TITLE.value: heading,
                        ChunkFilterField.ORIGIN.value: "readme",
                    },
                )
            )
    return package_record


def _append_doc_file_chunks(dist, name: str, package_record: dict):
    try:
        for f in (dist.files or []):
            fn = str(f).lower()
            if not any(fn.endswith(e) for e in (".md", ".rst", ".txt")):
                continue
            if not any(k in fn for k in ("readme", "doc", "guide", "api", "usage")):
                continue
            loc = f.locate()
            if loc.exists() and loc.stat().st_size < 500_000:
                text = loc.read_text("utf-8", errors="ignore")
                for heading, body in split_into_chunks(text):
                    package_record["chunks"].append(
                        Chunk(
                            text=body,
                            metadata={
                                ChunkFilterField.PACKAGE.value: name,
                                ChunkFilterField.TITLE.value: heading,
                                ChunkFilterField.ORIGIN.value: "doc",
                            },
                        )
                    )
    except Exception as e:
        log.debug("Failed to read doc files for %s: %s", name, e)


def _package_from_record(package_record: dict) -> Package:
    return Package(
        name=package_record["name"],
        version=package_record["version"],
        summary=package_record["summary"],
        homepage=package_record["homepage"],
        dependencies=package_record["requires"],
        content_hash=package_record["hash"],
        origin=PackageOrigin.DEPENDENCY,
    )


def _find_installed_distribution(dep_name: str):
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


def _extract_dependency_record(
    dep_name: str, use_inspect: bool = False, depth: int = 1,
) -> _DependencyExtractionRecord | None:
    """Extract the full record for ``dep_name``, or ``None`` if not installed.

    Shared by :func:`extract_dependency_chunks` and
    :func:`extract_dependency_members` via ``_dependency_cache``.
    """
    cache_key = (normalize_package_name(dep_name), use_inspect, depth)
    cached = _dependency_cache.get(cache_key)
    if cached is not None:
        return cached

    dist = _find_installed_distribution(dep_name)
    if dist is None:
        return None

    collector = _extract_by_import if use_inspect else _extract_from_static_sources
    package_record = collector(dist, depth)
    record = _DependencyExtractionRecord(
        chunks=tuple(package_record["chunks"]),
        members=tuple(package_record["symbols"]),
        package=_package_from_record(package_record),
    )
    _dependency_cache[cache_key] = record
    return record


async def extract_dependency_chunks(
    dep_name: str, use_inspect: bool = False, depth: int = 1,
) -> tuple[tuple[Chunk, ...], Package]:
    """Return (chunks, package) for an installed dependency.

    Raises :class:`LookupError` when the dep is declared but not installed —
    callers (typically :class:`IndexProjectService`) translate that into a
    failed-counter bump so one missing dep does not abort the pass.
    """
    record = await asyncio.to_thread(
        _extract_dependency_record, dep_name, use_inspect, depth,
    )
    if record is None:
        raise LookupError(f"dependency {dep_name!r} is not installed")
    return record.chunks, record.package


async def extract_dependency_members(
    dep_name: str, use_inspect: bool = False, depth: int = 1,
) -> tuple[ModuleMember, ...]:
    """Return the module-member tuple for an installed dependency.

    Raises :class:`LookupError` when the dep is declared but not installed —
    matches :func:`extract_dependency_chunks` so callers can catch either
    function's failure the same way.
    """
    record = await asyncio.to_thread(
        _extract_dependency_record, dep_name, use_inspect, depth,
    )
    if record is None:
        raise LookupError(f"dependency {dep_name!r} is not installed")
    return record.members


# ── Static mode: read .py files, no imports ───────────────────────────────

def _extract_from_static_sources(dist, depth: int) -> dict:
    """Read .py files from site-packages, parse with regex. No imports."""
    name = dist.metadata["Name"].lower().replace("-", "_")
    version = dist.metadata["Version"] or "?"
    package_record = _build_package_record(dist, name, version)
    _append_doc_file_chunks(dist, name, package_record)

    py_files = list_dependency_source_files(dist)
    if py_files:
        root = find_site_packages_root(py_files[0])
        chunks, module_members = _extract_from_source_files(name, py_files, root, "dep")
        package_record["chunks"].extend(chunks)
        package_record["symbols"] = module_members

    return package_record


def list_dependency_source_files(dist) -> list[str]:
    """Find all .py files installed by a distribution."""
    result = []
    try:
        for f in (dist.files or []):
            fname = str(f)
            if fname.endswith(".py") and "setup.py" not in fname:
                loc = f.locate()
                if loc.exists() and loc.stat().st_size < 500_000:
                    result.append(str(loc))
    except Exception:
        pass
    return result


def find_site_packages_root(any_file: str) -> str:
    """Walk up to find site-packages directory."""
    for parent in Path(any_file).parents:
        if parent.name in ("site-packages", "dist-packages"):
            return str(parent)
    return str(Path(any_file).parent.parent)


# ── Inspect mode: import and use inspect ──────────────────────────────────

def _extract_by_import(dist, depth: int) -> dict:
    """Import module, extract API via inspect.getmembers."""
    name = dist.metadata["Name"].lower().replace("-", "_")
    version = dist.metadata["Version"] or "?"
    package_record = _build_package_record(dist, name, version)
    _append_doc_file_chunks(dist, name, package_record)

    if name not in SKIP_IMPORT:
        iname = IMPORT_ALIASES.get(name, name)
        try:
            module = importlib.import_module(iname)
            if module.__doc__ and len(module.__doc__.strip()) > 30:
                package_record["chunks"].append(
                    Chunk(
                        text=module.__doc__.strip()[:MODULE_DOCSTRING_MAX],
                        metadata={
                            ChunkFilterField.PACKAGE.value: name,
                            ChunkFilterField.TITLE.value: name,
                            ChunkFilterField.ORIGIN.value: "docstring",
                        },
                    )
                )
            package_record["symbols"] = _extract_members_by_import(module, iname, name, max_depth=depth)
        except Exception as e:
            log.debug("Failed to import %s: %s", iname, e)

    return package_record


def _extract_callable_signature(obj) -> tuple[str, str, list[Parameter]]:
    try:
        sig = inspect.signature(obj)
    except (ValueError, TypeError):
        return "", "", []
    ret = ""
    if sig.return_annotation != inspect.Parameter.empty:
        try:
            ret = getattr(sig.return_annotation, "__name__", str(sig.return_annotation))
        except Exception:
            pass
    params: list[Parameter] = []
    for pn, p in sig.parameters.items():
        if pn in ("self", "cls"):
            continue
        annotation = ""
        if p.annotation != inspect.Parameter.empty:
            try:
                annotation = getattr(p.annotation, "__name__", str(p.annotation))
            except Exception:
                pass
        default_value = ""
        if p.default != inspect.Parameter.empty:
            try:
                default_value = repr(p.default)[:PARAM_DEFAULT_MAX]
            except Exception:
                pass
        params.append(Parameter(name=pn, annotation=annotation, default=default_value))
    return str(sig)[:SIGNATURE_MAX], ret[:RETURN_TYPE_MAX], params


def _parameters_payload_size(parameters: list[Parameter]) -> int:
    serialised = json.dumps(
        [{"name": p.name, "annotation": p.annotation, "default": p.default} for p in parameters]
    )
    return len(serialised)


def _truncate_parameters(parameters: list[Parameter]) -> tuple[Parameter, ...]:
    """Drop trailing Parameters until the JSON payload fits PARAMS_JSON_MAX bytes.

    Keeps the contract the old implementation had when it stored a trimmed JSON
    string: mild truncation under huge signatures, no crash.
    """
    trimmed = list(parameters)
    while trimmed and _parameters_payload_size(trimmed) > PARAMS_JSON_MAX:
        trimmed.pop()
    return tuple(trimmed)


def _extract_members_by_import(module, mod_name, owner, depth=0, max_depth=1) -> list[ModuleMember]:
    rows: list[ModuleMember] = []
    root = owner.replace("-", "_")
    try:
        members = inspect.getmembers(module)
    except Exception:
        return rows

    for name, obj in members:
        if name.startswith("_"):
            continue
        obj_mod = getattr(obj, "__module__", "") or ""
        if obj_mod and not obj_mod.startswith(root):
            continue
        try:
            if inspect.isfunction(obj) or inspect.isbuiltin(obj):
                sig, ret, params = _extract_callable_signature(obj)
                doc = (inspect.getdoc(obj) or "")[:FUNC_DOCSTRING_MAX]
                rows.append(
                    ModuleMember(
                        metadata={
                            ModuleMemberFilterField.PACKAGE.value: owner,
                            ModuleMemberFilterField.MODULE.value: mod_name,
                            ModuleMemberFilterField.NAME.value: name,
                            ModuleMemberFilterField.KIND.value: "function",
                            "signature": sig,
                            "return_annotation": ret,
                            "parameters": _truncate_parameters(params),
                            "docstring": doc,
                        }
                    )
                )
            elif inspect.isclass(obj):
                sig, _, params = _extract_callable_signature(obj)
                doc = (inspect.getdoc(obj) or "")[:CLASS_DOCSTRING_MAX]
                method_summaries = []
                try:
                    for mn, member in inspect.getmembers(obj):
                        if mn.startswith("_") and mn != "__init__": continue
                        if not (inspect.isfunction(member) or inspect.ismethod(member)): continue
                        s, _, _ = _extract_callable_signature(member)
                        md = (inspect.getdoc(member) or "").split("\n")[0][:METHOD_SUMMARY_MAX]
                        method_summaries.append(f"  .{mn}{s} -- {md}")
                        if len(method_summaries) >= CLASS_METHODS_MAX: break
                except Exception:
                    pass
                if method_summaries:
                    doc += "\n\nMethods:\n" + "\n".join(method_summaries)
                rows.append(
                    ModuleMember(
                        metadata={
                            ModuleMemberFilterField.PACKAGE.value: owner,
                            ModuleMemberFilterField.MODULE.value: mod_name,
                            ModuleMemberFilterField.NAME.value: name,
                            ModuleMemberFilterField.KIND.value: "class",
                            "signature": sig,
                            "return_annotation": "",
                            "parameters": _truncate_parameters(params),
                            "docstring": doc[:CLASS_FULL_DOC_MAX],
                        }
                    )
                )
        except Exception:
            continue
        if len(rows) > 120:
            break

    if depth < max_depth and hasattr(module, "__path__"):
        try:
            for _, sn, _ in pkgutil.iter_modules(module.__path__):
                if sn.startswith("_"): continue
                try:
                    sub = importlib.import_module(f"{mod_name}.{sn}")
                    rows.extend(_extract_members_by_import(sub, f"{mod_name}.{sn}", owner, depth+1, max_depth))
                except Exception:
                    pass
        except Exception:
            pass
    return rows
