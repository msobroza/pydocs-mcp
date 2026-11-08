# Getting the model's reasoning out of ChatOpenAI for the ask-your-docs UI

The UI can't show reasoning today because langchain-openai 1.1.9 throws it away, even though OpenRouter already sends it for `qwen/qwen3.8-27b` without being asked. The fix is two small method overrides in a ChatOpenAI subclass plus a streaming agent loop. I checked the overrides by replaying the three recorded responses offline: the reasoning comes through, streamed and non-streamed, with and without a tool bound. I used 3 paid calls, all under 200 output tokens, about $0.0006 in total.

## 1. What the code does today

- **Non-streaming:** `_convert_dict_to_message` (`langchain_openai/chat_models/base.py:161-227`) keeps only `content`, `function_call`, `tool_calls` and `audio`. `_create_chat_result` (1480-1548) adds `parsed` and `refusal`. The `reasoning`, `reasoning_content` and `reasoning_details` fields are silently dropped.
- **Streaming:** `_convert_delta_to_message_chunk` (369-424) keeps only `content`, `function_call` and `tool_calls`. `_convert_chunk_to_generation_chunk` (1092-1163) adds nothing more.
- **Reasoning in ChatOpenAI today:** the `reasoning=` parameter and reasoning summaries only exist on the OpenAI Responses API path (base.py 4649-4690). They don't apply to OpenAI-compatible Chat Completions servers.
- **langchain-core fallback:** `_extract_reasoning_from_additional_kwargs` (`langchain_core/messages/base.py:24-44`) reads only `additional_kwargs["reasoning_content"]`. But ChatOpenAI tags every message as provider "openai", so `content_blocks` goes through the openai translator instead (`block_translators/openai.py:157-178`). That translator emits only text and tool-call blocks.
  - Confirmed in the replay: even after my subclass fills `reasoning_content`, `content_blocks` stays `['tool_call']`. **The harness has to read `additional_kwargs` directly, not `content_blocks`.**
- **Harness:** `agent.py:480-481` uses `agent.ainvoke(...)` and keeps only `result["messages"][-1].content`. Every intermediate AIMessage and ToolMessage is discarded. `app.py:477` shows only a spinner. So tool calls are invisible for the same structural reason.

## 2. Paid probe results (recordings in `.../scratchpad/ayd-ui/rec/`)

**Call A** — ChatOpenAI, non-streaming, tool bound, no reasoning parameter (the harness's current request shape):
- On the wire, `choices[0].message` had these keys: `content` (null), `reasoning` (90-character string), `reasoning_details` (`[{type: "reasoning.text", text, format: "unknown", index: 0}]`), `refusal`, `role`, `tool_calls`. The request was routed to the Parasail provider.
- The AIMessage kept only `refusal` in `additional_kwargs`.
- Usage reported 22 of 51 output tokens as reasoning, costing $0.00019.
- **The harness is already paying for reasoning it throws away.** This includes the reformulation call, which uses the same model object.

**Call B** — ChatOpenAI streaming, no tool, `extra_body={"reasoning":{"effort":"low"}}`:
- The wire deltas carried `reasoning` and `reasoning_details` together, with the same text.
- The merged chunk had empty `additional_kwargs`.
- Effort `low` cut reasoning to 2 tokens (A had 22 with no parameter), though the prompt was different.
- First reasoning arrived at 0.99s, first content at 1.20s, end at 2.02s.
- Quirks the UI needs to handle:
  - an SSE comment `": OPENROUTER PROCESSING"`;
  - `finish_reason` sent on 2 chunks, so the merged metadata reads `"stopstop"` and the model name appears twice;
  - the answer text starts with `"\n\n"`.

**Call C** — raw httpx streaming, tool bound, effort `medium`:
- 19 deltas carried `reasoning` and `reasoning_details` (71 characters); the tool-call deltas followed.
- Timing: reasoning from 0.84s to 1.08s, tool call from 1.42s to 1.73s, end at 1.82s.
- Usage: 19 reasoning tokens out of 48 output tokens; the streamed usage included `cost` = $0.00018.

**Offline replay** (`replay.py`, no cost):

| Recording | Stock `ChatOpenAI` | Subclass |
|---|---|---|
| A (non-streaming) | reasoning dropped | 90 chars kept |
| B (streaming) | reasoning dropped | 3 chars kept |
| C (streaming + tool) | reasoning dropped | 71 chars kept |

- For B and C, the merged reasoning exactly matched the raw wire text.
- Reasoning is not sent back to the model on the next turn: serialising the enriched message gives only `['content','role','tool_calls']`. So it doesn't inflate later prompts.
- In the langgraph `create_react_agent` replay (C, then a tool step, then B) with `stream_mode=["messages","updates"]`, 20 chunks carrying reasoning arrived from the `agent` node, plus the ToolMessage from `tools` and the node updates. The UI can stream reasoning live and see every tool step from this one loop.

**OpenRouter `/models`** (public, free): `qwen/qwen3.8-27b` lists `include_reasoning`, `reasoning` and `reasoning_effort`. Across the 436 models, 305 list `reasoning` and the same 305 list `include_reasoning`. 31 of them bill reasoning at a separate `pricing.internal_reasoning` rate.

## 3. Proposal

### (a) Extraction
Add a small subclass at the one place the harness builds ChatOpenAI (`llm_connection.build_chat_model`, which currently returns `ChatOpenAI(**kwargs)`). The version I validated is in `replay.py`:

```python
_REASONING_STRING_FIELDS = ("reasoning_content", "reasoning")  # first hit wins: OpenRouter sends reasoning + reasoning_details with the same text
_DETAIL_TEXT_TYPES = {"reasoning.text": "text", "reasoning.summary": "summary"}
class ReasoningChatOpenAI(ChatOpenAI):
    def _create_chat_result(self, response, generation_info=None):      # non-streaming
        result = super()._create_chat_result(response, generation_info)
        body = response if isinstance(response, dict) else response.model_dump()  # the openai SDK keeps extra fields
        for gen, choice in zip(result.generations, body.get("choices") or []):
            if text := reasoning_text_from_payload(choice.get("message") or {}):
                gen.message.additional_kwargs["reasoning_content"] = text
        return result
    def _convert_chunk_to_generation_chunk(self, chunk, cls, info):     # streaming; runs before callbacks, so langgraph "messages" sees it
        gen = super()._convert_chunk_to_generation_chunk(chunk, cls, info)
        ...same, reading choices[0]["delta"]
```

- **Why `reasoning_content`:** it is the key langchain-core already uses for DeepSeek, xAI, Groq and Ollama, and string values in `additional_kwargs` concatenate correctly when stream chunks merge (verified).
- **Hidden reasoning:** add `reasoning_is_redacted()` for `reasoning.encrypted` details, so the UI can tell "the model reasoned but the provider hides it" apart from "no reasoning".
- **Servers that leave `<think>…</think>` inline:** `ThinkTagSplitter` in `replay.py` handles tags split across chunks, and the case where the closing tag appears without an opening one (Qwen3 and R1 chat templates put the opening tag in the prompt).
  - Caveat: in that orphan-close case, text streamed before `</think>` is first shown as answer and then moved to reasoning, so the UI has to re-render the answer area.
  - Make the splitter opt-in or auto-detected, so a literal `<think>` in a normal answer isn't mangled.
- **Local servers:** none were running, so these field names come from the servers' documentation and were not probed. vLLM and SGLang with a reasoning parser, llama.cpp (`--reasoning-format deepseek`, its default) and the DeepSeek API send `reasoning_content`. Ollama's OpenAI-compatible endpoint, and newer vLLM, send `reasoning`. llama.cpp with `--reasoning-format none` leaves the tags inline. Checking both names covers all of these whichever version is running.
- **Fragility:** both overrides are private methods, and the harness pins `langchain-openai>=0.2` with no upper bound (latest on PyPI is 1.6.2, which I didn't inspect). Add a contract test that replays the recorded A, B and C bodies through `httpx.MockTransport`, so an upgrade that renames these methods fails loudly.
  - `langchain-openrouter` 0.2.8 exists, but it only covers OpenRouter.
  - No public ChatOpenAI hook or callback can recover the dropped fields.
- **Agent loop:** replace `ainvoke` in `ask()` with `agent.astream(..., stream_mode=["messages","updates"])`. That yields reasoning deltas, tool-call chunks, ToolMessages and the final answer in one pass. Keep the final answer string as today's return value.

### (b) Capability signal
Mirror the vision capability ladder in `multimodal.py` (`CapabilitySource` plus the positive-only `_entry_hints_vision`), in this order:

1. **YAML setting** (`ask_your_docs.llm.reasoning`), which always wins.
2. **Observed on real turns**, at no extra cost:
   - reasoning text received → "shown";
   - `reasoning_tokens > 0` but no text, or an encrypted detail → "reasoned, text hidden by provider";
   - neither → leave it undecided.
3. **Endpoint metadata:** `supported_parameters` includes `reasoning` or `include_reasoning` → "supported". Treat this as positive-only, because most OpenAI-compatible servers don't publish this field. `model_listing.fetch_models_payload` already keeps every field of each entry.
4. **Static prefix table.**
5. **Default:** "unknown".

Show one badge in the status line or Connection dialog with four states: shown / hidden by provider / not exposed / unknown. Never spend a separate probe call on this.

### (c) Cost, latency and privacy
- **Cost:** reasoning is billed as output tokens: about 40–43% of output tokens in A and C, at $3 per million for this model, and a separate rate on 31 OpenRouter models. For qwen3.8-27b it happens by default, so showing it costs nothing extra. Reducing it is a request setting (OpenRouter `reasoning: {effort | max_tokens | enabled: false}`; `exclude: true` only hides it, it's still billed).
  - Per the repo rule, this belongs in YAML, not a new MCP parameter: e.g. `ask_your_docs.llm.reasoning.{display, request_extra_body}`. The request shape differs by endpoint (vLLM uses `chat_template_kwargs.enable_thinking`, OpenAI uses `reasoning_effort`).
- **Latency:** reasoning is the first thing to arrive (0.84–0.99s, before the tool call or answer at 1.2–1.4s). Streaming it replaces the silent spinner period. Long reasoning should be collapsed by default and capped in rendering, with a "show all" option.
- **Privacy:**
  - Reasoning can quote tool output (`read_file`, `grep`), including secrets in files. Pass displayed reasoning through `redact_bearer` like every other string that reaches the browser.
  - Never log the text; log only structured counts, e.g. `{"event":"reasoning_captured","chars":N,"reasoning_tokens":N,"source":...}`.
  - Keep it in a UI-only transcript, never in `st.session_state.history`, which is what gets sent to the model.
  - Label it as the model's scratchpad, which may be incomplete or not faithful to what the model actually did.

Files are in /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/ayd-ui:
- probe.py
- replay.py
- rec/ (A.body, B.body, C.body with timed files; response bodies only, no request headers, so no key on disk)
- models.json