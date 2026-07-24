"""Pure construction helpers for the CrossCommitVuln QA dataset (design §5).

Deterministic, network-free transforms from a CrossCommitVuln-Bench
``annotation.json`` dict to vendored-record parts: the inclusion filter
(design §4.3), banned-token mining + query leak-check (design §5.2), the
unbiased single-snapshot query template, and the gold builders (design
§5.3). The network build tool (``benchmarks/tools/build_crosscommitvuln.py``)
composes these at construction time; the runtime loader reuses only
:func:`gold_from_record`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence

from .base_dataset import GoldAnswer

log = logging.getLogger(__name__)

# v2 framing bans (design §5.2): provenance / detection-difficulty vocabulary.
# Any of these in the query would leak that the flaw was assembled across
# commits or that it evades scanners — biasing the needle-search measurement.
_FRAMING_BANS: tuple[str, ...] = (
    "commit",
    "commits",
    "multiple",
    "multi-commit",
    "gradually",
    "over time",
    "across",
    "benign",
    "static analysis",
    "sast",
    "per-commit",
    "individually",
    "scanner",
)

# CWE id -> flaw-class phrases banned from the query (design §5.2). Unknown
# ids simply add no phrases; extend as construction meets new CWE classes.
_CWE_CLASS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "CWE-22": ("path traversal", "directory traversal"),
    "CWE-73": ("path traversal", "file inclusion"),
    "CWE-78": ("os command injection", "command injection"),
    "CWE-79": ("cross-site scripting", "xss"),
    "CWE-89": ("sql injection",),
    "CWE-94": ("code injection",),
    "CWE-306": ("missing authentication",),
    "CWE-321": ("hard-coded key", "hardcoded key"),
    "CWE-347": ("signature bypass", "signature verification"),
    "CWE-502": ("deserialization", "unsafe deserialization"),
    "CWE-770": ("resource exhaustion",),
    "CWE-915": ("mass assignment",),
    "CWE-918": ("server-side request forgery", "ssrf"),
    "CWE-943": ("query injection", "nosql injection"),
}

# Single-word sink names that the code-shape regex below cannot catch
# (no underscore / dot); scanned against role + subject + chain text.
_SINK_VOCAB: tuple[str, ...] = (
    "subprocess",
    "eval",
    "exec",
    "pickle",
    "marshal",
    "os.system",
    "yaml.load",
    "shell",
)

# Code-like symbol tokens inside prose: dotted names (pickle.loads),
# snake_case calls (exec_cmd, doveadm_cmd), kwarg literals (shell=True).
_SYMBOL_RE = re.compile(
    r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b"
    r"|\b[A-Za-z]\w*_\w+\b"
    r"|\b\w+=(?:True|False|\w+)\b"
)

# ISO-ish and slashed date literals: 2024-01-15, 2024/01/15, 15-01-2024.
_DATE_RE = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b")

# File-path tokens inside role prose — the build_file_set fallback source.
_FILE_TOKEN_RE = re.compile(r"[\w./-]+\.py\b")

# LOCKED template (design §5.2): interpolates ONLY ecosystem / slug / severity.
# Nothing temporal, nothing about commit structure or flaw class.
_QUERY_TEMPLATE = (
    "This snapshot of the {ecosystem} project {repo_slug} contains a "
    "{severity}-severity security vulnerability. Locate the exploitable "
    "condition: identify where untrusted input enters, the dangerous "
    "operation it reaches, and classify the vulnerability by its exploit."
)


def is_included(annotation: dict) -> bool:
    """First construction gate (design §4.3): filled-chain positives only."""
    desc = (annotation.get("vulnerability_chain") or {}).get("description", "") or ""
    return (
        annotation.get("annotation_status") == "complete+sast"
        and bool(desc)
        and not desc.startswith("TODO")
    )


def repo_slug(annotation: dict) -> str:
    """``org/name`` slug from the annotation's GitHub URL."""
    url = str(annotation.get("repo", "")).rstrip("/")
    parts = url.removesuffix(".git").split("/")
    if len(parts) < 2 or not parts[-1] or not parts[-2]:
        raise ValueError(f"invalid repo url: got {url!r}, expected …github.com/<org>/<name>")
    return f"{parts[-2]}/{parts[-1]}"


def mine_banned_tokens(annotation: dict) -> tuple[str, ...]:
    """Mine every token banned from this record's query (design §5.2)."""
    tokens: list[str] = []
    tokens += _id_tokens(annotation)
    tokens += _commit_tokens(annotation)
    tokens += _file_tokens(annotation)
    tokens += _symbol_tokens(annotation)
    tokens += _flaw_class_tokens(annotation)
    tokens += _date_tokens(annotation)
    tokens += _FRAMING_BANS
    return _drop_slug_collisions(_ordered_distinct(tokens), repo_slug(annotation))


def build_query(annotation: dict) -> str:
    """The unbiased single-snapshot question — LOCKED template (design §5.2)."""
    return _QUERY_TEMPLATE.format(
        ecosystem=annotation.get("ecosystem", "PyPI"),
        repo_slug=repo_slug(annotation),
        severity=str(annotation.get("severity_combined", "high")).lower(),
    )


def assert_query_clean(query: str, banned: Sequence[str]) -> None:
    """Raise ``ValueError`` naming the first banned token found in ``query``.

    Case-insensitive, word-boundary aware. Slug/token collisions never reach
    here: :func:`mine_banned_tokens` already dropped slug-colliding tokens
    (repo identity wins, design §5.2 precedence rule).
    """
    for token in banned:
        if _token_in_text(token, query):
            raise ValueError(
                f"query leaks banned token {token!r} in {query!r} — "
                "regenerate the query or fix the template (design §5.2)"
            )


def build_file_set(annotation: dict) -> tuple[str, ...]:
    """Gold files: union of ``contributing_commits[].files_changed``.

    Fallback (locked contract): file paths named in SOURCE/SINK role prose.
    Empty gold is a construction error — this dataset's gold is always
    non-empty (design §6.5), so fail loud with the offending cve_id.
    """
    commits = annotation.get("contributing_commits") or []
    primary = _ordered_distinct(f for c in commits for f in (c.get("files_changed") or []))
    if primary:
        return primary
    role_files = _ordered_distinct(
        m.group(0) for c in commits for m in _FILE_TOKEN_RE.finditer(str(c.get("role", "")))
    )
    if role_files:
        return role_files
    raise ValueError(
        f"no gold files derivable for {annotation.get('cve_id')!r}: "
        "contributing_commits[].files_changed empty and no .py path in role text"
    )


def build_gold_extra(annotation: dict) -> dict[str, str]:
    """Gate-candidate strings only — NO prose (both gates tokenize every value)."""
    extra = {"cve_id": str(annotation["cve_id"])}
    for i, cwe in enumerate(annotation.get("cwe_ids") or []):
        extra[f"cwe_id_{i}"] = str(cwe)
    return extra


def build_mechanism(annotation: dict) -> str:
    """The source→sink sentence — rides ``GoldAnswer.ast_body``, never ``extra``."""
    desc = (annotation.get("vulnerability_chain") or {}).get("description", "") or ""
    if not desc:
        raise ValueError(
            f"empty vulnerability_chain.description for {annotation.get('cve_id')!r}; "
            "is_included should have excluded this annotation"
        )
    return " ".join(desc.split())


def gold_from_record(rec: dict) -> GoldAnswer:
    """Vendored record -> ``GoldAnswer`` (runtime loader seam, design §5.3)."""
    gold = rec["gold"]
    extra: dict[str, object] = {"cve_id": str(gold["cve_id"])}
    for i, cwe in enumerate(gold.get("cwe_ids") or []):
        extra[f"cwe_id_{i}"] = str(cwe)
    return GoldAnswer(
        ast_body=gold["mechanism"],
        file_set=tuple(gold["files"]),
        extra=extra,
    )


def _id_tokens(annotation: dict) -> list[str]:
    tokens = [str(annotation.get("cve_id", "")), str(annotation.get("ghsa_id", "") or "")]
    for cwe in annotation.get("cwe_ids") or []:
        cwe_str = str(cwe)
        tokens += [cwe_str, cwe_str.removeprefix("CWE-")]
    return [t for t in tokens if t]


def _commit_tokens(annotation: dict) -> list[str]:
    tokens: list[str] = []
    fix = str(annotation.get("fix_commit", ""))
    if fix:
        tokens += [fix, fix[:8]]
    for commit in annotation.get("contributing_commits") or []:
        full = str(commit.get("hash", ""))
        if full:
            tokens += [full, full[:8]]
        if commit.get("short_hash"):
            tokens.append(str(commit["short_hash"]))
    return tokens


def _file_tokens(annotation: dict) -> list[str]:
    tokens: list[str] = []
    for commit in annotation.get("contributing_commits") or []:
        for path in commit.get("files_changed") or []:
            tokens += [path, path.rsplit("/", 1)[-1]]
    return tokens


def _symbol_tokens(annotation: dict) -> list[str]:
    commits = annotation.get("contributing_commits") or []
    text = " ".join(
        [str(c.get("role", "")) for c in commits]
        + [str(c.get("subject", "")) for c in commits]
        + [str((annotation.get("vulnerability_chain") or {}).get("description", ""))]
    )
    tokens = [m.group(0) for m in _SYMBOL_RE.finditer(text)]
    tokens += [w for w in _SINK_VOCAB if _token_in_text(w, text)]
    return tokens


def _flaw_class_tokens(annotation: dict) -> list[str]:
    summary = str(annotation.get("summary", "")).lower()
    tokens: list[str] = []
    for cwe in annotation.get("cwe_ids") or []:
        tokens += _CWE_CLASS_KEYWORDS.get(str(cwe), ())
    for phrases in _CWE_CLASS_KEYWORDS.values():
        tokens += [p for p in phrases if p in summary]
    return tokens


def _date_tokens(annotation: dict) -> list[str]:
    raw = [str(annotation.get("fix_commit_date", "") or "")]
    raw += [str(c.get("date", "") or "") for c in annotation.get("contributing_commits") or []]
    tokens: list[str] = []
    for value in raw:
        if value:
            tokens.append(value)
            tokens += _DATE_RE.findall(value)
    return tokens


def _drop_slug_collisions(tokens: tuple[str, ...], slug: str) -> tuple[str, ...]:
    # Precedence rule (design §5.2): the query must carry the repo slug; a
    # banned token that matches inside the slug would fail every query, so
    # repo identity wins — drop the token, log it, and let the mandatory
    # manual review pass inspect the record.
    kept: list[str] = []
    for token in tokens:
        if _token_in_text(token, slug):
            log.info(
                "crosscommitvuln build: banned token %r collides with repo slug %r "
                "— repo identity wins, token dropped from the ban list",
                token,
                slug,
            )
            continue
        kept.append(token)
    return tuple(kept)


def _token_in_text(token: str, text: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _ordered_distinct(items: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        if item:
            seen.setdefault(item)
    return tuple(seen)
