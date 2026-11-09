"""Transport contract and optional base transports.

The base transports live in submodules so their dependencies stay optional:
`netsdk.core.transports.ssh` needs `netsdk-core[ssh]`.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Transport(Protocol):
    """What every transport provides, whether from core or a device package."""

    @property
    def is_open(self) -> bool: ...

    def open(self) -> None: ...

    def close(self) -> None: ...


__all__ = ["Transport"]
