"""ReferenceResolver — alias-aware exact + suffix match (spec §7.2).

Runs once per ``IndexingService.reindex_package(...)`` call, AFTER the
chunker pass has emitted unresolved candidates. Mutates each candidate's
``to_node_id`` from ``None`` to the resolved qname (when found) or
leaves it as ``None`` (Rule E — no match).

Construction is cheap (just dict refs + a frozenset of qnames). The
resolver owns no I/O — the caller loads the qname universe and alias
map from ``uow.trees.load_all_in_package(...)`` and the alias table
populated by the capture pass, then invokes ``resolve(...)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydocs_mcp.storage.node_reference import NodeReference

if TYPE_CHECKING:
    pass


# Suffixes that mark synthetic doc/notebook module ids (sub-PR #5 F20).
# CALLS / IMPORTS / INHERITS edges don't target these — when both a bare
# `pkg.helpers` and `pkg.helpers.md` exist in the universe, the resolver
# prefers the bare candidate.
_SYNTHETIC_MODULE_SUFFIXES: tuple[str, ...] = (".md", ".ipynb")


@dataclass(frozen=True, slots=True)
class ReferenceResolver:
    """Resolves NodeReference.to_name → to_node_id using a static qname universe.

    ``qname_universe`` is the set of all indexed qnames across every
    package (currently-indexed and the freshly-reindexed one). Built by
    the caller from ``uow.trees.load_all_in_package(...)`` per spec §7.2
    step 2.

    ``aliases`` is the per-module alias table built by
    ``capture_imports`` during the chunker pass. Keys are module qnames
    (matching from_node_id's leading dotted segments); values are
    ``{alias: dotted_target}`` maps for that module.

    ``class_attribute_types`` is the per-class ``self.X`` attribute-type
    table built by ``capture_self_attribute_types``. Keys are CLASS qnames
    (e.g. ``pkg.mod.Cls``); values are ``{attr_name: type_qname}`` maps.
    Drives Rule 0: ``self.X.Y`` is rewritten to ``<type>.Y`` before Rule 5
    short-circuits the reference. Empty (the default) preserves the
    pre-#5d behavior of unconditional ``self.*`` short-circuit.

    The frozen+slotted dataclass shape lets the resolver be re-used
    safely across packages within the same `reindex_package` call.
    """

    qname_universe: frozenset[str]
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)
    class_attribute_types: dict[str, dict[str, str]] = field(default_factory=dict)
    # WHY: ablation knob — when False, Rule C (strict-suffix-within-package)
    # and Rule D (ambiguous-suffix) are skipped entirely; only Rules 0, A,
    # B, F20 and Rule 5 short-circuit run. Lets the benchmark harness
    # measure Rule C's contribution to AC #15 resolution rate against a
    # baseline. Default True preserves pre-PR behavior.
    strict_suffix: bool = True

    def resolve(self, refs: Sequence[NodeReference]) -> list[NodeReference]:
        """Return a NEW list of NodeReferences with to_node_id filled.

        Does NOT mutate inputs — NodeReference is frozen. Each output ref
        is `dataclasses.replace(input, to_node_id=resolved_qname or None)`.
        """
        from dataclasses import replace

        result: list[NodeReference] = []
        for ref in refs:
            resolved = self._resolve_one(ref)
            result.append(replace(ref, to_node_id=resolved))
        return result

    def _resolve_one(self, ref: NodeReference) -> str | None:  # noqa: C901 — sequential resolution rules (Rule 0 + A → B/F20 → C → D → E) are inherently sequential decision points; splitting hides the priority order

        to_name = ref.to_name

        # Rule 0 — self.X.Y inference. When ``self.X`` was typed at
        # ``__init__`` time, rewrite to ``<type>.Y`` and let the normal
        # rules (A → B/F20 → C → D → E) resolve the rewritten target.
        if to_name.startswith("self."):
            inferred = self._infer_self_type(ref.from_node_id, to_name)
            if inferred is not None:
                to_name = inferred

        # Rule 5 — self.X.Y short-circuit for anything Rule 0 couldn't
        # rewrite. Recorded verbatim so users see the intent in callees.
        if to_name.startswith("self."):
            return None

        # Rule A — apply alias rewriting before exact/suffix lookup.
        module_of_from_node = _module_part_of(ref.from_node_id)
        alias_map = self.aliases.get(module_of_from_node, {})
        leading = to_name.split(".", 1)[0]
        if leading in alias_map:
            rest = to_name[len(leading) :]  # includes leading "." or empty
            to_name = alias_map[leading] + rest

        # Rule B — exact qname match, then F20-disambiguate.
        if to_name in self.qname_universe:
            return to_name
        # F20: prefer bare candidate over .md / .ipynb siblings only when
        # to_name matches a synthetic-suffixed candidate (rare but spec
        # asks for it). Implementation: if to_name has a synthetic suffix
        # and the bare form is in the universe, return the bare form.
        for suffix in _SYNTHETIC_MODULE_SUFFIXES:
            if to_name.endswith(suffix):
                bare = to_name[: -len(suffix)]
                if bare in self.qname_universe:
                    return bare

        # Rule C — strict dotted suffix within from_package. Gated by the
        # ``strict_suffix`` ablation knob: when False, the resolver skips
        # straight to Rule E (no match) so the benchmark harness can
        # measure Rule C's contribution to AC #15 resolution rate.
        # Build candidates = {qname in universe whose package prefix == from_package
        #                     AND qname endswith ".<to_name>" OR qname == to_name}.
        if self.strict_suffix:
            candidates: list[str] = []
            suffix_dot = "." + to_name
            for qname in self.qname_universe:
                if not qname.startswith(ref.from_package + ".") and qname != ref.from_package:
                    continue
                if qname == to_name or qname.endswith(suffix_dot):
                    candidates.append(qname)
            if len(candidates) == 1:
                return candidates[0]
            # Rule D — ambiguous suffix (>1 candidate) leaves None deterministically.
            if len(candidates) > 1:
                return None

        # Rule E — no match.
        return None

    def _infer_self_type(self, from_node_id: str, to_name: str) -> str | None:
        """Rewrite ``self.X[.Y]`` to a dotted target when self's type is known.

        Two sources of evidence (in priority order):

        1. **Attribute-typed**: ``self.X`` has a known type from the
           ``class_attribute_types`` table (built from class-body
           AnnAssigns and ``__init__`` patterns B/C/D/E). Rewrites
           ``self.X.Y`` to ``<type>.Y`` and trusts the type — the
           normal rules (A → B/F20 → C → D → E) handle the rewritten
           target. Alias rewrites can still apply on the type.

        2. **Self-as-class**: when no attribute type is known but the
           rewritten target ``<enclosing_class>.<after_self>`` exists
           verbatim in the qname universe, return it. Covers the common
           ``self.method()`` pattern of methods calling other methods on
           the same class. Gated on Rule B (exact match) to avoid Rule C
           suffix-match false positives.

        Returns None when neither source applies; the caller falls
        through to Rule 5.
        """
        cls_qname = _enclosing_class_qname(from_node_id)
        if cls_qname is None:
            return None
        # Strip the literal "self." prefix once, then split on the FIRST
        # dot so chained access (self.X.Y.Z) preserves the remainder.
        # Defensive: a degenerate "self." input yields ``head == ""``,
        # which misses ``attrs.get("")`` and produces a rewritten string
        # ending in a trailing dot that can't appear in the universe —
        # both inference paths fail-safe in that case.
        after_self = to_name[5:]
        head, _sep, rest = after_self.partition(".")

        # 1. Attribute-typed inference.
        attrs = self.class_attribute_types.get(cls_qname)
        if attrs is not None:
            type_qname = attrs.get(head)
            if type_qname is not None:
                return f"{type_qname}.{rest}" if rest else type_qname

        # 2. Self-as-class fallback — guard with universe membership so
        #    only Rule B-equivalent rewrites get through. Rule C / D
        #    matches via this path would risk false positives.
        rewritten = f"{cls_qname}.{after_self}"
        if rewritten in self.qname_universe:
            return rewritten
        return None


def _enclosing_class_qname(from_node_id: str) -> str | None:
    """Return the class qname enclosing ``from_node_id``, or None.

    The chunker stamps method qnames as ``pkg.mod.ClassName.method``; the
    second-to-last segment is the class name and starts uppercase by PEP 8.
    Free functions and module-level captures fall through with None.

    Same heuristic as ``_module_part_of`` (one segment up), inverted to
    return the class-qname rather than the module-qname.

    Safety: false negatives (e.g. a snake_case class ``my_helper`` or an
    UPPERCASE module ``pkg.HTTP.client``) simply miss the table lookup
    and Rule 5 short-circuits the reference unchanged. False positives
    are impossible to leak into a real resolution because both inference
    paths in :meth:`ReferenceResolver._infer_self_type` either consult
    the captured ``class_attribute_types`` table (which only contains
    real classes) or gate on qname-universe membership (the self-as-
    class fallback). Misfires lose accuracy, never correctness.

    TODO: this heuristic depends on the chunker stamping method qnames
    as ``<module>.<Class>.<method>``. If
    :class:`~pydocs_mcp.extraction.strategies.chunkers.ast_python.AstPythonChunker`
    ever changes that convention (e.g. to support nested classes inside
    functions, or PEP 695 generic params in the qname), update this
    heuristic in lockstep.
    """
    parts = from_node_id.split(".")
    if len(parts) >= 3 and parts[-2] and parts[-2][0].isupper():
        return ".".join(parts[:-1])
    return None


def _module_part_of(node_id: str) -> str:
    """Return the dotted prefix that names the MODULE containing node_id.

    The from_node_id is the chunker's qname for the source node —
    ``pkg.mod.fn`` (function) or ``pkg.mod.ClassName.method`` or
    ``pkg.mod`` (the module itself for IMPORTS). The alias table is
    keyed by MODULE qname, not by symbol qname.

    Implementation: walk-from-left over the dotted parts and return the
    longest prefix that exists in self.aliases — but we don't have access
    to self.aliases here. Simpler: return everything before the LAST
    segment for symbols inside a module. For module-level (IMPORTS from
    `pkg.mod`), the whole thing IS the module qname. The resolver
    handles both via `self.aliases.get(...)` which returns {} on miss —
    a wrong split silently misses the alias but never crashes.
    """
    # Heuristic: if the second-to-last segment starts with a capital,
    # assume it's a class (e.g. ``pkg.mod.Cls.method``); strip TWO segments.
    # Otherwise strip ONE (e.g. ``pkg.mod.fn`` → ``pkg.mod``). For module-
    # level captures (from_node_id == module qname) the caller passes the
    # full module qname through; this function's output won't match the
    # alias table for those cases, which is fine — they don't need
    # rewriting (the alias table is consulted for SYMBOL captures inside
    # a module, not for module-level IMPORTS captures whose to_name is
    # already absolute).
    parts = node_id.split(".")
    if len(parts) >= 2 and parts[-2] and parts[-2][0].isupper():
        return ".".join(parts[:-2])
    return ".".join(parts[:-1])
