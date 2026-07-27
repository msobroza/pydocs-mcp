"""Shipped optimize run-config YAMLs (spec §D7).

``optimize_tool_docs.yaml`` + ``optimize_usage_skill.yaml`` are the canonical
run configs; ``optimize_search_skill.yaml`` is the canonical example of the
``arms:`` block over the ``search_skill`` family (run-contract design §6). The
package exists so ``importlib.resources.files`` resolves them in a built
install, not just from the PYTHONPATH source tree.
"""
