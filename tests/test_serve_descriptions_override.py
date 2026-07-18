"""ADR 0006 §2–§4 — description-override plumbing (Phase 1 Task 3).

Pins the entry-point contract around the externalized description source:

- **Config plumbing** — ``ServeConfig.descriptions_path`` (YAML
  ``serve.descriptions_path``, env ``PYDOCS_SERVE__DESCRIPTIONS_PATH``)
  layered by AppConfig with env outranking the user YAML.
- **Precedence** — CLI flag > env var > user YAML > packaged default,
  resolved by ``application.description_override``.
- **Universal strictness** — any explicitly named source that is missing or
  invalid is a hard startup error carrying the winning source's origin;
  fallback to the packaged document exists only when no override was named.
- **Startup log** — ``descriptions artifact <hash12> source=packaged|<path>``
  is pinned by format (Phase 2 attribution parses it from the startup log).
- **MCP/CLI parity** — the same applied source yields identical description
  strings on the MCP registration surface and the argparse tree, and
  ``main()`` applies the env override BEFORE the parser is built (R2).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from pydocs_mcp.application import description_override as do
from pydocs_mcp.application import description_source as ds
from pydocs_mcp.application import tool_docs
from pydocs_mcp.retrieval.config import AppConfig, ServeConfig

_ENV_VAR = "PYDOCS_SERVE__DESCRIPTIONS_PATH"


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate from ambient ``PYDOCS_*`` env vars and any user config file
    (mirrors ``tests/test_default_config_serve_watch.py``)."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv(_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def restore_tool_docs():
    """Snapshot + restore the module attributes around rebinding tests —
    ``apply_source`` mutates process-global state (same shape as the fixture
    in ``tests/application/test_description_loading.py``)."""
    saved_docs = dict(tool_docs.TOOL_DOCS)
    saved_instructions = tool_docs.SERVER_INSTRUCTIONS
    saved_preamble = tool_docs.TURN0_PREAMBLE
    yield
    tool_docs.TOOL_DOCS.clear()
    tool_docs.TOOL_DOCS.update(saved_docs)
    tool_docs.SERVER_INSTRUCTIONS = saved_instructions
    tool_docs.TURN0_PREAMBLE = saved_preamble


def _write_overlay(path: Path, *, marker: str) -> Path:
    """Write a valid override document whose grep section carries ``marker``."""
    sections = ds.load_packaged()
    sections[ds.tool_section_header("grep")] += f"\n{marker}"
    path.write_text(ds.render_sections(sections), encoding="utf-8")
    return path


def _write_invalid(path: Path) -> Path:
    """Write a drift-failing document (a required section is missing)."""
    sections = ds.load_packaged()
    del sections[ds.tool_section_header("glob")]
    path.write_text(ds.render_sections(sections), encoding="utf-8")
    return path


# ── ServeConfig / AppConfig plumbing ───────────────────────────────────────


def test_serve_config_descriptions_path_defaults_to_none() -> None:
    assert ServeConfig().descriptions_path is None


def test_shipped_defaults_document_the_key() -> None:
    from importlib import resources

    text = resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml").read_text("utf-8")
    data = yaml.safe_load(text)
    assert "descriptions_path" in data["serve"]
    assert data["serve"]["descriptions_path"] is None


def test_yaml_overlay_populates_descriptions_path(tmp_path: Path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("serve:\n  descriptions_path: /candidates/a.md\n")
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.serve.descriptions_path == "/candidates/a.md"


def test_env_var_outranks_user_yaml(monkeypatch, tmp_path: Path) -> None:
    """ADR 0006 §3: env settings outrank the user YAML layer — the folk
    order 'defaults → YAML → env' is wrong for this codebase."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("serve:\n  descriptions_path: /candidates/yaml.md\n")
    monkeypatch.setenv(_ENV_VAR, "/candidates/env.md")
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.serve.descriptions_path == "/candidates/env.md"


# ── resolution precedence ──────────────────────────────────────────────────


def test_resolve_no_sources_means_packaged() -> None:
    assert do.resolve_descriptions_override(cli_path=None, configured_path=None) is None


def test_resolve_empty_configured_path_means_packaged() -> None:
    # An empty string (e.g. ``PYDOCS_SERVE__DESCRIPTIONS_PATH=""``) is
    # "unset", not an explicit empty source.
    assert do.resolve_descriptions_override(cli_path=None, configured_path="") is None


def test_resolve_cli_flag_wins_over_configured(monkeypatch) -> None:
    monkeypatch.setenv(_ENV_VAR, "/candidates/env.md")
    resolved = do.resolve_descriptions_override(
        cli_path=Path("/candidates/flag.md"), configured_path="/candidates/env.md"
    )
    assert resolved is not None
    path, origin = resolved
    assert path == Path("/candidates/flag.md")
    assert "--descriptions" in origin


def test_resolve_configured_with_env_set_names_the_env_var(monkeypatch) -> None:
    monkeypatch.setenv(_ENV_VAR, "/candidates/env.md")
    resolved = do.resolve_descriptions_override(cli_path=None, configured_path="/candidates/env.md")
    assert resolved is not None
    path, origin = resolved
    assert path == Path("/candidates/env.md")
    assert _ENV_VAR in origin


def test_resolve_configured_without_env_names_the_yaml_key() -> None:
    resolved = do.resolve_descriptions_override(
        cli_path=None, configured_path="/candidates/yaml.md"
    )
    assert resolved is not None
    _, origin = resolved
    assert "serve.descriptions_path" in origin


# ── apply_descriptions_override ────────────────────────────────────────────


def test_apply_without_override_reports_packaged_and_current_hash() -> None:
    artifact_hash, source = do.apply_descriptions_override(cli_path=None, configured_path=None)
    assert source == "packaged"
    assert artifact_hash == ds.current_artifact_hash()


def test_apply_cli_flag_rebinds_and_labels_source(tmp_path: Path, restore_tool_docs) -> None:
    doc = _write_overlay(tmp_path / "flag.md", marker="Flag-marker-sentence.")
    artifact_hash, source = do.apply_descriptions_override(cli_path=doc, configured_path=None)
    assert "Flag-marker-sentence." in tool_docs.TOOL_DOCS["grep"]
    assert source == str(doc)
    assert artifact_hash == ds.current_artifact_hash()


def test_apply_cli_flag_beats_configured_source(tmp_path: Path, restore_tool_docs) -> None:
    flag_doc = _write_overlay(tmp_path / "flag.md", marker="Flag-marker-sentence.")
    yaml_doc = _write_overlay(tmp_path / "yaml.md", marker="Yaml-marker-sentence.")
    _, source = do.apply_descriptions_override(cli_path=flag_doc, configured_path=str(yaml_doc))
    assert "Flag-marker-sentence." in tool_docs.TOOL_DOCS["grep"]
    assert "Yaml-marker-sentence." not in tool_docs.TOOL_DOCS["grep"]
    assert source == str(flag_doc)


def test_apply_missing_flag_path_is_hard_error_naming_the_flag(tmp_path: Path) -> None:
    missing = tmp_path / "nope.md"
    with pytest.raises(ds.DescriptionSourceError, match="nope.md") as excinfo:
        do.apply_descriptions_override(cli_path=missing, configured_path=None)
    notes = "\n".join(getattr(excinfo.value, "__notes__", []))
    assert "--descriptions" in notes


def test_apply_invalid_env_source_is_hard_error_naming_the_env_var(
    monkeypatch, tmp_path: Path, restore_tool_docs
) -> None:
    invalid = _write_invalid(tmp_path / "invalid.md")
    monkeypatch.setenv(_ENV_VAR, str(invalid))
    before = dict(tool_docs.TOOL_DOCS)
    with pytest.raises(ds.DescriptionSourceError) as excinfo:
        do.apply_descriptions_override(cli_path=None, configured_path=str(invalid))
    notes = "\n".join(getattr(excinfo.value, "__notes__", []))
    assert _ENV_VAR in notes
    # No fallback and no half-applied doc: attributes stay untouched.
    assert dict(tool_docs.TOOL_DOCS) == before


# ── serve startup: apply-before-registration + pinned log line ─────────────


def test_startup_log_line_format_packaged(caplog) -> None:
    from pydocs_mcp.server import _apply_descriptions_source

    config = AppConfig.load(explicit_path=None)
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        _apply_descriptions_source(None, config)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("descriptions"))
    # Pinned format — Phase 2 attribution parses this line from the startup
    # log (ADR 0006 §6); loosening it silently breaks attribution.
    assert re.fullmatch(r"descriptions artifact [0-9a-f]{12} source=packaged", line)
    assert line.split()[2] == ds.current_artifact_hash()[:12]


def test_startup_log_line_names_the_override_path(
    caplog, tmp_path: Path, restore_tool_docs
) -> None:
    doc = _write_overlay(tmp_path / "cand.md", marker="Cand-marker-sentence.")
    config = AppConfig.load(explicit_path=None)
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        from pydocs_mcp.server import _apply_descriptions_source

        _apply_descriptions_source(doc, config)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("descriptions"))
    assert re.fullmatch(rf"descriptions artifact [0-9a-f]{{12}} source={re.escape(str(doc))}", line)
    assert "Cand-marker-sentence." in tool_docs.TOOL_DOCS["grep"]


def test_yaml_configured_source_applies_at_startup(
    caplog, tmp_path: Path, restore_tool_docs
) -> None:
    doc = _write_overlay(tmp_path / "yaml_cand.md", marker="Yaml-cand-sentence.")
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(f"serve:\n  descriptions_path: {doc}\n")
    config = AppConfig.load(explicit_path=overlay)
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        from pydocs_mcp.server import _apply_descriptions_source

        _apply_descriptions_source(None, config)
    assert "Yaml-cand-sentence." in tool_docs.TOOL_DOCS["grep"]
    assert any(f"source={doc}" in r.getMessage() for r in caplog.records)


# ── CLI surface ────────────────────────────────────────────────────────────


def test_serve_parser_accepts_descriptions_flag() -> None:
    from pydocs_mcp.__main__ import _build_parser

    args = _build_parser().parse_args(["serve", ".", "--descriptions", "cand.md"])
    assert args.descriptions == Path("cand.md")


def test_serve_threads_descriptions_path_to_server_run(tmp_path: Path) -> None:
    """``serve --db … --descriptions …`` must forward the flag to
    ``server.run`` (which applies it before FastMCP construction)."""
    bundle = tmp_path / "a.db"
    bundle.touch()
    doc = tmp_path / "cand.md"  # never read here — run() is mocked

    with patch("pydocs_mcp.server.run") as mock_run:
        argv = ["pydocs-mcp", "serve", "--db", str(bundle), "--descriptions", str(doc)]
        with patch("sys.argv", argv):
            from pydocs_mcp.__main__ import main

            rc = main()

    assert rc == 0
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["descriptions_path"] == doc


def test_main_applies_env_override_before_parser_build(
    monkeypatch, tmp_path: Path, restore_tool_docs, capsys
) -> None:
    """R2 parity: with the env var exported, even bare ``pydocs-mcp`` help
    output renders the override bundle — the parser is built AFTER apply."""
    sections = ds.load_packaged()
    sections[ds.SERVER_INSTRUCTIONS_HEADER] = "Env-overlay-instructions-sentinel."
    doc = tmp_path / "env_cand.md"
    doc.write_text(ds.render_sections(sections), encoding="utf-8")
    monkeypatch.setenv(_ENV_VAR, str(doc))

    with patch("sys.argv", ["pydocs-mcp"]):
        from pydocs_mcp.__main__ import main

        rc = main()

    assert rc == 0
    assert "Env-overlay-instructions-sentinel." in capsys.readouterr().out


def test_main_hard_errors_on_missing_env_source(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv(_ENV_VAR, str(tmp_path / "gone.md"))
    with patch("sys.argv", ["pydocs-mcp"]):
        from pydocs_mcp.__main__ import main

        rc = main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "gone.md" in err


# ── MCP/CLI parity from the same applied source ────────────────────────────


class _KwargRecorderMCP:
    """Registration-only FastMCP double — captures the description kwarg."""

    def __init__(self) -> None:
        self.descriptions: dict[str, str] = {}

    def tool(self, **kwargs):
        def deco(fn):
            self.descriptions[str(kwargs["name"])] = str(kwargs["description"])
            return fn

        return deco


def test_same_source_yields_identical_mcp_and_cli_descriptions(
    tmp_path: Path, restore_tool_docs
) -> None:
    from pydocs_mcp.__main__ import _build_parser
    from pydocs_mcp.server import _register_tools

    doc = _write_overlay(tmp_path / "parity.md", marker="Parity-marker-sentence.")
    do.apply_descriptions_override(cli_path=doc, configured_path=None)

    rec = _KwargRecorderMCP()
    _register_tools(rec, tools=None)
    parser = _build_parser()
    import argparse

    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name in ds.FROZEN_TOOL_NAMES:
        assert rec.descriptions[name] == tool_docs.TOOL_DOCS[name]
        assert sub.choices[name].description == tool_docs.TOOL_DOCS[name]
    assert "Parity-marker-sentence." in rec.descriptions["grep"]
