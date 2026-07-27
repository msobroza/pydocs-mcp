"""Shipped optimize run-config YAMLs (spec §D7).

``optimize_tool_docs.yaml`` + ``optimize_usage_skill.yaml`` are the canonical
run configs; ``optimize_search_skill.yaml`` is the canonical example of the
``arms:`` block over the ``search_skill`` family (run-contract design §6), and
``optimize_search_skill_repo_qa.yaml`` is the multi-framing example: two arms
over two corpora sharing one ``task_name`` while binding two different named
rubric objectives (design §5's first second framing). The
package exists so ``importlib.resources.files`` resolves them in a built
install, not just from the PYTHONPATH source tree.
"""
