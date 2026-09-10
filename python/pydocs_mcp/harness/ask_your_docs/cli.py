"""``harness-ask-your-docs`` — launch the Streamlit chat UI.

A thin wrapper over ``streamlit run app.py`` that forwards connection settings
as env vars (the sidebar prefills from them) and pins the dark theme base so
Streamlit's native chrome matches the in-app CSS.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.theme import streamlit_theme_flags
from pydocs_mcp.retrieval.config.ask_your_docs_models import _DEFAULT_MODEL

# WHY private names for --base-url / --model (0.6.1): the serve child inherits
# this process's environment (harness.core.serve_child_env). Written into
# OPENAI_BASE_URL, the CHAT endpoint re-pointed the child's OpenAI embedder and
# LLM client (both fall back to OPENAI_BASE_URL when YAML leaves base_url null)
# and sent the embedding key to the chat host. app.py reads these as the CLI
# tier of the LLM-connection precedence (spec R3), so the resolved chat
# endpoint is unchanged.
LAUNCH_BASE_URL_ENV_VAR = "HARNESS_ASK_YOUR_DOCS_BASE_URL"
LAUNCH_MODEL_ENV_VAR = "HARNESS_ASK_YOUR_DOCS_MODEL"
_LAUNCHER_OWNED = (LAUNCH_BASE_URL_ENV_VAR, LAUNCH_MODEL_ENV_VAR)

_ENV = {
    "workspace": "PYDOCS_WORKSPACE",
    "model": LAUNCH_MODEL_ENV_VAR,
    "base_url": LAUNCH_BASE_URL_ENV_VAR,
    "config": "PYDOCS_CONFIG",
}

# The agent stack (langgraph / langchain / streamlit) ships only with the
# optional extra, so point the user at it if they run the bare install.
_EXTRA_MODULES = ("streamlit", "langgraph", "langchain_mcp_adapters", "langchain_openai")

_DEFAULT_PORT = 8501


def _require_extra() -> None:
    # Thin wrapper (kept as the stable monkeypatch target for tests): the
    # guard itself is the harness-generic core seam.
    from pydocs_mcp.harness.core.extra_guard import require_extra_modules

    require_extra_modules(
        _EXTRA_MODULES, extra="harness-ask-your-docs", command="harness-ask-your-docs"
    )


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse tree — importable with core deps only.

    Kept as a named helper (mirroring ``pydocs_mcp.__main__._build_parser``)
    so doc-conformance tests can help-level-validate documented
    ``ask-your-docs`` invocations without the ``[harness-ask-your-docs]`` extra
    installed; ``main`` still gates execution on ``_require_extra``.
    """
    parser = argparse.ArgumentParser(prog="harness-ask-your-docs", description=__doc__)
    parser.add_argument("--workspace", help="folder of pydocs-mcp .db/.tq index bundles")
    parser.add_argument(
        "--model",
        help=(
            f"OpenAI-format model id (default without an ask_your_docs.llm block: {_DEFAULT_MODEL}); "
            "overrides ask_your_docs.llm.model and LLM_MODEL"
        ),
    )
    parser.add_argument(
        "--base-url",
        help="OpenAI-format base URL; overrides ask_your_docs.llm.base_url and OPENAI_BASE_URL",
    )
    parser.add_argument("--config", help="pydocs-mcp config YAML (embedder must match the bundles)")
    parser.add_argument(
        "--port", type=int, default=_DEFAULT_PORT, help=f"Streamlit port (default: {_DEFAULT_PORT})"
    )
    parser.add_argument(
        "streamlit_args",
        nargs=argparse.REMAINDER,
        help="extra args after -- are passed straight to `streamlit run`",
    )
    return parser


def _launch_env(args: argparse.Namespace) -> dict[str, str]:
    """This process's environment plus the given flags, under the names ``app.py`` reads."""
    env = os.environ.copy()
    for var in _LAUNCHER_OWNED:  # a stale shell export must not pose as a flag
        env.pop(var, None)
    for flag, var in _ENV.items():
        if value := getattr(args, flag):
            env[var] = value
    return env


def main(argv: list[str] | None = None) -> int:
    _require_extra()
    args = _build_parser().parse_args(argv)
    env = _launch_env(args)

    extra = args.streamlit_args[1:] if args.streamlit_args[:1] == ["--"] else args.streamlit_args
    app = Path(__file__).with_name("app.py")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "--server.port",
        str(args.port),
        *streamlit_theme_flags(),
        str(app),
        *extra,
    ]
    # cmd is developer-controlled (our own interpreter + streamlit + flags);
    # trailing args are the operator's own passthrough, not remote input.
    return subprocess.run(cmd, env=env, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
