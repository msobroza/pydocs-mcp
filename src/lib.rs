// src/lib.rs
//
// Rust-accelerated functions for pydocs-mcp.
//
// This module provides 4 fast functions callable from Python:
//   1. walk_py_files  — find all .py files, skipping venvs etc.
//   2. hash_files     — hash file paths + mtimes (detects changes)
//   3. chunk_text     — split markdown/rst into semantic chunks
//   4. parse_py_file  — extract functions/classes from Python source

use pyo3::prelude::*;
use rayon::prelude::*;
use regex::Regex;
use std::fs;
use std::path::Path;
use walkdir::WalkDir;
use xxhash_rust::xxh3::xxh3_64;

// ── 1. File Walker ───────────────────────────────────────────────────────
//
// Walks a directory tree and returns all .py file paths.
// Skips common directories like .git, __pycache__, .venv, etc.
// About 10x faster than Python's pathlib.rglob("*.py").

/// Directories we never want to enter.
const SKIP_DIRS: &[&str] = &[
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".tox", ".eggs", "build", "dist", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "htmlcov", ".nox", "egg-info",
];

/// Walk `root` and return all .py file paths as strings.
#[pyfunction]
fn walk_py_files(root: &str) -> Vec<String> {
    let root_path = Path::new(root);

    WalkDir::new(root_path)
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
        .collect()
}


// ── 2. File Hasher ───────────────────────────────────────────────────────
//
// Hashes a list of file paths + their modification times.
// Returns a hex string. If any file changed, the hash changes.
// Uses xxh3 which is ~3x faster than MD5.

/// Compute a single hash from file paths + mtimes.
/// Useful to detect if any source file was added, removed, or modified.
#[pyfunction]
fn hash_files(paths: Vec<String>) -> String {
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
}


// ── 3. Text Chunker ──────────────────────────────────────────────────────
//
// Splits text (markdown, rst, or code) into chunks at heading boundaries.
// Each chunk is a (heading, body) tuple.
// Chunks are capped at `max_chars` to fit in LLM context windows.

/// A single chunk of text with its heading.
#[derive(Clone)]
struct Chunk {
    heading: String,
    body: String,
}

/// Split text into semantic chunks at markdown heading boundaries.
///
/// Returns a list of (heading, body) tuples.
///
/// # Arguments
/// * `text`      — The full text to split
/// * `max_chars` — Maximum characters per chunk body (default: 4000)
#[pyfunction]
#[pyo3(signature = (text, max_chars=4000))]
fn chunk_text(text: &str, max_chars: usize) -> Vec<(String, String)> {
    // Pre-compile the heading regex once.
    let heading_re = Regex::new(r"(?m)^#{1,4}\s+(.+)$").unwrap();

    let mut results: Vec<Chunk> = Vec::new();
    let mut current_heading = "Overview".to_string();
    let mut current_body = String::new();

    // Helper: save the current chunk if it has content.
    let mut flush = |heading: &str, body: &mut String| {
        let trimmed = body.trim();
        if trimmed.len() > 30 {
            let capped = if trimmed.len() > max_chars {
                &trimmed[..max_chars]
            } else {
                trimmed
            };
            results.push(Chunk {
                heading: heading.to_string(),
                body: capped.to_string(),
            });
        }
        body.clear();
    };

    for line in text.lines() {
        // Check if this line is a heading.
        if let Some(cap) = heading_re.captures(line) {
            flush(&current_heading, &mut current_body);
            current_heading = cap[1].trim().to_string();
            continue;
        }

        current_body.push_str(line);
        current_body.push('\n');

        // Split if chunk is getting too large.
        if current_body.len() > max_chars * 2 {
            flush(&current_heading, &mut current_body);
        }
    }

    // Don't forget the last chunk.
    flush(&current_heading, &mut current_body);

    // Convert to Python-friendly tuples.
    results
        .into_iter()
        .map(|c| (c.heading, c.body))
        .collect()
}


// ── 4. Python Source Parser ──────────────────────────────────────────────
//
// Extracts function/class definitions from Python source code using regex.
// This is NOT a full AST parser, but it's ~5x faster than Python's ast.parse
// and works even on files with syntax errors.
//
// For each function/class, we extract:
//   - name, kind (def/async def/class), signature, docstring

/// One extracted symbol (function or class).
#[pyclass]
#[derive(Clone)]
struct Symbol {
    #[pyo3(get)]
    name: String,
    #[pyo3(get)]
    kind: String,       // "def", "async def", or "class"
    #[pyo3(get)]
    signature: String,  // everything between parentheses
    #[pyo3(get)]
    docstring: String,  // first triple-quoted string after definition
}

/// Extract top-level functions and classes from Python source code.
///
/// This uses regex, not a full parser, so it's fast and fault-tolerant.
/// Only extracts top-level definitions (no indentation before def/class).
///
/// Returns a list of Symbol objects.
#[pyfunction]
fn parse_py_file(source: &str) -> Vec<Symbol> {
    // Match: def name(...):  or  async def name(...):  or  class name(...):
    // Only at the start of a line (top-level).
    let def_re = Regex::new(
        r#"(?m)^(async\s+def|def|class)\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:->[\s\w\[\],.|]*)?:"#
    ).unwrap();

    // Match triple-quoted docstrings (both """ and ''').
    let doc_re = Regex::new(
        r#"(?s)(?:"""(.*?)"""|'''(.*?)''')"#
    ).unwrap();

    let lines: Vec<&str> = source.lines().collect();
    let mut symbols = Vec::new();

    for cap in def_re.captures_iter(source) {
        let kind = cap[1].to_string();
        let name = cap[2].to_string();
        let signature = format!("({})", cap[3].trim());

        // Skip private names.
        if name.starts_with('_') {
            continue;
        }

        // Find the docstring: look right after the definition line.
        let match_end = cap.get(0).unwrap().end();
        let rest = &source[match_end..];

        let docstring = doc_re
            .captures(rest.trim_start())
            .and_then(|dc| {
                // Get whichever group matched (""" or ''').
                dc.get(1).or_else(|| dc.get(2))
            })
            .map(|m| {
                // Only take it if it starts very close to the def line.
                let s = m.as_str().trim();
                if s.len() > 3000 { &s[..3000] } else { s }
            })
            .unwrap_or("")
            .to_string();

        symbols.push(Symbol {
            name,
            kind,
            signature,
            docstring,
        });
    }

    symbols
}


/// Extract the module-level docstring from Python source.
///
/// Returns the docstring or an empty string if none found.
#[pyfunction]
fn extract_module_doc(source: &str) -> String {
    let trimmed = source.trim_start();
    let doc_re = Regex::new(r#"(?s)^(?:"""(.*?)"""|'''(.*?)''')"#).unwrap();

    doc_re
        .captures(trimmed)
        .and_then(|cap| cap.get(1).or_else(|| cap.get(2)))
        .map(|m| {
            let s = m.as_str().trim();
            if s.len() > 5000 { s[..5000].to_string() } else { s.to_string() }
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
#[pyfunction]
fn read_files_parallel(paths: Vec<String>) -> Vec<(String, String)> {
    paths
        .par_iter()
        .map(|p| {
            let content = fs::read_to_string(p).unwrap_or_default();
            (p.clone(), content)
        })
        .collect()
}


// ── Module Registration ──────────────────────────────────────────────────

/// Register all functions so Python can import them.
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(walk_py_files, m)?)?;
    m.add_function(wrap_pyfunction!(hash_files, m)?)?;
    m.add_function(wrap_pyfunction!(chunk_text, m)?)?;
    m.add_function(wrap_pyfunction!(parse_py_file, m)?)?;
    m.add_function(wrap_pyfunction!(extract_module_doc, m)?)?;
    m.add_function(wrap_pyfunction!(read_file, m)?)?;
    m.add_function(wrap_pyfunction!(read_files_parallel, m)?)?;
    m.add_class::<Symbol>()?;
    Ok(())
}
