# Ask-your-docs agent

A minimal **LangGraph ReAct** conversational agent that uses **pydocs-mcp** as
its tool server to answer questions about the documentation and code of your
indexed projects — as a Streamlit webapp or in a notebook.

What it demonstrates:

- **Multi-repo**: one agent over a directory of pre-built pydocs-mcp indexes
  (`pydocs-mcp serve --workspace ...`, read-only).
- **Grounded answers**: a system prompt that forces every answer through the
  `search` / `lookup` tools, cites `project` + `package.module`, infers the
  right project when the user doesn't name one, and asks a clarifying
  question when things stay ambiguous.
- **Conversation memory**: the last N messages are kept, and follow-up
  questions are **reformulated** into standalone queries before hitting the
  tools ("what does *it* return?" → "what does `backend.db.Pool.acquire`
  return?").
- **Your LLM**: any model served over the OpenAI API protocol — OpenAI itself
  or a local vLLM / Ollama / LiteLLM endpoint via the base URL.
- **GPU indexing, CPU serving**: embed the corpus once on GPU
  (Qwen3-Embedding-4B, torch), then serve queries on CPU via OpenVINO.

## Setup

```bash
cd examples/ask_your_docs_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # pydocs-mcp from this repo + agent + streamlit
export OPENAI_API_KEY=sk-...             # any placeholder works for local servers
```

## 1. Index your repos (GPU, once per repo)

```bash
pydocs-mcp --config configs/index_gpu.yaml index ~/code/frontend --cache-dir ~/pydocs-index --gpu
pydocs-mcp --config configs/index_gpu.yaml index ~/code/backend  --cache-dir ~/pydocs-index --gpu
```

Each run writes a portable `{name}_{hash}.db` + `.tq` bundle into the
workspace directory.

## 2. Chat (Streamlit)

```bash
streamlit run streamlit_app.py
```

Set the workspace, model, base URL (for a local vLLM / Ollama endpoint) and
optional pydocs config in the sidebar — or pre-fill them via
`PYDOCS_WORKSPACE`, `PYDOCS_MODEL`, `OPENAI_BASE_URL`, `PYDOCS_CONFIG`.
Answers cite `project` + `package.module` and render code in fenced blocks.

## 3. Or in a notebook

See [`notebook.ipynb`](notebook.ipynb) — the same two calls:

```python
from agent import build_agent, ask

agent, llm = await build_agent("~/pydocs-index", model="gpt-4o-mini",
                               base_url=None,  # e.g. "http://localhost:8000/v1" for vLLM / Ollama / LiteLLM
                               pydocs_config="configs/serve_cpu_openvino.yaml")
history = []
print(await ask(agent, history, "how do I open a database pool?"))
print(await ask(agent, history, "and what does it return?"))
```

## Why two embedding configs?

Embedding the *corpus* is the expensive part, so it runs once on GPU
(`index_gpu.yaml`). At serve time only the short *query* text is embedded, so
CPU via OpenVINO is plenty (`serve_cpu_openvino.yaml` — sentence-transformers
auto-exports the model on first load). The serve step is read-only and
validates only that model + dim match the bundles, so the two files
interoperate.

**The one rule: index with `index_gpu.yaml`, serve with
`serve_cpu_openvino.yaml` — never re-index with the serve file.** The
`backend` key is part of the chunk-cache identity, so indexing under it would
re-embed the whole corpus on CPU.

| Free to differ between the two files | Must stay identical |
|---|---|
| `batch_size`, `device` (`--gpu`), `query_prompt_name` | `provider`, `model_name`, `dim`, `max_seq_length`, `normalize`, `bit_width` |

(`backend` / `model_file_name` are the deliberate exception: identical vector
space, different runtime — which is exactly why the serve file is serve-only.)

Prefer a lighter setup? Swap both configs to
`Qwen/Qwen3-Embedding-0.6B` + `dim: 1024` (or drop `--pydocs-config` entirely
to use the built-in `BAAI/bge-small-en-v1.5` default — then index without a
config too, so the embedders match).
