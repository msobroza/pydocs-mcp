"""FileToolsService — filesystem ``grep`` / ``glob`` / ``read_file`` cores.

The three filesystem tools serve LIVE DISK but walk exactly the file set
the indexer sees (tool-contracts.md §4.1, ADR 0003): the hardcoded
exclusion floor ∪ YAML ``exclude_dirs`` ∪ the indexed project's own
``[tool.pydocs-mcp] exclude_dirs``, plus the extension allowlist and the
size gate — via :class:`ProjectFileDiscoverer` /
:class:`DependencyFileDiscoverer`. Explicitly NOT ``.gitignore`` and NOT
the Rust ``walk_py_files`` (both diverge from the indexed corpus).

Each public method returns ``(markdown_body, items, meta_extras)``; the
router/server layer wraps these in the shared response envelope. Paths in
bodies and items are project-root-relative POSIX for project files and
absolute for dependency files (they live outside the root).
"""

from __future__ import annotations

import fnmatch
import re
from asyncio import to_thread
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    ServiceUnavailableError,
)
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.retrieval.config import FilesConfig

# NUL-byte sniff window for binary detection (grep skips, read_file errors).
_BINARY_SNIFF_BYTES = 8192
_NO_MATCHES = "No matches."
_NO_FILES = "No files matched."

FileToolResult = tuple[str, tuple[dict[str, object], ...], dict[str, object]]


class GrepRequest(Protocol):
    """Structural shape of a grep call — the pydantic ``GrepInput`` model
    (wire names ``-i``/``-n``/``-A``/``-B``/``-C`` per the contract)
    satisfies it by field name."""

    pattern: str
    path: str
    glob: str
    output_mode: Literal["content", "files_with_matches", "count"]
    case_insensitive: bool
    line_numbers: bool
    after_context: int | None
    before_context: int | None
    context: int | None
    head_limit: int | None
    multiline: bool
    scope: Literal["project", "deps", "all"]


class GlobRequest(Protocol):
    """Structural shape of a glob call (``GlobInput`` satisfies it)."""

    pattern: str
    path: str
    head_limit: int | None


class ReadFileRequest(Protocol):
    """Structural shape of a read_file call (``ReadFileInput`` satisfies it)."""

    file_path: str
    offset: int | None
    limit: int | None


@dataclass(frozen=True, slots=True)
class _CandidateFile:
    abs_path: Path
    rel: str  # root-relative POSIX — the filter key for path=/glob=
    display: str  # body/items path: rel for project files, absolute for deps


@dataclass(frozen=True, slots=True)
class _MatchSpan:
    start_line: int
    end_line: int
    text: str


# One file's grep outcome: candidate, its match spans, its split lines
# (kept for context rendering without a second read).
_FileHit = tuple[_CandidateFile, tuple[_MatchSpan, ...], list[str]]


def _compile_pattern(pattern: str, *, case_insensitive: bool, multiline: bool) -> re.Pattern[str]:
    flags = re.IGNORECASE if case_insensitive else 0
    if multiline:
        flags |= re.DOTALL | re.MULTILINE
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise InvalidArgumentError(
            f"grep pattern {pattern!r} is not a valid Python regex: {exc}"
        ) from exc


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob with ``**`` recursion to a regex over POSIX relpaths.

    ``pathlib.PurePath.full_match`` would do this but is 3.13-only; this
    helper keeps the 3.11 floor. ``*`` / ``?`` never cross ``/``; ``**/``
    matches zero or more whole directories; a bare ``**`` matches anything.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch != "*":
            out.append("[^/]" if ch == "?" else re.escape(ch))
            i += 1
        elif pattern[i : i + 3] == "**/":
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern[i : i + 2] == "**":
            out.append(".*")
            i += 2
        else:
            out.append("[^/]*")
            i += 1
    return re.compile(rf"^{''.join(out)}$")


def _read_text_or_none(path: Path) -> str | None:
    """File text, or ``None`` when binary/unreadable (grep skips silently)."""
    try:
        with path.open("rb") as fh:
            if b"\x00" in fh.read(_BINARY_SNIFF_BYTES):
                return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _line_spans(text: str, regex: re.Pattern[str]) -> tuple[_MatchSpan, ...]:
    return tuple(
        _MatchSpan(lineno, lineno, line)
        for lineno, line in enumerate(text.splitlines(), start=1)
        if regex.search(line)
    )


def _multiline_spans(text: str, regex: re.Pattern[str]) -> tuple[_MatchSpan, ...]:
    spans = []
    for match in regex.finditer(text):
        start = text.count("\n", 0, match.start()) + 1
        spans.append(_MatchSpan(start, start + match.group(0).count("\n"), match.group(0)))
    return tuple(spans)


def _scan_candidates(
    candidates: tuple[_CandidateFile, ...],
    regex: re.Pattern[str],
    multiline: bool,
) -> list[_FileHit]:
    hits: list[_FileHit] = []
    for cand in candidates:
        text = _read_text_or_none(cand.abs_path)
        if text is None:
            continue
        spans = _multiline_spans(text, regex) if multiline else _line_spans(text, regex)
        if spans:
            hits.append((cand, spans, text.splitlines()))
    return hits


def _filter_candidates(
    candidates: tuple[_CandidateFile, ...],
    path: str,
    glob: str,
) -> tuple[_CandidateFile, ...]:
    kept = list(candidates)
    if path:
        prefix = path.strip("/") + "/"
        kept = [c for c in kept if c.rel.startswith(prefix)]
    if glob:
        kept = [c for c in kept if fnmatch.fnmatch(c.rel, glob)]
    return tuple(kept)


def _span_item(cand: _CandidateFile, span: _MatchSpan) -> dict[str, object]:
    return {
        "path": cand.display,
        "start_line": span.start_line,
        "end_line": span.end_line,
        "text": span.text,
    }


def _context_bounds(payload: GrepRequest) -> tuple[int, int]:
    # -C overrides -A/-B (contract §3.7).
    if payload.context is not None:
        return payload.context, payload.context
    return payload.before_context or 0, payload.after_context or 0


def _merged_windows(
    spans: tuple[_MatchSpan, ...],
    before: int,
    after: int,
    n_lines: int,
) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for span in spans:
        lo = max(1, span.start_line - before)
        hi = min(n_lines, span.end_line + after)
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _file_content_blocks(
    cand: _CandidateFile,
    spans: tuple[_MatchSpan, ...],
    lines: list[str],
    before: int,
    after: int,
    line_numbers: bool,
) -> list[str]:
    match_lines: set[int] = set()
    for span in spans:
        match_lines.update(range(span.start_line, span.end_line + 1))
    blocks = []
    for lo, hi in _merged_windows(spans, before, after, len(lines)):
        rendered = []
        for ln in range(lo, hi + 1):
            sep = ":" if ln in match_lines else "-"
            prefix = f"{cand.display}{sep}{ln}{sep}" if line_numbers else f"{cand.display}{sep}"
            rendered.append(prefix + lines[ln - 1])
        blocks.append("\n".join(rendered))
    return blocks


def _render_grep_content(hits: list[_FileHit], payload: GrepRequest, limit: int) -> FileToolResult:
    before, after = _context_bounds(payload)
    has_context = any(
        v is not None for v in (payload.context, payload.after_context, payload.before_context)
    )
    truncated = sum(len(spans) for _, spans, _ in hits) > limit
    remaining = limit
    blocks: list[str] = []
    items: list[dict[str, object]] = []
    for cand, spans, lines in hits:
        if remaining <= 0:
            break
        take = spans[:remaining]
        remaining -= len(take)
        items.extend(_span_item(cand, s) for s in take)
        blocks.extend(_file_content_blocks(cand, take, lines, before, after, payload.line_numbers))
    separator = "\n--\n" if has_context else "\n"
    body = separator.join(blocks) if blocks else _NO_MATCHES
    return body, tuple(items), _truncation_meta(truncated)


def _render_grep_per_file(hits: list[_FileHit], mode: str, limit: int) -> FileToolResult:
    truncated = len(hits) > limit
    lines: list[str] = []
    items: list[dict[str, object]] = []
    for cand, spans, _ in hits[:limit]:
        lines.append(f"{cand.display}: {len(spans)}" if mode == "count" else cand.display)
        # items keep the first-match span so clients can jump straight in.
        items.append(_span_item(cand, spans[0]))
    body = "\n".join(lines) if lines else _NO_MATCHES
    return body, tuple(items), _truncation_meta(truncated)


def _truncation_meta(truncated: bool) -> dict[str, object]:
    return {"truncated": True} if truncated else {}


def _mtime_entries(matched: list[_CandidateFile]) -> tuple[list[str], list[dict[str, object]]]:
    pairs: list[tuple[float, str]] = []
    for cand in matched:
        try:
            pairs.append((cand.abs_path.stat().st_mtime, cand.display))
        except OSError:
            continue  # raced deletion between walk and stat — drop it
    # mtime DESCENDING is contractual (§3.8); path breaks ties deterministically.
    pairs.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, p in pairs], [{"path": p, "mtime": m} for m, p in pairs]


def _readable_text(path: Path, display: str) -> str:
    try:
        with path.open("rb") as fh:
            head = fh.read(_BINARY_SNIFF_BYTES)
    except OSError as exc:
        raise InvalidArgumentError(f"cannot read {display!r}: {exc}") from exc
    if b"\x00" in head:
        raise InvalidArgumentError(
            f"{display!r} looks binary (NUL byte in the first "
            f"{_BINARY_SNIFF_BYTES} bytes); read_file serves text files only"
        )
    return path.read_text(encoding="utf-8", errors="replace")


def _read_window(path: Path, display: str, offset: int, limit: int) -> FileToolResult:
    lines = _readable_text(path, display).splitlines()
    if not lines:
        return "", ({"path": display, "start_line": 0, "end_line": 0},), {}
    if offset > len(lines):
        raise InvalidArgumentError(
            f"offset={offset} is past the end of {display!r} ({len(lines)} lines)"
        )
    window = lines[offset - 1 : offset - 1 + limit]
    end = offset - 1 + len(window)
    body = "\n".join(
        f"{ln:>6}\t{line}" for ln, line in zip(range(offset, end + 1), window, strict=True)
    )
    meta: dict[str, object] = {}
    if end < len(lines):
        body += (
            f"\n... (file continues: {len(lines) - end} more lines; re-read with offset={end + 1})"
        )
        meta["truncated"] = True
    return body, ({"path": display, "start_line": offset, "end_line": end},), meta


@dataclass(frozen=True, slots=True)
class FileToolsService:
    """Discovery-scoped filesystem search/read (grep, glob, read_file).

    ``project_root`` is ``None`` for read-only bundles (an index served
    without its source checkout) — project-scope calls then raise
    :class:`ServiceUnavailableError`. Scanning runs via ``to_thread`` so
    the async event loop never blocks on disk I/O.
    """

    project_root: Path | None  # None ⇒ read-only bundle, no source tree
    project_scope: DiscoveryScopeConfig  # extraction.discovery.project
    dependency_scope: DiscoveryScopeConfig  # extraction.discovery.dependency
    list_dependency_packages: Callable[[], Awaitable[tuple[str, ...]]]
    files_config: FilesConfig

    async def grep(self, payload: GrepRequest) -> FileToolResult:
        """Regex search (Python ``re`` flavor) over the discovery-scope corpus."""
        regex = _compile_pattern(
            payload.pattern,
            case_insensitive=payload.case_insensitive,
            multiline=payload.multiline,
        )
        candidates = _filter_candidates(
            await self._candidates(payload.scope), payload.path, payload.glob
        )
        limit = self._effective_limit(payload.head_limit, self.files_config.grep_head_limit)
        hits = await to_thread(_scan_candidates, candidates, regex, payload.multiline)
        if payload.output_mode == "content":
            return _render_grep_content(hits, payload, limit)
        return _render_grep_per_file(hits, payload.output_mode, limit)

    async def glob(self, payload: GlobRequest) -> FileToolResult:
        """Find project files by glob pattern, newest (mtime) first."""
        regex = _glob_to_regex(payload.pattern)
        candidates = await to_thread(self._project_candidates)
        matched = [
            cand
            for cand in candidates
            if (key := _scoped_match_key(cand, payload.path)) is not None and regex.match(key)
        ]
        limit = self._effective_limit(payload.head_limit, self.files_config.glob_head_limit)
        paths, items = await to_thread(_mtime_entries, matched)
        truncated = len(items) > limit
        body = "\n".join(paths[:limit]) if paths else _NO_FILES
        return body, tuple(items[:limit]), _truncation_meta(truncated)

    async def read_file(self, payload: ReadFileRequest) -> FileToolResult:
        """Read a file (``cat -n`` style) inside project ∪ dependency roots."""
        resolved, display = await self._resolve_readable(payload.file_path)
        limit = self._effective_limit(payload.limit, self.files_config.read_limit)
        return await to_thread(_read_window, resolved, display, payload.offset or 1, limit)

    # ── candidate enumeration ────────────────────────────────────────────

    async def _candidates(self, scope: str) -> tuple[_CandidateFile, ...]:
        out: list[_CandidateFile] = []
        if scope in ("project", "all"):
            out.extend(await to_thread(self._project_candidates))
        if scope in ("deps", "all"):
            names = await self.list_dependency_packages()
            out.extend(await to_thread(self._dependency_candidates, names))
        return tuple(out)

    def _project_candidates(self) -> list[_CandidateFile]:
        root = self._require_project_root()
        paths, _, _ = ProjectFileDiscoverer(scope=self.project_scope).discover(root)
        return [
            _CandidateFile(Path(p), rel, rel)
            for p in paths
            for rel in (Path(p).relative_to(root).as_posix(),)
        ]

    def _dependency_candidates(self, names: tuple[str, ...]) -> list[_CandidateFile]:
        discoverer = DependencyFileDiscoverer(scope=self.dependency_scope)
        out: list[_CandidateFile] = []
        for name in names:
            paths, dep_root, _ = discoverer.discover(name)
            for p in paths:
                out.append(_CandidateFile(Path(p), _dep_rel(p, dep_root), p))
        return out

    # ── roots & boundaries ───────────────────────────────────────────────

    def _resolved_root(self) -> Path | None:
        if self.project_root is None or not self.project_root.is_dir():
            return None
        return self.project_root.resolve()

    def _require_project_root(self) -> Path:
        root = self._resolved_root()
        if root is None:
            raise ServiceUnavailableError(
                "project source tree unavailable: this index is a read-only "
                "bundle (no project root on disk). The filesystem tools "
                "(grep/glob/read_file) need the original checkout; indexed "
                "retrieval (search_codebase, get_symbol, ...) still works."
            )
        return root

    async def _resolve_readable(self, file_path: str) -> tuple[Path, str]:
        raw = Path(file_path)
        root = self._resolved_root()
        if not raw.is_absolute() and root is None:
            self._require_project_root()  # raises the read-only-bundle error
        resolved = (raw if raw.is_absolute() else Path(root or ".", raw)).resolve()
        if root is not None and resolved.is_relative_to(root):
            return resolved, resolved.relative_to(root).as_posix()
        for dep_root in await self._dependency_roots():
            if resolved.is_relative_to(dep_root):
                return resolved, str(resolved)
        raise InvalidArgumentError(
            f"file_path {file_path!r} resolves to {resolved} — outside the "
            f"project root and the indexed dependency roots; only files "
            f"inside that boundary are readable"
        )

    async def _dependency_roots(self) -> tuple[Path, ...]:
        names = await self.list_dependency_packages()
        return await to_thread(self._discover_dependency_roots, names)

    def _discover_dependency_roots(self, names: tuple[str, ...]) -> tuple[Path, ...]:
        discoverer = DependencyFileDiscoverer(scope=self.dependency_scope)
        roots = []
        for name in names:
            paths, dep_root, _ = discoverer.discover(name)
            if paths:
                roots.append(dep_root.resolve())
        return tuple(dict.fromkeys(roots))

    def _effective_limit(self, requested: int | None, default: int) -> int:
        limit = default if requested is None else requested
        return min(limit, self.files_config.max_head_limit)


def _dep_rel(path: str, dep_root: Path) -> str:
    try:
        return Path(path).relative_to(dep_root).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _scoped_match_key(cand: _CandidateFile, path: str) -> str | None:
    """Path-scoped glob key: the candidate's relpath under ``path``, or
    ``None`` when the candidate lives outside that directory."""
    if not path:
        return cand.rel
    prefix = path.strip("/") + "/"
    return cand.rel[len(prefix) :] if cand.rel.startswith(prefix) else None


__all__ = (
    "FileToolResult",
    "FileToolsService",
    "GlobRequest",
    "GrepRequest",
    "ReadFileRequest",
)
