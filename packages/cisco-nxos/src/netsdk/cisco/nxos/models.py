"""NX-OS models: core models plus what NX-OS reports on top."""

from netsdk.core import Facts, Interface, MacAddress


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
