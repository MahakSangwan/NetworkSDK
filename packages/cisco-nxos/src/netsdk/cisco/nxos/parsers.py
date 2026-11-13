"""Turn NX-OS command output into models.

Commands are sent with `| json` (D38), so this layer reads the device's own
structure: no text parsing happens here. It maps fields into our models,
enforces the fields a model can't do without and rejects what it doesn't
understand. Raised errors carry no host; the device adds it.
"""

import logging
import re
from datetime import timedelta
from typing import Any

from pydantic import ValidationError

from netsdk.cisco.nxos.commands import SHOW_INTERFACE, SHOW_INVENTORY, SHOW_VERSION
from netsdk.cisco.nxos.models import NXOSFacts, NXOSInterface
from netsdk.core import Duplex, ParseError
from netsdk.core.formats import KBPS_PER_UNIT
from netsdk.core.parsing import parse_json

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


def parse_facts(show_version: str, show_inventory: str) -> NXOSFacts:
    version = parse_json(SHOW_VERSION, show_version)
    inventory = parse_json(SHOW_INVENTORY, show_inventory)

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


def parse_show_interface(output: str) -> list[NXOSInterface]:
    data = parse_json(SHOW_INTERFACE, output)
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
