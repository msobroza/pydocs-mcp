"""Legible per-section markdown diff of two candidate documents (ADR 0020 §Owner
checkpoint 1a).

The owner sees the seed → frozen-candidate change as a **per-section** markdown
diff rendered from the section dicts (via the product's public ``parse_sections``),
NOT a raw unified diff of the delimited file — the 11-section dict makes a
section-scoped diff trivial and far more legible than a whole-file patch. Only
changed / added / removed sections are shown (unchanged ones are summarized in the
header), and each changed section carries a fenced ``diff`` block so the exact
lines that moved are visible. The optimizer's sentences are the transferable
insight (ADR 0020 §Options f), so this renderer is the report's qualitative core.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping

__all__ = ["render_artifact_diff"]


def render_artifact_diff(seed_document: str, candidate_document: str) -> str:
    """Render a per-section markdown diff of ``seed`` → ``candidate`` documents.

    Both documents are parsed through the product's public ``parse_sections``
    (runtime-local import, permissive mode) so a section rename/smuggle surfaces as
    an added/removed section rather than a raise. Deterministic: identical inputs
    render byte-identical output (golden-testable).
    """
    from pydocs_mcp.application.description_source import (  # runtime-local (product) import
        CANONICAL_HEADERS,
        parse_sections,
    )

    seed = parse_sections(seed_document)
    candidate = parse_sections(candidate_document)
    keys = _ordered_keys(seed, candidate, CANONICAL_HEADERS)
    blocks = [line for key in keys for line in _section_block(key, seed, candidate)]
    return "\n".join([*_summary(seed, candidate, keys), *blocks]).rstrip() + "\n"


def _ordered_keys(
    seed: Mapping[str, str], candidate: Mapping[str, str], canonical: tuple[str, ...]
) -> list[str]:
    """Canonical headers first (in canonical order), then any extras, sorted."""
    union = set(seed) | set(candidate)
    canonical_present = [key for key in canonical if key in union]
    extras = sorted(union - set(canonical))
    return canonical_present + extras


def _summary(seed: Mapping[str, str], candidate: Mapping[str, str], keys: list[str]) -> list[str]:
    """The header line + a changed/added/removed count over the section universe."""
    changed = sum(1 for k in keys if k in seed and k in candidate and seed[k] != candidate[k])
    added = sum(1 for k in keys if k not in seed and k in candidate)
    removed = sum(1 for k in keys if k in seed and k not in candidate)
    return [
        "# Artifact diff: seed -> candidate",
        "",
        f"sections changed: {changed}  added: {added}  removed: {removed}",
        "",
    ]


def _section_block(key: str, seed: Mapping[str, str], candidate: Mapping[str, str]) -> list[str]:
    """The markdown block for one section (empty when unchanged)."""
    in_seed, in_candidate = key in seed, key in candidate
    if in_seed and in_candidate:
        return (
            _modified_block(key, seed[key], candidate[key]) if seed[key] != candidate[key] else []
        )
    if in_candidate:
        return [f"## {key} (added)", "", *_fenced(candidate[key], prefix="+"), ""]
    return [f"## {key} (removed)", "", *_fenced(seed[key], prefix="-"), ""]


def _modified_block(key: str, before: str, after: str) -> list[str]:
    """A ``## key (modified)`` heading + a fenced unified diff of the two bodies."""
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"seed/{key}",
        tofile=f"candidate/{key}",
        lineterm="",
    )
    return [f"## {key} (modified)", "", "```diff", *diff, "```", ""]


def _fenced(body: str, *, prefix: str) -> list[str]:
    """A ``diff``-fenced block of ``body`` lines, each with ``+``/``-`` prefix."""
    return ["```diff", *(f"{prefix}{line}" for line in body.splitlines()), "```"]
