"""LLM-visible project-tree prompt building blocks.

Pure functions extracted from the ``llm_tree_reasoning`` step (which had
grown to 782 lines against the <500 rule): docstring excerpting
(:mod:`.doc_excerpt`), PageIndex-shape serialization
(:mod:`.pageindex_serializer`), and token-budget fitting
(:mod:`.tree_budget_fitter`). Nothing here touches ``RetrieverState`` —
every function is unit-testable in isolation.
"""
