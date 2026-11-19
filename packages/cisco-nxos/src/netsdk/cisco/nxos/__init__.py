"""netsdk SDK for Cisco NX-OS (standalone Nexus switches; not ACI)."""

from netsdk.cisco.nxos.device import CiscoNXOS
from netsdk.cisco.nxos.models import (
    NXOSVLAN,
    NXOSVPC,
    NXOSFacts,
    NXOSInterface,
    NXOSNeighbor,
    NXOSPortChannel,
    NXOSPortChannelMember,
    NXOSSwitchport,
    NXOSVPCPeerLink,
    NXOSVPCStatus,
    NXOSVTPStatus,
)
from netsdk.cisco.nxos.transport import NXOSSSHTransport

__version__ = "0.1.0"

__all__ = [
    "NXOSVLAN",
    "NXOSVPC",
    "CiscoNXOS",
    "NXOSFacts",
    "NXOSInterface",
    "NXOSNeighbor",
    "NXOSPortChannel",
    "NXOSPortChannelMember",
    "NXOSSSHTransport",
    "NXOSSwitchport",
    "NXOSVPCPeerLink",
    "NXOSVPCStatus",
    "NXOSVTPStatus",
]
