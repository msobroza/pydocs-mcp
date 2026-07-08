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

// Header only: matches up to (and captures) an optional opening '(' but does
// NOT try to capture the whole parenthesized group. A `[^)]*` charclass
// can't span a nested ')' inside a default value (tuple/call/dict literal —
// e.g. `def resize(size=(640, 480)):`, ubiquitous in vision/ML libs), so the
// old combined regex silently failed to match the entire definition whenever
// one appeared. The matching close paren is instead found by a depth-aware
// scan (`scan_matching_paren`), mirroring _fallback.py's parse_py_file.
//
// Paren group is optional: paren-less `class Config:` is the idiomatic, most
// common class form in modern Python (no base classes). `def`/`async def`
// always carry parens in valid syntax, so making the group optional here
// cannot introduce false positives for functions.
static HEADER_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?m)^(async\s+def|def|class)\s+([A-Za-z_]\w*)\s*(\()?"#).unwrap()
});

// Matches what must immediately follow the closing paren (or the name, for a
// paren-less class): an optional `-> ...` return annotation, then `:`. The
// annotation charclass has no quote character, so `-> "Foo":` (a forward
// reference) is tolerated by `.*?` non-greedy matching through to the `:`
// rather than being excluded outright.
static TAIL_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"^\s*(?:->.*?)?:"#).unwrap());

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

/// Core traversal logic, kept free of the PyO3 boundary so it's directly
/// unit-testable (see PyO3 boundary pattern in CLAUDE.md: thin boundary,
/// pure-Rust core).
fn walk_py_files_impl(root_path: &Path) -> Vec<String> {
    // Parity with the Python fallback's os.walk(root): os.walk() yields
    // nothing when root is a file or doesn't exist (it never treats root
    // itself as a discoverable entry). WalkDir, left unchecked, yields the
    // root entry itself when root is a .py file, silently indexing a lone
    // file as if it were a package root. Bail out early so both engines
    // agree at the substitution boundary (fallback contract, CLAUDE.md).
    if !root_path.is_dir() {
        return Vec::new();
    }

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
        // Keep only .py files. WalkDir runs with follow_links(false), so
        // entry.file_type() reports a symlink's OWN type (neither file
        // nor dir) even when it points at a regular file — that silently
        // dropped symlinked .py files (common in editable installs /
        // pyenv shims / nix-store layouts) that the Python fallback's
        // os.walk() includes via `filenames`. Resolve symlinks through
        // fs::metadata (follows the link) so both engines see the same
        // file set.
        .filter_map(|entry| {
            let entry = entry.ok()?;
            let path = entry.path();
            let is_file = if entry.file_type().is_symlink() {
                fs::metadata(path).map(|m| m.is_file()).unwrap_or(false)
            } else {
                entry.file_type().is_file()
            };
            if is_file && path.extension().and_then(|e| e.to_str()) == Some("py") {
                return Some(path.to_string_lossy().into_owned());
            }
            None
        })
        .collect();
    result.sort();
    result
}

/// Walk `root` and return all .py file paths as sorted strings.
///
/// Releases the GIL during directory traversal so Python threads can run
/// concurrently with the filesystem I/O.
#[pyfunction]
fn walk_py_files(py: Python<'_>, root: &str) -> Vec<String> {
    let root = root.to_owned();
    py.allow_threads(move || walk_py_files_impl(Path::new(&root)))
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

/// Bound the paren-balance scan so a pathological unclosed '(' can't walk
/// the rest of a huge source file byte-by-byte.
const PAREN_SCAN_LIMIT: usize = 4000;

/// Return the byte index just past the ')' matching the '(' at `open_idx`.
///
/// Plain depth counting (not a `[^)]*` regex charclass) so a nested ')'
/// inside a default value doesn't terminate the scan early. Mirrors
/// `_fallback._scan_matching_paren`. Returns `None` if unclosed within
/// `PAREN_SCAN_LIMIT` bytes (caller treats the definition as unparseable,
/// same as a regex non-match).
fn scan_matching_paren(source: &str, open_idx: usize) -> Option<usize> {
    let bytes = source.as_bytes();
    let end = (open_idx + PAREN_SCAN_LIMIT).min(bytes.len());
    let mut depth = 0i32;
    for (i, &b) in bytes.iter().enumerate().take(end).skip(open_idx) {
        match b {
            b'(' => depth += 1,
            b')' => {
                depth -= 1;
                if depth == 0 {
                    return Some(i + 1);
                }
            }
            _ => {}
        }
    }
    None
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

    for cap in HEADER_RE.captures_iter(source) {
        let kind = cap[1].to_string();
        let name = cap[2].to_string();

        // Skip private names.
        if name.starts_with('_') {
            continue;
        }

        let header_end = cap.get(0).unwrap().end();
        let (signature, after_paren) = match cap.get(3) {
            // Paren present: header_end is right after '(' (the capture is
            // the '(' itself), so open_idx = header_end - 1.
            Some(_) => match scan_matching_paren(source, header_end - 1) {
                Some(close_idx) => (
                    source[header_end..close_idx - 1].trim().to_string(),
                    close_idx,
                ),
                // Unclosed within the scan bound — skip, don't misparse.
                None => continue,
            },
            // Paren-less class (`class Config:`).
            None => (String::new(), header_end),
        };

        // `-> ...:` (or paren-less `:`) must immediately follow, else this
        // wasn't actually a def/class header.
        let tail_source = &source[after_paren..];
        let Some(tail_match) = TAIL_RE.find(tail_source) else {
            continue;
        };
        let match_end = after_paren + tail_match.end();

        // Find the docstring: look only at the first ~500 chars after the definition.
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
            signature: format!("({})", signature),
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

    // Regression: `\(([^)]*)\)` (the old combined header+params regex)
    // cannot span a nested ')' inside a default value, so a top-level
    // `def resize(size=(640, 480)):` (ubiquitous in vision/ML libs) never
    // matched at all — the whole symbol silently vanished from
    // module_members. HEADER_RE + scan_matching_paren must find it and
    // capture the full nested signature text.
    #[test]
    fn parse_py_file_finds_def_with_nested_paren_default() {
        let src = "def resize(size=(640, 480)):\n    \"\"\"Resize.\"\"\"\n    pass\n";
        let members = parse_py_file(src);
        assert_eq!(members.len(), 1);
        assert_eq!(members[0].name, "resize");
        assert_eq!(members[0].signature, "(size=(640, 480))");
    }

    #[test]
    fn parse_py_file_finds_class_with_call_in_bases() {
        let src = "class A(B, metaclass=Meta()):\n    \"\"\"Doc.\"\"\"\n    pass\n";
        let members = parse_py_file(src);
        assert_eq!(members.len(), 1);
        assert_eq!(members[0].name, "A");
        assert_eq!(members[0].kind, "class");
    }

    #[test]
    fn parse_py_file_finds_def_with_quoted_return_annotation() {
        let src = "def f() -> \"Foo\":\n    \"\"\"Doc.\"\"\"\n    pass\n";
        let members = parse_py_file(src);
        assert_eq!(members.len(), 1);
        assert_eq!(members[0].name, "f");
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

    // Regression: WalkDir runs with follow_links(false), so a symlink
    // entry's own file_type() is neither file nor dir even when the target
    // is a regular .py file — that used to make walk_py_files silently drop
    // symlinked .py files that the Python fallback's os.walk() includes.
    // Resolving through fs::metadata (which follows the link) keeps both
    // engines' discovered file sets identical.
    #[test]
    #[cfg(unix)]
    fn walk_py_files_includes_symlinked_py_file() {
        use std::os::unix::fs::symlink;

        let dir = std::env::temp_dir().join(format!(
            "pydocs_mcp_native_test_walk_symlink_{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create temp dir");

        let real = dir.join("real.py");
        fs::write(&real, "x = 1\n").expect("write real.py");
        let linked = dir.join("linked.py");
        symlink(&real, &linked).expect("create symlink");

        let found = walk_py_files_impl(&dir);
        let _ = fs::remove_dir_all(&dir);

        let real_str = real.to_string_lossy().into_owned();
        let linked_str = linked.to_string_lossy().into_owned();
        assert!(
            found.contains(&real_str),
            "expected {found:?} to contain {real_str}"
        );
        assert!(
            found.contains(&linked_str),
            "expected {found:?} to contain {linked_str}"
        );
    }

    // Regression: passing a .py FILE as root used to make WalkDir yield the
    // root entry itself (it passes the dir-only filter_entry, then is_file()
    // + .py extension matches), returning [root]. The Python fallback's
    // os.walk() yields nothing for a non-directory root, so this was a
    // cross-engine divergence at the substitution boundary — a caller
    // mis-passing a file path got a one-file index under Rust and an empty
    // index under Python. walk_py_files_impl now requires root to be a
    // directory, matching os.walk() semantics on both sides.
    #[test]
    fn walk_py_files_root_is_a_py_file_returns_empty() {
        let path = unique_temp_path("root_is_file");
        fs::write(&path, "x = 1\n").expect("write temp file");

        let found = walk_py_files_impl(&path);
        let _ = fs::remove_file(&path);

        assert!(found.is_empty(), "expected no files, got {found:?}");
    }

    #[test]
    fn walk_py_files_root_does_not_exist_returns_empty() {
        let missing = std::env::temp_dir().join(format!(
            "pydocs_mcp_native_test_missing_root_{}_{}",
            std::process::id(),
            "does-not-exist"
        ));
        assert!(!missing.exists());

        let found = walk_py_files_impl(&missing);
        assert!(found.is_empty(), "expected no files, got {found:?}");
    }
}
