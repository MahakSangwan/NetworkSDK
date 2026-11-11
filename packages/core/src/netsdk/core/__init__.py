"""Core building blocks shared by netsdk device packages."""

from netsdk.core.capabilities import (
    SupportsFacts,
    SupportsInterfaces,
    SupportsInventory,
    SupportsIPAddresses,
    SupportsNeighbors,
    SupportsSwitchports,
    SupportsVLANs,
)
from netsdk.core.credentials import Credentials
from netsdk.core.device import Device
from netsdk.core.exceptions import (
    AuthenticationError,
    CommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
    HostKeyError,
    NetSDKError,
    NotSupportedError,
    ParseError,
)
from netsdk.core.formats import Duplex, MacAddress
from netsdk.core.models import (
    VLAN,
    Facts,
    Interface,
    InventoryItem,
    IPAddress,
    Neighbor,
    NeighborProtocol,
    NetSDKModel,
    Switchport,
)
from netsdk.core.transports import Transport

__version__ = "0.1.0"

__all__ = [
    "VLAN",
    "AuthenticationError",
    "CommandError",
    "Credentials",
    "Device",
    "DeviceConnectionError",
    "DeviceTimeoutError",
    "Duplex",
    "Facts",
    "HostKeyError",
    "IPAddress",
    "Interface",
    "InventoryItem",
    "MacAddress",
    "Neighbor",
    "NeighborProtocol",
    "NetSDKError",
    "NetSDKModel",
    "NotSupportedError",
    "ParseError",
    "SupportsFacts",
    "SupportsIPAddresses",
    "SupportsInterfaces",
    "SupportsInventory",
    "SupportsNeighbors",
    "SupportsSwitchports",
    "SupportsVLANs",
    "Switchport",
    "Transport",
]
