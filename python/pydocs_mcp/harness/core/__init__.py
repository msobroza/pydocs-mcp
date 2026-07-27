"""Harness-agnostic runtime shared by every in-tree agent harness.

First resident: the shared prompt pool (``core/prompts``) — the fallback
templates every harness's architecture namespaces resolve against. The
dependency rule is one-way by design: ``core`` never imports a concrete
harness; harnesses import ``core`` (spec §4.2 in
docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md).
"""
