"""IOS-specific models."""

from netsdk.core import VLAN, Facts, Interface, MacAddress


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
