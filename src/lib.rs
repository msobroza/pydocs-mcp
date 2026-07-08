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

/// Truncate a UTF-8 string to at most `max_chars` *characters* (not bytes).
///
/// BUG FIX: the three limits above (DOCSTRING_LOOKAHEAD / FUNC_DOCSTRING_MAX /
/// MODULE_DOCSTRING_MAX) are documented and named as char counts, and the
/// Python fallback (_fallback.py) slices with `[:N]`, which is char-based.
/// An earlier version of this function truncated by *byte* count instead,
/// so multi-byte docstrings (CJK, emoji, accents) were cut ~3x shorter than
/// their Python-fallback counterpart, and a docstring closing within the
/// char-based DOCSTRING_LOOKAHEAD window but beyond the byte-based one was
/// silently dropped entirely (DOC_RE never saw the closing quotes). This
/// made `chunks.content_hash` diverge by which engine indexed the package.
fn safe_truncate(s: &str, max_chars: usize) -> &str {
    match s.char_indices().nth(max_chars) {
        Some((byte_idx, _)) => &s[..byte_idx],
        None => s,
    }
}

// ── Static regexes (compiled once) ──────────────────────────────────────

// Paren group is optional: paren-less `class Config:` is the idiomatic, most
// common class form in modern Python (no base classes). `def`/`async def`
// always carry parens in valid syntax, so making the group optional here
// cannot introduce false positives for functions. Mirrors _fallback.py.
static DEF_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?m)^(async\s+def|def|class)\s+([A-Za-z_]\w*)\s*(?:\(([^)]*)\))?\s*(?:->[\s\w\[\],.|]*)?:"#,
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
        // Group 3 (params) is absent for paren-less classes (`class Config:`).
        let signature = format!("({})", cap.get(3).map_or("", |m| m.as_str()).trim());

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

/// Read a file and return its contents. Returns empty string on error
/// (e.g. missing file). Invalid UTF-8 bytes are replaced lossily rather
/// than causing the whole file to read as empty — this must stay in sync
/// with `_fallback.read_file`'s `errors="replace"` behavior (see
/// CLAUDE.md "Fallback contract": same inputs must produce same outputs).
/// A single mis-encoded byte (e.g. a latin-1 comment in an older PyPI
/// package) must not silently drop the file from the index.
/// This is faster than Python's open().read() for batch operations
/// because it avoids Python's IO overhead.
#[pyfunction]
fn read_file(path: &str) -> String {
    fs::read(path)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .unwrap_or_default()
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
                let content = fs::read(p)
                    .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
                    .unwrap_or_default();
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

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // safe_truncate must count *characters*, not bytes, to match the
    // char-based slicing in the Python fallback (_fallback.py).

    #[test]
    fn safe_truncate_counts_chars_not_bytes_for_cjk() {
        let s = "中".repeat(10); // 10 chars, 30 bytes in UTF-8
        let truncated = safe_truncate(&s, 5);
        assert_eq!(truncated.chars().count(), 5);
        assert_eq!(truncated, "中".repeat(5));
    }

    #[test]
    fn safe_truncate_is_noop_when_under_limit() {
        let s = "中".repeat(3);
        assert_eq!(safe_truncate(&s, 10), s);
    }

    #[test]
    fn safe_truncate_ascii_unchanged_behavior() {
        let s = "x".repeat(20);
        assert_eq!(safe_truncate(&s, 5), "xxxxx");
    }

    #[test]
    fn module_docstring_truncates_by_char_count_for_cjk() {
        // 5500 CJK chars => 16500 bytes; a byte-based truncation to
        // MODULE_DOCSTRING_MAX (5000) would keep far fewer than 5000 chars.
        let cjk_doc = "中".repeat(5500);
        let src = format!("\"\"\"{cjk_doc}\"\"\"\n");
        let doc = extract_module_doc(&src);
        assert_eq!(doc.chars().count(), MODULE_DOCSTRING_MAX);
        assert_eq!(
            doc,
            cjk_doc
                .chars()
                .take(MODULE_DOCSTRING_MAX)
                .collect::<String>()
        );
    }

    #[test]
    fn func_docstring_closing_within_char_window_but_past_byte_window_is_found() {
        // 200 CJK chars = 600 bytes: beyond a byte-based 500-byte lookahead
        // window, but well within the char-based 500-char lookahead window
        // that the Python fallback uses. Regression for the gap where Rust
        // silently returned "" here while Python extracted the full doc.
        let cjk_doc = "あ".repeat(200);
        assert!(cjk_doc.chars().count() < DOCSTRING_LOOKAHEAD);
        assert!(cjk_doc.len() > DOCSTRING_LOOKAHEAD);

        let src = format!("def foo(x):\n    \"\"\"{cjk_doc}\"\"\"\n    pass\n");
        let members = parse_py_file(&src);
        assert_eq!(members.len(), 1);
        assert_eq!(members[0].docstring, cjk_doc);
    }

    use std::sync::atomic::{AtomicU64, Ordering};

    /// Unique path under the OS temp dir — avoids pulling in a `tempfile`
    /// dev-dependency for a single-file read test.
    fn unique_temp_path(label: &str) -> std::path::PathBuf {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!(
            "pydocs_mcp_native_test_{}_{}_{}.py",
            std::process::id(),
            n,
            label
        ))
    }

    // Regression: a single invalid UTF-8 byte (e.g. a latin-1 "café"
    // comment, common in older PyPI packages) must not vanish the whole
    // file. `read_file` previously used `fs::read_to_string(..)
    // .unwrap_or_default()`, which discards ALL content on the first
    // invalid byte — silently dropping the file from the index on native
    // builds while the pure-Python fallback still indexed it. `read_file`
    // now reads raw bytes and lossily converts, matching
    // `_fallback.read_file`'s errors="replace" semantics byte-for-byte.
    #[test]
    fn read_file_lossily_decodes_invalid_utf8_instead_of_returning_empty() {
        let path = unique_temp_path("invalid_utf8");
        fs::write(&path, b"x = 1  # caf\xe9\n").expect("write temp file");

        let content = read_file(path.to_str().expect("utf8 path"));
        let _ = fs::remove_file(&path);

        assert_eq!(content, "x = 1  # caf\u{FFFD}\n");
        assert!(!content.is_empty());
    }

    #[test]
    fn read_file_missing_file_returns_empty_string() {
        assert_eq!(read_file("/nonexistent/path/does-not-exist.py"), "");
    }
}
