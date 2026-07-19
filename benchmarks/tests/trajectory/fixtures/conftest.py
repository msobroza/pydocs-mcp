# Fixtures are DATA, not part of the benchmarks suite. The corpus under
# ``corpus/src/tests/`` ships ``test_*.py`` files that are the rollout's
# FAIL_TO_PASS / PASS_TO_PASS targets — intentionally failing in the buggy base
# state — so the harness pytest must never collect them. They are exercised only
# via subprocess by ``test_fixture_corpus.py``. Ignoring every direct child of
# ``fixtures/`` stops recursion into both ``corpus/`` and ``trajectories/``
# (neither holds any real suite test).
collect_ignore_glob = ["*"]
