"""Turn NX-OS command output into models.

Commands are sent with `| json` (D38), so this layer reads the device's own
structure: no text parsing happens here. It maps fields into our models,
enforces the fields a model can't do without and rejects what it doesn't
understand. Raised errors carry no host; the device adds it.
"""

from datetime import timedelta
from typing import Any

from netsdk.cisco.nxos.commands import SHOW_INVENTORY, SHOW_VERSION
from netsdk.cisco.nxos.models import NXOSFacts
from netsdk.core import ParseError
from netsdk.core.parsing import parse_json

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
