# widgetlib — trajectory fixture corpus

A deliberately tiny Python package used as the rollout workspace for Phase 2
trajectory-attribution fixtures. Four single-responsibility modules, each with
one planted bug an edit task asks a rollout to fix:

| Module           | Public surface                     | Planted bug (buggy state)                          |
| ---------------- | ---------------------------------- | -------------------------------------------------- |
| `calculator.py`  | `add`, `multiply`, `average`       | `average` divides by `len(values) + 1` (off-by-one)|
| `inventory.py`   | `Inventory`                        | `total_value` sums quantities, ignoring unit price |
| `textutil.py`    | `slugify`, `truncate`              | `slugify` never lowercases the result              |
| `pricing.py`     | `apply_discount`, `with_tax`       | `apply_discount` charges `price * pct`, not `* (1 - pct)` |

Tests live under `tests/`. In this shipped (buggy) state each task's
FAIL_TO_PASS tests fail and every PASS_TO_PASS test passes; applying the task's
gold patch flips the FAIL_TO_PASS tests to passing while the PASS_TO_PASS tests
stay green. This is asserted by
`benchmarks/tests/trajectory/test_fixture_corpus.py`.

Run the tests directly:

```bash
python -m pytest .            # from this directory (workspace root)
```

No third-party dependencies — stdlib plus `pytest` only.
