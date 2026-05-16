// src/lib.rs
//
// Rust-accelerated functions for pydocs-mcp.
//
// Exported functions:
//   - walk_py_files       — find all .py files, skipping venvs etc.
//   - hash_files          — hash file paths + mtimes (detects changes)
//   - parse_py_file       — extract functions/classes from Python source
//   - extract_module_doc  — pull the module-level docstring
//   - read_file           — read a single file
//   - read_files_parallel — read many files in parallel via rayon

use pyo3::prelude::*;
use rayon::prelude::*;
use regex::Regex;
use std::fs;
use std::path::Path;
use std::sync::LazyLock;
use walkdir::WalkDir;
use xxhash_rust::xxh3::xxh3_64;

// ---------------------------------------------------------------------------
// Truncation limits — MUST stay synchronized with python/pydocs_mcp/constants.py
// ---------------------------------------------------------------------------
/// Max chars inspected after a def/class line to find a docstring.
const DOCSTRING_LOOKAHEAD: usize = 500;
/// Max chars stored for a single function or method docstring.
const FUNC_DOCSTRING_MAX: usize = 3000;
/// Max chars stored for a module-level docstring.
const MODULE_DOCSTRING_MAX: usize = 5000;

/// Truncate a UTF-8 string to at most `max_bytes` bytes without
/// splitting a multi-byte character.
fn safe_truncate(s: &str, max_bytes: usize) -> &str {
    if s.len() <= max_bytes {
        return s;
    }
    let mut end = max_bytes;
    while end > 0 && !s.is_char_boundary(end) {
        end -= 1;
    }
    &s[..end]
}

// ── Static regexes (compiled once) ──────────────────────────────────────

static DEF_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?m)^(async\s+def|def|class)\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:->[\s\w\[\],.|]*)?:"#,
    )
    .unwrap()
});

static DOC_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"(?s)^(?:"""(.*?)"""|'''(.*?)''')"#).unwrap());

static MOD_DOC_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"(?s)^(?:"""(.*?)"""|'''(.*?)''')"#).unwrap());

// ── 1. File Walker ───────────────────────────────────────────────────────
//
// Walks a directory tree and returns all .py file paths.
// Skips common directories like .git, __pycache__, .venv, etc.
// About 10x faster than Python's pathlib.rglob("*.py").

/// Directories we never want to enter.
const SKIP_DIRS: &[&str] = &[
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".tox",
    ".eggs",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    ".nox",
    "egg-info",
];

/// Walk `root` and return all .py file paths as sorted strings.
///
/// Releases the GIL during directory traversal so Python threads can run
/// concurrently with the filesystem I/O.
#[pyfunction]
fn walk_py_files(py: Python<'_>, root: &str) -> Vec<String> {
    let root = root.to_owned();
    py.allow_threads(move || {
        let root_path = Path::new(&root);

        let mut result: Vec<String> = WalkDir::new(root_path)
            .into_iter()
            // Skip excluded directories early (before reading their contents).
            .filter_entry(|entry| {
                if entry.file_type().is_dir() {
                    let name = entry.file_name().to_string_lossy();
                    return !SKIP_DIRS.contains(&name.as_ref());
                }
                true
            })
            // Keep only .py files.
            .filter_map(|entry| {
                let entry = entry.ok()?;
                if entry.file_type().is_file() {
                    let path = entry.path();
                    if path.extension().and_then(|e| e.to_str()) == Some("py") {
                        return Some(path.to_string_lossy().into_owned());
                    }
                }
                None
            })
            .collect();
        result.sort();
        result
    })
}

// ── 2. File Hasher ───────────────────────────────────────────────────────
//
// Hashes a list of file paths + their modification times.
// Returns a hex string. If any file changed, the hash changes.
// Uses xxh3 which is ~3x faster than MD5.

/// Compute a single hash from file paths + mtimes.
/// Useful to detect if any source file was added, removed, or modified.
///
/// Releases the GIL during filesystem metadata reads.
#[pyfunction]
fn hash_files(py: Python<'_>, paths: Vec<String>) -> String {
    py.allow_threads(move || {
        // Build a single byte buffer with all path + mtime data.
        let mut data = Vec::with_capacity(paths.len() * 64);

        for path_str in &paths {
            data.extend_from_slice(path_str.as_bytes());
            data.push(b':');

            // Read the modification time (if possible).
            if let Ok(meta) = fs::metadata(path_str) {
                if let Ok(mtime) = meta.modified() {
                    if let Ok(duration) = mtime.duration_since(std::time::UNIX_EPOCH) {
                        data.extend_from_slice(&duration.as_nanos().to_le_bytes());
                    }
                }
            }
            data.push(b'\n');
        }

        // Hash everything at once with xxh3.
        let hash = xxh3_64(&data);
        format!("{:016x}", hash)
    })
}

// ── 3. Python Source Parser ──────────────────────────────────────────────
//
// Extracts function/class definitions from Python source code using regex.
// This is NOT a full AST parser, but it's ~5x faster than Python's ast.parse
// and works even on files with syntax errors.
//
// For each function/class, we extract:
//   - name, kind (def/async def/class), signature, docstring

/// One extracted Python API member (function or class).
#[pyclass]
#[derive(Clone)]
struct ParsedMember {
    #[pyo3(get)]
    name: String,
    #[pyo3(get)]
    kind: String, // "def", "async def", or "class" — converted to MemberKind enum at the Python indexer boundary (sub-PR #1 Task 15)
    #[pyo3(get)]
    signature: String, // everything between parentheses
    #[pyo3(get)]
    docstring: String, // first triple-quoted string after definition
}

/// Extract top-level functions and classes from Python source code.
///
/// This uses regex, not a full parser, so it's fast and fault-tolerant.
/// Only extracts top-level definitions (no indentation before def/class).
///
/// Returns a list of ParsedMember objects.
#[pyfunction]
fn parse_py_file(source: &str) -> Vec<ParsedMember> {
    let mut members = Vec::new();

    for cap in DEF_RE.captures_iter(source) {
        let kind = cap[1].to_string();
        let name = cap[2].to_string();
        let signature = format!("({})", cap[3].trim());

        // Skip private names.
        if name.starts_with('_') {
            continue;
        }

        // Find the docstring: look only at the first ~500 chars after the definition.
        let match_end = cap.get(0).unwrap().end();
        let rest_full = &source[match_end..];
        let rest = safe_truncate(rest_full, DOCSTRING_LOOKAHEAD);

        let docstring = DOC_RE
            .captures(rest.trim_start())
            .and_then(|dc| {
                // Get whichever group matched (""" or ''').
                dc.get(1).or_else(|| dc.get(2))
            })
            .map(|m| {
                let s = m.as_str().trim();
                safe_truncate(s, FUNC_DOCSTRING_MAX)
            })
            .unwrap_or("")
            .to_string();

        members.push(ParsedMember {
            name,
            kind,
            signature,
            docstring,
        });
    }

    members
}

/// Extract the module-level docstring from Python source.
///
/// Returns the docstring or an empty string if none found.
#[pyfunction]
fn extract_module_doc(source: &str) -> String {
    let trimmed = source.trim_start();

    MOD_DOC_RE
        .captures(trimmed)
        .and_then(|cap| cap.get(1).or_else(|| cap.get(2)))
        .map(|m| {
            let s = m.as_str().trim();
            safe_truncate(s, MODULE_DOCSTRING_MAX).to_string()
        })
        .unwrap_or_default()
}

/// Read a file and return its contents. Returns empty string on error.
/// This is faster than Python's open().read() for batch operations
/// because it avoids Python's IO overhead.
#[pyfunction]
fn read_file(path: &str) -> String {
    fs::read_to_string(path).unwrap_or_default()
}

/// Read multiple files in parallel using Rayon.
/// Returns a list of (path, content) tuples.
///
/// Releases the GIL so rayon's thread pool can read files in true parallel
/// without contending for Python's global lock.
#[pyfunction]
fn read_files_parallel(py: Python<'_>, paths: Vec<String>) -> Vec<(String, String)> {
    py.allow_threads(move || {
        paths
            .par_iter()
            .map(|p| {
                let content = fs::read_to_string(p).unwrap_or_default();
                (p.clone(), content)
            })
            .collect()
    })
}

// ── Module Registration ──────────────────────────────────────────────────

/// Register all functions so Python can import them.
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(walk_py_files, m)?)?;
    m.add_function(wrap_pyfunction!(hash_files, m)?)?;
    m.add_function(wrap_pyfunction!(parse_py_file, m)?)?;
    m.add_function(wrap_pyfunction!(extract_module_doc, m)?)?;
    m.add_function(wrap_pyfunction!(read_file, m)?)?;
    m.add_function(wrap_pyfunction!(read_files_parallel, m)?)?;
    m.add_class::<ParsedMember>()?;
    Ok(())
}
