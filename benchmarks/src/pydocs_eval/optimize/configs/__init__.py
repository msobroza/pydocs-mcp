"""Shipped optimize run-config YAMLs (spec §D7).

``optimize_tool_docs.yaml`` + ``optimize_usage_skill.yaml`` are the canonical
run configs; the package exists so ``importlib.resources.files`` resolves them
in a built install, not just from the PYTHONPATH source tree.
"""
