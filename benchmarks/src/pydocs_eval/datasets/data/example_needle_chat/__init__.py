"""importlib.resources-addressable data package for ``example-needle-chat``.

``records.jsonl`` holds one line per question: the verbatim query, the pinned
repository and commit, the gold sites authored from the code at that commit, and
the metadata the loader validates (``datasets/example_needle_chat.py``). It is
curated by hand — no build tool writes it — and the owner ratifies every gold
record (``metadata.gold_ratified``). The repository is the owner's own, so no
third-party NOTICE ships here.

Two authoring records sit beside it and are not package data (the wheel ships
``records.jsonl`` only): ``authoring_prompt.md``, the complete instruction the
agents that drafted the held-out questions and wrote their gold received, and
``held_out_gold_accounting.jsonl``, one line per held-out record with what each
gold site contributes and where every non-test grep hit of its identifiers went.
The ``reserved`` membership is the one-time draw of
``datasets/example_needle_chat_reserved.py``, stored as each record's literal
split.
"""
