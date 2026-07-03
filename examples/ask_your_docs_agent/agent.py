"""Ask-your-docs agent — a LangGraph ReAct agent over pydocs-mcp.

Points a conversational agent at a directory of pre-built pydocs-mcp indexes
(multi-repo bundles) and answers questions about their documentation and code,
grounded in the `search` / `lookup` MCP tools.

Terminal:   python agent.py --workspace ~/pydocs-index --model gpt-4o-mini
Notebook:   agent, history = await build_agent(...);  await ask(agent, history, "...")
"""

from __future__ import annotations

import argparse
import asyncio

from langchain_core.messages import AIMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

SYSTEM_PROMPT = """\
You are a documentation and code assistant for the indexed projects listed below.
You answer ONLY from the results of your two tools — never from memory:

- `search(query, kind, package, scope, limit, project)` — topics, keywords,
  "how do I..." questions. Use kind="docs" for prose, kind="api" for
  functions/classes. Use project="<name>" to scope one repo, package="<name>"
  for one library, scope="project"|"deps" to split own-code vs dependencies.
- `lookup(target, show, project)` — exact dotted paths (pkg.mod.Class.method)
  and code-graph questions: show="callers" (who uses X), "callees",
  "impact" (what breaks if X changes), "context" (everything to understand X).

Rules:
1. Users often don't know the framework or project name. Infer it from the
   task and the indexed-projects list below. If unsure, search UNSCOPED first
   (all projects) and let the results identify the owner, then narrow.
2. Rewrite follow-up questions into self-contained queries (resolve "it",
   "that function", ... from the conversation) before calling a tool.
3. If results stay ambiguous across projects, or the request is unclear, ask
   ONE short clarifying question instead of guessing.
4. Be concise. Cite the project and package.module for every claim. Put
   signatures and code in fenced code blocks. If the tools found nothing,
   say so plainly — do not invent an answer.
"""

REWRITE_PROMPT = """\
Rewrite the user's last question as ONE self-contained question, resolving any
references to the earlier conversation. Return only the rewritten question.

Conversation:
{history}

Last question: {question}
"""


async def build_agent(
    workspace: str,
    model: str,
    base_url: str | None = None,
    pydocs_config: str | None = None,
    pydocs_cmd: str = "pydocs-mcp",
):
    """Start pydocs-mcp over the workspace and return a ready ReAct agent.

    Returns (agent, llm). The MCP server runs as a stdio subprocess for the
    lifetime of the process.
    """
    # NOTE: --config is a root flag and must come BEFORE the serve subcommand.
    args = ["serve", "--workspace", workspace]
    if pydocs_config:
        args = ["--config", pydocs_config, *args]
    client = MultiServerMCPClient(
        {"pydocs": {"transport": "stdio", "command": pydocs_cmd, "args": args}}
    )
    tools = await client.get_tools()

    # Tell the model what is actually indexed (projects + packages) so it can
    # infer which project a task refers to when the user doesn't name one.
    lookup = next(t for t in tools if t.name == "lookup")
    listing = await lookup.ainvoke({"target": ""})
    prompt = f"{SYSTEM_PROMPT}\nIndexed projects and packages:\n{listing}"

    llm = ChatOpenAI(model=model, base_url=base_url)
    return create_react_agent(llm, tools, prompt=prompt), llm


async def reformulate(llm: ChatOpenAI, history: list, question: str) -> str:
    """Condense the last question + conversation into a standalone question."""
    if not history:
        return question
    lines = "\n".join(f"{m.type}: {m.content}" for m in history)
    reply = await llm.ainvoke(REWRITE_PROMPT.format(history=lines, question=question))
    return str(reply.content).strip() or question


async def ask(agent, history: list, question: str, max_history: int = 8) -> str:
    """One conversation turn: invoke the agent and update `history` in place."""
    result = await agent.ainvoke({"messages": [*history, HumanMessage(question)]})
    answer = result["messages"][-1].content
    history += [HumanMessage(question), AIMessage(answer)]
    del history[:-max_history]  # keep only the last N messages
    return answer


async def chat(args: argparse.Namespace) -> None:
    """Terminal REPL. Type a question; 'exit' to quit."""
    agent, llm = await build_agent(
        args.workspace, args.model, args.base_url, args.pydocs_config, args.pydocs_cmd
    )
    history: list = []
    print("Ready. Ask about your indexed projects ('exit' to quit).")
    while True:
        try:
            question = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in ("exit", "quit"):
            break
        question = await reformulate(llm, history, question)
        answer = await ask(agent, history, question, args.history)
        print(f"\nagent> {answer}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True, help="Directory of pre-built .db bundles")
    p.add_argument("--model", required=True, help="LLM name (any OpenAI-API-compatible model)")
    p.add_argument(
        "--base-url", default=None, help="OpenAI-compatible endpoint (vLLM, Ollama, ...)"
    )
    p.add_argument(
        "--pydocs-config",
        default=None,
        help="pydocs-mcp YAML (e.g. configs/serve_cpu_openvino.yaml)",
    )
    p.add_argument(
        "--history", type=int, default=8, help="How many past messages to keep (default 8)"
    )
    p.add_argument(
        "--pydocs-cmd", default="pydocs-mcp", help="pydocs-mcp executable (default: pydocs-mcp)"
    )
    asyncio.run(chat(p.parse_args()))


if __name__ == "__main__":
    main()
