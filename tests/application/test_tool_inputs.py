"""Input models for the six task-shaped tools (spec §D1)."""

import pytest
from pydantic import ValidationError

from pydocs_mcp.application import mcp_inputs
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    GlobInput,
    GrepInput,
    OverviewInput,
    ReadFileInput,
    ReferencesInput,
    SymbolInput,
    WhyInput,
    configure_from_app_config,
)


def test_symbol_input_defaults_and_depth_enum() -> None:
    payload = SymbolInput(target="pkg.mod.X")
    assert (payload.depth, payload.project) == ("summary", "")
    assert SymbolInput(target="t", depth="source").depth == "source"
    with pytest.raises(ValidationError):
        SymbolInput(target="t", depth="full")
    with pytest.raises(ValidationError):
        SymbolInput(target="")  # empty target is search_codebase's job


def test_context_input_targets_bounds() -> None:
    assert ContextInput(targets=["a"]).targets == ["a"]
    with pytest.raises(ValidationError):
        ContextInput(targets=[])  # spec §D1: empty list = validation error
    with pytest.raises(ValidationError):
        ContextInput(targets=["x"] * 21)  # max 20


def test_context_input_rejects_malformed_target_items() -> None:
    """Each item in ``targets`` must pass the same ``_TARGET_RE`` grammar
    as ``SymbolInput.target`` / ``ReferencesInput.target`` — a malformed
    item bypassing validation here reaches pointer-token interpolation in
    ``formatting.py`` (payloads must never contain ':' or ']]')."""
    with pytest.raises(ValidationError):
        ContextInput(targets=["foo..bar"])  # double-dot: not a valid dotted chain
    with pytest.raises(ValidationError):
        ContextInput(targets=["evil:]]x"])  # would break the "[[...]]" pointer grammar
    with pytest.raises(ValidationError):
        ContextInput(targets=[""])  # empty item — SymbolInput.target also rejects ""
    with pytest.raises(ValidationError):
        ContextInput(targets=["pkg.mod.X", "evil:]]x"])  # one bad item among good ones


def test_why_input_rejects_malformed_target_items() -> None:
    """Why-targets are PATH|QNAME (looser than ``ContextInput.targets`` —
    spec 2026-07-11-cli-mcp-docs-audit D1), but the pointer-grammar-hostile
    ``:`` / ``]`` characters and empty items stay rejected."""
    with pytest.raises(ValidationError):
        WhyInput(targets=["evil:]]x"])
    with pytest.raises(ValidationError):
        WhyInput(targets=[""])
    # Path forms are valid now — the documented `--target a/b.py` contract.
    assert WhyInput(targets=["a/b.py"]).targets == ["a/b.py"]


def test_references_input_direction_enum_and_limit() -> None:
    payload = ReferencesInput(target="pkg.mod.f")
    assert payload.direction == "callers"
    assert payload.limit >= 1  # YAML-wired default, not a literal here
    for direction in ("callers", "callees", "inherits", "impact", "governed_by"):
        assert ReferencesInput(target="t", direction=direction).direction == direction
    with pytest.raises(ValidationError):
        ReferencesInput(target="t", direction="uses")


def test_why_input_shapes() -> None:
    assert WhyInput().query == "" and WhyInput().targets is None
    assert WhyInput(query="auth").query == "auth"
    assert WhyInput(targets=["a", "b"]).targets == ["a", "b"]
    with pytest.raises(ValidationError):
        WhyInput(targets=[])  # empty list is an error, not dashboard mode


def test_overview_input() -> None:
    assert OverviewInput().package == ""
    assert OverviewInput(package="fastapi", project="backend").project == "backend"


# ─── symbol_source config wiring (Task 7) ────────────────────────────────
#
# Mirrors the LookupInput/SearchInput limit-wiring tests in
# tests/application/test_mcp_inputs_limit.py: flip the YAML-loaded value via
# ``configure_from_app_config`` and assert the effect lands (here, the
# get_symbol(depth="source") line cap threaded into ``SymbolSourceService``).


@pytest.fixture
def _restore_symbol_source_slot():
    """Restore the module-level symbol-source slot so the round-trip test
    can flip it without leaking state into sibling tests."""
    saved = mcp_inputs._SYMBOL_SOURCE_MAX_LINES
    try:
        yield
    finally:
        mcp_inputs._SYMBOL_SOURCE_MAX_LINES = saved


def test_configure_from_app_config_installs_symbol_source_max_lines(
    _restore_symbol_source_slot,
) -> None:
    """``configure_from_app_config`` pushes ``cfg.symbol_source.max_lines``
    into the module-level slot — proves YAML flows into the get_symbol
    line cap (parity with the LookupInput/SearchInput limit slots)."""
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.config.models import SymbolSourceConfig

    cfg = AppConfig(symbol_source=SymbolSourceConfig(max_lines=123))
    configure_from_app_config(cfg)
    assert mcp_inputs._SYMBOL_SOURCE_MAX_LINES == 123


def test_symbol_source_factory_reads_max_lines_from_config(
    _restore_symbol_source_slot,
) -> None:
    """The per-project ``SymbolSourceService`` builder threads
    ``cfg.symbol_source.max_lines`` into the service — the config→service
    wire the plan defers to Task 7."""
    from pathlib import Path

    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.config.models import SymbolSourceConfig
    from pydocs_mcp.storage.factories import build_sqlite_symbol_source_service

    cfg = AppConfig(symbol_source=SymbolSourceConfig(max_lines=77))
    svc = build_sqlite_symbol_source_service(Path("/nonexistent.db"), config=cfg)
    assert svc.max_lines == 77


# ─── grep / glob / read_file inputs (tool-contracts.md §3.7-3.9) ─────────
#
# The dash-named grep flags (-i/-n/-A/-B/-C) are validation_alias'd: the
# MCP inputSchema advertises the contract's literal wire names, incoming
# calls key by dash, and model_dump() emits the Python field names the
# FileToolsService Protocols read (file_tools.py:GrepRequest).

# The contract's exact grep wire-name set — byte-for-byte from §3.7.
_GREP_WIRE_NAMES = {
    "pattern",
    "path",
    "glob",
    "output_mode",
    "-i",
    "-n",
    "-A",
    "-B",
    "-C",
    "head_limit",
    "multiline",
    "scope",
    "project",
}


@pytest.fixture
def _restore_files_head_limit_slot():
    """Restore the module-level files ceiling slot after cap tests."""
    saved = mcp_inputs._FILES_HEAD_LIMIT_MAX
    try:
        yield
    finally:
        mcp_inputs._FILES_HEAD_LIMIT_MAX = saved


def test_grep_input_schema_pins_contract_wire_names() -> None:
    """The advertised inputSchema properties are EXACTLY the contract's
    wire names — `-i`/`-n`/`-A`/`-B`/`-C` literal, not the Python field
    names (tool-contracts.md §3.7)."""
    properties = GrepInput.model_json_schema()["properties"]
    assert set(properties) == _GREP_WIRE_NAMES


def test_grep_input_defaults_match_contract() -> None:
    payload = GrepInput(pattern="foo")
    assert payload.output_mode == "files_with_matches"
    assert payload.case_insensitive is False
    assert payload.line_numbers is True
    assert payload.after_context is None
    assert payload.before_context is None
    assert payload.context is None
    assert payload.head_limit is None  # None ⇒ YAML default at the service
    assert payload.multiline is False
    assert payload.scope == "project"  # differs from search_codebase's "all"
    assert (payload.path, payload.glob, payload.project) == ("", "", "")


def test_grep_input_accepts_dash_keyed_payload() -> None:
    """FastMCP hands the client's raw dict to model_validate — the dash
    wire names must populate the Python-named fields."""
    payload = GrepInput.model_validate(
        {"pattern": "def .*", "-i": True, "-n": False, "-A": 2, "-B": 1, "-C": 3}
    )
    assert payload.case_insensitive is True
    assert payload.line_numbers is False
    assert (payload.after_context, payload.before_context, payload.context) == (2, 1, 3)


def test_grep_input_accepts_field_name_construction() -> None:
    """populate_by_name: server-internal callers (CLI verbs) construct by
    Python field name without spelling dash-keyed dicts."""
    payload = GrepInput(pattern="x", case_insensitive=True, context=1)
    assert payload.case_insensitive is True and payload.context == 1


def test_grep_input_dumps_by_field_name() -> None:
    """model_dump() must emit field names (what FastMCP's
    model_dump_one_level forwards as kwargs / what GrepRequest reads)."""
    dumped = GrepInput.model_validate({"pattern": "x", "-i": True}).model_dump()
    assert dumped["case_insensitive"] is True
    assert "-i" not in dumped


def test_grep_input_rejects_bad_regex_with_pattern_in_message() -> None:
    """The error must carry the offending pattern (CLAUDE.md error rule:
    offending value + expected shape, not a vague 'invalid input')."""
    with pytest.raises(ValidationError, match=r"\[unclosed"):
        GrepInput(pattern="[unclosed")


def test_grep_input_enums_and_bounds() -> None:
    with pytest.raises(ValidationError):
        GrepInput(pattern="x", output_mode="lines")
    with pytest.raises(ValidationError):
        GrepInput(pattern="x", scope="everything")
    with pytest.raises(ValidationError):
        GrepInput(pattern="")  # min_length=1
    with pytest.raises(ValidationError):
        GrepInput.model_validate({"pattern": "x", "-C": -1})  # ge=0
    with pytest.raises(ValidationError):
        GrepInput(pattern="x", head_limit=0)  # ge=1
    with pytest.raises(ValidationError):
        GrepInput(pattern="x", project="bad name")


def test_grep_and_glob_head_limit_capped_by_yaml_ceiling(
    _restore_files_head_limit_slot,
) -> None:
    """``configure_from_app_config`` installs ``files.max_head_limit`` as
    the ceiling for client-supplied caps — mirrors the SearchInput /
    LookupInput limit wiring in test_mcp_inputs_limit.py."""
    from pydocs_mcp.retrieval.config import AppConfig, FilesConfig

    cfg = AppConfig(
        files=FilesConfig(grep_head_limit=10, glob_head_limit=10, read_limit=10, max_head_limit=50)
    )
    configure_from_app_config(cfg)
    assert GrepInput(pattern="x", head_limit=50).head_limit == 50
    assert GlobInput(pattern="*.py", head_limit=50).head_limit == 50
    with pytest.raises(ValidationError, match="files.max_head_limit"):
        GrepInput(pattern="x", head_limit=51)
    with pytest.raises(ValidationError, match="files.max_head_limit"):
        GlobInput(pattern="*.py", head_limit=51)


def test_glob_input_shapes() -> None:
    payload = GlobInput(pattern="**/*_test.py")
    assert (payload.path, payload.head_limit, payload.project) == ("", None, "")
    with pytest.raises(ValidationError):
        GlobInput(pattern="")  # required, non-empty
    with pytest.raises(ValidationError):
        GlobInput(pattern="*.py", head_limit=0)  # ge=1
    with pytest.raises(ValidationError):
        GlobInput(pattern="*.py", project="bad name")


def test_read_file_input_shapes() -> None:
    payload = ReadFileInput(file_path="src/app.py")
    assert (payload.offset, payload.limit, payload.project) == (None, None, "")
    assert ReadFileInput(file_path="a", offset=1, limit=1).offset == 1
    with pytest.raises(ValidationError):
        ReadFileInput(file_path="")  # min_length=1
    with pytest.raises(ValidationError):
        ReadFileInput(file_path="a", offset=0)  # 1-indexed, ge=1
    with pytest.raises(ValidationError):
        ReadFileInput(file_path="a", limit=0)  # ge=1
    with pytest.raises(ValidationError):
        ReadFileInput(file_path="a", project="bad name")


def test_file_tool_inputs_satisfy_file_tools_protocols() -> None:
    """The pydantic models must satisfy the structural Protocols the
    FileToolsService consumes (application/file_tools.py) — field-name
    access, not wire-name access."""
    grep = GrepInput(pattern="x")
    for attr in (
        "pattern",
        "path",
        "glob",
        "output_mode",
        "case_insensitive",
        "line_numbers",
        "after_context",
        "before_context",
        "context",
        "head_limit",
        "multiline",
        "scope",
    ):
        assert hasattr(grep, attr)
    glob_payload = GlobInput(pattern="*.py")
    for attr in ("pattern", "path", "head_limit"):
        assert hasattr(glob_payload, attr)
    read_payload = ReadFileInput(file_path="a")
    for attr in ("file_path", "offset", "limit"):
        assert hasattr(read_payload, attr)
