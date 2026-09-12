# Ask-your-docs agent

A **LangGraph ReAct** conversational agent that uses **pydocs-mcp** as its tool
server to answer questions about the documentation and code of your indexed
projects, with a **Streamlit** chat UI. It ships inside pydocs-mcp behind the
`harness-ask-your-docs` extra, so installing it adds the `harness-ask-your-docs` command.

<p align="center">
  <img src="screenshot-chat.png" width="860"
       alt="Ask-your-docs chat UI — sidebar scope pickers (project / package / own-vs-dependency) beside the grounded-answer chat">
</p>

What it demonstrates:

- **Multi-repo**: one agent over a directory of pre-built pydocs-mcp indexes
  (`pydocs-mcp serve --workspace ...`, read-only).
- **Grounded answers**: a system prompt that forces every answer through the
  pydocs-mcp tools (`search_codebase`, `get_symbol`, `get_references`, …),
  cites `project` + `package.module`, infers the right project when the user
  doesn't name one, asks a clarifying question when things stay ambiguous, and
  ends with a runnable usage-example snippet built from the retrieved
  signatures.
- **Scoped retrieval**: sidebar pickers pin a project / package / own-code-vs-
  dependencies slice, enforced deterministically on the tool calls (a
  `langchain-mcp-adapters` interceptor rewrites the arguments) rather than left
  to the model.
- **Conversation memory**: the last N messages are kept, and follow-up
  questions are **reformulated** into standalone queries before hitting the
  tools ("what does *it* return?" → "what does `backend.db.Pool.acquire`
  return?").
- **Your LLM**: any model served over the OpenAI API protocol, hosted or local,
  via the base URL — with an internal token service or an environment-variable
  key as the bearer, and the endpoint's models listed in the UI.
- **GPU indexing, CPU serving**: embed the corpus once on GPU
  (Qwen3-Embedding-4B, torch), then serve queries on CPU via OpenVINO.

The code lives in `pydocs_mcp/harness/ask_your_docs/`; this directory holds the two
embedding configs and this guide.

## Architecture

The chat agent is a **LangGraph ReAct** loop, shown here exactly as LangGraph
draws its own graph (`agent.get_graph().draw_mermaid_png()`):

<p align="center">
  <img src="agent-graph.png" width="210"
       alt="LangGraph agent graph: __start__ to agent; agent loops to tools and back; agent to __end__">
</p>

- **`agent`** — the LLM (your OpenAI-protocol model). Each turn it either calls a
  pydocs-mcp tool or, once it has enough grounding, returns the final answer
  (`agent ⇢ __end__`).
- **`tools`** — the nine pydocs-mcp tools (`search_codebase`, `get_symbol`,
  `get_references`, `get_context`, `get_overview`, `get_why`, `grep`, `glob`,
  `read_file`). Results flow back into `agent`, which loops until it can answer.

End to end: **Streamlit UI → LangGraph agent → pydocs-mcp (stdio subprocess) →
read-only `.db` + `.tq` index bundles.** Before any tool runs, a
`langchain-mcp-adapters` interceptor rewrites its arguments to the sidebar's
pinned project / package / code scope, so retrieval stays inside the chosen
slice no matter what the model asks for. Each browser session holds ONE pydocs-mcp
subprocess for all of its questions: it starts with the first question, is closed when
the tab disconnects or the model changes, and is restarted (with a notice) if it stops.

<!-- agent-graph.png is the compiled agent's own graph, regenerated with
     agent.get_graph().draw_mermaid_png() (see pydocs_mcp.harness.ask_your_docs.agent).
     Every registered architecture (text_react / inline / vision_subagent /
     auto) builds a graph exposing get_graph(), so the picture can be
     regenerated per architecture by selecting it in YAML first. -->

## Setup

```bash
pip install "pydocs-mcp[harness-ask-your-docs]"          # from PyPI
# ...or from a checkout of this repo:
#   pip install -e ".[harness-ask-your-docs]"

export OPENAI_API_KEY=sk-...   # any placeholder works for local servers
```

The agent works out of the box with the default fastembed embedder. For the
Qwen3 GPU-index / CPU-serve recipe below, also install the embedder extras:

```bash
pip install "pydocs-mcp[harness-ask-your-docs,sentence-transformers,openvino]"
```

## 1. Index your repos (GPU, once per repo)

```bash
pydocs-mcp --config configs/index_gpu.yaml index ~/code/frontend --cache-dir ~/pydocs-index --gpu
pydocs-mcp --config configs/index_gpu.yaml index ~/code/backend  --cache-dir ~/pydocs-index --gpu
```

Each run writes a portable `{name}_{hash}.db` + `.tq` bundle into the
workspace directory.

## 2. Chat

```bash
harness-ask-your-docs --workspace ~/pydocs-index --config configs/serve_cpu_openvino.yaml
```

`harness-ask-your-docs` launches the Streamlit UI. The chat model's endpoint
comes from the `ask_your_docs.llm` block of the same YAML the `--config` flag
points at: `base_url` (an OpenAI-format endpoint), `auth.token_url` (an
internal token service whose response body is the bearer; set `token_field`
when the body is JSON) or `auth.api_key_env` (the name of the environment
variable holding a key), and `vision` (`true`, `false`, `null` to detect, or
`{model: …}` to send images to a second model on the same endpoint).
`OPENAI_BASE_URL` / `LLM_MODEL` override the YAML, `--base-url` / `--model`
override those, and the sidebar's **Connection** dialog overrides everything
for the session — it lists the endpoint's models (with a ↻ to re-ask the
endpoint), renews the token, carries the model settings below and tests the
connection. With no `llm` block the
agent uses the vendor default endpoint and `OPENAI_API_KEY`, as before. The
pydocs-mcp server the UI starts sees the environment of the shell you launched
from, minus trace variables, exported shell functions and values containing
`${...}`. So an exported `OPENAI_BASE_URL` also re-points that server's OpenAI
clients (an `embedding.provider: openai` embedder whose `embedding.base_url` is
null, and the LLM client) and sends them their key. Set the chat endpoint with
`--base-url` or `ask_your_docs.llm.base_url`, and embeddings with
`embedding.base_url`. Keys are never put in YAML or on the command line: the
dialog has no key field, no launch flag carries one, and only a token's last
four characters are ever shown.

Above the dialog button, one status line reads host, model, bearer and vision
verdict (`vision: yes (configured)`), and it is where the connection warnings
land: a session endpoint that differs from the configured `base_url`, a bearer
sent over plain `http` to a non-loopback host, a key variable that is unset, a
token service that would not answer. None of that crashes the page — a failed
bearer or a failed model listing leaves the dialog reachable with the reason in
its caption, and every failure the UI renders, a rejected bearer included, is
redacted.

`--workspace` / `--config` / `--port` and `PYDOCS_WORKSPACE` / `PYDOCS_CONFIG`
prefill the sidebar; anything after `--` is forwarded to `streamlit run` (e.g.
`-- --server.headless true`). Answers cite `project` + `package.module` and
render code in fenced blocks.

The agent's behavior is configured under the `ask_your_docs:` block of the
same pydocs-mcp YAML the `--config` flag points at: `architecture` (`auto`
routes by the detected model capability — it builds
`multimodal.preferred_architecture`, `inline` by default, on a vision model,
and the separate describe hop whenever `ask_your_docs.llm.vision` names a
second model; `text_react` pins the classic text agent; `inline` /
`vision_subagent` are the image-capable graphs), `multimodal.detection` (the
capability-detection ladder, short-circuited when `ask_your_docs.llm.vision`
states the answer — the status line's vision cell shows the verdict and its
source), `multimodal.text_only_fallback` (`reject` | `describe`), and `images`
limits. Every key also accepts an env override of the form
`PYDOCS_ASK_YOUR_DOCS__ARCHITECTURE=inline`
(`PYDOCS_` prefix, `__`-nested path). On vision-capable models, attach
images (screenshots, error dialogs, diagrams) with the paperclip in the
chat input; recent images stay reinspectable — when a later question
refers back to one, the agent's `reinspect_images` tool re-reads just the
relevant image(s) against the new question (`images.session_retention`
bounds the store).

The sidebar's **Scope** pickers (project / own code vs dependencies / package)
pin every question to a slice of the corpus. A `langchain-mcp-adapters` tool
interceptor forces the pin onto the tool calls — the `project` on every tool,
and the `package` / own-vs-dependency filters on the search tools — so the
choice is enforced deterministically rather than trusted to the model. The
question is also prefixed with a `[pinned scope: ...]` note so the agent knows
why.

**Light / dark theme.** Switch with Streamlit's own menu: the **⋮** button at the
top right → **System** / **Light** / **Dark**. **System** follows your OS setting;
your browser remembers the choice for this app.
The launcher registers both palettes with Streamlit (`[theme.light]` and
`[theme.dark]`), so every element — chat text, code, dropdowns, the sidebar —
switches together. If you launch with `streamlit run` directly instead of
`harness-ask-your-docs`, the app falls back to Streamlit's stock light/dark themes.

### Model settings

Below the model picker, the **Connection** dialog offers what this model can
actually be asked for: a **Thinking** switch (`Auto · Off · Low · Medium ·
High`, or `Auto · On` for a model that only switches thinking on and off),
**Temperature** and **Max output tokens**, with **Top p** and **Seed** under a
collapsed **More**. A blank field means the model's own default and is not sent;
`Auto` sends no thinking setting at all. The line above them ends with the
provider in force — the one you set under `provider:`, or, on `auto`, the one
the dialog recognised: `OpenAI`, `OpenRouter`, `vLLM`, `LiteLLM`, or `provider
unknown — settings unverified`.

**A control the model or the endpoint cannot honour is not shown, and its saved
value is not sent.** There is no greyed-out row and no caption saying so: the
dialog offers only what will land. What that is comes from the endpoint itself
— OpenRouter's model listing, a LiteLLM gateway's `/model_group/info` — plus a
small table of model families that applies everywhere (`gpt-5-mini` takes no
temperature; a vLLM Qwen3 is offered `Auto · On`, because turning its thinking
off through this field is not verified end to end). So a `temperature: 0.2` in your YAML
simply does not travel to a model that refuses it. The honest channels are
**Test connection**, whose result line ends with exactly what went out — `test
passed: OK · sent reasoning_effort=low, temperature=0.2,
max_completion_tokens=4096` — and one `chat_params_effective` JSON log line
each time the settings are resolved (the agent build, and Test connection),
which names what was sent and what was dropped: names only, never values.

Two things the page learns while you use it. If the endpoint answers a question
with a 400 naming a setting it was sent, that control is hidden for the rest of
the session and the turn ends with `The endpoint rejected Thinking for
Qwen/Qwen3-8B (400), so it's off for this session. Send your question again.` —
nothing is retried behind your back, and **More** grows a **Restore hidden
settings** button. If a reply ends because it ran out of tokens while thinking,
the turn says `The reply ran out of tokens while thinking. Raise Max output
tokens or turn Thinking down.`

**Some models arrive with their maker's recommended numbers already in the
fields.** For a Qwen3.8 model the dialog opens on the card's thinking-mode
values — `Temperature 1.0`, `Top p 0.95` — and turning **Thinking** off swaps
them for the instruct pair, `0.7` and `0.80`. They are filled in, not applied
behind your back: you can see them, **Test connection** sends exactly them, and
they travel only once you press **Apply**. A value you set in YAML, or type
yourself, wins and survives the switch; a number the endpoint already reports as
its own default stays a grey placeholder instead, because that deployment
applies it anyway. **Use YAML settings** ends the recommendation for the
session. The card's other advice — `top_k`, `min_p`, the penalties — has no
route through the five keys below and is not sent.

The starting values come from the same YAML:

```yaml
ask_your_docs:
  llm:
    base_url: https://llm.internal/v1
    provider: auto        # auto | openai | openrouter | vllm | litellm | generic
    params:               # every key optional; an absent one is not sent
      thinking: low       # auto | off | low | medium | high (a bare off works)
      temperature: 0.2    # [0, 2]
      max_tokens: 4096    # integer >= 1; sent as max_completion_tokens
      top_p: 0.95         # (0, 1]
      seed: 7             # integer in [0, 2**63-1]
```

`provider` only pins how the request is built; `auto` reads the endpoint URL.
Those five keys are the whole vocabulary — `reasoning_effort`, `extra_body`,
`stop`, the penalties and friends are refused by name, each with its own
message. An environment override takes the JSON form, because environment
values arrive as text and a numeric string is refused:
`PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS='{"temperature": 0.2}'`. Whatever the dialog
holds when you press **Apply** replaces this block for the session.

**Behind a LiteLLM proxy, check `drop_params` yourself.** With it on, the proxy
silently drops a parameter the upstream model does not take and answers as if
it had been honoured — no 400, nothing a client can detect. The UI reports what
it sent; whether the gateway forwarded it is the operator's to verify. One
`litellm_detected` log line, carrying that note, is written the first time the
dialog recognises such an endpoint.

### Activity panel

Every answer has a panel above it. Collapsed, it is one line — `Done in 6.4 s · 4
steps · 3 files · reasoning shown`, `Answered without searching · 1.1 s` when no tool
was called, or `Stopped after 3 steps · … · the model endpoint rejected the request`
when the turn failed. Expanded, it lists the steps in plain words: the rephrased
question (when the rewrite changed it), the pinned scope, each tool call behind its
own icon (search, map, folder, …) with its
outcome and up to three file chips, notes such as `Results were cut off at the
limit`, and the model's reasoning for each round. Below the answer, **Sources** lists
the files the answer names and **Also looked at** the rest. The sidebar's **Show
technical details** toggle adds, per call, the arguments the model proposed, the
arguments actually sent after the scope pin, the result's `meta` and a preview of
the raw result. A failed or stopped turn stays in the chat with its steps.

Reasoning appears only when the endpoint returns it (OpenRouter's `reasoning`, vLLM /
DeepSeek's `reasoning_content`); the request is never changed to ask for it. A
separate caption under the status line says what to expect from this model
(`Reasoning: shown (seen in answers)`, `hidden by provider`, `not shared by this
model`, `unknown until the first answer`), learned from its answers without any extra
call; when the request itself carries Thinking off, it says so at once —
`Reasoning: off (your setting)`. The reasoning is the model's working notes: it can be incomplete or differ from
what the model actually did, and it can quote the files the agent read. It is shown
as plain text, collapsed by default, and redacted like everything in the panel — the
bearer and the value of the configured key variable are masked — but a secret that
sits inside one of your repository files cannot be recognised. Set
`ask_your_docs.ui.reasoning.display: hidden` to keep it off the page.

The panel is tuned under `ask_your_docs.ui` in the same YAML:

```yaml
ask_your_docs:
  ui:
    activity:
      enabled: true              # false = the plain spinner + answer
      live: true                 # false = no streaming; the panel is built after the turn
      technical_details: false   # default of the sidebar toggle
      collapse_when_done: false  # true folds a finished turn into its summary line
                                 # failed / stopped turns always stay expanded
      history_keep: 20           # older turns keep only their summary line + sources
    reasoning:
      display: collapsed         # collapsed | expanded | hidden
      max_chars: 20000           # per turn; head + tail are kept
```

Set `live: false` if your server streams tool calls badly: the model is then called
without streaming, exactly as with the panel off.

### Graph explorer

<p align="center">
  <img src="screenshot-graph.png" width="860"
       alt="Graph explorer — a project's packages as a node-link graph, coloured by relationship, with shape/colour node-type filters and a Hide test files toggle">
</p>

The app has a second page (sidebar → **Graph**) that visualizes a project's
structure as a **zoom / drill-down**: the canvas shows the direct children of
the current focus (a breadcrumb up top), and clicking a container — package
`⬡`, module `◆`, class `■`, or doc file `▲` — zooms into it; click a breadcrumb
segment to zoom back out. Node types are shown by shape + colour (legend) and
edges are coloured by relationship (calls / imports / inherits). Leaves
(functions, methods, decisions) open a docstring panel. A **Hide test files**
toggle and node/edge filters trim the view. Click **➕ Add to question** on any
node to attach it to your next chat question. The page reads the index bundles
directly (read-only) — no model calls.

Use the **Content** selector to switch between **Codebase** (modules, classes,
functions), **Documentation** (markdown files and architectural decisions), or
both — with `documents` / `concerns` links from docs and decisions to the code.

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
| `batch_size`, `device` (`--gpu`), `query_prompt_name`, `query_prefix` | `provider`, `model_name`, `dim`, `max_seq_length`, `normalize`, `bit_width` |

(`backend` / `model_file_name` are the deliberate exception: identical vector
space, different runtime — which is exactly why the serve file is serve-only.)

Prefer a lighter setup? Swap both configs to
`Qwen/Qwen3-Embedding-0.6B` + `dim: 1024` (or drop `--config` entirely to use
the built-in `BAAI/bge-small-en-v1.5` default — then index without a config
too, so the embedders match).

No internet on either box? See
[Air-gapped: GPU index, CPU serve, internal token service](#air-gapped-gpu-index-cpu-serve-internal-token-service)
below for the same recipe with the model and the wheels staged by hand.

## Air-gapped: GPU index, CPU serve, internal token service

Three machines, and only the first one has internet:

| box | what it does | network |
|---|---|---|
| **staging** | downloads the wheels and the embedding model, hands you files | yes |
| **GPU box** | embeds the corpus once, writes the index bundles | no |
| **CPU box** | serves the bundles and runs the chat UI | no |

The chat model is an OpenAI-format endpoint already inside your network, and
its bearer comes from an **internal token service** — no key is ever typed,
stored in YAML, or passed on a command line.

Read ["Why two embedding configs?"](#why-two-embedding-configs) first; this
section is the same recipe with nothing downloaded at run time.

### The invariant

`provider`, `model_name`, `dim`, `max_seq_length`, `normalize` and `bit_width`
define the **vector space**. They must be identical in the index file and the
serve file. Only `model_name` and `dim` are actually checked when the server
opens a bundle — a mismatched `max_seq_length` starts cleanly and then
truncates queries differently from the documents, silently. Keep them in sync
by hand.

`backend` is the deliberate exception: `openvino` at serve time, **absent
(torch) at index time**. It folds into the chunk-cache identity, so indexing
under the serve file would re-embed the whole corpus on CPU. Index with the
index file; serve with the serve file.

One air-gap-specific trap: the bundle stamps `model_name` **as a string**, and
the server compares that string to its own config. A side-loaded model
directory is a `model_name`, so **use the same absolute directory path on both
boxes**. Index under `/opt/models/Qwen3-Embedding-4B` and serve under
`~/models/Qwen3-Embedding-4B` — identical weights, different string — and the
server refuses the bundle at startup.

### 0. Stage everything on the connected box

**0a — two wheelhouses.** `pip download` resolves for the machine it runs on,
so run it on a host whose OS, architecture and Python version match the target
(e.g. Linux x86_64 / CPython 3.11):

```bash
# for the CPU box: the UI + the OpenVINO runtime
pip download 'pydocs-mcp[harness-ask-your-docs,openvino]' -d ./wheelhouse-cpu

# for the GPU box: the embedder + the OpenVINO exporter — the UI is not needed there
pip download 'pydocs-mcp[openvino]' -d ./wheelhouse-gpu
```

`[openvino]` resolves `sentence-transformers[openvino]`, so it already contains
everything `[sentence-transformers]` does, and it still pulls torch — the CPU
box is not a torch-free box. The GPU box needs `[openvino]` too, not just
`[sentence-transformers]`: step 3 runs the torch → OpenVINO conversion there,
and that conversion is `optimum-intel` + `openvino` code. Without them, step 3
stops at `Using the OpenVINO backend requires installing Optimum and OpenVINO`.
Neither box needs `[late-interaction]` or `[graph]`.

For a CUDA build of torch on the GPU box, add
`--extra-index-url https://download.pytorch.org/whl/cu<NNN>` (your CUDA
version) to the GPU download. Check the wheelhouse actually contains the
`+cu<NNN>` torch wheel before you walk it over.

**0b — the OpenBLAS package (Linux targets only).** `turbovec` links the CBLAS
C-ABI, and it is a required dependency of *both* boxes. Stage the system
package too — on Debian/Ubuntu:

```bash
sudo apt-get install --download-only -y libopenblas-pthread-dev   # .debs land in /var/cache/apt/archives/
```

Copy those `.deb` files next to the wheelhouses. macOS (Accelerate) and
Windows (MSVC runtime) need nothing.

**0c — the embedding model.** Download the full sentence-transformers
repository into a directory:

```bash
pip install huggingface_hub
hf download Qwen/Qwen3-Embedding-4B --local-dir ./Qwen3-Embedding-4B
```

Check the directory before you ship it. It must contain at least:

```
Qwen3-Embedding-4B/
├── modules.json                        # without this the wrong pooling recipe is used
├── config_sentence_transformers.json   # the model's named prompts
├── config.json
├── model*.safetensors                  # or shards + their index file
├── tokenizer.json  tokenizer_config.json  (+ vocab / merges if present)
└── 1_Pooling/config.json
```

`modules.json` is the one that changes results if missing: without it,
sentence-transformers falls back to a generic mean-pooling recipe instead of
the model's own. It says so — `No sentence-transformers model found with name
<dir>. Creating a new one with mean pooling.` — but only as one warning in a
noisy startup, and nothing downstream complains, so the vectors just come out
different.

**0d — checksums.** Produce a manifest now, on the box where the files are
known good, and carry it with them. Walk the tree rather than globbing:
`Qwen3-Embedding-4B/*` would hand `sha256sum` the `1_Pooling` directory and
skip the file inside it.

```bash
find wheelhouse-cpu wheelhouse-gpu Qwen3-Embedding-4B -type f \
    -exec sha256sum {} + > STAGED.sha256          # macOS: shasum -a 256
```

### 1. Install on the GPU box

```bash
sudo dpkg -i libopenblas*.deb                     # Linux only
pip install --no-index --find-links ./wheelhouse-gpu 'pydocs-mcp[openvino]'

sudo mkdir -p /opt/models
sudo cp -r ./Qwen3-Embedding-4B /opt/models/       # the path both boxes will use
```

Export the offline switches **in the shell you launch from**. `huggingface_hub`
reads them once, at import, into module constants, so setting them later in the
process is too late. Only `1`, `ON`, `YES` and `TRUE` count as true:

```bash
export HF_HUB_OFFLINE=1              # the one that matters
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_UPDATE_CHECK=1
export DO_NOT_TRACK=1                # huggingface_hub reads this as a second telemetry off-switch
```

`TRANSFORMERS_OFFLINE` is deliberately not in that list: transformers 5.x — the
range this stack installs — no longer reads it anywhere, so exporting it is
inert. Keep it only if you also run something older against the same shell.

Belt and braces: when `embedding.model_name` names an existing directory,
pydocs-mcp sets `HF_HUB_OFFLINE=1` itself before the first model load
(`extraction/strategies/embedders/local_source.py`), with `setdefault`, so your
own explicit setting always wins.

Verify the install without touching the network:

```bash
python -c "import pydocs_mcp, torch; print(torch.cuda.is_available())"
```

You should see `True`. An `ImportError: ... undefined symbol: cblas_sgemm`
means the OpenBLAS package did not install — see `INSTALL.md`.

### 2. Index on the GPU box

`configs/index_airgap.yaml` — the shipped `index_gpu.yaml` with the model
name replaced by the staged directory:

```yaml
# INDEX-time config, air-gapped — embed the corpus ONCE on GPU (torch backend).
#
# Usage (once per repo you want in the workspace):
#   pydocs-mcp -v --config configs/index_airgap.yaml index ~/code/myrepo \
#       --cache-dir ~/pydocs-index --gpu
#
# model_name is a LOCAL DIRECTORY: sentence-transformers loads it straight off
# disk and never asks the Hub. Use the SAME absolute path on the serving box —
# the bundle stamps this string and the server compares it verbatim.
#
# Keep model_name / dim / max_seq_length identical to serve_airgap.yaml (they
# define the vector space); batch_size and --gpu are free to differ. Do NOT add
# `backend` here: it folds into the chunk-cache identity.
embedding:
  provider: sentence_transformers
  model_name: /opt/models/Qwen3-Embedding-4B
  dim: 2560
  batch_size: 2          # 4B model — small batches keep VRAM bounded
  max_seq_length: 2048
```

Index one repo per command:

```bash
pydocs-mcp -v --config configs/index_airgap.yaml index ~/code/frontend \
    --cache-dir ~/pydocs-index --gpu
pydocs-mcp -v --config configs/index_airgap.yaml index ~/code/backend \
    --cache-dir ~/pydocs-index --gpu
```

`-v` makes the per-package lines visible — `Project: 4812 chunks, 1330
symbols, 402 trees`, then one `ok <package> <version> (…)` per dependency.
Each repo leaves a `{name}_{hash}.db` + `{name}_{hash}.tq` pair in
`~/pydocs-index`.

What you must **not** see: any log line about contacting huggingface.co. If
one appears, `model_name` is not pointing at an existing directory and the
loader fell back to repo-id mode.

### 3. Pre-bake the OpenVINO model (still on the GPU box)

Skip this and the CPU box re-converts the 4B model into a temporary directory
on **every process start** — the conversion is not cached anywhere. Bake it
once, into the model directory itself, and ship the result:

```bash
python - <<'PY'
from sentence_transformers import SentenceTransformer
m = SentenceTransformer("/opt/models/Qwen3-Embedding-4B", backend="openvino", device="cpu")
m.save_pretrained("/opt/models/Qwen3-Embedding-4B")
PY
```

This is a local torch → OpenVINO conversion; it needs no network, only torch,
`optimum-intel` + `openvino`, and the weights. Two warnings are expected and
benign, both from sentence-transformers: `No 'openvino_model.xml' found in
'/opt/models/Qwen3-Embedding-4B'. Exporting the model to OpenVINO.` on the way
in, and `Saving the exported OpenVINO model is heavily recommended…` on the way
out — the `save_pretrained` line above is precisely that saving step.
Afterwards:

```bash
ls /opt/models/Qwen3-Embedding-4B/openvino/
# openvino_model.bin  openvino_model.xml
```

Those two files are what stops the re-export. Ship the whole directory.

Do **not** ship a `openvino_model_qint8_quantized.xml` instead: a quantized
query encoder against fp32 document vectors moves the query side of the vector
space, and nothing in the serve-time guard catches it.

### 4. Copy to the CPU box, then verify

Copy, per repo:

- `~/pydocs-index/{name}_{hash}.db`
- `~/pydocs-index/{name}_{hash}.tq`
- `~/pydocs-index/{name}_{hash}.db-wal` and `-shm` **if they exist** (they
  will not after a clean indexer exit; if the indexer was killed, they hold
  committed data)

Plus `/opt/models/Qwen3-Embedding-4B` (with its `openvino/` subdirectory) and
`wheelhouse-cpu`. Do **not** copy `pydocs-links.sqlite3` or anything under a
`links/` directory — that is a per-deployment overlay, not part of a bundle.

Checksum on the GPU box, verify on the CPU box:

```bash
# GPU box
cd ~/pydocs-index && sha256sum -- *.db *.tq > BUNDLES.sha256
# CPU box, after the copy
cd ~/pydocs-index && sha256sum -c BUNDLES.sha256
```

Verify this before you serve. A bad copy fails in two different ways depending
on which path opens it, and only one of them is loud:

- **Serving the workspace** (`--workspace`, which is what this guide does) is
  read-only and checks the file before touching it, so a damaged bundle is
  **refused by name** and left exactly as it is on disk:
  `sqlite3.DatabaseError: /…/frontend_1a2b3c4d5e.db: file is not a database`,
  or `…: database disk image is malformed` for a partial copy whose header
  survived. Nothing loads, and the whole workspace fails to open — not just
  that one project.
- **Indexing** into a cache file that is not valid SQLite is the opposite
  policy — the writable cache is derived data, so it is **deleted and recreated
  empty**, and an unrecognised schema version is rebuilt from scratch, both with
  a warning. Never point an index run at a hand-copied bundle you have not
  verified.

Then read the stamp the server will check:

```bash
sqlite3 ~/pydocs-index/frontend_1a2b3c4d5e.db \
  "PRAGMA integrity_check;
   PRAGMA user_version;
   SELECT project_name, embedding_provider, embedding_model, embedding_dim
   FROM index_metadata;"
```

Expect `ok`, a schema number, and a row whose `embedding_model` is exactly
`/opt/models/Qwen3-Embedding-4B` and whose `embedding_dim` is `2560`. That
string is what the serve config must match.

Run the **same pydocs-mcp version** on both boxes. A bundle from a newer build
is refused outright (with a message naming both schema versions); a bundle
from an older build is migrated in place, one-way, on the serving box.

### 5. Install on the CPU box

```bash
sudo dpkg -i libopenblas*.deb                     # Linux only
pip install --no-index --find-links ./wheelhouse-cpu 'pydocs-mcp[harness-ask-your-docs,openvino]'

sudo mkdir -p /opt/models
sudo cp -r ./Qwen3-Embedding-4B /opt/models/       # SAME absolute path as the GPU box
```

Export the same offline switches as step 1, in the launching shell. The
pydocs-mcp server the UI starts inherits the environment of that shell, so
they reach it too — with one exception: **never export a value containing a
literal `${...}`**, which is withheld from the child.

Optional, if outbound connection attempts are logged and you would rather not
see them: the OpenVINO stack registers a telemetry client at import. Opt out
once per box with its own console script:

```bash
opt_in_out --opt_out      # writes ~/intel/openvino_telemetry
```

Its sends fail and are swallowed on an air gap either way — this only silences
the attempts.

### 6. The serve config

`configs/serve_airgap.yaml` — the shipped `serve_cpu_openvino.yaml` with the
local model directory and an explicit renewal policy:

```yaml
# SERVE-time config, air-gapped — CPU inference via OpenVINO. Only the QUERY is
# embedded at serve time (one short text per search), so CPU is plenty.
#
# Usage:
#   harness-ask-your-docs --workspace ~/pydocs-index --config configs/serve_airgap.yaml
#
# DO NOT INDEX with this file: `backend` folds into the chunk cache identity,
# so re-indexing under it would re-embed everything on CPU.
embedding:
  provider: sentence_transformers
  model_name: /opt/models/Qwen3-Embedding-4B  # must match index_airgap.yaml, character for character
  dim: 2560                                   # must match index_airgap.yaml
  max_seq_length: 2048                        # must match index_airgap.yaml
  backend: openvino          # uses the pre-baked openvino/ subdirectory; no export at startup
  query_prompt_name: query   # Qwen3 embeddings are asymmetric — use its query prompt

# The chat model: one OpenAI-format endpoint behind an internal token service.
ask_your_docs:
  llm:
    base_url: https://llm.internal/v1
    auth:
      token_url: https://token.internal/access-token   # no credentials in the URL
    # token_field is a SIBLING of `auth`, not a key inside it. null (the
    # default) means the whole response body is the token; name a field to read
    # it out of a JSON body instead, e.g. token_field: access_token
    token_field: null
    # Renew the bearer and retry the request once on these statuses. Only 401,
    # 403 and 407 are accepted. Narrow to [401] if your gateway means 403 as
    # "this token may not use this model" — renewing then wastes a round trip.
    renew_on_status: [401, 403, 407]
    vision: true                        # the served model is multimodal; no probe
```

How the token service is called: a plain **GET**, no body and no headers, with
a 5-second timeout, up to 3 attempts, backing off 2s then 4s. The response
body (stripped) is the bearer. It is fetched lazily on the first question and
renewed at most once every 5 seconds. The token is never written to disk, never
logged, and never shown beyond its last four characters.

Three rules the config loader enforces, loudly, at startup: exactly one of
`token_url` / `api_key_env` under `auth:`; no username, password or query
string in `token_url`; and a `token_url` needs a `base_url` beside it (a token
service authenticates one named endpoint, never the vendor default).

If your token service presents an internal CA, set `SSL_CERT_FILE` — or
`SSL_CERT_DIR` for a directory of certificates — in the shell you launch from.
Those two are what the token fetcher (`httpx`) reads; it does **not** read
`REQUESTS_CA_BUNDLE`, so setting only that leaves the fetch failing on
verification.

### 7. Launch

```bash
harness-ask-your-docs --workspace ~/pydocs-index --config configs/serve_airgap.yaml
# headless box? append:  -- --server.headless true
```

Open the UI. In the sidebar, just above the **Connection** button, the status
line should read four cells:

```
llm.internal · qwen2.5-72b-instruct · token …4f2a 14:32 · vision: yes (configured)
```

- `token …4f2a 14:32` — the bearer arrived; the time is when it was fetched.
- `token unavailable ⚠` — the token service did not answer. Hover the line for
  the reason; the Connection dialog stays reachable and has a **Renew** button.
- A trailing `⚠ http` means a bearer is travelling over plain `http` to a
  non-loopback host. Use `https` for `base_url` on a real deployment.
- A trailing `⚠ endpoint differs from ask_your_docs.llm.base_url` means a
  launch flag, env var or the dialog overrode the file.

### 8. Confirm it is really offline

Ask a question in the UI and watch the activity panel: it should list tool
calls and finish with `Done in N s · … steps · … files`.

To confirm nothing reaches out, run the check on the CPU box **with the
network interface down** (or with egress blocked and logged):

```bash
pydocs-mcp --config configs/serve_airgap.yaml get_overview --workspace ~/pydocs-index
```

This loads the embedder exactly as the server does and prints the workspace
card — every indexed project, its packages and its freshness line. If it
prints, the model came off the local disk and the bundles matched.

Expect three of the nine tools — `grep`, `glob`, `read_file` — to report that
the project source tree is unavailable. They need the original checkout, which
lives on the GPU box; indexed retrieval (`search_codebase`, `get_symbol`,
`get_references`, …) is unaffected.

### If it goes wrong

| symptom | cause |
|---|---|
| `project 'frontend' (…) was indexed with embedder '…' (dim …), but the configured pipeline uses '…'` at startup | the two `model_name` strings differ. Usually one box spells the model directory differently (`~/models/…` vs `/opt/models/…`), or one file still names the Hub repo id. Make both files carry the same absolute path. |
| `No 'openvino_model.xml' found in '…'. Exporting the model to OpenVINO.` on every start | the pre-baked `openvino/` subdirectory did not make the trip. Re-do step 3 and copy the directory again — otherwise the 4B model is re-converted per process start. |
| `Prompt name 'query' not found in the configured prompts dictionary with keys […]` at the first search | the model directory has no `config_sentence_transformers.json`, or the model does not define a prompt named `query`. Re-stage the full directory, or drop the `query_prompt_name` line — sentence-transformers picks the model's own query prompt when it defines one. |
| `ImportError: turbovec/_turbovec.abi3.so: undefined symbol: cblas_sgemm` | the OpenBLAS system package was not installed on this box. See `INSTALL.md` for the alternatives. |
| an OpenVINO backend error that tells you to `install sentence-transformers[openvino]` when the extra *is* installed | a version mismatch between `optimum-intel` and `openvino`, not a missing extra. Read the chained exception; install both from one wheelhouse resolution rather than pinning them by hand. |
| an `ImportError` mentioning **torchvision** while loading the model | transformers 5.0–5.9 hard-require torchvision for image-processing classes some repos reference. Either upgrade transformers to `>=5.10,<6` (it falls back to Pillow there; the extra caps transformers at `<6`, so this stays inside the pin) or install a torchvision wheel that exactly matches your installed torch — torchvision exact-pins its torch sibling. Stage whichever you choose in the wheelhouse; the error itself spells both remedies out. |
| the workspace refuses to open with `<path>: file is not a database` or `<path>: database disk image is malformed` | the named `.db` copy is truncated or corrupt. The bundle is untouched on disk, and no project loads until it is fixed. Restore from the checksummed copy (step 4). |
| the first index run on a new pydocs-mcp version re-embeds everything | the effective file-extension scope or the embedder identity changed, which invalidates every chunk hash by design. Budget that pass on the GPU box, before shipping bundles — never discover it on the CPU box. |
| `index --config configs/serve_airgap.yaml … --gpu` stops with `The OpenVINO backend (embedding.backend: openvino) runs on CPU/iGPU and is incompatible with device: cuda` | you pointed an index run at the serve file. `--gpu` runs the same backend/device check as a YAML `device: cuda` line, so the run is refused at config load, before anything is embedded. Index with `configs/index_airgap.yaml` (step 2). The same mistake **without** `--gpu` is not refused — OpenVINO on CPU is a valid pairing — and quietly re-embeds the whole corpus on CPU under the OpenVINO identity; there the rule "index with the index file" is the only guard. |
| the server tries to download `BAAI/bge-small-en-v1.5` | `--config` was missing or came *after* the subcommand. It is a root flag: `pydocs-mcp --config … index …`. |

### What we could not verify offline

This recipe was assembled by reading the code and the installed packages,
without network access. These points were **not** executed and should be
confirmed on your own hardware before you rely on them:

- that Qwen3-Embedding-4B converts cleanly to OpenVINO under this version
  combination, and what the conversion costs in time, RAM and disk;
- that the OpenVINO-encoded query vectors agree numerically with the
  torch-encoded document vectors closely enough for your retrieval quality
  (fp32 IR is expected to; a quantized export definitely does not);
- that this model's repository defines a prompt named `query`;
- that a `.tq` file copies cleanly between different CPU architectures — only
  same-architecture transfers are recommended on the evidence available;
- the exact CUDA wheelhouse incantation for your driver version;
- that `libopenblas-pthread-dev` alone satisfies turbovec on your distribution;
- that `pip download` for `[openvino]` resolves a complete, mutually compatible
  `optimum-intel` + `openvino` pair for your platform — the version-mismatch row
  in the table above is the failure to watch for.

Everything else in this section — the CLI flags, both YAML files (they were
loaded through the real config loader), the token-service envelope, the
schema-version and corrupt-bundle behaviour, the status-line wording, the
environment-variable semantics and every quoted log line — was checked against
the code and the installed packages on this machine.
