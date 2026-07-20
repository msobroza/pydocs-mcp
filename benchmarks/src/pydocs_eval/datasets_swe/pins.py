"""Pinned SWE-bench snapshot manifests for the Phase 3 baseline campaigns (ADR 0013).

Every dataset fact a campaign consumes is pinned by a content-addressed HuggingFace
commit SHA so a re-run months from now loads byte-identical corpora (ADR 0013 §Decision;
requirement R5 — every pin lands in the campaign lockfile). Both pins + the conan dedupe
rule are exported through :func:`pin_metadata` for the ADR 0016 campaign lockfile.

These are constants, not knobs: bumping a pin is a deliberate edit that MUST re-run the
overlap check (a future Pro public release could add repos — datasets-overlap §Open items)
and re-hash the splits.
"""

from __future__ import annotations

from dataclasses import dataclass

# WHY these exact SHAs: Phase 3 evidence (datasets-overlap §1, §3), fetched 2026-07-20.
# The Live cadence stalled at 2025-09-18, so `main` is de-facto frozen — pinning its SHA
# cannot drift under a re-index. Pro is ungated/public @ its lastModified SHA.
LIVE_DATASET_ID = "SWE-bench-Live/SWE-bench-Live"
LIVE_REVISION = "a637bd46829f3132e12938c8a0ca93173a977b8e"
LIVE_SPLIT = "full"

PRO_DATASET_ID = "ScaleAI/SWE-bench_Pro"
PRO_REVISION = "7ab5114912baf22bb098818e604c02fe7ad2c11f"
PRO_SPLIT = "test"

# The Live `full` split is sharded into two parquet files under data/ at the pinned
# revision (datasets-overlap §1, tree listing). Pro is a single parquet.
LIVE_PARQUET_FILES: tuple[str, ...] = (
    "data/full-00000-of-00002.parquet",
    "data/full-00001-of-00002.parquet",
)
PRO_PARQUET_FILES: tuple[str, ...] = ("data/test-00000-of-00001.parquet",)

# Dedupe rule (committed alongside the pins, ADR 0013 §Decision): the Live `full` split
# is 1888 rows / 1887 distinct instance_id — one known duplicate. Drop the SECOND
# occurrence, yielding 1887 working instances.
LIVE_DUPLICATE_INSTANCE_ID = "conan-io__conan-18153"

# Measured full-split shape (datasets-overlap §1) — the numbers the build asserts against.
LIVE_RAW_ROWS = 1888
LIVE_DISTINCT_ROWS = 1887
LIVE_REPOS = 223

# Pro public Python surface (datasets-overlap §3): the ENTIRE public Python surface is
# 266 instances across exactly 3 repos. `repo_language == "python"` selects them.
PRO_PYTHON_LANGUAGE = "python"
PRO_PYTHON_INSTANCES = 266
PRO_PYTHON_REPOS: tuple[str, ...] = (
    "ansible/ansible",
    "internetarchive/openlibrary",
    "qutebrowser/qutebrowser",
)


@dataclass(frozen=True, slots=True)
class DatasetPin:
    """One content-addressed HF snapshot pin, lockfile-serializable."""

    dataset_id: str
    revision: str
    split: str
    parquet_files: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "revision": self.revision,
            "split": self.split,
            "parquet_files": list(self.parquet_files),
        }


LIVE_PIN = DatasetPin(LIVE_DATASET_ID, LIVE_REVISION, LIVE_SPLIT, LIVE_PARQUET_FILES)
PRO_PIN = DatasetPin(PRO_DATASET_ID, PRO_REVISION, PRO_SPLIT, PRO_PARQUET_FILES)


def pin_metadata() -> dict[str, object]:
    """Return the lockfile-consumable pin block (ADR 0013 action item 1, R5).

    The ADR 0016 campaign lockfile embeds this verbatim so every campaign records
    exactly which snapshots + dedupe rule produced its corpora.
    """
    return {
        "dev_val": LIVE_PIN.to_dict(),
        "frozen_test": {
            **PRO_PIN.to_dict(),
            "python_repos": list(PRO_PYTHON_REPOS),
            "python_instances": PRO_PYTHON_INSTANCES,
        },
        "dedupe": {
            "rule": "drop_second_occurrence",
            "instance_id": LIVE_DUPLICATE_INSTANCE_ID,
            "raw_rows": LIVE_RAW_ROWS,
            "working_rows": LIVE_DISTINCT_ROWS,
        },
    }
