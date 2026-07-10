"""Paired agent-efficiency harness (spec §D15).

Two-arm harness: the same headless agent answers SWE-QA-Pro questions with
bare file tools (arm A) vs with the pydocs-mcp MCP server attached (arm B);
a blind LLM judge scores answer quality; per-task-paired aggregation reports
cost / tool-call / file-read / token deltas at answer-quality parity.

Manual and expensive by design — never CI. Everything testable is pure or
Protocol-seamed; the one expensive path sits behind a subprocess adapter and
hard guardrails.
"""
