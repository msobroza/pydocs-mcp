"""PageIndex node enrichment: signature title, decorators, docstring excerpt.

The tree-reasoning step renders each code node with a real signature in the
``title`` (decorators prefixed) and an optional bounded ``doc`` excerpt of
the docstring, so the LLM can match queries about inputs/outputs, author
intent, and role markers. These tests pin the render-side helpers and the
two new YAML-tunable step params (``doc_excerpt`` / ``doc_excerpt_max_chars``).
"""

from __future__ import annotations

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.strategies.chunkers._shared import _header_from_text
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from pydocs_mcp.retrieval.tree_prompt.doc_excerpt import (
    DEFAULT_DOC_EXCERPT,
    DEFAULT_DOC_EXCERPT_MAX_CHARS,
    doc_excerpt,
)
from pydocs_mcp.retrieval.tree_prompt.pageindex_serializer import (
    TITLE_MAX_CHARS,
    enriched_title,
    pageindex_with_qname,
)
from pydocs_mcp.retrieval.tree_prompt.tree_budget_fitter import prune_to_node_budget


def _node(
    *,
    kind: NodeKind = NodeKind.FUNCTION,
    title: str = "def foo()",
    text: str = "def foo(a: int) -> int:\n    return a",
    summary: str = "",
    extra: dict | None = None,
    children: tuple[DocumentNode, ...] = (),
) -> DocumentNode:
    return DocumentNode(
        node_id="n",
        qualified_name="pkg.mod.foo",
        title=title,
        kind=kind,
        source_path="m.py",
        start_line=1,
        end_line=2,
        text=text,
        content_hash="",
        summary=summary,
        extra_metadata=extra if extra is not None else {},
        parent_id=None,
        children=children,
    )


# ── _header_from_text ─────────────────────────────────────────────────────


def test_header_one_liner_strips_trailing_colon_and_body() -> None:
    assert _header_from_text("def foo(a: int) -> Time:\n    return a") == "def foo(a: int) -> Time"


def test_header_multiline_signature_is_assembled() -> None:
    text = "def foo(\n    a: int,\n    b: str,\n) -> Time:\n    return a"
    assert _header_from_text(text) == "def foo(a: int, b: str,) -> Time"


def test_header_return_annotation_with_brackets() -> None:
    assert _header_from_text("def f() -> Dict[str, int]:\n    ...") == "def f() -> Dict[str, int]"


def test_header_one_line_body_after_colon() -> None:
    assert _header_from_text("def f(): return 1") == "def f()"


def test_header_class_with_bases() -> None:
    assert _header_from_text("class Foo(Base1, Base2):\n    pass") == "class Foo(Base1, Base2)"


def test_header_class_without_bases() -> None:
    assert _header_from_text("class Foo:\n    pass") == "class Foo"


def test_header_param_annotation_colon_is_not_a_terminator() -> None:
    assert _header_from_text("def f(a: int = 1):\n    ...") == "def f(a: int = 1)"


# ── _enriched_title ───────────────────────────────────────────────────────


def test_enriched_title_prefixes_decorators_before_signature() -> None:
    node = _node(
        text="async def login(req: Request) -> Response:\n    ...",
        title="async def login()",
        extra={"decorators": ("@app.route('/login')",)},
    )
    assert enriched_title(node) == "@app.route('/login') async def login(req: Request) -> Response"


def test_enriched_title_without_decorators_is_just_signature() -> None:
    node = _node(text="def foo(a: int) -> int:\n    return a", title="def foo()")
    assert enriched_title(node) == "def foo(a: int) -> int"


def test_enriched_title_class_uses_bases() -> None:
    node = _node(kind=NodeKind.CLASS, text="class Foo(Base):\n    pass", title="class Foo")
    assert enriched_title(node) == "class Foo(Base)"


def test_enriched_title_non_code_kind_passes_through_title() -> None:
    node = _node(kind=NodeKind.MODULE, text="whatever text", title="pkg.mod")
    assert enriched_title(node) == "pkg.mod"


def test_enriched_title_falls_back_when_text_is_not_a_signature() -> None:
    # Synthetic node whose text isn't real source: header derivation must
    # not fire; fall back to the plain title.
    node = _node(text="body of foo", title="foo")
    assert enriched_title(node) == "foo"


def test_enriched_title_is_bounded() -> None:
    long_sig = "def f(" + ", ".join(f"a{i}: int" for i in range(200)) + "):\n    ..."
    node = _node(text=long_sig, title="def f()")
    assert len(enriched_title(node)) <= TITLE_MAX_CHARS


# ── _doc_excerpt ──────────────────────────────────────────────────────────


def test_doc_excerpt_off_returns_empty() -> None:
    assert doc_excerpt("Summary.\n\nArgs:\n    x: y", "off", 240) == ""


def test_doc_excerpt_empty_docstring() -> None:
    assert doc_excerpt("", "sections", 240) == ""
    assert doc_excerpt("   \n  ", "sections", 240) == ""


def test_doc_excerpt_single_line_is_just_that_line() -> None:
    assert doc_excerpt("Authenticate a user.", "sections", 240) == "Authenticate a user."


def test_doc_excerpt_google_sections() -> None:
    doc = (
        "Authenticate a user.\n\n"
        "Args:\n    req: the request\n"
        "Returns:\n    a session token\n"
        "Raises:\n    AuthError: when invalid\n"
    )
    out = doc_excerpt(doc, "sections", 400)
    assert out.startswith("Authenticate a user.")
    assert "Args:" in out and "req: the request" in out
    assert "Returns:" in out and "a session token" in out
    assert "Raises:" in out and "AuthError: when invalid" in out


def test_doc_excerpt_sphinx_field_list() -> None:
    doc = "Summary.\n\n:param req: the request\n:returns: a token\n:raises AuthError: bad\n"
    out = doc_excerpt(doc, "sections", 400)
    assert ":param req: the request" in out
    assert ":returns: a token" in out
    assert ":raises AuthError: bad" in out


def test_doc_excerpt_numpy_sections() -> None:
    doc = (
        "Summary.\n\n"
        "Parameters\n----------\nreq : Request\n    the request\n\n"
        "Returns\n-------\nToken\n    a token\n"
    )
    out = doc_excerpt(doc, "sections", 400)
    assert out.startswith("Summary.")
    assert "Parameters" in out and "req : Request" in out
    assert "Returns" in out and "a token" in out


def test_doc_excerpt_full_mode_keeps_body() -> None:
    out = doc_excerpt("Line one.\n\nMore detail here.", "full", 400)
    assert "Line one." in out and "More detail here." in out


def test_doc_excerpt_is_bounded() -> None:
    assert len(doc_excerpt("word " * 500, "full", 50)) <= 50


def test_doc_excerpt_unknown_mode_defaults_to_sections() -> None:
    doc = "Summary.\nArgs:\n    x: y\n"
    assert doc_excerpt(doc, "bogus", 240) == doc_excerpt(doc, "sections", 240)


# ── _pageindex_with_qname ─────────────────────────────────────────────────


def test_pageindex_emits_enriched_title_and_no_node_id() -> None:
    node = _node(
        text="def foo(a: int) -> int:\n    return a",
        title="def foo()",
        extra={"decorators": ("@staticmethod",), "docstring": ""},
    )
    out = pageindex_with_qname(node)
    assert out["title"] == "@staticmethod def foo(a: int) -> int"
    assert "node_id" not in out


def test_pageindex_includes_doc_when_richer_than_summary() -> None:
    node = _node(
        summary="Authenticate a user.",
        extra={
            "docstring": "Authenticate a user.\n\nArgs:\n    req: the request\n",
        },
    )
    out = pageindex_with_qname(node, doc_mode="sections", doc_max_chars=240)
    assert "doc" in out
    assert "req: the request" in out["doc"]


def test_pageindex_omits_doc_when_equal_to_summary() -> None:
    node = _node(
        summary="Authenticate a user.",
        extra={"docstring": "Authenticate a user."},
    )
    out = pageindex_with_qname(node, doc_mode="sections", doc_max_chars=240)
    assert "doc" not in out


def test_pageindex_omits_doc_when_off() -> None:
    node = _node(
        summary="s",
        extra={"docstring": "Summary.\n\nArgs:\n    x: y\n"},
    )
    out = pageindex_with_qname(node, doc_mode="off", doc_max_chars=240)
    assert "doc" not in out


def test_pageindex_recurses_into_children() -> None:
    child = _node(title="def child()", text="def child(x: str) -> None:\n    ...")
    parent = _node(kind=NodeKind.MODULE, title="pkg.mod", text="mod", children=(child,))
    out = pageindex_with_qname(parent)
    assert out["nodes"][0]["title"] == "def child(x: str) -> None"


# ── budget pruning preserves doc ──────────────────────────────────────────


def test_prune_preserves_doc_field() -> None:
    forest = [
        {
            "qualified_name": "a",
            "title": "def a()",
            "kind": "function",
            "summary": "s",
            "doc": "Args: x",
            "nodes": [],
        }
    ]
    pruned = prune_to_node_budget(forest, 10)
    assert pruned[0]["doc"] == "Args: x"


# ── step params: to_dict / from_dict ──────────────────────────────────────


def _step(**kw) -> LlmTreeReasoningStep:
    from tests._fakes import FakeLlmClient, make_fake_uow_factory

    return LlmTreeReasoningStep(
        llm_client=FakeLlmClient(responses={}),
        uow_factory=make_fake_uow_factory(),
        **kw,
    )


def _ctx():
    from pydocs_mcp.retrieval.serialization import BuildContext
    from tests._fakes import FakeLlmClient, make_fake_uow_factory

    return BuildContext(llm_client=FakeLlmClient(responses={}), uow_factory=make_fake_uow_factory())


def test_defaults_are_sections_and_240() -> None:
    step = _step()
    assert step.doc_excerpt == DEFAULT_DOC_EXCERPT == "sections"
    assert step.doc_excerpt_max_chars == DEFAULT_DOC_EXCERPT_MAX_CHARS == 240


def test_to_dict_omits_default_doc_params() -> None:
    d = _step().to_dict()
    assert "doc_excerpt" not in d
    assert "doc_excerpt_max_chars" not in d


def test_to_dict_includes_non_default_doc_params() -> None:
    d = _step(doc_excerpt="full", doc_excerpt_max_chars=500).to_dict()
    assert d["doc_excerpt"] == "full"
    assert d["doc_excerpt_max_chars"] == 500


def test_from_dict_reads_doc_params() -> None:
    step = LlmTreeReasoningStep.from_dict(
        {"type": "llm_tree_reasoning", "doc_excerpt": "full", "doc_excerpt_max_chars": 500},
        _ctx(),
    )
    assert step.doc_excerpt == "full"
    assert step.doc_excerpt_max_chars == 500


def test_from_dict_rejects_unknown_doc_excerpt_mode() -> None:
    with pytest.raises(ValueError, match="doc_excerpt"):
        LlmTreeReasoningStep.from_dict(
            {"type": "llm_tree_reasoning", "doc_excerpt": "bogus"}, _ctx()
        )


@pytest.mark.parametrize("bad", [0, -5])
def test_from_dict_rejects_nonpositive_doc_excerpt_max_chars(bad: int) -> None:
    # A negative cap would become a silent negative slice (drops the tail
    # instead of capping) — fail fast at YAML-build time instead.
    with pytest.raises(ValueError, match="doc_excerpt_max_chars"):
        LlmTreeReasoningStep.from_dict(
            {"type": "llm_tree_reasoning", "doc_excerpt_max_chars": bad}, _ctx()
        )


def test_doc_excerpt_negative_cap_is_clamped_to_empty() -> None:
    # Defense in depth: even a direct (non-YAML) construction can't produce a
    # negative slice — the "always capped" contract holds.
    assert doc_excerpt("abcdefghij", "full", -3) == ""


def test_from_dict_rejects_removed_max_tree_words_key() -> None:
    # max_tree_words was renamed to max_tree_tokens (word budget -> real
    # tiktoken token budget). yaml_kwargs() only reads _YAML_KEYS, so an old
    # config's max_tree_words is silently dropped there — this hand-written
    # guard in from_dict is the only thing that stops a stale deployment from
    # falling back to the auto-derived budget with no error and no warning.
    with pytest.raises(ValueError, match="max_tree_tokens"):
        LlmTreeReasoningStep.from_dict(
            {"type": "llm_tree_reasoning", "max_tree_words": 50000}, _ctx()
        )


def test_from_dict_requires_uow_factory() -> None:
    from pydocs_mcp.retrieval.serialization import BuildContext
    from tests._fakes import FakeLlmClient

    with pytest.raises(ValueError, match="uow_factory"):
        LlmTreeReasoningStep.from_dict(
            {"type": "llm_tree_reasoning"},
            BuildContext(llm_client=FakeLlmClient(responses={}), uow_factory=None),
        )


# ── _doc_sections: unrecognized sections must NOT leak ─────────────────────


def test_doc_excerpt_excludes_unrecognized_numpy_sections() -> None:
    doc = (
        "Connect to the server.\n\n"
        "See Also\n--------\ndisconnect : Tears down the connection.\n\n"
        "Notes\n-----\nImplementation detail nobody needs.\n"
    )
    out = doc_excerpt(doc, "sections", 240)
    assert out == "Connect to the server."
    assert "Notes" not in out
    assert "See Also" not in out
    assert "disconnect" not in out


def test_doc_excerpt_recognized_numpy_section_still_captured_after_fix() -> None:
    doc = "Summary.\n\nReturns\n-------\nToken\n    a token\n"
    out = doc_excerpt(doc, "sections", 240)
    assert "Returns" in out and "a token" in out


def test_doc_excerpt_blank_line_terminates_section() -> None:
    doc = "Summary.\n\nReturns:\n    a token\n\nTrailing prose not in a section.\n"
    out = doc_excerpt(doc, "sections", 240)
    assert "a token" in out
    assert "Trailing prose" not in out


# ── _pageindex doc/summary de-duplication ─────────────────────────────────


def test_pageindex_omits_doc_for_long_single_line_docstring() -> None:
    # A 180-char single-line docstring: summary is the 140-char cut, the doc
    # excerpt would be the 180-char cut of the SAME line — pure overlap, no
    # structured content. Omit it.
    line = "Resolve the configured backend for the given capability and return it" + (" x" * 56)
    node = _node(summary=line[:140], extra={"docstring": line})
    out = pageindex_with_qname(node, doc_mode="sections", doc_max_chars=240)
    assert "doc" not in out


def test_pageindex_keeps_doc_when_sections_add_content() -> None:
    # Regression guard for the dedup above: a richer excerpt (sections beyond
    # the first line) must STILL be emitted even though it starts with summary.
    node = _node(
        summary="Authenticate a user.",
        extra={"docstring": "Authenticate a user.\n\nArgs:\n    req: the request\n"},
    )
    out = pageindex_with_qname(node, doc_mode="sections", doc_max_chars=240)
    assert "doc" in out and "req: the request" in out["doc"]


# ── _header_from_text safety on pathological input ────────────────────────


def test_header_string_default_with_paren_is_best_effort_no_crash() -> None:
    # A ')' inside a string default can truncate the header early; the
    # contract is only "never crash, stay bounded".
    out = _header_from_text('def f(x="a): b"):\n    return x')
    assert out.startswith("def f(")
    assert len(out) <= TITLE_MAX_CHARS


# ── doc-excerpt truncation visibility ─────────────────────────────────────


def test_pageindex_records_truncation_for_emitted_doc_over_cap() -> None:
    node = _node(summary="Auth.", extra={"docstring": "Auth.\n\nArgs:\n    req: " + "y" * 500})
    trunc: list[int] = []
    out = pageindex_with_qname(node, doc_mode="sections", doc_max_chars=40, _truncations=trunc)
    assert "doc" in out and len(out["doc"]) == 40
    assert len(trunc) == 1


def test_pageindex_no_truncation_recorded_when_doc_within_cap() -> None:
    node = _node(summary="Auth.", extra={"docstring": "Auth.\n\nArgs:\n    req: the request"})
    trunc: list[int] = []
    pageindex_with_qname(node, doc_mode="sections", doc_max_chars=240, _truncations=trunc)
    assert trunc == []


def test_pageindex_no_truncation_recorded_for_omitted_doc() -> None:
    # Long single line: doc is omitted (prefix of first line) even though
    # capped — nothing emitted means nothing to warn about.
    line = "x" * 400
    node = _node(summary=line[:140], extra={"docstring": line})
    trunc: list[int] = []
    out = pageindex_with_qname(node, doc_mode="sections", doc_max_chars=240, _truncations=trunc)
    assert "doc" not in out
    assert trunc == []


@pytest.mark.asyncio
async def test_run_warns_once_when_doc_excerpt_truncated(caplog) -> None:
    import json
    import logging

    from pydocs_mcp.models import Chunk, SearchQuery
    from pydocs_mcp.retrieval.pipeline import RetrieverState
    from tests._fakes import (
        FakeLlmClient,
        InMemoryChunkStore,
        InMemoryDocumentTreeStore,
        make_fake_uow_factory,
    )

    long_doc = "Authenticate.\n\nArgs:\n    req: " + "y" * 500
    func = DocumentNode(
        node_id="n",
        qualified_name="pkg.mod.login",
        title="def login()",
        kind=NodeKind.FUNCTION,
        source_path="m.py",
        start_line=1,
        end_line=2,
        text="def login(req):\n    ...",
        content_hash="",
        summary="Authenticate.",
        extra_metadata={"docstring": long_doc},
        parent_id="root",
        children=(),
    )
    tree = DocumentNode(
        node_id="root",
        qualified_name="pkg.mod",
        title="pkg.mod",
        kind=NodeKind.MODULE,
        source_path="m.py",
        start_line=1,
        end_line=9,
        text="mod",
        content_hash="",
        summary="mod",
        extra_metadata={},
        parent_id=None,
        children=(func,),
    )
    chunks = InMemoryChunkStore()
    await chunks.upsert(
        (Chunk(text="...", metadata={"qualified_name": "pkg.mod.login", "package": "__project__"}),)
    )
    uow = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [tree]}), chunks=chunks
    )
    llm = FakeLlmClient(
        responses={"login": json.dumps({"thinking": "", "node_list": ["pkg.mod.login"]})}
    )
    step = LlmTreeReasoningStep(
        llm_client=llm, uow_factory=uow, doc_excerpt="sections", doc_excerpt_max_chars=30
    )
    state = RetrieverState(
        query=SearchQuery(terms="login", max_results=5),
        candidates=None,
        result=None,
        scratch={},
    )
    with caplog.at_level(logging.WARNING):
        await step.run(state)
    warnings = [r for r in caplog.records if "doc_excerpt_max_chars" in r.getMessage()]
    assert len(warnings) == 1
