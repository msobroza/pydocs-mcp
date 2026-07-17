"""ReferenceResolver tests (spec §7.2 — rules A, B, C, D, E + F20 + self.X.Y)."""

from __future__ import annotations

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.reference_resolver import ReferenceResolver
from pydocs_mcp.storage.node_reference import NodeReference


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="x",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


def test_rule_e_no_match_leaves_to_node_id_none():
    """Spec §7.2 Rule E — no match → to_node_id stays None."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.mod.fn", "pkg.helpers.compute"},
        aliases={},
    )
    out = resolver.resolve(
        [
            _ref(from_node_id="pkg.mod.fn", to_name="totally.unknown"),
        ]
    )
    assert out[0].to_node_id is None


def test_rule_b_exact_match_sets_to_node_id():
    """Spec §7.2 Rule B — exact qname match → to_node_id = that qname."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.helpers.compute"},
        aliases={},
    )
    out = resolver.resolve(
        [
            _ref(to_name="pkg.helpers.compute"),
        ]
    )
    assert out[0].to_node_id == "pkg.helpers.compute"


def test_rule_c_suffix_match_within_from_package():
    """Spec §7.2 Rule C — strict dotted suffix within from_package → resolve.

    `to_name="compute"` matches `pkg.helpers.compute` if it's the only
    qname in `pkg.*` ending in `.compute`.
    """
    resolver = ReferenceResolver(
        qname_universe={"pkg.helpers.compute", "other.unrelated.compute"},
        aliases={},
    )
    out = resolver.resolve(
        [
            _ref(from_node_id="pkg.mod.fn", to_name="compute"),
        ]
    )
    # Only one qname matches within `pkg.*`; resolved.
    assert out[0].to_node_id == "pkg.helpers.compute"


def test_rule_d_ambiguous_suffix_leaves_none():
    """AC #8 — Rule D: when suffix matches MULTIPLE qnames within
    from_package, the resolver leaves to_node_id = None deterministically.

    This prevents nondeterministic "first match wins" between Python's
    inherent dict-iteration order across runs.
    """
    resolver = ReferenceResolver(
        qname_universe={"pkg.a.Foo.bar", "pkg.b.Foo.bar"},
        aliases={},
    )
    out = resolver.resolve(
        [
            _ref(from_node_id="pkg.something.x", to_name="bar"),
        ]
    )
    assert out[0].to_node_id is None


def test_rule_a_alias_rewrites_then_resolves_exactly():
    """AC #6 — Rule A: alias rewrite first, then exact match.

    `from pkg.helpers import compute as do_it` makes
    `do_it(42)` resolve to `pkg.helpers.compute`.
    """
    resolver = ReferenceResolver(
        qname_universe={"pkg.helpers.compute"},
        aliases={"pkg.utils": {"do_it": "pkg.helpers.compute"}},
    )
    out = resolver.resolve(
        [
            _ref(from_node_id="pkg.utils.runner", to_name="do_it"),
        ]
    )
    assert out[0].to_node_id == "pkg.helpers.compute"


def test_rule_a_alias_with_dotted_remainder():
    """Spec §7.2 — `do_it.something()` after `import X.Y as do_it` → X.Y.something."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.real.something"},
        aliases={"pkg.utils": {"R": "pkg.real"}},
    )
    out = resolver.resolve(
        [
            _ref(from_node_id="pkg.utils.fn", to_name="R.something"),
        ]
    )
    assert out[0].to_node_id == "pkg.real.something"


def test_f20_prefers_bare_module_over_md_or_ipynb():
    """AC §7.2 step 4 — when multiple qnames differ ONLY by trailing
    `.md` / `.ipynb` synthetic suffix, prefer the bare (.py module)
    candidate. CALLS / IMPORTS / INHERITS don't target docs/notebooks."""
    resolver = ReferenceResolver(
        qname_universe={
            "pkg.helpers",  # .py module
            "pkg.helpers.md",  # markdown sibling
            "pkg.helpers.ipynb",  # notebook sibling
        },
        aliases={},
    )
    out = resolver.resolve(
        [
            _ref(to_name="pkg.helpers"),
        ]
    )
    assert out[0].to_node_id == "pkg.helpers"


def test_self_dot_short_circuit_leaves_none():
    """AC #9 — to_name starting with 'self.' short-circuits, to_node_id stays None."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.cls.client.fetch"},  # plausible target
        aliases={},
    )
    out = resolver.resolve(
        [
            _ref(from_node_id="pkg.cls.method", to_name="self.client.fetch"),
        ]
    )
    assert out[0].to_node_id is None
    # The to_name is preserved verbatim — users see "self.client.fetch" in callees.
    assert out[0].to_name == "self.client.fetch"


def test_inherits_resolution_works_same_rules():
    """Rules A-E apply uniformly across CALLS / IMPORTS / INHERITS."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.base.Base"},
        aliases={},
    )
    out = resolver.resolve(
        [
            _ref(to_name="pkg.base.Base", kind=ReferenceKind.INHERITS),
        ]
    )
    assert out[0].to_node_id == "pkg.base.Base"


def test_unresolved_external_stays_unresolved():
    """AC #10 — `os.path.join` not in qname_universe → to_node_id stays None,
    queryable by to_name."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.something"},
        aliases={},
    )
    out = resolver.resolve(
        [
            _ref(to_name="os.path.join"),
        ]
    )
    assert out[0].to_node_id is None
    assert out[0].to_name == "os.path.join"


def test_strict_suffix_off_skips_rule_c() -> None:
    """When strict_suffix=False, Rule C (suffix-within-package) does NOT fire.

    Setup: a reference like ``compute`` from package ``pkg`` and a single
    qname ``pkg.helpers.compute`` in the universe. With strict_suffix=True
    Rule C resolves it. With False, only Rule B (exact match) runs — no
    resolution because the to_name doesn't match the full qname.
    """
    qnames = frozenset({"pkg.helpers.compute"})
    resolver_strict = ReferenceResolver(
        qname_universe=qnames,
        aliases={},
        class_attribute_types={},
        strict_suffix=True,
    )
    resolver_loose = ReferenceResolver(
        qname_universe=qnames,
        aliases={},
        class_attribute_types={},
        strict_suffix=False,
    )
    ref = NodeReference(
        from_package="pkg",
        from_node_id="pkg.module.fn",
        to_name="compute",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    out_strict = resolver_strict.resolve([ref])
    out_loose = resolver_loose.resolve([ref])
    # Rule C resolves under strict; only Rule B runs under loose.
    assert out_strict[0].to_node_id == "pkg.helpers.compute"
    assert out_loose[0].to_node_id is None


# ── Rule C for project code (ADR 0004 fix iii) ───────────────────────────
#
# Probe finding: the Rule C candidate filter requires qnames prefixed by
# from_package, which is never true for '__project__' (project qnames are
# prefixless) — Rule C was structurally dead for project code. The fix
# filters against the project's own qname universe instead; Rule D
# conservatism (ambiguity → None) is preserved.


def test_rule_c_project_package_unique_suffix_resolves():
    resolver = ReferenceResolver(
        qname_universe={"probepkg.mod.one_of_a_kind", "stdlib.thing"},
        project_qnames=frozenset({"probepkg.mod.one_of_a_kind"}),
    )
    out = resolver.resolve(
        [
            _ref(
                from_package="__project__",
                from_node_id="probepkg.mod.entry",
                to_name="one_of_a_kind",
            )
        ]
    )
    assert out[0].to_node_id == "probepkg.mod.one_of_a_kind"


def test_rule_c_project_package_ambiguous_suffix_stays_none():
    """Rule D conservatism — two project qnames share the suffix → None."""
    qnames = {"probepkg.mod.Alpha.dup", "probepkg.mod.Beta.dup"}
    resolver = ReferenceResolver(
        qname_universe=qnames,
        project_qnames=frozenset(qnames),
    )
    out = resolver.resolve(
        [
            _ref(
                from_package="__project__",
                from_node_id="probepkg.mod.entry",
                to_name="dup",
            )
        ]
    )
    assert out[0].to_node_id is None


def test_rule_c_project_package_ignores_non_project_universe():
    """A dependency qname with the same suffix must not leak into the
    __project__ scope — the filter is project-tree membership, not the
    whole universe."""
    resolver = ReferenceResolver(
        qname_universe={"somedep.util.compute"},
        project_qnames=frozenset(),
    )
    out = resolver.resolve(
        [
            _ref(
                from_package="__project__",
                from_node_id="probepkg.mod.entry",
                to_name="compute",
            )
        ]
    )
    assert out[0].to_node_id is None


def test_rule_c_dependency_prefix_filter_unchanged_by_project_qnames():
    """The project scope must not widen DEPENDENCY-package Rule C."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.helpers.compute", "probepkg.local.compute"},
        project_qnames=frozenset({"probepkg.local.compute"}),
    )
    out = resolver.resolve([_ref(from_package="pkg", to_name="compute")])
    assert out[0].to_node_id == "pkg.helpers.compute"
