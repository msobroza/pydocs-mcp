"""Per-language tree-sitter query data for :class:`MultilangChunker`
(ADR 0021 T3).

Split out of ``multilang_treesitter.py`` so the chunker file stays small and
the language-specific S-expression queries live in one grep-able table. Each
entry maps a T3 code extension to the tuple::

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
  fallback (ADR 0021 Decision 5); the ONE sanctioned wrapper is ESM's
  ``export_statement`` (see ``_esm_top_level_query``). Captures ``@item`` (the
  symbol node) and ``@name`` (its identifier) — paired within one match, which
  is why the chunker MUST read them via ``matches()`` not ``captures()`` (the
  probe found ``captures()`` returns per-name lists in independent document
  order, so pairing silently misaligns — evidence-treesitter §3).
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


# --- ECMAScript modules (.js/.ts/.tsx): the ONE sanctioned wrapper -----------
# A declaration is a top-level item bare OR one node below the root under
# ``export_statement declaration:`` — ``export class B {}`` is the dominant
# shape in ES modules (issue #246 item 1). Each pattern is listed ONCE below;
# the renderer emits it twice, and the export form also captures the statement
# as ``@wrapper``: ``@item`` stays the inner declaration (its type keys the kind
# map) while the symbol takes the wrapper's span, because a decorator written
# above ``export`` hangs on the statement, not on the class, and an
# ``export default`` may take a row of its own (``_symbol_extent_node``).
# Export lists (``export { x }``) and anonymous ``export default`` expressions
# are not declarations and get no symbol. Written once so a new declaration
# shape cannot be added bare and forgotten under ``export``.


def _esm_top_level_query(declarations: tuple[str, ...]) -> str:
    """The query source for one ESM grammar: every ``declarations`` pattern
    root-anchored bare, then root-anchored under ``export_statement`` with the
    statement captured as ``@wrapper`` (same order both halves)."""
    bare = "".join(f"(program {decl} @item)\n" for decl in declarations)
    exported = "".join(
        f"(program (export_statement declaration:\n    {decl} @item) @wrapper)\n"
        for decl in declarations
    )
    return "\n" + bare + exported


# --- JavaScript (.js) — class name is an ``identifier`` (unlike TypeScript) ---
_JS_DECLARATIONS = (
    "(function_declaration name:(identifier) @name)",
    "(generator_function_declaration name:(identifier) @name)",
    "(class_declaration name:(identifier) @name)",
    "(lexical_declaration (variable_declarator name:(identifier) @name))",
)
_JS_QUERY = _esm_top_level_query(_JS_DECLARATIONS)
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
_TS_DECLARATIONS = (
    "(function_declaration name:(identifier) @name)",
    "(generator_function_declaration name:(identifier) @name)",
    "(class_declaration name:(type_identifier) @name)",
    "(abstract_class_declaration name:(type_identifier) @name)",
    "(interface_declaration name:(type_identifier) @name)",
    "(type_alias_declaration name:(type_identifier) @name)",
    "(enum_declaration name:(identifier) @name)",
    "(lexical_declaration (variable_declarator name:(identifier) @name))",
)
_TS_QUERY = _esm_top_level_query(_TS_DECLARATIONS)
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

# --- Java (.java) — classes/interfaces/enums/records only; Java has no ------
# top-level functions, so there is deliberately NO FUNCTION mapping (spec
# §5.6 / ADR 0022). Names are plain ``identifier`` in this grammar.
_JAVA_QUERY = """
(program (class_declaration name:(identifier) @name) @item)
(program (interface_declaration name:(identifier) @name) @item)
(program (enum_declaration name:(identifier) @name) @item)
(program (record_declaration name:(identifier) @name) @item)
"""
_JAVA_KINDS: Mapping[str, NodeKind] = {
    "class_declaration": NodeKind.CLASS,
    "interface_declaration": NodeKind.CLASS,
    "enum_declaration": NodeKind.CLASS,
    "record_declaration": NodeKind.CLASS,
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
    ".java": ("tree_sitter_java", "language", _JAVA_QUERY, _JAVA_KINDS),
}

# The T3 code extensions this chunker owns — all in ALLOWED_EXTENSIONS; indexed
# by default for the project scope, opt-in via YAML for the dependency scope
# (the per-scope defaults in extraction/config.py, ADR 0022).
MULTILANG_EXTENSIONS: tuple[str, ...] = tuple(LANGUAGE_SPECS)

__all__ = ("LANGUAGE_SPECS", "MULTILANG_EXTENSIONS", "LanguageSpec")
