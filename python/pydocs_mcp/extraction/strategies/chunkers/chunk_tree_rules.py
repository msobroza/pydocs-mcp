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

Three parts, folded as one token by ``ContentHashStage``:

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
  a re-extraction (PR #257 verified exactly that property). Note the price of
  that conservatism: tree-sitter ignores query whitespace and ``;`` comments,
  so REFORMATTING a query still bills everyone a re-extract. Edit query text
  deliberately.
- :func:`_chunking_config_digest` is DERIVED from the deployment's
  ``ChunkingConfig``. Those four knobs — ``text_section.window_lines`` /
  ``json_max_chunks``, ``markdown.{min,max}_heading_level``,
  ``notebook.include_outputs`` — parameterize the chunkers from YAML, and they
  reached no hash at all: ``ChunkingStage.to_dict`` returns only its type, and
  ``ingestion_pipeline_hash`` folds the ingestion PIPELINE yaml, not
  ``default_config.yaml`` or the user overlay. Changing one was not merely
  serving stale trees — ``content_hash`` runs AFTER ``embed_chunks`` in
  ``pipelines/ingestion.yaml``, so every pass re-chunked and re-embedded and
  then discarded the result as a cache hit, forever, healed only by
  ``--force``. Derived from the model rather than listed field by field, so a
  knob added later folds itself.

The derived halves cover the tree-sitter chunkers and every YAML-tunable knob,
because those are the parts whose behavior lives in data. The AST-Python,
Markdown-heading, notebook and text-section chunkers express the REST of theirs
in code, which is what the hand-bumped part is for. When you change any chunker,
ask: does the emitted tree change for some input? If yes and neither the query
table nor ``ChunkingConfig`` moved, bump the version.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.extraction.config import ChunkingConfig

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


def chunk_tree_fingerprint(chunking: ChunkingConfig) -> str:
    """The chunk-tree salt: the hand-bumped version and the two derived digests.

    One token so the package hash grows one fold rather than three, and the
    version stays readable inside it — a digest collision must not be able to
    swallow a deliberate bump.

    Example: ``chunk_tree_fingerprint(ChunkingConfig())`` returns
    ``'chunk-trees/1|3f6b1c0d9a2e4571|8c02de11a7b45930'``.
    """
    return (
        f"{CHUNK_TREE_RULE_VERSION}|{_language_spec_digest()}|{_chunking_config_digest(chunking)}"
    )


def _chunking_config_digest(chunking: ChunkingConfig) -> str:
    """Digest of the deployment's chunker tunables.

    ``model_dump_json`` rather than a hand-written field list: pydantic emits
    fields in declaration order, so the serialization is stable across
    processes, and a knob added to ``ChunkingConfig`` later is folded without
    anyone remembering to come back here — which is the whole failure this salt
    exists to prevent.
    """
    return hashlib.md5(chunking.model_dump_json().encode(), usedforsecurity=False).hexdigest()[:16]


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
