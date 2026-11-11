"""Models shared by all vendors.

Only truly cross-vendor concepts live here; device packages subclass them to
add what their platform reports. Models are frozen (D8): a getter's result is
a snapshot, not something to edit.
"""

from datetime import timedelta
from ipaddress import IPv4Address, IPv4Interface, IPv6Address, IPv6Interface
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from netsdk.core.formats import Duplex, MacAddress


class NetSDKModel(BaseModel):
    """Base for every netsdk model: immutable, and unknown fields are errors."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Facts(NetSDKModel):
    """Identity of one device (one chassis; stacks are not modelled, D26)."""

    hostname: str = Field(min_length=1)
    vendor: str = Field(min_length=1)
    model: str = Field(min_length=1)
    serial_number: str = Field(min_length=1)
    #: Exactly as the device prints it, e.g. "15.2(7)E4".
    os_version: str = Field(min_length=1)
    #: As the device reports it (minute precision on most platforms), not
    #: derived from clocks. None when the device doesn't say.
    uptime: timedelta | None = None


class Interface(NetSDKModel):
    """One interface as the device reports it. Values use netsdk.core.formats."""

    #: Full name as the device prints it, never abbreviated: "GigabitEthernet1/0/1".
    name: str = Field(min_length=1)
    description: str | None = None
    #: Administratively enabled (not shut down).
    enabled: bool
    #: Operationally up (line protocol up).
    oper_up: bool
    mac_address: MacAddress | None = None
    mtu: int | None = Field(default=None, gt=0)
    #: Speed the device reports, in kbit/s: negotiated, or configured on a port
    #: that is down. None when it only says auto.
    speed: int | None = Field(default=None, gt=0)
    duplex: Duplex | None = None


class IPAddress(NetSDKModel):
    """One address configured on one interface (an interface may have several).

    Unnumbered interfaces have no address of their own and produce no record;
    the address belongs to the interface it is configured on.
    """

    #: Same string as `Interface.name`, so the two join directly.
    interface: str = Field(min_length=1)
    #: Address with its prefix length, e.g. IPv4Interface("10.0.0.1/24").
    address: IPv4Interface | IPv6Interface
    #: An IOS-style secondary address; False for the primary.
    secondary: bool = False
    #: VRF name; None is the global (default) routing table.
    vrf: str | None = None


class VLAN(NetSDKModel):
    """One VLAN as the device reports it."""

    vlan_id: int = Field(ge=1, le=4094)
    name: str | None = None
    #: Usable: exists and is not shut down or suspended.
    active: bool
    #: Full names of the ports carrying it untagged (access ports), joining
    #: `Interface.name`. Trunk membership is a separate getter.
    interfaces: tuple[str, ...] = ()


class Switchport(NetSDKModel):
    """One layer-2 (switched) port. Routed ports have no record.

    Allowed VLANs are what the port's trunk configuration permits, reported
    whatever the current mode: `all_vlans_allowed` when the device says "all";
    otherwise `allowed_vlans` lists them, empty for "none" and None when the
    device doesn't print the list at all.
    """

    #: Full name, joining `Interface.name`.
    interface: str
    #: Operational mode; None when the port is down or in another mode
    #: (private VLAN, ...). The raw strings stay on the vendor model.
    mode: Literal["access", "trunk"] | None
    access_vlan: int | None = Field(default=None, ge=1, le=4094)
    native_vlan: int | None = Field(default=None, ge=1, le=4094)
    all_vlans_allowed: bool = False
    allowed_vlans: tuple[int, ...] | None = None

    @model_validator(mode="after")
    def _check_allowed_vlans(self) -> Self:
        if self.all_vlans_allowed and self.allowed_vlans is not None:
            raise ValueError("allowed_vlans must be None when all_vlans_allowed is set")
        if self.allowed_vlans is not None:
            if any(not 1 <= v <= 4094 for v in self.allowed_vlans):
                raise ValueError("allowed VLAN IDs must be 1-4094")
            if list(self.allowed_vlans) != sorted(set(self.allowed_vlans)):
                raise ValueError("allowed_vlans must be sorted and unique")
        return self


NeighborProtocol = Literal["cdp", "lldp"]


class Neighbor(NetSDKModel):
    """One neighbor seen on one local port, by one discovery protocol.

    Remote values are as the neighbor advertised them: names, ports and
    chassis IDs are not expanded or normalized, since we don't know the
    neighbor's OS. A neighbor seen by both CDP and LLDP gives two records.
    """

    protocol: NeighborProtocol
    #: Full local name, joining `Interface.name`. None only when the device
    #: doesn't print it (some older IOS releases in LLDP detail output).
    local_interface: str | None
    #: CDP device ID or LLDP system name; None when not advertised.
    remote_name: str | None = None
    #: CDP port ID or LLDP port ID.
    remote_port: str | None = None
    #: LLDP port description.
    remote_port_description: str | None = None
    #: LLDP chassis ID, as advertised (often, not always, a MAC address).
    remote_chassis_id: str | None = None
    #: CDP platform, e.g. "cisco WS-C3850-24T".
    remote_platform: str | None = None
    #: CDP software version text or LLDP system description.
    remote_description: str | None = None
    management_addresses: tuple[IPv4Address | IPv6Address, ...] = ()
    #: Lowercase names: CDP's words ("router", "switch", "igmp") or LLDP's
    #: codes expanded ("bridge", "router", "telephone", ...).
    capabilities: tuple[str, ...] = ()
