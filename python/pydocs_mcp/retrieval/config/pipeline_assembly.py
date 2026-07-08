"""Pipeline assembly — YAML pipeline loading + the ``pipeline_path`` allowlist.

Isolated so the path-allowlist logic (the only security-sensitive code in
retrieval config: a user-controlled ``pipeline_path`` must never read files
outside the shipped ``pipelines/`` dir + the user-config directory) has its
own module and reason to change.
"""

from __future__ import annotations

import importlib.resources
from functools import cache
from pathlib import Path

import yaml

# Side-effect import: populate the step/formatter registries via their
# decorators BEFORE any pipeline YAML is decoded. Lives here — not in the
# model modules — because registries only matter at YAML decode time.
from pydocs_mcp.retrieval import formatters as _formatters  # noqa: F401
from pydocs_mcp.retrieval.config.app_config import AppConfig
from pydocs_mcp.retrieval.config.models import HandlerConfig
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
from pydocs_mcp.retrieval.serialization import BuildContext

# WHY: ``RouteCase`` / ``RouteStep`` are imported lazily inside
# :func:`_build_handler_pipeline` rather than top-level. After the
# Task-9 corpse removal removed the eager ``retrievers`` side-effect
# import from :mod:`pydocs_mcp.retrieval.__init__`, the import chain
# ``retrieval.steps`` → ``token_budget`` → ``application`` →
# ``storage`` → ``extraction.reference_capture`` → ``retrieval.config``
# hits this module before ``retrieval.steps.__init__`` has finished
# binding ``RouteCase`` / ``RouteStep``. Deferring the import to
# function scope breaks the cycle without changing call-site shape.


@cache
def _shipped_pipelines_dir() -> Path:
    """Resolved path to the ``pydocs_mcp/pipelines/`` directory (spec §5.9).

    Cached for the same reason as :func:`_shipped_default_config_path` —
    the pipeline-path allowlist recomputes this on every YAML load.
    """
    return Path(str(importlib.resources.files("pydocs_mcp.pipelines"))).resolve()


# ── Pipeline assembly ───────────────────────────────────────────────────


def build_chunk_pipeline_from_config(
    config: AppConfig,
    context: BuildContext,
) -> CodeRetrieverPipeline:
    return _build_handler_pipeline(
        "chunk",
        config.pipelines["chunk"],
        context,
        config._user_config_path(),
    )


def build_member_pipeline_from_config(
    config: AppConfig,
    context: BuildContext,
) -> CodeRetrieverPipeline:
    return _build_handler_pipeline(
        "member",
        config.pipelines["member"],
        context,
        config._user_config_path(),
    )


def _pipeline_path_allowed_roots(user_config_path: Path | None) -> tuple[Path, ...]:
    """Return the directories a ``pipeline_path`` may resolve inside.

    A YAML config is user-controlled input — unrestricted ``pipeline_path``
    would happily load ``/etc/shadow`` (and surface the contents in the
    subsequent YAML parse error). Keep the allowlist to:

    1. The shipped ``pydocs_mcp/pipelines/`` directory (the baseline YAMLs).
    2. The directory that contains the user's config file, if they supplied
       one — so ``./my_pipeline.yaml`` next to ``pydocs-mcp.yaml`` works.
    """
    roots = [_shipped_pipelines_dir()]
    if user_config_path is not None:
        roots.append(user_config_path.resolve().parent)
    return tuple(roots)


def _path_is_inside(candidate: Path, roots: tuple[Path, ...]) -> bool:
    """Return True iff ``candidate`` (already resolved) sits inside any root."""
    for root in roots:
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _resolve_pipeline_path(
    pipeline_path: Path,
    user_config_path: Path | None = None,
) -> Path:
    """Resolve a YAML ``pipeline_path`` against the user/shipped roots.

    Relative paths are first tried under the user's config directory, then
    under the shipped ``pipelines/`` dir. Absolute paths are accepted only if
    they land inside the allowlist. Symlinks are resolved before the check
    so a symlink planted inside ``pipelines/`` pointing at ``/etc/shadow`` is
    rejected.
    """
    allowed_roots = _pipeline_path_allowed_roots(user_config_path)
    pipelines_dir = _shipped_pipelines_dir()

    if pipeline_path.is_absolute():
        resolved = pipeline_path.resolve()
    else:
        parts = pipeline_path.parts
        # ``presets/...`` is the pre-refactor convention; give a clear
        # migration error rather than a confusing FileNotFoundError.
        if parts and parts[0] == "presets":
            raise ValueError(
                f"pipeline_path={pipeline_path!s}: the 'presets/' prefix was "
                f"renamed to 'pipelines/' (chunk_fts.yaml → chunk_search.yaml, "
                f"member_like.yaml → member_search.yaml). Update your "
                f"pydocs-mcp.yaml accordingly."
            )
        # ``pipelines/foo.yaml`` uses search-path semantics: user-dir wins
        # when the file is present locally (so a user can override the shipped
        # pipeline by dropping their own ``pipelines/chunk_search.yaml`` next
        # to their config), otherwise falls back to the shipped dir. This
        # lets ``default_config.yaml`` reference bundled YAMLs without
        # knowing the install path AND lets users override them.
        if parts and parts[0] == "pipelines":
            user_local = None
            if user_config_path is not None:
                user_local = (user_config_path.resolve().parent / pipeline_path).resolve()
            if user_local is not None and user_local.exists():
                candidate = user_local
            else:
                candidate = Path(
                    str(importlib.resources.files("pydocs_mcp").joinpath(str(pipeline_path)))
                ).resolve()
        else:
            base = (
                user_config_path.resolve().parent if user_config_path is not None else pipelines_dir
            )
            candidate = (base / pipeline_path).resolve()
        resolved = candidate

    if not _path_is_inside(resolved, allowed_roots):
        raise ValueError(
            f"pipeline_path must be inside one of {sorted(str(r) for r in allowed_roots)}; "
            f"got {pipeline_path!s} (resolved to {resolved!s})"
        )
    return resolved


def _load_preset_yaml(path: Path, context: BuildContext) -> CodeRetrieverPipeline:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return CodeRetrieverPipeline.from_dict(data, context)


def _build_handler_pipeline(
    handler_name: str,
    handler_config: HandlerConfig,
    context: BuildContext,
    user_config_path: Path | None = None,
) -> CodeRetrieverPipeline:
    # Lazy import — see top-level WHY note. Breaks the
    # ``retrieval.steps`` ⇄ ``retrieval.config`` cycle that runs through
    # the extraction-side reference-capture stage at import time.
    from pydocs_mcp.retrieval.steps import RouteCase, RouteStep

    if not handler_config.routes:
        # WHY: fail at config-load time, not on the first live query. An
        # empty route list previously assembled a RouteStep(routes=(),
        # default=None) whose run() returns the input state unchanged —
        # every search against this handler silently yielded zero results,
        # indistinguishable from an empty index.
        raise ValueError(f"{handler_name}: handler has no routes")

    routes: list[RouteCase] = []
    default: CodeRetrieverPipeline | None = None
    for entry in handler_config.routes:
        resolved = _resolve_pipeline_path(entry.pipeline_path, user_config_path)
        # WHY: a CodeRetrieverPipeline subclasses ``RetrieverStep``, so we
        # slot it directly into ``RouteCase.stage`` — no adapter needed.
        sub_pipeline = _load_preset_yaml(resolved, context)
        # PipelineRouteEntry guarantees exactly-one-of, so we needn't re-validate
        if entry.default:
            if default is not None:
                raise ValueError(f"{handler_name}: multiple default routes declared")
            default = sub_pipeline
        else:
            # predicate must be set — guaranteed by PipelineRouteEntry validator
            predicate_name = entry.predicate
            if predicate_name is None:
                raise ValueError(
                    f"{handler_name}: route entry missing predicate; "
                    "PipelineRouteEntry validator should have caught this"
                )
            # WHY: fail at config-load time, not on the first live query.
            # RouteStep.run resolves predicate names lazily via
            # registry.get(...), so a typo'd name (e.g. "scope_is_deps_only"
            # instead of "scope_is_dependencies_only") previously built a
            # green pipeline and only raised KeyError from inside a request.
            context.predicate_registry.get(predicate_name)
            routes.append(RouteCase(predicate_name=predicate_name, stage=sub_pipeline))
    if not routes and default is not None:
        # Single-default route collapses to the inner pipeline directly so
        # callers inspecting pipeline.stages see the preset's stage list,
        # not a RouteStep wrapper (preserves sub-PR #2's golden parity).
        return CodeRetrieverPipeline(name=default.name, stages=default.stages)
    if default is None:
        # WHY: fail at config-load time, not on the first live query. A
        # predicate-only route list with no default previously built a
        # RouteStep that silently no-ops (returns the state unchanged) for
        # any query matching none of the predicates.
        raise ValueError(f"{handler_name}: handler has no default route")
    return CodeRetrieverPipeline(
        name=f"{handler_name}_from_config",
        stages=(RouteStep(routes=tuple(routes), default=default),),
    )
