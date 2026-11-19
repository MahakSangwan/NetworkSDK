"""NX-OS models: core models plus what NX-OS reports on top."""

from ipaddress import IPv4Address, IPv6Address
from typing import Literal

from pydantic import Field

from netsdk.core import (
    LAG,
    VLAN,
    Duplex,
    Facts,
    Interface,
    LAGMember,
    MacAddress,
    Neighbor,
    NetSDKModel,
    Switchport,
)


class NXOSFacts(Facts):
    """Facts plus what NX-OS `show version` adds. Extras are None when absent.

    `model` and `serial_number` are the chassis part ID and serial from
    `show inventory`; `show version` only has a description and the board ID.
    """

    #: As `show version` describes the chassis, e.g. "Nexus9000 C93180YC-EX chassis".
    chassis_description: str | None = None
    #: Booted system image as printed, e.g. "bootflash:///nxos64-cs.10.4.5.M.bin".
    running_image: str | None = None
    bios_version: str | None = None
    reload_reason: str | None = None


class NXOSInterface(Interface):
    """Interface plus what NX-OS `show interface` adds. Extras are None when absent."""

    #: Why the interface is down, as printed: "Link not connected",
    #: "XCVR not inserted", "Administratively down". None when up.
    state_reason: str | None = None
    #: e.g. "100/1000/10000 Ethernet", "Port-Channel"; None on SVIs.
    hardware_type: str | None = None
    burned_in_address: MacAddress | None = None
    #: Configured bandwidth (`BW`) in kbit/s; the routing-metric figure, not link speed.
    bandwidth: int | None = None
    #: As printed: "access", "trunk", "routed", "fex-fabric", ...; None on SVIs.
    port_mode: str | None = None


class NXOSVLAN(VLAN):
    """VLAN plus its NX-OS state strings.

    `interfaces` are access ports only (D41): NX-OS lists trunk ports under
    each VLAN too, which are filtered out with `show interface switchport`.
    """

    #: As printed: "active", "suspend", ...
    status: str
    #: As printed: "noshutdown", "shutdown"; None when not printed.
    shut_state: str | None = None


class NXOSSwitchport(Switchport):
    """Switchport plus what NX-OS `show interface switchport` adds."""

    #: Operational mode as printed: "access", "trunk", "fex-fabric", "down", ...
    #: NX-OS prints no administrative mode.
    oper_mode: str | None = None


VTPMode = Literal["server", "client", "transparent", "off"]


class NXOSVTPStatus(NetSDKModel):
    """VTP state from NX-OS `show vtp status` (D43).

    Cisco-only, so it has no core model; field names follow `IOSVTPStatus` in
    the IOS package where the meaning is the same.
    """

    #: VTP version running (`running-version`).
    version: int = Field(ge=1, le=3)
    #: VTP version configured (`version`).
    configured_version: int | None = Field(default=None, ge=1, le=3)
    #: None when no domain is set.
    domain_name: str | None = None
    mode: VTPMode
    configuration_revision: int = Field(ge=0)
    #: Number of existing VLANs.
    vlan_count: int = Field(ge=0)
    #: Maximum VLANs supported locally.
    max_vlans: int | None = Field(default=None, ge=0)
    pruning_enabled: bool
    #: Pruning in effect (`oper_pruning_mode`).
    oper_pruning_enabled: bool | None = None
    traps_enabled: bool
    v2_mode_enabled: bool | None = None
    #: As printed, e.g. "0x9A 0x3C ...": usable to compare switches in a domain.
    md5_digest: str | None = None
    #: Who last changed the VLAN database; None when printed as 0.0.0.0.
    last_modified_by: IPv4Address | None = None
    #: As printed: no timezone, so not converted (D28).
    last_modified_at: str | None = None
    #: The address this switch sends updates from; None when 0.0.0.0.
    updater_id: IPv4Address | None = None
    #: As printed, e.g. why no updater address was chosen.
    updater_reason: str | None = None
    #: The interface updates are sent from (7.3 prints it instead of an address).
    updater_interface: str | None = None


class NXOSNeighbor(Neighbor):
    """Neighbor plus what NX-OS CDP/LLDP detail output adds (D44)."""

    #: CDP addresses the packet came from (`v4addr`).
    entry_addresses: tuple[IPv4Address | IPv6Address, ...] = ()
    #: CDP native VLAN, or LLDP port VLAN ID, as the neighbor advertises it.
    native_vlan: int | None = None
    #: CDP only.
    duplex: Duplex | None = None
    #: CDP `sysname`, next to the device ID NX-OS neighbors put a serial in.
    remote_system_name: str | None = None


class NXOSPortChannelMember(LAGMember):
    """Port-channel member plus its NX-OS flag (D46)."""

    #: As printed: "P" up in port-channel, "D" down, "s" suspended,
    #: "I" individual, "H" hot-standby, "r" module removed, ...
    status: str


class NXOSPortChannel(LAG):
    """Port-channel plus what NX-OS `show port-channel summary` adds (D46)."""

    #: Channel-group number.
    group: int = Field(ge=0)
    #: "switched" or "routed" (`S`/`R`); None when printed otherwise.
    layer: Literal["switched", "routed"] | None = None
    #: Status flags as printed, e.g. "U", "D", "SU".
    status: str
    #: Protocol as printed, e.g. "LACP", "NONE".
    protocol_raw: str | None = None
    members: tuple[NXOSPortChannelMember, ...] = ()


class NXOSVPC(NetSDKModel):
    """One vPC: its ID, port-channel and health (D47)."""

    vpc_id: int = Field(ge=0)
    #: Full name, joining `Interface.name`.
    port_channel: str = Field(min_length=1)
    up: bool
    #: None when NX-OS didn't check ("not-applicable").
    consistent: bool | None = None
    #: As printed: "consistent", "inconsistent", "not-applicable".
    consistency: str | None = None
    #: As printed: "SUCCESS" or why the check failed or wasn't done.
    consistency_reason: str | None = None


class NXOSVPCPeerLink(NetSDKModel):
    """The vPC peer-link."""

    #: Full name, joining `Interface.name`.
    port_channel: str = Field(min_length=1)
    link_id: int | None = None
    up: bool


class NXOSVPCStatus(NetSDKModel):
    """vPC domain from NX-OS `show vpc` (D47): configuration plus health flags.

    Cisco-only, so it has no core model. Each flag keeps its raw string; the
    VLAN up-bitsets are left out (operational detail).
    """

    domain_id: int = Field(ge=0)
    #: As printed: "primary", "secondary", "primary-secondary" (operational
    #: primary, configured secondary), ...
    role: str
    #: True only for "peer-ok".
    peer_ok: bool
    peer_status: str
    peer_status_reason: str | None = None
    #: True only for "peer-alive".
    keepalive_alive: bool
    keepalive_status: str
    #: Type-1 (global) consistency; None when not checked.
    peer_consistent: bool | None = None
    peer_consistency_reason: str | None = None
    type2_consistent: bool | None = None
    type2_consistency_reason: str | None = None
    per_vlan_consistent: bool | None = None
    graceful_consistency_check: bool | None = None
    #: As printed: "Disabled", or a phrase with timers on some releases.
    auto_recovery: str | None = None
    #: As printed: a number or a word, depending on the release.
    peer_gateway: str | None = None
    peer_link: NXOSVPCPeerLink | None = None
    vpcs: tuple[NXOSVPC, ...] = ()
