"""IOS-specific models."""

from ipaddress import IPv4Address
from typing import Literal

from pydantic import Field

from netsdk.core import VLAN, Facts, Interface, MacAddress, NetSDKModel

VTPMode = Literal["server", "primary server", "client", "transparent", "off"]


class IOSFacts(Facts):
    """Facts plus what IOS `show version` adds. Extras are None when absent."""

    #: Image family in parentheses on the version line, e.g. "C2960X-UNIVERSALK9-M".
    software_image: str | None = None
    #: Booted image path without the filesystem prefix, e.g. "/c2960x-....bin".
    running_image: str | None = None
    config_register: str | None = None
    reload_reason: str | None = None


class IOSInterface(Interface):
    """Interface plus what IOS `show interfaces` adds. Extras are None when absent."""

    #: State as printed, e.g. "up", "down", "administratively down", "reset".
    link_status: str
    #: Line protocol as printed, e.g. "up (connected)", "down (err-disabled)".
    protocol_status: str
    #: e.g. "Gigabit Ethernet", "EtherSVI", "Loopback".
    hardware_type: str | None = None
    #: e.g. "10/100/1000BaseTX", "Not Present".
    media_type: str | None = None
    burned_in_address: MacAddress | None = None
    #: Configured bandwidth (`BW`) in kbit/s; the routing-metric figure, not link speed.
    bandwidth: int | None = None


class IOSVLAN(VLAN):
    """VLAN plus its IOS status string."""

    #: As printed: "active", "act/lshut", "act/unsup", "suspended", ...
    status: str


class IOSVTPStatus(NetSDKModel):
    """VTP state from `show vtp status`, VLAN feature only (VTP v3 MST is skipped).

    Cisco-only, so it has no core model. Optional fields are None when this
    VTP version or IOS release doesn't print them. The MD5 digest is left out:
    Genie returns its bytes sorted, which makes it useless.
    """

    #: VTP version running.
    version: int = Field(ge=1, le=3)
    #: Highest version the device supports ("1 to 3", "VTP2 capable").
    max_version: int | None = Field(default=None, ge=1, le=3)
    #: None when no domain is set (printed blank or as "<>").
    domain_name: str | None = None
    mode: VTPMode
    configuration_revision: int = Field(ge=0)
    #: "Number of existing VLANs"; extended VLANs are counted separately on v3.
    vlan_count: int = Field(ge=0)
    extended_vlan_count: int | None = Field(default=None, ge=0)
    #: "Maximum VLANs supported locally"; v3 clients don't print it.
    max_vlans: int | None = Field(default=None, ge=0)
    pruning_enabled: bool
    traps_enabled: bool
    #: v3: this device's VTP ID.
    device_id: MacAddress | None = None
    #: v3: the primary server of the VLAN database, as printed.
    primary_id: MacAddress | None = None
    primary_description: str | None = None
    #: v1/v2: who last changed the VLAN database; None when printed as 0.0.0.0.
    last_modified_by: IPv4Address | None = None
    #: As printed, e.g. "12-5-17 09:35:46": the date order varies and there is
    #: no timezone, so it is not converted (D28).
    last_modified_at: str | None = None
    #: v1/v2 server: the address this device sends updates from, and its interface.
    updater_id: IPv4Address | None = None
    updater_interface: str | None = None
