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
from pydocs_mcp.deps import normalize

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

def _parse_source_files(
    pkg: str,
    py_paths: list[str],
    root: str,
    kind_prefix: str = "project",
) -> tuple[list[tuple], list[tuple]]:
    """Parse .py files with Rust/regex parser. No imports needed.

    Used for both project source and deps in static mode.
    Returns (chunk_rows, sym_rows) ready for executemany.
    """
    file_contents = read_files_parallel(py_paths)
    chunk_rows: list[tuple] = []
    sym_rows: list[tuple] = []

    for filepath, source in file_contents:
        if not source:
            continue

        try:
            rel = os.path.relpath(filepath, root)
        except ValueError:
            continue
        module = rel.replace(os.sep, ".").removesuffix(".py").replace(".__init__", "")

        doc = extract_module_doc(source)
        if len(doc) > 20:
            chunk_rows.append((pkg, module, doc[:MODULE_DOCSTRING_MAX], f"{kind_prefix}_doc"))

        for sym in parse_py_file(source):
            sym_rows.append((
                pkg, module, sym.name, sym.kind,
                sym.signature, "", "[]", sym.docstring,
            ))

        for heading, body in split_into_chunks(source):
            chunk_rows.append((pkg, f"{module}:{heading}", body, f"{kind_prefix}_code"))

    return chunk_rows, sym_rows


# ── Project source ────────────────────────────────────────────────────────

def index_project(conn: sqlite3.Connection, root: Path):
    """Index project .py files using Rust parser (or Python fallback)."""
    pkg = "__project__"
    py_paths = walk_py_files(str(root))
    new_hash = hash_files(py_paths)

    if get_stored_content_hash(conn, pkg) == new_hash:
        log.info("Project: no changes (cached)")
        return

    remove_package(conn, pkg)
    conn.execute(
        "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
        (pkg, "local", f"Project: {root.name}", "", "[]", new_hash, "project"),
    )

    chunk_rows, sym_rows = _parse_source_files(pkg, py_paths, str(root))

    conn.executemany(
        "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)", chunk_rows,
    )
    conn.executemany(
        "INSERT INTO module_members(package,module,name,kind,signature,return_annotation,parameters,docstring) "
        "VALUES(?,?,?,?,?,?,?,?)", sym_rows,
    )
    conn.commit()
    log.info("Project: %d files -> %d chunks, %d symbols",
             len(py_paths), len(chunk_rows), len(sym_rows))


# ── Dependency indexing ───────────────────────────────────────────────────

def index_deps(
    conn: sqlite3.Connection,
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
    lookup = {normalize(n) for n in dep_names}

    dists, seen = [], set()
    for dist in importlib.metadata.distributions():
        raw = dist.metadata["Name"]
        if not raw:
            continue
        n = raw.lower().replace("-", "_")
        if n in seen or n not in lookup:
            continue
        seen.add(n)
        dists.append(dist)

    work = []
    for dist in dists:
        n = dist.metadata["Name"].lower().replace("-", "_")
        v = dist.metadata["Version"] or "?"
        h = hashlib.md5(f"{n}:{v}".encode()).hexdigest()[:12]
        if get_stored_content_hash(conn, n) == h:
            stats["cached"] += 1
        else:
            work.append(dist)

    w = min(workers, len(work)) if work else 1
    mode = "inspect" if use_inspect else "static"
    log.info("Deps: %d to index, %d cached (%d workers, mode=%s)",
             len(work), stats["cached"], w, mode)

    collector = _collect_inspect if use_inspect else _collect_static

    with ThreadPoolExecutor(max_workers=w) as pool:
        futures = {pool.submit(collector, d, depth): d for d in work}
        for fut in as_completed(futures):
            dist = futures[fut]
            n = dist.metadata["Name"].lower().replace("-", "_")
            try:
                data = fut.result()
                _write_dep(conn, data)
                stats["indexed"] += 1
                log.info("  ok %s %s (%d chunks, %d syms)",
                         data["name"], data["version"],
                         len(data["chunks"]), len(data["symbols"]))
            except Exception as e:
                stats["failed"] += 1
                log.warning("  fail %s: %s", n, e)

    return stats


# ── Shared dep helpers ────────────────────────────────────────────────────

def _base_data(dist, name: str, version: str) -> dict:
    data = {
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
            data["chunks"].append((name, h, b, "readme"))
    return data


def _add_doc_files(dist, name: str, data: dict):
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
                    data["chunks"].append((name, h, b, "doc"))
    except Exception as e:
        log.debug("Failed to read doc files for %s: %s", name, e)


def _write_dep(conn: sqlite3.Connection, data: dict):
    remove_package(conn, data["name"])
    conn.execute(
        "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
        (data["name"], data["version"], data["summary"],
         data["homepage"], data["requires"], data["hash"], "dependency"),
    )
    if data["chunks"]:
        conn.executemany(
            "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)",
            data["chunks"],
        )
    if data["symbols"]:
        conn.executemany(
            "INSERT INTO module_members(package,module,name,kind,signature,return_annotation,parameters,docstring) "
            "VALUES(?,?,?,?,?,?,?,?)",
            data["symbols"],
        )
    conn.commit()


# ── Static mode: read .py files, no imports ───────────────────────────────

def _collect_static(dist, depth: int) -> dict:
    """Read .py files from site-packages, parse with regex. No imports."""
    name = dist.metadata["Name"].lower().replace("-", "_")
    version = dist.metadata["Version"] or "?"
    data = _base_data(dist, name, version)
    _add_doc_files(dist, name, data)

    py_files = _dep_py_files(dist)
    if py_files:
        root = _site_packages_root(py_files[0])
        chunk_rows, sym_rows = _parse_source_files(name, py_files, root, "dep")
        data["chunks"].extend(chunk_rows)
        data["symbols"] = sym_rows

    return data


def _dep_py_files(dist) -> list[str]:
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


def _site_packages_root(any_file: str) -> str:
    """Walk up to find site-packages directory."""
    for parent in Path(any_file).parents:
        if parent.name in ("site-packages", "dist-packages"):
            return str(parent)
    return str(Path(any_file).parent.parent)


# ── Inspect mode: import and use inspect ──────────────────────────────────

def _collect_inspect(dist, depth: int) -> dict:
    """Import module, extract API via inspect.getmembers."""
    name = dist.metadata["Name"].lower().replace("-", "_")
    version = dist.metadata["Version"] or "?"
    data = _base_data(dist, name, version)
    _add_doc_files(dist, name, data)

    if name not in SKIP_IMPORT:
        iname = IMPORT_ALIASES.get(name, name)
        try:
            mod = importlib.import_module(iname)
            if mod.__doc__ and len(mod.__doc__.strip()) > 30:
                data["chunks"].append((name, name, mod.__doc__.strip()[:MODULE_DOCSTRING_MAX], "docstring"))
            data["symbols"] = _inspect_syms(mod, iname, name, max_depth=depth)
        except Exception as e:
            log.debug("Failed to import %s: %s", iname, e)

    return data


def _get_sig(obj) -> tuple[str, str, list[dict]]:
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


def _inspect_syms(mod, mod_name, owner, depth=0, max_depth=1) -> list[tuple]:
    rows, root = [], owner.replace("-", "_")
    try:
        members = inspect.getmembers(mod)
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
                sig, ret, params = _get_sig(obj)
                doc = (inspect.getdoc(obj) or "")[:FUNC_DOCSTRING_MAX]
                rows.append((owner, mod_name, name, "function",
                             sig, ret, json.dumps(params)[:PARAMS_JSON_MAX], doc))
            elif inspect.isclass(obj):
                sig, _, params = _get_sig(obj)
                doc = (inspect.getdoc(obj) or "")[:CLASS_DOCSTRING_MAX]
                ms = []
                try:
                    for mn, mo in inspect.getmembers(obj):
                        if mn.startswith("_") and mn != "__init__": continue
                        if not (inspect.isfunction(mo) or inspect.ismethod(mo)): continue
                        s, _, _ = _get_sig(mo)
                        md = (inspect.getdoc(mo) or "").split("\n")[0][:METHOD_SUMMARY_MAX]
                        ms.append(f"  .{mn}{s} -- {md}")
                        if len(ms) >= CLASS_METHODS_MAX: break
                except Exception:
                    pass
                if ms:
                    doc += "\n\nMethods:\n" + "\n".join(ms)
                rows.append((owner, mod_name, name, "class",
                             sig, "", json.dumps(params)[:PARAMS_JSON_MAX], doc[:CLASS_FULL_DOC_MAX]))
        except Exception:
            continue
        if len(rows) > 120:
            break

    if depth < max_depth and hasattr(mod, "__path__"):
        try:
            for _, sn, _ in pkgutil.iter_modules(mod.__path__):
                if sn.startswith("_"): continue
                try:
                    sub = importlib.import_module(f"{mod_name}.{sn}")
                    rows.extend(_inspect_syms(sub, f"{mod_name}.{sn}", owner, depth+1, max_depth))
                except Exception:
                    pass
        except Exception:
            pass
    return rows
