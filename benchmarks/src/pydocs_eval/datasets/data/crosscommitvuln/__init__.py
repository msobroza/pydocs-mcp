"""importlib.resources-addressable data package (design §6.2).

The vendored ``records.jsonl`` / ``banned_tokens.jsonl`` in this directory are
PRODUCED (and overwritten) by the one-time network build tool
``benchmarks/tools/build_crosscommitvuln.py`` (design §6.3), then committed.
They ship EMPTY as a placeholder until that gated run has cloned the upstream
repos and pinned each pre-fix snapshot; see the sibling ``NOTICE`` for the CC BY
4.0 attribution the packaged data carries.
"""
