"""Shipped trajectory derived-layer config (ADR 0012).

``score_weights.yaml`` (shaped-score weights + ``score_version``) and
``taxonomy.yaml`` (test-runner patterns + ``taxonomy_version``) ship as package
data so ``importlib.resources.files`` resolves them in a built install, not only
from the source tree.
"""
