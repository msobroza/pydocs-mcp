"""Application/domain layer for the graph explorer.

``GraphService`` turns a bundle's raw rows (read through an injected
:class:`~pydocs_mcp.ask_your_docs.bundle.BundleReader`) into the ``Node`` / ``Edge``
/ ``Graph`` value objects the UI renders. It contains all the domain logic —
module-name reconciliation, namespace navigation, edge collapsing, filtering —
and NO SQL and NO Streamlit, so it unit-tests against a fake reader.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.ask_your_docs.bundle import BundleReader
from pydocs_mcp.ask_your_docs.model import Edge, Graph, Node, NodeMeta

MAX_NEIGHBORS = 50
_STRUCTURAL = frozenset({"contains", "documents", "concerns"})
_TEST_SEGMENTS = frozenset({"tests", "test", "conftest"})


# --- pure helpers (no I/O) ---------------------------------------------------


def _short(node_id: str) -> str:
    return node_id.rsplit(".", 1)[-1] or node_id


def _prefixes(node_ids: set[str]) -> set[str]:
    """Every dotted prefix of every node id (the import-path module space)."""
    out: set[str] = set()
    for nid in node_ids:
        segs = nid.split(".")
        for i in range(1, len(segs) + 1):
            out.add(".".join(segs[:i]))
    return out


def _normalize(raw_module: str, prefixes: set[str]) -> str | None:
    """The longest suffix of a fs-derived module that is a real import path."""
    segs = raw_module.split(".")
    for i in range(len(segs)):
        cand = ".".join(segs[i:])
        if cand in prefixes:
            return cand
    return None


def _module_of(node_id: str | None, modules: list[str]) -> str | None:
    """Longest module that is the node id or a dotted prefix of it."""
    if not node_id:
        return None
    best: str | None = None
    for m in modules:
        if (node_id == m or node_id.startswith(m + ".")) and (best is None or len(m) > len(best)):
            best = m
    return best


def is_test(node_id: str) -> bool:
    """True for test modules/files: a ``test``/``tests`` package segment, a
    ``conftest`` module, or a ``test_*`` / ``*_test`` / ``*_tests`` module.

    Deliberately name-based (module/file granularity): a segment like ``testing``
    or ``contest`` is NOT a test, so real modules such as ``pkg.testing`` survive.
    """
    for seg in node_id.split("."):
        if seg in _TEST_SEGMENTS or seg.startswith("test_") or seg.endswith(("_test", "_tests")):
            return True
    return False


def type_of(node_id: str, module_set: set[str]) -> str:
    """Node category from the id alone (so callers can type without a lookup)."""
    if node_id.startswith(("doc:", "section:")):
        return "doc"
    if node_id.startswith("decision:"):
        return "decision"
    if node_id.startswith("project:"):
        return "module"
    if node_id in module_set:
        return "module"
    return "class" if _short(node_id)[:1].isupper() else "function"


def induce(g: Graph, node_types: frozenset[str], edge_kinds: frozenset[str]) -> Graph:
    """Keep nodes whose type is enabled; keep an edge when both endpoints survive
    AND (its kind is enabled OR it is structural)."""
    nodes = tuple(n for n in g.nodes if n.node_type in node_types)
    ids = {n.id for n in nodes}
    edges = tuple(
        e
        for e in g.edges
        if e.source in ids and e.target in ids and (e.kind in edge_kinds or e.kind in _STRUCTURAL)
    )
    return Graph(nodes, edges, g.truncated)


def _anchor(node_id: str | None, visible_by_len: list[str]) -> str | None:
    """The nearest visible ancestor of ``node_id``: the longest visible dotted
    prefix of it (or the id itself). ``visible_by_len`` must be sorted
    longest-first so the first match is the nearest ancestor."""
    if not node_id:
        return None
    for v in visible_by_len:
        if node_id == v or node_id.startswith(v + "."):
            return v
    return None


def _namespace_children(mods: set[str], focus: str) -> list[Node]:
    """The next dotted segment under ``focus``: a module if that exact prefix is a
    module, else a package (it has modules deeper still). ``mods`` is assumed
    already test-filtered by the caller, so test packages never appear."""
    prefix = focus + "." if focus else ""
    segs: dict[str, str] = {}
    for m in mods:
        if not m.startswith(prefix):
            continue
        rest = m[len(prefix) :]
        if not rest:
            continue
        child = prefix + rest.split(".", 1)[0]
        segs[child] = "module" if child in mods else "package"
    return [Node(cid, _short(cid), kind) for cid, kind in sorted(segs.items())]


# --- the service -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphService:
    reader: BundleReader
    hide_tests: bool = True
    """When set (default), test modules/files are excluded from every view —
    a graph-wide invariant, not something individual methods opt into."""

    def _node_ids(self) -> set[str]:
        # NB: unfiltered on purpose — the full reference space is what
        # ``_normalize`` matches fs-derived modules against. Test exclusion
        # happens on the *derived* module/neighbour sets, not here.
        ids: set[str] = set()
        for a, b, _kind in self.reader.reference_rows():
            if a:
                ids.add(a)
            if b:
                ids.add(b)
        return ids

    def _test_module_ids(self, prefixes: set[str]) -> frozenset[str]:
        """Import-path ids of test modules. Detected on the RAW fs-derived module
        (``member_rows``), which keeps its ``tests``/``test``/``conftest`` package
        prefix even when ``_normalize`` would strip it — so a src-layout / path-root
        mismatch can't smuggle a test module past a name check on the stripped id.
        Empty when not hiding tests, so callers can skip the membership test."""
        if not self.hide_tests:
            return frozenset()
        return frozenset(
            norm
            for (raw, _name, _kind) in self.reader.member_rows()
            if (norm := _normalize(raw, prefixes)) and (is_test(raw) or is_test(norm))
        )

    @staticmethod
    def _under_test_module(node_id: str, test_mods: frozenset[str]) -> bool:
        """True iff ``node_id`` is, or lives under, a test module. This is the ONLY
        way test-ness is judged for nodes/edges — deliberately module-granular, so a
        production symbol merely NAMED ``test_*`` (e.g. ``app.db.test_connection``)
        is never hidden."""
        return any(node_id == m or node_id.startswith(m + ".") for m in test_mods)

    def _module_set(self, prefixes: set[str]) -> set[str]:
        """Own modules in import-path form; test modules dropped when hiding tests.
        This is the master switch — overview nodes, namespace children and the
        module/package views all derive from here. Test-ness is judged on the RAW
        module too, so a stripped ``tests.`` prefix can't leak the module back in."""
        out: set[str] = set()
        for raw, _name, _kind in self.reader.member_rows():
            norm = _normalize(raw, prefixes)
            if not norm:
                continue
            if self.hide_tests and (is_test(raw) or is_test(norm)):
                continue
            out.add(norm)
        return out

    def modules(self) -> set[str]:
        """Own modules in import-path form (reconciled with the reference graph)."""
        return self._module_set(_prefixes(self._node_ids()))

    def _defined_members(self, module: str, prefixes: set[str]) -> list[tuple[str, str]]:
        """(name, kind) for every class/def defined in ``module``, deduped by name
        across worktree-copy module rows."""
        seen: dict[str, str] = {}
        for raw, name, kind in self.reader.member_rows():
            if _normalize(raw, prefixes) == module:
                seen.setdefault(name, kind)
        return sorted(seen.items())

    def overview(self, project: str) -> Graph:
        """The project's own modules + aggregated module->module edges."""
        prefixes = _prefixes(self._node_ids())
        modules = sorted(self._module_set(prefixes))
        nodes = tuple(Node(m, _short(m), "module") for m in modules)
        seen: set[tuple[str, str, str]] = set()
        edges: list[Edge] = []
        for from_id, to_id, kind in self.reader.reference_rows():
            a = _module_of(from_id, modules)
            b = _module_of(to_id, modules)
            if a and b and a != b and (a, b, kind) not in seen:
                seen.add((a, b, kind))
                edges.append(Edge(a, b, kind))
        return Graph(nodes, tuple(edges))

    def expand(self, node_id: str, node_type: str, kinds: frozenset[str]) -> Graph:
        """Module -> members (contains); class/function -> reference neighbours
        (filtered to ``kinds``, capped); doc file -> its sections."""
        if node_type == "doc":
            secs = self.reader.markdown_sections(node_id.removeprefix("doc:"))
            nodes = tuple(Node(f"section:{cid}", title, "doc") for cid, title in secs)
            return Graph(nodes, tuple(Edge(node_id, n.id, "contains") for n in nodes))

        prefixes = _prefixes(self._node_ids())
        if node_type == "module":
            # A surviving module is non-test, so ALL its members show — including a
            # production def merely named ``test_*``. Test filtering is module-level.
            members = self._defined_members(node_id, prefixes)
            member_nodes = tuple(
                Node(f"{node_id}.{name}", name, "class" if kind == "class" else "function")
                for name, kind in members
            )
            return Graph(member_nodes, tuple(Edge(node_id, n.id, "contains") for n in member_nodes))

        module_set = self._module_set(prefixes)
        test_mods = self._test_module_ids(prefixes)
        edges: list[Edge] = []
        nodes: dict[str, Node] = {}
        total = 0
        for from_id, to_id, kind in self.reader.references_of(node_id):
            if kind not in kinds or not to_id:
                continue
            other = to_id if from_id == node_id else from_id
            if other == node_id or self._under_test_module(other, test_mods):
                continue
            total += 1
            if len(edges) >= MAX_NEIGHBORS:
                continue
            edges.append(Edge(from_id, to_id, kind))
            nodes[other] = Node(other, _short(other), type_of(other, module_set))
        return Graph(tuple(nodes.values()), tuple(edges), truncated=max(0, total - len(edges)))

    def edges_for(self, visible: set[str], kinds: frozenset[str]) -> tuple[Edge, ...]:
        """Reference edges among the visible nodes, each raw edge collapsed to the
        nearest visible *ancestor* (longest visible dotted prefix of an endpoint).
        Edges whose collapsed anchor is a test module are dropped (defensive — the
        visible set is normally already test-free)."""
        test_mods = self._test_module_ids(_prefixes(self._node_ids()))
        vis = sorted(visible, key=len, reverse=True)

        seen: set[tuple[str, str, str]] = set()
        out: list[Edge] = []
        for from_id, to_id, kind in self.reader.reference_rows():
            if kind not in kinds:
                continue
            a = _anchor(from_id, vis)
            b = _anchor(to_id, vis)
            if not (a and b) or a == b or (a, b, kind) in seen:
                continue
            if self._under_test_module(a, test_mods) or self._under_test_module(b, test_mods):
                continue
            seen.add((a, b, kind))
            out.append(Edge(a, b, kind))
        return tuple(out)

    def children(self, focus: str, content: str = "Codebase") -> tuple[Node, ...]:
        """Direct namespace children of ``focus`` for the zoom view (root -> top
        packages/modules -> members -> methods; doc file -> sections). Test
        modules/files are excluded per the service's ``hide_tests`` flag."""
        if focus.startswith("doc:"):
            return self.expand(focus, "doc", frozenset()).nodes

        ids = self._node_ids()
        prefixes = _prefixes(ids)
        mods = self._module_set(prefixes)

        if focus == "":
            nodes: list[Node] = []
            if content != "Documentation":
                nodes += _namespace_children(mods, "")
            if content != "Codebase":
                nodes += self._doc_decision_children()
            return tuple(nodes)

        if focus in mods:  # module -> its defined members (module is non-test)
            members = self._defined_members(focus, prefixes)
            return tuple(
                Node(f"{focus}.{name}", name, "class" if kind == "class" else "function")
                for name, kind in members
            )

        if any(m.startswith(focus + ".") for m in mods):  # a package prefix
            return tuple(_namespace_children(mods, focus))

        # a class (or leaf) -> its methods (ids one segment beyond ``focus``)
        methods = sorted(
            nid for nid in ids if nid.startswith(focus + ".") and "." not in nid[len(focus) + 1 :]
        )
        return tuple(Node(mid, _short(mid), "function") for mid in methods)

    def _doc_decision_children(self) -> list[Node]:
        docs = [Node(f"doc:{f}", f, "doc") for f in self.reader.markdown_files()]
        decisions = [
            Node(f"decision:{cid}", title, "decision") for cid, title in self.reader.decisions()
        ]
        return docs + decisions

    def node_meta(self, node_id: str, node_type: str) -> NodeMeta | None:
        if node_type in {"class", "function"}:
            module_part, _, name = node_id.rpartition(".")
            row = self.reader.find_member(name, module_part)
            if row is None:
                return NodeMeta(node_id, node_type, name or node_id, "")
            body = "\n\n".join(part for part in (row[1], row[2]) if part)
            return NodeMeta(node_id, node_type, row[0], body)
        if node_type == "module":
            count = len(self._defined_members(node_id, _prefixes(self._node_ids())))
            return NodeMeta(node_id, "module", node_id, f"{count} members")
        if node_type == "package":
            n = sum(1 for m in self.modules() if m == node_id or m.startswith(node_id + "."))
            return NodeMeta(node_id, "package", node_id, f"{n} modules")
        if node_type in {"doc", "decision"}:
            if node_id.startswith(("section:", "decision:")):
                row = self.reader.chunk(int(node_id.split(":", 1)[1]))
                if row:
                    return NodeMeta(node_id, node_type, row[0], row[1] or "")
            if node_id.startswith("doc:"):
                return NodeMeta(node_id, "doc", node_id.removeprefix("doc:"), "Markdown file")
        return None
