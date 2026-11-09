"""Capability protocols: what a device can be asked for.

Consumers check support with `isinstance(device, SupportsFacts)` instead of
knowing every device class. Each getter has a `_raw()` twin (D4) returning
the unparsed output keyed by the command or endpoint it came from.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from netsdk.core.models import Facts, Interface


@runtime_checkable
class SupportsFacts(Protocol):
    def get_facts(self) -> Facts: ...

    def get_facts_raw(self) -> dict[str, str]: ...


@runtime_checkable
class SupportsInterfaces(Protocol):
    def get_interfaces(self) -> Sequence[Interface]: ...

    def get_interfaces_raw(self) -> dict[str, str]: ...
