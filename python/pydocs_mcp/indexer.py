"""Indexing logic: project source + installed deps.

Two modes for deps:
  --no-inspect (static): reads .py from site-packages, same parser as project
  default (inspect): imports modules, uses inspect.getmembers

Static mode is faster, safer (no side-effects), and fully parallelizable.

Writes flow through :class:`pydocs_mcp.application.indexing_service.IndexingService`
— the indexer never touches SQLite directly (spec §6.4, AC #19).
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydocs_mcp._fast import (
    extract_module_doc,
    hash_files,
    parse_py_file,
    read_files_parallel,
    split_into_chunks,
    walk_py_files,
)
from pydocs_mcp.application.indexing_service import IndexingService
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

async def _project_already_cached(
    indexing_service: IndexingService, package_name: str, new_hash: str,
) -> bool:
    existing = await indexing_service.package_store.get(package_name)
    return existing is not None and existing.content_hash == new_hash


async def index_project_source(indexing_service: IndexingService, root: Path) -> None:
    """Index project .py files using Rust parser (or Python fallback).

    Async-native: the caller owns the event loop so a single ``asyncio.run``
    in ``__main__`` wraps the whole indexing phase instead of each call
    spinning up a fresh loop (and so the function is usable from a live
    async context in library-embedded scenarios).
    """
    package_name = "__project__"
    py_paths = walk_py_files(str(root))
    new_hash = hash_files(py_paths)

    if await _project_already_cached(indexing_service, package_name, new_hash):
        log.info("Project: no changes (cached)")
        return

    chunks, module_members = _extract_from_source_files(package_name, py_paths, str(root))

    package = Package(
        name=package_name,
        version="local",
        summary=f"Project: {root.name}",
        homepage="",
        dependencies=(),
        content_hash=new_hash,
        origin=PackageOrigin.PROJECT,
    )
    await indexing_service.reindex_package(
        package=package,
        chunks=tuple(chunks),
        module_members=tuple(module_members),
    )
    log.info("Project: %d files -> %d chunks, %d symbols",
             len(py_paths), len(chunks), len(module_members))


# ── Dependency indexing ───────────────────────────────────────────────────

async def index_dependencies(
    indexing_service: IndexingService,
    dep_names: list[str],
    depth: int = 1,
    workers: int = 4,
    use_inspect: bool = True,
) -> dict:
    """Index installed dependencies.

    Args:
        use_inspect: True = import + inspect (richer but slower).
                     False = read .py files statically (faster, safer).

    Async-native: the caller owns the event loop so the CLI can wrap the
    whole indexing phase in a single ``asyncio.run``. CPU-bound collector
    work still runs on a ThreadPoolExecutor via ``loop.run_in_executor``.
    """
    stats = {"indexed": 0, "cached": 0, "failed": 0}
    lookup = {normalize_package_name(n) for n in dep_names}

    installed_distributions, seen = [], set()
    for dist in importlib.metadata.distributions():
        raw = dist.metadata["Name"]
        if not raw:
            continue
        package_name = raw.lower().replace("-", "_")
        if package_name in seen or package_name not in lookup:
            continue
        seen.add(package_name)
        installed_distributions.append(dist)

    packages_to_index = []
    for dist in installed_distributions:
        package_name = dist.metadata["Name"].lower().replace("-", "_")
        v = dist.metadata["Version"] or "?"
        h = hashlib.md5(f"{package_name}:{v}".encode()).hexdigest()[:12]
        existing = await indexing_service.package_store.get(package_name)
        if existing is not None and existing.content_hash == h:
            stats["cached"] += 1
        else:
            packages_to_index.append(dist)

    w = min(workers, len(packages_to_index)) if packages_to_index else 1
    mode = "inspect" if use_inspect else "static"
    log.info("Deps: %d to index, %d cached (%d workers, mode=%s)",
             len(packages_to_index), stats["cached"], w, mode)

    collector = _extract_by_import if use_inspect else _extract_from_static_sources

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=w) as pool:
        # Performance: run collector calls on the pool but await them via
        # ``run_in_executor`` so persist (which needs the event loop) can
        # interleave with still-running collectors.
        async def _index_one(dist) -> tuple[str, bool, dict | None]:
            pn = dist.metadata["Name"].lower().replace("-", "_")
            try:
                package_record = await loop.run_in_executor(pool, collector, dist, depth)
            except Exception as e:
                log.warning("  fail %s: %s", pn, e)
                return pn, False, None
            try:
                await _persist_dependency(indexing_service, package_record)
            except Exception as e:
                log.warning("  fail %s: %s", pn, e)
                return pn, False, None
            return pn, True, package_record

        results = await asyncio.gather(
            *[_index_one(d) for d in packages_to_index],
            return_exceptions=False,
        )
        for _pn, ok, record in results:
            if ok and record is not None:
                stats["indexed"] += 1
                log.info("  ok %s %s (%d chunks, %d syms)",
                         record["name"], record["version"],
                         len(record["chunks"]), len(record["symbols"]))
            else:
                stats["failed"] += 1

    return stats


# ── Shared dep helpers ────────────────────────────────────────────────────

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


async def _persist_dependency(
    indexing_service: IndexingService, package_record: dict,
) -> None:
    package = Package(
        name=package_record["name"],
        version=package_record["version"],
        summary=package_record["summary"],
        homepage=package_record["homepage"],
        dependencies=package_record["requires"],
        content_hash=package_record["hash"],
        origin=PackageOrigin.DEPENDENCY,
    )
    await indexing_service.reindex_package(
        package=package,
        chunks=tuple(package_record["chunks"]),
        module_members=tuple(package_record["symbols"]),
    )


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
