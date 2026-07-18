"""CLI: ``python -m pydocs_mcp {serve,index,watch,link,<the nine tools>,lookup}``.

Each subcommand is a thin wrapper over the application-layer services:

* ``serve`` / ``index`` / ``watch`` route through :class:`ProjectIndexer`.
* The nine task-shaped MCP tools have canonical, identically-named
  subcommands (``search_codebase``, ``get_overview``, ``get_symbol``,
  ``get_context``, ``get_references``, ``get_why``, ``grep``, ``glob``,
  ``read_file``); the historical short verbs (``search``, ``overview``,
  ``symbol``, ``context``, ``refs``, ``why``) remain as argparse aliases
  (docs/tool-contracts.md §6 note 4). ``lookup`` is the deprecated alias
  that routes onto symbol/refs/context (and overview for an empty target).

Every subcommand routes its failures through :func:`_report_cli_failure`,
the single owner of the diagnostic policy: on failure it prints
``Error: <msg>`` to stderr and exits non-zero. Under ``-v``/``--verbose``
it additionally prints the traceback (via ``traceback.print_exc`` plus
``log.exception`` for structured-log consumers); without it, a one-line
hint points users at ``--verbose`` and only ``log.error`` records the
failure so the traceback stays out of the user's stderr pipeline. Async
commands enter via :func:`_run_cmd`; the blocking serve/watch entry
points enter via :func:`_run_blocking`, which adds the
KeyboardInterrupt-as-success contract (Ctrl+C is a graceful shutdown).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, get_args

if TYPE_CHECKING:
    from pydocs_mcp.project_toml import ProjectExcludes
    from pydocs_mcp.retrieval.config import AppConfig, WatchConfig
    from pydocs_mcp.serve.watcher import FileWatcher

from pydocs_mcp._fast import RUST_AVAILABLE, disable_rust
from pydocs_mcp.db import (
    cache_path_for_project,
    open_index_database,
)

log = logging.getLogger("pydocs-mcp")


# ── Argument parsing ──────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse tree — kept as a named helper so tests can
    build the parser without triggering ``main``'s dispatch logic."""
    # Function-local on purpose (R2): the benchmarks-side description overlay
    # re-binds these module attributes before the parser is built; a
    # module-level import would freeze the pre-overlay binding.
    from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS, TOOL_DOCS

    p = argparse.ArgumentParser(
        prog="pydocs-mcp",
        # The MCP server-level orientation doubles as the CLI top-level
        # description — one source, zero drift (contract §6 note 4).
        description=SERVER_INSTRUCTIONS,
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--config",
        type=Path,
        help="Path to pydocs-mcp.yaml (must precede the subcommand: "
        "pydocs-mcp --config x.yaml serve .)",
    )
    sub = p.add_subparsers(dest="cmd")

    _no_rust = dict(
        action="store_true",
        help="Force pure-Python fallback even if Rust extension is available.",
    )
    # ``--cache-dir`` overrides the directory the SQLite cache (and ``.tq``
    # sidecar) live in. CLI-only knob — never plumbed through to the MCP
    # tool surface. Common to every subcommand so the four wirings stay in
    # sync. (Per-deployment knob; no impact on the fixed nine-tool MCP API.)
    _cache_dir = dict(
        type=Path,
        default=None,
        help="Override the cache directory (default: ~/.pydocs-mcp).",
    )
    # Multi-repo loading (CLI-only knob). ``--workspace`` loads every pre-built
    # ``.db`` bundle in a directory; ``--db`` loads specific bundles (repeatable).
    # Both are READ-ONLY (the real source may be absent, so no reindex/watch). The
    # per-query ``--project`` scope selects among what was loaded.
    _workspace = dict(
        type=Path,
        default=None,
        metavar="DIR",
        help="Load every pre-built .db bundle in DIR (read-only multi-repo).",
    )
    _db = dict(
        type=Path,
        action="append",
        default=None,
        dest="db_paths",
        metavar="FILE",
        help="Load a specific pre-built .db bundle (repeatable; read-only).",
    )
    _project_scope = dict(
        default="",
        dest="project_scope",
        metavar="NAME",
        help="Restrict the query to one loaded project by name (default: all loaded).",
    )
    # Re-declaring ``-v/--verbose`` on each subparser so it parses
    # regardless of position (``-m pydocs_mcp -v search …`` and
    # ``-m pydocs_mcp search … -v`` both work). ``default=argparse.SUPPRESS``
    # is the trick: when the subparser's ``-v`` is absent the namespace
    # keeps whatever value the top-level parser already assigned, so a
    # leading ``-v`` is never silently clobbered.
    _verbose = dict(
        action="store_true",
        default=argparse.SUPPRESS,
        help="Verbose logging + traceback on failure.",
    )

    # The shared corpus-selector + engine knobs every query subcommand carries.
    # Single source of truth so the task-shaped subcommands (plus the
    # deprecated ``lookup`` alias) never drift on which flags they accept:
    # ``--project-dir`` picks the cache DB; ``--workspace`` / ``--db`` load
    # read-only multi-repo bundles; ``--project`` scopes the query to one loaded
    # project; ``--no-rust`` / ``--cache-dir`` / ``-v`` are engine/verbosity knobs.
    def _add_query_flags(sp_query: argparse.ArgumentParser) -> None:
        sp_query.add_argument(
            "--project-dir",
            dest="project",
            default=".",
            help="Path to the project root (default: current directory). "
            "Determines which cache database is loaded.",
        )
        sp_query.add_argument("--workspace", **_workspace)
        sp_query.add_argument("--db", **_db)
        sp_query.add_argument("--project", **_project_scope)
        sp_query.add_argument("--no-rust", **_no_rust)
        sp_query.add_argument("--cache-dir", **_cache_dir)
        sp_query.add_argument("-v", "--verbose", **_verbose)

    # ``watch`` is the standalone watcher counterpart to ``serve --watch``:
    # the whole subcommand IS watch mode (it does NOT accept ``--watch``,
    # which would be redundant noise). Shares every other knob with the
    # ``serve`` / ``index`` family so operators don't relearn flags when
    # picking between the two modes.
    for cmd, hlp in [
        ("serve", "Index + start MCP"),
        ("index", "Index only"),
        ("watch", "Index + watch project for changes (no MCP server)"),
    ]:
        sp = sub.add_parser(cmd, help=hlp)
        sp.add_argument("project", nargs="?", default=".")
        # default=None so the YAML-configured inspect_depth wins when the
        # flag is absent (without this, argparse's hard-coded default
        # silently shadows ``extraction.members.inspect_depth``, mirroring
        # the F11 dead-config defect /ultrareview just removed for
        # by_extension).
        sp.add_argument(
            "--depth",
            type=int,
            default=None,
            help="Submodule scan depth (default: YAML extraction.members.inspect_depth)",
        )
        sp.add_argument("--workers", type=int, default=4, help="Parallel workers")
        sp.add_argument("--force", action="store_true", help="Clear cache, re-index all")
        sp.add_argument("--skip-project", action="store_true", help="Skip project source")
        sp.add_argument(
            "--skip-deps",
            action="store_true",
            help="Skip dependency indexing — index only the project source.",
        )
        sp.add_argument("--no-rust", **_no_rust)
        sp.add_argument("--cache-dir", **_cache_dir)
        sp.add_argument("-v", "--verbose", **_verbose)
        sp.add_argument(
            "--no-inspect",
            action="store_true",
            help="Don't import deps. Read .py files from site-packages instead. "
            "Faster, safer, no side-effects. Uses the same parser as project source.",
        )
        sp.add_argument(
            "--full-dep",
            action="append",
            dest="full_deps",
            default=None,
            metavar="NAME",
            help="Promote a dependency to the full project-grade pipeline (all its "
            "chunks dense-embedded, not just doc pages). Repeatable; accepts fnmatch "
            "globs. Merges into embedding.full_index_dependencies from YAML.",
        )
        sp.add_argument(
            "--gpu",
            action="store_true",
            help="Run embedder inference on CUDA. Requires the matching GPU "
            "runtime (onnxruntime-gpu / fastembed-gpu / CUDA torch). Does not "
            "trigger a re-index (device is excluded from the cache key).",
        )
        if cmd == "serve":
            sp.add_argument(
                "--watch",
                action="store_true",
                help="Watch the project for changes and reindex on edits.",
            )
            # Description-source override (ADR 0006). A knob about WHICH
            # document is served, not how retrieval behaves — so it lives on
            # the CLI (and env/YAML), never on the MCP tool surface.
            sp.add_argument(
                "--descriptions",
                type=Path,
                default=None,
                metavar="PATH",
                help="Serve the LLM-visible tool descriptions from PATH (a "
                "delimited descriptions document) instead of the packaged "
                "default. A missing or invalid PATH is a hard startup error "
                "— never a silent fallback. Precedence: this flag > "
                "PYDOCS_SERVE__DESCRIPTIONS_PATH env var > YAML "
                "serve.descriptions_path > packaged.",
            )
            # Multi-repo serve: load pre-built db bundles read-only (skips
            # indexing + watch) so one MCP server hosts several indexed repos.
            sp.add_argument("--workspace", **_workspace)
            sp.add_argument("--db", **_db)

    # ``link`` is an OPERATOR action (spec 2026-07-11 §3.9): materialize or
    # refresh the workspace cross-link overlay sidecar. It takes no tuning
    # flags — all behavior (kinds, match_scope, alias resolution, scores)
    # comes from YAML via AppConfig, per the MCP-surface/YAML rule.
    sp_link = sub.add_parser(
        "link",
        help="Build/refresh cross-repo reference links for a workspace",
        description=(
            "Resolve references across the bundles of a multi-repo workspace and "
            "persist them to the pydocs-links.sqlite3 overlay next to the bundles. "
            "Serve runs this automatically at startup (reference_graph.cross_repo."
            "link_on_serve); the verb exists to pre-bake overlays into CI images / "
            "read-only deployments and for freshness gating."
        ),
    )
    sp_link.add_argument("--workspace", **_workspace)
    sp_link.add_argument("--db", **_db)
    sp_link.add_argument(
        "--check",
        action="store_true",
        help="Detection only: exit 1 if any bundle's links are stale; write nothing.",
    )
    sp_link.add_argument("-v", "--verbose", **_verbose)

    # ── Task-shaped subcommands mirror the nine MCP tools 1:1 (spec §D1) ──
    # Canonical subcommand names equal the MCP tool names; the historical
    # short verbs stay as argparse aliases (contract §6 note 4). Each parser
    # takes its help= (first line) and description= (full text) from the
    # ``TOOL_DOCS`` single source so the CLI and MCP prose never drift
    # (spec §D13); enum choices= come from the shared ``mcp_inputs`` Literal
    # aliases so the value sets match the models and the MCP inputSchema.
    from pydocs_mcp.application.mcp_inputs import (
        DepthLiteral,
        DirectionLiteral,
        KindLiteral,
        OutputModeLiteral,
        ScopeLiteral,
    )

    def _task_parser(canonical: str, aliases: list[str]) -> argparse.ArgumentParser:
        return sub.add_parser(
            canonical,
            aliases=aliases,
            help=TOOL_DOCS[canonical].splitlines()[0],
            description=TOOL_DOCS[canonical],
            # Raw formatter: TOOL_DOCS is pre-formatted prose with its own
            # line structure (sections + Examples) — don't re-wrap it.
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

    sp_search = _task_parser("search_codebase", ["search"])
    sp_search.add_argument(
        "query", help="Search terms (space-separated; prose AND identifiers work)"
    )
    sp_search.add_argument(
        "--kind",
        choices=list(get_args(KindLiteral)),
        default="any",
        help="Which index to search: 'docs' = prose / README, 'api' = functions / classes, 'decision' = mined architectural decisions, 'any' = both docs+api (default).",
    )
    sp_search.add_argument(
        "-p",
        "--package",
        dest="package",
        default="",
        help='Restrict to one package (e.g. "fastapi"). Use "__project__" for YOUR code, not a library. Default: all packages.',
    )
    sp_search.add_argument(
        "--scope",
        choices=list(get_args(ScopeLiteral)),
        default="all",
        help='Restrict by scope: "project" = your code only, "deps" = installed deps only, "all" = both (default). Use "project" when the user asks about THEIR code.',
    )
    # default=None so the YAML-wired model default (search.output.default_limit)
    # wins when the flag is absent — an argparse literal here would silently
    # shadow the deployment's configured value (contract §6 note 4).
    sp_search.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Result cap for multi-repo union searches (--workspace/--db with "
        "2+ projects; 1-1000; default: YAML search.output.default_limit). "
        "Single-project result count is set by the retrieval pipeline YAML, "
        "not this flag.",
    )
    _add_query_flags(sp_search)

    p_overview = _task_parser("get_overview", ["overview"])
    p_overview.add_argument("package", nargs="?", default="")
    _add_query_flags(p_overview)

    p_symbol = _task_parser("get_symbol", ["symbol"])
    p_symbol.add_argument("target")
    p_symbol.add_argument("--depth", choices=list(get_args(DepthLiteral)), default="summary")
    _add_query_flags(p_symbol)

    p_context = _task_parser("get_context", ["context"])
    p_context.add_argument("targets", nargs="+")
    _add_query_flags(p_context)

    p_refs = _task_parser("get_references", ["refs"])
    p_refs.add_argument("target")
    p_refs.add_argument(
        "--direction",
        choices=list(get_args(DirectionLiteral)),
        default="callers",
    )
    p_refs.add_argument("--limit", type=int, default=None)
    _add_query_flags(p_refs)

    p_why = _task_parser("get_why", ["why"])
    p_why.add_argument("query", nargs="?", default="")
    p_why.add_argument(
        "--target",
        action="append",
        dest="targets",
        default=None,
        # §D11 target classification, mirrored from ``_classify_target``: a value
        # with ``/`` or a source-file extension is a path (``a/b.py``); a dotted
        # value is a qname (``pkg.mod``); a bare single token tries both.
        help=(
            "decisions affecting a target; repeatable. A path (a/b.py) or a "
            "qualified name (pkg.mod) — a value with / or a source-file "
            "extension is treated as a path, a dotted value as a qname, a bare "
            "token as both."
        ),
    )
    _add_query_flags(p_why)

    # ── The three filesystem subcommands mirror the grep/glob/read_file MCP
    # tools 1:1 (contract §3.7-3.9). Flag spellings track the wire names
    # (-i/-n/-A/-B/-C are the literal MCP parameter names); defaults live in
    # the input models / YAML (files.*), so argparse defaults stay None.
    p_grep = _task_parser("grep", [])
    p_grep.add_argument("pattern", help="Regular expression (Python re flavor).")
    p_grep.add_argument(
        "--path",
        default="",
        help="Directory to search under, relative to the selected root(s).",
    )
    p_grep.add_argument(
        "--glob",
        default="",
        help='Glob filter on candidate file paths (e.g. "*.py", "src/**/*.md").',
    )
    p_grep.add_argument(
        "--output-mode",
        dest="output_mode",
        choices=list(get_args(OutputModeLiteral)),
        default="files_with_matches",
        help="content = matching lines (file:line:text); files_with_matches = "
        "paths only (default); count = per-file match counts.",
    )
    p_grep.add_argument(
        "-i",
        dest="case_insensitive",
        action="store_true",
        help="Case-insensitive matching.",
    )
    p_grep.add_argument(
        "-n",
        dest="line_numbers",
        action="store_true",
        default=True,
        help="Include line numbers in content output (on by default).",
    )
    p_grep.add_argument(
        "--no-line-numbers",
        dest="line_numbers",
        action="store_false",
        default=argparse.SUPPRESS,
        help="Omit line numbers in content output.",
    )
    p_grep.add_argument(
        "-A",
        dest="after_context",
        type=int,
        default=None,
        metavar="N",
        help="content mode: trailing context lines after each match.",
    )
    p_grep.add_argument(
        "-B",
        dest="before_context",
        type=int,
        default=None,
        metavar="N",
        help="content mode: leading context lines before each match.",
    )
    p_grep.add_argument(
        "-C",
        dest="context",
        type=int,
        default=None,
        metavar="N",
        help="content mode: context lines around each match (overrides -A/-B).",
    )
    p_grep.add_argument(
        "--head-limit",
        dest="head_limit",
        type=int,
        default=None,
        help="Cap on emitted entries (default: YAML files.grep_head_limit).",
    )
    p_grep.add_argument(
        "--multiline",
        action="store_true",
        help="Patterns may span lines and . matches newlines.",
    )
    p_grep.add_argument(
        "--scope",
        choices=list(get_args(ScopeLiteral)),
        default="project",
        help='"project" = your source tree (default), "deps" = installed '
        'dependency roots, "all" = both.',
    )
    _add_query_flags(p_grep)

    p_glob = _task_parser("glob", [])
    p_glob.add_argument("pattern", help='Glob pattern; ** recurses (e.g. "**/*_test.py").')
    p_glob.add_argument(
        "--path",
        default="",
        help="Directory to match under, relative to the project root.",
    )
    p_glob.add_argument(
        "--head-limit",
        dest="head_limit",
        type=int,
        default=None,
        help="Cap on returned paths (default: YAML files.glob_head_limit).",
    )
    _add_query_flags(p_glob)

    p_read = _task_parser("read_file", [])
    p_read.add_argument(
        "file_path",
        help="Path to read — project-root-relative or absolute, inside the "
        "project root or an indexed dependency root.",
    )
    p_read.add_argument(
        "--offset",
        type=int,
        default=None,
        help="1-indexed line to start reading from (default: line 1).",
    )
    p_read.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum lines to return (default: YAML files.read_limit).",
    )
    _add_query_flags(p_read)

    # ``session-start-context`` is PRODUCT CLI, not an MCP tool — the
    # nine-tool surface stays frozen (ADR 0008 §Decision 5.ii): external
    # harnesses compose the printed pack into their own prompts. Corpus
    # selectors only; the budget and the injection flag are YAML
    # (``serve.session_start_context.*``), never CLI flags.
    p_session_start = sub.add_parser(
        "session-start-context",
        help="Print the session-start context pack (marker + preamble + "
        "overview card + version inventory)",
        description=(
            "Build and print the deterministic session-start context pack a "
            "harness injects at agent-session start: a fixed harness-injected "
            "marker line, the SESSION_START_PREAMBLE framing prose, the same "
            "overview card get_overview serves, and the installed-package "
            "version inventory (name + version per line). The token budget "
            "and trim order come from YAML "
            "(serve.session_start_context.budget_tokens); the card is "
            "trimmed before the inventory and truncation is noted."
        ),
    )
    p_session_start.add_argument("package", nargs="?", default="")
    _add_query_flags(p_session_start)

    sp_lookup = sub.add_parser(
        "lookup",
        help="[deprecated] Alias for symbol/refs/context — use those directly",
        description=(
            "[deprecated] Alias for symbol/refs/context (and overview for an empty "
            "target) — use those subcommands directly. "
            "Navigate to a known symbol (dotted path) and optionally traverse its "
            "reference graph — callers, callees, base classes. Use this when you "
            "know the exact target; use 'search' when you only have a keyword or topic."
        ),
        epilog=(
            "Examples:\n"
            "  pydocs-mcp lookup                                                           # list all indexed packages\n"
            "  pydocs-mcp lookup fastapi                                                   # package overview\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter                                 # class + members\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show callers   # who calls this method\n"
            "  pydocs-mcp lookup requests.auth.HTTPBasicAuth --show inherits               # base classes\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show impact    # what breaks if I change it\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show context   # everything to understand it\n"
            "  pydocs-mcp lookup __project__.my_module.MyClass                             # YOUR class, not a library\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_lookup.add_argument(
        "target",
        nargs="?",
        default="",
        help='Dotted path (e.g. "fastapi.routing.APIRouter"). Use "__project__.<module>.<symbol>" for YOUR code. Empty = list all indexed packages.',
    )
    sp_lookup.add_argument(
        "--show",
        choices=[
            "default",
            "tree",
            "callers",
            "callees",
            "inherits",
            "impact",
            "context",
            "governed_by",
        ],
        default="default",
        help=(
            "What to show: 'default' = symbol summary + immediate children (start here); "
            "'tree' = full nested subtree (use when 'default' is too shallow); "
            "'callers' = who references this — use to answer 'who uses X?'; "
            "'callees' = what this calls — use to answer 'what does X depend on?'; "
            "'inherits' = base classes / interface chain — use to answer 'what does X extend?'; "
            "'impact' = everything that transitively calls this, ranked — 'what breaks if I change X?'; "
            "'governed_by' = which mined decisions govern this symbol — 'why is X the way it is?'; "
            "'context' = dependency closure packed under a token budget — 'everything to understand X'."
        ),
    )
    _add_query_flags(sp_lookup)

    return p


# ── Shared setup helpers ──────────────────────────────────────────────────


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _apply_no_rust_flag(args: argparse.Namespace) -> None:
    """Flip the Rust/Python toggle once, logging the decision."""
    if getattr(args, "no_rust", False) and RUST_AVAILABLE:
        disable_rust()
        log.info("Engine: Python (Rust disabled via --no-rust)")
    else:
        log.info("Engine: %s", "Rust" if RUST_AVAILABLE else "Python")


def _project_and_db(args: argparse.Namespace) -> tuple[Path, Path]:
    project = Path(getattr(args, "project", ".")).resolve()
    db_path = cache_path_for_project(project)
    cache_dir = getattr(args, "cache_dir", None)
    if cache_dir is not None:
        # Preserve the per-project ``<dirname>_<hash>.db`` slug computed by
        # ``cache_path_for_project`` so multiple projects keep separate
        # state under the overridden root. The ``.tq`` (and ``.plaid``)
        # sidecars the indexing path derives via ``db_path.with_suffix(...)``
        # share this slug, so the SQLite cache and its sidecars always land
        # side-by-side under whatever cache root the CLI picked.
        db_path = Path(cache_dir) / db_path.name
    log.debug("DB: %s", db_path)
    return project, db_path


# ── Subcommand handlers ───────────────────────────────────────────────────


def _load_indexing_config(args: argparse.Namespace, app_config_cls: type[AppConfig]) -> AppConfig:
    """AppConfig for an indexing run: YAML + --gpu device + --full-dep merges.

    CLI --full-dep promotions merge into the YAML-declared list. Affects the
    per-package embed tier folded into chunk hashes, so a newly promoted
    dependency re-embeds fully on this run (and only that dependency).
    """
    config = app_config_cls.load(explicit_path=getattr(args, "config", None))
    config = config.with_device(gpu=getattr(args, "gpu", False))
    return config.with_full_index_dependencies(tuple(getattr(args, "full_deps", None) or ()))


async def _run_indexing(args: argparse.Namespace) -> None:
    """Thin driver for ``index`` / ``serve`` / ``watch`` reindex passes.

    Kept as a module-level coroutine so ``_cmd_index``, ``_cmd_serve``, and
    the watch loop's ``_on_change`` callback can drive it through a single
    ``asyncio.run``. All write-side wiring lives in
    ``storage.factories.build_project_indexer`` (the composition root); the
    pass sequence (integrity sweep -> stale-model invalidation -> index ->
    FTS rebuild -> metadata stamp) lives in ``application.run_index_pass``.
    This function only resolves CLI flags into arguments for those two.
    """
    from pydocs_mcp.application import run_index_pass
    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import build_project_indexer

    project, db_path = _project_and_db(args)

    # Ensure the schema exists before repositories issue queries.
    open_index_database(db_path).close()

    use_inspect = not args.no_inspect
    log.info("Project: %s (mode=%s)", project, "inspect" if use_inspect else "static")

    config = _load_indexing_config(args, AppConfig)
    # Push YAML-loaded settings into module-level slots read by
    # ``LookupInput`` validators and ``ReferenceCaptureStage``. Global side
    # effect — kept as an explicit call here, NOT hidden inside the factory.
    configure_from_app_config(config)

    # CLI flag wins over YAML; YAML wins over hard-coded fallback. Depth
    # resolution stays client-side so the factory carries no argparse
    # knowledge — undocumented defaults at the wiring layer are silent traps.
    inspect_depth = (
        args.depth if args.depth is not None else config.extraction.members.inspect_depth
    )
    bundle = build_project_indexer(
        config,
        db_path,
        use_inspect=use_inspect,
        inspect_depth=inspect_depth,
    )

    stats = await run_index_pass(
        orchestrator=bundle.orchestrator,
        indexing_service=bundle.indexing_service,
        pipeline_hash=bundle.pipeline_hash,
        project=project,
        embedding_provider=config.embedding.provider,
        embedding_model=config.embedding.model_name,
        embedding_dim=config.embedding.dim,
        force=args.force,
        include_project_source=not args.skip_project,
        include_dependencies=not args.skip_deps,
        workers=args.workers,
        check_integrity=bundle.check_integrity,
        rebuild_fts=bundle.rebuild_fts,
        stamp_metadata=bundle.stamp_metadata,
        write_aggregates=bundle.write_aggregates,
    )

    kb = db_path.stat().st_size / 1024 if db_path.exists() else 0.0
    log.info(
        "Done: %d indexed, %d cached, %d failed (db: %.0f KB)",
        stats.indexed,
        stats.cached,
        stats.failed,
        kb,
    )


async def _run_serve_indexing(args: argparse.Namespace) -> None:
    """Async indexing phase of ``serve`` — runs before the MCP server boots.

    Split out from the blocking ``server.run`` call so the indexing build-up
    can route through ``_run_cmd``'s ``--verbose`` / traceback policy while
    the MCP server itself runs on the main thread (see ``_cmd_serve`` for
    the SIGINT rationale).
    """
    await _run_indexing(args)


def _derive_watch_globs(
    project: Path,
    scope_entries: tuple[str, ...],
    loader: Callable[[Path], ProjectExcludes],
) -> tuple[str, ...]:
    """Best-effort watchdog ignore globs from the user exclusion surfaces.

    Churn suppression only (spec decision D6) — discovery owns correctness,
    so a failed or partial derivation degrades to extra cheap cached reindex
    cycles, never to wrong index content. The `_EXCLUDED_DIRS` floor is
    deliberately NOT folded in (empty-floor merge): the high-traffic floor
    dirs are covered by the shipped watch defaults; remaining floor misses
    are D6-sanctioned churn (a cheap cached reindex per event).
    """
    from pydocs_mcp.project_toml import (
        EMPTY_PROJECT_EXCLUDES,
        ProjectExcludeConfigError,
        merge_excludes,
    )
    from pydocs_mcp.serve.watcher import derive_exclude_globs

    try:
        loaded = loader(project)
    except ProjectExcludeConfigError as exc:
        # Spec §8 (watcher glob-derivation row): warn and derive from the
        # YAML entries only — the reindex path fails loud on its own.
        log.warning(
            "watch: project exclude config invalid (%s); "
            "ignore globs derived from YAML entries only",
            exc,
        )
        loaded = EMPTY_PROJECT_EXCLUDES
    effective = merge_excludes(frozenset(), scope_entries, loaded)
    return derive_exclude_globs(effective, project)


def _build_watcher_and_callback(
    args: argparse.Namespace,
    watch_cfg: WatchConfig,
    *,
    project_exclude_dirs: tuple[str, ...] = (),
    excludes_loader: Callable[[Path], ProjectExcludes] | None = None,
) -> tuple[FileWatcher, Callable[[], Awaitable[None]]]:
    """Build the ``FileWatcher`` + ``on_change`` callback shared by
    ``serve --watch`` and the standalone ``watch`` subcommand.

    Single source of truth for watcher construction so the two modes can
    only differ in whether they ALSO run an MCP server. Lifted out of
    ``_run_watch_loop`` to keep the two consumers in sync — bug-fixes
    or YAML-knob additions land here and reach both modes automatically.

    ``project_exclude_dirs`` carries the YAML project-scope entries
    (``extraction.discovery.project.exclude_dirs``); ``excludes_loader``
    is the pyproject-excludes loader seam (default: the real
    ``load_project_excludes``) so tests inject fakes without touching the
    filesystem.
    """
    from pydocs_mcp.project_toml import ProjectExcludeConfigError, load_project_excludes
    from pydocs_mcp.serve.watcher import FileWatcher

    loader = excludes_loader if excludes_loader is not None else load_project_excludes
    project, _db = _project_and_db(args)

    # One-element list so the `_on_change` closure below can swap the
    # derived suffix after each reindex (spec D6 shrink direction, AC-25)
    # while the watcher re-reads it through `derived_globs_provider` on
    # every event. The configured `ignore_globs` tuple stays operator-owned
    # and static — only the derived suffix ever refreshes. `_matches` reads
    # this from watchdog's emitter thread; safety rests on the GIL-atomic
    # item assignment of an immutable tuple — never mutate the inner tuple
    # in place.
    derived_globs: list[tuple[str, ...]] = [
        _derive_watch_globs(project, project_exclude_dirs, loader)
    ]
    watcher = FileWatcher(
        root=project,
        extensions=tuple(watch_cfg.extensions),
        ignore_globs=tuple(watch_cfg.ignore_globs),
        debounce_ms=watch_cfg.debounce_ms,
        derived_globs_provider=lambda: derived_globs[0],
    )

    # File-change reindexes must NEVER inherit --force: force wipes the
    # whole cache (SQLite + .tq via IndexingService.clear_all) and re-embeds
    # project + dependencies — what the user asked for on the INITIAL pass,
    # catastrophic on every save (and in serve --watch mode, queries during
    # the re-embed window would hit an empty index). Copy the namespace so
    # the caller-driven initial pass keeps its force semantics.
    watch_args = argparse.Namespace(**vars(args))
    watch_args.force = False

    async def _on_change() -> None:
        # Reindex via the same Phase 1 helper used at startup. Cache
        # makes the no-change case <100ms (spec §2).
        try:
            await _run_indexing(watch_args)
        except ProjectExcludeConfigError as exc:
            # WHY: a half-saved pyproject.toml can PARSE with a wrong-typed
            # value (`exclude_dirs = "docs"` before the brackets land).
            # Killing the serve process on a keystroke race would be worse
            # than the misconfiguration — log, skip this cycle, and let the
            # very next save retry (spec §8, watch row). Detection stays
            # loud; only delivery is softened.
            log.error(
                "watch: project exclude config invalid; skipping this reindex cycle: %s",
                exc,
            )
            return
        except Exception as exc:
            # WHY: a reindex failure during the watch loop should NOT
            # take down the consumer (MCP server in --watch mode; the
            # whole process in standalone watch mode). Log + keep
            # serving stale data instead.
            log.error("watch: reindex failed: %s", exc)
            return
        # WHY re-derive after EVERY successful reindex (not only manifest-
        # triggered ones — this callback receives no trigger paths, and
        # re-deriving an unchanged set is idempotent): startup-only
        # derivation fails the shrink direction. Removing an exclude entry
        # re-includes the directory on the manifest-triggered reindex, but
        # a stale startup glob would then swallow every subsequent event
        # inside it — no event, no reindex, a silently stale subtree until
        # restart (spec D6, AC-25).
        derived_globs[0] = _derive_watch_globs(project, project_exclude_dirs, loader)

    return watcher, _on_change


async def _run_watch_loop(
    args: argparse.Namespace,
    *,
    db_path: Path | None = None,
) -> None:
    """Run the MCP server (Phase 2) AND the file watcher concurrently.

    Spec §4.1 deliverable 5: ``--watch`` adds a third element to
    ``_cmd_serve`` — the watcher asyncio task. The MCP server still runs
    on the main thread (CQ-1 SIGINT delivery preserved); the watcher
    runs on the asyncio loop in a worker thread via ``asyncio.to_thread``.

    Try/finally guarantees the watcher task is cancelled regardless of
    how ``run(...)`` exits (KeyboardInterrupt, RuntimeError, etc.) —
    pins Risk R4 (no orphan Observer on crash) + spec Decision G.
    """
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.server import run

    project, resolved_db = _project_and_db(args)
    if db_path is None:
        db_path = resolved_db

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    watch_cfg = config.serve.watch

    watcher, on_change = _build_watcher_and_callback(
        args,
        watch_cfg,
        project_exclude_dirs=tuple(config.extraction.discovery.project.exclude_dirs),
    )

    watcher_task = asyncio.create_task(watcher.run_until_cancelled(on_change))
    log.info("watch: started (debounce=%dms, root=%s)", watch_cfg.debounce_ms, project)
    try:
        # ``run(...)`` is blocking; offload to a worker thread so the
        # watcher_task keeps draining events on the asyncio loop.
        # ``gpu`` must mirror the no-watch path (``_serve_run``) — otherwise
        # `serve --watch --gpu` silently falls back to CPU query embedding.
        await asyncio.to_thread(
            run,
            db_path,
            config_path=getattr(args, "config", None),
            gpu=getattr(args, "gpu", False),
            # Mirror ``_serve_run`` — otherwise `serve --watch --descriptions X`
            # would silently serve the packaged prose.
            descriptions_path=getattr(args, "descriptions", None),
        )
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("watch: watcher task exited with %s", exc)


async def _run_watch_only(args: argparse.Namespace) -> None:
    """Run only the file watcher — no MCP server.

    Used by the standalone ``pydocs-mcp watch`` subcommand for operators
    who want a fresh on-disk index for CLI ``search`` / ``lookup`` calls
    without keeping an idle FastMCP stdio server running. Blocks on
    ``watcher.run_until_cancelled`` until the task is cancelled
    (KeyboardInterrupt-driven cancellation propagates through
    ``asyncio.run`` in ``_cmd_watch``).
    """
    from pydocs_mcp.retrieval.config import AppConfig

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    watch_cfg = config.serve.watch

    watcher, on_change = _build_watcher_and_callback(
        args,
        watch_cfg,
        project_exclude_dirs=tuple(config.extraction.discovery.project.exclude_dirs),
    )
    project, _db = _project_and_db(args)
    log.info(
        "watch (CLI-only): started (debounce=%dms, root=%s, MCP server: off)",
        watch_cfg.debounce_ms,
        project,
    )
    await watcher.run_until_cancelled(on_change)


def _query_db_path(args: argparse.Namespace) -> Path | None:
    """Single-db path for a query, or ``None`` when a workspace/--db load is used."""
    if getattr(args, "workspace", None) or getattr(args, "db_paths", None):
        return None
    return _project_and_db(args)[1]


def _build_cli_services(args: argparse.Namespace):
    """Build ``(ToolRouter, per-project services, loaded AppConfig)`` for a
    query subcommand (the CLI composition root).

    Every task-shaped subcommand loads config + configures the input-model slots
    + builds routers identically — ``surface="cli"`` picks the CLI pointer syntax
    the shared envelope resolves to. Collapsed into one helper so each ``_run_*``
    runner stays a small adapter (build tools → construct its input model → print)
    and they can't drift on how they select / load databases (``--project-dir``
    single db vs ``--workspace`` / ``--db`` read-only multi-repo). The richer
    return exists for ``_run_session_start_context``, which needs a project's service
    set + the YAML budget, not a router method.
    """
    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.server import build_routers

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    configure_from_app_config(config)
    tools, services = build_routers(
        config,
        db_path=_query_db_path(args),
        workspace=args.workspace,
        db_paths=args.db_paths,
        surface="cli",
    )
    return tools, services, config


def _build_cli_tools(args: argparse.Namespace):
    """The ``ToolRouter`` for a query subcommand (see ``_build_cli_services``)."""
    tools, _services, _config = _build_cli_services(args)
    return tools


async def _run_search(args: argparse.Namespace) -> None:
    """Mirror the MCP ``search_codebase`` tool: same router + same rendering.

    Routes to one loaded project (``--project`` / a single ``--project-dir`` db)
    or unions across a ``--workspace`` / ``--db`` multi-repo load.
    """
    from pydocs_mcp.application import SearchInput

    tools = _build_cli_tools(args)
    # ``limit`` is omitted when the user didn't pass ``--limit`` so the input
    # model's YAML-wired default_factory supplies search.output.default_limit —
    # no literal duplicated here (single-source-of-truth defaults; mirrors
    # ``_run_refs`` and the MCP handler).
    fields: dict[str, object] = {
        "query": args.query,
        "kind": args.kind,
        "package": args.package,
        "scope": args.scope,
        "project": args.project_scope,
    }
    if args.limit is not None:
        fields["limit"] = args.limit
    print((await tools.search_codebase(SearchInput(**fields))).text)


async def _run_overview(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_overview`` tool."""
    from pydocs_mcp.application.mcp_inputs import OverviewInput

    tools = _build_cli_tools(args)
    payload = OverviewInput(package=args.package, project=args.project_scope)
    print((await tools.get_overview(payload)).text)


async def _run_session_start_context(args: argparse.Namespace) -> None:
    """Print the ADR 0008 session-start context pack (product CLI, not an MCP tool).

    Reuses the SAME per-project ``OverviewService`` + ``uow_factory`` the
    router's ``get_overview`` uses, so the printed pack cannot disagree with
    what the tools would return one call later. Printed regardless of
    ``serve.session_start_context.enabled`` — invoking the subcommand IS the
    harness's explicit opt-in; the flag gates only the ask-your-docs
    auto-injection channel.
    """
    from pydocs_mcp.application.description_override import apply_descriptions_override
    from pydocs_mcp.application.multi_project_search import _select_service
    from pydocs_mcp.application.session_start_context import build_session_start_context

    _tools, services, config = _build_cli_services(args)
    # The pack embeds the LIVE ``SESSION_START_PREAMBLE``. ``main()`` already applied
    # the env-var leg before the parser was built; this applies the YAML
    # ``serve.descriptions_path`` leg (ADR 0006) so the printed pack matches
    # what the MCP channel would serve. Idempotent when env already won.
    apply_descriptions_override(cli_path=None, configured_path=config.serve.descriptions_path)
    svc = _select_service(services, args.project_scope) if args.project_scope else services[0]
    pack = await build_session_start_context(
        uow_factory=svc.overview.uow_factory,
        overview=svc.overview,
        budget_tokens=config.serve.session_start_context.budget_tokens,
        package=args.package,
    )
    print(pack)


async def _run_symbol(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_symbol`` tool (``--depth`` summary/tree/source)."""
    from pydocs_mcp.application.mcp_inputs import SymbolInput

    tools = _build_cli_tools(args)
    payload = SymbolInput(target=args.target, depth=args.depth, project=args.project_scope)
    print((await tools.get_symbol(payload)).text)


async def _run_context(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_context`` tool — batched targets under one budget."""
    from pydocs_mcp.application.mcp_inputs import ContextInput

    tools = _build_cli_tools(args)
    payload = ContextInput(targets=args.targets, project=args.project_scope)
    print((await tools.get_context(payload)).text)


async def _run_refs(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_references`` tool (``--direction`` + graph traversal)."""
    from pydocs_mcp.application.mcp_inputs import ReferencesInput

    tools = _build_cli_tools(args)
    # ``limit`` is omitted when the client didn't pass ``--limit`` so the input
    # model's YAML-wired default_factory supplies the reference-graph default —
    # no literal duplicated here (single-source-of-truth defaults).
    fields = {"target": args.target, "direction": args.direction, "project": args.project_scope}
    if args.limit is not None:
        fields["limit"] = args.limit
    print((await tools.get_references(ReferencesInput(**fields))).text)


async def _run_why(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_why`` tool — decision search / per-target / dashboard.

    When ``decision_capture.enabled`` (the shipped default) the router dispatches
    to the real ``DecisionService`` (query → search, ``--target`` → per-target
    cards, neither → dashboard). With capture disabled the ``NullDecisionService``
    raises ``ServiceUnavailableError`` (a typed :class:`MCPToolError`); the
    ``_run_cmd`` boundary maps it to ``Error: …`` on stderr + exit 1, exactly
    like the MCP handler's error path.
    """
    from pydocs_mcp.application.mcp_inputs import WhyInput

    tools = _build_cli_tools(args)
    payload = WhyInput(query=args.query, targets=args.targets, project=args.project_scope)
    print((await tools.get_why(payload)).text)


async def _run_grep(args: argparse.Namespace) -> None:
    """Mirror the MCP ``grep`` tool — regex search over the discovery scope."""
    from pydocs_mcp.application.mcp_inputs import GrepInput

    tools = _build_cli_tools(args)
    # None flag values ARE the model defaults (the YAML files.* deployment
    # defaults resolve inside FileToolsService), so no omission dance here.
    payload = GrepInput(
        pattern=args.pattern,
        path=args.path,
        glob=args.glob,
        output_mode=args.output_mode,
        case_insensitive=args.case_insensitive,
        line_numbers=args.line_numbers,
        after_context=args.after_context,
        before_context=args.before_context,
        context=args.context,
        head_limit=args.head_limit,
        multiline=args.multiline,
        scope=args.scope,
        project=args.project_scope,
    )
    print((await tools.grep(payload)).text)


async def _run_glob(args: argparse.Namespace) -> None:
    """Mirror the MCP ``glob`` tool — name-pattern file finding, newest first."""
    from pydocs_mcp.application.mcp_inputs import GlobInput

    tools = _build_cli_tools(args)
    payload = GlobInput(
        pattern=args.pattern,
        path=args.path,
        head_limit=args.head_limit,
        project=args.project_scope,
    )
    print((await tools.glob(payload)).text)


async def _run_read_file(args: argparse.Namespace) -> None:
    """Mirror the MCP ``read_file`` tool — line-numbered reads in the corpus."""
    from pydocs_mcp.application.mcp_inputs import ReadFileInput

    tools = _build_cli_tools(args)
    payload = ReadFileInput(
        file_path=args.file_path,
        offset=args.offset,
        limit=args.limit,
        project=args.project_scope,
    )
    print((await tools.read_file(payload)).text)


# ``lookup --show`` → new-router routing. ``default``/``tree`` are get_symbol
# depths; the graph shows map 1:1 to get_references directions. ``context`` and
# empty-target ("list packages") are handled separately in ``_run_lookup``.
_ALIAS_DEPTH = {"default": "summary", "tree": "tree"}
_ALIAS_DIRECTION = frozenset({"callers", "callees", "inherits", "impact", "governed_by"})


async def _run_lookup(args: argparse.Namespace) -> None:
    """Deprecated ``lookup`` alias — warn on stderr, then delegate to the new router.

    Kept for one release so existing scripts keep working. ``--show`` maps onto
    the task-shaped tools: ``default``/``tree`` → ``get_symbol``; graph shows
    (``callers``/``callees``/``inherits``/``impact``) → ``get_references``;
    ``context`` → ``get_context``; an empty target preserves the old "list
    packages" behavior via ``get_overview``.
    """
    from pydocs_mcp.application.mcp_inputs import (
        ContextInput,
        OverviewInput,
        ReferencesInput,
        SymbolInput,
    )

    print(
        "'pydocs-mcp lookup' is deprecated — use 'pydocs-mcp symbol' "
        "(or refs/context per --show); routing there now.",
        file=sys.stderr,
    )
    tools = _build_cli_tools(args)
    project = args.project_scope

    # Empty target = "list packages" — the old lookup behavior for every --show;
    # get_overview(package="") renders that listing.
    if not args.target:
        print((await tools.get_overview(OverviewInput(package="", project=project))).text)
        return
    if args.show == "context":
        print((await tools.get_context(ContextInput(targets=[args.target], project=project))).text)
        return
    if args.show in _ALIAS_DIRECTION:
        payload = ReferencesInput(target=args.target, direction=args.show, project=project)
        print((await tools.get_references(payload)).text)
        return
    depth = _ALIAS_DEPTH[args.show]
    print(
        (await tools.get_symbol(SymbolInput(target=args.target, depth=depth, project=project))).text
    )


def _report_cli_failure(exc: Exception, *, verbose: bool) -> int:
    """Single source of truth for the user-facing CLI failure report.

    Under ``--verbose`` the full traceback lands on stderr (via
    ``traceback.print_exc``) AND the logger records it via
    ``log.exception``. With the default stderr-attached handler that
    duplicates the traceback — intentionally: a user who reconfigures the
    logger to a file or JSON formatter still needs the traceback there,
    and ``print_exc`` alone wouldn't reach a non-stderr handler. Without
    ``--verbose`` only the short ``Error: <msg>`` line plus a hint is
    printed, and ``log.error`` (no traceback) keeps the default
    stderr-attached logger from leaking it.

    Must be called from inside an ``except`` block — ``print_exc`` and
    ``log.exception`` read the active exception context.
    """
    print(f"Error: {exc}", file=sys.stderr)
    if verbose:
        traceback.print_exc(file=sys.stderr)
        log.exception("CLI command failed")
    else:
        print("(re-run with --verbose to see the traceback)", file=sys.stderr)
        log.error("CLI command failed: %s", exc)
    return 1


def _run_blocking(fn: Callable[[], None], *, verbose: bool) -> int:
    """Run a blocking (sync) entry point under the shared error policy.

    ``fn`` executes synchronously on the CALLER's thread — no thread hop —
    so when the caller is the main thread, Python's default SIGINT handler
    still reaches the blocking ``mcp.run`` / asyncio loops inside ``fn``
    (Python delivers SIGINT only to the main thread).
    KeyboardInterrupt is a graceful Ctrl+C shutdown, not an error: exit 0.
    """
    try:
        fn()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        return _report_cli_failure(exc, verbose=verbose)


def _run_cmd(coro: Awaitable[None], *, verbose: bool) -> int:
    """Async entry-point wrapper: run ``coro`` under the shared error policy.

    The diagnostic policy itself lives in :func:`_report_cli_failure`;
    this wrapper only owns the ``asyncio.run`` hop. Unlike
    :func:`_run_blocking` it does NOT treat KeyboardInterrupt as success —
    index/search/lookup have no long-running loop a user would Ctrl+C out
    of as a normal exit.
    """
    try:
        asyncio.run(coro)
        return 0
    except Exception as exc:
        return _report_cli_failure(exc, verbose=verbose)


def _cmd_index(args: argparse.Namespace) -> int:
    return _run_cmd(_run_indexing(args), verbose=args.verbose)


def _serve_run(
    args: argparse.Namespace,
    *,
    db_path: Path | None,
    workspace: Path | None,
    db_paths: list[Path] | None,
) -> int:
    """Run the MCP server (single-db or multi-repo) under the shared error policy.

    Kept on the main thread so the default SIGINT handler reaches the blocking
    ``mcp.run`` loop (see the no-watch rationale in ``_cmd_serve``).
    """
    from pydocs_mcp.server import run

    return _run_blocking(
        lambda: run(
            db_path,
            config_path=getattr(args, "config", None),
            gpu=getattr(args, "gpu", False),
            workspace=workspace,
            db_paths=db_paths,
            descriptions_path=getattr(args, "descriptions", None),
        ),
        verbose=getattr(args, "verbose", False),
    )


def _cmd_serve(args: argparse.Namespace) -> int:
    # Multi-repo serve (``--workspace`` / ``--db``): the dbs are pre-built and
    # read-only, so there is NO indexing phase and no watch — jump straight to the
    # server over the loaded bundles.
    workspace = getattr(args, "workspace", None)
    db_paths = getattr(args, "db_paths", None)
    multi = workspace is not None or bool(db_paths)

    if multi:
        return _serve_run(args, db_path=None, workspace=workspace, db_paths=db_paths)

    # Phase 1 — async indexing through ``_run_cmd`` so the verbose /
    # traceback policy applies to indexing failures uniformly.
    code = _run_cmd(_run_serve_indexing(args), verbose=args.verbose)
    if code != 0:
        return code

    _project, db_path = _project_and_db(args)

    from pydocs_mcp.retrieval.config import AppConfig

    # Either switch enables watch mode: the CLI flag, or the YAML key
    # (serve.watch.enabled — per-deployment opt-in). The flag cannot force
    # watching OFF when the key is true. Short-circuit keeps the flag path
    # free of a config load. Spec:
    # docs/superpowers/specs/2026-07-11-cli-mcp-docs-audit-spec.md (D3).
    watch_enabled = (
        getattr(args, "watch", False)
        or AppConfig.load(explicit_path=getattr(args, "config", None)).serve.watch.enabled
    )

    if watch_enabled:
        # Phase 2 (--watch path): server + watcher concurrently via
        # ``_run_watch_loop``. ``run(...)`` is offloaded to a worker
        # thread inside ``_run_watch_loop`` so the watcher's asyncio
        # consumer keeps draining events.
        #
        # WHY this differs from the no-watch path: without `--watch`,
        # `run(...)` is the only thing happening on the main thread, so
        # SIGINT reaches it directly. With `--watch`, the asyncio loop
        # is also running here, so the loop owns SIGINT; `run(...)`
        # exits via thread-pool unwind when the loop is cancelled.
        return _run_blocking(
            lambda: asyncio.run(_run_watch_loop(args, db_path=db_path)),
            verbose=args.verbose,
        )

    # Phase 2 (no-watch path) — unchanged from today.
    # ``server.run`` calls ``anyio.run(self.run_stdio_async)`` internally,
    # which starts its own event loop. Running that inside
    # ``asyncio.to_thread`` would dispatch it to a worker thread, but
    # Python only delivers SIGINT to the main thread and
    # ``asyncio.to_thread`` cannot cancel a running thread — so Ctrl+C
    # against ``pydocs-mcp serve`` would not interrupt cleanly. Run on
    # the main thread so the default SIGINT handler reaches the blocking
    # loop. The try / except mirrors ``_run_cmd``'s policy.
    return _serve_run(args, db_path=db_path, workspace=None, db_paths=None)


def _cmd_watch(args: argparse.Namespace) -> int:
    """Standalone watcher mode: index once + watch + reindex on edits.

    No MCP server runs in this path — for users who want fresh index
    state without an idle FastMCP stdio process. Same two-phase shape
    as ``_cmd_serve`` (initial index, then loop) but Phase 2 here is
    the watcher loop only.
    """
    # Phase 1: initial indexing (same as ``serve`` / ``index`` does at
    # startup). Routes through ``_run_cmd`` so the --verbose / traceback
    # policy applies uniformly.
    code = _run_cmd(_run_serve_indexing(args), verbose=args.verbose)
    if code != 0:
        return code

    # Phase 2 (watcher-only) — own asyncio.run so SIGINT (KeyboardInterrupt)
    # propagates through the asyncio loop and cancels the watcher's
    # ``run_until_cancelled``, which then tears down the Observer via
    # the try/finally inside ``FileWatcher.run_until_cancelled``.
    return _run_blocking(
        lambda: asyncio.run(_run_watch_only(args)),
        verbose=args.verbose,
    )


def _cmd_link(args: argparse.Namespace) -> int:
    """The ``link`` verb (spec §3.9): full/incremental pass or ``--check``."""
    import asyncio

    from pydocs_mcp.application.workspace_linker import WorkspaceLinker, detect_stale
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.server import (
        _build_similar_generator,
        _bundle_handles,
        _open_overlay_store,
        _overlay_candidates,
        _resolve_projects,
    )
    from pydocs_mcp.storage.factories import build_cross_link_store

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    workspace = Path(args.workspace).expanduser() if args.workspace else None
    db_paths = [Path(p).expanduser() for p in (args.db_paths or [])]
    projects, _read_only = _resolve_projects(None, workspace, db_paths)
    if len(projects) < 2:
        print("link: nothing to do — a workspace needs at least two bundles")
        return 0
    cross_cfg = config.reference_graph.cross_repo
    bundles = _bundle_handles(projects)

    if args.check:
        # AC21: --check tests existence only — it must NOT create the overlay.
        # Resolve the candidate paths and open only an EXISTING one; a missing
        # overlay reports unlinked without writing anything.
        existing = next(
            (p for p in _overlay_candidates(config, workspace, db_paths) if p.exists()), None
        )
        if existing is None:
            print("cross-repo links: stale(unlinked) — no overlay yet; run `pydocs-mcp link`")
            return 1
        check_store = build_cross_link_store(existing)

        async def _check() -> int:
            stamps = await check_store.bundle_stamps()
            stale = detect_stale(bundles, stamps)
            departed = {s.project_name for s in stamps} - {b.project for b in bundles}
            if stale or departed:
                print(f"stale: {sorted(stale) or '-'}; departed: {sorted(departed) or '-'}")
                return 1
            print("cross-repo links: fresh")
            return 0

        return asyncio.run(_check())

    store, persisted = _open_overlay_store(config, workspace, db_paths)

    async def _run() -> int:
        if not persisted:
            print(
                "link: overlay location is not writable (read-only filesystem?) — "
                "nothing persisted. Serve still links in memory at startup."
            )
            return 2
        linker = WorkspaceLinker(
            bundles=bundles,
            cross_links=store,
            kinds=tuple(ReferenceKind(k) for k in cross_cfg.kinds),
            match_scope=cross_cfg.match_scope,
            alias_resolution=cross_cfg.alias_resolution,
            workspace_scores=cross_cfg.workspace_scores,
            similar_generator=_build_similar_generator(config),
        )
        # The explicit verb is always a FULL pass (spec §3.9) — the operator
        # asked for a refresh; incremental repair is the serve path's job.
        report = await linker.link(None)
        for project in sorted({b.project for b in bundles}):
            print(
                f"{project}: scanned {report.unresolved_scanned.get(project, 0)} unresolved, "
                f"created {report.edges_created.get(project, 0)} edge(s), "
                f"{report.collisions.get(project, 0)} collision(s)"
            )
        print(
            f"alias resolved {report.alias_resolved}, ambiguous {report.alias_ambiguous}; "
            f"workspace scores: {'computed' if report.workspace_scores_computed else 'skipped'}"
            f"{'' if report.pagerank_available else ' (pagerank unavailable — [graph] extra)'}"
        )
        if report.per_pair_similar_seconds:
            timings = ", ".join(
                f"{pair} {seconds:.2f}s"
                for pair, seconds in sorted(report.per_pair_similar_seconds.items())
            )
            print(
                f"similar edges {report.similar_edges}, "
                f"embedder mismatches {report.embedder_mismatches} ({timings})"
            )
        return 0

    return asyncio.run(_run())


def _cmd_search(args: argparse.Namespace) -> int:
    return _run_cmd(_run_search(args), verbose=args.verbose)


def _cmd_overview(args: argparse.Namespace) -> int:
    return _run_cmd(_run_overview(args), verbose=args.verbose)


def _cmd_symbol(args: argparse.Namespace) -> int:
    return _run_cmd(_run_symbol(args), verbose=args.verbose)


def _cmd_context(args: argparse.Namespace) -> int:
    return _run_cmd(_run_context(args), verbose=args.verbose)


def _cmd_refs(args: argparse.Namespace) -> int:
    return _run_cmd(_run_refs(args), verbose=args.verbose)


def _cmd_why(args: argparse.Namespace) -> int:
    return _run_cmd(_run_why(args), verbose=args.verbose)


def _cmd_lookup(args: argparse.Namespace) -> int:
    return _run_cmd(_run_lookup(args), verbose=args.verbose)


def _cmd_grep(args: argparse.Namespace) -> int:
    return _run_cmd(_run_grep(args), verbose=args.verbose)


def _cmd_glob(args: argparse.Namespace) -> int:
    return _run_cmd(_run_glob(args), verbose=args.verbose)


def _cmd_read_file(args: argparse.Namespace) -> int:
    return _run_cmd(_run_read_file(args), verbose=args.verbose)


def _cmd_session_start_context(args: argparse.Namespace) -> int:
    return _run_cmd(_run_session_start_context(args), verbose=args.verbose)


# ── Entry point ───────────────────────────────────────────────────────────


# argparse stores the TYPED subcommand string in ``args.cmd`` — an alias
# invocation never rewrites itself to the canonical name — so the dispatch
# table carries both spellings, pointing at one handler each (contract §6
# note 4: canonical tool names + historical short-verb aliases).
_CMD_TABLE = {
    "serve": _cmd_serve,
    "index": _cmd_index,
    "watch": _cmd_watch,
    "link": _cmd_link,
    "search_codebase": _cmd_search,
    "search": _cmd_search,
    "get_overview": _cmd_overview,
    "overview": _cmd_overview,
    "get_symbol": _cmd_symbol,
    "symbol": _cmd_symbol,
    "get_context": _cmd_context,
    "context": _cmd_context,
    "get_references": _cmd_refs,
    "refs": _cmd_refs,
    "get_why": _cmd_why,
    "why": _cmd_why,
    "grep": _cmd_grep,
    "glob": _cmd_glob,
    "read_file": _cmd_read_file,
    "session-start-context": _cmd_session_start_context,
    "lookup": _cmd_lookup,
}


def _apply_descriptions_env_override() -> int | None:
    """Apply an exported ``PYDOCS_SERVE__DESCRIPTIONS_PATH`` override, if any.

    Must run BEFORE ``_build_parser()``: the argparse tree snapshots the
    ``tool_docs`` prose, so applying afterwards would leave ``--help``
    rendering the packaged bundle while the MCP server serves the override
    (breaking CLI/MCP parity, R2 / ADR 0006 §2). A set-but-missing/invalid
    env source is a hard error (universal strictness) — returns the exit
    code; ``None`` means continue. A SET-but-EMPTY env var is a hard error
    too (it would silently clobber a YAML-configured path in the
    pydantic-settings merge); only a genuinely unset var falls through.
    """
    from pydocs_mcp.application import description_override

    env_value = os.environ.get(description_override.DESCRIPTIONS_PATH_ENV_VAR)
    if env_value is None:
        return None
    try:
        description_override.apply_descriptions_override(cli_path=None, configured_path=env_value)
    except Exception as exc:
        # ``--verbose`` is parsed later than this can run, so the default
        # (non-verbose) failure policy applies.
        return _report_cli_failure(exc, verbose=False)
    return None


def _argv_names_descriptions_flag(argv: list[str]) -> bool:
    """True when a ``--descriptions`` flag is present in raw argv."""
    return any(arg == "--descriptions" or arg.startswith("--descriptions=") for arg in argv)


def main() -> int:
    # WHY the argv scan: documented precedence is flag > env, and that must
    # hold on the FAILURE path too — a set-but-invalid env var must not kill
    # a serve run whose ``--descriptions`` flag names a valid source before
    # argparse even runs. A flag-carrying invocation is a serve run (never a
    # bare ``--help`` render), so skipping the pre-apply here cannot break
    # CLI-help parity; ``server.run`` applies the flag as the winning source.
    if not _argv_names_descriptions_flag(sys.argv[1:]):
        code = _apply_descriptions_env_override()
        if code is not None:
            return code
    parser = _build_parser()
    args = parser.parse_args()
    _configure_logging(args.verbose)

    if not args.cmd:
        parser.print_help()
        return 0

    _apply_no_rust_flag(args)
    handler = _CMD_TABLE[args.cmd]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
