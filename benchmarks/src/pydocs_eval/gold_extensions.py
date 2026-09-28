"""The file types a needle's gold may name: ``.py``, ``.md`` and the product's text/config set.

Two readers need exactly this set and each used to spell its own copy: the
example-needle-chat corpus (``datasets.example_needle_chat.CORPUS_GLOBS``) must
hold every gold file, or the gold can never be retrieved, and the answer judge
(``judge.config.DEFAULT_CITATION_EXTENSIONS``) must extract a citation of every
gold file, or it can never be cited. One home keeps the two from drifting apart.

The text/config part is the product's ``extraction/config._TEXT_CONFIG_EXTENSIONS``
(ADR 0021 T2). The eval keeps a zero-``pydocs_mcp`` import floor, so the set is
mirrored here and pinned by ``benchmarks/tests/core/test_gold_file_extensions.py``
where the product is installed.

Leaf module: stdlib-only, no ``pydocs_eval`` imports. The order is the chat
corpus's, which materializes its files glob by glob.
"""

from __future__ import annotations

GOLD_FILE_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".txt",
    ".json",
    ".rst",
)

__all__ = ("GOLD_FILE_EXTENSIONS",)
