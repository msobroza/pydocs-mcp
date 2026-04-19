"""Indexing logic: project source + installed deps.

Two modes for deps:
  --no-inspect (static): reads .py from site-packages, same parser as project
  default (inspect): imports modules, uses inspect.getmembers

Static mode is faster, safer (no side-effects), and fully parallelizable.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import inspect
import json
import logging
import os
import pkgutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from pydocs_mcp.db import get_stored_content_hash, remove_package
from pydocs_mcp.deps import normalize_package_name

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
) -> tuple[list[tuple], list[tuple]]:
    """Parse .py files with Rust/regex parser. No imports needed.

    Used for both project source and deps in static mode.
    Returns (chunks, module_members) ready for executemany.
    """
    file_contents = read_files_parallel(py_paths)
    chunks: list[tuple] = []
    module_members: list[tuple] = []

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
            chunks.append((package_name, module, doc[:MODULE_DOCSTRING_MAX], f"{kind_prefix}_doc"))

        for symbol in parse_py_file(source):
            module_members.append((
                package_name, module, symbol.name, symbol.kind,
                symbol.signature, "", "[]", symbol.docstring,
            ))

        for heading, body in split_into_chunks(source):
            chunks.append((package_name, f"{module}:{heading}", body, f"{kind_prefix}_code"))

    return chunks, module_members


# ── Project source ────────────────────────────────────────────────────────

def index_project_source(connection: sqlite3.Connection, root: Path):
    """Index project .py files using Rust parser (or Python fallback)."""
    package_name = "__project__"
    py_paths = walk_py_files(str(root))
    new_hash = hash_files(py_paths)

    if get_stored_content_hash(connection, package_name) == new_hash:
        log.info("Project: no changes (cached)")
        return

    remove_package(connection, package_name)
    connection.execute(
        "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
        (package_name, "local", f"Project: {root.name}", "", "[]", new_hash, "project"),
    )

    chunks, module_members = _extract_from_source_files(package_name, py_paths, str(root))

    connection.executemany(
        "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)", chunks,
    )
    connection.executemany(
        "INSERT INTO module_members(package,module,name,kind,signature,return_annotation,parameters,docstring) "
        "VALUES(?,?,?,?,?,?,?,?)", module_members,
    )
    connection.commit()
    log.info("Project: %d files -> %d chunks, %d symbols",
             len(py_paths), len(chunks), len(module_members))


# ── Dependency indexing ───────────────────────────────────────────────────

def index_dependencies(
    connection: sqlite3.Connection,
    dep_names: list[str],
    depth: int = 1,
    workers: int = 4,
    use_inspect: bool = True,
) -> dict:
    """Index installed dependencies.

    Args:
        use_inspect: True = import + inspect (richer but slower).
                     False = read .py files statically (faster, safer).
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
        if get_stored_content_hash(connection, package_name) == h:
            stats["cached"] += 1
        else:
            packages_to_index.append(dist)

    w = min(workers, len(packages_to_index)) if packages_to_index else 1
    mode = "inspect" if use_inspect else "static"
    log.info("Deps: %d to index, %d cached (%d workers, mode=%s)",
             len(packages_to_index), stats["cached"], w, mode)

    collector = _extract_by_import if use_inspect else _extract_from_static_sources

    with ThreadPoolExecutor(max_workers=w) as pool:
        futures = {pool.submit(collector, d, depth): d for d in packages_to_index}
        for fut in as_completed(futures):
            dist = futures[fut]
            package_name = dist.metadata["Name"].lower().replace("-", "_")
            try:
                package_record = fut.result()
                _persist_dependency(connection, package_record)
                stats["indexed"] += 1
                log.info("  ok %s %s (%d chunks, %d syms)",
                         package_record["name"], package_record["version"],
                         len(package_record["chunks"]), len(package_record["symbols"]))
            except Exception as e:
                stats["failed"] += 1
                log.warning("  fail %s: %s", package_name, e)

    return stats


# ── Shared dep helpers ────────────────────────────────────────────────────

def _build_package_record(dist, name: str, version: str) -> dict:
    package_record = {
        "name": name, "version": version,
        "hash": hashlib.md5(f"{name}:{version}".encode()).hexdigest()[:12],
        "summary": dist.metadata["Summary"] or "",
        "homepage": dist.metadata["Home-page"] or "",
        "requires": json.dumps(
            [r.split(";")[0].strip() for r in (dist.requires or [])[:REQUIREMENTS_PARSE_MAX]]
        ),
        "chunks": [], "symbols": [],
    }
    payload = dist.metadata.get_payload()
    if isinstance(payload, str) and len(payload.strip()) > 50:
        for h, b in split_into_chunks(payload.strip()):
            package_record["chunks"].append((name, h, b, "readme"))
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
                for h, b in split_into_chunks(text):
                    package_record["chunks"].append((name, h, b, "doc"))
    except Exception as e:
        log.debug("Failed to read doc files for %s: %s", name, e)


def _persist_dependency(connection: sqlite3.Connection, package_record: dict):
    remove_package(connection, package_record["name"])
    connection.execute(
        "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
        (package_record["name"], package_record["version"], package_record["summary"],
         package_record["homepage"], package_record["requires"], package_record["hash"], "dependency"),
    )
    if package_record["chunks"]:
        connection.executemany(
            "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)",
            package_record["chunks"],
        )
    if package_record["symbols"]:
        connection.executemany(
            "INSERT INTO module_members(package,module,name,kind,signature,return_annotation,parameters,docstring) "
            "VALUES(?,?,?,?,?,?,?,?)",
            package_record["symbols"],
        )
    connection.commit()


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
                package_record["chunks"].append((name, name, module.__doc__.strip()[:MODULE_DOCSTRING_MAX], "docstring"))
            package_record["symbols"] = _extract_members_by_import(module, iname, name, max_depth=depth)
        except Exception as e:
            log.debug("Failed to import %s: %s", iname, e)

    return package_record


def _extract_callable_signature(obj) -> tuple[str, str, list[dict]]:
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
    params = []
    for pn, p in sig.parameters.items():
        if pn in ("self", "cls"):
            continue
        entry: dict = {"name": pn}
        if p.annotation != inspect.Parameter.empty:
            try:
                entry["type"] = getattr(p.annotation, "__name__", str(p.annotation))
            except Exception:
                pass
        if p.default != inspect.Parameter.empty:
            try:
                entry["default"] = repr(p.default)[:PARAM_DEFAULT_MAX]
            except Exception:
                pass
        params.append(entry)
    return str(sig)[:SIGNATURE_MAX], ret[:RETURN_TYPE_MAX], params


def _extract_members_by_import(module, mod_name, owner, depth=0, max_depth=1) -> list[tuple]:
    rows, root = [], owner.replace("-", "_")
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
                rows.append((owner, mod_name, name, "function",
                             sig, ret, json.dumps(params)[:PARAMS_JSON_MAX], doc))
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
                rows.append((owner, mod_name, name, "class",
                             sig, "", json.dumps(params)[:PARAMS_JSON_MAX], doc[:CLASS_FULL_DOC_MAX]))
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
