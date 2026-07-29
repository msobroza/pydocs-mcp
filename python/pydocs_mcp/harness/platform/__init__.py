"""Harness-agnostic MACHINERY shared by every in-tree agent harness.

Zero harness instances and zero optimizable assets live here: the run contract,
the guidance fold, the prompt-namespace resolver and its pool loader, the
composed ``CliAgentHarness``, and the CLI-agent ``engines/`` adapters. The
optimizable text they read sits in ``harness/assets/``; the concrete harnesses
that compose them sit in ``harness/builtin/``.

The dependency rule is one-way by design and holds without qualifier:
``platform`` never imports a concrete harness — not at runtime and not under
``TYPE_CHECKING``; harnesses import ``platform`` (spec §4.2 in
docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md).
Where the machinery needs an instance's SHAPE it states a Protocol of its own
(``composed_harness.ComposedHarnessSettings``) rather than name the type.
"""
