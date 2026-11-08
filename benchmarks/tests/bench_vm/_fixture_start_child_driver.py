"""Run ``start_child`` in a process of its own, under a constraint the test process must not take on.

``python _fixture_start_child_driver.py <mode> <log path> '<json script>'``
starts ``_fixture_output_script`` through ``start_child`` with its log at the
path, waits, and prints ``{"exit_code": …}`` on stdout; the supervisor's
events go to its stderr. The modes:

- ``full-disk``: files cannot grow past ``LOG_SIZE_CAP`` bytes
  (``RLIMIT_FSIZE``; the interpreter ignores ``SIGXFSZ``, so a write past the
  cap fails with ``EFBIG``, as one on a full disk fails with ``ENOSPC``): a
  log that refuses writes;
- ``many-fds``: ``HELD_FDS`` descriptors are open first, so the output pipe's
  descriptor is above ``select``'s ``FD_SETSIZE`` (1024), as in a supervisor
  holding many open files.
"""

from __future__ import annotations

import json
import os
import resource
import sys
from pathlib import Path

from pydocs_eval.bench_vm.child_process import start_child

LOG_SIZE_CAP = 4096
HELD_FDS = 1100
_SCRIPT = Path(__file__).with_name("_fixture_output_script.py")


def _apply(mode: str) -> list[int]:
    if mode == "full-disk":
        _soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        resource.setrlimit(resource.RLIMIT_FSIZE, (LOG_SIZE_CAP, hard))
        return []
    if mode == "many-fds":
        return [os.open(os.devnull, os.O_RDONLY) for _ in range(HELD_FDS)]
    raise ValueError(f"invalid mode: got {mode!r}, expected 'full-disk' or 'many-fds'")


def main() -> int:
    mode, log_path, script = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    held = _apply(mode)
    child = start_child(
        [sys.executable, str(_SCRIPT), script],
        cwd=log_path.parent,
        env={"PATH": "/usr/bin:/bin"},
        log_path=log_path,
        provider_keys={},
    )
    print(json.dumps({"exit_code": child.wait(120.0)}), flush=True)
    for fd in held:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
