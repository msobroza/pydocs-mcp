# Multimodal model auto-detection + image-aware ask agent via architecture registry

| | |
|---|---|
| **Version** | 0.1 (draft) |
| **Status** | Proposed |
| **Date** | 2026-07-11 |
| **Audience** | Implementers + reviewers |
| **Component** | `pydocs_mcp/ask_your_docs/` (the `[ask-your-docs]` extra) + `retrieval/config/` (AppConfig sub-model) |

## 1. Context & problem statement

The ask-your-docs chat agent is a text-only LangGraph ReAct agent over the six
task-shaped MCP tools. Users increasingly want to attach an **image** to a
question — a screenshot of a stack trace, an architecture diagram, a plot of a
failing benchmark — and have the agent use it. Today nothing in the codebase
handles images at all:

- **Zero multimodal code exists.** `grep -rni 'multimodal|image_url|file_uploader|input_image'`
  over `python/pydocs_mcp/` and `examples/` returns no matches (verified
  2026-07-11 on this worktree); the only `vision`/`base64` hits anywhere are
  unrelated — comments in the notebook chunker about *stripping* base64
  outputs (`extraction/strategies/chunkers/notebook.py:5,158`), stdlib name
  data (`defaults/stdlib_qnames.json`), and substring hits like
  "ZeroDivisionError". This spec starts from a blank slate.
- **"Attachments" today are symbol names, not files.** The graph-explorer page
  appends dotted node ids to `st.session_state["attached"]`
  (`ask_your_docs/pages/2_Graph.py:254-258`), and
  `weave_attachments(attached, question)` prepends them as backticked plain
  text — `f"Regarding {names}: {question}"` — with empty-list identity
  (`ask_your_docs/agent.py:130-139`). There is no image branch.
- **The message path is `str` end to end.** `st.chat_input` (text-only, no
  `accept_file`) → `weave_attachments` → `reformulate` → `ask` →
  `HumanMessage(scope_prefix(scope) + question)`
  (`app.py:143-155`, `agent.py:208-209`). Every `HumanMessage` content is a
  plain string.
- **`reformulate()` would break on multimodal messages.** It string-serializes
  history via `"\n".join(f"{m.type}: {m.content}" ...)` and formats one prompt
  string (`agent.py:182-188`, `REWRITE_PROMPT` at `agent.py:88-96`). A
  content-block message (`content` = list of dicts) would be mangled into
  Python-repr noise. The rewrite step is a mandatory touch-point for any image
  design.
- **History policy is deliberate and must be extended, not broken.** `ask()`
  stores the BARE question/answer and trims to `max_history=8`; the scope-pin
  note is transient by design (`agent.py:198-214`, docstring lines 200-204).
  An image design must decide whether image bytes persist in history (token
  cost on every later turn) or are transient like the pin.
- **The agent graph is prebuilt, not hand-assembled.** The only LangGraph
  usage is `create_react_agent(llm, tools, prompt=prompt)`
  (`agent.py:20`, `agent.py:179`); there is no `StateGraph`/`add_node`
  anywhere in `python/pydocs_mcp`. A vision subagent would be the repo's first
  custom graph.
- **The agent LLM bypasses `AppConfig.llm`.** It is
  `ChatOpenAI(model=model, base_url=base_url)` from `langchain_openai`
  (`agent.py:19`, `agent.py:178`) — independent of the retrieval-side
  `LlmConfig` / `LlmClient`. The retrieval `LlmClient` Protocol is text-only
  (`ChatMessage.content: str`, `retrieval/protocols.py:143-144`), so the
  ChatOpenAI path is the only place multimodal messages can flow without a
  Protocol change.
- **The model may or may not be multimodal, and we don't know which.** The
  sidebar model defaults to the literal `"gpt-4o-mini"` (`app.py:65-68`), but
  users point the app at local OpenAI-compatible servers (vLLM / Ollama /
  LiteLLM, `cli.py:46`) serving arbitrary models. Sending an `image_url`
  content block to a text-only model yields a provider-specific error or —
  worse — a silent hallucinated answer.

**The problem, precisely:** (1) detect automatically whether the configured
model accepts images; (2) accept image attachments in the chat UI; (3) route
the image through an agent architecture appropriate to the model's capability;
(4) do all of this behind a **registry of agent architectures** selected via
YAML/settings — the same registry+decorator seam
(`@step_registry.register(...)`, CLAUDE.md §Design Patterns) that future specs
(agent auto-optimization, the spec-driven harness) will reuse to enumerate,
build, and A/B-benchmark architectures without touching call sites; (5)
degrade gracefully — never silently drop a user's image — when the model is
text-only.

### Governing constitution rules (cited throughout)

- **MCP surface is FIXED at six tools.** Nothing here touches `server.py`;
  everything lands agent-side, behind the surface (CLAUDE.md §MCP API surface
  vs YAML configuration). The ask-your-docs agent is not part of the MCP
  surface, but the same doctrine applies: *"if a new behavior could be A/B
  tested against a benchmark to measure quality, it belongs in YAML."* Which
  agent architecture answers a question is exactly such a behavior.
- **Optional heavy deps stay opt-in.** Everything ships inside the existing
  `[ask-your-docs]` extra (`pyproject.toml:90-96`); the ~90MB default install
  is untouched. New modules keep heavy imports lazy per the PEP 562 pattern in
  `ask_your_docs/__init__.py:21-38`.
- **Registry + decorator** for YAML-addressable extensions; **frozen slotted
  dataclasses** for value objects; **Null Object asymmetry** for degradation
  (`application/null_services.py:72,99` raise; `storage/null_vector_store.py`
  is silent); **single source of truth** for every default
  (`_DEFAULT_X` / pydantic `Field`); **duplicate registration is a hard
  `ValueError` at import time** (all three registry precedents:
  `retrieval/serialization.py:45-47`, `retrieval/route_predicates.py:20-22`,
  `extraction/serialization.py:59-66`).

## 2. Goals / Non-goals

### Goals

1. **Automatic multimodal detection** for the configured `(model, base_url)`
   pair, layered: explicit override → static prefix table → optional endpoint
   metadata probe → optional one-shot tiny-image probe → conservative
   text-only default. Result cached per pair.
2. **Image attachments in the chat UI** — a new attachment channel alongside
   the existing symbol-name channel, with size/count limits and visible chips.
3. **Three agent architectures**, each independently buildable and
   introspectable (`graph.get_graph().draw_mermaid_png()` keeps working per
   entry, as the README's `agent-graph.png` workflow expects,
   `examples/ask_your_docs_agent/README.md:62-63`):
   - `inline` — one multimodal `HumanMessage` to the main ReAct agent, plus an
     image-analysis section appended to the system prompt.
   - `vision_subagent` — a LangGraph subgraph: a vision node extracts
     structured, question-guided facts from the image; the text-only main
     agent consumes those facts as woven text.
   - `auto` (conditional hybrid) — routes between the two above (and the
     text-only degradation path) based on the detection result.
4. **An agent-architecture registry** (`agent_registry`) mirroring
   `step_registry` / `stage_registry`: `@agent_registry.register("name")`
   decorators, entries build LangGraph graphs from a frozen
   `AgentBuildContext`, selection via YAML (`ask_your_docs.architecture`).
   Designed as the extension seam for future specs: enumerable
   (`agent_registry.names()`), buildable headlessly (no Streamlit import),
   benchmark-iterable over `configs/*.yaml`.
5. **Graceful degradation** on text-only models: images are user-requested
   content, so the default is an actionable rejection (the
   `NullTreeService` side of the asymmetry), with an opt-in
   `describe` mode (apologize + answer from text only).
6. **TDD**: every behavior lands with named tests; existing
   `weave_attachments` contracts (`tests/ask_your_docs/test_attachment.py:8-18`)
   remain green unchanged.

### Non-goals

- **No change to the retrieval-side `LlmClient` Protocol** — it stays
  text-only (`retrieval/protocols.py:143-195`). Images flow only through the
  agent's ChatOpenAI path.
- **No new MCP tools or parameters.** The six-tool surface and `server.py`
  are untouched.
- **No OCR / local vision models.** Vision capability comes from the
  configured chat model only; we do not add `pillow`-based OCR, CLIP, or any
  local vision dependency in this spec.
- **No image indexing.** Images are per-question ephemera; they never enter
  the SQLite/TurboQuant index.
- **No image persistence in conversation history** beyond a textual
  placeholder (design decision, §3.6).
- **No benchmark harness integration in this spec** — the registry is
  *designed for* it (stable names, headless build, YAML selection), but the
  harness wiring is a follow-up spec.

## 3. Detailed design

### 3.1 Module layout

All new code lives inside the extra's subpackage; heavy imports
(`langgraph`, `langchain_openai`, `streamlit`) stay inside lazily-imported
modules, preserving the `__init__.py` PEP 562 contract
(`ask_your_docs/__init__.py:21-38`) and the mypy exclusion.

```
python/pydocs_mcp/ask_your_docs/
├── agent.py               # CHANGED: build path delegates to the registry;
│                          #   reformulate() gains the image-turn rule (§3.6)
├── app.py                 # CHANGED: image upload UI, capability badge,
│                          #   AskYourDocsConfig consumption (§3.5, §3.7)
├── cli.py                 # CHANGED: no new flags; PYDOCS_CONFIG now ALSO
│                          #   read app-side (§3.5)
├── attachments.py         # NEW: ImageAttachment value object + weaving
│                          #   helpers; agent.weave_attachments re-exported
│                          #   for back-compat (light module, no heavy imports)
├── multimodal.py          # NEW: capability detection ladder + cache
│                          #   (imports langchain_openai lazily, probe only)
└── architectures/
    ├── __init__.py        # NEW: agent_registry instance; side-effect imports
    │                      #   of the three entry modules (stage_registry
    │                      #   population pattern, extraction/serialization.py:27-33)
    ├── base.py            # NEW: AgentArchitecture ABC + AgentBuildContext
    ├── text_react.py      # NEW: @agent_registry.register("text_react") —
    │                      #   the CURRENT behavior, extracted verbatim
    ├── inline.py          # NEW: @agent_registry.register("inline")
    ├── vision_subagent.py # NEW: @agent_registry.register("vision_subagent")
    └── auto.py            # NEW: @agent_registry.register("auto") — hybrid router

python/pydocs_mcp/retrieval/config/
└── app_config.py          # CHANGED: AppConfig gains `ask_your_docs:
                           #   AskYourDocsConfig` sub-model (new module
                           #   ask_your_docs_models.py beside embedder_models.py)

python/pydocs_mcp/defaults/default_config.yaml
                           # CHANGED: new `ask_your_docs:` block (documented,
                           #   defaults match the pydantic Fields — the
                           #   sanctioned intentional YAML duplication,
                           #   CLAUDE.md §Default values)
```

### 3.2 Data models

All value objects are `@dataclass(frozen=True, slots=True)` per convention.

```python
# ask_your_docs/attachments.py  (no heavy imports)
from dataclasses import dataclass

_MAX_IMAGE_BYTES_DEFAULT = 5_000_000     # single source of truth
_MAX_IMAGES_PER_TURN_DEFAULT = 3
_ALLOWED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """One user-attached image for the CURRENT question (transient, like the
    scope pin — never persisted into conversation history)."""
    name: str               # original filename, for chips + placeholders
    media_type: str         # one of _ALLOWED_IMAGE_TYPES
    data_b64: str           # base64 payload (no data: prefix)

    def as_content_block(self) -> dict:
        """OpenAI-compatible image_url content block (data URI)."""
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{self.media_type};base64,{self.data_b64}"},
        }
```

```python
# ask_your_docs/multimodal.py
from dataclasses import dataclass
from typing import Literal

DetectionSource = Literal["override", "static", "endpoint", "probe", "default"]

@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    multimodal: bool
    source: DetectionSource     # which ladder rung decided — surfaced in the UI badge
```

```python
# ask_your_docs/architectures/base.py
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities

@dataclass(frozen=True, slots=True)
class AgentBuildContext:
    """Ambient dependencies for architecture builders — the agent-side mirror
    of retrieval's BuildContext (retrieval/serialization.py:119-182)."""
    llm: Any                        # ChatOpenAI (typed Any: extra is mypy-excluded)
    tools: Sequence[Any]            # MCP-adapter tools from MultiServerMCPClient
    prompt: str                     # SYSTEM_PROMPT + catalog listing
    capabilities: ModelCapabilities
    config: "AskYourDocsConfig"     # the AppConfig sub-model (§3.5)

class AgentArchitecture(ABC):
    """One registrable agent architecture. Entries are stateless frozen
    dataclasses; ``build`` returns a compiled LangGraph graph exposing
    ``ainvoke({"messages": [...]})`` and ``get_graph()`` (introspection
    contract — README's agent-graph.png regeneration must keep working)."""

    #: Build-time capability requirement, validated by build_agent BEFORE
    #: building (§3.4.4). ClassVar metadata is the minimal extension over the
    #: ComponentRegistry precedent (which carries only the class itself).
    requires_multimodal: ClassVar[bool] = False

    @abstractmethod
    def build(self, ctx: AgentBuildContext) -> Any: ...

    # from_dict/to_dict follow the ComponentRegistry contract so
    # agent_registry.build({"type": name, ...}, ctx) works if a future spec
    # wants data-driven construction; for now entries carry no parameters.
    @classmethod
    def from_dict(cls, data: dict, context: object) -> "AgentArchitecture":
        return cls()
```

### 3.3 The agent registry

**Reuse `ComponentRegistry` as another typed instance** — the fifth, after
`step_registry` / `formatter_registry`
(`retrieval/serialization.py:108-109`), `stage_registry`
(`extraction/serialization.py:27`), and `decision_source_registry`
(`extraction/decisions/_types.py:83`) — the sanctioned pattern proven by
`stage_registry` (`extraction/serialization.py:5-6`: *"a SEPARATE instance
from `retrieval.step_registry`"*), rather than writing a bespoke registry
class:

```python
# ask_your_docs/architectures/__init__.py
"""Agent-architecture registry for ask-your-docs.

Populated by side-effect import of the entry modules below — the same
import-time population pattern as extraction.pipeline.stages. Duplicate
registration raises ValueError at import time (wiring bugs surface at import
time, not first use — extraction/serialization.py:59-66 rationale).
"""
from pydocs_mcp.retrieval.serialization import ComponentRegistry
from pydocs_mcp.ask_your_docs.architectures.base import (
    AgentArchitecture,
    AgentBuildContext,
)

agent_registry: ComponentRegistry[AgentArchitecture] = ComponentRegistry()

# Side-effect imports populate the registry. Heavy langgraph imports live
# INSIDE these modules, which are only imported when the extra is installed
# and an agent is actually built.
from pydocs_mcp.ask_your_docs.architectures import (  # noqa: E402,F401
    auto,
    inline,
    text_react,
    vision_subagent,
)

__all__ = ["AgentArchitecture", "AgentBuildContext", "agent_registry"]
```

Notes:

- `pydocs_mcp.retrieval.serialization` is a light core module (no
  onnx/langgraph imports), so importing `ComponentRegistry` from the extra is
  safe and keeps one registry implementation repo-wide (DRY).
- `agent_registry.names()` → `("auto", "inline", "text_react",
  "vision_subagent")` is the enumeration surface future specs (architecture
  auto-optimization sweeps, the spec-driven harness) iterate over; unknown
  names fail with the standard KeyError listing sorted known names
  (`retrieval/serialization.py:33-80`).
- **Extension seam contract** (binding for future specs): an architecture is
  addable by (1) one new file under `architectures/`, (2) one
  `@agent_registry.register("name")` decorator, (3) one side-effect import in
  `architectures/__init__.py`, (4) selecting the name in YAML. No call-site
  edits in `app.py`/`agent.py`.

### 3.4 The three architectures

All three receive the same `AgentBuildContext` and return a compiled graph
with the `ainvoke({"messages": [...]})` shape `ask()` already uses
(`agent.py:209`), so `ask()` needs no per-architecture branching.

#### 3.4.0 `text_react` — the extracted status quo

```python
@agent_registry.register("text_react")
@dataclass(frozen=True, slots=True)
class TextReactArchitecture(AgentArchitecture):
    requires_multimodal: ClassVar[bool] = False

    def build(self, ctx: AgentBuildContext):
        from langgraph.prebuilt import create_react_agent
        return create_react_agent(ctx.llm, ctx.tools, prompt=ctx.prompt)
```

Exactly today's `agent.py:179` body, moved behind the registry. This is the
back-compat anchor: with no config and no images, behavior is byte-identical.

#### 3.4.1 `inline` — multimodal message to the main agent

The image content blocks are appended to the final `HumanMessage` of the
question turn, and an image-analysis section is appended to the system prompt:

```python
_IMAGE_ANALYSIS_PROMPT_SECTION = """
Image handling:
- The user may attach screenshots or diagrams. FIRST extract what is relevant
  to the question (error messages verbatim, symbol names, file paths, axis
  labels, box/arrow labels), THEN use your tools to ground every extracted
  name in the indexed corpus before citing it.
- Never answer from the image alone when a tool can verify; never invent
  symbols the image does not show.
"""

@agent_registry.register("inline")
@dataclass(frozen=True, slots=True)
class InlineMultimodalArchitecture(AgentArchitecture):
    requires_multimodal: ClassVar[bool] = True

    def build(self, ctx: AgentBuildContext):
        from langgraph.prebuilt import create_react_agent
        return create_react_agent(
            ctx.llm, ctx.tools,
            prompt=ctx.prompt + _IMAGE_ANALYSIS_PROMPT_SECTION,
        )
```

The graph is unchanged; what changes is message construction in `ask()`
(§3.6): when images are present, the `HumanMessage` content becomes
`[{"type": "text", "text": prefixed}, *(<image blocks>)]`.

**Pros:**
- Simplest possible change; zero new graph nodes; one LLM pass sees text +
  image + tools together (no lossy intermediate summary).
- The vision model can iterate: look at the image again after a tool result
  (e.g., re-read a stack frame once `get_symbol` shows the real signature).
- Lowest latency (no extra LLM round-trip).

**Cons:**
- Image tokens ride along on **every ReAct iteration** — a multi-tool turn
  re-pays the image cost per model call (vision tokens are expensive).
- Requires the *main* model to be multimodal; can't pair a strong text model
  with a separate vision model.
- Unstructured extraction — nothing forces the model to ground image content
  in tool results; the prompt section is advisory.
- Multimodal messages in flight complicate `reformulate` and history (§3.6).

#### 3.4.2 `vision_subagent` — extraction node feeding a text-only agent

The repo's first hand-built `StateGraph`: a two-node subgraph where a
**vision node** runs one focused multimodal call — guided by the user's
question — producing structured facts, and the existing text ReAct agent
(a compiled graph, added as a node: LangGraph's pipeline-IS-a-step analogue)
answers using those facts as woven text.

```python
_VISION_EXTRACTION_PROMPT = """\
You are a vision analyst for a code-documentation assistant. Look at the
attached image(s) and extract ONLY facts relevant to answering this question:

{question}

Return concise bullet lines, one fact per line, using these prefixes:
- ERROR: <verbatim error/exception text>
- SYMBOL: <function/class/module name visible in the image>
- PATH: <file path visible in the image>
- TEXT: <other verbatim text relevant to the question>
- VISUAL: <relevant non-text observation (arrows, highlights, chart shape)>
Do not answer the question. Do not speculate about code you cannot see.
"""

@agent_registry.register("vision_subagent")
@dataclass(frozen=True, slots=True)
class VisionSubagentArchitecture(AgentArchitecture):
    requires_multimodal: ClassVar[bool] = True

    def build(self, ctx: AgentBuildContext):
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import create_react_agent
        from langchain_core.messages import HumanMessage, RemoveMessage

        react = create_react_agent(ctx.llm, ctx.tools, prompt=ctx.prompt)

        async def vision_extract(state: MessagesState):
            last = state["messages"][-1]
            if isinstance(last.content, str):        # no image this turn
                return {}
            blocks = last.content
            question = next(b["text"] for b in blocks if b["type"] == "text")
            images = [b for b in blocks if b["type"] == "image_url"]
            reply = await ctx.llm.ainvoke([HumanMessage(content=[
                {"type": "text",
                 "text": _VISION_EXTRACTION_PROMPT.format(question=question)},
                *images,
            ])])
            facts = str(reply.content).strip()
            # Replace the multimodal message with a TEXT-ONLY message: facts
            # woven in the weave_attachments style ("Regarding ..."), so the
            # downstream ReAct agent never sees image blocks.
            woven = (f"[image analysis]\n{facts}\n[/image analysis]\n"
                     f"{question}") if facts else question
            # WHY RemoveMessage: MessagesState's ``add_messages`` reducer
            # merges by message id — a returned list APPENDS/updates, it
            # never deletes by omission. Without the explicit removal the
            # multimodal message would stay in state and the ReAct node
            # would still see (and re-pay for) the image blocks.
            return {"messages": [
                RemoveMessage(id=last.id), HumanMessage(woven)
            ]}

        graph = StateGraph(MessagesState)
        graph.add_node("vision_extract", vision_extract)
        graph.add_node("react_agent", react)
        graph.add_edge(START, "vision_extract")
        graph.add_edge("vision_extract", "react_agent")
        graph.add_edge("react_agent", END)
        return graph.compile()
```

Design choices, grounded in repo precedent:

- **Facts are woven as text**, mirroring `weave_attachments`' "Regarding …"
  prefix style (`agent.py:130-139`) rather than smuggled through a
  contextvar like `_active_scope` (`agent.py:34-36`). Rationale: the
  contextvar channel exists to *force* deterministic tool arguments the model
  must not override (`_intercept`, `agent.py:99-115`); image facts are the
  opposite — they are *evidence the model should reason over*, so they belong
  in the visible message stream where reformulation, history placeholders,
  and tests can see them. (Resolves the research open question; the
  contextvar alternative is recorded in §4.5.)
- The vision node **replaces** the multimodal message with the woven
  text-only message, so image tokens are paid exactly once, and every
  downstream ReAct iteration is text-only.
- One vision model call, `question`-guided — extraction is task-relevant, not
  a generic caption.

**Pros:**
- Image tokens paid **once per turn**, not per ReAct iteration — dominant
  cost win on multi-tool questions.
- Structured, greppable output contract (ERROR:/SYMBOL:/PATH:/…) that tests
  can assert against and the main agent can ground via tools.
- Decouples capabilities: the main agent stays text-only, so this
  architecture also unlocks a future "separate vision model" config without
  reshaping the graph.
- The main agent's prompt/tooling is untouched — no prompt-section drift.

**Cons:**
- Lossy: the ReAct agent can never "look again" after a tool result reveals
  the extraction missed something.
- One extra LLM round-trip of latency per image turn.
- First custom `StateGraph` in the repo — new surface to test (graph shape,
  no-image passthrough, `get_graph()` introspection).
- Two prompts to maintain (extraction + main).

#### 3.4.3 `auto` — conditional hybrid routed by detection

```python
@agent_registry.register("auto")
@dataclass(frozen=True, slots=True)
class AutoArchitecture(AgentArchitecture):
    # Validated at ROUTE time, not build time: auto itself builds on any model.
    requires_multimodal: ClassVar[bool] = False

    def build(self, ctx: AgentBuildContext):
        if not ctx.capabilities.multimodal:
            return agent_registry.get("text_react")().build(ctx)
        chosen = ctx.config.multimodal.preferred_architecture   # "vision_subagent" default
        return agent_registry.get(chosen)().build(ctx)
```

`auto` is **build-time routing** (per `(model, base_url, config)` agent-cache
entry, `app.py:49-55`), not per-message routing: the capability of a fixed
model does not change between questions, so routing once at build keeps the
compiled graph static and `get_graph()` rendering meaningful. Per-message
image-vs-no-image branching is already handled *inside* each architecture
(the vision node passes through when content is `str`; `inline` only attaches
blocks when images exist).

**Pros:**
- Zero-config best behavior: text-only models get today's agent, vision
  models get the preferred image architecture, and the text-only degradation
  policy (§3.8) applies uniformly.
- The default that makes `architecture: auto` safe to ship as the YAML
  default without breaking any existing deployment.

**Cons:**
- Indirection: "which graph am I actually running?" needs the UI badge
  (§3.7) and a log line to answer.
- Routing quality is only as good as detection (§3.9) — a false-negative
  detection silently downgrades a capable model to `text_react` (mitigated by
  the explicit override rung).

#### 3.4.4 Build-time capability validation

`build_agent` validates before building:

```python
arch_cls = agent_registry.get(name)
if arch_cls is None:
    raise ValueError(f"unknown architecture {name!r}; known: {agent_registry.names()}")
if arch_cls.requires_multimodal and not capabilities.multimodal:
    raise AgentArchitectureError(          # new exception, extra-local
        f"architecture {name!r} requires a multimodal model, but "
        f"{model!r} was detected text-only (source={capabilities.source}). "
        "Set ask_your_docs.multimodal.detection.override: true in your YAML "
        "if the detection is wrong, or select architecture: auto."
    )
```

The error message carries the offending value, the expected shape, and a
YAML-anchored actionable pointer — the `ServiceUnavailableError` message
style mandated by the Null Object section of CLAUDE.md. This answers the
research open question about per-entry capability metadata: a single
`ClassVar[bool]` is sufficient for the router and validator; richer metadata
(cost tiers, tool requirements) is deferred until a consumer exists (YAGNI).

### 3.5 YAML config surface

A new `AskYourDocsConfig` sub-model on `AppConfig`
(`retrieval/config/app_config.py:153-159` precedent: `llm: LlmConfig`),
defined in a new `retrieval/config/ask_your_docs_models.py` beside
`embedder_models.py:209-224`. This is the **first agent-side consumption of
AppConfig** — sanctioned because architecture choice and detection strategy
are squarely "A/B-testable against a benchmark" behaviors (CLAUDE.md litmus
test), and the alternative (a fifth env var) bypasses YAML layering (§4.4).

```yaml
# defaults/default_config.yaml — new documented block (defaults duplicate the
# pydantic Fields intentionally: YAML is the user-visible knob, CLAUDE.md
# §Default values)
ask_your_docs:
  architecture: auto              # one of agent_registry.names(); "text_react"
                                  # preserves pre-image behavior exactly
  multimodal:
    preferred_architecture: vision_subagent   # what "auto" picks on a vision model
    detection:
      override: null              # true | false | null(=run the ladder)
      static_table: true          # rung 2: prefix table (§3.9)
      endpoint_probe: false       # rung 3: GET /v1/models metadata (opt-in)
      image_probe: false          # rung 4: one-shot 1x1-px capability probe (opt-in)
    text_only_fallback: reject    # reject | describe   (§3.8)
  images:
    max_per_turn: 3               # _MAX_IMAGES_PER_TURN_DEFAULT
    max_bytes: 5000000            # _MAX_IMAGE_BYTES_DEFAULT (per image)
```

```python
# retrieval/config/ask_your_docs_models.py (sketch)
class MultimodalDetectionConfig(BaseModel):
    override: bool | None = Field(default=None)
    static_table: bool = Field(default=True)
    endpoint_probe: bool = Field(default=False)
    image_probe: bool = Field(default=False)

class MultimodalConfig(BaseModel):
    preferred_architecture: str = Field(default="vision_subagent")
    detection: MultimodalDetectionConfig = Field(default_factory=MultimodalDetectionConfig)
    text_only_fallback: Literal["reject", "describe"] = Field(default="reject")

class ImagesConfig(BaseModel):
    max_per_turn: int = Field(default=3, ge=1, le=10)
    max_bytes: int = Field(default=5_000_000, ge=1)

class AskYourDocsConfig(BaseModel):
    architecture: str = Field(default="auto")
    multimodal: MultimodalConfig = Field(default_factory=MultimodalConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
```

**Consumption path:** `app.py` (and `agent.build_agent` for headless callers)
call `AppConfig.load(...)` with the `PYDOCS_CONFIG` env value wrapped in
`Path` when set (`load` takes `explicit_path: Path | None` and calls
`.exists()` on it, `app_config.py:295` — passing the raw env string would
crash) and read `config.ask_your_docs`. The same `PYDOCS_CONFIG` value keeps being forwarded
verbatim to the pydocs-mcp subprocess (`agent.py:160-163`) — one YAML file
now configures both sides, which is exactly the per-deployment-tuning story
CLAUDE.md's YAML doctrine promises. Env overrides come free from AppConfig's
`env_prefix="PYDOCS_"` / `env_nested_delimiter="__"`
(`app_config.py:183-187`): `PYDOCS_ASK_YOUR_DOCS__ARCHITECTURE=inline` works
with zero new code, so the CLI `_ENV` map (`cli.py:18-23`) gains **no** fifth
entry. `AppConfig` and its sub-models are light pydantic — importing them
from the extra adds no heavy deps.

### 3.6 Control flow: message construction, reformulate, history

The per-turn path in `app.py:143-155` / `agent.py:191-215` changes as
follows (architecture-independent; the graphs consume what `ask` sends):

```
st.chat_input(accept_file="multiple", file_type=["png","jpg","jpeg","webp","gif"])
      │  → question: str, files → tuple[ImageAttachment, ...] (validated: count ≤
      │    images.max_per_turn, size ≤ images.max_bytes, type ∈ allowlist;
      │    violations render an inline error chip and drop the offending file)
      ▼
weave_attachments(attached_symbols, question)          # UNCHANGED contract
      ▼
reformulate(llm, history, woven)                       # TEXT-ONLY, always
      │    history serialization is hardened: content blocks (if any ever
      │    appear) are flattened to their text parts + "[image]" markers via a
      │    new _history_line(m) helper — REWRITE_PROMPT never sees a list repr
      ▼
ask(agent, history, standalone, scope, images=images)  # NEW keyword, default ()
      │    prefixed = scope_prefix(scope) + question
      │    content  = prefixed                          if not images
      │              [{"type":"text","text":prefixed}, *blocks]  otherwise
      │    result   = await agent.ainvoke({"messages": [*history, HumanMessage(content)]})
      ▼
history += [HumanMessage(question + placeholder), AIMessage(answer)]
      │    placeholder = f" [attached images: {', '.join(names)}]" if images
      │    — the image BYTES are transient, exactly like the scope-pin note
      │    (agent.py:200-204 precedent); only the placeholder persists, so
      │    later reformulations know an image existed without re-paying tokens
      ▼
del history[:-max_history]                              # UNCHANGED
```

Decisions locked in (resolving research open questions):

1. **`reformulate` never sees image content.** It runs on the woven text
   question before image blocks are attached; history is text +
   placeholders by construction. For the `vision_subagent` architecture the
   extraction runs *inside the graph, after* reformulation — the standalone
   question is what guides extraction, which is strictly better guidance
   than the raw follow-up ("what about that error?" → resolved first).
2. **Images do not persist in history.** Token cost on a vision model would
   otherwise grow linearly with `max_history`; the placeholder keeps the
   conversational record honest. A user who wants the image reconsidered
   re-attaches it.
3. **`ask()` gains one keyword-only parameter** `images:
   tuple[ImageAttachment, ...] = ()` — additive, default preserves the
   existing signature for all current callers (README examples, tests).

### 3.7 Streamlit UI changes (`app.py`)

- **Upload:** `st.chat_input(accept_file="multiple", file_type=[...])`.
  `accept_file` postdates the pinned `streamlit>=1.36`; the extra's floor is
  bumped to `streamlit>=1.43` (§4.7 for the alternative; §6 for rollout
  impact — the extra is opt-in, so the ~90MB default-install policy is
  untouched, `pyproject.toml:90-96`).
- **Capability badge:** the sidebar shows the detection result next to the
  model field, e.g. `vision: yes (static)` / `vision: no (default)` /
  `vision: yes (override)`, sourced from `ModelCapabilities.source`, so
  `auto`'s routing is never a mystery (mitigates the `auto` con in §3.4.3).
- **Caching:** capability detection is cached alongside the agent in
  `get_agent` (`@st.cache_resource` keyed on
  `(workspace, model, base_url, config)`, `app.py:49-55`) — detection runs
  once per key on the cached background event loop (`app.py:29-38`).
  Process-level caching is sufficient for v1; a persisted cache file is
  deliberately deferred (§7 Q2).
- **Image chips:** attached images render as removable chips above the input,
  visually distinct from the existing symbol-name chips
  (`test_app_attachment.py:11-21` pattern extends to image chips).

### 3.8 Graceful degradation on text-only models

Images are **user-requested content**, so the Null Object asymmetry
(CLAUDE.md: `NullTreeService`/`NullReferenceService` raise with a
YAML-anchored pointer because silence would mislead; `NullVectorStore` is
silent because vectors are advisory) puts us firmly on the *raise/visible*
side — a text-only deployment must never silently ignore an attached image.

Two policies, selected by `multimodal.text_only_fallback`:

- **`reject` (default):** the app refuses the turn before any LLM call, with
  an actionable inline message: *"The model `<name>` was detected as
  text-only (source=<source>), so the attached image cannot be read. Remove
  the image, switch to a vision-capable model, or — if the detection is
  wrong — set `ask_your_docs.multimodal.detection.override: true` in your
  config."* The question is preserved in the input box; nothing is sent.
  Rationale: an unread image is unmet user intent; failing loudly with the
  fix in hand is the `ServiceUnavailableError` posture.
- **`describe` (opt-in):** the turn proceeds text-only; the question is
  prefixed with `"[note: the user attached image(s) ({names}) that this
  model cannot see — say so explicitly in your answer and answer from text
  only]"`, and the UI shows a warning chip. The model apologizes and answers
  what it can. Rationale: some deployments (demos, air-gapped local models)
  prefer a degraded answer over a hard stop.

Pros/cons are argued in §4.6; the default is `reject`.

### 3.9 Multimodal capability detection — the ladder

Implemented in `ask_your_docs/multimodal.py` as
`async def detect_capabilities(model: str, base_url: str | None, cfg:
MultimodalDetectionConfig) -> ModelCapabilities`, evaluating rungs in order
and stopping at the first decisive one:

1. **Explicit override** (`detection.override: true|false`) — always wins.
2. **Static prefix table** (`detection.static_table`, default on) —
   `_MULTIMODAL_MODEL_PREFIXES: tuple[str, ...]` with **longest-prefix
   matching**, structurally identical to `_MODEL_CONTEXT_TOKENS` +
   `context_window_tokens` (`retrieval/llm_clients/model_budget.py:29-47,
   56-67`) and to the `_REASONING_MODEL_PREFIXES` capability branch
   (`llm_clients/openai.py:39-54`) — the repo already accepts name-based
   capability inference with documented failure modes (dated suffixes →
   prefix matching). The table covers well-known vision families
   (`gpt-4o`, `gpt-4.1`, `gpt-5`, `o3`, `o4`, `gemma-3`, `llava`, `qwen2-vl`,
   `qwen2.5-vl`, `pixtral`, `llama-3.2-.*vision` via prefix normalization,
   `internvl`, `minicpm-v`, …; exact list finalized at implementation with a
   dated `# WHY` comment) and a **negative** table for known text-only
   families so the ladder can stop early either way. Unknown → fall through.
3. **Endpoint metadata probe** (`detection.endpoint_probe`, default off) —
   `GET {base_url}/v1/models` and inspect the entry for `model` for modality
   hints (vLLM and some gateways expose them; many local servers do not —
   the field names vary, so this rung treats *absence* of a hint as
   fall-through, never as "text-only"). Network call ⇒ bounded retry +
   timeout per the repo's explicit-robustness rule, mirroring
   `_with_retry_async` (3 attempts, 2s/4s backoff,
   `llm_clients/openai.py:31-84`) with a 5s request timeout. Off by default
   because reliability across local OpenAI-compatible servers is unverified
   (§7 Q1).
4. **One-shot tiny-image probe** (`detection.image_probe`, default off) —
   send a single chat completion with a hardcoded base64 1×1-pixel PNG
   (~100 bytes, a module constant) and the text "Reply with the single word
   OK." A non-error response ⇒ multimodal; a 4xx/validation error mentioning
   image/content-type ⇒ text-only; timeout/5xx ⇒ fall through. Same
   timeout/bounded-retry envelope as rung 3. Off by default because it costs
   a real (tiny) LLM call and can surprise metered deployments.
5. **Conservative default:** `ModelCapabilities(multimodal=False,
   source="default")` — the exact posture of `_DEFAULT_CONTEXT_TOKENS =
   16_000` for unknown models (`model_budget.py:29-47`): when unsure, assume
   the lesser capability; the override rung is the escape hatch.

The result is cached per `(model, base_url)` inside the `get_agent` cache
entry (§3.7). The ladder is pure-async, Streamlit-free, and independently
unit-testable with a fake HTTP layer (named-fake convention).

## 4. Alternatives considered

### 4.1 Detection strategies (the four rungs, individually)

**A. Static model-name allowlist only** (settings-shipped prefix table)
- Pros: zero network, zero cost, deterministic, instant; strong repo
  precedent (`model_budget.py`, `_REASONING_MODEL_PREFIXES`); trivially
  testable; works air-gapped.
- Cons: rots as models ship; useless for local fine-tunes with custom names
  (`my-vlm-v2`); false negatives silently downgrade capable models; the
  table is a maintenance treadmill.

**B. Endpoint metadata probe only** (`/v1/models`)
- Pros: authoritative *when the server reports modalities*; no LLM spend;
  one small HTTP call; catches custom-named models the table can't.
- Cons: no metadata standard across OpenAI-compatible servers — vLLM,
  gateway, and single-model servers disagree or omit the field entirely
  (unverifiable from the repo, §7 Q1); absence proves nothing, so it can
  only ever be a *positive* signal; adds a network dependency + failure
  modes to app startup; no repo precedent (nothing calls `/v1/models`
  today).
- Cons: some servers require auth for `/v1/models` or disable it.

**C. One-shot tiny-image capability probe with caching**
- Pros: ground truth — tests the actual behavior we need, not metadata about
  it; works on any server; a 1×1 PNG + "reply OK" costs a few dozen tokens
  once per `(model, base_url)`.
- Cons: costs a real API call (metered deployments notice); error taxonomy is
  provider-specific (distinguishing "can't do images" 400s from unrelated
  400s needs careful, fuzzy matching); adds startup latency; a flaky server
  can mis-classify (mitigated: 5xx/timeout falls through rather than
  deciding); surprising side traffic if on by default.

**D. Explicit settings override only**
- Pros: always correct when the operator knows their model; zero code risk.
- Cons: "automatic" was the whole ask; every user must know and set it;
  defaults would have to be pessimistic for everyone.

**Recommendation: the layered combination (§3.9)** — override ⊳ static ⊳
endpoint (opt-in) ⊳ image probe (opt-in) ⊳ conservative default. Each rung
covers the previous rung's blind spot; each is independently toggleable in
YAML (A/B-testable per the litmus test); the shipped default (override
unset, static on, probes off) costs zero network and matches the repo's
"conservative fallback + escape hatch" posture. The probes exist for exactly
the deployments the static table fails: local servers with custom model
names.

### 4.2 Architecture (a): inline multimodal — see §3.4.1 pros/cons

### 4.3 Architectures (b) vision subagent and (c) conditional hybrid — see §3.4.2 / §3.4.3 pros/cons

**Recommendation across (a)/(b)/(c): ship all three behind the registry,
default `architecture: auto` with `preferred_architecture: vision_subagent`.**
The registry makes "which is best" an empirical question the benchmark
harness can answer later per-deployment (CLAUDE.md litmus test: A/B-testable
⇒ YAML) instead of a design-time bet. `vision_subagent` is the preferred
default on vision models because the once-per-turn image-token cost profile
dominates for the ReAct workload (multi-tool turns are the norm for
code-grounded questions), and its structured-facts contract is the more
testable behavior. `inline` remains selectable for latency-sensitive or
look-again-heavy use.

### 4.4 Where the settings live: AppConfig sub-model vs a fifth env var

**A. `AskYourDocsConfig` on AppConfig (chosen, §3.5)**
- Pros: full YAML layering (defaults → overlay → env) for free, including
  `PYDOCS_ASK_YOUR_DOCS__*` env overrides with zero new plumbing
  (`app_config.py:183-187`); one config file drives both the subprocess and
  the agent; the benchmark harness can sweep `configs/*.yaml` over
  architectures — the doctrinal requirement; typed + validated.
- Cons: first agent-side AppConfig consumption (new import edge from the
  extra into `retrieval/config`; light, but a precedent); slightly more code
  than an env var.

**B. Fifth env var (e.g. `AGENT_ARCHITECTURE`) in `cli.py`'s `_ENV` map**
- Pros: matches the existing four-var pattern exactly (`cli.py:18-23`);
  minimal diff.
- Cons: bypasses YAML layering — no defaults file, no overlay, no benchmark
  sweep without bespoke env plumbing; every future knob (detection rungs,
  fallback policy, image limits) becomes another env var; violates the
  spirit of "behavior that could be A/B tested belongs in YAML".

**Recommendation: A.** The env-var escape hatch still exists *through* A via
pydantic-settings, so B's only real advantage (quick shell override)
survives.

### 4.5 Vision-facts channel: woven text vs contextvar scratch

- **Woven text (chosen):** visible to the model, to `reformulate`-adjacent
  history placeholders, to logs, and to assertions in tests; mirrors the
  established `weave_attachments` prefix idiom. Con: consumes prompt tokens;
  the model could ignore it (acceptable — it's evidence, not a constraint).
- **Contextvar channel (like `_active_scope`):** pros — survives outside the
  message stream, can't be truncated by history trimming. Cons: the
  `_active_scope`/`_intercept` machinery exists to *force tool arguments*
  deterministically (`agent.py:34-36, 99-115`); image facts are model-facing
  evidence with no interceptor consumer, so a contextvar would be a hidden
  side-channel with no reader — no repo precedent for structured subagent
  output supports it, and it would be untestable through the public message
  API. Rejected.

### 4.6 Text-only degradation: reject vs describe

- **Reject (chosen default):** pros — honest, actionable, zero wasted LLM
  spend, matches the raising side of the Null Object asymmetry (user-requested
  content must not silently degrade); cons — hard stop can frustrate demo
  flows on text-only local models.
- **Describe-and-apologize (opt-in):** pros — the conversation keeps moving;
  the model's explicit "I cannot see the image you attached" keeps the user
  informed; cons — burns a turn on a knowingly incomplete answer; users skim
  past apologies and may trust a text-only answer to an image question.

Both ship; YAML selects (`text_only_fallback`), because which policy is
"right" is deployment-dependent — the definition of a YAML knob.

### 4.7 Image upload UI: Streamlit floor bump vs `st.file_uploader`

- **Bump extra floor to `streamlit>=1.43` and use
  `st.chat_input(accept_file=...)` (chosen):** pros — native attach-in-input
  UX, files arrive atomically with the question (no stale-uploader state),
  less session-state bookkeeping; cons — floor bump within the extra (the
  extra is opt-in, so the default-install size/policy is untouched; existing
  extra users pick up a newer Streamlit on next install).
- **Keep `>=1.36`, add a sidebar `st.file_uploader`:** pros — no floor
  change; cons — detached from the input (users attach then forget, or the
  uploader retains files across turns → accidental re-sends), a second
  clear-after-send state machine, and a worse UX permanently to avoid a
  version bump in an opt-in extra released well over a year ago.

**Recommendation: bump the floor.** Record the bump in `pyproject.toml` with
a `# WHY` comment citing `accept_file`.

### 4.8 Registry mechanics: shared `ComponentRegistry` vs bespoke class

- **Fifth `ComponentRegistry` instance (chosen):** pros — DRY (one registry
  implementation), inherits duplicate-`ValueError`-at-import, KeyError with
  sorted known names, `names()`/`get()`, and the typed-instance precedent set
  by `stage_registry` (`extraction/serialization.py:27-33`); the
  `from_dict(data, context)` contract keeps data-driven construction open
  for the future harness. Cons — imports `retrieval.serialization` from the
  extra (light, acceptable); `build()`'s `data["type"]` dispatch is more
  machinery than name-selection strictly needs today.
- **PredicateRegistry-style bespoke class:** pros — registers plain
  callables, has `copy()` for test isolation; cons — architectures are
  stateful-by-config classes, not bare callables, and a second registry
  implementation for classes duplicates `ComponentRegistry`.

### 4.9 Image persistence in history: full blocks vs placeholder

- **Persist full image blocks:** pros — later turns can re-inspect; cons —
  vision-token cost × every subsequent turn × `max_history`, and
  `reformulate`'s serializer must handle blocks forever.
- **Textual placeholder only (chosen, §3.6):** pros — flat token cost,
  history stays `str`-typed, mirrors the transient scope-pin precedent
  (`agent.py:200-204`); cons — "look at the image again" requires
  re-attaching. Acceptable: re-attachment is one click.

## 5. Testing & acceptance criteria

All new test modules live under `tests/ask_your_docs/` and
`importorskip` the extra, matching `test_attachment.py:8-18` /
`test_app_attachment.py:11-21`, so core CI skips them. LLM and HTTP calls are
mocked behind named fakes (`FakeVisionLlm`, `FakeModelsEndpoint`) per the
global clean-code rules. TDD: each AC's test is written first.

**Registry & architectures — `tests/ask_your_docs/test_agent_registry.py`, `test_architectures.py`**

- **AC1.** `agent_registry.names()` returns exactly
  `("auto", "inline", "text_react", "vision_subagent")`; `get()` of an
  unknown name returns `None` and `build_agent` with an unknown name raises
  `ValueError` listing the known names.
- **AC2.** Registering a second architecture under an existing name raises
  `ValueError` at decoration time (import-time wiring-bug contract).
- **AC3.** `text_react.build(ctx)` returns a graph whose behavior on a
  text-only question is identical to today's `create_react_agent` path
  (assert the same `ainvoke` message shape reaches a `FakeLlm`; regression
  anchor).
- **AC4.** Every registered architecture's built graph exposes
  `get_graph()` and renders mermaid without error (introspection contract,
  README `agent-graph.png` workflow).
- **AC5.** `inline.build(ctx)` produces a prompt ending with the
  image-analysis section; `text_react`'s prompt does not contain it.
- **AC6.** `vision_subagent` graph, given a multimodal `HumanMessage`, makes
  exactly ONE vision call on `FakeVisionLlm`, and the message reaching the
  ReAct node is text-only and contains the fake's `ERROR:`/`SYMBOL:` fact
  lines inside `[image analysis]...[/image analysis]`.
- **AC7.** `vision_subagent` graph, given a plain-`str` `HumanMessage`,
  passes it through unchanged (no vision call recorded on the fake).
- **AC8.** `auto.build(ctx)` with `capabilities.multimodal=False` builds the
  `text_react` graph; with `True` it builds
  `config.multimodal.preferred_architecture`'s graph (assert via a
  registry-spy or graph-node names).
- **AC9.** `build_agent(architecture="inline")` against
  `capabilities.multimodal=False` raises `AgentArchitectureError` whose
  message contains the model name, the detection source, and the literal
  YAML path `ask_your_docs.multimodal.detection.override`.

**Detection — `tests/ask_your_docs/test_multimodal_detection.py`**

- **AC10.** `override: true`/`false` short-circuits the ladder (no table
  lookup, no HTTP on the fakes) and yields `source="override"`.
- **AC11.** Longest-prefix semantics: a name matching both a shorter negative
  prefix and a longer positive prefix resolves to the longer match
  (mirrors `model_budget.py:56-67`); a known-vision prefix yields
  `(True, "static")`, a known-text prefix `(False, "static")`.
- **AC12.** With probes disabled and an unknown model name, the result is
  `(False, "default")` — the conservative fallback.
- **AC13.** Endpoint probe: `FakeModelsEndpoint` advertising a vision hint
  yields `(True, "endpoint")`; an entry with NO modality field falls through
  (does not decide text-only); a connection error / timeout falls through
  after ≤3 attempts (bounded-retry envelope asserted via the fake's call
  count).
- **AC14.** Image probe: fake 200 ⇒ `(True, "probe")`; fake 400 mentioning
  image content ⇒ `(False, "probe")`; fake 500/timeout ⇒ falls through to
  `(False, "default")`.
- **AC15.** Detection runs once per `(model, base_url)` across repeated
  `detect_capabilities`-via-cache calls (fake call-count == 1).

**Attachments & message flow — `tests/ask_your_docs/test_image_attachment.py`**

- **AC16.** Existing `weave_attachments` tests pass unchanged (dedup +
  prepend, empty-identity — `test_attachment.py:8-18` is not modified).
- **AC17.** `ImageAttachment.as_content_block()` yields a well-formed
  `image_url` data-URI block; validation rejects an over-`max_bytes` payload
  and a disallowed media type with errors naming the offending value and
  limit.
- **AC18.** `ask(..., images=())` (default) sends a plain-`str`
  `HumanMessage` — byte-for-byte today's `scope_prefix + question` shape;
  with one image it sends `[text-block, image-block]` in that order.
- **AC19.** After an image turn, `history` contains only `str` contents; the
  user entry ends with the `[attached images: ...]` placeholder; history
  still trims to `max_history`.
- **AC20.** `reformulate` on a history containing a placeholder-bearing turn
  produces a prompt with no Python-list reprs (the `_history_line` hardening);
  `reformulate` is never called with image blocks (asserted at the call
  site).

**Degradation — `tests/ask_your_docs/test_text_only_fallback.py`**

- **AC21.** `text_only_fallback: reject` + text-only capabilities + an
  attached image: no LLM call occurs and the surfaced error names the model,
  the source, and the override YAML path.
- **AC22.** `text_only_fallback: describe`: the turn proceeds; the
  `HumanMessage` text contains the cannot-see note with the image names; the
  image block is NOT attached.

**Config — `tests/retrieval/test_ask_your_docs_config.py` (core suite — pydantic only, no extra needed)**

- **AC23.** `AppConfig.load()` with no overlay yields the documented defaults
  (`architecture="auto"`, `preferred_architecture="vision_subagent"`,
  detection `static_table=True`/probes `False`/`override=None`,
  `text_only_fallback="reject"`, `max_per_turn=3`,
  `max_bytes=5_000_000`); a YAML overlay and a
  `PYDOCS_ASK_YOUR_DOCS__ARCHITECTURE` env var each override in the
  documented precedence order.
- **AC24.** `defaults/default_config.yaml`'s `ask_your_docs:` block
  round-trips through `AppConfig.load` equal to the pydantic defaults (no
  YAML↔Field drift).

**UI — `tests/ask_your_docs/test_app_image_attachment.py` (AppTest, extra-skipped)**

- **AC25.** The sidebar renders the capability badge with the detection
  source; injected image attachments render removable chips distinct from
  symbol chips (extends the `test_app_attachment.py` AppTest pattern).

**Gates.** The full CI set applies: `ruff check`/`format --check`, `mypy
python/pydocs_mcp` (the extra stays mypy-excluded; the new
`retrieval/config/ask_your_docs_models.py` is NOT excluded and must
type-check), `complexipy ≤15`, `vulture`, coverage ≥90% on the core suite
(AC23-24 run there), `uv lock --check` after the Streamlit floor bump.

## 6. Rollout / migration / back-compat

- **Default behavior is preserved.** `architecture: auto` + text-only
  detection ⇒ the `text_react` graph, which is the extracted status quo
  (AC3); `ask()`'s new `images` keyword defaults to `()` (AC18). A
  deployment that never attaches images and runs a text-only model observes
  no change. Deployments that want a hard guarantee pin
  `architecture: text_react`.
- **`weave_attachments` keeps its import path** (`agent.py` re-exports from
  `attachments.py`); the graph-explorer symbol-attachment flow
  (`pages/2_Graph.py:254-258`) is untouched.
- **Extra floor bump:** `streamlit>=1.36` → `>=1.43` inside
  `[ask-your-docs]` only; `uv lock` regenerated in the same PR (lockfile
  gate). No new packages; the default install and its ~90MB budget are
  untouched.
- **Demo configs:** `examples/ask_your_docs_agent/configs/*.yaml` gain a
  commented `ask_your_docs:` block; the README's flag/env paragraph
  (`README.md:98-103`) documents the new YAML keys and the
  `PYDOCS_ASK_YOUR_DOCS__*` env form, and the agent-graph section notes that
  `agent-graph.png` can now be regenerated per architecture. No internal
  PR/task jargon in README text (CLAUDE.md §README rules); the spec/plan
  files carry the history.
- **Suggested landing order** (each independently green): (1) config
  sub-model + defaults YAML (core-tested), (2) `attachments.py` +
  `multimodal.py` ladder, (3) registry + `text_react` extraction (pure
  refactor, AC3 anchor), (4) `inline` + `vision_subagent` + `auto`,
  (5) app UI + degradation + floor bump + docs.
- **Rollback:** each stage is additive behind the extra; reverting any stage
  restores the previous green state without data migration (no schema, no
  index changes anywhere in this spec).

## 7. Open questions

1. **Endpoint-probe field taxonomy.** Which modality-hint fields do vLLM /
   LiteLLM-style gateways / single-model servers actually expose on
   `/v1/models` today? Unverifiable from the repo; the rung ships off by
   default and treats absence as fall-through, but the positive-match field
   list needs a small empirical survey during implementation (documented as
   `# WHY` comments with dates).
2. **Persisted probe cache.** Is process-level caching (per `get_agent`
   entry) enough, or should probe results persist under `~/.pydocs-mcp/`
   (e.g. `multimodal_probe_cache.json`) so metered deployments never re-pay
   the image probe across app restarts? Deferred: ship process-level;
   revisit if probe adoption shows repeat-cost pain.
3. **Separate vision model.** `vision_subagent` structurally supports a
   *different* (smaller/cheaper) vision model than the main agent — should a
   future `ask_your_docs.multimodal.vision_model`/`vision_base_url` pair be
   added, and does that interact with detection (two ladders)? Out of scope
   here; the `AgentBuildContext` would grow one field.
4. **Benchmark harness integration.** The registry is designed for sweeps
   (stable names, headless `build`, YAML selection), but the eval-suite has
   no multimodal question set. What does an image-question benchmark look
   like (screenshot corpus? synthetic diagrams?), and which metric scores
   extraction fidelity? Follow-up spec.
5. **Static table maintenance.** Should `_MULTIMODAL_MODEL_PREFIXES` be
   overridable/extendable from YAML (e.g.
   `detection.extra_multimodal_prefixes: [...]`) so operators can teach the
   table their local model names without code changes? Leaning yes, but it
   overlaps with `override:` for the single-model case — decide at
   implementation review.
6. **`IngestionStage` symmetry.** Unrelated cleanup noted while designing:
   if the future `IngestionStage → IngestionStep` rename (CLAUDE.md
   §Naming) happens, `AgentArchitecture` should be revisited for naming
   consistency (`AgentBlueprint`?). No action now.

---

## Amendment A1 (2026-07-12, implemented with the initial PR — user-directed scope widening)

**§3.10 `reinspect_images` — re-contextualizing earlier images to a new
question.** The shipped design deliberately dropped image bytes after each
turn (§3.6 decision 2), leaving "look at the image again" to manual
re-attachment — the recorded con of §3.4.2 and §4.9. This amendment closes
that gap with an **agent-local LangChain tool** (NOT an MCP tool — the
six-tool surface stays fixed; the tool lives beside the MCP-adapter tools in
the agent's tool list):

- **Session image store.** `attachments.update_image_store` keeps the last
  `images.session_retention` (default 12, `0` disables, new `ImagesConfig`
  field) attached images per session, newest-last with re-attach refresh.
  Bytes live OUTSIDE conversation history — the history non-goal ("no image
  persistence in history beyond a textual placeholder") is untouched; the
  store is UI session state, not message state.
- **The tool.** `reinspect.build_reinspect_tool(llm)` →
  `reinspect_images(names, question)`: the ReAct agent picks the relevant
  names (history placeholders show them) and passes the CURRENT question;
  one vision call over ONLY the selected images reuses
  `_VISION_EXTRACTION_PROMPT`, returning the same ERROR:/SYMBOL:/… fact
  contract. Unknown names / empty store return model-facing guidance text
  (tools must not raise at the model).
- **Per-session isolation.** The compiled graph is cached across sessions,
  so the store rides a new `agent._active_image_store` contextvar set inside
  `ask(..., image_store=...)` — exactly the `_active_scope` pattern.
- **Capability gating.** `architectures/base.effective_tools(ctx)` appends
  the tool only when `capabilities.multimodal` — a text-only build carries no
  tool it can never satisfy. Consequence for AC3's "byte-identical" anchor:
  `text_react` remains byte-identical on text-only capabilities (the
  pre-image deployment case); on vision models its graph gains the tools
  node — a deliberate extension.

**Amended ACs:**
- **AC26.** The tool re-inspects ONLY the selected names in ONE vision call;
  unknown names return guidance listing the stored names; an empty store
  returns a re-attach hint — no vision call in either failure case.
- **AC27.** `ask()` pins the per-question store snapshot to the contextvar
  and resets it after the turn; the store helper evicts oldest beyond
  `session_retention`, refreshes position on re-attach, and `0` disables.
- **AC28.** Vision-capable builds of every architecture expose the tool
  (graph has a tools node even with zero MCP tools); text-only builds don't.

**A1 necessity gating (same-day follow-up):** every reinspect call is a full
vision-model call, so three deterministic guards keep it to when-necessary:
(1) the store snapshot a turn receives holds only PRIOR turns' images (the
current attachment was just seen/extracted — same-turn re-reads are waste);
(2) repeated same-args calls within a turn return memoized facts (free);
(3) a per-turn budget — `images.max_reinspect_per_turn` (default 2, 0
disables) — beyond which the tool refuses and directs the model to the facts
it already has. Budget/memo state rides a per-turn contextvar set in
`ask()`; the tool description states the cost explicitly. AC26 extends:
memoized repeats and over-budget calls make no vision call.
