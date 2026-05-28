"""TurboQuantUnitOfWork lifecycle + integrity (AC-5..AC-8)."""
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

# turbovec requires dim to be a multiple of 8 (panics otherwise) — see
# turbovec/src/lib.rs `dim must be a multiple of 8` assertion.
_DIM = 8


def _vec(*values: float) -> np.ndarray:
    """Pad/truncate ``values`` to a ``_DIM``-wide float32 vector."""
    padded = list(values) + [0.0] * max(0, _DIM - len(values))
    return np.asarray(padded[:_DIM], dtype=np.float32)


async def test_add_then_commit_persists(tmp_path: Path) -> None:
    tq = tmp_path / "test.tq"
    async with TurboQuantUnitOfWork(index_path=tq, dim=_DIM, bit_width=4) as uow:
        await uow.add_vectors(
            [1, 2, 3],
            [_vec(0.1, 0.2, 0.3, 0.4) for _ in range(3)],
        )
        await uow.commit()
    assert tq.exists()
    async with TurboQuantUnitOfWork(index_path=tq, dim=_DIM, bit_width=4) as uow2:
        assert uow2.size() == 3


async def test_rollback_discards_in_memory_adds(tmp_path: Path) -> None:
    tq = tmp_path / "test.tq"
    async with TurboQuantUnitOfWork(index_path=tq, dim=_DIM, bit_width=4) as uow:
        await uow.add_vectors([10, 11], [_vec(0, 0, 0, 0) for _ in range(2)])
        await uow.commit()
    async with TurboQuantUnitOfWork(index_path=tq, dim=_DIM, bit_width=4) as uow:
        await uow.add_vectors(
            [12, 13, 14], [_vec(1, 0, 0, 0) for _ in range(3)],
        )
        await uow.rollback()
        assert uow.size() == 2


async def test_multi_vector_input_raises_notimplementederror(tmp_path: Path) -> None:
    tq = tmp_path / "test.tq"
    multi: list[np.ndarray] = [
        _vec(0.1, 0.2, 0.3, 0.4), _vec(0.5, 0.6, 0.7, 0.8),
    ]
    async with TurboQuantUnitOfWork(index_path=tq, dim=_DIM, bit_width=4) as uow:
        with pytest.raises(NotImplementedError, match="chunk_vectors"):
            await uow.add_vectors([1], [multi])


class _BoomingIndex:
    """Test double whose ``write`` raises — proxies anything else to the real index.

    PyO3 extension types reject attribute monkeypatching (both at the
    class level and on bound methods: ``AttributeError: 'builtins.
    IdMapIndex' object attribute 'write' is read-only``). The UoW's
    ``_index`` slot, however, is a plain Python attribute — swapping
    the whole reference is the supported way to inject failure.
    """

    def __init__(self, real: object) -> None:
        self._real = real

    def write(self, path: str) -> None:
        raise OSError("disk full")

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    def __len__(self) -> int:
        return len(self._real)


async def test_write_is_atomic_via_tmp_rename(tmp_path: Path) -> None:
    """Simulate index.write raising mid-write; on-disk file unchanged."""
    tq = tmp_path / "test.tq"
    async with TurboQuantUnitOfWork(index_path=tq, dim=_DIM, bit_width=4) as uow:
        await uow.add_vectors([1], [_vec(0, 0, 0, 0)])
        await uow.commit()
    pre_bytes = tq.read_bytes()
    async with TurboQuantUnitOfWork(index_path=tq, dim=_DIM, bit_width=4) as uow:
        await uow.add_vectors([2], [_vec(1, 1, 1, 1)])
        # Swap the UoW's index reference for a failing proxy. See
        # ``_BoomingIndex`` for why monkeypatching the PyO3 class
        # or bound method doesn't work.
        uow._index = _BoomingIndex(uow._index)  # type: ignore[assignment]
        with pytest.raises(OSError, match="disk full"):
            await uow.commit()
    # Original file is byte-identical — the failed write went to <path>.tmp,
    # never replaced the target.
    assert tq.read_bytes() == pre_bytes
