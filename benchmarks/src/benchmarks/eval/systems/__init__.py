"""Concrete system plug-ins (spec §4.5, §4.10). Importing this package
fires the ``@system_registry.register`` decorators on every concrete
system so the runner can look them up by name."""

from __future__ import annotations

from .base_system import System
from .context7 import Context7System
from .neuledge import NeuledgeSystem
from .pydocs import (
    PydocsMcpCompositeSystem,
    PydocsMcpSystem,
    PydocsTreeOnlySystem,
    PydocsTreeParallelSystem,
)
from .pydocs_oracle import PydocsOracleSystem

__all__ = [
    "Context7System",
    "NeuledgeSystem",
    "PydocsMcpCompositeSystem",
    "PydocsMcpSystem",
    "PydocsOracleSystem",
    "PydocsTreeOnlySystem",
    "PydocsTreeParallelSystem",
    "System",
]
