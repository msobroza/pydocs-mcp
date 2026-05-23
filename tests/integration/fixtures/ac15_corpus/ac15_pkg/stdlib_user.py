"""Uses stdlib. Exercises Rule B against the bundled stdlib qname universe."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path


def hash_text(text: str) -> str:
    """Calls hashlib.sha256 (Rule B against stdlib bundle)."""
    return hashlib.sha256(text.encode()).hexdigest()


def parse_json(payload: str) -> dict:
    """Calls json.loads (Rule B)."""
    return json.loads(payload)


async def io_helper(path: str) -> str:
    """Calls asyncio.to_thread + Path operations (Rule B against stdlib)."""
    return await asyncio.to_thread(Path(path).read_text)
