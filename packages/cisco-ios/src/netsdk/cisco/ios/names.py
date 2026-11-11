"""Interface names: always the full IOS spelling (D29).

Some commands abbreviate (`show vlan brief` prints "Gi1/0/1"); everything we
return uses the name `show interfaces` prints ("GigabitEthernet1/0/1") so
results from different getters join. The expansion table is netutils'.
"""

import logging

from netutils.interface import canonical_interface_name

log = logging.getLogger(__name__)

#: Where IOS spells a full name differently from netutils' canonical one
#: (netutils: "VLAN", "HundredGigabitEthernet"; it doesn't know "Wl", the
#: embedded access point port on ISRs).
_IOS_NAMES = {
    "Vl": "Vlan",
    "Vlan": "Vlan",
    "VLAN": "Vlan",
    "Hu": "HundredGigE",
    "HundredGigE": "HundredGigE",
    "Wl": "Wlan-GigabitEthernet",
    "Wlan-GigabitEthernet": "Wlan-GigabitEthernet",
}


def full_interface_name(name: str) -> str:
    """Expand an IOS interface abbreviation; full names come back unchanged.

    An unknown prefix is logged and returned as printed, never guessed.
    """
    try:
        return canonical_interface_name(name, addl_name_map=_IOS_NAMES, verify=True)
    except ValueError:
        log.warning("unrecognised interface name %r; kept as printed", name)
        return name
