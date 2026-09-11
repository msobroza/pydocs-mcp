"""Single source of the chunk-tree salt — how a chunker change reaches an index.

The package content hash is the gate that decides whether a package is
re-extracted. It folds path+mtime, the exclusion fingerprint, the project-only
module-id token, the loadable-grammar fingerprint and the identity salt — none
of which carries anything about what the CHUNKERS emit. So changing a chunker
left every cached package untouched: issue #246 item 1 (PR #257, exported
JS/TS declarations became symbols) and item 4 (PR #258, chunk text was sliced on
the wrong lines after a form feed) each changed chunk trees and each had to ship
an upgrade note telling operators to touch the files or run
``index . --force``. This module is that missing input.

Two halves, folded as one token by ``ContentHashStage``:

- :data:`CHUNK_TREE_RULE_VERSION` is bumped BY HAND for a chunker change that is
  invisible in the declarative data below — new node kinds, a different span or
  title rule, a changed text-window shape, a fixed row splitter (item 4's
  shape). Bumping it costs every package one re-extraction and re-embeds only
  the chunks whose text actually moves, because chunk hashes fold the text.
- :func:`_language_spec_digest` is DERIVED from ``LANGUAGE_SPECS``, so a
  tree-sitter query edit (item 1's shape) invalidates on its own and cannot be
  forgotten. It reads the rendered query strings, never the code that renders
  them: ``_esm_top_level_query`` builds the JS/TS queries from a declaration
  tuple, and a refactor that keeps the text byte-identical must not cost anyone
  a re-extraction (PR #257 verified exactly that property).

The derived half covers only the tree-sitter chunkers, because they are the only
ones whose behavior lives in data. The AST-Python, Markdown-heading, notebook
and text-section chunkers express theirs in code, which is what the hand-bumped
half is for. When you change any chunker, ask: does the emitted tree change for
some input? If yes and the query table did not move, bump the version.
"""

from __future__ import annotations

import hashlib

# Upgrade token for every chunker rule that is not visible in LANGUAGE_SPECS.
# WHY a token and not a SCHEMA_VERSION bump: the bundle shape is unchanged, and
# an older running process that met an unknown schema version would wipe the
# index (member-module-ids spec §4 — the same reason MODULE_ID_RULE_VERSION is
# a token). A stale bundle just misses its hash once and re-extracts.
CHUNK_TREE_RULE_VERSION = "chunk-trees/1"

# Separators for the digest blob. Chosen from the ASCII separator block so they
# can never occur in a tree-sitter query, an extension or a NodeKind value —
# two different tables must not be able to render the same blob.
_FIELD_SEPARATOR = "\x1f"
_RECORD_SEPARATOR = "\x1e"


def chunk_tree_fingerprint() -> str:
    """The chunk-tree salt: the hand-bumped version plus the spec digest.

    One token so the package hash grows one fold rather than two, and the
    version stays readable inside it — a digest collision must not be able to
    swallow a deliberate bump.

    Example: ``chunk_tree_fingerprint()`` returns
    ``'chunk-trees/1|3f6b1c0d9a2e4571'``.
    """
    return f"{CHUNK_TREE_RULE_VERSION}|{_language_spec_digest()}"


def _language_spec_digest() -> str:
    """Digest of every T3 extension's parse instructions.

    All four spec fields, because each one changes the emitted tree: the grammar
    module and its accessor decide WHICH parser runs (a `.tsx` served by the
    `.ts` accessor reparses every file, and the loadable-grammar fingerprint
    records only which extensions load, not which wheel produced them), the
    query decides which nodes become symbols, and the kind map decides what each
    one persists as.
    """
    # Read per call rather than bound at import, for two reasons — neither of
    # them "avoid pulling the chunker package", which is impossible from inside
    # it (``chunkers/__init__`` imports ``multilang_queries`` eagerly): the
    # digest must reflect the CURRENT table, and this is the seam
    # tests/extraction/test_chunk_tree_fingerprint.py varies to prove each spec
    # field reaches the hash. Binding it at import would silence those tests.
    from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import LANGUAGE_SPECS

    # Performance: recomputed per package hash rather than memoized — seven
    # short strings through md5 is microseconds, and a process-level cache would
    # have to grow a reset seam for the suites that vary LANGUAGE_SPECS to prove
    # the fold works. The grammar fingerprint next to it IS memoized because it
    # imports every grammar wheel; this one imports nothing but strings.
    records = []
    for ext in sorted(LANGUAGE_SPECS):
        grammar_module, accessor, query, kinds = LANGUAGE_SPECS[ext]
        kind_map = ",".join(f"{item}={kinds[item].value}" for item in sorted(kinds))
        records.append(_FIELD_SEPARATOR.join((ext, grammar_module, accessor, query, kind_map)))
    blob = _RECORD_SEPARATOR.join(records)
    # md5 matches the package hash's non-cryptographic cache-fingerprint posture
    # (see _fold_digest); [:16] matches its width.
    return hashlib.md5(blob.encode(), usedforsecurity=False).hexdigest()[:16]


__all__ = ("CHUNK_TREE_RULE_VERSION", "chunk_tree_fingerprint")
