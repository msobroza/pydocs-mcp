"""Per-file artifacts round-trip through the blob cache, the split tells a full
row under the current key from everything else, and the key moves with exactly
the settings that shape a file's extraction (spec §6.1, §6.3 step 2; #261, #309).
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.application.branch_manifest import BranchManifest
from pydocs_mcp.application.branch_membership import extraction_rows
from pydocs_mcp.application.extraction_cache import (
    EMPTY_SWEEP,
    FileArtifacts,
    ReferenceSweep,
    artifacts_json,
    cached_file,
    file_artifacts,
    file_extraction_cache_key,
    members_from_json,
    members_to_json,
    split_cache_hits,
    sweep_from_json,
    sweep_to_json,
)
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.pipeline.stages import content_hash as stage_module
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    Chunk,
    ModuleMember,
    Parameter,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.branch_records import BranchFile, FileExtraction
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import make_fake_uow_factory


def _tree(module: str, path: str) -> DocumentNode:
    return DocumentNode(module, module, module, NodeKind.MODULE, path, 1, 3, "t", "h")


def _member(module: str, name: str) -> ModuleMember:
    return ModuleMember(
        metadata={"package": PROJECT_PACKAGE_NAME, "module": module, "name": name, "kind": "def"}
    )


def _ref(from_node_id: str, to_name: str = "g") -> NodeReference:
    return NodeReference(PROJECT_PACKAGE_NAME, from_node_id, to_name, None, ReferenceKind.CALLS)


def _group(trees, members=(), refs=(), aliases=None, types=None, paths=None):
    return file_artifacts(
        trees,
        members,
        refs,
        aliases=aliases or {},
        class_attribute_types=types or {},
        relative_paths=paths if paths is not None else tuple(t.source_path for t in trees),
    )


# ── codecs ────────────────────────────────────────────────────────────────


def test_codecs_round_trip_and_drop_resolution() -> None:
    members = (_member("pkg.a", "f"),)
    back = members_from_json(members_to_json(members), branch="feature/x")
    assert back[0].metadata["name"] == "f" and back[0].metadata["branch"] == "feature/x"
    refs = (NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "g", "pkg.b.g", ReferenceKind.CALLS),)
    aliases = {"pkg.a": {"g": "pkg.b.g"}}
    types = {"pkg.a.C": {"x": "pkg.b.T"}}
    restored = sweep_from_json(sweep_to_json(refs, aliases, types))
    assert restored.references[0].to_name == "g" and restored.references[0].to_node_id is None
    assert restored.references[0].kind == ReferenceKind.CALLS
    assert restored.aliases == aliases and restored.class_attribute_types == types


def test_the_stored_branch_never_leaks_into_the_cache() -> None:
    stamped = ModuleMember(metadata={**_member("pkg.a", "f").metadata, "branch": "main"})
    assert "branch" not in json.loads(members_to_json((stamped,)))[0]
    back = members_from_json(members_to_json((stamped,)), branch="other")
    assert back[0].metadata["branch"] == "other"


def test_member_parameters_round_trip_as_parameters() -> None:
    """Inspect-mode members carry ``Parameter`` objects, which json cannot
    encode; they come back as the same value objects the row mapper reads."""
    params = (Parameter("x", "int", "0"), Parameter("y"))
    member = ModuleMember(metadata={**_member("pkg.a", "f").metadata, "parameters": params})
    (back,) = members_from_json(members_to_json((member,)), branch="main")
    assert back.metadata["parameters"] == params


# ── grouping one extraction per file ───────────────────────────────────────


def test_file_artifacts_group_by_file() -> None:
    trees = (_tree("pkg.a", "pkg/a.py"), _tree("pkg.b", "pkg/b.py"))
    members = (_member("pkg.a", "f"), _member("pkg.b", "g"))
    refs = (_ref("pkg.a.f"),)
    grouped = _group(
        trees,
        members,
        refs,
        aliases={"pkg.a": {"g": "pkg.b.g"}},
        types={"pkg.b.C": {"x": "int"}},
    )
    assert grouped["pkg/a.py"].tree.qualified_name == "pkg.a"
    assert [m.metadata["name"] for m in grouped["pkg/a.py"].members] == ["f"]
    assert grouped["pkg/a.py"].sweep.references == refs
    assert grouped["pkg/a.py"].sweep.aliases == {"pkg.a": {"g": "pkg.b.g"}}
    assert grouped["pkg/b.py"].sweep.references == ()
    assert grouped["pkg/b.py"].sweep.class_attribute_types == {"pkg.b.C": {"x": "int"}}


def test_a_reference_belongs_to_the_longest_module_prefix() -> None:
    """``pkg`` (``pkg/__init__.py``) prefixes ``pkg.a.C.m`` too; the file that
    owns the edge is the one whose module id is the LONGEST prefix."""
    trees = (_tree("pkg", "pkg/__init__.py"), _tree("pkg.a", "pkg/a.py"))
    grouped = _group(trees, refs=(_ref("pkg.a.C.m"), _ref("pkg.helper")))
    assert [r.from_node_id for r in grouped["pkg/a.py"].sweep.references] == ["pkg.a.C.m"]
    assert [r.from_node_id for r in grouped["pkg/__init__.py"].sweep.references] == ["pkg.helper"]


def test_edges_no_file_owns_are_not_cached() -> None:
    """A GOVERNS edge (``decision:<key>``) is decision-derived, not file-derived."""
    grouped = _group((_tree("pkg.a", "pkg/a.py"),), refs=(_ref("decision:abc"),))
    assert grouped["pkg/a.py"].sweep == EMPTY_SWEEP


# The pass derives these across files: SIMILAR from the kNN over every chunk it
# embedded, GOVERNS from its mined decisions. Neither is a function of one blob.
_PASS_DERIVED = {ReferenceKind.SIMILAR, ReferenceKind.GOVERNS}


@pytest.mark.parametrize("kind", list(ReferenceKind), ids=lambda k: k.value)
def test_only_file_derived_edge_kinds_are_cached(kind: ReferenceKind) -> None:
    """An edge from a symbol ``pkg/a.py`` owns is cached only when that file's
    bytes produced it. A cached SIMILAR edge would come back from a hit with
    its ``to_node_id`` dropped (the codec never stores one, and the resolver
    passes SIMILAR through untouched): a dangling edge (#309 review)."""
    edge = NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "pkg.b.g", "pkg.b.g", kind)
    grouped = _group((_tree("pkg.a", "pkg/a.py"), _tree("pkg.b", "pkg/b.py")), refs=(edge,))
    cached = [r.kind for s in grouped.values() for r in s.sweep.references]
    assert cached == ([] if kind in _PASS_DERIVED else [kind])


def test_a_file_outside_the_manifest_keeps_its_rows_out_of_its_parent_s() -> None:
    """``pkg/a.py`` is not in the manifest; its edges must not fall through to
    ``pkg/__init__.py``, whose module id also prefixes them."""
    trees = (_tree("pkg", "pkg/__init__.py"), _tree("pkg.a", "pkg/a.py"))
    grouped = _group(
        trees, members=(_member("pkg.a", "f"),), refs=(_ref("pkg.a.f"),), paths=("pkg/__init__.py",)
    )
    assert set(grouped) == {"pkg/__init__.py"}
    assert grouped["pkg/__init__.py"].sweep == EMPTY_SWEEP
    assert grouped["pkg/__init__.py"].members == ()


def test_files_sharing_a_module_id_get_no_artifacts() -> None:
    """Package rooting maps ``examples/a/app/main.py`` and ``examples/b/app/
    main.py`` to one id (OD-A): their members and edges cannot be told apart,
    so neither file is cacheable — a spans-only row, which is always a miss."""
    trees = (
        _tree("app.main", "examples/a/app/main.py"),
        _tree("app.main", "examples/b/app/main.py"),
        _tree("app", "app.py"),
    )
    grouped = _group(trees, members=(_member("app.main", "f"),), refs=(_ref("app.main.f"),))
    assert set(grouped) == {"app.py"}
    assert grouped["app.py"].sweep == EMPTY_SWEEP  # the shorter prefix never inherits them


def test_members_match_their_module_exactly() -> None:
    """A member whose file produced no tree must not land in its package's row."""
    grouped = _group((_tree("pkg", "pkg/__init__.py"),), members=(_member("pkg.broken", "x"),))
    assert grouped["pkg/__init__.py"].members == ()


# ── rows ──────────────────────────────────────────────────────────────────


# A key in the shape ``file_extraction_cache_key`` makes; the manifest and the
# split refuse any other shape.
_KEY = "p|x:k"


def _manifest(key: str = _KEY) -> BranchManifest:
    return BranchManifest(
        "main",
        "a" * 40,
        BranchIndexSource.WORKING_TREE,
        "p",
        (BranchFile("main", "pkg/a.py", "blob1"), BranchFile("main", "pkg/e.py", "blob2")),
        extraction_cache_key=key,
    )


def _chunk(path: str = "pkg/a.py") -> Chunk:
    return Chunk.from_test_inputs(
        package=PROJECT_PACKAGE_NAME,
        module="pkg.a",
        title="f",
        text="x",
        metadata={"source_path": path, "start_line": 1, "end_line": 2},
    )


def test_extraction_rows_carry_the_json_columns_under_the_extraction_key() -> None:
    artifacts = {
        "pkg/a.py": FileArtifacts(
            _tree("pkg.a", "pkg/a.py"), (_member("pkg.a", "f"),), ReferenceSweep((), {}, {})
        )
    }
    (row,) = extraction_rows(_manifest(), [(_chunk(), 7)], 1.0, artifacts=artifacts)
    assert row.pipeline_hash == _KEY  # the extraction key, not the bare pipeline hash
    assert json.loads(row.chunk_spans) == [[7, 1, 2]]
    assert row.tree_json is not None and json.loads(row.members_json)[0]["name"] == "f"
    assert json.loads(row.references_json) == {
        "refs": [],
        "aliases": {},
        "class_attribute_types": {},
    }


def test_a_file_with_a_tree_but_no_chunk_still_gets_a_row() -> None:
    """A tree whose nodes carry no text flattens to no chunk; without a row the
    file would be a miss on every branch."""
    artifacts = {"pkg/e.py": FileArtifacts(_tree("pkg.e", "pkg/e.py"), (), EMPTY_SWEEP)}
    (row,) = extraction_rows(_manifest(), (), 1.0, artifacts=artifacts)
    assert (row.path, row.chunk_spans, row.pipeline_hash) == ("pkg/e.py", "[]", _KEY)
    assert row.tree_json is not None


def test_a_manifest_must_name_a_key_file_extraction_cache_key_made() -> None:
    """Forgotten wiring fails loudly (#309 review). A manifest with no key, or
    with P0's bare pipeline hash, once meant a cache that silently wrote, hit and
    swept nothing — or hit rows a grammar or chunker change had superseded."""
    with pytest.raises(TypeError, match="extraction_cache_key"):
        BranchManifest("main", "a" * 40, BranchIndexSource.WORKING_TREE, "p", ())  # type: ignore[call-arg]
    for malformed in ("", "p", "p|x:", "|x:k"):
        with pytest.raises(ValueError, match=re.escape(f"got {malformed!r}")):
            _manifest(key=malformed)


# ── the split ─────────────────────────────────────────────────────────────


async def test_split_treats_spans_only_rows_as_misses() -> None:
    factory = make_fake_uow_factory()
    tree_json, members_json, references_json = artifacts_json(
        FileArtifacts(_tree("pkg.a", "pkg/a.py"), (), EMPTY_SWEEP)
    )
    full = FileExtraction(
        "b1",
        "pkg/a.py",
        _KEY,
        "[[7, 1, 2]]",
        1.0,
        tree_json=tree_json,
        members_json=members_json,
        references_json=references_json,
    )
    spans_only = FileExtraction("b2", "pkg/b.py", _KEY, "[[8, 1, 2]]", 1.0)
    async with factory() as uow:
        await uow.file_extractions.upsert_many([full, spans_only])
        files = (
            BranchFile("f", "pkg/a.py", "b1"),
            BranchFile("f", "pkg/b.py", "b2"),
            BranchFile("f", "pkg/c.py", "b3"),
            BranchFile("f", "untracked.py", ""),
        )
        split = await split_cache_hits(uow, files, _KEY)
    assert [f.path for f, _ in split.hits] == ["pkg/a.py"]
    assert [f.path for f in split.misses] == ["pkg/b.py", "pkg/c.py", "untracked.py"]
    cached = cached_file(full, branch="f")
    assert [(m.chunk_id, m.start_line, m.end_line) for m in cached.memberships] == [(7, 1, 2)]
    assert cached.memberships[0].branch == "f"


def _stored_rows() -> tuple[FileExtraction, ...]:
    tree, members = _tree("pkg.a", "pkg/a.py"), (_member("pkg.a", "f"),)
    artifacts = {
        "pkg/a.py": FileArtifacts(tree, members, ReferenceSweep((_ref("pkg.a.f"),), {}, {}))
    }
    return extraction_rows(_manifest(), [(_chunk(), 7)], 1.0, artifacts=artifacts)


async def test_a_second_branch_with_the_same_blob_and_path_hits_and_a_changed_blob_misses() -> None:
    """#309 AC 1, read per spec §6.1 (rows keyed by blob AND path): a second
    branch carrying the same (blob, path) reuses the stored extraction; the
    same path at another blob re-extracts. The pass that skips the parse on
    such a hit is #310's (plan Task 11); this is the seam it consumes."""
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.file_extractions.upsert_many(_stored_rows())
        same = await split_cache_hits(uow, (BranchFile("feature/x", "pkg/a.py", "blob1"),), _KEY)
        other = await split_cache_hits(uow, (BranchFile("feature/y", "pkg/a.py", "blob9"),), _KEY)
    tree = _tree("pkg.a", "pkg/a.py")
    ((_, row),) = same.hits
    back = cached_file(row, branch="feature/x")
    assert back.artifacts.tree == tree
    assert [m.metadata["name"] for m in back.artifacts.members] == ["f"]
    assert back.artifacts.members[0].metadata["branch"] == "feature/x"
    assert [r.from_node_id for r in back.artifacts.sweep.references] == ["pkg.a.f"]
    assert other.hits == () and [f.blob_sha for f in other.misses] == ["blob9"]


async def test_the_same_blob_under_another_path_is_a_miss() -> None:
    """By design (spec §6.1): module ids, and so the cached tree, members and
    edges, derive from the path — a renamed or copied file re-extracts."""
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.file_extractions.upsert_many(_stored_rows())
        moved = await split_cache_hits(uow, (BranchFile("feature/x", "pkg/z.py", "blob1"),), _KEY)
    assert moved.hits == () and [f.path for f in moved.misses] == ["pkg/z.py"]


async def test_the_split_refuses_a_key_file_extraction_cache_key_did_not_make() -> None:
    """P0's bare pipeline hash as the key would hit nothing, silently (#309 review)."""
    async with make_fake_uow_factory()() as uow:
        with pytest.raises(ValueError, match="got 'p'"):
            await split_cache_hits(uow, (BranchFile("main", "pkg/a.py", "b1"),), "p")


# ── the key ───────────────────────────────────────────────────────────────


def _key_for(config: AppConfig) -> str:
    return file_extraction_cache_key(
        config.compute_ingestion_pipeline_hash(),
        chunking=config.extraction.chunking,
        reference_capture=config.reference_graph.capture,
    )


def _config(tmp_path: Path, overlay: str) -> AppConfig:
    path = tmp_path / "overlay.yaml"
    path.write_text(overlay, encoding="utf-8")
    return AppConfig.load(explicit_path=path)


def test_the_key_names_the_pipeline_hash_and_the_file_identity() -> None:
    config = AppConfig.load()
    key = file_extraction_cache_key(
        "PIPE",
        chunking=config.extraction.chunking,
        reference_capture=config.reference_graph.capture,
    )
    prefix, _, identity = key.partition("|x:")
    assert prefix == "PIPE" and len(identity) == 16


@pytest.mark.parametrize(
    ("overlay", "hits"),
    [
        ("extraction:\n  chunking:\n    text_section:\n      window_lines: 7\n", False),
        ("reference_graph:\n  capture:\n    kinds: [calls, imports, inherits, mentions]\n", False),
        ("reference_graph:\n  capture:\n    enabled: false\n", False),
        ("decision_capture:\n  merge_jaccard: 0.5\n", True),
        ("extraction:\n  members:\n    members_per_module_cap: 5\n", True),
        ("llm:\n  model_name: another-model\n", True),
    ],
    ids=[
        "chunking-knob",
        "capture-kinds",
        "capture-off",
        "decision-knob",
        "members-cap",
        "llm-model",
    ],
)
async def test_a_setting_turns_a_hit_into_a_miss_only_if_it_shapes_the_file(
    tmp_path: Path, overlay: str, hits: bool
) -> None:
    stock_key = _key_for(AppConfig.load())
    row = FileExtraction("b1", "pkg/a.py", stock_key, "[]", 1.0, tree_json="{}")
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.file_extractions.upsert_many([row])
        split = await split_cache_hits(
            uow, (BranchFile("main", "pkg/a.py", "b1"),), _key_for(_config(tmp_path, overlay))
        )
    assert bool(split.hits) is hits


def test_the_pipeline_hash_and_the_grammar_state_move_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig.load()
    stock = _key_for(config)
    other_pipeline = file_extraction_cache_key(
        "another-pipeline",
        chunking=config.extraction.chunking,
        reference_capture=config.reference_graph.capture,
    )
    monkeypatch.setattr(stage_module, "_grammar_fingerprint", lambda: "grammar-state-2")
    assert len({stock, other_pipeline, _key_for(config)}) == 3


async def test_p0_rows_are_misses() -> None:
    """P0 wrote spans-only rows keyed by the bare pipeline hash: neither the
    key nor the shape can satisfy a P1 lookup."""
    config = AppConfig.load()
    pipeline_hash, key = config.compute_ingestion_pipeline_hash(), _key_for(config)
    p0_row = FileExtraction("b1", "pkg/a.py", pipeline_hash, "[[1, 1, 2]]", 1.0)
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.file_extractions.upsert_many([p0_row, replace(p0_row, pipeline_hash=key)])
        files = (BranchFile("main", "pkg/a.py", "b1"),)
        assert (await split_cache_hits(uow, files, key)).hits == ()
