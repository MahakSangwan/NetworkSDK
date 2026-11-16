"""NX-OS models: core models plus what NX-OS reports on top."""

from netsdk.core import VLAN, Facts, Interface, MacAddress, Switchport


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
