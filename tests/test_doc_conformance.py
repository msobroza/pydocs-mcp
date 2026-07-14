"""Doc-conformance harness (spec 2026-07-11-cli-mcp-docs-audit, groups G2-G5).

Statically cross-checks the root markdown docs against the code — every
documented CLI invocation parses against the real argparse tree, TOOL_DOCS
matches the pydantic input models and the registered server handlers,
doc-referenced YAML keys exist in AppConfig, MyST include markers resolve,
and client JSON snippets parse. Pure stdlib + runtime deps (yaml, pydantic);
no subprocess, no network, no index build — Windows-safe by construction.

Escape hatch: a fenced block directly preceded by the HTML comment
``<!-- doc-conformance: skip -->`` is excluded from every harvester. Unused
in the current corpus — grep before assuming otherwise.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shlex
import unittest.mock
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import annotated_types
import pydantic
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

_DOC_FILES: tuple[str, ...] = (
    "README.md",
    "INSTALL.md",
    "DOCUMENTATION.md",
    "CLAUDE.md",
    "SPEC.md",
    "EXTENSIONS.md",
    "examples/ask_your_docs_agent/README.md",
)

_SKIP_MARKER = "<!-- doc-conformance: skip -->"
_SHELL_LANGS = frozenset({"bash", "sh", "shell", "console", ""})
_ENTRY_POINTS = ("pydocs-mcp", "ask-your-docs")
_TOOL_NAMES = (
    "get_overview",
    "search_codebase",
    "get_symbol",
    "get_context",
    "get_references",
    "get_why",
)

# The six documentation/ pages whose MyST include uses the fragile
# `end-before: "---"` marker today (known repo hazard). New uses fail.
_DASH_MARKER_ALLOWLIST = frozenset(
    {
        "architecture/index.md",
        "getting-started/mcp-clients.md",
        "rnd/cache.md",
        "rnd/reference-graph.md",
        "user-guide/configuration.md",
        "user-guide/live-reindex.md",
    }
)


@dataclass(frozen=True, slots=True)
class FencedBlock:
    """One fenced code block lifted from a markdown file."""

    file: Path  # repo-relative
    line: int  # 1-based line of the opening fence
    language: str  # "" for untagged fences
    text: str


@dataclass(frozen=True, slots=True)
class DocCommand:
    """One tokenized CLI invocation found inside a fenced block."""

    file: Path
    line: int
    raw: str
    tokens: tuple[str, ...]


def _iter_fenced_blocks(rel_path: str) -> Iterator[FencedBlock]:
    """Yield triple-backtick fences (indented fences included — README uses
    list-item fences), honoring the skip-marker escape hatch."""
    lines = (ROOT / rel_path).read_text(encoding="utf-8").splitlines()
    open_re = re.compile(r"^(\s*)```(\S*)\s*$")
    i = 0
    while i < len(lines):
        m = open_re.match(lines[i])
        if not m:
            i += 1
            continue
        skipped = i > 0 and lines[i - 1].strip() == _SKIP_MARKER
        indent, lang = m.group(1), m.group(2)
        body: list[str] = []
        open_line = i + 1  # 1-based
        i += 1
        while i < len(lines) and lines[i].strip() != "```":
            body.append(lines[i].removeprefix(indent))
            i += 1
        i += 1  # past the closing fence
        if not skipped:
            yield FencedBlock(Path(rel_path), open_line, lang, "\n".join(body))


def _fenced_blocks() -> list[FencedBlock]:
    return [b for f in _DOC_FILES for b in _iter_fenced_blocks(f)]


def _prose_text(rel_path: str) -> str:
    """The file's text with fenced blocks blanked out (prose-only scans)."""
    lines = (ROOT / rel_path).read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        if re.match(r"^\s*```", line):
            in_block = not in_block
            out.append("")
        else:
            out.append("" if in_block else line)
    return "\n".join(out)


# ── group 1: CLI command conformance (G2) ─────────────────────────────


def _strip_shell_noise(line: str) -> str:
    line = line.removeprefix("$ ")
    if line.lstrip().startswith("#"):
        return ""
    # Trailing comments: none of the corpus commands carry a quoted '#'.
    return re.sub(r"\s+#.*$", "", line).strip()


def _split_compound(line: str) -> list[str]:
    return [seg for seg in re.split(r"\s(?:&&|\|\||;|\|)\s|\s>\s", line) if seg.strip()]


def _expand_alternation(tokens: list[str]) -> list[list[str]]:
    """`serve|index|watch --gpu` → three token lists (intra-token `|` only)."""
    for idx, tok in enumerate(tokens):
        if "|" in tok and not tok.startswith("-"):
            return [
                variant
                for alt in tok.split("|")
                for variant in _expand_alternation(tokens[:idx] + [alt] + tokens[idx + 1 :])
            ]
    return [tokens]


def _harvest_commands() -> list[DocCommand]:
    commands: list[DocCommand] = []
    for block in _fenced_blocks():
        if block.language not in _SHELL_LANGS:
            continue
        # Join backslash continuations, tracking the first physical line.
        joined: list[tuple[int, str]] = []
        pending: str | None = None
        pending_line = 0
        for offset, raw in enumerate(block.text.splitlines()):
            if pending is not None:
                raw = pending + " " + raw.strip()
            else:
                pending_line = block.line + 1 + offset
            if raw.rstrip().endswith("\\"):
                pending = raw.rstrip()[:-1].rstrip()
                continue
            joined.append((pending_line, raw))
            pending = None
        for line_no, raw in joined:
            cleaned = _strip_shell_noise(raw)
            if not cleaned:
                continue
            for segment in _split_compound(cleaned):
                try:
                    tokens = shlex.split(segment, posix=True)
                except ValueError:
                    continue  # unbalanced quotes → not a command fragment
                if not tokens or tokens[0] not in _ENTRY_POINTS:
                    continue
                for variant in _expand_alternation(tokens):
                    commands.append(
                        DocCommand(block.file, line_no, segment.strip(), tuple(variant))
                    )
    return commands


def _command_id(cmd: DocCommand) -> str:
    return f"{cmd.file}:{cmd.line}:{' '.join(cmd.tokens[:3])}"


class _DocCommandError(Exception):
    """argparse rejected a documented invocation."""


def _parser_for(entry_point: str) -> argparse.ArgumentParser:
    if entry_point == "pydocs-mcp":
        from pydocs_mcp.__main__ import _build_parser

        return _build_parser()
    from pydocs_mcp.ask_your_docs.cli import _build_parser as _ayd_parser

    return _ayd_parser()


def _parse_or_raise(tokens: tuple[str, ...]) -> None:
    parser = _parser_for(tokens[0])
    # Performance: patch at class level so subparser instances inherit it.
    with unittest.mock.patch.object(
        argparse.ArgumentParser,
        "error",
        lambda self, message: (_ for _ in ()).throw(_DocCommandError(message)),
    ):
        try:
            parser.parse_args(list(tokens[1:]))
        except SystemExit as exc:  # a documented `--help` exits 0 — that's a pass
            if exc.code not in (0, None):
                raise _DocCommandError(f"exit {exc.code}") from exc


@pytest.mark.parametrize("cmd", _harvest_commands(), ids=_command_id)
def test_documented_cli_invocation_parses(cmd: DocCommand) -> None:
    try:
        _parse_or_raise(cmd.tokens)
    except _DocCommandError as exc:
        pytest.fail(f"{cmd.file}:{cmd.line}: `{cmd.raw}` rejected: {exc}")


def _all_option_strings() -> set[str]:
    found: set[str] = set()

    def walk(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            found.update(action.option_strings)
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    walk(sub)

    walk(_parser_for("pydocs-mcp"))
    walk(_parser_for("ask-your-docs"))
    return found


def test_prose_flags_exist_on_a_parser() -> None:
    """AC7: a backticked `--flag` in prose must be a real option string —
    catches docs inventing a flag outside a fenced block. Scope: bare-flag
    spans (`--watch`) and entry-point-prefixed spans (`pydocs-mcp serve
    --watch`); spans naming a foreign tool first (`maturin build --zig`,
    `pytest --cov`) are that tool's business."""
    registered = _all_option_strings()
    for rel in _DOC_FILES:
        for span_m in re.finditer(r"`([^`\n]+)`", _prose_text(rel)):
            span = span_m.group(1).strip()
            is_ours = span.split()[0] in _ENTRY_POINTS if span else False
            if not is_ours and not re.fullmatch(r"--[a-z][a-z0-9-]*", span):
                continue
            for m in re.finditer(r"(?:^|\s)(--[a-z][a-z0-9-]*)", span):
                flag = m.group(1)
                assert flag in registered, f"{rel}: prose names unknown flag {flag}"


def test_alternation_expansion_validates_all_variants() -> None:
    """AC6 mechanism: `serve|index|watch --gpu` expands to three invocations,
    each parsing against the real tree. The corpus instance of this pattern
    (INSTALL.md's --gpu sentence) lives in PROSE, not a fenced block — the
    spec's fenced-block anchor was prose at spec time too — so the mechanism
    is pinned synthetically here and the prose fragment by the test below."""
    variants = _expand_alternation(["pydocs-mcp", "serve|index|watch", ".", "--gpu"])
    assert [v[1] for v in variants] == ["serve", "index", "watch"]
    for tokens in variants:
        _parse_or_raise(tuple(tokens))


def test_prose_subcommand_alternations_are_real_subcommands() -> None:
    """AC6 corpus half: a prose fragment like `pydocs-mcp serve|index|watch`
    must name only registered subcommands — renaming one reds this."""
    parser = _parser_for("pydocs-mcp")
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    subcommands = set(action.choices)
    seen = 0
    for rel in _DOC_FILES:
        for span in re.finditer(r"`pydocs-mcp ([a-z|]+)`", _prose_text(rel)):
            if "|" not in span.group(1):
                continue
            seen += 1
            for alt in span.group(1).split("|"):
                assert alt in subcommands, (
                    f"{rel}: `{span.group(0)}` names unknown subcommand {alt}"
                )
    assert seen > 0, "no prose subcommand alternations found — INSTALL.md sentence moved?"


def test_extras_install_lines_are_shell_quoted() -> None:
    """D10 regression lint: every `pip install …[extra]` line in the corpus
    wraps the extra spec in quotes (zsh globbing). Single or double quotes
    both count; the lint is vacuous for extras with zero install lines."""
    for block in _fenced_blocks():
        if block.language not in _SHELL_LANGS:
            continue
        for offset, line in enumerate(block.text.splitlines()):
            if "pip install" not in line or not re.search(r"\[[a-z0-9,\-]+\]", line):
                continue
            assert re.search(r"""['"][^'"]*\[[a-z0-9,\-]+\][^'"]*['"]""", line), (
                f"{block.file}:{block.line + 1 + offset}: unquoted extras install: {line.strip()}"
            )


# ── group 2: TOOL_DOCS ↔ MCP schema snapshot (G3) ─────────────────────


def _tool_input_models() -> dict[str, type[pydantic.BaseModel]]:
    from pydocs_mcp.application.mcp_inputs import (
        ContextInput,
        OverviewInput,
        ReferencesInput,
        SearchInput,
        SymbolInput,
        WhyInput,
    )

    return {
        "get_overview": OverviewInput,
        "search_codebase": SearchInput,
        "get_symbol": SymbolInput,
        "get_context": ContextInput,
        "get_references": ReferencesInput,
        "get_why": WhyInput,
    }


class _RecordingMCP:
    """Fake FastMCP capturing registered handlers — signature-only, no calls."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *args: object, **kwargs: object):
        def deco(fn):
            self.tools[str(kwargs.get("name", getattr(fn, "__name__", "?")))] = fn
            return fn

        return deco


def _registered_handlers() -> dict[str, object]:
    from pydocs_mcp.server import _register_tools

    mcp = _RecordingMCP()
    # `tools` is only closed over at registration time, never called.
    _register_tools(mcp, tools=None)
    return mcp.tools


def test_tool_key_parity_three_ways() -> None:
    """AC8: TOOL_DOCS == input-model map == registered handlers == the six."""
    from pydocs_mcp.application.tool_docs import TOOL_DOCS

    assert set(TOOL_DOCS) == set(_TOOL_NAMES)
    assert set(_tool_input_models()) == set(_TOOL_NAMES)
    assert set(_registered_handlers()) == set(_TOOL_NAMES)


def _extract_calls(text: str, tool: str) -> Iterator[str]:
    """Balanced-paren extraction of every `tool(...)` call substring."""
    for m in re.finditer(rf"\b{tool}\(", text):
        depth, start = 0, m.start()
        for pos in range(m.end() - 1, len(text)):
            if text[pos] == "(":
                depth += 1
            elif text[pos] == ")":
                depth -= 1
                if depth == 0:
                    yield text[start : pos + 1]
                    break


def test_tool_docs_example_kwargs_are_model_fields() -> None:
    """AC9: every kwarg in every TOOL_DOCS call example is a real field of
    that tool's input model (ast-parsed, not regex-guessed)."""
    from pydocs_mcp.application.tool_docs import TOOL_DOCS

    models = _tool_input_models()
    checked = 0
    for doc in TOOL_DOCS.values():
        for tool, model in models.items():
            for call_src in _extract_calls(doc, tool):
                try:
                    call = ast.parse(call_src, mode="eval").body
                except SyntaxError:  # prose like `get_symbol(...)` — not a call example
                    continue
                assert isinstance(call, ast.Call)
                for kw in call.keywords:
                    checked += 1
                    assert kw.arg in model.model_fields, (
                        f"TOOL_DOCS example `{call_src}` uses unknown kwarg "
                        f"{kw.arg!r} (fields: {list(model.model_fields)})"
                    )
    assert checked > 0, "no TOOL_DOCS call examples found — harvester broken?"


def test_handler_signatures_subset_of_model_fields() -> None:
    """AC8/G3: each registered handler's parameters ⊆ its input model fields."""
    import inspect

    models = _tool_input_models()
    for name, fn in _registered_handlers().items():
        params = set(inspect.signature(fn).parameters)  # type: ignore[arg-type]
        fields = set(models[name].model_fields)
        assert params <= fields, f"{name}: handler params {params - fields} not in model"


def _max_length(model: type[pydantic.BaseModel], field: str) -> int:
    for meta in model.model_fields[field].metadata:
        if isinstance(meta, annotated_types.MaxLen):
            return meta.max_length
    raise AssertionError(f"{model.__name__}.{field} has no MaxLen bound")


def test_numeric_claims_single_sourced() -> None:
    """AC10: 'up to N' claims and documented defaults are read from the
    models / a default AppConfig — never re-hardcoded here."""
    from pydocs_mcp.application.tool_docs import TOOL_DOCS
    from pydocs_mcp.retrieval.config.app_config import AppConfig

    models = _tool_input_models()
    for tool in ("get_context", "get_why"):
        bound = _max_length(models[tool], "targets")
        assert f"up to {bound}" in TOOL_DOCS[tool], (
            f"{tool}: TOOL_DOCS 'up to' claim disagrees with targets max_length={bound}"
        )
    cfg = AppConfig()
    doc = (ROOT / "DOCUMENTATION.md").read_text(encoding="utf-8")
    assert f"default {cfg.search.output.default_limit}" in doc, (
        "DOCUMENTATION.md search-limit default drifted from AppConfig"
    )


def _tool_table_rows() -> dict[str, tuple[str, set[str]]]:
    """DOCUMENTATION.md tool table → {tool: (description_cell, param_names)}."""
    doc = (ROOT / "DOCUMENTATION.md").read_text(encoding="utf-8")
    rows: dict[str, tuple[str, set[str]]] = {}
    for m in re.finditer(r"^\|\s*`(\w+)`\s*\|\s*`(\w+)\(([^)]*)\)`\s*\|(.*)\|\s*$", doc, re.M):
        params = {p.strip() for p in m.group(3).split(",") if p.strip()}
        rows[m.group(1)] = (m.group(4), params)
    return rows


def test_documentation_tool_table_matches_models() -> None:
    """AC11: every table-row param is a model field and every model field
    appears in the row — the table cannot drift from the schema."""
    rows = _tool_table_rows()
    models = _tool_input_models()
    assert set(rows) == set(_TOOL_NAMES), f"tool table rows: {sorted(rows)}"
    for tool, (_desc, params) in rows.items():
        fields = set(models[tool].model_fields)
        assert params == fields, f"{tool}: table params {params} != model fields {fields}"


def test_limit_row_carries_multi_repo_caveat() -> None:
    """D2 regression: the search_codebase table row must state that `limit`
    caps multi-repo union results only."""
    desc, _params = _tool_table_rows()["search_codebase"][0], None
    assert "multi-repo union" in desc, (
        "search_codebase table row lost the limit multi-repo-union caveat (D2)"
    )


# ── group 3: YAML keys in docs exist in AppConfig (G4) ────────────────


def _submodel_types(annotation: object) -> list[type[pydantic.BaseModel]]:
    import types
    import typing

    if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
        return [annotation]
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return [s for arg in typing.get_args(annotation) for s in _submodel_types(arg)]
    return []


def _is_dict_annotation(annotation: object) -> bool:
    # AppConfig's dict-shaped fields (pipelines, metadata_schemas) are
    # annotated Mapping[str, …], so the abc origins must count as dicts too.
    import collections.abc
    import typing

    return typing.get_origin(annotation) in (
        dict,
        collections.abc.Mapping,
        collections.abc.MutableMapping,
    )


def _valid_dotted_paths(model: type[pydantic.BaseModel], prefix: str = "") -> set[str]:
    """Flatten model_fields into dotted paths, recursing into sub-models.

    dict[str, X]-typed fields terminate the walk (any subkey is legal, marked
    with a trailing '*'); Union members are each walked and unioned.
    """
    paths: set[str] = set()
    for name, field in model.model_fields.items():
        dotted = f"{prefix}{name}"
        paths.add(dotted)
        if _is_dict_annotation(field.annotation):
            paths.add(f"{dotted}.*")
            continue
        for sub in _submodel_types(field.annotation):
            paths |= _valid_dotted_paths(sub, prefix=f"{dotted}.")
    return paths


def _app_config_model() -> type[pydantic.BaseModel]:
    from pydocs_mcp.retrieval.config.app_config import AppConfig

    return AppConfig


def _path_is_valid(dotted: str, valid: set[str]) -> bool:
    if dotted.rstrip(".*") in {v.rstrip(".*") for v in valid}:
        return True
    # A prefix ending in a dict-typed field legalizes any deeper subkey.
    parts = dotted.split(".")
    return any(".".join(parts[:i]) + ".*" in valid for i in range(1, len(parts)))


def _flatten_yaml(data: object, prefix: str = "") -> Iterator[str]:
    if isinstance(data, dict):
        for key, value in data.items():
            dotted = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            yield dotted
            yield from _flatten_yaml(value, dotted)


# Non-AppConfig yaml snippets the corpus legitimately carries (client
# configs). Anything else with zero AppConfig roots fails — a lone typo'd
# root (`embeddng:`) must not slip through as "foreign".
_FOREIGN_YAML_ROOTS = frozenset({"mcpServers"})


def _validate_yaml_block(block: FencedBlock, data: dict, valid: set[str], roots: set[str]) -> None:
    if "steps" in data:  # pipeline blueprint → step registry, not AppConfig
        _validate_blueprint(block, data)
        return
    top = set(data)
    if not top & roots and top <= _FOREIGN_YAML_ROOTS:
        return  # known client-config snippet — parse-only
    unknown_roots = top - roots
    assert not unknown_roots, (
        f"{block.file}:{block.line}: unknown top-level config keys {unknown_roots} "
        f"(AppConfig roots or the _FOREIGN_YAML_ROOTS allowlist)"
    )
    for dotted in _flatten_yaml(data):
        assert _path_is_valid(dotted, valid), (
            f"{block.file}:{block.line}: `{dotted}` is not an AppConfig path"
        )


def _validate_step_entries(block: FencedBlock, entries: list) -> None:
    """Validate each pipeline-step entry's ``type:`` against the registry.

    Shared by ``steps:``-dict blueprints and bare top-level step lists.
    Entries that are not dicts, or dicts without a ``type``/``step`` key, are
    skipped (structural pipeline-loader forms / non-step list items), so a
    plain-scalar YAML list is a safe no-op."""
    from pydocs_mcp.extraction.serialization import stage_registry
    from pydocs_mcp.retrieval.serialization import step_registry

    known = set(step_registry._types) | set(stage_registry._types)
    for entry in entries:
        name = entry.get("type") if isinstance(entry, dict) else None
        if name is None and isinstance(entry, dict):
            name = entry.get("step")
        if name is None:
            continue  # structural forms without a type key are pipeline-loader domain
        assert name in known, f"{block.file}:{block.line}: unregistered step/stage {name!r}"


def _validate_blueprint(block: FencedBlock, data: dict) -> None:
    _validate_step_entries(block, data["steps"])


def test_doc_yaml_blocks_validate_against_app_config() -> None:
    """AC12: every fenced yaml block safe-loads and (when it speaks AppConfig)
    every dotted path exists; blueprint blocks validate registry names."""
    app_config = _app_config_model()
    valid = _valid_dotted_paths(app_config)
    roots = set(app_config.model_fields)
    seen = 0
    for block in _fenced_blocks():
        if block.language != "yaml":
            continue
        seen += 1
        try:
            data = yaml.safe_load(block.text)  # a non-parsing doc YAML block is itself a defect
        except yaml.YAMLError as exc:
            pytest.fail(f"{block.file}:{block.line}: yaml block does not parse: {exc}")
        if isinstance(data, dict):
            _validate_yaml_block(block, data, valid, roots)
        elif isinstance(data, list):
            # Bare top-level list (no `steps:` wrapper) — e.g. a single-step
            # doc illustration. Validate its step `type:`s against the registry
            # so a renamed/typo'd step name in such a snippet is caught. Plain
            # non-step lists are a no-op (entries without a type are skipped).
            _validate_step_entries(block, data)
    assert seen > 0, "no yaml blocks harvested — extractor broken?"


def test_yaml_validator_rejects_synthetic_drift() -> None:
    """AC13 mutation coverage: a bogus leaf and a top-level typo both fail —
    including the AppConfig extra='ignore' top-level blind spot. A lone
    typo'd root must fail too (the corpus's dominant shape is single-root
    snippets); only the explicit foreign-roots allowlist is parse-only."""
    app_config = _app_config_model()
    valid = _valid_dotted_paths(app_config)
    roots = set(app_config.model_fields)
    fake = FencedBlock(Path("synthetic.md"), 1, "yaml", "")
    with pytest.raises(AssertionError, match="bogus_knob"):
        _validate_yaml_block(fake, {"search": {"output": {"bogus_knob": 1}}}, valid, roots)
    with pytest.raises(AssertionError, match="retreival"):
        _validate_yaml_block(fake, {"retreival": {}, "search": {}}, valid, roots)
    with pytest.raises(AssertionError, match="embeddng"):
        _validate_yaml_block(fake, {"embeddng": {"provider": "openai"}}, valid, roots)
    _validate_yaml_block(fake, {"mcpServers": {"pydocs": {}}}, valid, roots)  # allowlisted
    with pytest.raises(AssertionError, match="unregistered"):
        _validate_blueprint(fake, {"steps": [{"type": "not_a_real_step"}]})


def test_yaml_validator_rejects_barelist_step_drift() -> None:
    """A bare top-level YAML list (no ``steps:`` wrapper) whose items are
    pipeline steps — e.g. a single-step doc illustration ``- name: rollup /
    type: parent_rollup`` — must have each ``type:`` validated against the
    registry, exactly like a ``steps:`` blueprint. Without this, a renamed or
    typo'd step name in such a snippet goes undetected (the harness previously
    only validated dict-shaped blocks). Non-step list items stay parse-only."""
    fake = FencedBlock(Path("synthetic.md"), 1, "yaml", "")
    with pytest.raises(AssertionError, match="unregistered"):
        _validate_step_entries(fake, [{"name": "rollup", "type": "not_a_real_step"}])
    # A registered step passes; entries without a type (or non-dict items) are
    # skipped, so a plain-scalar list never raises.
    _validate_step_entries(fake, [{"name": "topk", "type": "top_k_filter"}])
    _validate_step_entries(fake, [{"note": "no type key here"}])
    _validate_step_entries(fake, ["just", "strings"])


def test_mapping_typed_fields_terminate_the_path_walk() -> None:
    """AC12/AC14 mechanism: Mapping-typed AppConfig fields (pipelines,
    metadata_schemas) legalize any subkey — a doc referencing
    `pipelines.<name>.<key>` must not false-red."""
    valid = _valid_dotted_paths(_app_config_model())
    assert "pipelines.*" in valid, "Mapping termination never fired — annotation drift?"
    assert _path_is_valid("pipelines.chunk.blueprint", valid)
    assert _path_is_valid("metadata_schemas.custom", valid)
    assert not _path_is_valid("pipelines_typo.chunk", valid)


def test_prose_dotted_paths_exist_in_app_config() -> None:
    """AC14: backticked dotted paths whose first segment is an AppConfig root
    must be real config paths (first-segment gate kills module-path noise)."""
    app_config = _app_config_model()
    valid = _valid_dotted_paths(app_config)
    roots = set(app_config.model_fields)
    checked = 0
    for rel in _DOC_FILES:
        for m in re.finditer(r"`([a-z_]+(?:\.[a-z_*]+)+)`", _prose_text(rel)):
            dotted = m.group(1)
            if dotted.split(".")[0] not in roots:
                continue
            checked += 1
            probe = dotted[:-2] if dotted.endswith(".*") else dotted
            assert _path_is_valid(probe, valid), f"{rel}: `{dotted}` is not an AppConfig path"
    assert checked > 0, "no prose dotted paths harvested — regex broken?"


# ── groups 4 + 5: site include integrity + client JSON (G5) ───────────


_INCLUDE_RE = re.compile(
    r"```\{include\}\s+(?P<target>\S+)\n(?P<options>(?::[a-z-]+:[^\n]*\n)*)",
)
_OPTION_RE = re.compile(r":(start-after|end-before):\s*\"(?P<marker>[^\"]+)\"")


def test_myst_include_markers_resolve() -> None:
    """AC15: every MyST include target exists and both marker strings appear
    verbatim — a reworded heading cannot silently blank a site page."""
    pages = sorted((ROOT / "documentation").rglob("*.md"))
    assert pages, "documentation/ pages missing?"
    seen = 0
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for m in _INCLUDE_RE.finditer(text):
            seen += 1
            target = (page.parent / m.group("target")).resolve()
            assert target.is_file(), (
                f"{page.relative_to(ROOT)}: include target {m.group('target')} missing"
            )
            target_text = target.read_text(encoding="utf-8")
            for om in _OPTION_RE.finditer(m.group("options")):
                marker = om.group("marker")
                assert marker in target_text, (
                    f"{page.relative_to(ROOT)}: marker {marker!r} not found in {target.name}"
                )
                if marker == "---":
                    rel = str(page.relative_to(ROOT / "documentation"))
                    assert rel in _DASH_MARKER_ALLOWLIST, (
                        f'{rel}: new `end-before: "---"` marker — use a next-heading '
                        "string instead (MyST include table-separator trap)"
                    )
    assert seen >= 20, f"only {seen} includes found — inventory regression?"


def test_fenced_json_blocks_parse() -> None:
    """AC16: every fenced json block in the corpus (incl. the MCP-client
    snippets) parses — the machine-checkable surface of D11."""
    seen = 0
    for block in _fenced_blocks():
        if block.language != "json":
            continue
        seen += 1
        try:
            json.loads(block.text)
        except json.JSONDecodeError as exc:
            pytest.fail(f"{block.file}:{block.line}: invalid JSON snippet: {exc}")
    assert seen > 0, "no json blocks harvested — extractor broken?"


# ── prose assertions pinning individual defect fixes ──────────────────


def test_gpu_prose_names_all_three_subcommands() -> None:
    """D9 regression: the README, DOCUMENTATION and INSTALL `--gpu` sentences
    name serve, index AND watch (argparse registers --gpu on all three)."""
    for rel in ("README.md", "DOCUMENTATION.md", "INSTALL.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        m = re.search(r"[^.]*`--gpu`[^.]*\.", text)
        assert m, f"{rel}: no --gpu sentence found"
        assert "watch" in m.group(0), (
            f"{rel}: --gpu sentence omits the watch subcommand: {m.group(0)!r}"
        )


def test_extensions_wsi_not_called_planned() -> None:
    """D6 regression: WeightedScoreInterpolationStep is shipped — EXTENSIONS.md
    must not call it planned."""
    text = (ROOT / "EXTENSIONS.md").read_text(encoding="utf-8")
    assert "planned `WeightedScoreInterpolationStep`" not in text, (
        "EXTENSIONS.md still calls the shipped WeightedScoreInterpolationStep 'planned' (D6)"
    )


def test_site_index_offline_claim_names_all_network_paths() -> None:
    """D8 regression: the docs-site landing page must not claim LLM reasoning
    is the only network path (openai embeddings + decision structuring exist)."""
    text = (ROOT / "documentation" / "index.md").read_text(encoding="utf-8")
    assert "The only call that ever leaves your machine" not in text, (
        "documentation/index.md still overstates the offline guarantee (D8)"
    )
    assert "embedding provider" in text, (
        "documentation/index.md network sentence must name the embedding provider opt-in (D8)"
    )


def test_no_stale_two_tool_docstrings() -> None:
    """D5 regression: the removed 2-tool surface must not be described
    anywhere in shipped code."""
    offenders: list[str] = []
    for path in (ROOT / "python" / "pydocs_mcp").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("fixed 2 tools", "``search`` MCP tool", "``lookup`` MCP tool"):
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle!r}")
    assert offenders == [], f"stale two-tool docstrings: {offenders}"


def test_pipeline_base_docstring_example_uses_real_fields() -> None:
    """D7 regression: every `XxxStep(...)` call in RetrieverPipeline's class
    docstring uses only real dataclass field names of that step class."""
    import dataclasses

    import pydocs_mcp.retrieval.steps as steps_pkg
    from pydocs_mcp.retrieval.pipeline import base as base_mod

    doc = base_mod.RetrieverPipeline.__doc__ or ""
    checked = 0
    for m in re.finditer(r"\b([A-Z]\w+Step)\(", doc):
        cls = getattr(steps_pkg, m.group(1), None)
        if cls is None:
            continue
        field_names = {f.name for f in dataclasses.fields(cls)}
        for call_src in _extract_calls(doc, m.group(1)):
            call = ast.parse(call_src, mode="eval").body
            assert isinstance(call, ast.Call)
            for kw in call.keywords:
                checked += 1
                assert kw.arg in field_names, (
                    f"RetrieverPipeline docstring: {m.group(1)}({kw.arg}=…) is not a "
                    f"field (has: {sorted(field_names)})"
                )
    assert checked > 0, "no Step constructions found in the docstring — example removed?"
