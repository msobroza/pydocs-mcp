"""Per-language tree-sitter query data for :class:`MultilangChunker`
(ADR 0021 T3).

Split out of ``multilang_treesitter.py`` so the chunker file stays small and
the language-specific S-expression queries live in one grep-able table. Each
entry maps a ceiling-only code extension to the tuple::

    (grammar_module, language_accessor, query_source, item_type -> NodeKind)

- ``grammar_module`` — the official MIT grammar wheel's import name
  (``tree_sitter_rust`` …), imported LAZILY via ``importlib`` in the chunker so
  ``import pydocs_mcp`` never pulls tree-sitter (the ``[late-interaction]``
  lazy-import discipline).
- ``language_accessor`` — the module attribute returning the grammar's
  PyCapsule (``.language()``; TypeScript exposes ``language_typescript`` for
  ``.ts`` and ``language_tsx`` for ``.tsx`` — two dialects, one wheel).
- ``query_source`` — every pattern is anchored to the grammar's ROOT node
  (``source_file`` / ``translation_unit`` / ``program``) so only TOP-LEVEL
  items match; nested members are intentionally left to the text-window
  fallback (ADR 0021 Decision 5). Captures ``@item`` (the symbol node) and
  ``@name`` (its identifier) — paired within one match, which is why the
  chunker MUST read them via ``matches()`` not ``captures()`` (the probe found
  ``captures()`` returns per-name lists in independent document order, so
  pairing silently misaligns — evidence-treesitter §3).
- ``item_type -> NodeKind`` — maps each captured tree-sitter node type onto an
  EXISTING :class:`NodeKind` (functions → FUNCTION, aggregate/nominal types →
  CLASS). T3 deliberately adds NO new NodeKind (ADR 0021 action item 3 lists
  none); the coarse function/class split is what ``get_symbol`` / ``parent_rollup``
  already understand cross-language.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydocs_mcp.extraction.model import NodeKind

# --- Rust (.rs) — the explicit dogfood language (census: 0 in the dev pool) --
_RUST_QUERY = """
(source_file (function_item name:(identifier) @name) @item)
(source_file (struct_item name:(type_identifier) @name) @item)
(source_file (enum_item name:(type_identifier) @name) @item)
(source_file (trait_item name:(type_identifier) @name) @item)
(source_file (mod_item name:(identifier) @name) @item)
(source_file (impl_item type:(type_identifier) @name) @item)
"""
_RUST_KINDS: Mapping[str, NodeKind] = {
    "function_item": NodeKind.FUNCTION,
    "struct_item": NodeKind.CLASS,
    "enum_item": NodeKind.CLASS,
    "trait_item": NodeKind.CLASS,
    "mod_item": NodeKind.CLASS,
    "impl_item": NodeKind.CLASS,
}

# --- C (.c/.h) — ``declaration`` w/ a function_declarator = a prototype -------
_C_QUERY = """
(translation_unit (function_definition
    declarator:(function_declarator declarator:(identifier) @name)) @item)
(translation_unit (declaration
    declarator:(function_declarator declarator:(identifier) @name)) @item)
(translation_unit (struct_specifier name:(type_identifier) @name) @item)
(translation_unit (enum_specifier name:(type_identifier) @name) @item)
(translation_unit (type_definition declarator:(type_identifier) @name) @item)
"""
_C_KINDS: Mapping[str, NodeKind] = {
    "function_definition": NodeKind.FUNCTION,
    "declaration": NodeKind.FUNCTION,
    "struct_specifier": NodeKind.CLASS,
    "enum_specifier": NodeKind.CLASS,
    "type_definition": NodeKind.CLASS,
}

# --- JavaScript (.js) — class name is an ``identifier`` (unlike TypeScript) ---
_JS_QUERY = """
(program (function_declaration name:(identifier) @name) @item)
(program (generator_function_declaration name:(identifier) @name) @item)
(program (class_declaration name:(identifier) @name) @item)
(program (lexical_declaration (variable_declarator name:(identifier) @name)) @item)
"""
_JS_KINDS: Mapping[str, NodeKind] = {
    "function_declaration": NodeKind.FUNCTION,
    "generator_function_declaration": NodeKind.FUNCTION,
    "lexical_declaration": NodeKind.FUNCTION,
    "class_declaration": NodeKind.CLASS,
}

# --- TypeScript (.ts/.tsx) — class name is a ``type_identifier`` here; adds ---
# interface / type-alias / enum. The probe found the JS query's
# ``class_declaration name:(identifier)`` an "Impossible pattern" for the TS
# grammar — hence a separate query, not JS + extras.
_TS_QUERY = """
(program (function_declaration name:(identifier) @name) @item)
(program (generator_function_declaration name:(identifier) @name) @item)
(program (class_declaration name:(type_identifier) @name) @item)
(program (abstract_class_declaration name:(type_identifier) @name) @item)
(program (interface_declaration name:(type_identifier) @name) @item)
(program (type_alias_declaration name:(type_identifier) @name) @item)
(program (enum_declaration name:(identifier) @name) @item)
(program (lexical_declaration (variable_declarator name:(identifier) @name)) @item)
"""
_TS_KINDS: Mapping[str, NodeKind] = {
    "function_declaration": NodeKind.FUNCTION,
    "generator_function_declaration": NodeKind.FUNCTION,
    "lexical_declaration": NodeKind.FUNCTION,
    "class_declaration": NodeKind.CLASS,
    "abstract_class_declaration": NodeKind.CLASS,
    "interface_declaration": NodeKind.CLASS,
    "type_alias_declaration": NodeKind.CLASS,
    "enum_declaration": NodeKind.CLASS,
}

# extension -> (grammar_module, accessor, query_source, item_type -> NodeKind).
LanguageSpec = tuple[str, str, str, Mapping[str, NodeKind]]

LANGUAGE_SPECS: Mapping[str, LanguageSpec] = {
    ".rs": ("tree_sitter_rust", "language", _RUST_QUERY, _RUST_KINDS),
    ".c": ("tree_sitter_c", "language", _C_QUERY, _C_KINDS),
    ".h": ("tree_sitter_c", "language", _C_QUERY, _C_KINDS),
    ".js": ("tree_sitter_javascript", "language", _JS_QUERY, _JS_KINDS),
    ".ts": ("tree_sitter_typescript", "language_typescript", _TS_QUERY, _TS_KINDS),
    ".tsx": ("tree_sitter_typescript", "language_tsx", _TS_QUERY, _TS_KINDS),
}

# The T3 code extensions this chunker owns — ceiling-only opt-in (present in
# ALLOWED_EXTENSIONS, absent from the default include_extensions).
MULTILANG_EXTENSIONS: tuple[str, ...] = tuple(LANGUAGE_SPECS)

__all__ = ("LANGUAGE_SPECS", "MULTILANG_EXTENSIONS", "LanguageSpec")
