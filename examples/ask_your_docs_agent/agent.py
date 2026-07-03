"""Ask-your-docs agent — a LangGraph ReAct agent over pydocs-mcp.

Used by the Streamlit app (`streamlit run streamlit_app.py`) and the
notebook (`notebook.ipynb`):

    agent, llm = await build_agent("~/pydocs-index", model="gpt-4o-mini")
    history = []
    answer = await ask(agent, history, "how do I open a database pool?")
"""

from __future__ import annotations

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
    """Start pydocs-mcp over the workspace; return (agent, llm)."""
    # --config is a root flag: it must come BEFORE the serve subcommand.
    args = ["serve", "--workspace", workspace]
    if pydocs_config:
        args = ["--config", pydocs_config, *args]
    client = MultiServerMCPClient(
        {"pydocs": {"transport": "stdio", "command": pydocs_cmd, "args": args}}
    )
    tools = await client.get_tools()

    # Fold the indexed projects/packages listing into the prompt so the model
    # can infer which project a task refers to.
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
    del history[:-max_history]
    return answer
