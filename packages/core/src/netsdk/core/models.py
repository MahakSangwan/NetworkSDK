"""Models shared by all vendors.

Only truly cross-vendor concepts live here; device packages subclass them to
add what their platform reports. Models are frozen (D8): a getter's result is
a snapshot, not something to edit.
"""

from datetime import timedelta
from ipaddress import IPv4Interface, IPv6Interface

from pydantic import BaseModel, ConfigDict, Field

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
