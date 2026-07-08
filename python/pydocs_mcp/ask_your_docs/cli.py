"""``ask-your-docs`` — launch the Streamlit chat UI.

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

from pydocs_mcp.ask_your_docs.theme import streamlit_theme_flags

_ENV = {
    "workspace": "PYDOCS_WORKSPACE",
    "model": "LLM_MODEL",
    "base_url": "OPENAI_BASE_URL",
    "config": "PYDOCS_CONFIG",
}

# The agent stack (langgraph / langchain / streamlit) ships only with the
# optional extra, so point the user at it if they run the bare install.
_EXTRA_MODULES = ("streamlit", "langgraph", "langchain_mcp_adapters", "langchain_openai")


def _require_extra() -> None:
    from importlib.util import find_spec

    missing = [m for m in _EXTRA_MODULES if find_spec(m) is None]
    if missing:
        raise SystemExit(
            f"ask-your-docs needs the optional agent stack (missing: {', '.join(missing)}). "
            "Install it with:  pip install 'pydocs-mcp[ask-your-docs]'"
        )


def main(argv: list[str] | None = None) -> int:
    _require_extra()
    parser = argparse.ArgumentParser(prog="ask-your-docs", description=__doc__)
    parser.add_argument("--workspace", help="folder of pydocs-mcp .db/.tq index bundles")
    parser.add_argument("--model", help="OpenAI-protocol model name (default: gpt-4o-mini)")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL (vLLM/Ollama/LiteLLM)")
    parser.add_argument("--config", help="pydocs-mcp config YAML (embedder must match the bundles)")
    parser.add_argument("--port", type=int, default=8501, help="Streamlit port (default: 8501)")
    parser.add_argument(
        "streamlit_args",
        nargs=argparse.REMAINDER,
        help="extra args after -- are passed straight to `streamlit run`",
    )
    args = parser.parse_args(argv)

    env = os.environ.copy()
    for flag, var in _ENV.items():
        if value := getattr(args, flag):
            env[var] = value

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
