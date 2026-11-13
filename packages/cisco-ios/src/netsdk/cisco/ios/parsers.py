"""Turn IOS command output into models.

Parsing is done by Genie or ntc-templates, per command as D33 lists. This
layer maps their results into our models, enforces the fields a model can't
do without and rejects what we don't support; no whole-command parsing
happens here. Raised errors carry no host; the device adds it.
"""

import logging
import re
from datetime import timedelta
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Any, Literal

from netutils.vlan import vlanconfig_to_list
from pydantic import ValidationError

from netsdk.cisco.ios.commands import (
    SHOW_CDP_NEIGHBORS_DETAIL,
    SHOW_INTERFACES,
    SHOW_INTERFACES_SWITCHPORT,
    SHOW_INVENTORY,
    SHOW_IP_INTERFACE,
    SHOW_LLDP_NEIGHBORS_DETAIL,
    SHOW_VERSION,
    SHOW_VLAN_BRIEF,
    SHOW_VTP_STATUS,
)
from netsdk.cisco.ios.models import (
    IOSVLAN,
    IOSFacts,
    IOSInterface,
    IOSNeighbor,
    IOSSwitchport,
    IOSVTPStatus,
)
from netsdk.cisco.ios.names import full_interface_name
from netsdk.core import Duplex, InventoryItem, IPAddress, NotSupportedError, ParseError
from netsdk.core.formats import KBPS_PER_UNIT
from netsdk.core.parsing import parse_genie, parse_textfsm

log = logging.getLogger(__name__)

PLATFORM = "cisco_ios"
GENIE_OS = "ios"
VENDOR = "Cisco"

_UPTIME_UNITS = {
    # IOS counts uptime from its own timer; a year is taken as 365 days.
    "uptime_years": timedelta(days=365),
    "uptime_weeks": timedelta(weeks=1),
    "uptime_days": timedelta(days=1),
    "uptime_hours": timedelta(hours=1),
    "uptime_minutes": timedelta(minutes=1),
}


def parse_show_version(output: str) -> IOSFacts:
    rows = parse_textfsm(PLATFORM, SHOW_VERSION, output)
    if len(rows) != 1:
        raise ParseError(f"expected one {SHOW_VERSION!r} record, got {len(rows)}")
    row = rows[0]

    models: list[str] = row["hardware"]
    serials: list[str] = row["serial"]
    if len(models) > 1 or len(serials) > 1:
        raise NotSupportedError(
            f"{SHOW_VERSION!r} lists {max(len(models), len(serials))} chassis; "
            "switch stacks are not supported"
        )
    required = {
        "hostname": row["hostname"],
        "version": row["version"],
        "model": models,
        "serial number": serials,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ParseError(f"{SHOW_VERSION!r} output has no {', '.join(missing)}")

    return IOSFacts(
        hostname=row["hostname"],
        vendor=VENDOR,
        model=models[0],
        serial_number=serials[0],
        os_version=row["version"],
        uptime=_uptime(row),
        software_image=row["software_image"] or None,
        running_image=row["running_image"] or None,
        config_register=row["config_register"] or None,
        reload_reason=row["reload_reason"] or None,
    )


def _uptime(row: dict[str, Any]) -> timedelta | None:
    if not row["uptime"]:
        return None
    return sum(
        (int(row[field] or 0) * unit for field, unit in _UPTIME_UNITS.items()),
        timedelta(),
    )


# -- show interfaces ---------------------------------------------------------

# "100Mb/s", "1000Mbps", "10Gb/s"; "a-1000Mb/s" when auto-negotiated.
_SPEED = re.compile(r"(?:a-)?(\d+)\s*([kmgt])b(?:/s|ps)", re.IGNORECASE)
# The `BW 100000 Kbit` figure.
_BANDWIDTH = re.compile(r"(\d+)\s*([kmgt])bit", re.IGNORECASE)
_DUPLEX: dict[str, Duplex] = {"full": "full", "half": "half", "auto": "auto"}
_NOT_NEGOTIATED = {"", "auto-speed", "auto speed", "auto"}
_ADMIN_DOWN = {"administratively down", "deleted"}


def parse_show_interfaces(output: str) -> list[IOSInterface]:
    rows = parse_textfsm(PLATFORM, SHOW_INTERFACES, output)
    interfaces = [_interface(row) for row in rows]
    names = [interface.name for interface in interfaces]
    if len(set(names)) != len(names):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise ParseError(f"{SHOW_INTERFACES!r} lists {', '.join(duplicates)} more than once")
    return interfaces


def _interface(row: dict[str, Any]) -> IOSInterface:
    name = row["interface"]
    return IOSInterface(
        name=name,
        description=row["description"] or None,
        enabled=row["link_status"] not in _ADMIN_DOWN,
        oper_up=row["protocol_status"].startswith("up"),
        mac_address=row["mac_address"] or None,
        mtu=int(row["mtu"]) if row["mtu"] else None,
        speed=_speed(name, row["speed"]),
        duplex=_duplex(name, row["duplex"]),
        link_status=row["link_status"],
        protocol_status=row["protocol_status"],
        hardware_type=row["hardware_type"] or None,
        media_type=row["media_type"] or None,
        burned_in_address=row["bia"] or None,
        bandwidth=_kbps(_BANDWIDTH, row["bandwidth"]),
    )


def _kbps(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.fullmatch(text.strip())
    if match is None:
        return None
    return int(match[1]) * KBPS_PER_UNIT[match[2].lower()]


def _speed(name: str, text: str) -> int | None:
    if text.strip().lower() in _NOT_NEGOTIATED:
        return None
    kbps = _kbps(_SPEED, text)
    if kbps is None:
        # Unknown spellings are logged, not fatal: speed is optional and a
        # new release shouldn't break the whole getter.
        log.warning("%s: unrecognised speed %r", name, text)
    return kbps


def _duplex(name: str, text: str) -> Duplex | None:
    if not text:
        return None
    # "Full-duplex", "Full Duplex", "Auto-duplex", "a-full".
    word = re.sub(r"^a-", "", text.strip().lower()).split("-")[0].split()[0]
    duplex = _DUPLEX.get(word)
    if duplex is None:
        log.warning("%s: unrecognised duplex %r", name, text)
    return duplex


# -- show ip interface -------------------------------------------------------


#: Released ntc-templates reads a tunnel's transport VRF ("Tunnel VPN
#: Routing/Forwarding") as the interface VRF; delete with the file once a
#: release has networktocode/ntc-templates#2378 (D37).
_IP_INTERFACE_TEMPLATE = Path(__file__).parent / "templates" / "cisco_ios_show_ip_interface.textfsm"


def parse_show_ip_interface(output: str) -> list[IPAddress]:
    """One record per address. IOS lists the primary first, then secondaries."""
    addresses: list[IPAddress] = []
    for row in parse_textfsm(PLATFORM, SHOW_IP_INTERFACE, output, template=_IP_INTERFACE_TEMPLATE):
        name = row["interface"]
        for index, (ip, prefix) in enumerate(
            zip(row["ip_address"], row["prefix_length"], strict=True)
        ):
            # Check both halves: a bare "10.0.0.1" would otherwise become /32,
            # and the template splits it into "10.0.0." and "1".
            try:
                ip_address(ip)
                if not prefix:
                    raise ValueError("no prefix length")
                addresses.append(
                    IPAddress(
                        interface=name,
                        address=f"{ip}/{prefix}",
                        secondary=index > 0,
                        vrf=row["vrf"] or None,
                    )
                )
            except ValueError as exc:
                raise ParseError(
                    f"{name}: unreadable address {ip!r} prefix {prefix!r}; expected address/length"
                ) from exc
    return addresses


# -- show vlan brief ---------------------------------------------------------


def parse_show_vlan_brief(output: str) -> list[IOSVLAN]:
    # ntc-templates' `show vlan` template reads the first table and stops at
    # the second; `show vlan brief` prints only that first table.
    vlans = [
        IOSVLAN(
            vlan_id=int(row["vlan_id"]),
            name=row["vlan_name"] or None,
            active=row["status"] == "active",
            interfaces=tuple(full_interface_name(name) for name in row["interfaces"]),
            status=row["status"],
        )
        for row in parse_textfsm(PLATFORM, SHOW_VLAN_BRIEF, output)
    ]
    ids = [vlan.vlan_id for vlan in vlans]
    if len(set(ids)) != len(ids):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise ParseError(f"{SHOW_VLAN_BRIEF!r} lists VLAN {duplicates} more than once")
    return vlans


# -- show vtp status (Genie, D32, D33) ---------------------------------------

#: Printed for "nothing" in place of an address, a time or a domain name.
_NO_ADDRESS = "0.0.0.0"
_NO_TIME = "0-0-00 00:00:00"
_NO_DOMAIN = "<>"


def parse_show_vtp_status(output: str) -> IOSVTPStatus:
    vtp = parse_genie(GENIE_OS, SHOW_VTP_STATUS, output).get("vtp")
    if not isinstance(vtp, dict):
        raise ParseError(f"{SHOW_VTP_STATUS!r}: Genie returned no 'vtp' section")
    # VTP v3 nests the VLAN database under "feature"; v1/v2 print it flat.
    vlan = vtp.get("feature", {}).get("vlan", vtp)
    missing = [
        key
        for section, key in [
            (vtp, "version"),
            (vtp, "pruning_mode"),
            (vtp, "traps_generation"),
            (vlan, "operating_mode"),
            (vlan, "configuration_revision"),
            (vlan, "existing_vlans"),
        ]
        if key not in section
    ]
    if missing:
        raise ParseError(f"{SHOW_VTP_STATUS!r} output has no {', '.join(missing)}")

    # Genie prints "1 to 3" as [1, 2, 3] but "VTP2 capable" as ["2"].
    capable = [int(v) for v in vtp.get("version_capable", [])]
    domain = vtp.get("domain_name") or None
    interface = vtp.get("updater_interface")
    try:
        return IOSVTPStatus(
            version=int(vtp["version"]),
            max_version=max(capable) if capable else None,
            domain_name=None if domain == _NO_DOMAIN else domain,
            mode=vlan["operating_mode"],
            configuration_revision=vlan["configuration_revision"],
            vlan_count=vlan["existing_vlans"],
            extended_vlan_count=vlan.get("existing_extended_vlans"),
            max_vlans=vlan.get("maximum_vlans"),
            pruning_enabled=vtp["pruning_mode"],
            traps_enabled=vtp["traps_generation"],
            device_id=vtp.get("device_id"),
            primary_id=vlan.get("primary_id"),
            primary_description=vlan.get("primary_description") or None,
            last_modified_by=_address(vtp.get("conf_last_modified_by")),
            last_modified_at=(
                None
                if vtp.get("conf_last_modified_time") in (None, _NO_TIME)
                else vtp["conf_last_modified_time"]
            ),
            updater_id=_address(vtp.get("updater_id")),
            updater_interface=full_interface_name(interface) if interface else None,
        )
    except ValidationError as exc:
        raise ParseError(f"unexpected {SHOW_VTP_STATUS!r} value: {exc}") from exc


def _address(value: str | None) -> str | None:
    return None if value in (None, _NO_ADDRESS) else value


# -- show interfaces switchport (Genie, D33) ---------------------------------

#: Operational modes that map to the core `mode`; anything else is None.
_SWITCHPORT_MODES: dict[str, Literal["access", "trunk"]] = {
    "static access": "access",
    "access": "access",
    "trunk": "trunk",
}


def parse_show_interfaces_switchport(output: str) -> list[IOSSwitchport]:
    ports = []
    for name, port in parse_genie(GENIE_OS, SHOW_INTERFACES_SWITCHPORT, output).items():
        if not port.get("switchport_enable"):
            continue  # routed port
        if "switchport_mode" not in port:
            raise ParseError(f"{SHOW_INTERFACES_SWITCHPORT!r}: {name} has no administrative mode")
        oper = port.get("operational_mode")
        encapsulation = port.get("encapsulation", {})
        all_allowed, allowed = _allowed_vlans(name, port.get("trunk_vlans"))
        try:
            ports.append(
                IOSSwitchport(
                    # Genie expands names itself; full names come back unchanged.
                    interface=full_interface_name(name),
                    mode=_SWITCHPORT_MODES.get(oper) if oper else None,
                    access_vlan=_vlan(port.get("access_vlan")),
                    native_vlan=_vlan(encapsulation.get("native_vlan")),
                    all_vlans_allowed=all_allowed,
                    allowed_vlans=allowed,
                    admin_mode=port["switchport_mode"],
                    oper_mode=oper,
                    voice_vlan=_vlan(port.get("voice_vlan")),
                    negotiation=port.get("negotiation_of_trunk"),
                    encapsulation=encapsulation.get("operational_encapsulation"),
                )
            )
        except (ValidationError, ValueError) as exc:
            raise ParseError(
                f"unexpected {SHOW_INTERFACES_SWITCHPORT!r} value for {name}: {exc}"
            ) from exc
    return ports


def _vlan(value: str | None) -> int | None:
    # No VLAN: "none" (voice), "unassigned" (access), and "0 (Inactive)",
    # printed as the native VLAN of a port-channel that is down.
    if value is None or value.lower() in ("none", "unassigned", "0"):
        return None
    return int(value)


def _allowed_vlans(name: str, value: str | None) -> tuple[bool, tuple[int, ...] | None]:
    if value is None:
        return False, None
    text = value.strip().lower()
    if text == "all":
        return True, None
    if text == "none":
        return False, ()
    try:
        return False, tuple(sorted(set(vlanconfig_to_list(text))))
    except ValueError as exc:
        raise ParseError(
            f"{SHOW_INTERFACES_SWITCHPORT!r}: unreadable allowed VLANs {value!r} on {name}"
        ) from exc


# -- neighbors: show cdp neighbors detail (Genie), show lldp ... (ntc, D35) ----

#: The device's own statements, recognised before parsing: a protocol that is
#: off or unknown has no neighbors. Some IOS releases end with a closing count
#: ("Total cdp entries displayed : 2"), some don't (verified live 2026-11-10);
#: when it is there it must match what the engine returned, so an engine
#: dropping a record fails.
_PROTOCOL_OFF = re.compile(
    r"^% (?:CDP|LLDP) is not enabled|^% Invalid input detected", re.MULTILINE
)
_CDP_TOTAL = re.compile(r"^\s*Total cdp entries displayed\s*:\s*(\d+)\s*$", re.MULTILINE)
_LLDP_TOTAL = re.compile(r"^\s*Total entries displayed:\s*(\d+)\s*$", re.MULTILINE)

#: CDP capability words, lowercased; multi-word ones joined with "-".
_CDP_CAPABILITIES = {
    "router",
    "trans-bridge",
    "source-route-bridge",
    "switch",
    "host",
    "igmp",
    "repeater",
    "phone",
    "remote",
    "cvta",
    "two-port-mac-relay",
    "phone-port",
}
_CDP_PHRASES = {"two-port mac relay": "two-port-mac-relay", "phone port": "phone-port"}
#: The legend IOS prints with `show lldp neighbors`.
_LLDP_CAPABILITIES = {
    "R": "router",
    "B": "bridge",
    "T": "telephone",
    "C": "docsis-cable-device",
    "W": "wlan-access-point",
    "P": "repeater",
    "S": "station",
    "O": "other",
}


def neighbor_protocol_off(output: str) -> bool:
    """True when the device says CDP/LLDP is disabled or the command is unknown."""
    return _PROTOCOL_OFF.search(output) is not None


def parse_show_cdp_neighbors_detail(output: str) -> list[IOSNeighbor]:
    if _no_neighbors(_CDP_TOTAL, output):
        return []
    result = parse_genie(GENIE_OS, SHOW_CDP_NEIGHBORS_DETAIL, output)
    entries = [result["index"][i] for i in sorted(result.get("index", {}))]
    _check_total(SHOW_CDP_NEIGHBORS_DETAIL, _CDP_TOTAL, output, len(entries))
    neighbors = []
    for entry in entries:
        local = entry.get("local_interface")
        if not local:
            raise ParseError(f"{SHOW_CDP_NEIGHBORS_DETAIL!r}: a neighbor has no local interface")
        neighbors.append(
            _neighbor(
                SHOW_CDP_NEIGHBORS_DETAIL,
                protocol="cdp",
                local_interface=full_interface_name(local),
                remote_name=_text(entry.get("device_id")),
                remote_port=_text(entry.get("port_id")),
                remote_platform=_text(entry.get("platform")),
                remote_description=_text(entry.get("software_version")),
                management_addresses=_addresses(entry.get("management_addresses", {})),
                entry_addresses=_addresses(entry.get("entry_addresses", {})),
                capabilities=_cdp_capabilities(entry.get("capabilities", "")),
                native_vlan=_text(entry.get("native_vlan")),
                duplex=_duplex("CDP neighbor", entry.get("duplex_mode") or ""),
            )
        )
    return neighbors


def parse_show_lldp_neighbors_detail(output: str) -> list[IOSNeighbor]:
    if _no_neighbors(_LLDP_TOTAL, output):
        return []
    rows = parse_textfsm(PLATFORM, SHOW_LLDP_NEIGHBORS_DETAIL, output)
    _check_total(SHOW_LLDP_NEIGHBORS_DETAIL, _LLDP_TOTAL, output, len(rows))
    neighbors = []
    for row in rows:
        local = row["local_interface"]
        if not local:
            log.warning(
                "LLDP neighbor %r: the device doesn't print its local interface",
                row["neighbor_name"] or row["chassis_id"],
            )
        neighbors.append(
            _neighbor(
                SHOW_LLDP_NEIGHBORS_DETAIL,
                protocol="lldp",
                local_interface=full_interface_name(local) if local else None,
                remote_name=_text(row["neighbor_name"]),
                remote_port=_text(row["neighbor_port_id"]),
                remote_port_description=_text(row["neighbor_interface"]),
                remote_chassis_id=_text(row["chassis_id"]),
                remote_description=_text(row["neighbor_description"]),
                management_addresses=_addresses(
                    [row["mgmt_address"]] if row["mgmt_address"] else []
                ),
                capabilities=_lldp_capabilities(row["capabilities"]),
                native_vlan=_text(row["vlan_id"]),
            )
        )
    return neighbors


def _no_neighbors(total: re.Pattern[str], output: str) -> bool:
    """Protocol off or unknown, a count of 0, or no output at all."""
    if neighbor_protocol_off(output) or not output.strip():
        return True
    match = total.search(output)
    return match is not None and int(match.group(1)) == 0


def _check_total(command: str, total: re.Pattern[str], output: str, parsed: int) -> None:
    match = total.search(output)
    if match is not None and int(match.group(1)) != parsed:
        raise ParseError(
            f"{command!r}: the device lists {match.group(1)} neighbors, parsed {parsed}"
        )


def _neighbor(command: str, **fields: Any) -> IOSNeighbor:
    try:
        return IOSNeighbor(**fields)
    except ValidationError as exc:
        raise ParseError(f"unexpected {command!r} value: {exc}") from exc


def _text(value: str | None) -> str | None:
    return value.strip() or None if value else None


def _addresses(values: Any) -> tuple[IPv4Address | IPv6Address, ...]:
    try:
        return tuple(ip_address(v) for v in values)
    except ValueError as exc:
        raise ParseError(f"unreadable neighbor address: {exc}") from exc


def _cdp_capabilities(text: str) -> tuple[str, ...]:
    words = text.strip().lower()
    for phrase, name in _CDP_PHRASES.items():
        words = words.replace(phrase, name)
    names = tuple(words.split())
    for name in names:
        if name not in _CDP_CAPABILITIES:
            log.warning("unrecognised CDP capability %r; kept as printed", name)
    return names


def _lldp_capabilities(text: str) -> tuple[str, ...]:
    names = []
    for code in (c.strip() for c in text.split(",") if c.strip()):
        name = _LLDP_CAPABILITIES.get(code)
        if name is None:
            log.warning("unrecognised LLDP capability code %r; kept as printed", code)
            name = code.lower()
        names.append(name)
    return tuple(names)


# -- show inventory (ntc-templates with a corrected template, D36) ------------

#: Released ntc-templates truncates serials at "-" and PIDs at a space;
#: delete with the file once a release has networktocode/ntc-templates#2377.
_INVENTORY_TEMPLATE = Path(__file__).parent / "templates" / "cisco_ios_show_inventory.textfsm"


def parse_show_inventory(output: str) -> list[InventoryItem]:
    rows = parse_textfsm(PLATFORM, SHOW_INVENTORY, output, template=_INVENTORY_TEMPLATE)
    items = []
    for row in rows:
        items.append(
            InventoryItem(
                name=_text(row["name"]),
                description=_text(row["descr"]),
                part_id=_text(row["pid"]),
                version_id=_text(row["vid"]),
                serial_number=_text(row["sn"]),
            )
        )
    return items
