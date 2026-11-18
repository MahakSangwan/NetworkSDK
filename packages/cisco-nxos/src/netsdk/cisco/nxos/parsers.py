"""Turn NX-OS command output into models.

Commands are sent with `| json` (D38), so this layer reads the device's own
structure: no text parsing happens here. It maps fields into our models,
enforces the fields a model can't do without and rejects what it doesn't
understand. Raised errors carry no host; the device adds it.
"""

import logging
import re
from datetime import timedelta
from ipaddress import AddressValueError, IPv4Address, IPv4Interface
from typing import Any, Literal

from pydantic import ValidationError

from netsdk.cisco.nxos.commands import (
    SHOW_INTERFACE,
    SHOW_INTERFACE_SWITCHPORT,
    SHOW_INVENTORY,
    SHOW_IP_INTERFACE,
    SHOW_VERSION,
    SHOW_VLAN_BRIEF,
    SHOW_VTP_STATUS,
)
from netsdk.cisco.nxos.models import (
    NXOSVLAN,
    NXOSFacts,
    NXOSInterface,
    NXOSSwitchport,
    NXOSVTPStatus,
)
from netsdk.core import Duplex, IPAddress, ParseError
from netsdk.core.formats import KBPS_PER_UNIT
from netsdk.core.parsing import Damaged, parse_json

log = logging.getLogger(__name__)

VENDOR = "Cisco"

#: Values NX-OS prints for "nothing here".
_EMPTY = {"", "N/A", "n/a"}


def rows(data: dict[str, Any], table: str, row: str, command: str) -> list[dict[str, Any]]:
    """The records of an NX-OS `TABLE_x` / `ROW_x` pair, always as a list.

    NX-OS prints a single record as an object instead of a one-item list, and
    leaves the table out when it has no records.
    """
    block = data.get(table)
    if block is None:
        return []
    if not isinstance(block, dict):
        raise ParseError(f"{command!r} output: {table} is not an object")
    records = block.get(row)
    if records is None:
        return []
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ParseError(f"{command!r} output: {table}.{row} is not a list of objects")
    return records


def text(data: dict[str, Any], key: str) -> str | None:
    """A field as a stripped string; None when missing or an "empty" marker."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, Damaged):
        raise ParseError(f"field {key!r} was broken across lines by the device; unreadable")
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise ParseError(f"field {key!r} is not a plain value: {value!r}")
    value = str(value).strip()
    return None if value in _EMPTY else value


# -- facts --------------------------------------------------------------------

_UPTIME_FIELDS = {
    "kern_uptm_days": timedelta(days=1),
    "kern_uptm_hrs": timedelta(hours=1),
    "kern_uptm_mins": timedelta(minutes=1),
    "kern_uptm_secs": timedelta(seconds=1),
}


def parse_facts(
    show_version: str, show_inventory: str, *, wrap_width: int | None = None
) -> NXOSFacts:
    version = parse_json(SHOW_VERSION, show_version, wrap_width=wrap_width)
    inventory = parse_json(SHOW_INVENTORY, show_inventory, wrap_width=wrap_width)

    hostname = text(version, "host_name")
    # 9.x and later print `nxos_ver_str`; 7.x prints kickstart and system
    # versions, and the system one is what runs.
    os_version = text(version, "nxos_ver_str") or text(version, "sys_ver_str")
    if not hostname or not os_version:
        missing = [
            name for name, value in (("host_name", hostname), ("version", os_version)) if not value
        ]
        raise ParseError(f"{SHOW_VERSION!r} output has no {' or '.join(missing)}")

    model, serial = _chassis(inventory)
    return NXOSFacts(
        hostname=hostname,
        vendor=VENDOR,
        model=model,
        serial_number=serial,
        os_version=os_version,
        uptime=_uptime(version),
        chassis_description=text(version, "chassis_id"),
        running_image=text(version, "nxos_file_name") or text(version, "isan_file_name"),
        bios_version=text(version, "bios_ver_str"),
        reload_reason=text(version, "rr_reason"),
    )


def _chassis(inventory: dict[str, Any]) -> tuple[str, str]:
    # Some releases print the name with its quotes: "\"Chassis\"". FEX and
    # modules have other names ("FEX 101 CHASSIS", "Slot 1").
    chassis = [
        r
        for r in rows(inventory, "TABLE_inv", "ROW_inv", SHOW_INVENTORY)
        if (text(r, "name") or "").strip('"').strip().casefold() == "chassis"
    ]
    if len(chassis) != 1:
        raise ParseError(f"{SHOW_INVENTORY!r} output has {len(chassis)} 'Chassis' entries, not 1")
    model, serial = text(chassis[0], "productid"), text(chassis[0], "serialnum")
    if not model or not serial:
        raise ParseError(f"{SHOW_INVENTORY!r} output has no chassis part ID or serial number")
    return model, serial


def _uptime(version: dict[str, Any]) -> timedelta | None:
    values = {key: version.get(key) for key in _UPTIME_FIELDS}
    if all(v is None for v in values.values()):
        return None
    total = timedelta()
    for key, unit in _UPTIME_FIELDS.items():
        value = values[key]
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ParseError(f"{SHOW_VERSION!r} output: {key} is {value!r}, not a count")
        total += value * unit
    return total


# -- show interface ---------------------------------------------------------

# "1000 Mb/s", "10 Gb/s", "100 Gb/s".
_SPEED = re.compile(r"(\d+)\s*([kmgt])b/s", re.IGNORECASE)
_NOT_NEGOTIATED = {"auto-speed", "auto"}
_DUPLEX: dict[str, Duplex] = {"full": "full", "half": "half", "auto": "auto"}
_UP_DOWN = {"up": True, "down": False}
_ADMIN_DOWN = "administratively down"


def parse_show_interface(output: str, *, wrap_width: int | None = None) -> list[NXOSInterface]:
    data = parse_json(SHOW_INTERFACE, output, wrap_width=wrap_width)
    records = rows(data, "TABLE_interface", "ROW_interface", SHOW_INTERFACE)
    if not records:
        raise ParseError(f"{SHOW_INTERFACE!r} output lists no interfaces")
    interfaces = [_interface(record) for record in records]
    names = [interface.name for interface in interfaces]
    if len(set(names)) != len(names):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise ParseError(f"{SHOW_INTERFACE!r} lists {', '.join(duplicates)} more than once")
    return interfaces


def _interface(record: dict[str, Any]) -> NXOSInterface:
    name = text(record, "interface")
    if not name:
        raise ParseError(f"{SHOW_INTERFACE!r} has an interface without a name")
    # NX-OS reports interfaces in one of two field sets, whatever the name:
    # SVIs use svi_*, everything else seen so far state/eth_* (D39).
    if "svi_admin_state" in record:
        fields = {
            "enabled": _up_down(name, record, "svi_admin_state"),
            "oper_up": _up_down(name, record, "svi_line_proto"),
            "description": text(record, "svi_desc"),
            "mac_address": text(record, "svi_mac"),
            "mtu": _count(name, record, "svi_mtu"),
            "state_reason": text(record, "svi_rsn_desc"),
            "bandwidth": _count(name, record, "svi_bw"),
        }
    elif "state" in record:
        fields = {
            "enabled": _enabled(name, record),
            # `state` is "up", "down" or a reason; only "up" is up.
            "oper_up": text(record, "state") == "up",
            "description": text(record, "desc"),
            "mac_address": text(record, "eth_hw_addr"),
            "mtu": _count(name, record, "eth_mtu"),
            "speed": _speed(name, text(record, "eth_speed")),
            "duplex": _duplex(name, text(record, "eth_duplex")),
            "state_reason": text(record, "state_rsn_desc"),
            "hardware_type": text(record, "eth_hw_desc"),
            "burned_in_address": text(record, "eth_bia_addr"),
            "bandwidth": _count(name, record, "eth_bw"),
            "port_mode": text(record, "eth_mode"),
        }
    else:
        raise ParseError(
            f"{SHOW_INTERFACE!r}: {name} has neither state nor svi_admin_state; "
            f"fields: {', '.join(sorted(record))}"
        )
    try:
        return NXOSInterface(name=name, **fields)
    except ValidationError as exc:
        raise ParseError(f"unexpected {SHOW_INTERFACE!r} value for {name}: {exc}") from exc


def _enabled(name: str, record: dict[str, Any]) -> bool:
    if "admin_state" in record:
        return _up_down(name, record, "admin_state")
    # 7.3(11)N1(1) prints no admin_state (live, 2026-11-13). A shut port is
    # down with the reason "Administratively down", as in the text output.
    if text(record, "state") == "up":
        return True
    return (text(record, "state_rsn_desc") or "").casefold() != _ADMIN_DOWN


def _up_down(name: str, record: dict[str, Any], key: str) -> bool:
    value = text(record, key)
    if value not in _UP_DOWN:
        raise ParseError(f"{SHOW_INTERFACE!r}: {name} {key} is {value!r}, not up or down")
    return _UP_DOWN[value]


def _count(name: str, record: dict[str, Any], key: str) -> int | None:
    value = text(record, key)
    if value is None:
        return None
    if not value.isdigit():
        raise ParseError(f"{SHOW_INTERFACE!r}: {name} {key} is {value!r}, not a number")
    return int(value)


def _speed(name: str, value: str | None) -> int | None:
    if value is None or value.lower() in _NOT_NEGOTIATED:
        return None
    match = _SPEED.fullmatch(value)
    if match is None:
        # Logged, not fatal: speed is optional and a new spelling shouldn't
        # break the whole getter.
        log.warning("%s: unrecognised speed %r", name, value)
        return None
    return int(match[1]) * KBPS_PER_UNIT[match[2].lower()]


def _duplex(name: str, value: str | None) -> Duplex | None:
    if value is None:
        return None
    duplex = _DUPLEX.get(value.lower())
    if duplex is None:
        log.warning("%s: unrecognised duplex %r", name, value)
    return duplex


# -- show ip interface vrf all ----------------------------------------------

#: The VRF NX-OS calls the global table.
_DEFAULT_VRF = "default"


def parse_show_ip_interface(output: str, *, wrap_width: int | None = None) -> list[IPAddress]:
    """One record per IPv4 address: each interface's primary, then its secondaries.

    NX-OS prints the VRF in one of two layouts (D40):
    - in each interface row (`vrf-name-out`), with no TABLE_vrf (live);
    - as a parallel TABLE_vrf: entry N is one VRF, entry N of TABLE_intf holds
      that VRF's interfaces (one object or a list). Paired by position only
      when their lengths match.
    """
    data = parse_json(SHOW_IP_INTERFACE, output, wrap_width=wrap_width)
    groups = [_row_list(entry, "ROW_intf") for entry in _entries(data, "TABLE_intf")]
    pairs: list[tuple[str | None, dict[str, Any]]]
    if "TABLE_vrf" in data:
        vrfs = [_only_row(entry, "ROW_vrf") for entry in _entries(data, "TABLE_vrf")]
        if len(vrfs) != len(groups):
            raise ParseError(
                f"{SHOW_IP_INTERFACE!r} lists {len(vrfs)} VRF entries for {len(groups)} "
                "interface entries; can't tell which VRF each interface is in"
            )
        pairs = []
        for vrf_row, group in zip(vrfs, groups, strict=True):
            vrf = text(vrf_row, "vrf-name-out")
            for row in group:
                own = text(row, "vrf-name-out")
                if own is not None and own != vrf:
                    raise ParseError(
                        f"{SHOW_IP_INTERFACE!r}: {text(row, 'intf-name')} is paired with one VRF "
                        "but names another"
                    )
                pairs.append((vrf, row))
    else:
        pairs = [(text(row, "vrf-name-out"), row) for group in groups for row in group]
    addresses: list[IPAddress] = []
    seen: set[str] = set()
    for vrf, row in pairs:
        name = text(row, "intf-name")
        if not name or not vrf:
            raise ParseError(f"{SHOW_IP_INTERFACE!r} has an entry without interface or VRF name")
        if name in seen:
            raise ParseError(f"{SHOW_IP_INTERFACE!r} lists {name} more than once")
        seen.add(name)
        if text(row, "unnum-intf"):
            # Unnumbered: the address belongs to the interface it is
            # configured on, which has its own record (D30).
            continue
        addresses.extend(_addresses(name, None if vrf == _DEFAULT_VRF else vrf, row))
    return addresses


def _addresses(name: str, vrf: str | None, row: dict[str, Any]) -> list[IPAddress]:
    found = []
    if text(row, "prefix") is not None:
        found.append(_address(name, vrf, row, "prefix", "masklen", secondary=False))
    for entry in rows(row, "TABLE_secondary_address", "ROW_secondary_address", SHOW_IP_INTERFACE):
        found.append(_address(name, vrf, entry, "prefix1", "masklen1", secondary=True))
    return found


def _address(
    name: str, vrf: str | None, row: dict[str, Any], ip_key: str, len_key: str, *, secondary: bool
) -> IPAddress:
    ip, length = text(row, ip_key), text(row, len_key)
    try:
        # A missing prefix length is an error, never assumed /32 (D30).
        if ip is None or length is None or not length.isdigit():
            raise ValueError(f"{ip_key}={ip!r}, {len_key}={length!r}")
        return IPAddress(
            interface=name,
            address=IPv4Interface(f"{ip}/{length}"),
            secondary=secondary,
            vrf=vrf,
        )
    except (ValueError, ValidationError) as exc:
        raise ParseError(f"{SHOW_IP_INTERFACE!r}: bad address on {name}: {exc}") from exc


def _entries(data: dict[str, Any], table: str) -> list[dict[str, Any]]:
    # Here a TABLE_x is a list of {ROW_x: {...}} objects (one when alone).
    entries = data.get(table)
    if entries is None:
        return []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ParseError(f"{SHOW_IP_INTERFACE!r} output: {table} is not a list of objects")
    return entries


def _row_list(entry: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = entry.get(key)
    items = [value] if isinstance(value, dict) else value
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise ParseError(f"{SHOW_IP_INTERFACE!r} output: an entry has no {key} objects")
    return items


def _only_row(entry: dict[str, Any], key: str) -> dict[str, Any]:
    row = entry.get(key)
    if not isinstance(row, dict):
        raise ParseError(f"{SHOW_IP_INTERFACE!r} output: an entry has no single {key}")
    return row


# -- show interface switchport (D41) ----------------------------------------

#: NX-OS prints this for a trunk with no `allowed vlan` restriction (and for
#: an explicit `allowed vlan 1-4094`); reported as all_vlans_allowed (D41).
_ALL_VLANS = "1-4094"
_SWITCHPORT_MODES: dict[str, Literal["access", "trunk"]] = {"access": "access", "trunk": "trunk"}
_NO_VLAN = {"none", "0"}


def vlan_ids(value: str) -> tuple[int, ...]:
    """Expand an NX-OS VLAN list ("1-5,7,10-12") into sorted, unique IDs."""
    ids: set[int] = set()
    for part in value.replace(" ", "").split(","):
        low, dash, high = part.partition("-")
        if not low.isdigit() or (dash and not high.isdigit()):
            raise ValueError(f"not a VLAN list: {value!r}")
        first, last = int(low), int(high) if dash else int(low)
        if not 1 <= first <= last <= 4094:
            raise ValueError(f"not a VLAN list: {value!r}")
        ids.update(range(first, last + 1))
    return tuple(sorted(ids))


def parse_show_interface_switchport(
    output: str, *, wrap_width: int | None = None
) -> list[NXOSSwitchport]:
    data = parse_json(SHOW_INTERFACE_SWITCHPORT, output, wrap_width=wrap_width)
    ports = []
    for row in rows(data, "TABLE_interface", "ROW_interface", SHOW_INTERFACE_SWITCHPORT):
        name = text(row, "interface")
        if not name:
            raise ParseError(f"{SHOW_INTERFACE_SWITCHPORT!r} has an interface without a name")
        enabled = text(row, "switchport")
        if enabled == "Disabled":
            continue  # routed port
        if enabled != "Enabled":
            raise ParseError(f"{SHOW_INTERFACE_SWITCHPORT!r}: {name} switchport is {enabled!r}")
        oper = text(row, "oper_mode")
        all_allowed, allowed = _allowed_vlans(name, text(row, "trunk_vlans"))
        try:
            ports.append(
                NXOSSwitchport(
                    interface=name,
                    mode=_SWITCHPORT_MODES.get(oper) if oper else None,
                    access_vlan=_switchport_vlan(name, row, "access_vlan"),
                    native_vlan=_switchport_vlan(name, row, "native_vlan"),
                    all_vlans_allowed=all_allowed,
                    allowed_vlans=allowed,
                    oper_mode=oper,
                )
            )
        except ValidationError as exc:
            raise ParseError(
                f"unexpected {SHOW_INTERFACE_SWITCHPORT!r} value for {name}: {exc}"
            ) from exc
    names = [p.interface for p in ports]
    if len(set(names)) != len(names):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        raise ParseError(
            f"{SHOW_INTERFACE_SWITCHPORT!r} lists {', '.join(duplicates)} more than once"
        )
    return ports


def _switchport_vlan(name: str, row: dict[str, Any], key: str) -> int | None:
    value = text(row, key)
    if value is None or value.lower() in _NO_VLAN:
        return None
    if not value.isdigit():
        raise ParseError(f"{SHOW_INTERFACE_SWITCHPORT!r}: {name} {key} is {value!r}")
    return int(value)


def _allowed_vlans(name: str, value: str | None) -> tuple[bool, tuple[int, ...] | None]:
    if value is None:
        return False, None
    if value.replace(" ", "") == _ALL_VLANS:
        return True, None
    if value.lower() == "none":
        return False, ()
    try:
        return False, vlan_ids(value)
    except ValueError as exc:
        raise ParseError(
            f"{SHOW_INTERFACE_SWITCHPORT!r}: unreadable allowed VLANs {value!r} on {name}"
        ) from exc


# -- show vlan brief (D41) ---------------------------------------------------


def parse_show_vlan_brief(
    show_vlan_brief: str, show_interface_switchport: str, *, wrap_width: int | None = None
) -> list[NXOSVLAN]:
    """VLANs with their access ports only.

    ID, name and state come from `show vlan brief`; its port lists are not
    read: they include trunk ports, and 7.3(11)N1(1) damages them (D41).
    A VLAN's ports are the access ports `show interface switchport` puts in it.
    """
    data = parse_json(SHOW_VLAN_BRIEF, show_vlan_brief, wrap_width=wrap_width)
    members: dict[int, list[str]] = {}
    for p in parse_show_interface_switchport(show_interface_switchport, wrap_width=wrap_width):
        if p.mode == "access" and p.access_vlan is not None:
            members.setdefault(p.access_vlan, []).append(p.interface)
    vlans = []
    for row in rows(data, "TABLE_vlanbriefxbrief", "ROW_vlanbriefxbrief", SHOW_VLAN_BRIEF):
        # The -utf ID is the right one where both exist: some releases print
        # a garbled plain ID next to it (NAPALM issue 1490).
        raw_id = text(row, "vlanshowbr-vlanid-utf") or text(row, "vlanshowbr-vlanid")
        if raw_id is None or not raw_id.isdigit():
            raise ParseError(f"{SHOW_VLAN_BRIEF!r} has a VLAN without a readable ID: {raw_id!r}")
        vlan_id = int(raw_id)
        status = text(row, "vlanshowbr-vlanstate")
        if status is None:
            raise ParseError(f"{SHOW_VLAN_BRIEF!r}: VLAN {vlan_id} has no state")
        shut = text(row, "vlanshowbr-shutstate")
        try:
            vlans.append(
                NXOSVLAN(
                    vlan_id=vlan_id,
                    name=text(row, "vlanshowbr-vlanname"),
                    active=status == "active" and shut in (None, "noshutdown"),
                    interfaces=tuple(members.get(vlan_id, ())),
                    status=status,
                    shut_state=shut,
                )
            )
        except ValidationError as exc:
            raise ParseError(
                f"unexpected {SHOW_VLAN_BRIEF!r} value for VLAN {vlan_id}: {exc}"
            ) from exc
    ids = [v.vlan_id for v in vlans]
    if len(set(ids)) != len(ids):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise ParseError(f"{SHOW_VLAN_BRIEF!r} lists VLAN {duplicates} more than once")
    orphans = sorted(set(members) - set(ids))
    if orphans:
        log.info("access ports in VLANs that don't exist: %s", orphans)
    return vlans


# -- show vtp status (D43) ---------------------------------------------------

_ENABLED = {"enabled": True, "disabled": False}
_NO_ADDRESS = IPv4Address("0.0.0.0")


def parse_show_vtp_status(output: str, *, wrap_width: int | None = None) -> NXOSVTPStatus:
    data = parse_json(SHOW_VTP_STATUS, output, wrap_width=wrap_width)
    try:
        return NXOSVTPStatus(
            version=_vtp_int(data, "running-version", required=True),
            configured_version=_vtp_int(data, "version"),
            domain_name=text(data, "domain_name"),
            mode=_vtp_required(data, "oper_mode"),
            configuration_revision=_vtp_int(data, "config_rev", required=True),
            vlan_count=_vtp_int(data, "num_current_vlans", required=True),
            max_vlans=_vtp_int(data, "max_vlan_supported_local"),
            pruning_enabled=_vtp_flag(data, "pruning_mode", required=True),
            oper_pruning_enabled=_vtp_flag(data, "oper_pruning_mode"),
            traps_enabled=_vtp_flag(data, "trap_enabled", required=True),
            v2_mode_enabled=_vtp_flag(data, "v2_mode"),
            md5_digest=text(data, "md5_digest"),
            last_modified_by=_vtp_address(data, "last_modified_ip"),
            last_modified_at=text(data, "last_modified_time"),
            updater_id=_vtp_address(data, "updater_id"),
            updater_reason=text(data, "updater_reason"),
        )
    except ValidationError as exc:
        raise ParseError(f"unexpected {SHOW_VTP_STATUS!r} value: {exc}") from exc


def _vtp_required(data: dict[str, Any], key: str) -> str:
    value = text(data, key)
    if value is None:
        raise ParseError(f"{SHOW_VTP_STATUS!r} output has no {key}")
    return value


def _vtp_int(data: dict[str, Any], key: str, *, required: bool = False) -> int | None:
    value = text(data, key)
    if value is None:
        if required:
            raise ParseError(f"{SHOW_VTP_STATUS!r} output has no {key}")
        return None
    if not value.isdigit():
        raise ParseError(f"{SHOW_VTP_STATUS!r}: {key} is {value!r}, not a number")
    return int(value)


def _vtp_flag(data: dict[str, Any], key: str, *, required: bool = False) -> bool | None:
    value = text(data, key)
    if value is None and not required:
        return None
    if value is None or value.lower() not in _ENABLED:
        raise ParseError(f"{SHOW_VTP_STATUS!r}: {key} is {value!r}, not enabled or disabled")
    return _ENABLED[value.lower()]


def _vtp_address(data: dict[str, Any], key: str) -> IPv4Address | None:
    value = text(data, key)
    if value is None:
        return None
    try:
        address = IPv4Address(value)
    except AddressValueError as exc:
        raise ParseError(f"{SHOW_VTP_STATUS!r}: {key} is not an IPv4 address") from exc
    return None if address == _NO_ADDRESS else address
