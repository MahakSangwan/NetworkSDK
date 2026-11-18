"""netsdk SDK for Cisco NX-OS (standalone Nexus switches; not ACI)."""

from netsdk.cisco.nxos.device import CiscoNXOS
from netsdk.cisco.nxos.models import (
    NXOSVLAN,
    NXOSFacts,
    NXOSInterface,
    NXOSSwitchport,
    NXOSVTPStatus,
)
from netsdk.cisco.nxos.transport import NXOSSSHTransport

__version__ = "0.1.0"

__all__ = [
    "NXOSVLAN",
    "CiscoNXOS",
    "NXOSFacts",
    "NXOSInterface",
    "NXOSSSHTransport",
    "NXOSSwitchport",
    "NXOSVTPStatus",
]
