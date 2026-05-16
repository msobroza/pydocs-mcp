"""Shipped pipeline YAML blueprints.

Contains ``chunk_search.yaml`` (BM25 over chunks), ``member_search.yaml``
(SQL LIKE over module members), and ``ingestion.yaml`` (write-side
extraction pipeline). Looked up via ``importlib.resources`` so the
paths are correct under wheel installs and zipimport.
"""
